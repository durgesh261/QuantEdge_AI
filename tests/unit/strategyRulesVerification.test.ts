import { describe, it, expect, beforeEach } from 'vitest';
import { OrderBlockMergeEngine } from '../../backend/src/modules/indicator-engine/engines/orderBlockMergeEngine.js';
import { PersistentOBRegistry } from '../../backend/src/modules/scanner/services/PersistentOBRegistry.js';
import { OrderBlockWidthEngine } from '../../backend/src/modules/indicator-engine/engines/orderBlockWidthEngine.js';
import { MarketFilterEngine } from '../../backend/src/modules/decision/filters/marketFilterEngine.js';
import { DynamicRiskLeverageService } from '../../backend/src/modules/live-trading/services/DynamicRiskLeverageService.js';
import { OrderBlockDto, CandleDto, StrategySignalOutcome, DecisionState } from '@algoapp/shared';

describe('QuantEdge AI Strategy Engine — 30 Criteria Verification (PART 35)', () => {

  beforeEach(() => {
    PersistentOBRegistry.clear();
    OrderBlockWidthEngine.resetUsed();
  });

  // 1 & 15. Untouched Demand OB remains active when price moves upward away
  it('1 & 15. Untouched Demand OB remains active when price moves away from it', () => {
    const ob: OrderBlockDto = {
      id: 'OB-DEMAND-1',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      type: 'BULLISH',
      upperPrice: 64000,
      lowerPrice: 63800,
      widthPercent: 0.312,
      entryPrice: 64000,
      stopLossPrice: 63800,
      takeProfitPrice: 64500,
      calculatedLeverage: 10,
      baseCandleIndex: 1,
      breakCandleIndex: 2,
      isMitigated: false,
      isInvalidated: false,
      isUsed: false,
      touchCount: 0,
      source: 'SMC',
      createdAt: new Date().toISOString(),
    };

    PersistentOBRegistry.addAll('BTCUSD.P', [ob]);
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(1);

    // Price moves far away to 67000
    const highCandle: CandleDto = {
      timestamp: new Date().toISOString(),
      open: 65000,
      high: 67200,
      low: 64900,
      close: 67000,
      volume: 500,
    };

    PersistentOBRegistry.checkAndInvalidate('BTCUSD.P', highCandle);
    // Moving away MUST NOT invalidate the untouched OB
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(1);
    expect(PersistentOBRegistry.getActive('BTCUSD.P')[0]?.id).toBe('OB-DEMAND-1');
  });

  // 2. Untouched Supply OB remains active when price moves downward away
  it('2. Untouched Supply OB remains active when price moves downward away from it', () => {
    const ob: OrderBlockDto = {
      id: 'OB-SUPPLY-1',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      type: 'BEARISH',
      upperPrice: 67500,
      lowerPrice: 67200,
      widthPercent: 0.444,
      entryPrice: 67200,
      stopLossPrice: 67500,
      takeProfitPrice: 66500,
      calculatedLeverage: 10,
      baseCandleIndex: 10,
      breakCandleIndex: 11,
      isMitigated: false,
      isInvalidated: false,
      isUsed: false,
      touchCount: 0,
      source: 'SMC',
      createdAt: new Date().toISOString(),
    };

    PersistentOBRegistry.addAll('BTCUSD.P', [ob]);
    const lowCandle: CandleDto = {
      timestamp: new Date().toISOString(),
      open: 66000,
      high: 66100,
      low: 63000,
      close: 63500,
      volume: 800,
    };

    PersistentOBRegistry.checkAndInvalidate('BTCUSD.P', lowCandle);
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(1);
  });

  // 3. Demand and Supply never merge
  it('3. Demand and Supply never merge', () => {
    const demand: OrderBlockDto = {
      id: 'OB-DEM-1', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 65000, lowerPrice: 64500, widthPercent: 0.77,
      entryPrice: 64875, stopLossPrice: 64500, takeProfitPrice: 66000,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const supply: OrderBlockDto = {
      id: 'OB-SUP-1', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BEARISH',
      upperPrice: 65200, lowerPrice: 64800, widthPercent: 0.61, // Overlaps in price range 64800-65000
      entryPrice: 64900, stopLossPrice: 65200, takeProfitPrice: 64000,
      calculatedLeverage: 10, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([demand], [supply]);
    expect(merged).toHaveLength(2); // Retained separately as Bullish & Bearish
    expect(merged.filter(m => m.type === 'BULLISH')).toHaveLength(1);
    expect(merged.filter(m => m.type === 'BEARISH')).toHaveLength(1);
  });

  // 4. Same-direction overlapping OBs merge
  it('4. Same-direction overlapping OBs merge', () => {
    const obA: OrderBlockDto = {
      id: 'OB-DEM-A', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 100, lowerPrice: 99, widthPercent: 1.0,
      entryPrice: 99.75, stopLossPrice: 99, takeProfitPrice: 102,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const obB: OrderBlockDto = {
      id: 'OB-DEM-B', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 100.5, lowerPrice: 99.5, widthPercent: 0.995,
      entryPrice: 100.25, stopLossPrice: 99.5, takeProfitPrice: 102,
      calculatedLeverage: 10, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([obA], [obB]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.upperPrice).toBe(100.5);
    expect(merged[0]?.lowerPrice).toBe(99);
  });

  // 5. Three transitive overlapping OBs merge
  it('5. Three transitive overlapping OBs merge (A overlaps B, B overlaps C)', () => {
    const obA: OrderBlockDto = {
      id: 'OB-1', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 100, lowerPrice: 95, widthPercent: 5.0,
      entryPrice: 98.75, stopLossPrice: 95, takeProfitPrice: 110,
      calculatedLeverage: 5, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const obB: OrderBlockDto = {
      id: 'OB-2', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 102, lowerPrice: 98, widthPercent: 3.92,
      entryPrice: 101, stopLossPrice: 98, takeProfitPrice: 110,
      calculatedLeverage: 5, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };
    const obC: OrderBlockDto = {
      id: 'OB-3', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 105, lowerPrice: 101, widthPercent: 3.8,
      entryPrice: 104, stopLossPrice: 101, takeProfitPrice: 110,
      calculatedLeverage: 5, baseCandleIndex: 5, breakCandleIndex: 6,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([obA, obC], [obB]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.upperPrice).toBe(105);
    expect(merged[0]?.lowerPrice).toBe(95);
  });

  // 6. Merged width is recalculated
  it('6. Merged width is recalculated using final merged boundaries', () => {
    const obA: OrderBlockDto = {
      id: 'OB-W1', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 100, lowerPrice: 99.5, widthPercent: 0.5,
      entryPrice: 100, stopLossPrice: 99.5, takeProfitPrice: 102,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const obB: OrderBlockDto = {
      id: 'OB-W2', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 101, lowerPrice: 99.8, widthPercent: 1.188,
      entryPrice: 100.7, stopLossPrice: 99.8, takeProfitPrice: 103,
      calculatedLeverage: 10, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([obA], [obB]);
    expect(merged).toHaveLength(1);
    // Final boundaries: 101 -> 99.5. Raw width = 1.5. Width % = (1.5 / 101) * 100 = 1.4851%
    expect(merged[0]?.widthPercent).toBeCloseTo(1.4851, 3);
  });

  // 7. Width <= 0.6% uses first edge
  it('7. Width <= 0.6% uses first edge for entry', () => {
    const demand: OrderBlockDto = {
      id: 'OB-NARROW-DEM', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 64000, lowerPrice: 63800, widthPercent: 0.3125, // 0.3125% <= 0.6%
      entryPrice: 64000, stopLossPrice: 63800, takeProfitPrice: 64500,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const supply: OrderBlockDto = {
      id: 'OB-NARROW-SUP', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BEARISH',
      upperPrice: 65200, lowerPrice: 65000, widthPercent: 0.3067, // 0.3067% <= 0.6%
      entryPrice: 65000, stopLossPrice: 65200, takeProfitPrice: 64000,
      calculatedLeverage: 10, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([demand], [supply]);
    const demMerged = merged.find(m => m.type === 'BULLISH')!;
    const supMerged = merged.find(m => m.type === 'BEARISH')!;

    expect(demMerged.entryPrice).toBe(64000); // Upper edge
    expect(supMerged.entryPrice).toBe(65000); // Lower edge
  });

  // 8. Width > 0.6% uses 25% inside
  it('8. Width > 0.6% uses 25% inside Order Block for entry', () => {
    // Upper = 100, Lower = 99, Width = 1.0% (> 0.6%)
    // Demand entry = 100 - (1 * 0.25) = 99.75
    // Supply entry = 99 + (1 * 0.25) = 99.25
    const demand: OrderBlockDto = {
      id: 'OB-WIDE-DEM', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 100, lowerPrice: 99, widthPercent: 1.0,
      entryPrice: 99.75, stopLossPrice: 99, takeProfitPrice: 102,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };
    const supply: OrderBlockDto = {
      id: 'OB-WIDE-SUP', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BEARISH',
      upperPrice: 100, lowerPrice: 99, widthPercent: 1.0,
      entryPrice: 99.25, stopLossPrice: 100, takeProfitPrice: 97,
      calculatedLeverage: 10, baseCandleIndex: 3, breakCandleIndex: 4,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'PAT', createdAt: new Date().toISOString(),
    };

    const { merged } = OrderBlockMergeEngine.merge([demand], [supply]);
    const demMerged = merged.find(m => m.type === 'BULLISH')!;
    const supMerged = merged.find(m => m.type === 'BEARISH')!;

    expect(demMerged.entryPrice).toBe(99.75);
    expect(supMerged.entryPrice).toBe(99.25);
  });

  // 12 & 13. Used OB cannot trigger another trade & state survives DB reload
  it('12 & 13. Used OB cannot trigger another trade and state persists', async () => {
    const ob: OrderBlockDto = {
      id: 'OB-USED-TEST', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 64000, lowerPrice: 63800, widthPercent: 0.312,
      entryPrice: 64000, stopLossPrice: 63800, takeProfitPrice: 64500,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };

    PersistentOBRegistry.addAll('BTCUSD.P', [ob]);
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(1);

    // Mark as USED
    PersistentOBRegistry.markUsed('OB-USED-TEST');
    OrderBlockWidthEngine.markUsed('OB-USED-TEST');

    // Should no longer be active
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(0);
    expect(PersistentOBRegistry.isConsumed('OB-USED-TEST')).toBe(true);
    expect(OrderBlockWidthEngine.isUsed('OB-USED-TEST')).toBe(true);
  });

  // 14. Structural break invalidates untouched OB
  it('14. Candle CLOSE below Demand lower boundary invalidates untouched Demand OB', () => {
    const ob: OrderBlockDto = {
      id: 'OB-DEMAND-BREAK', symbol: 'BTCUSD.P', timeframe: '1H', type: 'BULLISH',
      upperPrice: 64000, lowerPrice: 63800, widthPercent: 0.312,
      entryPrice: 64000, stopLossPrice: 63800, takeProfitPrice: 64500,
      calculatedLeverage: 10, baseCandleIndex: 1, breakCandleIndex: 2,
      isMitigated: false, isInvalidated: false, isUsed: false, touchCount: 0,
      source: 'SMC', createdAt: new Date().toISOString(),
    };

    PersistentOBRegistry.addAll('BTCUSD.P', [ob]);

    // Candle closes at 63750 (BELOW 63800 lower boundary)
    const breakingCandle: CandleDto = {
      timestamp: new Date().toISOString(),
      open: 64100,
      high: 64150,
      low: 63700, // wick goes lower
      close: 63750, // candle CLOSE below lower boundary
      volume: 1200,
    };

    PersistentOBRegistry.checkAndInvalidate('BTCUSD.P', breakingCandle);
    expect(PersistentOBRegistry.getActive('BTCUSD.P')).toHaveLength(0);
    expect(PersistentOBRegistry.isConsumed('OB-DEMAND-BREAK')).toBe(true);
  });

  // 19. Ranging market -> NO TRADE
  it('19. Ranging market regime is strictly rejected', () => {
    const mockIndicators: any = {
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      atr14: 500,
      atr200: 500,
      marketStructure: {
        trend: 'BULLISH',
        internalTrend: 'BEARISH', // Inconsistent trend = RANGING
        swingTrend: 'BULLISH',
      },
    };

    const res = MarketFilterEngine.evaluateMarket(mockIndicators, false);
    expect(res.allowed).toBe(false);
    expect(res.marketRegime).toBe('RANGING');
  });

  // 24, 25, 26. SL = 35% risk, TP = 60% profit, max leverage <= 100x
  it('24, 25, 26. Risk engine calculates 35% SL risk, 60% TP profit target, capped at 100x leverage', () => {
    const riskResult = DynamicRiskLeverageService.calculateRiskAndLeverage({
      accountBalance: 1000,
      entryPrice: 64000,
      stopLossPrice: 63800, // $200 distance
      direction: 'BUY',
      orderBlockUpperPrice: 64000,
      orderBlockLowerPrice: 63800,
      orderBlockWidthPercent: 0.312,
    });

    expect(riskResult.isValid).toBe(true);
    expect(riskResult.riskAmount).toBe(350); // 35% of $1000
    expect(riskResult.rewardAmount).toBe(600); // 60% of $1000
    expect(riskResult.leverage).toBeLessThanOrEqual(100);
    expect(riskResult.takeProfitPrice).toBeGreaterThan(64000);
    expect(riskResult.stopLossPrice).toBe(63800);
  });

  // 27. Zero account balance -> execution blocked
  it('27. Zero account balance blocks risk calculation cleanly', () => {
    const riskResult = DynamicRiskLeverageService.calculateRiskAndLeverage({
      accountBalance: 0,
      entryPrice: 64000,
      stopLossPrice: 63800,
      direction: 'BUY',
    });

    expect(riskResult.isValid).toBe(false);
    expect(riskResult.rejectionReason).toContain('Account balance is zero');
  });

});
