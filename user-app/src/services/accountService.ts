import { apiClient } from './apiClient'
import { AlgoConfigResponse, UpdateAlgoConfigRequest } from '../types/risk'
import { AccountSummaryDto } from '../types/trading'

export const accountService = {
  async getAlgoConfig(accountId?: string): Promise<AlgoConfigResponse> {
    const { data } = await apiClient.get<AlgoConfigResponse>('/api/v1/account/algo-config', {
      params: accountId ? { accountId } : {},
    })
    return data
  },

  async updateAlgoConfig(request: UpdateAlgoConfigRequest): Promise<AlgoConfigResponse> {
    const { data } = await apiClient.put<AlgoConfigResponse>('/api/v1/account/algo-config', request)
    return data
  },

  async getAccountSummary(accountId?: string): Promise<AccountSummaryDto> {
    const { data } = await apiClient.get<AccountSummaryDto>('/api/v1/account/summary', {
      params: accountId ? { accountId } : {},
    })
    return data
  },
}
