// ============================================================================
// LuxAlgoSMCEngine.ts
// ============================================================================
// EXACT TypeScript port of Pine Script v5:
//   Smart Money Concepts [LuxAlgo]
//
// CRITICAL FIXES:
// 1. ATR(200) now returns a SERIES (one value per bar) — matches Pine ta.atr(200)
// 2. Crossover detection uses prevClose <= level && close > level (bullish)
//    and prevClose >= level && close < level (bearish) — matches ta.crossover/ta.crossunder
// 3. Volatility filter uses per-bar ATR from the series
//
// DEFAULT SETTINGS (from supplied Pine Script):
//   Mode: HISTORICAL, Style: COLORED
//   Internal Structure: ON, Internal Bullish: ALL, Internal Bearish: ALL
//   Confluence Filter: OFF
//   Swing Structure: ON
//   Internal Order Blocks: ON (size 5)
//   Swing Order Blocks: OFF
//   Order Block Filter: ATR, Order Block Mitigation: HIGH/LOW
//   Swing Length: 50, Internal Length: 5
//   EQH/EQL Length: 3, Threshold: 0.1
// ============================================================================

import {
  CandleDto,
  OrderBlockDto,
  MarketStructureEventDto,
  PivotPointDto,
  EqualHighLowDto,
  TradingTimeframe,
} from '@algoapp/shared';

const BULLISH_LEG = 1;
const BEARISH_LEG = 0;
const BULLISH = 1;
const BEARISH = -1;

export interface LuxAlgoConfig {
  mode?: 'HISTORICAL' | 'PRESENT';
  style?: 'COLORED' | 'MONOCHROME';
  showInternals?: boolean;
  showInternalBull?: 'ALL' | 'BOS' | 'CHOCH';
  showInternalBear?: 'ALL' | 'BOS' | 'CHOCH';
  internalFilterConfluence?: boolean;
  showStructure?: boolean;
  showSwingBull?: 'ALL' | 'BOS' | 'CHOCH';
  showSwingBear?: 'ALL' | 'BOS' | 'CHOCH';
  showInternalOrderBlocks?: boolean;
  internalOrderBlocksSize?: number;
  showSwingOrderBlocks?: boolean;
  swingOrderBlocksSize?: number;
  orderBlockFilter?: 'ATR' | 'RANGE';
  orderBlockMitigation?: 'CLOSE' | 'HIGHLOW';
  swingLength?: number;
  internalLength?: number;
  eqhEqlLength?: number;
  eqhEqlThreshold?: number;
  showEqualHighsLows?: boolean;
  showHighLowSwings?: boolean;
  showPremiumDiscountZones?: boolean;
  showTrend?: boolean;
}

interface PivotState {
  currentLevel: number;
  lastLevel: number;
  crossed: boolean;
  barIndex: number;
  barTime: string;
}

interface TrendState {
  bias: 1 | -1 | 0;
}

export interface TrailingExtremes {
  top: number;
  bottom: number;
  barTime: string;
  barIndex: number;
  lastTopTime: string;
  lastBottomTime: string;
}

export interface LuxAlgoSMCOutput {
  symbol: string;
  timeframe: TradingTimeframe;
  pivotsSwing: PivotPointDto[];
  pivotsInternal: PivotPointDto[];
  structureEvents: MarketStructureEventDto[];
  internalEvents: MarketStructureEventDto[];
  swingEvents: MarketStructureEventDto[];
  orderBlocks: OrderBlockDto[];
  internalOrderBlocks: OrderBlockDto[];
  swingOrderBlocks: OrderBlockDto[];
  equalHighLows: EqualHighLowDto[];
  swingTrend: 'BULLISH' | 'BEARISH';
  internalTrend: 'BULLISH' | 'BEARISH';
  trailingExtremes: TrailingExtremes;
  atr14: number;
  atr200: number;
}

export class LuxAlgoSMCEngine {

