import { api } from './api'

export interface ControlResponse {
  success: boolean
  killSwitchActive: boolean
  algoEnabled: boolean
  message: string
  timestamp: string
}

export const tradeService = {
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
