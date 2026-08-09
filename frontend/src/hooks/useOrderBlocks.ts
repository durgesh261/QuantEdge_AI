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
    // Don't fetch if symbol is empty
    if (!targetSymbol) return;
    
    setIsLoading(true);
    setError(null);
    lastSymbolRef.current = targetSymbol;

    try {
      const res = await api.get('/order-blocks', { 
        params: { symbol: targetSymbol },
        timeout: 8000,
      });
      
      // Only update if symbol hasn't changed since request started (race condition guard)
      if (lastSymbolRef.current !== targetSymbol) return;

      if (res.data?.success && Array.isArray(res.data.data)) {
        setBlocks(res.data.data);
      } else {
        setBlocks([]);
      }
    } catch (err: any) {
      if (lastSymbolRef.current !== targetSymbol) return;
      console.error(`[useOrderBlocks] Failed for ${targetSymbol}:`, err);
      setError(err.message || 'Failed to load order blocks');
      setBlocks([]); // Empty on error — never fake data
    } finally {
      if (lastSymbolRef.current === targetSymbol) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    // Reset blocks immediately when symbol changes (don't show old symbol's blocks)
    setBlocks([]);
    setError(null);
    fetchBlocks(symbol);

    // Poll every 10 seconds for live updates from scanner engine
    const interval = setInterval(() => fetchBlocks(symbol), 10000);
    return () => clearInterval(interval);
  }, [symbol, fetchBlocks]);

  return { blocks, isLoading, error, refetch: () => fetchBlocks(symbol) };
}
