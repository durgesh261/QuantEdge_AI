import { create } from 'zustand';

export interface ScannerPair {
  symbol: string;
  isActive: boolean;
  isPaused: boolean;
  status: string;
  livePrice: number;
  priceChange24h: number;
  activeOBs: number;
  obWidthPct: number | null;
  aiScore: number | null;
  lastTickAt?: string;
  ticksProcessed: number;
  signalsTriggered: number;
  tradesExecuted: number;
}

export interface ScannerGlobal {
  isRunning: boolean;
  isPaused: boolean;
  ticksTotal: number;
  signalsTotal: number;
  tradesTotal: number;
}

interface ScannerState {
  global: ScannerGlobal | null;
  pairs: ScannerPair[];
  signals: any[];
  isLoading: boolean;

  setState: (global: ScannerGlobal, pairs: ScannerPair[], signals: any[]) => void;
  updatePair: (symbol: string, data: Partial<ScannerPair>) => void;
  updateGlobal: (data: Partial<ScannerGlobal>) => void;
  setLoading: (loading: boolean) => void;
}

export const useScannerStore = create<ScannerState>((set, _get) => ({
  global: null,
  pairs: [],
  signals: [],
  isLoading: true,

  setState: (global, pairs, signals) =>
    set({ global, pairs, signals, isLoading: false }),

  updatePair: (symbol, data) =>
    set((state) => ({
      pairs: state.pairs.map((p) => (p.symbol === symbol ? { ...p, ...data } : p)),
    })),

  updateGlobal: (data) =>
    set((state) => {
      if (!state.global) return state;
      return {
        global: { ...state.global, ...data },
      };
    }),

  setLoading: (isLoading) => set({ isLoading }),
}));
