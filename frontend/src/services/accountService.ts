import { api } from './api'

export interface BalanceDetail {
  assetSymbol: string
  balance: number
  availableBalance: number
  positionMargin: number
  orderMargin: number
}

export interface PositionDetail {
  productId?: number
  symbol: string
  side: 'LONG' | 'SHORT'
  size: number
  entryPrice: number
  markPrice: number
  unrealizedPnl: number
  realizedPnl: number
  leverage: number
  margin: number
  liquidationPrice?: number
}

export interface OrderDetail {
  id: number
  clientOrderId?: string
  symbol: string
  side: string
  orderType: string
  size: number
  unfilledSize: number
  limitPrice?: number
  status: string
  createdAt: string
}

export interface ConnectAccountRequest {
  accountId?: string
  name?: string
  apiKey: string
  apiSecret: string
}

export interface ConnectAccountResponse {
  success: boolean
  accountId: string
  name: string
  maskedApiKey: string
  connectionStatus: string
  wsStatus?: string
  streamHealth?: string
  totalEquity: number
  availableBalance: number
  marginUsed: number
  positionsCount: number
  ordersCount: number
  algoEnabled: boolean
  killSwitchActive: boolean
  lastConnectedAt?: string
  error?: string
}

export interface AccountStatusResponse {
  accountId: string
  name: string
  connected: boolean
  connectionStatus: string
  wsStatus?: string
  streamHealth?: string
  maskedApiKey: string
  environment: string
  lastConnectedAt?: string
  lastSyncedAt?: string
  lastEventAt?: string
  reconnectCount?: number
  algoEnabled: boolean
  killSwitchActive: boolean
  lastError?: string
}

export interface AccountSummaryResponse {
  success: boolean
  accountId: string
  name: string
  connectionStatus: string
  wsStatus?: string
  streamHealth?: string
  maskedApiKey: string
  totalEquity: number
  availableBalance: number
  marginUsed: number
  baseCurrency: string
  algoEnabled: boolean
  killSwitchActive: boolean
  lastSyncedAt?: string
  lastEventAt?: string
  balances: BalanceDetail[]
  positions: PositionDetail[]
  openOrders: OrderDetail[]
  error?: string
}

export interface AlgoConfigResponse {
  success: boolean
  accountId: string
  version: number
  takeProfitPercent: number
  stopLossPercent: number
  riskPerTradePercent: number
  maxDailyLossPercent: number
  maxLeverage: number
  algoEnabled: boolean
  killSwitchActive: boolean
  message?: string
  updatedAt?: string
}

export interface UpdateAlgoConfigRequest {
  accountId?: string
  takeProfitPercent?: number
  stopLossPercent?: number
  riskPerTradePercent?: number
  maxDailyLossPercent?: number
  maxLeverage?: number
  algoEnabled?: boolean
  killSwitchActive?: boolean
}

export interface AlgoConfigHistoryItem {
  id: string
  action: string
  description: string
  timestamp: string
}

export interface AlgoConfigHistoryResponse {
  success: boolean
  accountId: string
  history: AlgoConfigHistoryItem[]
  message?: string
}

export interface TradeConfigSnapshotResponse {
  success: boolean
  setupId: string
  accountId: string
  strategyName: string
  strategyVersion: string
  configurationVersion: number
  entryPrice: number
  stopLoss: number
  takeProfit: number
  riskReward: number
  createdAt: string
  message?: string
}

export const accountService = {
  async connectAccount(data: ConnectAccountRequest): Promise<ConnectAccountResponse> {
    const response = await api.post<ConnectAccountResponse>('/api/v1/account/connect', data)
    return response.data
  },

  async verifyAccount(accountId?: string): Promise<AccountSummaryResponse> {
    const response = await api.post<AccountSummaryResponse>('/api/v1/account/verify', { accountId })
    return response.data
  },

  async getAccountStatus(accountId?: string): Promise<AccountStatusResponse> {
    const response = await api.get<AccountStatusResponse>('/api/v1/account/status', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },

  async getAccountSummary(accountId?: string): Promise<AccountSummaryResponse> {
    const response = await api.get<AccountSummaryResponse>('/api/v1/account/summary', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },

  async disconnectAccount(accountId?: string): Promise<AccountStatusResponse> {
    const response = await api.post<AccountStatusResponse>('/api/v1/account/disconnect', { accountId })
    return response.data
  },

  async getAlgoConfig(accountId?: string): Promise<AlgoConfigResponse> {
    const response = await api.get<AlgoConfigResponse>('/api/v1/account/algo-config', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },

  async updateAlgoConfig(data: UpdateAlgoConfigRequest): Promise<AlgoConfigResponse> {
    const response = await api.put<AlgoConfigResponse>('/api/v1/account/algo-config', data)
    return response.data
  },

  async getAlgoConfigHistory(accountId?: string): Promise<AlgoConfigHistoryResponse> {
    const response = await api.get<AlgoConfigHistoryResponse>('/api/v1/account/algo-config/history', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },

  async getTradeConfigSnapshot(setupId: string, accountId?: string): Promise<TradeConfigSnapshotResponse> {
    const response = await api.get<TradeConfigSnapshotResponse>(`/api/v1/account/algo-config/snapshot/${setupId}`, {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },
}
