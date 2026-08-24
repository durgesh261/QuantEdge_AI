import { create } from 'zustand'
import { TickerDto, ProductDto } from '../types/market'
import { marketService } from '../services/marketService'
import { normalizeSymbol, SUPPORTED_SYMBOLS } from '../constants/instruments'

interface MarketState {
  activeSymbol: string
  activeInterval: string
  products: ProductDto[]
  tickers: Record<string, TickerDto>
  isLoading: boolean
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

  setActiveSymbol: (symbol) => {
    const clean = normalizeSymbol(symbol)
    set({ activeSymbol: clean })
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
    const clean = normalizeSymbol(symbol)
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
    } catch (err) {
      console.warn('Failed to fetch all market tickers', err)
    }
  },
}))
