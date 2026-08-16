import {
  ExecutionRequestDto,
  ExecutionResultDto,
  ExecutionMode,
  ExecutionStatus,
} from '@algoapp/shared';

import { IExecutionAdapter, AdapterValidationResult } from './executionAdapter.interface.js';
import { DeltaRestClient, DeltaPlaceOrderRequest, DeltaOrder } from '../../delta-exchange/services/DeltaRestClient.js';
import { ExecutionEngineService } from '../../execution-engine/services/ExecutionEngineService.js';
import { LiveTradingGuard } from '../../production/services/liveTradingGuard.js';

export class DeltaAdapter implements IExecutionAdapter {
  public readonly name = 'DELTA_ADAPTER';
  public readonly mode = ExecutionMode.LIVE;

  constructor(private restClient: DeltaRestClient) {}

  public async validate(request: ExecutionRequestDto): Promise<AdapterValidationResult> {
    try {
      // Validate basic order parameters
      if (!request.symbol || !request.side || !request.quantity || request.quantity <= 0) {
        return { valid: false, reason: 'Invalid order parameters' };
      }
      if (request.orderType === 'LIMIT' && (!request.price || request.price <= 0)) {
        return { valid: false, reason: 'Limit price required for LIMIT orders' };
      }
      if (request.stopLoss && request.stopLoss <= 0) {
        return { valid: false, reason: 'Invalid stop loss price' };
      }
      if (request.takeProfit && request.takeProfit <= 0) {
        return { valid: false, reason: 'Invalid take profit price' };
      }

      // Check contract quantity step and minimum compliance
      const qtyCheck = ExecutionEngineService.normalizeContractQuantity(request.symbol, request.quantity);
      if (!qtyCheck.isValid) {
        return { valid: false, reason: qtyCheck.reason || 'Invalid order quantity' };
      }

      // Check if symbol is supported
      const product = this.restClient.getProduct(request.symbol);
      if (!product) {
        return { valid: false, reason: `Unsupported symbol: ${request.symbol}` };
      }

      // Check balance for margin
      const balances = this.restClient.getWalletBalances ? await this.restClient.getWalletBalances() : [];
      const usdtBalance = balances.find((b) => b.asset_symbol === 'USDT' || b.asset_symbol === 'USD');
      if (!usdtBalance || parseFloat(usdtBalance.available_balance || '0') <= 0) {
        return { valid: false, reason: 'Insufficient USDT balance' };
      }

      return { valid: true };
    } catch (err) {
      return { valid: false, reason: `Validation error: ${err instanceof Error ? err.message : 'Unknown error'}` };
    }
  }

