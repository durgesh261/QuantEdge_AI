import { create } from 'zustand';
import { TerminalPage, SystemStatus, TradingTimeframe } from '@algoapp/shared';

export interface WidgetVisibilityState {
  showCurrentPair: boolean;
  showAccountSummary: boolean;
  showChallengeSummary: boolean;
  showOpportunityRadar: boolean;
  showEquityCurve: boolean;
  showPnLChart: boolean;
  showWinRate: boolean;
  showOpenTrades: boolean;
  showSignals: boolean;
}

const STORAGE_KEY = 'quantedge_terminal_layout';

const defaultWidgetState: WidgetVisibilityState = {
  showCurrentPair: true,
  showAccountSummary: true,
  showChallengeSummary: true,
  showOpportunityRadar: true,
  showEquityCurve: true,
  showPnLChart: true,
  showWinRate: true,
  showOpenTrades: true,
  showSignals: true,
};

const loadInitialWidgetState = (): WidgetVisibilityState => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return { ...defaultWidgetState, ...JSON.parse(saved) };
    }
  } catch {
    // fallback
  }
  return defaultWidgetState;
};

interface TerminalState {
  activePage: TerminalPage;
  activeSymbol: string;
  activeTimeframe: TradingTimeframe;
  activeProfileId: string;
  isSidebarCollapsed: boolean;
  isMarketWatchOpen: boolean;
  marketWatchWidth: number;
  liveTradingLeftColWidth: number;
  liveTradingRightColWidth: number;
  isCommandPaletteOpen: boolean;
  isDeveloperMode: boolean;
  systemStatus: SystemStatus;
  widgets: WidgetVisibilityState;
  isAlgoRunning: boolean;
  executionMode: 'PAPER' | 'LIVE' | 'SHADOW';
  toggleAlgo: () => void;
  setAlgoRunning: (running: boolean) => void;
  setExecutionMode: (mode: 'PAPER' | 'LIVE' | 'SHADOW') => void;

  setActivePage: (page: TerminalPage) => void;
  setActiveSymbol: (symbol: string) => void;
  setActiveTimeframe: (timeframe: TradingTimeframe) => void;
  setActiveProfileId: (id: string) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleMarketWatch: () => void;
  setMarketWatchOpen: (open: boolean) => void;
  setMarketWatchWidth: (width: number) => void;
  setLiveTradingLeftColWidth: (width: number) => void;
  setLiveTradingRightColWidth: (width: number) => void;
  toggleCommandPalette: () => void;
  setCommandPaletteOpen: (open: boolean) => void;
  toggleDeveloperMode: () => void;
  setDeveloperMode: (enabled: boolean) => void;
  setSystemStatus: (status: SystemStatus) => void;
  setIsAlgoRunning: (running: boolean) => void;

  toggleWidget: (widgetKey: keyof WidgetVisibilityState) => void;
  resetWidgetLayout: () => void;
}

const savedWidths = (() => {
  try {
    const mw = localStorage.getItem('quantedge_mw_width');
    const ltc = localStorage.getItem('quantedge_ltc_width');
    const rtc = localStorage.getItem('quantedge_rtc_width');
    return {
      mw: mw ? Number(mw) : 260,
      ltc: ltc ? Number(ltc) : 220,
      rtc: rtc ? Number(rtc) : 320,
    };
  } catch {
    return { mw: 260, ltc: 220, rtc: 320 };
  }
})();

export const useTerminalStore = create<TerminalState>((set, get) => ({
  activePage: TerminalPage.DASHBOARD,
  activeSymbol: 'BTCUSD.P',
  activeTimeframe: '1H',
  activeProfileId: 'DEF-1H-PROF',
  isSidebarCollapsed: false,
  isMarketWatchOpen: false,
  marketWatchWidth: savedWidths.mw,
  liveTradingLeftColWidth: savedWidths.ltc,
  liveTradingRightColWidth: savedWidths.rtc,
  isCommandPaletteOpen: false,
  isDeveloperMode: false,
  systemStatus: SystemStatus.HEALTHY,
  widgets: loadInitialWidgetState(),
  isAlgoRunning: false,
  executionMode: 'PAPER',
  toggleAlgo: () => set((state) => ({ isAlgoRunning: !state.isAlgoRunning })),
  setAlgoRunning: (running) => set({ isAlgoRunning: running }),
  setExecutionMode: (mode) => set({ executionMode: mode }),

  setActivePage: (page) => set({ activePage: page }),
  setActiveSymbol: (symbol) => set({ activeSymbol: symbol }),
  setActiveTimeframe: (timeframe) => set({ activeTimeframe: timeframe }),
  setActiveProfileId: (id) => set({ activeProfileId: id }),
  toggleSidebar: () => set((state) => ({ isSidebarCollapsed: !state.isSidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ isSidebarCollapsed: collapsed }),
  toggleMarketWatch: () => set((state) => ({ isMarketWatchOpen: !state.isMarketWatchOpen })),
  setMarketWatchOpen: (open) => set({ isMarketWatchOpen: open }),
  setMarketWatchWidth: (width) => {
    const clamped = Math.min(Math.max(width, 180), 550);
    set({ marketWatchWidth: clamped });
    try {
      localStorage.setItem('quantedge_mw_width', String(clamped));
    } catch {}
  },
  setLiveTradingLeftColWidth: (width) => {
    const clamped = Math.min(Math.max(width, 160), 450);
    set({ liveTradingLeftColWidth: clamped });
    try {
      localStorage.setItem('quantedge_ltc_width', String(clamped));
    } catch {}
  },
  setLiveTradingRightColWidth: (width) => {
    const clamped = Math.min(Math.max(width, 240), 550);
    set({ liveTradingRightColWidth: clamped });
    try {
      localStorage.setItem('quantedge_rtc_width', String(clamped));
    } catch {}
  },
  toggleCommandPalette: () => set((state) => ({ isCommandPaletteOpen: !state.isCommandPaletteOpen })),
  setCommandPaletteOpen: (open) => set({ isCommandPaletteOpen: open }),
  toggleDeveloperMode: () => set((state) => ({ isDeveloperMode: !state.isDeveloperMode })),
  setDeveloperMode: (enabled) => set({ isDeveloperMode: enabled }),
  setSystemStatus: (status) => set({ systemStatus: status }),
  setIsAlgoRunning: (running) => set({ isAlgoRunning: running }),

  toggleWidget: (widgetKey) => {
    const currentWidgets = get().widgets;
    const updated = { ...currentWidgets, [widgetKey]: !currentWidgets[widgetKey] };
    set({ widgets: updated });
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    } catch {
      // ignore
    }
  },

  resetWidgetLayout: () => {
    set({ widgets: defaultWidgetState });
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(defaultWidgetState));
    } catch {
      // ignore
    }
  },
}));
