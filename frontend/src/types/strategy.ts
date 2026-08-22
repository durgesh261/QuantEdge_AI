/**
 * Dynamic Strategy Contract & DTO Types for QuantEdge AI Frontend.
 *
 * Fundamental Architectural Principle:
 * Trading logic (SMC, Order Blocks, Liquidity, FVG Confirmation, Position Sizing, TP/SL calculation)
 * lives strictly in the Python Engine & Java Backend.
 * The frontend acts purely as a presentation, visualization, and parameter-configuration layer.
 */

export type StrategyDirection = 'LONG' | 'SHORT' | 'NONE'

export type SetupState =
  | 'NO_SETUP'
  | 'WATCHING_OB'
  | 'OB_ENGAGED'
  | 'QUALIFIED_LONG'
  | 'QUALIFIED_SHORT'
  | 'TRADE_SETUP_READY'
  | 'EXPIRED'
  | 'REJECTED'

export interface StrategyResult {
  signal: StrategyDirection
  symbol: string
  timeframe: string
  direction: StrategyDirection
  entry?: number
  stopLoss?: number
  takeProfit?: number
  riskReward?: number
  confidence?: number
  orderBlockUpperEdge?: number
  orderBlockLowerEdge?: number
  stopDistancePct?: number
  maxLossPct?: number
  calculatedLeverage?: number
  takeProfitTargetPct?: number
  takeProfitPrice?: number
  configurationVersion?: number
  riskValidationStatus?: string
  strategyName: string
  strategyVersion: string
  setupId?: string
  status: SetupState
  rejectionReason?: string
  timestamp: string
  metadata?: Record<string, any>
}

export interface AlgoConfig {
  accountId: string
  version: number
  takeProfitPercent: number
  stopLossPercent: number
  riskPerTradePercent: number
  takeProfitTargetPercent?: number
  maxLossPercent?: number
  maxDailyLossPercent: number
  maxLeverage: number
  algoEnabled: boolean
  killSwitchActive: boolean
  updatedAt?: string
}

export interface TradeRecordSnapshot {
  setupId: string
  strategyName: string
  strategyVersion: string
  configVersion: number
  takeProfitPercent: number
  stopLossPercent: number
  riskPercent: number
  takeProfitTargetPercent?: number
  maxLossPercent?: number
  calculatedLeverage?: number
  orderBlockUpperEdge?: number
  orderBlockLowerEdge?: number
  entryPrice: number
  stopLossPrice: number
  takeProfitPrice: number
  riskReward: number
  createdAt: string
}

export interface SingleTradeLockStatus {
  accountId: string
  isLocked: boolean
  activeSetupId?: string
  activeSymbol?: string
  acquiredAt?: string
}

export interface CompoundingTradeRecord {
  setupId: string
  symbol: string
  direction: StrategyDirection
  entryPrice: number
  closePrice?: number
  grossPnL: number
  tradingFees: number
  fundingCosts: number
  netPnL: number
  preTradeBalance: number
  postTradeBalance: number
  closedAt?: string
}
