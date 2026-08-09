import { useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import { useScannerStore } from '../store/useScannerStore';
import { apiClient as api } from '../services/api';

export function useScannerSocket() {
  const socketRef = useRef<Socket | null>(null);
  const { setState, updatePair, updateGlobal } = useScannerStore();

  useEffect(() => {
    // Initial REST fetch
    api.get('/scanner/state').then((res) => {
      if (res.data?.success) {
        setState(
          res.data.data.global,
          res.data.data.pairs,
          res.data.data.signals
        );
      }
    }).catch(console.error);

    // WebSocket connection
    // Ensure the socket connects to the backend host properly, assuming it's on localhost:3000
    // Adjust if necessary depending on your proxy setup
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
      updateGlobal({ ticksTotal: (prev: any) => (prev?.ticksTotal || 0) + 1 });
    });

    socket.on('signal', (payload: any) => {
      updateGlobal({ signalsTotal: (prev: any) => (prev?.signalsTotal || 0) + 1 });
    });

    socket.on('control', (payload: any) => {
      // Refresh full state on control actions
      api.get('/scanner/state').then((res) => {
        if (res.data?.success) {
          setState(res.data.data.global, res.data.data.pairs, res.data.data.signals);
        }
      });
    });

    socket.on('disconnect', () => console.log('[WS:Scanner] Disconnected'));

    return () => {
      socket.disconnect();
    };
  }, [setState, updatePair, updateGlobal]);

  const sendControl = async (action: string, symbol?: string) => {
    if (symbol) {
      await api.post(`/scanner/pair/${symbol}/control`, { action });
    } else {
      await api.post('/scanner/control', { action });
    }
  };

  return { sendControl };
}
