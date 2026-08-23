export interface CandleDto {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface ChartCandlesResponseDto {
  symbol: string
  exchange: string
  interval: string
  candles: CandleDto[]
}

export interface TickerDto {
  symbol: string
  markPrice: number
  lastPrice: number
  high24h: number
  low24h: number
  volume24h: number
  turnover24h: number
  priceChangePercent24h: number
  timestamp: string
}

export interface ProductDto {
  productId: number
  symbol: string
  description: string
  contractType: string
  baseAsset: string
  quoteAsset: string
  settlementAsset: string
  tickSize: number
  lotSize: number
  minOrderQty: number
  active: boolean
}

export interface MarketStatusDto {
  connected: boolean
  exchange: string
  primarySymbol: string
  latencyMs: number
  timestamp: string
}
