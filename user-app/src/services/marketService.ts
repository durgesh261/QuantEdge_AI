import { apiClient } from './apiClient'
import { Product, Ticker, ChartCandlesResponse, MarketStatus } from '../types/market'

export const marketService = {
  async getProducts(): Promise<Product[]> {
    const { data } = await apiClient.get<Product[]>('/api/v1/market/products')
    return data
  },

  async getTicker(symbol: string): Promise<Ticker> {
    const { data } = await apiClient.get<Ticker>(`/api/v1/market/ticker/${symbol}`)
    return data
  },

  async getCandles(symbol: string, interval = '1h', limit = 500): Promise<ChartCandlesResponse> {
    const { data } = await apiClient.get<ChartCandlesResponse>('/api/v1/market/candles', {
      params: { symbol, interval, limit },
    })
    return data
  },

  async getMarketStatus(symbol = 'BTCUSD'): Promise<MarketStatus> {
    const { data } = await apiClient.get<MarketStatus>('/api/v1/market/status', {
      params: { symbol },
    })
    return data
  },
}
