import { OrderBlockDto } from '@algoapp/shared';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';

export interface CanonicalOBEntry {
  id: string;
  symbol: string;
  timeframe: string;
  direction: 'BULLISH' | 'BEARISH';
  sourceType: 'INTERNAL' | 'SWING';
  upperPrice: number;
  lowerPrice: number;
  barHigh: number;
  barLow: number;
  barTime: string;
  baseCandleIndex: number;
  breakCandleIndex: number;
  createdAt: string;
  mitigated: boolean;
  mitigatedAt?: string | undefined;
  touched: boolean;
  firstTouchTime?: string | undefined;
  firstTouchPrice?: number | undefined;
  traded: boolean;
  status: 'ACTIVE' | 'TOUCHED' | 'TRADED' | 'MITIGATED';
  sourceBarTime: string;
  createdFromStructure: string;
  structureType: 'INTERNAL' | 'SWING';
}

export class CanonicalOBRegistry {
  private static registry = new Map<string, CanonicalOBEntry[]>();

  public static async loadFromDb(): Promise<void> {
    try {
      const blocks = await prisma.canonicalOrderBlock.findMany({
        where: { mitigated: false },
      });
      for (const b of blocks) {
        const existing = this.registry.get(b.symbol) || [];
        if (!existing.some((e) => e.id === b.id)) {
          existing.push({
            id: b.id,
            symbol: b.symbol,
            timeframe: b.timeframe,
            direction: b.direction as any,
            sourceType: b.sourceType as any,
            upperPrice: b.upperPrice,
            lowerPrice: b.lowerPrice,
            barHigh: (b as any).barHigh ?? b.upperPrice,
            barLow: (b as any).barLow ?? b.lowerPrice,
            barTime: b.sourceBarTime ? b.sourceBarTime.toISOString() : b.createdAt.toISOString(),
            baseCandleIndex: b.baseCandleIndex,
            breakCandleIndex: b.breakCandleIndex,
            createdAt: b.createdAt.toISOString(),
            mitigated: b.mitigated,
            touched: b.touched,
            traded: b.traded,
            status: b.status as any,
            sourceBarTime: b.sourceBarTime ? b.sourceBarTime.toISOString() : b.createdAt.toISOString(),
            createdFromStructure: b.createdFromStructure,
            structureType: b.structureType as any,
          });
          this.registry.set(b.symbol, existing);
        }
      }
    } catch (err) {
      logger.warn('[CanonicalOBRegistry] Failed to load from DB:', err);
    }
  }

  public static syncFromIndicator(symbol: string, activeOBs: OrderBlockDto[]): void {
    const existing = this.registry.get(symbol) || [];
    const activeIds = new Set(activeOBs.map((ob) => ob.id));

    // 1. Add new OBs from indicator
    for (const ob of activeOBs) {
      let entry = existing.find((e) => e.id === ob.id);
      if (!entry) {
        const isInternal = ob.id.includes('INT') || ob.id.includes('INTERNAL');
        const newEntry: CanonicalOBEntry = {
          id: ob.id,
          symbol,
          timeframe: ob.timeframe || '1H',
          direction: ob.type,
          sourceType: isInternal ? 'INTERNAL' : 'SWING',
          upperPrice: ob.upperPrice,
          lowerPrice: ob.lowerPrice,
          barHigh: ob.upperPrice,
          barLow: ob.lowerPrice,
          barTime: ob.createdAt,
          baseCandleIndex: ob.baseCandleIndex,
          breakCandleIndex: ob.breakCandleIndex,
          createdAt: ob.createdAt,
          mitigated: false,
          touched: false,
          traded: false,
          status: 'ACTIVE',
          sourceBarTime: ob.createdAt,
          createdFromStructure: 'BOS',
          structureType: isInternal ? 'INTERNAL' : 'SWING',
        };
        existing.push(newEntry);

        // Persist to DB asynchronously
        prisma.canonicalOrderBlock
          .upsert({
            where: { id: newEntry.id },
            create: {
              id: newEntry.id,
              symbol: newEntry.symbol,
              timeframe: newEntry.timeframe,
              direction: newEntry.direction,
              sourceType: newEntry.sourceType,
              sourceBarTime: new Date(newEntry.sourceBarTime),
              upperPrice: newEntry.upperPrice,
              lowerPrice: newEntry.lowerPrice,
              createdAt: new Date(newEntry.createdAt),
              createdFromStructure: newEntry.createdFromStructure,
              structureType: newEntry.structureType,
              status: newEntry.status,
              mitigated: newEntry.mitigated,
              touched: newEntry.touched,
              traded: newEntry.traded,
              parsedBarHigh: newEntry.barHigh,
              parsedBarLow: newEntry.barLow,
              baseCandleIndex: newEntry.baseCandleIndex,
              breakCandleIndex: newEntry.breakCandleIndex,
            },
            update: {
              status: newEntry.status,
              mitigated: newEntry.mitigated,
            },
          })
          .catch((err) => {
            logger.warn(`[CanonicalOBRegistry] DB upsert failed for ${newEntry.id}:`, err);
          });
      }
    }

    // 2. Mark OBs mitigated if no longer in indicator's active list
    for (const entry of existing) {
      if (!entry.mitigated && !activeIds.has(entry.id)) {
        entry.mitigated = true;
        entry.status = 'MITIGATED';

        prisma.canonicalOrderBlock
          .update({
            where: { id: entry.id },
            data: { status: 'MITIGATED', mitigated: true, mitigatedAt: new Date() },
          })
          .catch(() => {});
      }
    }

    this.registry.set(symbol, existing);
  }

  public static checkLiveTouch(symbol: string, livePrice: number, timestamp: string): CanonicalOBEntry[] {
    const entries = this.registry.get(symbol) || [];
    const newlyTouched: CanonicalOBEntry[] = [];

    for (const entry of entries) {
      if (!entry.mitigated && !entry.touched) {
        // Price touches range (between lowerPrice and upperPrice)
        if (livePrice >= entry.lowerPrice && livePrice <= entry.upperPrice) {
          entry.touched = true;
          entry.firstTouchTime = timestamp;
          entry.firstTouchPrice = livePrice;
          entry.status = 'TOUCHED';
          newlyTouched.push(entry);

          prisma.canonicalOrderBlock
            .update({
              where: { id: entry.id },
              data: {
                touched: true,
                firstTouchTime: new Date(timestamp),
                firstTouchPrice: livePrice,
                status: 'TOUCHED',
              },
            })
            .catch(() => {});
        }
      }
    }

    return newlyTouched;
  }

  public static markTraded(obId: string): void {
    for (const [_symbol, entries] of this.registry.entries()) {
      const entry = entries.find((e) => e.id === obId);
      if (entry) {
        entry.traded = true;
        entry.status = 'TRADED';

        prisma.canonicalOrderBlock
          .update({
            where: { id: obId },
            data: { traded: true, status: 'TRADED' },
          })
          .catch(() => {});
        break;
      }
    }
  }

  public static getActive(symbol: string): CanonicalOBEntry[] {
    const entries = this.registry.get(symbol) || [];
    return entries.filter((e) => !e.mitigated);
  }

  public static getTouched(symbol: string): CanonicalOBEntry[] {
    const entries = this.registry.get(symbol) || [];
    return entries.filter((e) => e.touched && !e.mitigated);
  }

  public static clear(symbol?: string): void {
    if (symbol) {
      this.registry.delete(symbol);
    } else {
      this.registry.clear();
    }
  }
}
