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
          // Only keep active canonical OBs — backend already filters, this is a safety net
          const active = (res.data || []).filter(
            (ob: any) => !ob.isMitigated && !ob.isUsed && !ob.isInvalidated
          );
          setOrderBlocks(active);
          setError(null);
        }
      } catch (err: any) {
        if (mounted) setError(err.message || 'Failed to fetch order blocks');
      } finally {
        if (mounted) setLoading(false);
      }
    };

    fetchOBs();
    const interval = setInterval(fetchOBs, 10000);

    // ChartWebSocketService zone updates
    const handleZones = (data: any) => {
      if (data.symbol !== symbol) return;

      if (data.type === 'ob_touched' && data.orderBlockId) {
        // Real-time: remove consumed OB immediately from chart
        setOrderBlocks((prev) => prev.filter((ob) => ob.id !== data.orderBlockId));
        return;
      }

      if (data.zones) {
        const active = (data.zones || []).filter(
          (ob: any) => !ob.isMitigated && !ob.isUsed && !ob.isInvalidated
        );
        setOrderBlocks(active);
      }
    };

    chartWebSocketService.on('zones', handleZones);

    // Also subscribe directly to the backend WS for ob_touched events
    const wsUrl = (import.meta as any).env?.VITE_WS_URL || 'ws://localhost:4000/ws';
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => ws?.send(JSON.stringify({ type: 'subscribe', channel: 'zones' }));
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'ob_touched' && msg.symbol === symbol && mounted) {
            setOrderBlocks((prev) => prev.filter((ob) => ob.id !== msg.orderBlockId));
          }
        } catch { /* ignore */ }
      };
    } catch { /* WS unavailable */ }

    return () => {
      mounted = false;
      clearInterval(interval);
      chartWebSocketService.off('zones', handleZones);
      ws?.close();
    };
  }, [symbol]);

  const activeBullishOBs = (orderBlocks || []).filter(
    ob => (ob.type === 'BULLISH' || (ob.type as string) === 'DEMAND') && !ob.isMitigated && !ob.isUsed
  );
  const activeBearishOBs = (orderBlocks || []).filter(
    ob => (ob.type === 'BEARISH' || (ob.type as string) === 'SUPPLY') && !ob.isMitigated && !ob.isUsed
  );
  const mitigatedOBs = (orderBlocks || []).filter(ob => ob.isMitigated || ob.isUsed);

  const nearestBullishOB = [...activeBullishOBs].sort((a, b) => b.upperPrice - a.upperPrice)[0] || null;
  const nearestBearishOB = [...activeBearishOBs].sort((a, b) => a.lowerPrice - b.lowerPrice)[0] || null;

  return { orderBlocks, activeBullishOBs, activeBearishOBs, mitigatedOBs, nearestBullishOB, nearestBearishOB, loading, error };
}