  public async submit(request: ExecutionRequestDto): Promise<ExecutionResultDto> {
    const startTime = Date.now();
// ── F-3: Product Metadata Validation (must pass before LIVE safety) ────────────
    const product = this.restClient.getProduct(request.symbol);
    if (!product) {
      return this.errorResult(request, `Product not found: ${request.symbol}`, startTime);
    }

    // Validate contract quantity step and minimum compliance against product metadata
    const qtyCheck = ExecutionEngineService.normalizeContractQuantity(request.symbol, request.quantity, product);
    if (!qtyCheck.isValid) {
      return this.errorResult(request, qtyCheck.reason || 'Invalid order quantity', startTime);
    }
    const normalizedQuantity = qtyCheck.normalizedQuantity;
    // ────────────────────────────────────────────────────────────────────────

    // ── F-3: Mandatory LIVE Safety Guard ───────────────────────────────────────────
    const liveSafety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    if (!liveSafety.isAllowed) {
      return this.errorResult(
        request,
        `LIVE_SAFETY_REJECTED: ${liveSafety.rejectionReasons.join('; ')}`,
        startTime
      );
    }
    // ────────────────────────────────────────────────────────────────────────

    // Convert LONG/SHORT to Delta's buy/sell
    const deltaSide = request.side === 'LONG' ? 'buy' : 'sell';

    if (!product) {
      return this.errorResult(request, `Product not found: ${request.symbol}`, startTime);
    }

    const deltaOrder: DeltaPlaceOrderRequest = {
      product_id: product.id,
      product_symbol: request.symbol,
      side: deltaSide,
      order_type: request.orderType === 'MARKET' ? 'market' : 'limit',
      size: normalizedQuantity,
      price: request.price,
      stop_price: request.stopPrice,
      stop_loss: request.stopLoss,
      take_profit: request.takeProfit,
      client_order_id: request.idempotencyKey,
      reduce_only: false,
      post_only: request.orderType === 'LIMIT',
    };

    try {
      const result = await this.restClient.placeOrder(deltaOrder);
      if (!result || !result.id) {
        return this.errorResult(request, 'Order placement failed: no order ID returned', startTime);
      }

      const deltaOrderResult = result as DeltaOrder & { filled_size?: number; avg_price?: number; filled_price?: number };
      const filledQty = parseFloat(String(deltaOrderResult.filled_size ?? deltaOrderResult.size ?? 0));
      const fillPrice = parseFloat(String(deltaOrderResult.avg_price ?? deltaOrderResult.filled_price ?? deltaOrderResult.price ?? 0));

      return {
        id: `RES-${Date.now()}`,
        requestId: request.id,
        sessionId: request.sessionId,
        adapter: this.name,
        status: filledQty > 0 ? ExecutionStatus.FILLED : ExecutionStatus.SUBMITTED,
        filledQuantity: filledQty,
        fillPrice: fillPrice > 0 ? fillPrice : undefined,
        observability: {
          queueTimeMs: 1,
          validationLatencyMs: 1,
          adapterLatencyMs: Date.now() - startTime,
          totalLifecycleTimeMs: Date.now() - startTime,
        },
        message: filledQty > 0 ? 'Order filled' : 'Order submitted',
        timestamp: new Date().toISOString(),
      };
    } catch (err) {
      return this.errorResult(request, `Order submission failed: ${err instanceof Error ? err.message : 'Unknown error'}`, startTime);
    }
  }

  public async modify(orderId: string, input: Partial<ExecutionRequestDto>): Promise<ExecutionResultDto> {
    const startTime = Date.now();
    try {
      // Delta requires cancel + replace for modifications
      const product = this.restClient.getProduct(input.symbol || '');
      if (!product) {
        return this.errorResult({ ...input, id: orderId } as ExecutionRequestDto, 'Product not found for modify', startTime);
      }

      // Cancel existing order
      const existingOrderId = parseInt(orderId);
      await this.restClient.cancelOrder(existingOrderId, product.id);

      // Place new order
      const newRequest: ExecutionRequestDto = {
        ...input,
        id: `MOD-${Date.now()}`,
        idempotencyKey: `MOD-${orderId}-${Date.now()}`,
      } as ExecutionRequestDto;

      return this.submit(newRequest);
    } catch (err) {
      return this.errorResult({ ...input, id: orderId } as ExecutionRequestDto, `Modify failed: ${err instanceof Error ? err.message : 'Unknown error'}`, startTime);
    }
  }

  public async cancel(orderId: string): Promise<ExecutionResultDto> {
    const startTime = Date.now();
    try {
      // Find product from active orders
      const orders = await this.restClient.getOrders({ status: 'open' });
      const order = orders.find((o) => String(o.id) === orderId);
      if (!order) {
        return this.errorResult({ id: orderId } as ExecutionRequestDto, 'Order not found for cancellation', startTime);
      }

      const productId = order.id;
      await this.restClient.cancelOrder(productId, order.product_id);

      return {
        id: `RES-${Date.now()}`,
        requestId: `REQ-CANCEL-${orderId}`,
        sessionId: 'SESSION-DEFAULT',
        adapter: this.name,
        status: ExecutionStatus.CANCELLED,
        filledQuantity: 0,
        observability: {
          queueTimeMs: 1,
          validationLatencyMs: 1,
          adapterLatencyMs: Date.now() - startTime,
          totalLifecycleTimeMs: Date.now() - startTime,
        },
        message: 'Order cancelled',
        timestamp: new Date().toISOString(),
      };
    } catch (err) {
      return this.errorResult({ id: orderId } as ExecutionRequestDto, `Cancel failed: ${err instanceof Error ? err.message : 'Unknown error'}`, startTime);
    }
  }

