import { OrderBlockDto } from '@algoapp/shared';
import { createHash } from 'crypto';

export interface MergedOrderBlock extends OrderBlockDto {
  sourceIds: string[];
  mergedZoneId: string;
  isMerged: boolean;
}

export interface MergeResult {
  merged: MergedOrderBlock[];
}

export class OrderBlockMergeEngine {
  /**
   * Merge overlapping Order Blocks into combined zones.
   * 
   * Rules:
   * - Demand (BULLISH) and Supply (BEARISH) are kept separate, never merged
   * - Within same direction: merge ANY overlapping OBs
   * - Sort by lowerPrice
   * - Merge ANY overlapping OBs (not just 40% threshold)
   * - Merged zone: upper = max(all.upperPrice), lower = min(all.lowerPrice)
   * - Deterministic ID based on source IDs hash
   * - Track sourceIds for audit trail
   */
  public static merge(demandOBs: OrderBlockDto[], supplyOBs: OrderBlockDto[]): MergeResult {
    // Combine all OBs, then separate by direction for merging
    const allOBs = [...demandOBs, ...supplyOBs];
    const bullish = allOBs.filter(ob => ob.type === 'BULLISH');
    const bearish = allOBs.filter(ob => ob.type === 'BEARISH');

    const mergedBullish = this.mergeDirection(bullish);
    const mergedBearish = this.mergeDirection(bearish);

    return { merged: [...mergedBullish, ...mergedBearish] };
  }

  private static mergeDirection(obs: OrderBlockDto[]): MergedOrderBlock[] {
    if (obs.length <= 1) {
      return obs.map(ob => ({
        ...ob,
        sourceIds: [ob.id],
        mergedZoneId: `MERGED-${ob.symbol}-${ob.type}-${this.hashId(ob.id)}`,
        isMerged: false,
      }));
    }

    // Sort by lowerPrice ascending
    const sorted = [...obs].sort((a, b) => a.lowerPrice - b.lowerPrice);
    const result: MergedOrderBlock[] = [];

    // Track the merged range of the current group
    let currentGroup: OrderBlockDto[] = [sorted[0]!];
    let groupUpper = sorted[0]!.upperPrice;
    let groupLower = sorted[0]!.lowerPrice;

    for (let i = 1; i < sorted.length; i++) {
      const ob = sorted[i]!;

      // Check if current OB overlaps with the current group's merged range
      // Overlap: ob.lowerPrice <= groupUpper
      if (ob.lowerPrice <= groupUpper) {
        // Overlaps - add to current group and expand range
        currentGroup.push(ob);
        groupUpper = Math.max(groupUpper, ob.upperPrice);
        groupLower = Math.min(groupLower, ob.lowerPrice);
      } else {
        // No overlap - finalize current group and start new
        result.push(this.finalizeGroup(currentGroup));
        currentGroup = [ob];
        groupUpper = ob.upperPrice;
        groupLower = ob.lowerPrice;
      }
    }

    // Don't forget the last group
    result.push(this.finalizeGroup(currentGroup));

    return result;
  }

  private static finalizeGroup(group: OrderBlockDto[]): MergedOrderBlock {
    if (group.length === 1) {
      const ob = group[0]!;
      return {
        ...ob,
        sourceIds: [ob.id],
        mergedZoneId: `MERGED-${ob.symbol}-${ob.type}-${this.hashId(ob.id)}`,
        isMerged: false,
      };
    }

    // Merge multiple OBs
    const upperPrice = Math.max(...group.map(ob => ob.upperPrice));
    const lowerPrice = Math.min(...group.map(ob => ob.lowerPrice));
    const sourceIds = group.map(ob => ob.id).sort();
    const firstOb = group[0]!;
    
    // Deterministic merged ID from sorted source IDs
    const mergedZoneId = `MERGED-${firstOb.symbol}-${firstOb.type}-${this.hashId(sourceIds.join('-'))}`;
    
    const width = upperPrice - lowerPrice;
    const widthPercent = Number(((width / Math.max(0.0001, upperPrice)) * 100).toFixed(3));

    // Use the earliest creation time from source OBs
    const createdAt = group.reduce((earliest, ob) => 
      new Date(ob.createdAt) < new Date(earliest) ? ob.createdAt : earliest, 
      group[0]!.createdAt
    );

    // Earliest base candle index
    const baseCandleIndex = Math.min(...group.map(ob => ob.baseCandleIndex));
    // Latest break candle index
    const breakCandleIndex = Math.max(...group.map(ob => ob.breakCandleIndex));

    // Calculate entry, SL, TP, leverage for merged OB using same logic as individual OB creation
    const isBullish = firstOb.type === 'BULLISH';
    const rawWidth = width;
    const widthPct = widthPercent;
    
    let entryPrice: number;
    let stopLossPrice: number;
    if (isBullish) {
      entryPrice = widthPct <= 0.6 ? upperPrice : upperPrice - 0.25 * rawWidth;
      stopLossPrice = lowerPrice;
    } else {
      entryPrice = widthPct <= 0.6 ? lowerPrice : lowerPrice + 0.25 * rawWidth;
      stopLossPrice = upperPrice;
    }
    
    const slDist = Math.max(0.01, Math.abs(entryPrice - stopLossPrice) / entryPrice * 100);
    const calculatedLeverage = Math.min(100, Math.max(1, Math.round(35 / slDist)));
    const takeProfitPrice = isBullish 
      ? entryPrice * (1 + 60 / calculatedLeverage / 100)
      : entryPrice * (1 - 60 / calculatedLeverage / 100);

    return {
      id: mergedZoneId,
      symbol: firstOb.symbol,
      timeframe: firstOb.timeframe,
      type: firstOb.type,
      upperPrice: Number(upperPrice.toFixed(4)),
      lowerPrice: Number(lowerPrice.toFixed(4)),
      widthPercent,
      entryPrice: Number(entryPrice.toFixed(4)),
      stopLossPrice: Number(stopLossPrice.toFixed(4)),
      takeProfitPrice: Number(takeProfitPrice.toFixed(4)),
      calculatedLeverage,
      baseCandleIndex,
      breakCandleIndex,
      isMitigated: false,
      isInvalidated: false,
      isUsed: false,
      touchCount: 0,
      source: 'SMC' as const,
      createdAt,
      sourceIds,
      mergedZoneId,
      isMerged: true,
    };
  }

  private static hashId(input: string): string {
    return createHash('sha256').update(input).digest('hex').slice(0, 16);
  }
}