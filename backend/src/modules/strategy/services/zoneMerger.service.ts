import { BaseZone } from '@algoapp/shared';

interface MergeCandidate {
  upperPrice: number;
  lowerPrice: number;
  type: 'SUPPLY' | 'DEMAND';
  symbol: string;
  sources: string[];
  strengths: number[];
  timestamps: string[];
}

/**
 * Zone Merger Service
 *
 * Merges overlapping Supply/Demand zones from LuxAlgo + UAlgo engines.
 * If two zones of the SAME direction overlap at all in price, they merge into one.
 * This prevents duplicate DEMAND/SUPPLY boxes at the same price level.
 */
export class ZoneMergerService {
  // Any physical overlap = merge (strategy requirement: overlapping OBs = one zone)
  private static readonly OVERLAP_THRESHOLD = 0;

  public static detectAndMergeZones(zones: BaseZone[]): BaseZone[] {
    if (!zones || zones.length === 0) return [];

    // Group by symbol + type
    const grouped = new Map<string, BaseZone[]>();
    for (const z of zones) {
      const key = `${z.symbol}-${z.type}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(z);
    }

    const merged: BaseZone[] = [];

    for (const group of grouped.values()) {
      const mergedGroup = this.mergeOverlapping(group);
      merged.push(...mergedGroup);
    }

    // Sort by mergedStrength descending
    return merged.sort((a, b) => b.mergedStrength - a.mergedStrength);
  }

  private static mergeOverlapping(zones: BaseZone[]): BaseZone[] {
    if (zones.length <= 1) return zones;

    const candidates: MergeCandidate[] = zones.map(z => ({
      upperPrice: z.upperPrice,
      lowerPrice: z.lowerPrice,
      type: z.type,
      symbol: z.symbol,
      sources: [z.source],
      strengths: [z.mergedStrength],
      timestamps: [z.createdAt],
    }));

    const merged: BaseZone[] = [];
    const consumed = new Set<number>();
    const now = new Date().toISOString();

    for (let i = 0; i < candidates.length; i++) {
      if (consumed.has(i)) continue;

      let candidate = candidates[i]!;

      // Find all overlapping zones
      for (let j = i + 1; j < candidates.length; j++) {
        if (consumed.has(j)) continue;
        
        let candidateJ = candidates[j]!;

        const overlap = this.calculateOverlap(candidate, candidateJ);
        if (overlap >= this.OVERLAP_THRESHOLD) {
          // Merge: expand range to cover both, average strength, track both sources
          candidate.upperPrice = Math.max(candidate.upperPrice, candidateJ.upperPrice);
          candidate.lowerPrice = Math.min(candidate.lowerPrice, candidateJ.lowerPrice);
          candidate.strengths.push(...candidateJ.strengths);
          candidate.sources.push(...candidateJ.sources);
          candidate.timestamps.push(...candidateJ.timestamps);
          consumed.add(j);
        }
      }

      // Compute merged properties
      const avgStrength = candidate.strengths.reduce((a, b) => a + b, 0) / candidate.strengths.length;
      const width = Math.abs(candidate.upperPrice - candidate.lowerPrice);

      // Determine freshness: if any source says FRESH, result is FRESH
      const sourceZones = zones.filter(z => 
        z.symbol === candidate.symbol && 
        z.type === candidate.type &&
        this.calculateOverlap(candidate, {
          upperPrice: z.upperPrice,
          lowerPrice: z.lowerPrice,
          type: z.type,
          symbol: z.symbol,
          sources: [],
          strengths: [],
          timestamps: [],
        }) > 0.5
      );

      const isFresh = sourceZones.some(z => z.status === 'NEW');
      const touchCount = Math.max(...sourceZones.map(z => z.touchCount || 0));

      // Build deterministic ID from sorted source zone IDs
      const sourceIds = sourceZones.map(z => z.id).sort();
      const mergedId  = `ZON-MERGED-${candidate.symbol}-${candidate.type}-${sourceIds.join('_')}`;

      const mergedZone: BaseZone = {
        id: mergedId,
        symbol: candidate.symbol,
        type: candidate.type,
        timeframe: '1H',
        upperPrice: Number(candidate.upperPrice.toFixed(4)),
        lowerPrice: Number(candidate.lowerPrice.toFixed(4)),
        source: candidate.sources.includes('PAT') ? 'MERGED' : 'MERGED',
        patStrength: 0,
        smcStrength: 0,
        mergedStrength: Math.round(avgStrength),
        confidence: Math.round(avgStrength),
        age: 0,
        width: Number(width.toFixed(4)),
        freshness: isFresh ? 100 : Math.round(sourceZones.reduce((sum, z) => sum + (z.freshness || 0), 0) / sourceZones.length),
        touchCount,
        status: isFresh ? 'NEW' : 'ACTIVE',
        createdAt: candidate.timestamps[0] || now,
        updatedAt: now,
      };

      merged.push(mergedZone);
    }

    return merged;
  }

  private static calculateOverlap(a: MergeCandidate, b: MergeCandidate): number {
    const overlapLower = Math.max(a.lowerPrice, b.lowerPrice);
    const overlapUpper = Math.min(a.upperPrice, b.upperPrice);
    if (overlapUpper <= overlapLower) return 0;

    const overlapSize = overlapUpper - overlapLower;
    const sizeA = a.upperPrice - a.lowerPrice;
    const sizeB = b.upperPrice - b.lowerPrice;
    const minSize = Math.min(sizeA, sizeB);

    return minSize > 0 ? overlapSize / minSize : 0;
  }
}
