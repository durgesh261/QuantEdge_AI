import { create } from 'zustand'
import { TickerDto, ProductDto } from '../types/market'
import { marketService } from '../services/marketService'
import { tryNormalizeSymbol, SUPPORTED_SYMBOLS } from '../constants/instruments'

interface MarketState {
  activeSymbol: string
  activeInterval: string
  products: ProductDto[]
  tickers: Record<string, TickerDto>
  isLoading: boolean
  symbolError: string | null
  setActiveSymbol: (symbol: string) => void
  setActiveInterval: (interval: string) => void
  fetchProducts: () => Promise<void>
  fetchTicker: (symbol: string) => Promise<void>
  fetchAllTickers: () => Promise<void>
}

export const useMarketStore = create<MarketState>((set, get) => ({
  activeSymbol: 'BTCUSD',
  activeInterval: '1h',
  products: [],
  tickers: {},
  isLoading: false,
  symbolError: null,

  setActiveSymbol: (symbol) => {
    const clean = tryNormalizeSymbol(symbol)
    if (!clean) {
      set({ symbolError: `Unsupported symbol: ${symbol}. Supported: ${SUPPORTED_SYMBOLS.join(', ')}` })
      return
    }
    set({ activeSymbol: clean, symbolError: null })
    get().fetchTicker(clean)
  },

  setActiveInterval: (interval) => set({ activeInterval: interval }),

  fetchProducts: async () => {
    try {
      const products = await marketService.getProducts()
      set({ products })
    } catch (err) {
      console.warn('Failed to fetch market products', err)
    }
  },

  fetchTicker: async (symbol) => {
    const clean = tryNormalizeSymbol(symbol)
    if (!clean) {
      console.warn(`Invalid symbol for ticker: ${symbol}`)
      return
    }
    try {
      const ticker = await marketService.getTicker(clean)
      set((state) => ({
        tickers: { ...state.tickers, [clean]: ticker },
      }))
    } catch (err) {
      console.warn(`Failed to fetch ticker for ${clean}`, err)
    }
  },

  fetchAllTickers: async () => {
    try {
      const bulkTickers = await marketService.getAllTickers()
      set((state) => ({
        tickers: { ...state.tickers, ...bulkTickers },
      }))
    } catch (err) {
      console.warn('Failed to fetch all market tickers via bulk endpoint, falling back to individual', err)
      // Fallback to individual requests
      try {
        const promises = SUPPORTED_SYMBOLS.map((sym) =>
          marketService.getTicker(sym).catch(() => null)
        )
        const results = await Promise.all(promises)
        const newTickers: Record<string, TickerDto> = {}
        SUPPORTED_SYMBOLS.forEach((sym, idx) => {
          if (results[idx]) {
            newTickers[sym] = results[idx] as TickerDto
          }
        })
        set((state) => ({
          tickers: { ...state.tickers, ...newTickers },
        }))
      } catch (fallbackErr) {
        console.warn('Fallback ticker fetch also failed', fallbackErr)
      }
    }
  },
}))
