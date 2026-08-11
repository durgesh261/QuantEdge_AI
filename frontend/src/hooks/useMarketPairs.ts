import { useCallback, useEffect, useState } from 'react';
import { marketDataApi, strategyApi } from '../services/api';
import type { CandleDto, ZoneDto } from '@algoapp/shared';
import { chartWebSocketService, LiveTicker } from '../services/ChartWebSocketService';

// Matches the symbols seeded in the backend's MarketSnapshotService.
export const WATCHLIST_SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];

const SYMBOL_NAMES: Record<string, string> = {
  'BTCUSD.P': 'Bitcoin Perpetual',
  'ETHUSD.P': 'Ethereum Perpetual',
  'SOLUSD.P': 'Solana Perpetual',
  'XRPUSD.P': 'XRP Perpetual',
};

export interface LiveMarketPair {
  symbol: string;
  name: string;
  price: number;
  priceLabel: string;
  change24h: number;
  changeLabel: string;
  isPositive: boolean;
  trend: string;
  volatility: string;
  session: string;
  /** Strongest active zone for this symbol, if any (real ZoneDto, not fabricated). */
  topZone: ZoneDto | null;
  /** Latest strategy signal outcome/confidence for this symbol, if any. */
  signalOutcome: string | null;
  signalConfidence: number | null;
}

function formatPrice(p: number): string {
  if (!Number.isFinite(p)) return '—';
  return p >= 1
    ? `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : `$${p.toFixed(4)}`;
}

export function useMarketPairs(pollMs = 2000) {
  const [pairs, setPairs] = useState<Record<string, LiveMarketPair>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const results = await Promise.all(
        WATCHLIST_SYMBOLS.map(async (symbol) => {
          const [snapshotRes, candlesRes, zonesRes, signalsRes] = await Promise.all([
            marketDataApi.getSnapshot(symbol).catch(() => null),
            marketDataApi.getCandles(symbol, '1H', 24).catch(() => ({ data: [] as CandleDto[] })),
            strategyApi.getZones(symbol).catch(() => ({ data: [] as ZoneDto[] })),
            strategyApi.getSignals().catch(() => ({ data: [] as any[] })),
          ]);

          const snapshot = snapshotRes?.data ?? (snapshotRes as any) ?? {};
          const currentPrice = snapshot.currentPrice ?? 0;
          const candles = candlesRes?.data ?? (candlesRes as any) ?? [];
          const zones = zonesRes?.data ?? (zonesRes as any) ?? [];
          const signals = (signalsRes?.data ?? (signalsRes as any) ?? []).filter((s: any) => s.symbol === symbol);

          let change24h = 0;
          if (candles.length >= 2) {
            const first = candles[0]!;
            const last = candles[candles.length - 1]!;
            if (first.open) change24h = ((last.close - first.open) / first.open) * 100;
          }

          const topZone = [...zones].sort((a, b) => b.strength - a.strength)[0] ?? null;
          const latestSignal = [...signals].sort(
            (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
          )[0];

          const pair: LiveMarketPair = {
            symbol,
            name: SYMBOL_NAMES[symbol] ?? symbol,
            price: currentPrice,
            priceLabel: formatPrice(currentPrice),
            change24h,
            changeLabel: `${change24h >= 0 ? '+' : ''}${change24h.toFixed(2)}%`,
            isPositive: change24h >= 0,
            trend: snapshot.trend ?? 'NEUTRAL',
            volatility: snapshot.volatility ?? 'MEDIUM',
            session: snapshot.session ?? '—',
            topZone,
            signalOutcome: latestSignal?.outcome ?? null,
            signalConfidence: latestSignal?.confidenceScore ?? null,
          };
          return pair;
        })
      );

      const map: Record<string, LiveMarketPair> = {};
      results.forEach((p) => {
        map[p.symbol] = p;
      });
      setPairs(map);
      setError(null);
    } catch (err) {
      setError('Could not reach the backend API. Is it running on the expected port?');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, pollMs);
    return () => clearInterval(id);
  }, [fetchAll, pollMs]);

  useEffect(() => {
    const handleTicker = (ticker: LiveTicker) => {
      setPairs((prev) => {
        const p = prev[ticker.symbol];
        if (!p || p.price === ticker.markPrice) return prev;
        return {
          ...prev,
          [ticker.symbol]: {
            ...p,
            price: ticker.markPrice,
            priceLabel: formatPrice(ticker.markPrice),
          }
        };
      });
    };
    chartWebSocketService.on('ticker', handleTicker);
    return () => {
      chartWebSocketService.off('ticker', handleTicker);
    };
  }, []);

  return {
    pairs,
    pairList: WATCHLIST_SYMBOLS.map((s) => pairs[s]).filter(Boolean) as LiveMarketPair[],
    isLoading,
    error,
    refetch: fetchAll,
  };
}
