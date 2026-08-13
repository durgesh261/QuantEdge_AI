import { OrderBlockDto } from '@algoapp/shared';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { OrderBlockMergeEngine } from '../engines/orderBlockMergeEngine.js';
import { eventBus } from '../../../services/EventBus.js';

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
  status: 'ACTIVE' | 'TOUCHED' | 'TRADED' | 'MITIGATED' | 'MERGED';
  sourceBarTime: string;
  createdFromStructure: string;
  structureType: 'INTERNAL' | 'SWING';
  // Merge tracking
  sourceIds?: string[];
  mergedZoneId?: string;
  isMerged?: boolean;
}

export class CanonicalOBRegistry {
  private static registry = new Map<string, CanonicalOBEntry[]>();

  public static async loadFromDb(): Promise<void> {
    try {
      // ─── STARTUP CLEANUP ────────────────────────────────────────────────
      // Mark any stale TOUCHED OBs from previous sessions as TRADED.
      // If an OB was TOUCHED but never TRADED (server crashed / was killed),
      // it must be consumed on restart to prevent infinite re-evaluation.
      await prisma.canonicalOrderBlock.updateMany({
        where: { status: 'TOUCHED', mitigated: false },
        data: { status: 'TRADED', traded: true },
      }).catch((e: any) => logger.warn('[CanonicalOBRegistry] Stale TOUCHED cleanup failed:', e));

      // Only load ACTIVE OBs on startup.
      // TOUCHED/TRADED OBs from previous sessions must NOT be reloaded —
      // they would fire as "first-touched" again every tick.
      const blocks = await prisma.canonicalOrderBlock.findMany({
        where: { mitigated: false, status: 'ACTIVE' },
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
            sourceIds: (b as any).sourceIds,
            mergedZoneId: (b as any).mergedZoneId,
            isMerged: (b as any).isMerged,
          });
          this.registry.set(b.symbol, existing);
        }
      }
      logger.info(`[CanonicalOBRegistry] Loaded ${blocks.length} ACTIVE OBs from DB`);
    } catch (err) {
      logger.warn('[CanonicalOBRegistry] Failed to load from DB:', err);
    }
  }

  public static syncFromIndicator(symbol: string, activeOBs: OrderBlockDto[]): void {
    const existing = this.registry.get(symbol) || [];
    
    // ── MERGE OVERLAPPING ORDER BLOCKS ────────────────────────────────────
    const demandOBs = activeOBs.filter(ob => ob.type === 'BULLISH');
    const supplyOBs = activeOBs.filter(ob => ob.type === 'BEARISH');
    const mergeResult = OrderBlockMergeEngine.merge(demandOBs, supplyOBs);
    const merged = mergeResult.merged;
    
    const activeIds = new Set(merged.map((ob) => ob.id));

    // 1. Add new merged OBs from indicator
    for (const ob of merged) {
      let entry = existing.find((e) => e.id === ob.id);
      const isNew = !entry;
      const isMerged = ob.isMerged && ob.sourceIds && ob.sourceIds.length > 1;
      
      if (isNew) {
        const newEntry: CanonicalOBEntry = {
          id: ob.id,
          symbol,
          timeframe: ob.timeframe || '1H',
          direction: ob.type,
          sourceType: ob.id.includes('INT') ? 'INTERNAL' : 'SWING',
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
          structureType: ob.id.includes('INT') ? 'INTERNAL' : 'SWING',
          sourceIds: ob.sourceIds,
          mergedZoneId: ob.mergedZoneId,
          isMerged: ob.isMerged,
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

        // Emit OB created/merged event
        eventBus.emit('ob:created', {
          symbol,
          orderBlockId: ob.id,
          type: ob.type,
          upperPrice: ob.upperPrice,
          lowerPrice: ob.lowerPrice,
          widthPercent: ob.widthPercent,
          baseCandleIndex: ob.baseCandleIndex,
          breakCandleIndex: ob.breakCandleIndex,
          sourceIds: ob.sourceIds,
          isMerged: isMerged,
          timestamp: new Date().toISOString(),
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

        // Emit OB invalidated event
        eventBus.emit('ob:invalidated', {
          symbol,
          orderBlockId: entry.id,
          type: entry.direction,
          upperPrice: entry.upperPrice,
          lowerPrice: entry.lowerPrice,
          reason: 'mitigated_by_price',
          timestamp: new Date().toISOString(),
        });
      }
    }

    this.registry.set(symbol, existing);

    // Emit zones updated event with all active OBs
    const activeEntries = existing.filter(e => !e.mitigated && !e.traded);
    eventBus.emit('zones:updated', {
      symbol,
      zones: activeEntries.map(e => ({
        id: e.id,
        type: e.direction === 'BULLISH' ? 'DEMAND' : 'SUPPLY',
        upperPrice: e.upperPrice,
        lowerPrice: e.lowerPrice,
        widthPercent: ((e.upperPrice - e.lowerPrice) / e.upperPrice) * 100,
        baseCandleIndex: e.baseCandleIndex,
        breakCandleIndex: e.breakCandleIndex,
        sourceIds: e.sourceIds,
        isMerged: e.isMerged,
        createdAt: e.createdAt,
        touched: e.touched,
        traded: e.traded,
      })),
      timestamp: new Date().toISOString(),
    });
  }

  public static checkLiveTouch(symbol: string, livePrice: number, timestamp: string): CanonicalOBEntry[] {
    const entries = this.registry.get(symbol) || [];
    const newlyTouched: CanonicalOBEntry[] = [];

    for (const entry of entries) {
      // GUARD: skip mitigated, already-touched, OR already-traded OBs
      // An OB can only be FIRST-TOUCHED once in its lifetime
      if (!entry.mitigated && !entry.touched && !entry.traded) {
        // Price touches range (between lowerPrice and upperPrice)
        if (livePrice >= entry.lowerPrice && livePrice <= entry.upperPrice) {
          entry.touched = true;
          entry.firstTouchTime = timestamp;
          entry.firstTouchPrice = livePrice;
          entry.status = 'TOUCHED';
          newlyTouched.push(entry);

          logger.info(
            `[CanonicalOBRegistry] FIRST-TOUCH: ${entry.id} price=${livePrice} range=[${entry.lowerPrice},${entry.upperPrice}]`
          );

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
    // Active = not mitigated AND not traded (traded OBs are consumed)
    return entries.filter((e) => !e.mitigated && !e.traded);
  }

  public static getTouched(symbol: string): CanonicalOBEntry[] {
    const entries = this.registry.get(symbol) || [];
    // Touched = touched flag set, not mitigated, NOT yet traded
    // Once markTraded is called, this OB will NOT appear here again
    return entries.filter((e) => e.touched && !e.mitigated && !e.traded);
  }

  public static clear(symbol?: string): void {
    if (symbol) {
      this.registry.delete(symbol);
    } else {
      this.registry.clear();
    }
  }
}