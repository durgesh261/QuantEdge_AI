import { apiClient } from './apiClient'
import {
  TradingSystemStatusDto,
  AccountSummaryDto,
  OrderDto,
  PositionDto,
  OrderFillDto,
  TradeHistoryDto,
  SignalSetupDto,
} from '../types/trading'
import { AiEnrichmentDto } from '../types/ai'

export const tradingService = {
  async getTradingStatus(accountId?: string): Promise<TradingSystemStatusDto> {
    const { data } = await apiClient.get<TradingSystemStatusDto>('/api/v1/trade/status', {
      params: accountId ? { accountId } : {},
    })
    return data
  },

  async getAccountSummary(): Promise<AccountSummaryDto> {
    const { data } = await apiClient.get<AccountSummaryDto>('/api/v1/account/summary')
    return data
  },

  async getOrders(symbol?: string, status?: string, limit = 100): Promise<OrderDto[]> {
    const { data } = await apiClient.get<OrderDto[]>('/api/v1/trade/orders', {
      params: { symbol, status, limit },
    })
    return data
  },

  async getActiveOrders(): Promise<OrderDto[]> {
    const { data } = await apiClient.get<OrderDto[]>('/api/v1/trade/active')
    return data
  },

  async getPositions(status = 'OPEN'): Promise<PositionDto[]> {
    const { data } = await apiClient.get<PositionDto[]>('/api/v1/trade/positions', {
      params: { status },
    })
    return data
  },

  async getFills(symbol?: string, limit = 100): Promise<OrderFillDto[]> {
    const { data } = await apiClient.get<OrderFillDto[]>('/api/v1/trade/fills', {
      params: { symbol, limit },
    })
    return data
  },

  async getTradeHistory(limit = 100): Promise<TradeHistoryDto[]> {
    const { data } = await apiClient.get<TradeHistoryDto[]>('/api/v1/trade/history', {
      params: { limit },
    })
    return data
  },

  async getSignals(symbol?: string, state?: string, limit = 100): Promise<SignalSetupDto[]> {
    const { data } = await apiClient.get<SignalSetupDto[]>('/api/v1/trade/signals', {
      params: { symbol, state, limit },
    })
    return data
  },

  async getSignalById(setupId: string): Promise<SignalSetupDto> {
    const { data } = await apiClient.get<SignalSetupDto>(`/api/v1/trade/signals/${setupId}`)
    return data
  },

  async getAiIntelligence(setupId: string): Promise<AiEnrichmentDto> {
    const { data } = await apiClient.get<AiEnrichmentDto>(`/api/v1/ai/enrichments/${setupId}`)
    return data
  },

  async getBulkAiIntelligence(setupIds: string[], accountId?: string): Promise<Record<string, AiEnrichmentDto>> {
    if (!setupIds.length) return {}
    const { data } = await apiClient.post<Record<string, AiEnrichmentDto>>('/api/v1/ai/enrichments/bulk', {
      setupIds,
      accountId,
    })
    return data
  },

  async getRecentAiEnrichments(symbol?: string, limit = 20): Promise<AiEnrichmentDto[]> {
    const { data } = await apiClient.get<AiEnrichmentDto[]>('/api/v1/ai/enrichments', {
      params: { symbol, limit },
    })
    return data
  },

  async toggleAlgo(enabled: boolean, accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await apiClient.post('/api/v1/trade/algo/toggle', { enabled, accountId })
    return data
  },

  async activateKillSwitch(reason = 'Operator emergency stop', accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await apiClient.post('/api/v1/trade/kill-switch', { reason, accountId })
    return data
  },

  async triggerKillSwitch(reason = 'Operator emergency stop', accountId?: string): Promise<{ success: boolean; message: string }> {
    return this.activateKillSwitch(reason, accountId)
  },

  async resetKillSwitch(accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await apiClient.post('/api/v1/trade/kill-switch/reset', { accountId })
    return data
  },
}
