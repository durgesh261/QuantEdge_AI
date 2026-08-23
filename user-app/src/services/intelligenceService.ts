import { apiClient } from './apiClient'
import { NewsArticleDto } from '../types/news'
import { EconomicEventDto } from '../types/economic'

export const intelligenceService = {
  async getNews(category?: string, importance?: string, symbol?: string, limit = 50): Promise<NewsArticleDto[]> {
    const { data } = await apiClient.get<NewsArticleDto[]>('/api/v1/news', {
      params: { category, importance, symbol, limit },
    })
    return data
  },

  async getNewsById(id: string): Promise<NewsArticleDto> {
    const { data } = await apiClient.get<NewsArticleDto>(`/api/v1/news/${id}`)
    return data
  },

  async getUpcomingEconomicEvents(limit = 50): Promise<EconomicEventDto[]> {
    const { data } = await apiClient.get<EconomicEventDto[]>('/api/v1/economic-events/upcoming', {
      params: { limit },
    })
    return data
  },

  async getEconomicEvents(
    country?: string,
    currency?: string,
    importance?: string,
    from?: string,
    to?: string,
    limit = 100
  ): Promise<EconomicEventDto[]> {
    const { data } = await apiClient.get<EconomicEventDto[]>('/api/v1/economic-events', {
      params: { country, currency, importance, from, to, limit },
    })
    return data
  },

  async getEconomicEventById(id: string): Promise<EconomicEventDto> {
    const { data } = await apiClient.get<EconomicEventDto>(`/api/v1/economic-events/${id}`)
    return data
  },
}
