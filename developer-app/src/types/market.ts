export interface ProductDto {
  symbol: string
  contractType: string
  tickSize: number
  lotSize: number
  priceBandLow: number | null
  priceBandHigh: number | null
  state: string
  tradingStatus: string
}

export interface TickerDto {
  symbol: string
  markPrice: number | null
  lastPrice: number
  bid: number | null
  ask: number | null
  volume24h: number | null
  turnover24h: number | null
  priceChange24h: number | null
  priceChangePercent24h: number | null
  high24h: number | null
  low24h: number | null
  timestamp: string | null
}

export interface MarketStatusDto {
  symbol: string
  status: string
  tradingActive: boolean
  lastPrice: number
  volume24h: number
  lastUpdated: string
}

export interface AiEnrichmentDto {
  id: string
  setupId: string
  symbol: string
  direction: string
  confidence: number
  recommendedAction: string
  orderFlowImbalance: string
  liquidityClusterRisk: string
  macroEventRisk: string
  marketContext: string
  keyLevelAlignment: string
  invalidated: boolean
  invalidationReason: string | null
  enrichedAt: string
}
