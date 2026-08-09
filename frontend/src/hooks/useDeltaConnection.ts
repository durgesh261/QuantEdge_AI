import { useCallback, useEffect, useRef } from 'react';
import { useDeltaStore } from '../store/useDeltaStore';
import { apiClient as api } from '../services/api';

const POLL_INTERVAL_MS = 3000;
const WS_URL = 'wss://socket.delta.exchange';

export function useDeltaConnection() {
  const {
    isDeltaEnabled,
    isConnected,
    isConnecting,
    setDeltaEnabled,
    setConnected,
    setConnecting,
    setConnectionMode,
    setConnectionError,
    setTicker,
    setPositions,
  } = useDeltaStore();

  const wsRef = useRef<WebSocket | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isMounted = useRef(true);

  // ─── REST Polling Fallback ───────────────────────────
  const startPolling = useCallback(() => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    
    const poll = async () => {
      if (!isMounted.current || !useDeltaStore.getState().isDeltaEnabled) return;
      
      try {
        // Try Delta public ticker as health check + data source
        const product = 'BTCUSD'; // or derive from active symbol
        const res = await fetch(`https://api.delta.exchange/v2/tickers/${product}`, {
          signal: AbortSignal.timeout(5000),
        });
        
        if (!isMounted.current) return;
        
        if (res.ok) {
          const json = await res.json();
          const result = json.result || json;
          setTicker({
            price: parseFloat(result.mark_price || result.price || 0),
            change_24h: parseFloat(result.change_24h || 0),
            volume_24h: parseFloat(result.volume_24h || 0),
            high_24h: parseFloat(result.high || 0),
            low_24h: parseFloat(result.low || 0),
          });
          setConnected(true);
          setConnecting(false);
          setConnectionMode('polling');
          setConnectionError(null);
        }
      } catch (err: any) {
        if (!isMounted.current) return;
        setConnectionError('Polling fallback active');
      }
    };

    poll(); // Immediate
    pollTimerRef.current = setInterval(poll, POLL_INTERVAL_MS);
  }, [setConnected, setConnecting, setConnectionMode, setConnectionError, setTicker]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // ─── WebSocket Connection ────────────────────────────
  const connectWS = useCallback(() => {
    if (!isMounted.current) return;
    
    // Notify backend
    api.post('/orders/delta-state', { enabled: true }).catch(() => {});

    try {
      const ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        if (!isMounted.current) return;
        setConnected(true);
        setConnecting(false);
        setConnectionMode('websocket');
        setConnectionError(null);
        stopPolling(); // WS succeeded, stop polling
        
        ws.send(JSON.stringify({
          type: 'subscribe',
          payload: { channels: ['v2/ticker', 'v2/positions'] },
        }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case 'ticker':
              setTicker(data.payload);
              break;
            case 'positions':
              setPositions(data.payload || []);
              break;
          }
        } catch (e) {
          console.error('[Delta WS] Parse error:', e);
        }
      };

      ws.onerror = () => {
        if (!isMounted.current) return;
        setConnectionMode('none');
        // Fall back to polling
        startPolling();
      };

      ws.onclose = () => {
        if (!isMounted.current) return;
        setConnected(false);
        setConnectionMode('none');
        // If still enabled, reconnect via polling
        if (useDeltaStore.getState().isDeltaEnabled) {
          startPolling();
        }
      };

      wsRef.current = ws;
    } catch (err: any) {
      setConnecting(false);
      startPolling();
    }
  }, [setConnected, setConnecting, setConnectionMode, setConnectionError, setTicker, setPositions, startPolling, stopPolling]);

  const disconnect = useCallback(() => {
    // Notify backend
    api.post('/orders/delta-state', { enabled: false }).catch(() => {});

    stopPolling();
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
    setConnecting(false);
    setConnectionMode('none');
    setConnectionError(null);
  }, [setConnected, setConnecting, setConnectionMode, setConnectionError, stopPolling]);

  // ─── Toggle Handler ──────────────────────────────────
  const toggleConnection = useCallback(() => {
    const store = useDeltaStore.getState();
    
    if (store.isDeltaEnabled) {
      // Turn OFF
      setDeltaEnabled(false);
      disconnect();
    } else {
      // Turn ON
      setDeltaEnabled(true);
      setConnecting(true);
      connectWS();
    }
  }, [setDeltaEnabled, setConnecting, connectWS, disconnect]);

  // ─── Sync on mount / cleanup ─────────────────────────
  useEffect(() => {
    isMounted.current = true;
    
    // If store says enabled but we're not connected, reconnect
    if (useDeltaStore.getState().isDeltaEnabled && !useDeltaStore.getState().isConnected) {
      setConnecting(true);
      connectWS();
    }

    return () => {
      isMounted.current = false;
      disconnect();
    };
  }, [connectWS, disconnect, setConnecting]);

  return {
    isDeltaEnabled,
    isConnected,
    isConnecting,
    connectionError: useDeltaStore((s) => s.connectionError),
    connectionMode: useDeltaStore((s) => s.connectionMode),
    toggleConnection,
    connect: connectWS,
    disconnect,
  };
}
