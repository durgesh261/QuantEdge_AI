export interface TradingSystemStatus {
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
  activeLockSetupId?: string | null
  streamHealth: string
  lastSyncTimestamp: string
}

export interface AccountSummary {
  accountId: string
  accountName: string
  exchange: string
  balance: number
  availableBalance: number
  currency: string
  leverage: number
  marginUsed: number
  unrealizedPnl: number
  realizedPnl24h: number
  connected: boolean
  algoEnabled: boolean
  killSwitchActive: boolean
}
