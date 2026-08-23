export interface NewsArticleDto {
  id: string
  title: string
  summary: string
  source: string
  sourceUrl: string
  category: 'CRYPTO' | 'FINANCE' | 'MARKETS' | 'CENTRAL_BANKS' | 'REGULATION' | 'ECONOMY' | 'MACRO' | string
  importance: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | string
  relevantSymbols: string | null
  sentiment: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | string
  publishedAt: string
  expiresAt: string
}
