import { OrderBlockDto, TradingTimeframe } from '@algoapp/shared';
import { prisma } from '../../../db.js';

// In-memory cache (populated from DB on startup via loadUsedFromDb)
const usedOrderBlockIds = new Set<string>();

export class OrderBlockWidthEngine {
  /**
   * Calculates entry, stop loss, take profit, leverage, and width metrics
   * strictly adhering to Version 5.1 rules:
   * - 35% fixed Stop Loss account risk
   * - 60% fixed Take Profit account growth
   * - OB width <= 0.6% -> edge entry
   * - OB width > 0.6% -> 25% deep inside the Order Block
   * - Single-use lifetime
   */
  public static enrichOrderBlock(
    id: string,
    symbol: string,
    timeframe: TradingTimeframe,
    type: 'BULLISH' | 'BEARISH',
    upperPrice: number,
    lowerPrice: number,
    baseCandleIndex: number,
    breakCandleIndex: number,
    isMitigated: boolean,
    isInvalidated: boolean,
    touchCount: number,
    source: 'PAT' | 'SMC',
    createdAt: string,
    mitigatedAtIndex?: number | undefined
  ): OrderBlockDto {
    // Width = ((Upper - Lower) / Upper) × 100  [User-defined formula]
    const rawWidth = Math.max(0.0001, upperPrice - lowerPrice);
    const widthPercent = Number(((rawWidth / Math.max(0.0001, upperPrice)) * 100).toFixed(3));

    let entryPrice: number;
    let stopLossPrice: number;
    let slDistPercent: number;

    if (type === 'BULLISH') {
      // Bullish OB: price approaches FROM ABOVE → first edge = upper price (top)
      // Width <= 0.6% → enter at upper edge (top of zone)
      // Width >  0.6% → enter 25% inside from top: upperPrice - 0.25 × rawWidth
      // Example: Upper=100, Lower=99, Width=1% → Entry = 100 - 0.25×1 = 99.75, SL = 99
      if (widthPercent <= 0.6) {
        entryPrice = upperPrice; // Enter at first edge (top)
      } else {
        entryPrice = upperPrice - 0.25 * rawWidth; // 25% inside from top
      }
      stopLossPrice = lowerPrice; // SL at opposite edge (bottom)
      slDistPercent = ((entryPrice - stopLossPrice) / entryPrice) * 100;
    } else {
      // Bearish OB: price approaches FROM BELOW → first edge = lower price (bottom)
      // Width <= 0.6% → enter at lower edge (bottom of zone)
      // Width >  0.6% → enter 25% inside from bottom: lowerPrice + 0.25 × rawWidth
      // Example: Upper=100, Lower=99, Width=1% → Entry = 99 + 0.25×1 = 99.25, SL = 100
      if (widthPercent <= 0.6) {
        entryPrice = lowerPrice; // Enter at first edge (bottom)
      } else {
        entryPrice = lowerPrice + 0.25 * rawWidth; // 25% inside from bottom
      }
      stopLossPrice = upperPrice; // SL at opposite edge (top)
      slDistPercent = ((stopLossPrice - entryPrice) / entryPrice) * 100;
    }

    entryPrice = Number(entryPrice.toFixed(4));
    stopLossPrice = Number(stopLossPrice.toFixed(4));
    slDistPercent = Math.max(0.1, slDistPercent);

    // Dynamic Leverage calculation: exactly 35% account risk, capped at 100x
    const calculatedLeverage = Math.min(100, Math.max(1, Math.round(35 / slDistPercent)));

    // Take Profit target: exactly 60% account growth
    const tpDistPercent = 60 / calculatedLeverage;
    let takeProfitPrice: number;
    if (type === 'BULLISH') {
      takeProfitPrice = Number((entryPrice * (1 + tpDistPercent / 100)).toFixed(4));
    } else {
      takeProfitPrice = Number((entryPrice * (1 - tpDistPercent / 100)).toFixed(4));
    }

    const isUsed = usedOrderBlockIds.has(id);

    return {
      id,
      symbol,
      timeframe,
      type,
      upperPrice,
      lowerPrice,
      widthPercent,
      entryPrice,
      stopLossPrice,
      takeProfitPrice,
      calculatedLeverage,
      baseCandleIndex,
      breakCandleIndex,
      isMitigated,
      mitigatedAtIndex,
      isInvalidated,
      isUsed,
      usedAt: isUsed ? new Date().toISOString() : undefined,
      touchCount,
      source,
      createdAt,
    };
  }

