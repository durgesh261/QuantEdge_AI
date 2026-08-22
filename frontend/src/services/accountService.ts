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
}
