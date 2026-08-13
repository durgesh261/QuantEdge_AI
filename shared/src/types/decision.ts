import { TradingTimeframe } from './strategyProfile.js';
import { StrategySignalOutcome } from './strategy.js';
import { IndicatorEngineOutput } from './indicatorEngine.js';

export enum DecisionState {
  EXECUTE = 'EXECUTE',
  WAIT = 'WAIT',
  SKIP = 'SKIP',
  INVALID = 'INVALID',
}

export enum DecisionReasonCode {
  // Zone & Touch
  FRESH_ZONE_CONFIRMED = 'FRESH_ZONE_CONFIRMED',
  FIRST_TOUCH_VALIDATED = 'FIRST_TOUCH_VALIDATED',
  MOMENTUM_ALIGNED = 'MOMENTUM_ALIGNED',
  CONFIDENCE_THRESHOLD_MET = 'CONFIDENCE_THRESHOLD_MET',
  OPPOSING_ZONE_BLOCKED = 'OPPOSING_ZONE_BLOCKED',
  ZONE_WIDTH_EXCEEDED = 'ZONE_WIDTH_EXCEEDED',
  ZONE_FRESHNESS_DECAYED = 'ZONE_FRESHNESS_DECAYED',
  ZONE_BROKEN_INVALIDATED = 'ZONE_BROKEN_INVALIDATED',
  REPEATED_TOUCH_EXHAUSTED = 'REPEATED_TOUCH_EXHAUSTED',

  // Session & Market Filters
  SESSION_OUTSIDE_ALLOWED_HOURS = 'SESSION_OUTSIDE_ALLOWED_HOURS',
  WEEKEND_TRADING_BLOCKED = 'WEEKEND_TRADING_BLOCKED',
  MARKET_VOLATILITY_OUTLIER = 'MARKET_VOLATILITY_OUTLIER',
  MARKET_COMPRESSION_LOW_ATR = 'MARKET_COMPRESSION_LOW_ATR',

  // Deduplication & Cooldown
  COOLDOWN_ACTIVE = 'COOLDOWN_ACTIVE',
  DUPLICATE_ZONE_ENTRY_BLOCKED = 'DUPLICATE_ZONE_ENTRY_BLOCKED',
  DUPLICATE_CANDLE_ENTRY_BLOCKED = 'DUPLICATE_CANDLE_ENTRY_BLOCKED',
  EXISTING_POSITION_OPEN = 'EXISTING_POSITION_OPEN',

  // Structure & Liquidity
  BOS_CONFIRMED = 'BOS_CONFIRMED',
  CHOCH_CONFIRMED = 'CHOCH_CONFIRMED',
  LIQUIDITY_SWEEP_CONFIRMED = 'LIQUIDITY_SWEEP_CONFIRMED',
  FVG_CONFLUENCE_CONFIRMED = 'FVG_CONFLUENCE_CONFIRMED',

  // Risk & Challenge
  DAILY_LOSS_LIMIT_REACHED = 'DAILY_LOSS_LIMIT_REACHED',
  MAX_DRAWDOWN_EXCEEDED = 'MAX_DRAWDOWN_EXCEEDED',
  INSUFFICIENT_MARGIN = 'INSUFFICIENT_MARGIN',
  RR_BELOW_MINIMUM = 'RR_BELOW_MINIMUM',
  MAX_POSITIONS_REACHED = 'MAX_POSITIONS_REACHED',

  // Sizing & Execution
  POSITION_SIZE_CALCULATED = 'POSITION_SIZE_CALCULATED',
  LEVERAGE_CAPPED = 'LEVERAGE_CAPPED',

  // AI Confirmation
  AI_CONFIRMATION_APPROVED = 'AI_CONFIRMATION_APPROVED',
  AI_CONFIRMATION_REJECTED = 'AI_CONFIRMATION_REJECTED',
  NEWS_MACRO_BLOCKED = 'NEWS_MACRO_BLOCKED',
}

