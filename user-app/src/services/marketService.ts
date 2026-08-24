import { apiClient } from './apiClient'
import { ProductDto, TickerDto, ChartCandlesResponseDto, MarketStatusDto } from '../types/market'

export const marketService = {
  async getProducts(): Promise<ProductDto[]> {
    const { data } = await apiClient.get<ProductDto[]>('/api/v1/market/products')
    return data
  },

  async getTicker(symbol: string): Promise<TickerDto> {
    const { data } = await apiClient.get<TickerDto>(`/api/v1/market/ticker/${symbol}`)
    return data
  },

  async getAllTickers(): Promise<Record<string, TickerDto>> {
    const { data } = await apiClient.get<Record<string, TickerDto>>('/api/v1/market/tickers')
    return data
  },

  async getCandles(symbol: string, interval = '1h', limit = 500): Promise<ChartCandlesResponseDto> {
    const { data } = await apiClient.get<ChartCandlesResponseDto>('/api/v1/market/candles', {
      params: { symbol, interval, limit },
    })
    return data
  },

  async getMarketStatus(symbol = 'BTCUSD'): Promise<MarketStatusDto> {
    const { data } = await apiClient.get<MarketStatusDto>('/api/v1/market/status', {
      params: { symbol },
    })
    return data
  },
}
