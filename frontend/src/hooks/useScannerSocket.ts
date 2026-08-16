import { useEffect, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import { useScannerStore } from '../store/useScannerStore';
import { apiClient as api } from '../services/api';

const FALLBACK_GLOBAL = {
  isRunning: true,
  isPaused: false,
  ticksTotal: 0,
  signalsTotal: 0,
  tradesTotal: 0,
};

const FALLBACK_PAIRS = [
  { symbol: 'BTCUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'ETHUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'SOLUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'XRPUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
];

export function useScannerSocket() {
  const socketRef = useRef<Socket | null>(null);
  const { setState, updatePair, updateGlobal, setLoading } = useScannerStore();

  // ── Fetch state from REST (stable callback) ───────────
  const fetchState = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/scanner/state');
      const payload = res.data;
      if (payload?.success) {
        const dataObj = payload.data || {};
        setState(
          dataObj.global || FALLBACK_GLOBAL,
          dataObj.pairs?.length ? dataObj.pairs : FALLBACK_PAIRS,
          dataObj.signals || [],
          dataObj.isDeltaConnected ?? false
        );
      } else {
        throw new Error('Invalid response');
      }
    } catch (err) {
      console.error('[ScannerSocket] Fetch failed:', err);
      setState(FALLBACK_GLOBAL, FALLBACK_PAIRS, [], false);
    } finally {
      setLoading(false);
    }
  }, [setState, setLoading]);

  useEffect(() => {
    // 1. Immediate REST load
    fetchState();

    // 2. REST polling every 3s (PRIMARY data source — never rely on WS alone)
    const pollInterval = setInterval(() => fetchState(true), 3000);

    // 3. WebSocket (real-time enhancement)
    const socketUrl = (import.meta as any).env?.VITE_API_URL?.replace('/api/v1', '') || '/';
    const socket = io(`${socketUrl}/scanner`, {
      transports: ['websocket'],
      reconnection: true,
      reconnectionDelay: 3000,
    });

    socketRef.current = socket;

    socket.on('connect', () => {
      console.log('[WS:Scanner] Connected');
      fetchState(true);
    });

    socket.on('tick', (payload: any) => {
      updatePair(payload.symbol, {
        livePrice: payload.price,
        priceChange24h: payload.change24h,
        activeOBs: payload.activeOBs,
        obWidthPct: payload.obWidthPct,
        aiScore: payload.aiScore,
        status: payload.status,
        lastTickAt: payload.timestamp,
      });
    });

    socket.on('signal', () => {
      const g = useScannerStore.getState().global;
      if (g) updateGlobal({ signalsTotal: g.signalsTotal + 1 });
    });

    socket.on('control', () => fetchState(true));

    socket.on('disconnect', () => console.log('[WS:Scanner] Disconnected'));

    return () => {
      clearInterval(pollInterval);
      socket.disconnect();
    };
  }, [fetchState, updatePair, updateGlobal]);

  // ── Control actions with Optimistic UI ────────────────
  const sendControl = async (action: string, symbol?: string) => {
    // Optimistic update for instant button feedback
    if (!symbol) {
      if (action === 'PAUSE_ALL') updateGlobal({ isPaused: true });
      if (action === 'RESUME_ALL') updateGlobal({ isPaused: false });
      if (action === 'STOP_ALL') updateGlobal({ isRunning: false });
      if (action === 'START_ALL') updateGlobal({ isRunning: true, isPaused: false });
    } else {
      if (action === 'PAUSE') updatePair(symbol, { isPaused: true, status: 'PAUSED' });
      if (action === 'RESUME') updatePair(symbol, { isPaused: false, status: 'ENGINE' });
      if (action === 'STOP') updatePair(symbol, { isActive: false, status: 'STOPPED' });
    }

    try {
      if (symbol) {
        await api.post(`/scanner/pair/${encodeURIComponent(symbol)}/control`, { action });
      } else {
        await api.post('/scanner/control', { action });
      }
      // Hard refresh to sync with backend truth
      await fetchState(true);
    } catch (err) {
      console.error('[ScannerSocket] Control failed:', err);
      // Revert by fetching truth
      await fetchState(true);
    }
  };

  return { sendControl, refresh: fetchState };
}
