export interface OrderDto {
  id: string
  accountId: string | null
  clientOrderId: string
  deltaOrderId: string | null
  setupId: string | null
  symbol: string
  side: 'BUY' | 'SELL'
  orderType: 'LIMIT' | 'MARKET' | 'STOP_LIMIT' | string
  status: 'PENDING' | 'OPEN' | 'FILLED' | 'PARTIALLY_FILLED' | 'CANCELLED' | 'REJECTED' | 'FAILED' | string
  price: number | null
  stopPrice: number | null
  quantity: number
  filledQuantity: number
  averageFillPrice: number | null
  leverage: number
  reduceOnly: boolean | null
  postOnly: boolean | null
  timeInForce: string | null
  placedAt: string
  submittedAt: string | null
  filledAt: string | null
  cancelledAt: string | null
  reconciliationState: string | null
  errorMessage: string | null
}

export interface PositionDto {
  id: string
  accountId: string | null
  deltaPositionId: string | null
  setupId: string | null
  entryOrderId: string | null
  closeOrderId: string | null
  symbol: string
  side: 'LONG' | 'SHORT' | string
  status: 'OPEN' | 'CLOSED' | 'LIQUIDATED' | string
  entryPrice: number
  currentPrice: number | null
  quantity: number
  leverage: number
  unrealizedPnl: number
  realizedPnl: number
  liquidationPrice: number | null
  marginUsed: number | null
  stopLossPrice: number | null
  takeProfitPrice: number | null
  reconciliationState: string | null
  lastReconciledAt: string | null
  openedAt: string
  closedAt: string | null
}

export interface OrderFillDto {
  id: string
  accountId: string | null
  orderId: string | null
  exchangeFillId: string
  clientOrderId: string
  deltaOrderId: string | null
  symbol: string
  side: string
  fillQuantity: number
  fillPrice: number
  fee: number
  feeAsset: string
  filledAt: string
}

export interface TradeHistoryDto {
  id: string
  accountId: string | null
  setupId: string | null
  symbol: string
  direction: string
  entryPrice: number
  exitPrice: number
  quantity: number
  leverage: number
  grossPnl: number
  tradingFees: number
  fundingCosts: number
  otherCosts: number
  netPnl: number
  preTradeBalance: number | null
  postTradeBalance: number | null
  closeReason: string
  closedAt: string
}

export interface SignalSetupDto {
  id: string
  setupId: string
  symbol: string
  timeframe: string
  direction: 'LONG' | 'SHORT' | string
  setupState: 'SCANNING' | 'QUALIFIED' | 'ACTIVE' | 'INVALIDATED' | 'COMPLETED' | string
  entryPrice: number
  stopLoss: number
  takeProfit: number
  confidence: number
  riskReward: number
  orderBlockHigh?: number
  orderBlockLow?: number
  orderBlockType?: string
  createdAt: string
  updatedAt: string
}

export interface TradingSystemStatusDto {
  accountId: string
  accountName: string
  exchange: string
  connected: boolean
  algoEnabled: boolean
  killSwitchActive: boolean
  balance: number
  currency: string
  leverage: number
  openOrdersCount: number
  openPositionsCount: number
  hasActiveTradeLock: boolean
  activeLockSetupId: string | null
  streamHealth: string
  lastSyncTimestamp: string
}