export interface SessionFilterResultDto {
  allowed: boolean;
  activeSession: 'ASIA' | 'LONDON' | 'NEW_YORK' | 'OFF_HOURS';
  isWeekend: boolean;
  reasonCode?: DecisionReasonCode | undefined;
}

export interface MarketFilterResultDto {
  allowed: boolean;
  marketRegime: 'TRENDING' | 'RANGING' | 'VOLATILITY_OUTLIER' | 'COMPRESSION';
  atr14: number;
  atr200: number;
  atrRatio: number;
  reasonCode?: DecisionReasonCode | undefined;
}

export interface RiskValidationResultDto {
  passed: boolean;
  riskRewardRatio: number;
  dailyLossUsed: number;
  maxDailyLoss: number;
  currentDrawdownPct: number;
  maxDrawdownPct: number;
  openPositionCount: number;
  maxOpenPositions: number;
  availableMargin: number;
  reasonCodes: DecisionReasonCode[];
}

export interface PositionSizingResultDto {
  positionSize: number;
  contractQuantity: number;
  notionalValue?: number | undefined;
  riskAmount: number;
  marginRequired: number;
  leverage: number;
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
}

export interface AIConfirmationResultDto {
  approved: boolean;
  confidenceScore: number;
  ruleAgreementScore: number;
  setupQuality: 'INSTITUTIONAL' | 'HIGH' | 'MEDIUM' | 'POOR';
  reasonCodes: DecisionReasonCode[];
  rationale: string;
}

export interface DecisionDto {
  id: string;
  signalId?: string | undefined;
  symbol: string;
  timeframe: TradingTimeframe;
  decisionState?: DecisionState | undefined;
  state?: DecisionState | undefined;
  outcome?: StrategySignalOutcome | undefined;
  entryPrice?: number | undefined;
  stopLossPrice?: number | undefined;
  takeProfitPrice?: number | undefined;
  positionSize?: number | undefined;
  contractQuantity?: number | undefined;
  notionalValue?: number | undefined;
  leverage?: number | undefined;
  riskPercent?: number | undefined;
  confidenceScore: number; // 0 to 100
  reasonCodes: DecisionReasonCode[];
  inputSnapshotHash: string;
  sessionFilter?: SessionFilterResultDto | undefined;
  marketFilter?: MarketFilterResultDto | undefined;
  riskValidation?: RiskValidationResultDto | undefined;
  positionSizing?: PositionSizingResultDto | undefined;
  aiConfirmation?: AIConfirmationResultDto | undefined;
  timestamp?: string | undefined;
  createdAt?: string | undefined;
}

export interface StrategyPipelineResultDto {
  id?: string | undefined;
  symbol?: string | undefined;
  timeframe?: TradingTimeframe | undefined;
  decisionState?: DecisionState | undefined;
  entryPrice?: number | undefined;
  stopLossPrice?: number | undefined;
  takeProfitPrice?: number | undefined;
  positionSize?: number | undefined;
  leverage?: number | undefined;
  confidenceScore?: number | undefined;
  reasonCodes?: DecisionReasonCode[] | undefined;
  executedAt?: string | undefined;
  createdAt?: string | undefined;
  executionResult?: any | undefined;
  executionError?: string | undefined;
  
  decision?: DecisionDto | undefined;
  signal?: StrategySignalDto | undefined;
  indicatorSnapshot?: IndicatorEngineOutput;
  executionRequested?: boolean;
  executionOrderId?: string | undefined;
  rejectionReason?: string | undefined;
  timestamp?: string;
}

export interface StrategySignalDto {
  id: string;
  symbol: string;
  timeframe: TradingTimeframe;
  outcome: StrategySignalOutcome;
  price: number;
  stopLossPrice?: number | undefined;
  takeProfitPrice?: number | undefined;
  activeZoneId?: string | undefined;
  rationale: string;
  confidenceScore: number;
  timestamp: string;
}
