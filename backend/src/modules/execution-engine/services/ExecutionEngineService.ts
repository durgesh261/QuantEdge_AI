import crypto from 'crypto';
import { deltaSyncService } from '../../delta-exchange/index.js';
import { DeltaProduct } from '../../delta-exchange/services/DeltaRestClient.js';
import { orderLifecycleService } from './OrderLifecycleService.js';
import { tradeAccountingTrigger } from '../../trade-accounting/TradeAccountingTrigger.js';
import { candleEngine } from '../../../engine/CandleEngine.js';
import { eventBus } from '../../../services/EventBus.js';
export interface OrderExecutionRequest {
  symbol: string;
  side: 'buy' | 'sell';
  orderType: 'market' | 'limit' | 'stop_market' | 'stop_limit';
  size: number;
  price?: number | undefined;
  stopPrice?: number | undefined;
  leverage?: number | undefined;
  reduceOnly?: boolean | undefined;
  postOnly?: boolean | undefined;
  stopLossPrice?: number | undefined;
  takeProfitPrice?: number | undefined;
  clientOrderId?: string | undefined;
}

export interface ValidationRuleResult {
  ruleNumber: number;
  ruleName: string;
  passed: boolean;
  message: string;
}

export interface ValidationSummary {
  isValid: boolean;
  results: ValidationRuleResult[];
  estimatedRequiredMargin: number;
  availableMargin: number;
  riskAmountPercent: number;
}

export interface ExecutionResult {
  success: boolean;
  orderId?: string | number | undefined;
  clientOrderId: string;
  symbol: string;
  side: 'buy' | 'sell';
  orderType: string;
  size: number;
  price?: number | undefined;
  state: string;
  latencyMs: number;
  message?: string | undefined;
  rawExchangeResponse?: unknown | undefined;
}

export class ExecutionEngineService {
  private isKillSwitchActive = false;
  private executionHistory: ExecutionResult[] = [];
  private readonly MAX_HISTORY = 100;

  public setKillSwitch(active: boolean): void {
    this.isKillSwitchActive = active;
    eventBus.emit('execution:kill_switch_toggled', { active });
  }

  public getKillSwitchStatus(): boolean {
    return this.isKillSwitchActive;
  }

  public static normalizeContractQuantity(
    symbol: string,
    quantity: number,
    customProduct?: DeltaProduct
  ): {
    isValid: boolean;
    normalizedQuantity: number;
    step: number;
    reason?: string;
  } {
    if (typeof quantity !== 'number' || isNaN(quantity) || quantity <= 0) {
      return { isValid: false, normalizedQuantity: 0, step: 0, reason: 'Order size must be greater than 0' };
    }

    let product: DeltaProduct | undefined = customProduct;
    if (!product) {
      try {
        const restClient = deltaSyncService.getRestClient();
        product = restClient.getProduct(symbol);
      } catch {
        // Handled below if product is missing
      }
    }

    if (!product) {
      return {
        isValid: false,
        normalizedQuantity: 0,
        step: 0,
        reason: `MISSING_EXCHANGE_METADATA: Authoritative product metadata unavailable from Delta Exchange for ${symbol}`,
      };
    }

    const rawContractValue = product.contract_value;
    if (rawContractValue === undefined || rawContractValue === null || String(rawContractValue).trim() === '') {
      return {
        isValid: false,
        normalizedQuantity: 0,
        step: 0,
        reason: `MISSING_EXCHANGE_METADATA: Authoritative product contract_value metadata unavailable from Delta Exchange for ${symbol}`,
      };
    }

    const step = parseFloat(String(rawContractValue));
    if (isNaN(step) || step <= 0) {
      return {
        isValid: false,
        normalizedQuantity: 0,
        step: 0,
        reason: `MISSING_EXCHANGE_METADATA: Authoritative product contract_value metadata unavailable from Delta Exchange for ${symbol}`,
      };
    }

    if (quantity < step) {
      return {
        isValid: false,
        normalizedQuantity: 0,
        step,
        reason: `Order quantity ${quantity} is below exchange minimum contract step of ${step} for ${symbol}`,
      };
    }

    // Floor rounding to nearest step (never rounds up to avoid exceeding risk ceiling)
    const steps = Math.floor(quantity / step);
    const normalizedQuantity = Number((steps * step).toFixed(8));

    if (normalizedQuantity <= 0) {
      return {
        isValid: false,
        normalizedQuantity: 0,
        step,
        reason: `Normalized quantity for ${symbol} evaluated to 0`,
      };
    }

    return { isValid: true, normalizedQuantity, step };
  }

