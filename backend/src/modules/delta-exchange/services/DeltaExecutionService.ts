import { ExecutionMode } from '@algoapp/shared';
import { DeltaRestClient, DeltaPlaceOrderRequest } from './DeltaRestClient.js';
import { LiveTradingGuard } from '../../production/services/liveTradingGuard.js';
import { eventBus } from '../../../services/EventBus.js';

export interface DeltaOrderExecutionInput {
  symbol: string;
  side: 'buy' | 'sell';
  orderType: 'market' | 'limit' | 'stop_market' | 'stop_limit';
  size: number;
  price?: number | undefined;
  stopPrice?: number | undefined;
  stopLoss?: number | undefined;
  takeProfit?: number | undefined;
  clientOrderId?: string | undefined;
  reduceOnly?: boolean | undefined;
}

export class DeltaExecutionService {
  constructor(private rest: DeltaRestClient) {}

  public async placeOrder(input: DeltaOrderExecutionInput): Promise<{
    success: boolean;
    orderId?: number | undefined;
    latencyMs: number;
    data?: any;
    error?: string | undefined;
  }> {
    const startTime = Date.now();
    const idempotencyKey = input.clientOrderId || `ord-${Date.now()}-${Math.floor(Math.random() * 1000)}`;

    try {
      // 1. Mandatory LIVE Trading Activation Guard Check
      const liveSafety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      if (!liveSafety.isAllowed) {
        const latencyMs = Date.now() - startTime;
        const errorMsg = `LIVE_SAFETY_REJECTED: Live Trading Safety Guard blocked execution. Reasons: ${(liveSafety.rejectionReasons || []).join('; ')}`;
        eventBus.emit('delta:order:failed', {
          symbol: input.symbol,
          error: errorMsg,
          latencyMs,
        });
        return {
          success: false,
          latencyMs,
          error: errorMsg,
        };
      }

      // 2. Canonical 12-Rule Pre-Flight Risk & Margin Validation
      const { ExecutionEngineService } = await import('../../execution-engine/services/ExecutionEngineService.js');
      const validationEngine = new ExecutionEngineService();
      const validation = await validationEngine.validateOrder({
        symbol: input.symbol,
        side: input.side,
        orderType: input.orderType,
        size: input.size,
        price: input.price,
        stopPrice: input.stopPrice,
        reduceOnly: input.reduceOnly,
        clientOrderId: idempotencyKey,
      });

      if (!validation.isValid) {
        const failedRule = validation.results.find((r) => !r.passed);
        const latencyMs = Date.now() - startTime;
        const errorMsg = `Validation Failed: ${failedRule?.ruleName} - ${failedRule?.message}`;
        eventBus.emit('delta:order:failed', {
          symbol: input.symbol,
          error: errorMsg,
          latencyMs,
        });
        return {
          success: false,
          latencyMs,
          error: errorMsg,
        };
      }

      // 3. Authoritative Contract Metadata & Quantity Normalization Check
      const product = this.rest.getProduct(input.symbol);
      const qtyCheck = ExecutionEngineService.normalizeContractQuantity(input.symbol, input.size, product);
      if (!qtyCheck.isValid) {
        const latencyMs = Date.now() - startTime;
        const errorMsg = qtyCheck.reason || `MISSING_EXCHANGE_METADATA: Invalid contract quantity or metadata for ${input.symbol}`;
        eventBus.emit('delta:order:failed', {
          symbol: input.symbol,
          error: errorMsg,
          latencyMs,
        });
        return {
          success: false,
          latencyMs,
          error: errorMsg,
        };
      }

      if (!product || !product.id) {
        const latencyMs = Date.now() - startTime;
        const errorMsg = `MISSING_EXCHANGE_METADATA: Authoritative product metadata unavailable from Delta Exchange for ${input.symbol}`;
        eventBus.emit('delta:order:failed', {
          symbol: input.symbol,
          error: errorMsg,
          latencyMs,
        });
        return {
          success: false,
          latencyMs,
          error: errorMsg,
        };
      }

      const orderPayload: DeltaPlaceOrderRequest = {
        product_id: product.id,
        product_symbol: input.symbol,
        side: input.side,
        order_type: input.orderType,
        size: qtyCheck.normalizedQuantity,
        price: input.price,
        stop_price: input.stopPrice,
        stop_loss: input.stopLoss,
        take_profit: input.takeProfit,
        client_order_id: idempotencyKey,
        reduce_only: input.reduceOnly,
      };

      const result = await this.rest.placeOrder(orderPayload);
      const latencyMs = Date.now() - startTime;

      eventBus.emit('delta:order:placed', {
        orderId: result?.id,
        symbol: input.symbol,
        side: input.side,
        size: qtyCheck.normalizedQuantity,
        latencyMs,
      });

      return {
        success: true,
        orderId: result?.id,
        latencyMs,
        data: result,
      };
    } catch (err: any) {
      const latencyMs = Date.now() - startTime;
      const errorMsg = err?.response?.data?.error?.message || err?.message || 'Execution error';

      eventBus.emit('delta:order:failed', {
        symbol: input.symbol,
        error: errorMsg,
        latencyMs,
      });

      return {
        success: false,
        latencyMs,
        error: errorMsg,
      };
    }
  }

  public async cancelOrder(orderId: number, productId?: number | undefined): Promise<{
    success: boolean;
    latencyMs: number;
    error?: string | undefined;
  }> {
    const startTime = Date.now();
    try {
      await this.rest.cancelOrder(orderId, productId);
      const latencyMs = Date.now() - startTime;

      eventBus.emit('delta:order:cancelled', { orderId, latencyMs });
      return { success: true, latencyMs };
    } catch (err: any) {
      const latencyMs = Date.now() - startTime;
      const errorMsg = err?.response?.data?.error?.message || err?.message || 'Cancel error';
      return { success: false, latencyMs, error: errorMsg };
    }
  }
}
