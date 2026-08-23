import { developerApiClient } from './developerApiClient'
import {
  OrderDto,
  PositionDto,
  OrderFillDto,
  TradeHistoryDto,
  SignalSetupDto,
  TradingSystemStatusDto,
} from '../types/trading'
import { AiEnrichmentDto } from '../types/market'

export const tradingService = {
  async getTradingStatus(accountId?: string): Promise<TradingSystemStatusDto> {
    const { data } = await developerApiClient.get<TradingSystemStatusDto>('/api/v1/trade/status', {
      params: accountId ? { accountId } : {},
    })
    return data
  },

  async getOrders(symbol?: string, status?: string, limit = 50): Promise<OrderDto[]> {
    const { data } = await developerApiClient.get<OrderDto[]>('/api/v1/trade/orders', {
      params: { symbol, status, limit },
    })
    return data
  },

  async getPositions(status = 'OPEN'): Promise<PositionDto[]> {
    const { data } = await developerApiClient.get<PositionDto[]>('/api/v1/trade/positions', {
      params: { status },
    })
    return data
  },

  async getFills(symbol?: string, limit = 50): Promise<OrderFillDto[]> {
    const { data } = await developerApiClient.get<OrderFillDto[]>('/api/v1/trade/fills', {
      params: { symbol, limit },
    })
    return data
  },

  async getTradeHistory(limit = 50): Promise<TradeHistoryDto[]> {
    const { data } = await developerApiClient.get<TradeHistoryDto[]>('/api/v1/trade/history', {
      params: { limit },
    })
    return data
  },

  async getSignals(symbol?: string, state?: string, limit = 50): Promise<SignalSetupDto[]> {
    const { data } = await developerApiClient.get<SignalSetupDto[]>('/api/v1/trade/signals', {
      params: { symbol, state, limit },
    })
    return data
  },

  async getAiIntelligence(setupId: string): Promise<AiEnrichmentDto> {
    const { data } = await developerApiClient.get<AiEnrichmentDto>(`/api/v1/ai/enrichments/${setupId}`)
    return data
  },

  async toggleAlgo(enabled: boolean, accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await developerApiClient.post('/api/v1/trade/algo/toggle', { enabled, accountId })
    return data
  },

  async activateKillSwitch(reason = 'Operator emergency stop', accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await developerApiClient.post('/api/v1/trade/kill-switch', { reason, accountId })
    return data
  },

  async resetKillSwitch(accountId?: string): Promise<{ success: boolean; message: string }> {
    const { data } = await developerApiClient.post('/api/v1/trade/kill-switch/reset', { accountId })
    return data
  },
}
