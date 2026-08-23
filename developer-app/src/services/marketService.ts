import { developerApiClient } from './developerApiClient'
import { ProductDto, TickerDto, MarketStatusDto } from '../types/market'

export const marketService = {
  async getProducts(): Promise<ProductDto[]> {
    const { data } = await developerApiClient.get<ProductDto[]>('/api/v1/market/products')
    return data
  },

  async getTicker(symbol: string): Promise<TickerDto> {
    const { data } = await developerApiClient.get<TickerDto>(`/api/v1/market/ticker/${symbol}`)
    return data
  },

  async getMarketStatus(symbol = 'BTCUSD'): Promise<MarketStatusDto> {
    const { data } = await developerApiClient.get<MarketStatusDto>('/api/v1/market/status', {
      params: { symbol },
    })
    return data
  },
}
