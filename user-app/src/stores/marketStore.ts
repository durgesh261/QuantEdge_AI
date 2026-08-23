import { create } from 'zustand'
import { Ticker, Product } from '../types/market'
import { marketService } from '../services/marketService'

interface MarketState {
  activeSymbol: string
  activeInterval: string
  products: Product[]
  tickers: Record<string, Ticker>
  isLoading: boolean
  setActiveSymbol: (symbol: string) => void
  setActiveInterval: (interval: string) => void
  fetchProducts: () => Promise<void>
  fetchTicker: (symbol: string) => Promise<void>
}

export const useMarketStore = create<MarketState>((set, get) => ({
  activeSymbol: 'BTCUSD',
  activeInterval: '1h',
  products: [],
  tickers: {},
  isLoading: false,

  setActiveSymbol: (symbol) => {
    set({ activeSymbol: symbol })
    get().fetchTicker(symbol)
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
    try {
      const ticker = await marketService.getTicker(symbol)
      set((state) => ({
        tickers: { ...state.tickers, [symbol]: ticker },
      }))
    } catch (err) {
      console.warn(`Failed to fetch ticker for ${symbol}`, err)
    }
  },
}))
