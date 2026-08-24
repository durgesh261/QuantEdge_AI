/**
 * Authoritative Centralized Instrument Registry for QuantEdge AI User App.
 *
 * Supported Canonical Trading Pairs:
 * 1. BTC/USD (BTCUSD)
 * 2. ETH/USD (ETHUSD)
 * 3. SOL/USD (SOLUSD)
 * 4. XRP/USD (XRPUSD)
 */

export interface InstrumentMeta {
  symbol: string
  displaySymbol: string
  name: string
  baseAsset: string
  quoteAsset: string
  pricePrecision: number
  qtyPrecision: number
  tickSize: number
  lotSize: number
  maxLeverage: number
  description: string
  supportedTimeframes: string[]
}

export const CANONICAL_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '1d'] as const
export type CanonicalTimeframe = (typeof CANONICAL_TIMEFRAMES)[number]

export const CANONICAL_INSTRUMENTS: Record<string, InstrumentMeta> = {
  BTCUSD: {
    symbol: 'BTCUSD',
    displaySymbol: 'BTC/USD',
    name: 'Bitcoin Perpetual',
    baseAsset: 'BTC',
    quoteAsset: 'USDT',
    pricePrecision: 2,
    qtyPrecision: 4,
    tickSize: 0.5,
    lotSize: 1,
    maxLeverage: 100,
    description: 'Bitcoin Perpetual Futures (Delta Exchange India)',
    supportedTimeframes: [...CANONICAL_TIMEFRAMES],
  },
  ETHUSD: {
    symbol: 'ETHUSD',
    displaySymbol: 'ETH/USD',
    name: 'Ethereum Perpetual',
    baseAsset: 'ETH',
    quoteAsset: 'USDT',
    pricePrecision: 2,
    qtyPrecision: 4,
    tickSize: 0.05,
    lotSize: 1,
    maxLeverage: 100,
    description: 'Ethereum Perpetual Futures (Delta Exchange India)',
    supportedTimeframes: [...CANONICAL_TIMEFRAMES],
  },
  SOLUSD: {
    symbol: 'SOLUSD',
    displaySymbol: 'SOL/USD',
    name: 'Solana Perpetual',
    baseAsset: 'SOL',
    quoteAsset: 'USDT',
    pricePrecision: 2,
    qtyPrecision: 4,
    tickSize: 0.01,
    lotSize: 1,
    maxLeverage: 50,
    description: 'Solana Perpetual Futures (Delta Exchange India)',
    supportedTimeframes: [...CANONICAL_TIMEFRAMES],
  },
  XRPUSD: {
    symbol: 'XRPUSD',
    displaySymbol: 'XRP/USD',
    name: 'XRP Perpetual',
    baseAsset: 'XRP',
    quoteAsset: 'USDT',
    pricePrecision: 4,
    qtyPrecision: 2,
    tickSize: 0.0001,
    lotSize: 1,
    maxLeverage: 50,
    description: 'XRP Perpetual Futures (Delta Exchange India)',
    supportedTimeframes: [...CANONICAL_TIMEFRAMES],
  },
}

export const SUPPORTED_SYMBOLS: string[] = Object.keys(CANONICAL_INSTRUMENTS)

/**
 * Normalizes any symbol input (e.g. "BTCUSD.P", "btc/usd", "BTC/USD") to canonical "BTCUSD".
 * Throws if the symbol is invalid or unsupported.
 */
export function normalizeSymbol(raw?: string): string {
  if (!raw || !raw.trim()) {
    throw new Error('Symbol is required')
  }
  const clean = raw.trim().toUpperCase().replace('.P', '').replace('/', '')
  if (!CANONICAL_INSTRUMENTS[clean]) {
    throw new Error(`Unsupported symbol: ${raw}. Supported: ${Object.keys(CANONICAL_INSTRUMENTS).join(', ')}`)
  }
  return clean
}

/**
 * Gets instrument metadata. Throws if symbol is invalid.
 */
export function getInstrumentMeta(symbol?: string): InstrumentMeta {
  const norm = normalizeSymbol(symbol)
  return CANONICAL_INSTRUMENTS[norm]
}

/**
 * Safely normalizes symbol, returning null if invalid (for cases where you need to handle gracefully).
 */
export function tryNormalizeSymbol(raw?: string): string | null {
  try {
    return normalizeSymbol(raw)
  } catch {
    return null
  }
}

/**
 * Safely gets instrument metadata, returning null if invalid.
 */
export function tryGetInstrumentMeta(symbol?: string): InstrumentMeta | null {
  const norm = tryNormalizeSymbol(symbol)
  return norm ? CANONICAL_INSTRUMENTS[norm] : null
}

/**
 * Formats price according to instrument precision.
 */
export function formatPrice(price: number | string | null | undefined, symbol?: string): string {
  if (price === null || price === undefined || price === '') return '—'
  const num = typeof price === 'string' ? parseFloat(price) : price
  if (isNaN(num)) return '—'
  const meta = getInstrumentMeta(symbol)
  return num.toLocaleString('en-US', {
    minimumFractionDigits: meta.pricePrecision,
    maximumFractionDigits: meta.pricePrecision,
  })
}

/**
 * Formats quantity according to instrument precision.
 */
export function formatQuantity(qty: number | string | null | undefined, symbol?: string): string {
  if (qty === null || qty === undefined || qty === '') return '—'
  const num = typeof qty === 'string' ? parseFloat(qty) : qty
  if (isNaN(num)) return '—'
  const meta = getInstrumentMeta(symbol)
  return num.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: meta.qtyPrecision,
  })
}
