import { CandleDto } from '@algoapp/shared';
import { prisma } from '../../../db.js';
import { DeltaRestClient } from '../../delta-exchange/services/DeltaRestClient.js';

const INTERNAL_SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
const TIMEFRAME = '1H';
const RESOLUTION = '60';
const DAYS_BACK = 180;
const MAX_CANDLES_PER_REQUEST = 500;

export class HistoricalBackfillService {
  private static isRunning = false;

  public static async backfillAll(restClient: DeltaRestClient): Promise<void> {
    if (this.isRunning) {
      console.log('[HistoricalBackfill] Already running, skipping');
      return;
    }

    this.isRunning = true;
    console.log('[HistoricalBackfill] Starting 180-day 1H backfill for all pairs...');

    try {
      for (const symbol of INTERNAL_SYMBOLS) {
        await this.backfillSymbol(restClient, symbol);
      }
      console.log('[HistoricalBackfill] Completed for all pairs');
    } catch (err) {
      console.error('[HistoricalBackfill] Error:', err);
    } finally {
      this.isRunning = false;
    }
  }

  private static async backfillSymbol(restClient: DeltaRestClient, internalSymbol: string): Promise<void> {
    const deltaSymbol = restClient.toExchangeSymbol(internalSymbol);
    const to = Math.floor(Date.now() / 1000);
    const from = to - DAYS_BACK * 24 * 3600;

    console.log(`[HistoricalBackfill] Fetching ${internalSymbol} (${deltaSymbol}) from ${new Date(from * 1000).toISOString()} to ${new Date(to * 1000).toISOString()}`);

    const allCandles = await this.fetchAllCandlesPaginated(restClient, deltaSymbol, from, to);

    if (allCandles.length === 0) {
      console.warn(`[HistoricalBackfill] No candles returned for ${internalSymbol}`);
      return;
    }

    console.log(`[HistoricalBackfill] Received ${allCandles.length} candles for ${internalSymbol}`);

    const candleDtos: CandleDto[] = allCandles.map((c, idx) => ({
      id: `CNDL-${internalSymbol}-${c.t}-${idx}`,
      symbol: internalSymbol,
      timeframe: TIMEFRAME,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
      volume: c.v,
      timestamp: new Date(c.t * 1000).toISOString(),
    }));

    await this.persistCandles(candleDtos);
    console.log(`[HistoricalBackfill] Persisted ${candleDtos.length} candles for ${internalSymbol}`);
  }

  private static async fetchAllCandlesPaginated(
    restClient: DeltaRestClient,
    deltaSymbol: string,
    from: number,
    to: number
  ): Promise<Array<{ t: number; o: number; h: number; l: number; c: number; v: number }>> {
    const allCandles: Array<{ t: number; o: number; h: number; l: number; c: number; v: number }> = [];
    let currentTo = to;
    let hasMore = true;
    let requestCount = 0;
    const maxRequests = 20;

    while (hasMore && requestCount < maxRequests) {
      requestCount++;
      const requestFrom = Math.max(from, currentTo - MAX_CANDLES_PER_REQUEST * 3600);

      console.log(`[HistoricalBackfill]   Batch ${requestCount}: ${deltaSymbol} ${new Date(requestFrom * 1000).toISOString()} to ${new Date(currentTo * 1000).toISOString()}`);

      const batch = await restClient.getHistoricalCandles(deltaSymbol, RESOLUTION as any, requestFrom, currentTo);

      if (batch.length === 0) {
        console.log(`[HistoricalBackfill]   No more candles available`);
        hasMore = false;
        break;
      }

      allCandles.unshift(...batch);
      console.log(`[HistoricalBackfill]   Received ${batch.length} candles (total: ${allCandles.length})`);

      const earliestBatchTime = batch[0]?.t ?? currentTo;
      if (earliestBatchTime <= from || batch.length < MAX_CANDLES_PER_REQUEST) {
        hasMore = false;
        break;
      }

      currentTo = earliestBatchTime - 1;
      await new Promise(resolve => setTimeout(resolve, 100));
    }

    const seen = new Set<number>();
    const uniqueCandles = allCandles.filter(candle => {
      if (seen.has(candle.t)) return false;
      seen.add(candle.t);
      return true;
    });

    uniqueCandles.sort((a, b) => a.t - b.t);

    return uniqueCandles;
  }

  private static async persistCandles(candles: CandleDto[]): Promise<void> {
    if (candles.length === 0) return;

    const batchSize = 100;
    for (let i = 0; i < candles.length; i += batchSize) {
      const batch = candles.slice(i, i + batchSize);

      const operations = batch.map((c) =>
        prisma.marketCandle.upsert({
          where: {
            symbol_timeframe_timestamp: {
              symbol: c.symbol,
              timeframe: c.timeframe,
              timestamp: new Date(c.timestamp),
            },
          },
          update: {
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
          },
          create: {
            id: c.id,
            symbol: c.symbol,
            timeframe: c.timeframe,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
            timestamp: new Date(c.timestamp),
          },
        })
      );

      await prisma.$transaction(operations);
    }
  }

  public static async hasSufficientData(): Promise<boolean> {
    try {
      for (const symbol of INTERNAL_SYMBOLS) {
        const count = await prisma.marketCandle.count({
          where: { symbol, timeframe: TIMEFRAME },
        });
        if (count < 500) {
          return false;
        }
      }
      return true;
    } catch {
      return false;
    }
  }
}