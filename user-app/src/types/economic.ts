export interface EconomicEventDto {
  id: string
  eventName: string
  country: 'US' | 'IN' | 'EU' | 'GB' | 'JP' | 'CN' | 'CA' | 'AU' | string
  currency: string
  category: 'INFLATION' | 'CENTRAL_BANK' | 'EMPLOYMENT' | 'GROWTH' | 'TRADE' | string
  importance: 'HIGH' | 'MEDIUM' | 'LOW' | string
  scheduledAt: string
  previousValue: string | null
  forecastValue: string | null
  actualValue: string | null
  status: 'UPCOMING' | 'IN_PROGRESS' | 'COMPLETED' | string
  source: string
  sourceUrl: string
  expiresAt: string
}
