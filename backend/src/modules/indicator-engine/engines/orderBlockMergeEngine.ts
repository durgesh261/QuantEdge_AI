import { OrderBlockDto } from '@algoapp/shared';

// ============================================================================
// Order Block Merge Engine — Canonical Implementation
//
// Merges overlapping Order Blocks from two independent sources:
//   - LuxAlgo SMC engine  (source = 'SMC')
//   - UAlgo PAT engine    (source = 'PAT')
//
// Rules:
//   1. Only merge OBs of the same direction (both BULLISH or both BEARISH).
//   2. Two OBs overlap when their price ranges physically intersect:
//        overlap = max(lower1, lower2) < min(upper1, upper2)
//   3. Transitive overlap: A overlaps B, B overlaps C → all three merge (connected-components).
//   4. Merged OB: upper = max, lower = min, width/entry/SL/TP/leverage recalculated.
//   5. ID = deterministic: "OB-MERGED-{type}-{sorted source ids}" — same zone = same ID always.
// ============================================================================

export interface OrderBlockWithMeta extends OrderBlockDto {
  /** Original source before merging — 'PAT', 'SMC', or 'MERGED' */
  mergeSource: 'PAT' | 'SMC' | 'MERGED';
  /** IDs of OBs that were merged to produce this record (empty if not merged) */
  mergedFromIds: string[];
}

export class OrderBlockMergeEngine {

  public static merge(
    smcBlocks: OrderBlockDto[],
    patBlocks: OrderBlockDto[]
  ): {
    merged:    OrderBlockWithMeta[];
    analytics: OrderBlockWithMeta[];
  } {
    const tagged: OrderBlockWithMeta[] = [
      ...smcBlocks.map(ob => ({ ...ob, mergeSource: 'SMC' as const, mergedFromIds: [] })),
      ...patBlocks.map(ob => ({ ...ob, mergeSource: 'PAT' as const, mergedFromIds: [] })),
    ];

    const analytics: OrderBlockWithMeta[] = tagged.map(ob => ({ ...ob }));

    const bullishTagged = tagged.filter(ob => ob.type === 'BULLISH');
    const bearishTagged = tagged.filter(ob => ob.type === 'BEARISH');

    const mergedBullish = this.mergeGroup(bullishTagged);
    const mergedBearish = this.mergeGroup(bearishTagged);

    const merged = [...mergedBullish, ...mergedBearish]
      .sort((a, b) => a.breakCandleIndex - b.breakCandleIndex);

    return { merged, analytics };
  }

  // Connected-components transitive overlap merge for one direction
  private static mergeGroup(blocks: OrderBlockWithMeta[]): OrderBlockWithMeta[] {
    if (blocks.length === 0) return [];
    if (blocks.length === 1) return [{ ...blocks[0]!, mergedFromIds: [] }];

    const n = blocks.length;
    const parent = Array.from({ length: n }, (_, i) => i);

    const find = (i: number): number => {
      while (parent[i] !== i) {
        parent[i] = parent[parent[i]!]!;
        i = parent[i]!;
      }
      return i;
    };

    const union = (i: number, j: number) => {
      parent[find(i)] = find(j);
    };

    // Union any two blocks whose price ranges physically overlap
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = blocks[i]!;
        const b = blocks[j]!;
        const overlapLow  = Math.max(a.lowerPrice, b.lowerPrice);
        const overlapHigh = Math.min(a.upperPrice, b.upperPrice);
        if (overlapLow < overlapHigh) {
          union(i, j);
        }
      }
    }

    // Group by connected component
    const groups = new Map<number, OrderBlockWithMeta[]>();
    for (let i = 0; i < n; i++) {
      const root = find(i);
      if (!groups.has(root)) groups.set(root, []);
      groups.get(root)!.push(blocks[i]!);
    }

    const result: OrderBlockWithMeta[] = [];

    for (const group of groups.values()) {
      if (group.length === 1) {
        result.push({ ...group[0]!, mergedFromIds: [] });
        continue;
      }

      const upper = Math.max(...group.map(ob => ob.upperPrice));
      const lower = Math.min(...group.map(ob => ob.lowerPrice));
      const type  = group[0]!.type;

      // Deterministic stable ID from sorted source IDs
      const sortedIds = [...group.map(ob => ob.id)].sort();
      const mergedId  = `OB-MERGED-${type}-${sortedIds.join('_')}`;

      const rawWidth   = Math.max(0.0001, upper - lower);
      const widthPct   = Number(((rawWidth / Math.max(0.0001, upper)) * 100).toFixed(4));
      const entryPrice = this.calcEntryPrice(type, upper, lower);
      const stopLossPrice = type === 'BULLISH' ? lower : upper;
      const slDistPct  = Math.max(0.01, Math.abs(entryPrice - stopLossPrice) / entryPrice * 100);
      const leverage   = Math.min(100, Math.max(1, Math.round(35 / slDistPct)));
      const tpDist     = 60 / leverage;
      const takeProfitPrice = type === 'BULLISH'
        ? Number((entryPrice * (1 + tpDist / 100)).toFixed(4))
        : Number((entryPrice * (1 - tpDist / 100)).toFixed(4));

      const createdAt     = group.reduce((earliest, ob) =>
        ob.createdAt < earliest ? ob.createdAt : earliest, group[0]!.createdAt);
      const isMitigated   = group.some(ob => ob.isMitigated);
      const isInvalidated = group.some(ob => ob.isInvalidated);
      const isUsed        = group.some(ob => ob.isUsed);
      const touchCount    = Math.max(...group.map(ob => ob.touchCount ?? 0));
      const baseCandleIndex  = Math.min(...group.map(ob => ob.baseCandleIndex));
      const breakCandleIndex = Math.min(...group.map(ob => ob.breakCandleIndex));

      result.push({
        id:                 mergedId,
        symbol:             group[0]!.symbol,
        timeframe:          group[0]!.timeframe,
        type,
        upperPrice:         Number(upper.toFixed(4)),
        lowerPrice:         Number(lower.toFixed(4)),
        widthPercent:       widthPct,
        entryPrice:         Number(entryPrice.toFixed(4)),
        stopLossPrice:      Number(stopLossPrice.toFixed(4)),
        takeProfitPrice:    Number(takeProfitPrice.toFixed(4)),
        calculatedLeverage: leverage,
        baseCandleIndex,
        breakCandleIndex,
        isMitigated,
        isInvalidated,
        isUsed,
        touchCount,
        source:             'SMC',
        createdAt,
        mergeSource:        'MERGED',
        mergedFromIds:      sortedIds,
      });
    }

    return result;
  }

  // Entry price using 0.6% width rule
  private static calcEntryPrice(
    type:  'BULLISH' | 'BEARISH',
    upper: number,
    lower: number
  ): number {
    const rawWidth = Math.max(0.0001, upper - lower);
    const widthPct = (rawWidth / Math.max(0.0001, upper)) * 100;

    if (type === 'BULLISH') {
      if (widthPct <= 0.6) return Number(upper.toFixed(4));
      return Number((upper - 0.25 * rawWidth).toFixed(4));
    } else {
      if (widthPct <= 0.6) return Number(lower.toFixed(4));
      return Number((lower + 0.25 * rawWidth).toFixed(4));
    }
  }
}