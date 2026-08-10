import { useState, useEffect, useCallback, useRef } from 'react';
import { apiClient as api } from '../services/api';

export interface OrderBlock {
  id: string;
  symbol: string;
  type: 'DEMAND' | 'SUPPLY';
  priceLow: number;
  priceHigh: number;
  strength: number;
  touches: number;
  freshness: number;
  aiScore: number;
  createdAt: string;
}

export function useOrderBlocks(symbol: string) {
  const [blocks, setBlocks] = useState<OrderBlock[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSymbolRef = useRef(symbol);

  const fetchBlocks = useCallback(async (targetSymbol: string) => {
    if (!targetSymbol) return;
    setIsLoading(true);
    setError(null);
    lastSymbolRef.current = targetSymbol;
    try {
      const res = await api.get('/order-blocks', { params: { symbol: targetSymbol }, timeout: 8000 });
      if (lastSymbolRef.current !== targetSymbol) return;
      if (res.data?.success && Array.isArray(res.data.data)) {
        setBlocks(res.data.data);
      } else {
        setBlocks([]);
      }
    } catch (err: any) {
      if (lastSymbolRef.current !== targetSymbol) return;
      setError(err.message || 'Failed to load order blocks');
      setBlocks([]);
    } finally {
      if (lastSymbolRef.current === targetSymbol) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    setBlocks([]);
    setError(null);
    fetchBlocks(symbol);
    const interval = setInterval(() => fetchBlocks(symbol), 10000);

    // Real-time OB removal via WebSocket ob_touched event
    const wsUrl = (import.meta as any).env?.VITE_WS_URL || 'ws://localhost:4000/ws';
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => ws?.send(JSON.stringify({ type: 'subscribe', channel: 'zones' }));
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'ob_touched' && msg.symbol === symbol) {
            setBlocks((prev) => prev.filter((b) => b.id !== msg.orderBlockId));
          }
        } catch { /* ignore */ }
      };
    } catch { /* WS unavailable — polling still works */ }

    return () => { clearInterval(interval); ws?.close(); };
  }, [symbol, fetchBlocks]);

  return { blocks, isLoading, error, refetch: () => fetchBlocks(symbol) };
}