  public async closePosition(symbol: string, exitPrice: number): Promise<ExecutionResultDto> {
    const startTime = Date.now();
    try {
      // ── F-3 Position integrity checks ───────────────────────────────────────────
      const positions = await this.restClient.getPositions();
      const position = positions.find((p) => p.product_symbol === symbol && p.size !== 0);
      if (!position) {
        return this.errorResult({ symbol } as ExecutionRequestDto, 'No open position to close', startTime);
      }

      const product = this.restClient.getProduct(symbol);
      if (!product) {
        return this.errorResult({ symbol } as ExecutionRequestDto, 'Product not found', startTime);
      }

      // Enforce exact reverse side — never open or increase
      const closeSide = position.side === 'buy' ? 'sell' : 'buy';
      // Clamp to actual open size — never exceed
      const closeSize = Math.abs(position.size);

      const closeOrder: DeltaPlaceOrderRequest = {
        product_id: product.id,        // authoritative product ID
        product_symbol: symbol,
        side: closeSide,               // forced exact reverse
        order_type: 'market',
        size: closeSize,               // clamped to actual
        reduce_only: true,             // enforce: never increase exposure
        client_order_id: `CLOSE-${Date.now()}`,
      };

      const result = await this.restClient.placeOrder(closeOrder);
      const deltaOrderResult = result as DeltaOrder & { filled_size?: number; avg_price?: number };
      const filledQty = parseFloat(String(deltaOrderResult.filled_size ?? deltaOrderResult.size ?? 0));
      const fillPrice = parseFloat(String(deltaOrderResult.avg_price ?? deltaOrderResult.price ?? exitPrice));

      return {
        id: `RES-${Date.now()}`,
        requestId: `REQ-CLOSE-${symbol}`,
        sessionId: 'SESSION-DEFAULT',
        adapter: this.name,
        status: filledQty > 0 ? ExecutionStatus.FILLED : ExecutionStatus.SUBMITTED,
        filledQuantity: filledQty,
        fillPrice: fillPrice > 0 ? fillPrice : exitPrice,
        observability: {
          queueTimeMs: 1,
          validationLatencyMs: 1,
          adapterLatencyMs: Date.now() - startTime,
          totalLifecycleTimeMs: Date.now() - startTime,
        },
        message: filledQty > 0 ? 'Position closed' : 'Close order submitted',
        timestamp: new Date().toISOString(),
      };
    } catch (err) {
      return this.errorResult({ symbol } as ExecutionRequestDto, `Close position failed: ${err instanceof Error ? err.message : 'Unknown error'}`, startTime);
    }
  }

  public async synchronize(): Promise<void> {
    // Trigger reconciliation via DeltaSyncService
    // This will be called by the sync timer
  }

  public async getExecutionStatus(orderId: string): Promise<ExecutionStatus> {
    try {
      const orders = await this.restClient.getOrders({ status: 'open' });
      const order = orders.find((o) => String(o.id) === orderId);
      if (!order) {
        const history = await this.restClient.getHistory({ limit: 100 });
        const histOrder = history.find((o) => String(o.id) === orderId);
        if (histOrder) {
          return histOrder.state === 'closed' ? ExecutionStatus.FILLED : ExecutionStatus.CANCELLED;
        }
        return ExecutionStatus.REJECTED;
      }
      return order.state === 'closed' ? ExecutionStatus.FILLED : ExecutionStatus.SUBMITTED;
    } catch {
      return ExecutionStatus.REJECTED;
    }
  }

  private errorResult(request: ExecutionRequestDto, message: string, startTime: number): ExecutionResultDto {
    return {
      id: `RES-${Date.now()}`,
      requestId: request.id,
      sessionId: request.sessionId,
      adapter: this.name,
      status: ExecutionStatus.REJECTED,
      filledQuantity: 0,
      observability: {
        queueTimeMs: 1,
        validationLatencyMs: 1,
        adapterLatencyMs: Date.now() - startTime,
        totalLifecycleTimeMs: Date.now() - startTime,
      },
      message,
      timestamp: new Date().toISOString(),
    };
  }
}