import { MarketSnapshotDto } from '@algoapp/shared';
import { deltaSyncService } from '../../delta-exchange/index.js';

interface SnapshotCacheEntry {
  snapshot: MarketSnapshotDto;
  timestamp: number;
}

const snapshotCache: Record<string, SnapshotCacheEntry> = {};
const CACHE_TTL_MS = 5000;

export class MarketSnapshotService {
  public static async getSnapshot(symbol: string): Promise<MarketSnapshotDto | null> {
    const cached = snapshotCache[symbol];
    if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
      return cached.snapshot;
    }

    const restClient = deltaSyncService.getRestClient();
    if (!restClient.isConfigured()) {
      return null;
    }

    try {
      const deltaSymbol = restClient.toExchangeSymbol(symbol);
      const t = await restClient.getTicker(deltaSymbol);

      if (t && (t.mark_price || t.close || t.spot_price)) {
        const livePrice = parseFloat(t.close || t.mark_price || t.spot_price);

        if (!isNaN(livePrice) && livePrice > 0) {
          const updated: MarketSnapshotDto = {
            id: `SNAP-${symbol}`,
            symbol,
            currentPrice: livePrice,
            spread: parseFloat(t.quotes?.best_ask) - parseFloat(t.quotes?.best_bid) || 0.5,
            session: 'NEW_YORK',
            trend: parseFloat(t.change_24h) >= 0 ? 'BULLISH' : 'BEARISH',
            volatility: 'MEDIUM',
            timestamp: new Date().toISOString(),
          };

          snapshotCache[symbol] = { snapshot: updated, timestamp: Date.now() };
          return updated;
        }
      }
    } catch (err) {
    }

    return null;
  }

  public static updateSnapshot(symbol: string, currentPrice: number, spread?: number): MarketSnapshotDto {
    const cached = snapshotCache[symbol];
    const existing = cached?.snapshot;

    const updated: MarketSnapshotDto = {
      id: `SNAP-${symbol}`,
      symbol,
      currentPrice,
      spread: spread ?? existing?.spread ?? 0.5,
      session: existing?.session ?? 'NEW_YORK',
      trend: existing?.trend ?? 'NEUTRAL',
      volatility: existing?.volatility ?? 'MEDIUM',
      timestamp: new Date().toISOString(),
    };

    snapshotCache[symbol] = { snapshot: updated, timestamp: Date.now() };
    return updated;
  }

  public static isAvailable(symbol: string): boolean {
    const cached = snapshotCache[symbol];
    return !!cached && Date.now() - cached.timestamp < 10000;
  }

  public static getDataStatus(symbol: string): { available: boolean; ageMs: number; source: 'DELTA_API' | 'WS_TICK' | 'STALE' | 'UNAVAILABLE' } {
    const cached = snapshotCache[symbol];
    if (!cached) return { available: false, ageMs: 0, source: 'UNAVAILABLE' };

    const ageMs = Date.now() - cached.timestamp;
    if (ageMs < 5000) return { available: true, ageMs, source: 'WS_TICK' };
    if (ageMs < 30000) return { available: true, ageMs, source: 'DELTA_API' };
    return { available: false, ageMs, source: 'STALE' };
  }
}