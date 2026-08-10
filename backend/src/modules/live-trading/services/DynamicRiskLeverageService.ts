import { deltaSyncService } from '../../delta-exchange/index.js';

export interface RiskCalculationInput {
  accountBalance: number;
  entryPrice: number;
  stopLossPrice: number;
  direction: 'BUY' | 'SELL';
  orderBlockWidthPercent?: number;
  orderBlockUpperPrice?: number;
  orderBlockLowerPrice?: number;
  riskPercent?: number | undefined;
}

export interface RiskCalculationResult {
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  positionSize: number;
  notionalValue: number;
  leverage: number;
  marginRequired: number;
  riskAmount: number;
  riskPercent: number;
  rewardAmount: number;
  rewardPercent: number;
  riskRewardRatio: number;
  isValid: boolean;
  rejectionReason?: string;
}

/**
 * Dynamic Risk & Leverage Engine
 * 
 * Strategy Alignment:
 * §16: Use 100% of current account balance
 * §17: Maximum 100x leverage. SL hit = 35% account loss.
 * §18: SL at opposite edge of Order Block
 * §19: TP when account profit = 60%
 * §11: Width ≤0.6% → edge entry; Width >0.6% → 25% inside
 */
export class DynamicRiskLeverageService {
  private static readonly MAX_LEVERAGE = 100;
  private static readonly TARGET_RISK_PERCENT = 35.0;
  private static readonly TARGET_REWARD_PERCENT = 60.0;
  private static readonly MAX_SLIPPAGE_BUFFER = 0.5; // 0.5% extra buffer

  /**
   * Calculate position sizing, leverage, entry, SL, and TP
   * based on strategy rules.
   */
  public static calculateRiskAndLeverage(input: RiskCalculationInput): RiskCalculationResult {
    const {
      accountBalance,
      entryPrice: rawEntryPrice,
      stopLossPrice: rawStopLoss,
      direction,
      orderBlockWidthPercent = 0,
      orderBlockUpperPrice,
      orderBlockLowerPrice,
      riskPercent: customRiskPercent,
    } = input;

    // Validate inputs
    if (accountBalance <= 0) {
      return this.reject('Account balance is zero or negative');
    }
    if (rawEntryPrice <= 0 || rawStopLoss <= 0) {
      return this.reject('Invalid entry or stop loss price');
    }

    // ── Strategy §11: Width-Based Entry Price ──
    let entryPrice = rawEntryPrice;
    const obWidth = (orderBlockUpperPrice && orderBlockLowerPrice)
      ? Math.abs(orderBlockUpperPrice - orderBlockLowerPrice)
      : Math.abs(rawEntryPrice - rawStopLoss);

    const widthPercent = orderBlockWidthPercent > 0
      ? orderBlockWidthPercent
      : (obWidth / Math.max(rawEntryPrice, rawStopLoss)) * 100;

    if (orderBlockUpperPrice && orderBlockLowerPrice) {
      if (direction === 'BUY') {
        // Bullish OB: enter at upper edge if narrow, or 25% inside if wide
        entryPrice = widthPercent <= 0.6
          ? orderBlockUpperPrice
          : orderBlockUpperPrice - (0.25 * obWidth);
      } else {
        // Bearish OB: enter at lower edge if narrow, or 25% inside if wide
        entryPrice = widthPercent <= 0.6
          ? orderBlockLowerPrice
          : orderBlockLowerPrice + (0.25 * obWidth);
      }
    }

    entryPrice = Number(entryPrice.toFixed(4));

    // ── Strategy §18: Stop Loss at opposite edge ──
    let stopLossPrice = rawStopLoss;
    if (orderBlockUpperPrice && orderBlockLowerPrice) {
      stopLossPrice = direction === 'BUY' ? orderBlockLowerPrice : orderBlockUpperPrice;
    }
    stopLossPrice = Number(stopLossPrice.toFixed(4));

    const slDistance = Math.abs(entryPrice - stopLossPrice);
    if (slDistance <= 0) {
      return this.reject('Stop loss distance is zero');
    }

    // ── Strategy §17: Risk = custom riskPercent or 35% of account balance ──
    const targetRiskPct = customRiskPercent !== undefined ? customRiskPercent : this.TARGET_RISK_PERCENT;
    const riskAmount = accountBalance * (targetRiskPct / 100);
    
    // Position size = risk amount ÷ price distance to SL
    const positionSize = riskAmount / slDistance;
    
    // ── Strategy §16: Use 100% balance as notional ──
    const notionalValue = positionSize * entryPrice;
    
    // Leverage = notional ÷ account balance (using 100% of balance as margin)
    let leverage = notionalValue / accountBalance;
    leverage = Math.min(leverage, this.MAX_LEVERAGE);
    leverage = Math.max(leverage, 1); // Minimum 1x
    
    // Recalculate notional and size based on capped leverage
    const marginRequired = notionalValue / leverage;
    
    // ── Strategy §19: TP = 60% account profit ──
    // Profit needed = 60% of balance
    // Price move needed = profit ÷ position size
    const rewardAmount = accountBalance * (this.TARGET_REWARD_PERCENT / 100);
    const priceMoveToTP = rewardAmount / positionSize;
    
    const takeProfitPrice = direction === 'BUY'
      ? Number((entryPrice + priceMoveToTP).toFixed(4))
      : Number((entryPrice - priceMoveToTP).toFixed(4));

    // Validate TP direction
    if (direction === 'BUY' && takeProfitPrice <= entryPrice) {
      return this.reject('Take profit must be above entry for BUY');
    }
    if (direction === 'SELL' && takeProfitPrice >= entryPrice) {
      return this.reject('Take profit must be below entry for SELL');
    }

    const riskRewardRatio = this.TARGET_REWARD_PERCENT / this.TARGET_RISK_PERCENT; // 60/35 ≈ 1.71

    // Check if leverage cap prevents achieving 35% risk
    const actualRiskPercent = (riskAmount / accountBalance) * 100;
    const isValid = leverage <= this.MAX_LEVERAGE && actualRiskPercent <= (this.TARGET_RISK_PERCENT + this.MAX_SLIPPAGE_BUFFER);

    const result: RiskCalculationResult = {
      entryPrice,
      stopLossPrice,
      takeProfitPrice,
      positionSize: Number(positionSize.toFixed(8)),
      notionalValue: Number(notionalValue.toFixed(2)),
      leverage: Number(leverage.toFixed(2)),
      marginRequired: Number(marginRequired.toFixed(2)),
      riskAmount: Number(riskAmount.toFixed(2)),
      riskPercent: Number(actualRiskPercent.toFixed(2)),
      rewardAmount: Number(rewardAmount.toFixed(2)),
      rewardPercent: this.TARGET_REWARD_PERCENT,
      riskRewardRatio: Number(riskRewardRatio.toFixed(2)),
      isValid,
    };
    if (!isValid) {
      result.rejectionReason = `Leverage ${leverage.toFixed(2)}x exceeds max ${this.MAX_LEVERAGE}x or risk exceeds limit`;
    }
    return result;
  }

