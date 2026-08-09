import { create } from 'zustand';

export interface Ticker {
  price: number;
  change_24h: number;
  volume_24h: number;
  high_24h: number;
  low_24h: number;
}

interface DeltaState {
  // User toggle (master switch)
  isDeltaEnabled: boolean;
  
  // Actual operational state
  isConnected: boolean;
  isConnecting: boolean;
  connectionMode: 'websocket' | 'polling' | 'none';
  connectionError: string | null;
  
  // Data
  positions: any[];
  balances: any[];
  ticker: Ticker | null;
  
  // Actions
  setDeltaEnabled: (enabled: boolean) => void;
  setConnected: (connected: boolean) => void;
  setConnecting: (connecting: boolean) => void;
  setConnectionMode: (mode: 'websocket' | 'polling' | 'none') => void;
  setConnectionError: (error: string | null) => void;
  setTicker: (ticker: Ticker | null) => void;
  setPositions: (positions: any[]) => void;
  setBalances: (balances: any[]) => void;
  reset: () => void;
}

const initialState = {
  isDeltaEnabled: false,
  isConnected: false,
  isConnecting: false,
  connectionMode: 'none' as const,
  connectionError: null,
  positions: [],
  balances: [],
  ticker: null,
};

export const useDeltaStore = create<DeltaState>((set) => ({
  ...initialState,

  setDeltaEnabled: (isDeltaEnabled) => set({ isDeltaEnabled }),
  setConnected: (isConnected) => set({ isConnected }),
  setConnecting: (isConnecting) => set({ isConnecting }),
  setConnectionMode: (connectionMode) => set({ connectionMode }),
  setConnectionError: (connectionError) => set({ connectionError }),
  setTicker: (ticker) => set({ ticker }),
  setPositions: (positions) => set({ positions: positions || [] }),
  setBalances: (balances) => set({ balances: balances || [] }),
  
  reset: () => set(initialState),
}));
