import { useState, useEffect, useCallback } from 'react';
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
  const [isLoading, setIsLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchBlocks = useCallback(async () => {
    try {
      setIsLoading(true);
      const res = await api.get('/order-blocks', { params: { symbol } });
      if (res.data?.success) {
        setBlocks(res.data.data || []);
        setLastUpdated(new Date());
      }
    } catch (err) {
      console.error('[useOrderBlocks] Failed to fetch:', err);
    } finally {
      setIsLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchBlocks();
    // Poll every 10s for live updates
    const interval = setInterval(fetchBlocks, 10000);
    return () => clearInterval(interval);
  }, [fetchBlocks]);

  return { blocks, isLoading, lastUpdated, refetch: fetchBlocks };
}