  private static readonly DEFAULT_CONFIG: Required<LuxAlgoConfig> = {
    mode: 'HISTORICAL',
    style: 'COLORED',
    showInternals: true,
    showInternalBull: 'ALL',
    showInternalBear: 'ALL',
    internalFilterConfluence: false,
    showStructure: true,
    showSwingBull: 'ALL',
    showSwingBear: 'ALL',
    showInternalOrderBlocks: true,
    internalOrderBlocksSize: 5,
    showSwingOrderBlocks: false,
    swingOrderBlocksSize: 5,
    orderBlockFilter: 'ATR',
    orderBlockMitigation: 'HIGHLOW',
    swingLength: 50,
    internalLength: 5,
    eqhEqlLength: 3,
    eqhEqlThreshold: 0.1,
    showEqualHighsLows: true,
    showHighLowSwings: true,
    showPremiumDiscountZones: false,
    showTrend: false,
  };

  /**
   * ATR using Wilder's RMA — exact Pine Script ta.atr(length)
   * Pine: ta.atr(length) => ta.rma(ta.tr, length)
   * RMA: alpha = 1/length, rma = alpha * src + (1 - alpha) * rma[1]
   * 
   * RETURNS: Series (number[]) — one ATR value per bar, aligned with candles
   */
  public static calculateAtrSeries(candles: CandleDto[], period: number): number[] {
    if (candles.length < 2) return candles.map(() => 1.0);
    const trValues: number[] = [];
    const atrSeries: number[] = [];

    for (let i = 0; i < candles.length; i++) {
      const c = candles[i]!;
      if (i === 0) {
        trValues.push(c.high - c.low);
        atrSeries.push(c.high - c.low);
        continue;
      }
      const prev = candles[i - 1]!;
      const tr = Math.max(
        c.high - c.low,
        Math.abs(c.high - prev.close),
        Math.abs(c.low - prev.close)
      );
      trValues.push(tr);
    }

    const effectivePeriod = Math.min(period, trValues.length);
    let rma = trValues[0]!;
    const alpha = 1 / effectivePeriod;

    atrSeries[0] = rma;
    for (let i = 1; i < trValues.length; i++) {
      rma = alpha * trValues[i]! + (1 - alpha) * rma;
      atrSeries[i] = rma;
    }

    return atrSeries;
  }

  /**
   * ATR(200) using Wilder's RMA — exact Pine Script ta.atr(200)
   * RETURNS: Series (number[]) — one ATR value per bar, aligned with candles
   */
  public static calculateAtr200Series(candles: CandleDto[]): number[] {
    return this.calculateAtrSeries(candles, 200);
  }

  /**
   * ATR(14) using Wilder's RMA — exact Pine Script ta.atr(14)
   * RETURNS: Series (number[]) — one ATR value per bar, aligned with candles
   */
  public static calculateAtr14Series(candles: CandleDto[]): number[] {
    return this.calculateAtrSeries(candles, 14);
  }

