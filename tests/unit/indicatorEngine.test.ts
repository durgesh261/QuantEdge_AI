import { describe, it, expect } from 'vitest';
import { PivotEngine } from '../../backend/src/modules/indicator-engine/engines/pivotEngine';
import { SwingEngine } from '../../backend/src/modules/indicator-engine/engines/swingEngine';
import { MarketStructureEngine } from '../../backend/src/modules/indicator-engine/engines/marketStructureEngine';
import { PatZoneEngine } from '../../backend/src/modules/indicator-engine/engines/patZoneEngine';
import { LuxAlgoSMCEngine } from '../../backend/src/modules/indicator-engine/engines/LuxAlgoSMCEngine';
import { LiquiditySweepEngine } from '../../backend/src/modules/indicator-engine/engines/liquiditySweepEngine';
import { FvgEngine } from '../../backend/src/modules/indicator-engine/engines/fvgEngine';
import { EqhEqlEngine } from '../../backend/src/modules/indicator-engine/engines/eqhEqlEngine';
import { ZoneMergeEngine } from '../../backend/src/modules/indicator-engine/engines/zoneMergeEngine';
import { FreshnessEngine } from '../../backend/src/modules/indicator-engine/engines/freshnessEngine';
import { TouchEngine } from '../../backend/src/modules/indicator-engine/engines/touchEngine';
import { ZoneScoreEngine } from '../../backend/src/modules/indicator-engine/engines/zoneScoreEngine';
import { IndicatorEngineService } from '../../backend/src/modules/indicator-engine/services/indicatorEngine.service';
import { CandleDto, SupplyZone, DemandZone } from '@algoapp/shared';

