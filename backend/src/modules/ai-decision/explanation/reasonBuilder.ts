import { DecisionReasonCode } from '@algoapp/shared';

const REASON_EXPLANATION_MAP: Record<DecisionReasonCode, string> = {
  [DecisionReasonCode.SESSION_OUTSIDE_ALLOWED_HOURS]:
    'Trade rejected: Timestamp is outside the configured institutional trading hours.',
  [DecisionReasonCode.WEEKEND_TRADING_BLOCKED]:
    'Trade rejected: Weekend trading is blocked by the session filter engine.',
  [DecisionReasonCode.MARKET_VOLATILITY_OUTLIER]:
    'Trade rejected: ATR14 exceeds 2.5x ATR200 indicating abnormal volatility outlier.',
  [DecisionReasonCode.MARKET_COMPRESSION_LOW_ATR]:
    'Trade rejected: Market volatility compression detected (ATR14 is below 0.25x ATR200).',
  [DecisionReasonCode.DUPLICATE_CANDLE_ENTRY_BLOCKED]:
    'Trade rejected: An entry has already been executed on this exact candle timestamp.',
  [DecisionReasonCode.DUPLICATE_ZONE_ENTRY_BLOCKED]:
    'Trade rejected: An entry has already been executed on this specific supply/demand zone.',
  [DecisionReasonCode.EXISTING_POSITION_OPEN]:
    'Trade rejected: An active open position already exists on this trading pair.',
  [DecisionReasonCode.COOLDOWN_ACTIVE]:
    'Trade rejected: Signal cooldown period is active for this pair.',
  [DecisionReasonCode.FRESH_ZONE_CONFIRMED]:
    'Fresh Supply/Demand zone confirmed with high liquidity accumulation and un-exhausted orders.',
  [DecisionReasonCode.FIRST_TOUCH_VALIDATED]:
    'Price action entered the key market zone boundary for the first time, offering maximum structural edge.',
  [DecisionReasonCode.MOMENTUM_ALIGNED]:
    'Price action momentum is strongly aligned with the proposed structural direction.',
  [DecisionReasonCode.CONFIDENCE_THRESHOLD_MET]:
    'Overall decision confidence score exceeded the strict execution threshold.',
  [DecisionReasonCode.OPPOSING_ZONE_BLOCKED]:
    'An immediate opposing zone was detected blocking price movement, invalidating risk/reward.',
  [DecisionReasonCode.ZONE_WIDTH_EXCEEDED]:
    'Zone width exceeds maximum allowable risk boundary parameters for this asset class.',
  [DecisionReasonCode.ZONE_FRESHNESS_DECAYED]:
    'Zone freshness score has degraded below 50.0% due to elapsed time or previous touches.',
  [DecisionReasonCode.ZONE_BROKEN_INVALIDATED]:
    'Zone was invalidated because price closed past the structural boundary limit.',
  [DecisionReasonCode.REPEATED_TOUCH_EXHAUSTED]:
    'Multiple repeated touches have exhausted available liquidity inside this zone.',
  [DecisionReasonCode.BOS_CONFIRMED]:
    'Break of Structure (BOS) confirmed in the direction of the trade.',
  [DecisionReasonCode.CHOCH_CONFIRMED]:
    'Change of Character (CHoCH) structural reversal confirmed.',
  [DecisionReasonCode.LIQUIDITY_SWEEP_CONFIRMED]:
    'Key liquidity pool swept prior to zone entry, providing smart money fuel.',
  [DecisionReasonCode.FVG_CONFLUENCE_CONFIRMED]:
    'Fair Value Gap (FVG) confluence detected supporting the entry.',
  [DecisionReasonCode.RR_BELOW_MINIMUM]:
    'Trade rejected: Calculated Risk-to-Reward ratio is below the institutional 2.0 minimum.',
  [DecisionReasonCode.DAILY_LOSS_LIMIT_REACHED]:
    'Trade rejected: Daily loss limit has been reached for this challenge account.',
  [DecisionReasonCode.MAX_DRAWDOWN_EXCEEDED]:
    'Trade rejected: Maximum allowable drawdown percentage exceeded.',
  [DecisionReasonCode.MAX_POSITIONS_REACHED]:
    'Trade rejected: Maximum concurrent open positions limit reached.',
  [DecisionReasonCode.INSUFFICIENT_MARGIN]:
    'Trade rejected: Insufficient available margin to fund position.',
  [DecisionReasonCode.POSITION_SIZE_CALCULATED]:
    'Institutional risk-based position size and leverage calculated deterministically.',
  [DecisionReasonCode.LEVERAGE_CAPPED]:
    'Leverage was capped to maximum allowable limits for this asset class.',
  [DecisionReasonCode.AI_CONFIRMATION_APPROVED]:
    'AI Confirmation layer approved the proposed strategy setup.',
  [DecisionReasonCode.AI_CONFIRMATION_REJECTED]:
    'AI Confirmation layer rejected the setup due to insufficient confidence or structural divergence.',
  [DecisionReasonCode.NEWS_MACRO_BLOCKED]:
    'Trade rejected: High-impact news or macroeconomic event blocking window is active.',
};

export class ReasonBuilder {
  public static buildHumanExplanation(reasonCode: DecisionReasonCode): string {
    return REASON_EXPLANATION_MAP[reasonCode] || `Rule ${reasonCode} evaluated against market structure.`;
  }
}