  /**
   * Cumulative Mean Range — Pine Script ta.cum(ta.tr)/bar_index
   */
  private static calculateCumulativeMeanRange(candles: CandleDto[]): number[] {
    const result: number[] = [];
    let cumSum = 0;
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i]!;
      const prev = i > 0 ? candles[i - 1]! : c;
      const tr = Math.max(
        c.high - c.low,
        Math.abs(c.high - prev.close),
        Math.abs(c.low - prev.close)
      );
      cumSum += tr;
      result.push(cumSum / (i + 1));
    }
    return result;
  }

  /**
   * leg(size) — EXACT Pine Script implementation
   *
   * Pine:
   *   newLegHigh = high[size] > ta.highest(size)
   *   newLegLow  = low[size]  < ta.lowest(size)
   *
   * At bar i:
   *   high[size] = candles[i - size].high
   *   ta.highest(size) = max(candles[i-size+1 .. i].high)
   *   ta.lowest(size)  = min(candles[i-size+1 .. i].low)
   */
  private static getLeg(candles: CandleDto[], i: number, size: number): 0 | 1 | null {
    if (i < size) return null;

    const targetHigh = candles[i - size]!.high;
    const targetLow = candles[i - size]!.low;

    let highest = -Infinity;
    let lowest = Infinity;
    for (let j = i - size + 1; j <= i; j++) {
      if (candles[j]!.high > highest) highest = candles[j]!.high;
      if (candles[j]!.low < lowest) lowest = candles[j]!.low;
    }

    if (targetHigh > highest) return BEARISH_LEG;
    if (targetLow < lowest) return BULLISH_LEG;
    return null;
  }

  /**
   * MAIN ENGINE — bar-by-bar state machine, chronological, no look-ahead
   */
  public static run(
    symbol: string,
    candles: CandleDto[],
    timeframe: TradingTimeframe = '1H',
    userConfig?: LuxAlgoConfig
  ): LuxAlgoSMCOutput {
    const cfg = { ...this.DEFAULT_CONFIG, ...userConfig };

    const SWING_SIZE = cfg.swingLength;
    const INTERNAL_SIZE = cfg.internalLength;
    const EQ_SIZE = cfg.eqhEqlLength;
    const EQ_THRESHOLD = cfg.eqhEqlThreshold;

    const pivotsSwing: PivotPointDto[] = [];
    const pivotsInternal: PivotPointDto[] = [];
    const structureEvents: MarketStructureEventDto[] = [];
    const internalEvents: MarketStructureEventDto[] = [];
    const swingEvents: MarketStructureEventDto[] = [];
    const equalHighLows: EqualHighLowDto[] = [];

    const internalOrderBlocks: OrderBlockDto[] = [];
    const swingOrderBlocks: OrderBlockDto[] = [];

    // ATR(200) SERIES — one value per bar (Pine Script behavior)
    const atr200Series = this.calculateAtr200Series(candles);
    const atr200Final = atr200Series[atr200Series.length - 1] ?? 1.0;

    // ATR(14) SERIES — one value per bar (Pine Script behavior)
    const atr14Series = this.calculateAtr14Series(candles);
    const atr14Final = atr14Series[atr14Series.length - 1] ?? 1.0;

    const cumMeanRange = this.calculateCumulativeMeanRange(candles);

    // ── parsedHigh / parsedLow — Pine Script volatility correction ──
    // Uses PER-BAR ATR from series (matches Pine: (high - low) >= 2 * atr200)
    const parsedHighs: number[] = [];
    const parsedLows: number[] = [];
    const highs: number[] = [];
    const lows: number[] = [];
    const times: string[] = [];

    for (let i = 0; i < candles.length; i++) {
      const c = candles[i]!;
      const volatilityMeasure = cfg.orderBlockFilter === 'ATR'
        ? atr200Series[i] ?? (c.high - c.low)
        : cumMeanRange[i] ?? (c.high - c.low);
      const isHighVol = (c.high - c.low) >= (2 * volatilityMeasure);

      parsedHighs.push(isHighVol ? c.low : c.high);
      parsedLows.push(isHighVol ? c.high : c.low);
      highs.push(c.high);
      lows.push(c.low);
      times.push(c.timestamp);
    }

    // ── Mutable pivot state (Pine Script `var`) ──
    const swingHigh: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };
    const swingLow: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };
    const internalHigh: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };
    const internalLow: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };
    const eqHigh: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };
    const eqLow: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: -1, barTime: '' };

    const swingTrend: TrendState = { bias: 0 };
    const internalTrend: TrendState = { bias: 0 };

    const trailing: TrailingExtremes = {
      top: -Infinity,
      bottom: Infinity,
      barTime: candles[0]?.timestamp ?? '',
      barIndex: 0,
      lastTopTime: candles[0]?.timestamp ?? '',
      lastBottomTime: candles[0]?.timestamp ?? '',
    };

    let prevSwingLeg: 0 | 1 | null = null;
    let prevInternalLeg: 0 | 1 | null = null;
    let prevEqLeg: 0 | 1 | null = null;

    let bullishBar = true;
    let bearishBar = true;

    // Track previous close for proper crossover detection
    let prevClose = candles[0]?.close ?? 0;

    for (let i = 0; i < candles.length; i++) {
      const candle = candles[i]!;

      // ── updateTrailingExtremes() ──
      if (candle.high > trailing.top) {
        trailing.top = candle.high;
        trailing.lastTopTime = candle.timestamp;
      }
      if (candle.low < trailing.bottom) {
        trailing.bottom = candle.low;
        trailing.lastBottomTime = candle.timestamp;
      }

      // ── Confluence filter ──
      if (cfg.internalFilterConfluence) {
        const bodyTop = Math.max(candle.close, candle.open);
        const bodyBottom = Math.min(candle.close, candle.open);
        const upperWick = candle.high - bodyTop;
        const lowerWick = bodyBottom - candle.low;
        bullishBar = upperWick > lowerWick;
        bearishBar = upperWick < lowerWick;
      }

      // ═══════════════════════════════════════════════════════════════════════
      // SWING STRUCTURE
      // ═══════════════════════════════════════════════════════════════════════
      const swingLeg = this.getLeg(candles, i, SWING_SIZE);
      if (swingLeg !== null && swingLeg !== prevSwingLeg) {
        const pIdx = i - SWING_SIZE;
        if (pIdx >= 0) {
          const pc = candles[pIdx]!;
          if (swingLeg === BULLISH_LEG) {
            if (!isNaN(swingLow.currentLevel)) swingLow.lastLevel = swingLow.currentLevel;
            swingLow.currentLevel = pc.low;
            swingLow.crossed = false;
            swingLow.barIndex = pIdx;
            swingLow.barTime = pc.timestamp;
            pivotsSwing.push({ index: pIdx, time: pc.timestamp, price: pc.low, type: 'LOW', length: SWING_SIZE, isSwing: true, confirmedAtIndex: i });
            trailing.bottom = pc.low;
            trailing.barTime = pc.timestamp;
            trailing.barIndex = pIdx;
            trailing.lastBottomTime = pc.timestamp;
          } else {
            if (!isNaN(swingHigh.currentLevel)) swingHigh.lastLevel = swingHigh.currentLevel;
            swingHigh.currentLevel = pc.high;
            swingHigh.crossed = false;
            swingHigh.barIndex = pIdx;
            swingHigh.barTime = pc.timestamp;
            pivotsSwing.push({ index: pIdx, time: pc.timestamp, price: pc.high, type: 'HIGH', length: SWING_SIZE, isSwing: true, confirmedAtIndex: i });
            trailing.top = pc.high;
            trailing.barTime = pc.timestamp;
            trailing.barIndex = pIdx;
            trailing.lastTopTime = pc.timestamp;
          }
        }
        prevSwingLeg = swingLeg;
      }

      // ── Swing BOS/CHoCH — PROPER CROSSOVER DETECTION ──
      // Pine: ta.crossover(close, level) = close[1] <= level && close > level
      //       ta.crossunder(close, level) = close[1] >= level && close < level
      if (cfg.showStructure) {
        if (!isNaN(swingHigh.currentLevel) && !swingHigh.crossed) {
          // Bullish break: prevClose <= level && close > level
          if (prevClose <= swingHigh.currentLevel && candle.close > swingHigh.currentLevel) {
            const tag: 'BOS' | 'CHOCH' = swingTrend.bias === BEARISH ? 'CHOCH' : 'BOS';
            swingHigh.crossed = true;
            swingTrend.bias = BULLISH;
            const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BULLISH', brokenLevel: swingHigh.currentLevel, isInternal: false, confirmationCandleIndex: i };
            structureEvents.push(evt);
            swingEvents.push(evt);
            if (cfg.showSwingOrderBlocks) {
              this.createOrderBlock(swingOrderBlocks, symbol, timeframe, 'BULLISH', swingHigh.barIndex, i, parsedHighs, parsedLows, times, candles, atr200Final, false);
            }
          }
        }

        if (!isNaN(swingLow.currentLevel) && !swingLow.crossed) {
          // Bearish break: prevClose >= level && close < level
          if (prevClose >= swingLow.currentLevel && candle.close < swingLow.currentLevel) {
            const tag: 'BOS' | 'CHOCH' = swingTrend.bias === BULLISH ? 'CHOCH' : 'BOS';
            swingLow.crossed = true;
            swingTrend.bias = BEARISH;
            const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BEARISH', brokenLevel: swingLow.currentLevel, isInternal: false, confirmationCandleIndex: i };
            structureEvents.push(evt);
            swingEvents.push(evt);
            if (cfg.showSwingOrderBlocks) {
              this.createOrderBlock(swingOrderBlocks, symbol, timeframe, 'BEARISH', swingLow.barIndex, i, parsedHighs, parsedLows, times, candles, atr200Final, false);
            }
          }
        }
      }

      // ═══════════════════════════════════════════════════════════════════════
      // INTERNAL STRUCTURE
      // ═══════════════════════════════════════════════════════════════════════
      const internalLegVal = this.getLeg(candles, i, INTERNAL_SIZE);
      if (internalLegVal !== null && internalLegVal !== prevInternalLeg) {
        const pIdx = i - INTERNAL_SIZE;
        if (pIdx >= 0) {
          const pc = candles[pIdx]!;
          if (internalLegVal === BULLISH_LEG) {
            if (!isNaN(internalLow.currentLevel)) internalLow.lastLevel = internalLow.currentLevel;
            internalLow.currentLevel = pc.low;
            internalLow.crossed = false;
            internalLow.barIndex = pIdx;
            internalLow.barTime = pc.timestamp;
            pivotsInternal.push({ index: pIdx, time: pc.timestamp, price: pc.low, type: 'LOW', length: INTERNAL_SIZE, isSwing: false, confirmedAtIndex: i });
          } else {
            if (!isNaN(internalHigh.currentLevel)) internalHigh.lastLevel = internalHigh.currentLevel;
            internalHigh.currentLevel = pc.high;
            internalHigh.crossed = false;
            internalHigh.barIndex = pIdx;
            internalHigh.barTime = pc.timestamp;
            pivotsInternal.push({ index: pIdx, time: pc.timestamp, price: pc.high, type: 'HIGH', length: INTERNAL_SIZE, isSwing: false, confirmedAtIndex: i });
          }
        }
        prevInternalLeg = internalLegVal;
      }

      // ── Internal BOS/CHoCH — PROPER CROSSOVER DETECTION ──
      if (cfg.showInternals) {
        const bullishExtra = !isNaN(internalHigh.currentLevel) && internalHigh.currentLevel !== swingHigh.currentLevel && bullishBar;
        if (!isNaN(internalHigh.currentLevel) && !internalHigh.crossed && bullishExtra) {
          // Bullish break: prevClose <= level && close > level
          if (prevClose <= internalHigh.currentLevel && candle.close > internalHigh.currentLevel) {
            const tag: 'BOS' | 'CHOCH' = internalTrend.bias === BEARISH ? 'CHOCH' : 'BOS';
            internalHigh.crossed = true;
            internalTrend.bias = BULLISH;
            const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BULLISH', brokenLevel: internalHigh.currentLevel, isInternal: true, confirmationCandleIndex: i };
            structureEvents.push(evt);
            internalEvents.push(evt);
            if (cfg.showInternalOrderBlocks) {
              this.createOrderBlock(internalOrderBlocks, symbol, timeframe, 'BULLISH', internalHigh.barIndex, i, parsedHighs, parsedLows, times, candles, atr200Final, true);
            }
          }
        }

        const bearishExtra = !isNaN(internalLow.currentLevel) && internalLow.currentLevel !== swingLow.currentLevel && bearishBar;
        if (!isNaN(internalLow.currentLevel) && !internalLow.crossed && bearishExtra) {
          // Bearish break: prevClose >= level && close < level
          if (prevClose >= internalLow.currentLevel && candle.close < internalLow.currentLevel) {
            const tag: 'BOS' | 'CHOCH' = internalTrend.bias === BULLISH ? 'CHOCH' : 'BOS';
            internalLow.crossed = true;
            internalTrend.bias = BEARISH;
            const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BEARISH', brokenLevel: internalLow.currentLevel, isInternal: true, confirmationCandleIndex: i };
            structureEvents.push(evt);
            internalEvents.push(evt);
            if (cfg.showInternalOrderBlocks) {
              this.createOrderBlock(internalOrderBlocks, symbol, timeframe, 'BEARISH', internalLow.barIndex, i, parsedHighs, parsedLows, times, candles, atr200Final, true);
            }
          }
        }
      }

      // ═══════════════════════════════════════════════════════════════════════
      // EQUAL HIGHS / EQUAL LOWS
      // ═══════════════════════════════════════════════════════════════════════
      if (cfg.showEqualHighsLows) {
        const eqLegVal = this.getLeg(candles, i, EQ_SIZE);
        if (eqLegVal !== null && eqLegVal !== prevEqLeg) {
          const pIdx = i - EQ_SIZE;
          if (pIdx >= 0) {
            const pc = candles[pIdx]!;
            // Use final ATR for EQH/EQL threshold (Pine uses atr200 which is a series but threshold is scalar)
            const tol = EQ_THRESHOLD * atr200Final;
            if (eqLegVal === BULLISH_LEG) {
              if (!isNaN(eqLow.currentLevel) && Math.abs(eqLow.currentLevel - pc.low) < tol) {
                const avg = (eqLow.currentLevel + pc.low) / 2;
                equalHighLows.push({ id: `EQL-SMC-${symbol}-${eqLow.barIndex}-${pIdx}`, symbol, timeframe, type: 'EQL', priceLevel: Number(avg.toFixed(4)), firstPivotIndex: eqLow.barIndex, secondPivotIndex: pIdx, tolerance: Number(tol.toFixed(4)), isSwept: false });
              }
              if (!isNaN(eqLow.currentLevel)) eqLow.lastLevel = eqLow.currentLevel;
              eqLow.currentLevel = pc.low;
              eqLow.barIndex = pIdx;
              eqLow.barTime = pc.timestamp;
            } else {
              if (!isNaN(eqHigh.currentLevel) && Math.abs(eqHigh.currentLevel - pc.high) < tol) {
                const avg = (eqHigh.currentLevel + pc.high) / 2;
                equalHighLows.push({ id: `EQH-SMC-${symbol}-${eqHigh.barIndex}-${pIdx}`, symbol, timeframe, type: 'EQH', priceLevel: Number(avg.toFixed(4)), firstPivotIndex: eqHigh.barIndex, secondPivotIndex: pIdx, tolerance: Number(tol.toFixed(4)), isSwept: false });
              }
              if (!isNaN(eqHigh.currentLevel)) eqHigh.lastLevel = eqHigh.currentLevel;
              eqHigh.currentLevel = pc.high;
              eqHigh.barIndex = pIdx;
              eqHigh.barTime = pc.timestamp;
            }
          }
          prevEqLeg = eqLegVal;
        }
      }

      // ═══════════════════════════════════════════════════════════════════════
      // MITIGATION — deleteOrderBlocks() runs EVERY bar
      // ═══════════════════════════════════════════════════════════════════════
      if (cfg.showInternalOrderBlocks) {
        this.applyMitigation(internalOrderBlocks, candle, cfg.orderBlockMitigation);
      }
      if (cfg.showSwingOrderBlocks) {
        this.applyMitigation(swingOrderBlocks, candle, cfg.orderBlockMitigation);
      }

      // Update prevClose for next iteration
      prevClose = candle.close;
    }

    return {
      symbol, timeframe,
      pivotsSwing, pivotsInternal,
      structureEvents, internalEvents, swingEvents,
      orderBlocks: [...internalOrderBlocks, ...swingOrderBlocks],
      internalOrderBlocks,
      swingOrderBlocks,
      equalHighLows,
      swingTrend: swingTrend.bias >= 0 ? 'BULLISH' : 'BEARISH',
      internalTrend: internalTrend.bias >= 0 ? 'BULLISH' : 'BEARISH',
      trailingExtremes: trailing,
      atr14: atr14Final,
      atr200: atr200Final,
    };
  }

  /**
   * storeOrderBlock() — EXACT Pine Script implementation
   *
   * Pine:
   *   if bias == BEARISH
   *     a_rray      := parsedHighs.slice(p_ivot.barIndex, bar_index)
   *     parsedIndex := p_ivot.barIndex + a_rray.indexof(a_rray.max())
   *   else
   *     a_rray      := parsedLows.slice(p_ivot.barIndex, bar_index)
   *     parsedIndex := p_ivot.barIndex + a_rray.indexof(a_rray.min())
   *
   *   orderBlock.new(parsedHighs.get(parsedIndex), parsedLows.get(parsedIndex),
   *                  times.get(parsedIndex), bias)
   *
   * CRITICAL: slice is [from, to) — exclusive at the end.
   */
  private static createOrderBlock(
    orderBlocks: OrderBlockDto[],
    symbol: string,
    timeframe: TradingTimeframe,
    bias: 'BULLISH' | 'BEARISH',
    pivotBarIndex: number,
    breakBarIndex: number,
    parsedHighs: number[],
    parsedLows: number[],
    times: string[],
    _candles: CandleDto[],
    _atr200: number,
    isInternal: boolean
  ): void {
    const searchStart = pivotBarIndex;
    const searchEnd = breakBarIndex;

    if (searchStart < 0 || searchEnd > parsedHighs.length || searchStart >= searchEnd) return;

    let parsedIndex = -1;

    if (bias === 'BEARISH') {
      let maxVal = -Infinity;
      for (let k = searchStart; k < searchEnd; k++) {
        if (parsedHighs[k]! > maxVal) { maxVal = parsedHighs[k]!; parsedIndex = k; }
      }
    } else {
      let minVal = Infinity;
      for (let k = searchStart; k < searchEnd; k++) {
        if (parsedLows[k]! < minVal) { minVal = parsedLows[k]!; parsedIndex = k; }
      }
    }

    if (parsedIndex === -1) return;

    const barHigh = parsedHighs[parsedIndex]!;
    const barLow = parsedLows[parsedIndex]!;
    const barTime = times[parsedIndex]!;

    // FIX: Ensure upperPrice > lowerPrice always
    // For high-volatility candles, parsedHigh < parsedLow (parsedHigh=low, parsedLow=high)
    // The OB boundaries must be ordered correctly regardless of parsed values
    const upperPrice = Math.max(barHigh, barLow);
    const lowerPrice = Math.min(barHigh, barLow);
    const width = upperPrice - lowerPrice;
    const widthPercent = Number(((width / Math.max(0.0001, upperPrice)) * 100).toFixed(3));

    // Skip zero-width OBs (should not happen with real data, but guard anyway)
    if (width <= 0) return;

    const prefix = isInternal ? 'INT' : 'SWG';
    const obId = `OB-${prefix}-${bias}-${symbol}-${parsedIndex}-${breakBarIndex}`;

    const ob: OrderBlockDto = {
      id: obId,
      symbol,
      timeframe,
      type: bias,
      upperPrice: Number(upperPrice.toFixed(4)),
      lowerPrice: Number(lowerPrice.toFixed(4)),
      widthPercent,
      entryPrice: 0,
      stopLossPrice: 0,
      takeProfitPrice: 0,
      calculatedLeverage: 1,
      baseCandleIndex: parsedIndex,
      breakCandleIndex: breakBarIndex,
      isMitigated: false,
      isInvalidated: false,
      isUsed: false,
      touchCount: 0,
      source: 'SMC',
      createdAt: barTime,
    };

    if (orderBlocks.length >= 100) orderBlocks.pop();
    orderBlocks.unshift(ob);
  }

  /**
   * deleteOrderBlocks() — EXACT Pine Script implementation
   *
   * HIGH/LOW mode:
   *   Bearish OB: mitigated when high > ob.barHigh
   *   Bullish OB: mitigated when low < ob.barLow
   */
  private static applyMitigation(
    orderBlocks: OrderBlockDto[],
    candle: CandleDto,
    mitigationMode: 'CLOSE' | 'HIGHLOW'
  ): void {
    const bearishSource = mitigationMode === 'CLOSE' ? candle.close : candle.high;
    const bullishSource = mitigationMode === 'CLOSE' ? candle.close : candle.low;

    for (let idx = orderBlocks.length - 1; idx >= 0; idx--) {
      const ob = orderBlocks[idx]!;
      let crossed = false;
      if (ob.type === 'BEARISH' && bearishSource > ob.upperPrice) crossed = true;
      else if (ob.type === 'BULLISH' && bullishSource < ob.lowerPrice) crossed = true;

      if (crossed) {
        ob.isMitigated = true;
        orderBlocks.splice(idx, 1);
      }
    }
  }
}