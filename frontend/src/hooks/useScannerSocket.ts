import { useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import { useScannerStore } from '../store/useScannerStore';
import { apiClient as api } from '../services/api';

const FALLBACK_PAIRS = [
  { symbol: 'BTCUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'ETHUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'SOLUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'XRPUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, lastTickAt: new Date().toISOString(), ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
];

const FALLBACK_GLOBAL = {
  isRunning: true,
  isPaused: false,
  ticksTotal: 0,
  signalsTotal: 0,
  tradesTotal: 0,
};

export function useScannerSocket() {
  const socketRef = useRef<Socket | null>(null);
  const { setState, updatePair, updateGlobal, setLoading } = useScannerStore();

  // ── Fetch state from REST ─────────────────────────────
  const fetchState = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/scanner/state');
      if (res.data?.success) {
        setState(
          res.data.data.global || FALLBACK_GLOBAL,
          res.data.data.pairs?.length ? res.data.data.pairs : FALLBACK_PAIRS,
          res.data.data.signals || []
        );
      } else {
        throw new Error('Invalid response');
      }
    } catch (err) {
      console.error('[ScannerSocket] Failed to load state:', err);
      // Fallback so UI is never stuck on loading
      setState(FALLBACK_GLOBAL, FALLBACK_PAIRS, []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 1. Initial REST load
    fetchState();

    // 2. WebSocket connection
    const socketUrl = import.meta.env.VITE_API_URL?.replace('/api/v1', '') || 'http://localhost:3000';
    const socket = io(`${socketUrl}/scanner`, {
      transports: ['websocket'],
      reconnection: true,
      reconnectionDelay: 3000,
    });

    socketRef.current = socket;

    socket.on('connect', () => console.log('[WS:Scanner] Connected'));

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
      // Increment ticksTotal directly (no function-based updater)
      updateGlobal({ ticksTotal: (useScannerStore.getState().global?.ticksTotal || 0) + 1 });
    });

    socket.on('signal', () => {
      updateGlobal({ signalsTotal: (useScannerStore.getState().global?.signalsTotal || 0) + 1 });
    });

    socket.on('control', () => {
      // Refresh full state on any control broadcast so UI stays in sync
      fetchState(true);
    });

    socket.on('disconnect', () => console.log('[WS:Scanner] Disconnected'));

    return () => {
      socket.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Control actions ─────────────────────────────────
  const sendControl = async (action: string, symbol?: string) => {
    try {
      if (symbol) {
        await api.post(`/scanner/pair/${encodeURIComponent(symbol)}/control`, { action });
      } else {
        await api.post('/scanner/control', { action });
      }
      // Immediately refresh state so UI updates even if WS is down
      await fetchState(true);
    } catch (err) {
      console.error('[ScannerSocket] Control failed:', err);
    }
  };

  return { sendControl, refresh: fetchState };
}
