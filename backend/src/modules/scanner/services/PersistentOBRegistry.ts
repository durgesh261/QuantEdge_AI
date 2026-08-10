import { OrderBlockDto, CandleDto } from "@algoapp/shared";
import { prisma } from "../../../db.js";
import { logger } from "../../../logger/index.js";
import { eventBus } from "../../../services/EventBus.js";

// ============================================================================
// PersistentOBRegistry
//
// THE KEY RULE:
//   An Order Block has its own lifecycle INDEPENDENT of current market price.
//
//   NEW → ACTIVE
//     ├── price never reaches it     → REMAINS ACTIVE
//     ├── live price enters zone     → USED   (first-touch consumed)
//     └── candle CLOSES through zone → INVALID (structural break)
//
//   Moving price AWAY from an untouched OB is NOT an invalidation event.
//
// This registry is the SOLE source of truth for active OBs.
// The indicator engine FEEDS it (additive only). The scanner READS from it.
// ============================================================================

export type OBState = "ACTIVE" | "USED" | "INVALID";

export interface OBRegistryEntry {
  ob: OrderBlockDto;
  state: OBState;
  symbol: string;
  addedAt: string;
  stateChangedAt: string;
  invalidReason?: string;
}

class PersistentOBRegistryImpl {
  private registry = new Map<string, OBRegistryEntry>();

