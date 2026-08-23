export interface Product {
  symbol: string
  displayName: string
  quoteCurrency: string
  contractSize: string
  tickSize: string
  state: string
}

export interface Ticker {
  symbol: string
  price: number
  bid: number
  ask: number
  volume24h: number
  change24h: number
  markPrice: number
  timestamp: string
}

export interface Candle {
  time: number // Unix timestamp (seconds)
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface ChartCandlesResponse {
  symbol: string
  interval: string
  candles: Candle[]
  source: string
}

export interface MarketStatus {
  symbol: string
  connected: boolean
  lastPrice: number
  timestamp: string
  streamHealth: string
}
