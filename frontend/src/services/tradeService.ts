import { api } from './api'

export interface ControlResponse {
  success: boolean
  killSwitchActive: boolean
  algoEnabled: boolean
  message: string
  timestamp: string
}

export interface ExecuteTradeRequest {
  accountId: string
  setupId: string
  clientOrderId?: string
  reduceOnly?: boolean
}

export interface ExecutionResult {
  success: boolean
  state: string
  orderId?: string
  clientOrderId?: string
  setupId?: string
  symbol?: string
  direction?: string
  quantity?: number
  price?: number
  averageFillPrice?: number
  rejectionCode?: string
  errorMessage?: string
  reconciled: boolean
  reconciliationDetail?: string
  completedAt?: string
}

export const tradeService = {
  async executeTrade(request: ExecuteTradeRequest): Promise<ExecutionResult> {
    const response = await api.post<ExecutionResult>('/api/v1/trade/execute', request)
    return response.data
  },

  async activateKillSwitch(accountId?: string, reason?: string): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/v1/trade/kill-switch', { accountId, reason })
    return response.data
  },

  async resetKillSwitch(accountId?: string): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/v1/trade/kill-switch/reset', { accountId })
    return response.data
  },

  async toggleAlgo(enabled: boolean, accountId?: string): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/v1/trade/algo/toggle', { accountId, enabled })
    return response.data
  },

  async getActiveOrders(accountId?: string): Promise<any[]> {
    const response = await api.get<any[]>('/api/v1/trade/active', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },

  async getOrderHistory(accountId?: string): Promise<any[]> {
    const response = await api.get<any[]>('/api/v1/trade/history', {
      params: accountId ? { accountId } : undefined,
    })
    return response.data
  },
}