  // ──────────────────────────────────────────────────────────────────────────
  // ADD — feeds new OBs from indicator engine into registry.
  // Never overwrites an OB that is already USED or INVALID.
  // Never removes existing ACTIVE OBs not present in the new list.
  // ──────────────────────────────────────────────────────────────────────────
  public addAll(symbol: string, newOBs: OrderBlockDto[]): void {
    for (const ob of newOBs) {
      const existing = this.registry.get(ob.id);
      if (existing) {
        // Already known — only update the raw data if still ACTIVE
        if (existing.state === "ACTIVE") {
          this.registry.set(ob.id, { ...existing, ob });
        }
        // USED or INVALID: leave untouched
        continue;
      }
      // Genuinely new OB — add as ACTIVE
      this.registry.set(ob.id, {
        ob,
        state: "ACTIVE",
        symbol,
        addedAt: new Date().toISOString(),
        stateChangedAt: new Date().toISOString(),
      });
      logger.debug(`[OBRegistry] New OB registered: ${ob.id} (${ob.type} ${ob.lowerPrice}–${ob.upperPrice})`);
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // MARK USED — first-touch consumed. Persists to DB.
  // ──────────────────────────────────────────────────────────────────────────
  public markUsed(obId: string): void {
    const entry = this.registry.get(obId);
    if (!entry || entry.state !== "ACTIVE") return;

    const now = new Date().toISOString();
    this.registry.set(obId, { ...entry, state: "USED", stateChangedAt: now });

    // Persist to DB
    prisma.orderBlock
      .upsert({
        where: { id: obId },
        create: {
          id: obId,
          canonicalId: obId,
          symbol: entry.symbol,
          type: entry.ob.type,
          upperPrice: entry.ob.upperPrice,
          lowerPrice: entry.ob.lowerPrice,
          strength: 0,
          width: entry.ob.upperPrice - entry.ob.lowerPrice,
          widthPercent: entry.ob.widthPercent,
          isUsed: true,
          isTraded: true,
          usedAt: new Date(),
          status: "USED",
        },
        update: { isUsed: true, isTraded: true, usedAt: new Date(), status: "USED" },
      })
      .catch(() => {});
  }

  // ──────────────────────────────────────────────────────────────────────────
  // MARK INVALID — structural break. Persists to DB.
  // ──────────────────────────────────────────────────────────────────────────
  public markInvalid(obId: string, reason: string): void {
    const entry = this.registry.get(obId);
    if (!entry || entry.state !== "ACTIVE") return;

    const now = new Date().toISOString();
    this.registry.set(obId, {
      ...entry,
      state: "INVALID",
      stateChangedAt: now,
      invalidReason: reason,
    });

    logger.info(`[OBRegistry] OB INVALIDATED: ${obId} reason=${reason}`);

    // Persist to DB
    prisma.orderBlock
      .upsert({
        where: { id: obId },
        create: {
          id: obId,
          canonicalId: obId,
          symbol: entry.symbol,
          type: entry.ob.type,
          upperPrice: entry.ob.upperPrice,
          lowerPrice: entry.ob.lowerPrice,
          strength: 0,
          width: entry.ob.upperPrice - entry.ob.lowerPrice,
          widthPercent: entry.ob.widthPercent,
          isUsed: false,
          isTraded: false,
          status: "INVALIDATED",
        },
        update: { status: "INVALIDATED" },
      })
      .catch(() => {});

    // Emit WS event so frontend removes OB immediately
    eventBus.emit("ob:invalidated", {
      orderBlockId: obId,
      symbol: entry.symbol,
      type: entry.ob.type,
      upperPrice: entry.ob.upperPrice,
      lowerPrice: entry.ob.lowerPrice,
      reason,
      timestamp: now,
    });
  }

  // ──────────────────────────────────────────────────────────────────────────
  // GET ACTIVE — all ACTIVE OBs for a symbol.
  // This is what the scanner and API use — never the raw indicator output.
  // ──────────────────────────────────────────────────────────────────────────
  public getActive(symbol: string): OrderBlockDto[] {
    const result: OrderBlockDto[] = [];
    for (const entry of this.registry.values()) {
      if (entry.symbol === symbol && entry.state === "ACTIVE") {
        result.push(entry.ob);
      }
    }
    return result;
  }

  public getAll(symbol: string): OBRegistryEntry[] {
    const result: OBRegistryEntry[] = [];
    for (const entry of this.registry.values()) {
      if (entry.symbol === symbol) result.push(entry);
    }
    return result;
  }

  // ──────────────────────────────────────────────────────────────────────────
  // CHECK AND INVALIDATE — call after each new 1H candle closes.
  //
  // Invalidation rules (the ONLY structural way to remove an untouched OB):
  //   DEMAND/BULLISH: candle CLOSES BELOW lowerPrice  → structural break
  //   SUPPLY/BEARISH: candle CLOSES ABOVE upperPrice  → structural break
  // ──────────────────────────────────────────────────────────────────────────
  public checkAndInvalidate(symbol: string, closedCandle: CandleDto): void {
    for (const [id, entry] of this.registry.entries()) {
      if (entry.symbol !== symbol || entry.state !== "ACTIVE") continue;
      const ob = entry.ob;

      if (ob.type === "BULLISH") {
        // Demand OB: broken when candle closes BELOW the lower boundary
        if (closedCandle.close < ob.lowerPrice) {
          this.markInvalid(id, `candle_close_${closedCandle.close}_below_demand_lower_${ob.lowerPrice}`);
        }
      } else {
        // Supply OB: broken when candle closes ABOVE the upper boundary
        if (closedCandle.close > ob.upperPrice) {
          this.markInvalid(id, `candle_close_${closedCandle.close}_above_supply_upper_${ob.upperPrice}`);
        }
      }
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // LOAD FROM DB — on startup, restore USED/INVALID states from DB so that
  // a backend restart does not resurrect consumed OBs.
  // ──────────────────────────────────────────────────────────────────────────
  public async loadFromDb(): Promise<void> {
    try {
      const records = await prisma.orderBlock.findMany({
        where: { OR: [{ isUsed: true }, { status: "INVALIDATED" }] },
        select: { id: true, isUsed: true, status: true, symbol: true, type: true, upperPrice: true, lowerPrice: true, widthPercent: true, canonicalId: true },
      });

      for (const rec of records) {
        const state: OBState = rec.isUsed ? "USED" : "INVALID";
        const existing = this.registry.get(rec.id);
        if (existing) {
          this.registry.set(rec.id, { ...existing, state });
        } else {
          // Reconstruct a skeleton entry so the ID is known as consumed/invalid
          const skeletonOB: OrderBlockDto = {
            id: rec.id,
            symbol: rec.symbol,
            timeframe: "1H",
            type: rec.type as "BULLISH" | "BEARISH",
            upperPrice: rec.upperPrice,
            lowerPrice: rec.lowerPrice,
            widthPercent: rec.widthPercent,
            entryPrice: rec.upperPrice,
            stopLossPrice: rec.lowerPrice,
            takeProfitPrice: rec.upperPrice,
            calculatedLeverage: 1,
            baseCandleIndex: 0,
            breakCandleIndex: 0,
            isMitigated: rec.status === "INVALIDATED",
            isInvalidated: rec.status === "INVALIDATED",
            isUsed: rec.isUsed,
            touchCount: rec.isUsed ? 1 : 0,
            source: "SMC",
            createdAt: new Date().toISOString(),
          };
          this.registry.set(rec.id, {
            ob: skeletonOB,
            state,
            symbol: rec.symbol,
            addedAt: new Date().toISOString(),
            stateChangedAt: new Date().toISOString(),
          });
        }
      }

      logger.info(`[OBRegistry] Loaded ${records.length} consumed/invalid OB IDs from DB`);
    } catch (err) {
      logger.warn({ err }, "[OBRegistry] Could not load from DB — proceeding with empty registry");
    }
  }

  // Helper: check if an OB ID is already consumed/invalid
  public isConsumed(obId: string): boolean {
    const entry = this.registry.get(obId);
    return !!entry && entry.state !== "ACTIVE";
  }

  // Clear (for backtesting only — never call in production)
  public clear(): void {
    this.registry.clear();
  }

  // Diagnostics
  public stats(): { total: number; active: number; used: number; invalid: number } {
    let active = 0; let used = 0; let invalid = 0;
    for (const e of this.registry.values()) {
      if (e.state === "ACTIVE") active++;
      else if (e.state === "USED") used++;
      else invalid++;
    }
    return { total: this.registry.size, active, used, invalid };
  }
}

// Singleton export
export const PersistentOBRegistry = new PersistentOBRegistryImpl();