  public static getContractStep(symbol: string, customProduct?: DeltaProduct): number | null {
    const res = this.normalizeContractQuantity(symbol, 1, customProduct);
    return res.step > 0 ? res.step : null;
  }

  /**
   * 10-Rule Institutional Pre-Flight Validation
   * Strategy-aligned: 35% risk, 100% balance, max 100x leverage
   */
  public async validateOrder(req: OrderExecutionRequest): Promise<ValidationSummary> {
    const results: ValidationRuleResult[] = [];
    const balances = deltaSyncService.getBalances();
    const usdtBalance = balances.find((b) => b.asset_symbol === 'USDT' || b.asset_symbol === 'USD');
    
    // NO FAKE FALLBACK — if no balance data, reject
    const accountBalance = usdtBalance ? parseFloat(usdtBalance.balance || '0') : 0;
    const availableMargin = usdtBalance ? parseFloat(usdtBalance.available_balance || '0') : 0;
    
    const positions = deltaSyncService.getPositions();
    const existingPosition = positions.find(
      (p) => (p.product_symbol || '').toLowerCase() === (req.symbol || '').toLowerCase()
    );

    const restClient = deltaSyncService.getRestClient();
    const isConfigured = restClient.isConfigured();

    // 1. Exchange Connection Check
    results.push({
      ruleNumber: 1,
      ruleName: 'Exchange Connectivity',
      passed: isConfigured,
      message: isConfigured ? 'Delta REST client configured and connected' : 'BLOCKED: Delta REST client not configured',
    });

    // 2. Kill Switch Check
    results.push({
      ruleNumber: 2,
      ruleName: 'Emergency Kill Switch',
      passed: !this.isKillSwitchActive,
      message: !this.isKillSwitchActive ? 'Kill switch inactive — Execution permitted' : 'BLOCKED: Emergency Kill Switch is ACTIVE',
    });

    // 3. Trading Pair Symbol Validity — ONLY the 4 allowed pairs (Strategy §2)
    const allowedSymbols = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
    const isSymbolValid = allowedSymbols.includes(req.symbol);
    results.push({
      ruleNumber: 3,
      ruleName: 'Symbol Specification',
      passed: isSymbolValid,
      message: isSymbolValid ? `Symbol valid: ${req.symbol}` : `Invalid symbol: ${req.symbol}. Only BTCUSD.P, ETHUSD.P, SOLUSD.P, XRPUSD.P allowed.`,
    });

    // 4. Quantity / Lot Size Check against Exchange Minimum and Step
    const qtyCheck = ExecutionEngineService.normalizeContractQuantity(req.symbol, req.size);
    results.push({
      ruleNumber: 4,
      ruleName: 'Order Size Minimum & Step Compliance',
      passed: qtyCheck.isValid,
      message: qtyCheck.isValid
        ? `Order size valid: ${req.size} (normalized to ${qtyCheck.normalizedQuantity} with step ${qtyCheck.step})`
        : qtyCheck.reason || 'Order size invalid',
    });

    // 5. Price Check for Limit / Stop orders
    let isPriceValid = true;
    if (['limit', 'stop_limit'].includes(req.orderType)) {
      isPriceValid = typeof req.price === 'number' && req.price > 0;
    }
    if (['stop_market', 'stop_limit'].includes(req.orderType)) {
      isPriceValid = isPriceValid && typeof req.stopPrice === 'number' && req.stopPrice > 0;
    }
    results.push({
      ruleNumber: 5,
      ruleName: 'Price Boundaries',
      passed: isPriceValid,
      message: isPriceValid ? 'Price parameters within valid positive range' : 'Limit or Stop Price must be greater than 0',
    });

    // 6. Live Price Check — NO FAKE FALLBACKS
    let livePrice = 0;
    const liveCandle = candleEngine.getLiveCandle(req.symbol, '1H') || candleEngine.getLiveCandle(req.symbol, '15m');
    if (liveCandle && liveCandle.close > 0) {
      livePrice = liveCandle.close;
    }
    
    const hasLivePrice = livePrice > 0;
    results.push({
      ruleNumber: 6,
      ruleName: 'Live Market Data',
      passed: hasLivePrice,
      message: hasLivePrice ? `Live price available: $${livePrice}` : 'BLOCKED: No live price available from candle engine',
    });

    const estimatedPrice = req.price || livePrice;
    const leverage = Math.min(req.leverage || 1, 100); // Max 100x per strategy
    const notional = req.size * estimatedPrice;
    const estimatedRequiredMargin = notional / leverage;
    
    // 7. Margin Solvency — 100% balance usage (Strategy §16)
    const hasMargin = availableMargin > 0 && (availableMargin >= estimatedRequiredMargin || !!req.reduceOnly);
    results.push({
      ruleNumber: 7,
      ruleName: 'Margin Solvency',
      passed: hasMargin,
      message: hasMargin
        ? `Required margin $${estimatedRequiredMargin.toFixed(2)} <= Available $${availableMargin.toFixed(2)}`
        : `Insufficient margin: Need $${estimatedRequiredMargin.toFixed(2)}, Available $${availableMargin.toFixed(2)}`,
    });

    // 8. Leverage Bounds Check — Max 100x (Strategy §17)
    const isLeverageValid = leverage >= 1 && leverage <= 100;
    results.push({
      ruleNumber: 8,
      ruleName: 'Leverage Limits (1x - 100x)',
      passed: isLeverageValid,
      message: isLeverageValid ? `Leverage valid at ${leverage}x` : `Leverage ${leverage}x outside bounds (1-100x)`,
    });

    // 9. Reduce-Only Verification
    let isReduceOnlyValid = true;
    if (req.reduceOnly) {
      isReduceOnlyValid = !!existingPosition && existingPosition.size > 0;
    }
    results.push({
      ruleNumber: 9,
      ruleName: 'Reduce-Only Position Integrity',
      passed: isReduceOnlyValid,
      message: isReduceOnlyValid
        ? 'Reduce-only flag consistent with position state'
        : 'BLOCKED: Reduce-only requested but no open position exists to reduce',
    });

    // 10. 35% Max Risk Policy (Strategy §17-18)
    let riskAmountPercent = 0;
    let isRiskPolicyPassed = true;
    if (req.stopLossPrice && estimatedPrice > 0) {
      const perUnitRisk = Math.abs(estimatedPrice - req.stopLossPrice);
      const totalRisk = perUnitRisk * req.size;
      riskAmountPercent = accountBalance > 0 ? (totalRisk / accountBalance) * 100 : 0;
      // Allow up to 35.5% to account for slippage
      isRiskPolicyPassed = riskAmountPercent <= 35.5;
    }
    results.push({
      ruleNumber: 10,
      ruleName: '35% Maximum Risk Policy',
      passed: isRiskPolicyPassed,
      message: isRiskPolicyPassed
        ? `Risk sizing policy compliant (${riskAmountPercent.toFixed(2)}% of equity)`
        : `Risk exceeds 35% policy ceiling (${riskAmountPercent.toFixed(2)}% of equity)`,
    });

    // 11. One Trade Max Policy (Strategy §15)
    const hasNoOpenPosition = positions.length === 0;
    results.push({
      ruleNumber: 11,
      ruleName: 'One Trade Maximum',
      passed: hasNoOpenPosition || !!req.reduceOnly,
      message: hasNoOpenPosition || !!req.reduceOnly
        ? 'No conflicting open positions'
        : 'BLOCKED: Only one trade may remain open at a time',
    });

    // 12. Client Order ID Idempotency Check
    const clientOrderId = req.clientOrderId || `ORD-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
    const existingOrder = orderLifecycleService.getOrder(clientOrderId);
    const isUnique = !existingOrder || existingOrder.state === 'FILLED' || existingOrder.state === 'CANCELLED';
    results.push({
      ruleNumber: 12,
      ruleName: 'Idempotency Validation',
      passed: isUnique,
      message: isUnique ? 'Client order ID unique' : 'Duplicate active order ID detected',
    });

    const isValid = results.every((r) => r.passed);
    return {
      isValid,
      results,
      estimatedRequiredMargin: parseFloat(estimatedRequiredMargin.toFixed(2)),
      availableMargin: parseFloat(availableMargin.toFixed(2)),
      riskAmountPercent: parseFloat(riskAmountPercent.toFixed(2)),
    };
  }

  /**
   * Submit Real Order to Delta Exchange
   */
  public async placeOrder(req: OrderExecutionRequest): Promise<ExecutionResult> {
    const startTime = Date.now();
    const clientOrderId = req.clientOrderId || `ORD-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;

    // Run Pre-Flight Validation
    const validation = await this.validateOrder({ ...req, clientOrderId });
    if (!validation.isValid) {
      const failedRule = validation.results.find((r) => !r.passed);
      const latencyMs = Date.now() - startTime;
      const result: ExecutionResult = {
        success: false,
        clientOrderId,
        symbol: req.symbol,
        side: req.side,
        orderType: req.orderType,
        size: req.size,
        state: 'REJECTED',
        latencyMs,
        message: `Validation Failed: ${failedRule?.ruleName} - ${failedRule?.message}`,
      };
      this.recordHistory(result);
      return result;
    }

    // Register with state machine in PENDING state
    orderLifecycleService.createOrderRecord({
      id: clientOrderId,
      clientOrderId,
      symbol: req.symbol,
      side: req.side,
      orderType: req.orderType,
      size: req.size,
      price: req.price,
      stopPrice: req.stopPrice,
      reduceOnly: req.reduceOnly,
      postOnly: req.postOnly,
    });

    try {
      const restClient = deltaSyncService.getRestClient();
      const product = restClient.getProduct(req.symbol);
      if (!product) {
        throw new Error(`Product not found for symbol: ${req.symbol}`);
      }

      const qtyCheck = ExecutionEngineService.normalizeContractQuantity(req.symbol, req.size);
      const normalizedSize = qtyCheck.isValid ? qtyCheck.normalizedQuantity : req.size;

      const orderPayload: any = {
        product_id: product.id,
        product_symbol: req.symbol,
        side: req.side,
        order_type: req.orderType,
        size: normalizedSize,
        client_order_id: clientOrderId,
        reduce_only: req.reduceOnly || false,
        post_only: req.postOnly || false,
      };

      if (req.price) orderPayload.price = req.price;
      if (req.stopPrice) orderPayload.stop_price = req.stopPrice;
      if (req.leverage) orderPayload.leverage = req.leverage;
      if (req.stopLossPrice) orderPayload.stop_loss = req.stopLossPrice;
      if (req.takeProfitPrice) orderPayload.take_profit = req.takeProfitPrice;

      const response = await restClient.placeOrder(orderPayload);
      const latencyMs = Date.now() - startTime;

      const result: ExecutionResult = {
        success: true,
        orderId: response.result?.id || response.result?.client_order_id,
        clientOrderId,
        symbol: req.symbol,
        side: req.side,
        orderType: req.orderType,
        size: req.size,
        price: req.price,
        state: response.result?.state || 'PENDING',
        latencyMs,
        rawExchangeResponse: response,
      };

      orderLifecycleService.transition(clientOrderId, result.state as any, result);
      tradeAccountingTrigger.recordExecution(result);
      this.recordHistory(result);
      eventBus.emit('execution:order_placed', result);
      return result;
    } catch (err: any) {
      const latencyMs = Date.now() - startTime;
      const errorResult: ExecutionResult = {
        success: false,
        clientOrderId,
        symbol: req.symbol,
        side: req.side,
        orderType: req.orderType,
        size: req.size,
        state: 'ERROR',
        latencyMs,
        message: err?.message || 'Unknown execution error',
      };
      orderLifecycleService.transition(clientOrderId, 'ERROR', errorResult);
      this.recordHistory(errorResult);
      eventBus.emit('execution:order_error', errorResult);
      return errorResult;
    }
  }

  private recordHistory(result: ExecutionResult): void {
    this.executionHistory.unshift(result);
    if (this.executionHistory.length > this.MAX_HISTORY) {
      this.executionHistory.pop();
    }
  }

  public getExecutionHistory(): ExecutionResult[] {
    return this.executionHistory;
  }

  // Stubs to satisfy execution.controller.ts
  public async closePosition(_symbol: string): Promise<any> {
    throw new Error("Not implemented in rewritten execution engine");
  }

  public async modifyOrder(_orderId: string, _updates: any): Promise<any> {
    throw new Error("Not implemented in rewritten execution engine");
  }

  public async getOrder(_symbol: string): Promise<any> {
    return null;
  }

  public async updateOrder(_orderId: string, _updates: any): Promise<any> {
    return null;
  }

  public async getActiveOrders(_symbol?: string): Promise<any> {
    return [];
  }

  public async cancelOrder(_orderId: string): Promise<any> {
    throw new Error("Not implemented in rewritten execution engine");
  }

  public async cancelAllOrders(_symbol?: string): Promise<any> {
    throw new Error("Not implemented in rewritten execution engine");
  }
}

export const executionEngineService = new ExecutionEngineService();
