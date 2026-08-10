import { useState, useEffect } from 'react';
import { OrderBlockDto } from '@algoapp/shared';
import { indicatorApi } from '../services/api';
import { chartWebSocketService } from '../services/ChartWebSocketService';

export function useOrderBlocksChart(symbol: string) {
  const [orderBlocks, setOrderBlocks] = useState<OrderBlockDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    const fetchOBs = async () => {
      try {
        const res = await indicatorApi.getOrderBlocks(symbol);
        if (res.success && mounted) {
          setOrderBlocks(res.data);
          setError(null);
        }
      } catch (err: any) {
        if (mounted) {
          setError(err.message || 'Failed to fetch order blocks');
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };

    fetchOBs();
    const interval = setInterval(fetchOBs, 10000);

    const handleZones = (data: any) => {
      if (data.symbol === symbol && data.zones) {
        setOrderBlocks(data.zones);
      }
    };
    chartWebSocketService.on('zones', handleZones);

    return () => {
      mounted = false;
      clearInterval(interval);
      chartWebSocketService.off('zones', handleZones);
    };
  }, [symbol]);

  const activeBullishOBs = (orderBlocks || []).filter(ob => (ob.type === 'BULLISH' || (ob.type as string) === 'DEMAND') && !ob.isMitigated && !ob.isUsed);
  const activeBearishOBs = (orderBlocks || []).filter(ob => (ob.type === 'BEARISH' || (ob.type as string) === 'SUPPLY') && !ob.isMitigated && !ob.isUsed);
  const mitigatedOBs = (orderBlocks || []).filter(ob => ob.isMitigated || ob.isUsed);

  const nearestBullishOB = [...activeBullishOBs].sort((a, b) => b.upperPrice - a.upperPrice)[0] || null;
  const nearestBearishOB = [...activeBearishOBs].sort((a, b) => a.lowerPrice - b.lowerPrice)[0] || null;

  return {
    orderBlocks,
    activeBullishOBs,
    activeBearishOBs,
    mitigatedOBs,
    nearestBullishOB,
    nearestBearishOB,
    loading,
    error,
  };
}