  /**
   * Marks an order block as USED (in memory + DB for persistence across restarts).
   * Once marked used, this OB cannot trigger another trade — ever.
   */
  public static markUsed(orderBlockId: string, isUsed: boolean = true): void {
    if (isUsed) {
      usedOrderBlockIds.add(orderBlockId);
    } else {
      usedOrderBlockIds.delete(orderBlockId);
    }
    // Persist to DB asynchronously (fire-and-forget, non-blocking)
    prisma.orderBlock.upsert({
      where: { id: orderBlockId },
      create: {
        id:          orderBlockId,
        canonicalId: orderBlockId,
        symbol:      'UNKNOWN',   // will be overwritten by markUsedWithMeta()
        type:        'BULLISH',
        upperPrice:  0,
        lowerPrice:  0,
        strength:    0,
        width:       0,
        widthPercent:0,
        isUsed:      true,
        isTraded:    true,
        usedAt:      new Date(),
        status:      'USED',
      },
      update: {
        isUsed:   true,
        isTraded: true,
        usedAt:   new Date(),
        status:   'USED',
      },
    }).catch(() => { /* ignore DB errors — in-memory still enforces the rule */ });
  }

  /**
   * Marks an OB used with full context — preferred over markUsed() when metadata is available.
   */
  public static markUsedWithMeta(
    orderBlockId: string,
    symbol: string,
    type: 'BULLISH' | 'BEARISH',
    upper: number,
    lower: number,
    widthPct: number,
    canonicalId: string,
  ): void {
    usedOrderBlockIds.add(orderBlockId);
    prisma.orderBlock.upsert({
      where: { id: orderBlockId },
      create: {
        id:          orderBlockId,
        canonicalId: canonicalId || orderBlockId,
        symbol,
        type,
        upperPrice:  upper,
        lowerPrice:  lower,
        strength:    0,
        width:       upper - lower,
        widthPercent: widthPct,
        isUsed:      true,
        isTraded:    true,
        usedAt:      new Date(),
        status:      'USED',
      },
      update: {
        isUsed:      true,
        isTraded:    true,
        usedAt:      new Date(),
        status:      'USED',
        canonicalId: canonicalId || orderBlockId,
      },
    }).catch(() => { /* ignore */ });
  }

  /**
   * Checks if an order block is already used (fast in-memory lookup).
   */
  public static isUsed(orderBlockId: string): boolean {
    return usedOrderBlockIds.has(orderBlockId);
  }

  /**
   * Resets used order blocks (for backtesting only — not for production).
   */
  public static resetUsed(): void {
    usedOrderBlockIds.clear();
  }

  /**
   * Load used OB IDs from DB into the in-memory cache on startup.
   * Called once at application boot so that restarts don't resurrect consumed OBs.
   */
  public static async loadUsedFromDb(): Promise<void> {
    try {
      const usedRecords = await prisma.orderBlock.findMany({
        where: { isUsed: true },
        select: { id: true, canonicalId: true },
      });
      for (const rec of usedRecords) {
        usedOrderBlockIds.add(rec.id);
        if (rec.canonicalId) usedOrderBlockIds.add(rec.canonicalId);
      }
    } catch {
      // DB may not be ready yet — ignore; in-memory cache will be empty until populated
    }
  }
}