  /**
   * Convenience method that fetches live balance from Delta
   * and calculates risk for a given Order Block.
   */
  public static async calculateFromLiveBalance(params: {
    symbol: string;
    direction: 'BUY' | 'SELL';
    orderBlockUpperPrice: number;
    orderBlockLowerPrice: number;
    orderBlockWidthPercent: number;
  }): Promise<RiskCalculationResult> {
    const balances = deltaSyncService.getBalances();
    const usdtBalance = balances.find(
      (b) => b.asset_symbol === 'USDT' || b.asset_symbol === 'USD'
    );

    if (!usdtBalance) {
      return this.reject('No USDT/USD balance available from Delta Exchange');
    }

    const accountBalance = parseFloat(usdtBalance.balance || '0');
    if (accountBalance <= 0) {
      return this.reject('Account balance is zero');
    }

    // Determine entry and SL based on direction and width rule
    const entryPrice = params.direction === 'BUY'
      ? (params.orderBlockWidthPercent <= 0.6 ? params.orderBlockUpperPrice : params.orderBlockUpperPrice - 0.25 * (params.orderBlockUpperPrice - params.orderBlockLowerPrice))
      : (params.orderBlockWidthPercent <= 0.6 ? params.orderBlockLowerPrice : params.orderBlockLowerPrice + 0.25 * (params.orderBlockUpperPrice - params.orderBlockLowerPrice));

    const stopLossPrice = params.direction === 'BUY'
      ? params.orderBlockLowerPrice
      : params.orderBlockUpperPrice;

    return this.calculateRiskAndLeverage({
      accountBalance,
      entryPrice,
      stopLossPrice,
      direction: params.direction,
      orderBlockWidthPercent: params.orderBlockWidthPercent,
      orderBlockUpperPrice: params.orderBlockUpperPrice,
      orderBlockLowerPrice: params.orderBlockLowerPrice,
    });
  }

  private static reject(reason: string): RiskCalculationResult {
    return {
      entryPrice: 0,
      stopLossPrice: 0,
      takeProfitPrice: 0,
      positionSize: 0,
      notionalValue: 0,
      leverage: 0,
      marginRequired: 0,
      riskAmount: 0,
      riskPercent: 0,
      rewardAmount: 0,
      rewardPercent: 0,
      riskRewardRatio: 0,
      isValid: false,
      rejectionReason: reason,
    };
  }
}