describe('IndicatorEngine - Comprehensive Validation Test Suite (Module 7)', () => {
  // 60-bar synthetic deterministic trending dataset
  const generateMockCandles = (basePrice: number = 64000, count: number = 60): CandleDto[] => {
    return Array.from({ length: count }, (_, i) => {
      const cycle = Math.sin((i / 8) * Math.PI);
      const close = basePrice + cycle * 400 + i * 20;
      const open = close - 50 * (i % 2 === 0 ? 1 : -1);
      const high = Math.max(open, close) + 80;
      const low = Math.min(open, close) - 80;

      return {
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open,
        high,
        low,
        close,
        volume: 100 + i,
        timestamp: new Date(Date.now() - (count - i) * 3600 * 1000).toISOString(),
      };
    });
  };

  const mockCandles = generateMockCandles(64000, 60);

  it('1. PivotEngine - exact Pine Script ta.pivothigh & ta.pivotlow confirmation', () => {
    const pivots = PivotEngine.findPivots(mockCandles, 5, 5);
    expect(pivots.length).toBeGreaterThan(0);

    for (const p of pivots) {
      expect(['HIGH', 'LOW']).toContain(p.type);
      expect(p.confirmedAtIndex).toBe(p.index + 5);
      expect(p.confirmedAtIndex).toBeLessThanOrEqual(mockCandles.length - 1);
    }
  });

  it('2. SwingEngine - stateful alternating ZigZag legs and trend direction', () => {
    const pivots = PivotEngine.findPivots(mockCandles, 5, 5);
    const swings = SwingEngine.calculateSwings(pivots);

    expect(swings.legs.length).toBeGreaterThan(0);
    expect(['BULLISH', 'BEARISH']).toContain(swings.currentTrend);

    // Verify strictly alternating directions
    for (let i = 0; i < swings.legs.length - 1; i++) {
      expect(swings.legs[i]!.direction).not.toBe(swings.legs[i + 1]!.direction);
    }
  });

  it('3. MarketStructureEngine - bar-by-bar BOS and CHoCH structural transitions', () => {
    const pivotsInternal = PivotEngine.findPivots(mockCandles, 5, 5);
    const pivotsSwing = PivotEngine.findPivots(mockCandles, 15, 15);
    const result = MarketStructureEngine.evaluateStructure('BTCUSD.P', mockCandles, pivotsInternal, pivotsSwing, '1H');

    expect(result.marketStructure.symbol).toBe('BTCUSD.P');
    expect(['BULLISH', 'BEARISH']).toContain(result.marketStructure.trend);
    expect(Array.isArray(result.events)).toBe(true);

    for (const evt of result.events) {
      expect(['BOS', 'CHOCH']).toContain(evt.type);
      expect(['BULLISH', 'BEARISH']).toContain(evt.direction);
      expect(evt.brokenLevel).toBeGreaterThan(0);
    }
  });

  it('4. PatZoneEngine - extracts Price Action Toolkit Lite Order Blocks and S/D Zones', () => {
    const pivotsInternal = PivotEngine.findPivots(mockCandles, 5, 5);
    const pivotsSwing = PivotEngine.findPivots(mockCandles, 15, 15);
    const { events } = MarketStructureEngine.evaluateStructure('BTCUSD.P', mockCandles, pivotsInternal, pivotsSwing, '1H');

    const patResult = PatZoneEngine.extractPatZones('BTCUSD.P', mockCandles, events, '1H');
    expect(Array.isArray(patResult.supplyZones)).toBe(true);
    expect(Array.isArray(patResult.demandZones)).toBe(true);
    expect(Array.isArray(patResult.orderBlocks)).toBe(true);

    const atr = PatZoneEngine.calculateAtr(mockCandles, 14);
    expect(atr).toBeGreaterThan(0);
  });

  it('5. LuxAlgoSMCEngine - produces bounded supply-side (BEARISH) and demand-side (BULLISH) order blocks with positive ATR200', () => {
    const smcResult = LuxAlgoSMCEngine.run('BTCUSD.P', mockCandles, '1H');

    // Supply-side = BEARISH order blocks (old: supplyZones)
    const supplyBlocks = smcResult.orderBlocks.filter(ob => ob.type === 'BEARISH');
    // Demand-side = BULLISH order blocks (old: demandZones)
    const demandBlocks = smcResult.orderBlocks.filter(ob => ob.type === 'BULLISH');

    expect(supplyBlocks.length).toBeLessThanOrEqual(5);
    expect(demandBlocks.length).toBeLessThanOrEqual(5);

    // ATR200 is the last value of the Wilder RMA series — must be > 0
    expect(smcResult.atr200).toBeGreaterThan(0);
  });

  it('6. LiquiditySweepEngine - detects High and Low liquidity sweeps', () => {
    const pivotsInternal = PivotEngine.findPivots(mockCandles, 5, 5);
    const pivotsSwing = PivotEngine.findPivots(mockCandles, 15, 15);
    const sweeps = LiquiditySweepEngine.detectSweeps('BTCUSD.P', mockCandles, pivotsInternal, pivotsSwing, '1H');

    expect(Array.isArray(sweeps)).toBe(true);
    for (const s of sweeps) {
      expect(['HIGH_SWEEP', 'LOW_SWEEP']).toContain(s.sweepType);
      expect(s.wickRatio).toBeGreaterThanOrEqual(0);
    }
  });

  it('7. FvgEngine - detects 3-bar Fair Value Gaps and tracks fill status', () => {
    const fvgs = FvgEngine.detectFvgs('BTCUSD.P', mockCandles, '1H');
    expect(Array.isArray(fvgs)).toBe(true);

    for (const fvg of fvgs) {
      expect(['BULLISH', 'BEARISH']).toContain(fvg.type);
      expect(['OPEN', 'PARTIALLY_FILLED', 'FILLED']).toContain(fvg.status);
      expect(fvg.upperPrice).toBeGreaterThan(fvg.lowerPrice);
      expect(fvg.gapWidth).toBeGreaterThan(0);
    }
  });

  it('8. EqhEqlEngine - detects Equal Highs and Equal Lows within 0.1 * ATR tolerance', () => {
    const pivots = PivotEngine.findPivots(mockCandles, 5, 5);
    const eqResults = EqhEqlEngine.detectEqhEql('BTCUSD.P', mockCandles, pivots, '1H');

    expect(Array.isArray(eqResults)).toBe(true);
    for (const item of eqResults) {
      expect(['EQH', 'EQL']).toContain(item.type);
      expect(item.tolerance).toBeGreaterThan(0);
    }
  });

  it('9. ZoneMergeEngine - consolidates >=40% overlapping PAT and SMC zones', () => {
    const zoneA: SupplyZone = {
      id: 'ZON-1',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      type: 'SUPPLY',
      upperPrice: 65000,
      lowerPrice: 64500,
      patStrength: 80,
      smcStrength: 0,
      mergedStrength: 80,
      width: 500,
      freshness: 100,
      touchCount: 0,
      age: 2,
      confidence: 80,
      status: 'NEW',
      source: 'PAT',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    const zoneB: SupplyZone = {
      id: 'ZON-2',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      type: 'SUPPLY',
      upperPrice: 65200,
      lowerPrice: 64600,
      patStrength: 0,
      smcStrength: 85,
      mergedStrength: 85,
      width: 600,
      freshness: 100,
      touchCount: 0,
      age: 2,
      confidence: 85,
      status: 'NEW',
      source: 'SMC',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    const merged = ZoneMergeEngine.mergeZones([zoneA, zoneB]);
    expect(merged.length).toBe(1);
    expect(merged[0]!.source).toBe('MERGED');
    expect(merged[0]!.upperPrice).toBe(65200);
    expect(merged[0]!.lowerPrice).toBe(64500);
    expect(merged[0]!.mergedStrength).toBe(95);
  });

  it('10. FreshnessEngine & TouchEngine & ZoneScoreEngine - computes deterministic scoring', () => {
    const sampleZone: DemandZone = {
      id: 'DEM-1',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      type: 'DEMAND',
      upperPrice: 64000,
      lowerPrice: 63500,
      patStrength: 85,
      smcStrength: 85,
      mergedStrength: 95,
      width: 500,
      freshness: 100,
      touchCount: 0,
      age: 4,
      confidence: 90,
      status: 'NEW',
      source: 'MERGED',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    const freshness = FreshnessEngine.calculateFreshness(sampleZone);
    expect(freshness).toBeGreaterThan(0);
    expect(freshness).toBeLessThanOrEqual(100);

    const score = ZoneScoreEngine.calculateScore(sampleZone, {
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      trend: 'BULLISH',
      internalTrend: 'BULLISH',
      swingTrend: 'BULLISH',
      liquiditySwept: false,
    });

    expect(score.totalScore).toBeGreaterThanOrEqual(0);
    expect(score.totalScore).toBeLessThanOrEqual(100);
  });

  it('11. IndicatorEngineService - end-to-end evaluation across all 4 allowlist pairs on 15M and 1H', async () => {
    const service = new IndicatorEngineService();
    const pairs = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];

    for (const pair of pairs) {
      const base = pair.startsWith('BTC') ? 64000 : pair.startsWith('ETH') ? 3500 : pair.startsWith('SOL') ? 140 : 0.58;
      const testCandles15M = generateMockCandles(base, 80);
      const testCandles1H = generateMockCandles(base, 80);

      const out15M = await service.evaluateSymbol(pair, '15M', undefined, testCandles15M);
      expect(out15M.symbol).toBe(pair);
      expect(out15M.timeframe).toBe('15M');
      expect(out15M.pivotsInternal).toBeDefined();
      expect(out15M.zigzagLegs).toBeDefined();

      const out1H = await service.evaluateSymbol(pair, '1H', undefined, testCandles1H);
      expect(out1H.symbol).toBe(pair);
      expect(out1H.timeframe).toBe('1H');
      expect(out1H.marketStructure).toBeDefined();
      expect(out1H.atr14).toBeGreaterThan(0);
      expect(out1H.atr200).toBeGreaterThan(0);
    }
  });
});
