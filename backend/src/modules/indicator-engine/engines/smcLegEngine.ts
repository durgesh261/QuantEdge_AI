import {
  CandleDto,
  MarketStructureEventDto,
  OrderBlockDto,
  PivotPointDto,
  TradingTimeframe,
  EqualHighLowDto,
} from '@algoapp/shared';
import { OrderBlockWidthEngine } from './orderBlockWidthEngine.js';

// ============================================================================
// LuxAlgo SMC — Leg-based Pivot State Machine
// Direct TypeScript port of Pine Script:
//   leg(size), getCurrentStructure(), displayStructure(),
//   storeOrderBlock(), EQH/EQL detection
//
// Pine Script defaults used:
//   swingSize = 50, internalSize = 5, eqhEqlSize = 3, threshold = 0.1
// ============================================================================

const BULLISH_LEG = 1;
const BEARISH_LEG = 0;

export const SMC_SWING_SIZE = 50;
export const SMC_INTERNAL_SIZE = 5;
export const SMC_EQH_EQL_SIZE = 3;
export const SMC_EQH_EQL_THRESHOLD = 0.1; // × ATR(200)

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
  lastTopTime: string;
  lastBottomTime: string;
  lastTopIndex: number;
  lastBottomIndex: number;
}

export interface SmcLegOutput {
  pivotsSwing: PivotPointDto[];
  pivotsInternal: PivotPointDto[];
  structureEvents: MarketStructureEventDto[];
  internalEvents: MarketStructureEventDto[];
  swingEvents: MarketStructureEventDto[];
  orderBlocks: OrderBlockDto[];
  equalHighLows: EqualHighLowDto[];
  swingTrend: 'BULLISH' | 'BEARISH';
  internalTrend: 'BULLISH' | 'BEARISH';
  trailingExtremes: TrailingExtremes;
  atr200: number;
}

export class SmcLegEngine {

  // ============================================================
  // ATR(200) — Pine Script ta.atr(200) exact match
  // ============================================================
  public static calculateAtr200(candles: CandleDto[]): number {
    if (candles.length < 2) return 1.0;
    const period = Math.min(200, candles.length - 1);
    let sum = 0;
    for (let i = candles.length - period; i < candles.length; i++) {
      const c = candles[i]!;
      const prev = candles[i - 1]!;
      const tr = Math.max(
        c.high - c.low,
        Math.abs(c.high - prev.close),
        Math.abs(c.low - prev.close)
      );
      sum += tr;
    }
    return sum / period;
  }

  // ============================================================
  // leg(size) — Pine Script exact implementation
  //
  // Pine Script:
  //   leg(size):
  //     newLegHigh = high[size] > ta.highest(size)
  //     newLegLow  = low[size]  < ta.lowest(size)
  //     if newLegHigh → leg = BEARISH_LEG
  //     if newLegLow  → leg = BULLISH_LEG
  //
  // At current bar i:
  //   high[size]     = candles[i - size].high
  //   ta.highest(size) = max(candles[i-size+1 .. i].high)   (size bars, NOT the target)
  //   ta.lowest(size)  = min(candles[i-size+1 .. i].low)
  // ============================================================
  private static getLeg(candles: CandleDto[], i: number, size: number): 0 | 1 | null {
    if (i < size) return null;

    const targetHigh = candles[i - size]!.high;
    const targetLow  = candles[i - size]!.low;

    // highest/lowest of last `size` bars (candles[i-size+1 .. i])
    let highest = -Infinity;
    let lowest  = Infinity;
    for (let j = i - size + 1; j <= i; j++) {
      if (candles[j]!.high > highest) highest = candles[j]!.high;
      if (candles[j]!.low  < lowest)  lowest  = candles[j]!.low;
    }

    if (targetHigh > highest) return BEARISH_LEG;
    if (targetLow  < lowest)  return BULLISH_LEG;
    return null; // no leg change this bar
  }

  // ============================================================
  // Main engine — bar-by-bar state machine
  // ============================================================
  public static run(
    symbol:    string,
    candles:   CandleDto[],
    timeframe: TradingTimeframe = '1H',
    config?: { swingLen?: number; internalLen?: number }
  ): SmcLegOutput {
    // Q1: read configurable lengths from strategy profile (SQLite), fall back to Pine Script defaults
    const SWING_SIZE    = config?.swingLen    ?? SMC_SWING_SIZE;    // default 50
    const INTERNAL_SIZE = config?.internalLen ?? SMC_INTERNAL_SIZE; // default 5
    const pivotsSwing: PivotPointDto[]          = [];
    const pivotsInternal: PivotPointDto[]        = [];
    const structureEvents: MarketStructureEventDto[] = [];
    const internalEvents: MarketStructureEventDto[]  = [];
    const swingEvents: MarketStructureEventDto[]     = [];
    const orderBlocks: OrderBlockDto[]           = [];
    const equalHighLows: EqualHighLowDto[]       = [];

    const atr200 = this.calculateAtr200(candles);

    // parsedHigh/parsedLow — Pine Script volatility correction
    // highVolatilityBar = (high - low) >= 2 * atr(200)
    // parsedHigh = volatility ? low : high
    // parsedLow  = volatility ? high : low
    const parsedHighs: number[] = [];
    const parsedLows:  number[] = [];
    for (const c of candles) {
      const vol = c.high - c.low >= 2 * atr200;
      parsedHighs.push(vol ? c.low  : c.high);
      parsedLows.push( vol ? c.high : c.low);
    }

    // Mutable pivot state objects (Pine Script `var`)
    const swingHigh: PivotState    = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };
    const swingLow: PivotState     = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };
    const internalHigh: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };
    const internalLow: PivotState  = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };
    const eqHigh: PivotState = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };
    const eqLow: PivotState  = { currentLevel: NaN, lastLevel: NaN, crossed: false, barIndex: 0, barTime: '' };

    const swingTrend:    TrendState = { bias: 0 };
    const internalTrend: TrendState = { bias: 0 };

    const trailing: TrailingExtremes = {
      top: -Infinity,
      bottom: Infinity,
      lastTopTime: candles[0]?.timestamp ?? '',
      lastBottomTime: candles[0]?.timestamp ?? '',
      lastTopIndex: 0,
      lastBottomIndex: 0,
    };

    let prevSwingLeg:    0 | 1 | null = null;
    let prevInternalLeg: 0 | 1 | null = null;
    let prevEqLeg:       0 | 1 | null = null;

    for (let i = 0; i < candles.length; i++) {
      const candle = candles[i]!;

      // updateTrailingExtremes()
      if (candle.high > trailing.top) {
        trailing.top = candle.high;
        trailing.lastTopTime = candle.timestamp;
        trailing.lastTopIndex = i;
      }
      if (candle.low < trailing.bottom) {
        trailing.bottom = candle.low;
        trailing.lastBottomTime = candle.timestamp;
        trailing.lastBottomIndex = i;
      }

      // ── Swing Structure (size=SWING_SIZE) ── getCurrentStructure(50, false, false)
      const swingLeg = this.getLeg(candles, i, SWING_SIZE);
      if (swingLeg !== null && swingLeg !== prevSwingLeg) {
        const pIdx = i - SWING_SIZE;
        if (pIdx >= 0) {
          const pc = candles[pIdx]!;
          if (swingLeg === BULLISH_LEG) {
            swingLow.lastLevel    = swingLow.currentLevel;
            swingLow.currentLevel = pc.low;
            swingLow.crossed      = false;
            swingLow.barIndex     = pIdx;
            swingLow.barTime      = pc.timestamp;
            pivotsSwing.push({ index: pIdx, time: pc.timestamp, price: pc.low, type: 'LOW', length: SWING_SIZE, isSwing: true, confirmedAtIndex: i });
          } else {
            swingHigh.lastLevel    = swingHigh.currentLevel;
            swingHigh.currentLevel = pc.high;
            swingHigh.crossed      = false;
            swingHigh.barIndex     = pIdx;
            swingHigh.barTime      = pc.timestamp;
            pivotsSwing.push({ index: pIdx, time: pc.timestamp, price: pc.high, type: 'HIGH', length: SWING_SIZE, isSwing: true, confirmedAtIndex: i });
          }
        }
        prevSwingLeg = swingLeg;
      }

      // ── Swing BOS/CHoCH — displayStructure(false) ──
      // Pine Script: ta.crossover(close, swingHigh.currentLevel) = close crosses above
      if (!isNaN(swingHigh.currentLevel) && !swingHigh.crossed && candle.close > swingHigh.currentLevel) {
        const tag: 'BOS' | 'CHOCH' = swingTrend.bias === -1 ? 'CHOCH' : 'BOS';
        swingHigh.crossed = true;
        swingTrend.bias   = 1;
        const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BULLISH', brokenLevel: swingHigh.currentLevel, isInternal: false, confirmationCandleIndex: i };
        structureEvents.push(evt);
        swingEvents.push(evt);
        this.storeOrderBlock(orderBlocks, symbol, timeframe, 'BULLISH', swingHigh.barIndex, i, parsedHighs, parsedLows, candles, atr200, false);
      }

      if (!isNaN(swingLow.currentLevel) && !swingLow.crossed && candle.close < swingLow.currentLevel) {
        const tag: 'BOS' | 'CHOCH' = swingTrend.bias === 1 ? 'CHOCH' : 'BOS';
        swingLow.crossed = true;
        swingTrend.bias  = -1;
        const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BEARISH', brokenLevel: swingLow.currentLevel, isInternal: false, confirmationCandleIndex: i };
        structureEvents.push(evt);
        swingEvents.push(evt);
        this.storeOrderBlock(orderBlocks, symbol, timeframe, 'BEARISH', swingLow.barIndex, i, parsedHighs, parsedLows, candles, atr200, false);
      }

      // ── Internal Structure (size=INTERNAL_SIZE) ── getCurrentStructure(5, false, true)
      const internalLegVal = this.getLeg(candles, i, INTERNAL_SIZE);
      if (internalLegVal !== null && internalLegVal !== prevInternalLeg) {
        const pIdx = i - INTERNAL_SIZE;
        if (pIdx >= 0) {
          const pc = candles[pIdx]!;
          if (internalLegVal === BULLISH_LEG) {
            internalLow.lastLevel    = internalLow.currentLevel;
            internalLow.currentLevel = pc.low;
            internalLow.crossed      = false;
            internalLow.barIndex     = pIdx;
            internalLow.barTime      = pc.timestamp;
            pivotsInternal.push({ index: pIdx, time: pc.timestamp, price: pc.low, type: 'LOW', length: INTERNAL_SIZE, isSwing: false, confirmedAtIndex: i });
          } else {
            internalHigh.lastLevel    = internalHigh.currentLevel;
            internalHigh.currentLevel = pc.high;
            internalHigh.crossed      = false;
            internalHigh.barIndex     = pIdx;
            internalHigh.barTime      = pc.timestamp;
            pivotsInternal.push({ index: pIdx, time: pc.timestamp, price: pc.high, type: 'HIGH', length: INTERNAL_SIZE, isSwing: false, confirmedAtIndex: i });
          }
        }
        prevInternalLeg = internalLegVal;
      }

      // ── Internal BOS/CHoCH — displayStructure(true) ──
      // extraCondition for bullish: internalHigh.level != swingHigh.level
      if (
        !isNaN(internalHigh.currentLevel) &&
        !internalHigh.crossed &&
        candle.close > internalHigh.currentLevel &&
        internalHigh.currentLevel !== swingHigh.currentLevel
      ) {
        const tag: 'BOS' | 'CHOCH' = internalTrend.bias === -1 ? 'CHOCH' : 'BOS';
        internalHigh.crossed = true;
        internalTrend.bias   = 1;
        const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BULLISH', brokenLevel: internalHigh.currentLevel, isInternal: true, confirmationCandleIndex: i };
        structureEvents.push(evt);
        internalEvents.push(evt);
        this.storeOrderBlock(orderBlocks, symbol, timeframe, 'BULLISH', internalHigh.barIndex, i, parsedHighs, parsedLows, candles, atr200, true);
      }

      if (
        !isNaN(internalLow.currentLevel) &&
        !internalLow.crossed &&
        candle.close < internalLow.currentLevel &&
        internalLow.currentLevel !== swingLow.currentLevel
      ) {
        const tag: 'BOS' | 'CHOCH' = internalTrend.bias === 1 ? 'CHOCH' : 'BOS';
        internalLow.crossed = true;
        internalTrend.bias  = -1;
        const evt: MarketStructureEventDto = { index: i, time: candle.timestamp, type: tag, direction: 'BEARISH', brokenLevel: internalLow.currentLevel, isInternal: true, confirmationCandleIndex: i };
        structureEvents.push(evt);
        internalEvents.push(evt);
        this.storeOrderBlock(orderBlocks, symbol, timeframe, 'BEARISH', internalLow.barIndex, i, parsedHighs, parsedLows, candles, atr200, true);
      }

      // ── Equal Highs / Equal Lows — getCurrentStructure(3, true) ──
      const eqLegVal = this.getLeg(candles, i, SMC_EQH_EQL_SIZE);
      if (eqLegVal !== null && eqLegVal !== prevEqLeg) {
        const pIdx = i - SMC_EQH_EQL_SIZE;
        if (pIdx >= 0) {
          const pc = candles[pIdx]!;
          const tol = SMC_EQH_EQL_THRESHOLD * atr200;

          if (eqLegVal === BULLISH_LEG) {
            if (!isNaN(eqLow.currentLevel) && Math.abs(eqLow.currentLevel - pc.low) < tol) {
              const avg = (eqLow.currentLevel + pc.low) / 2;
              let isSwept = false;
              for (let m = pIdx + 1; m < candles.length; m++) {
                if (candles[m]!.low < avg - tol) { isSwept = true; break; }
              }
              equalHighLows.push({ id: `EQL-SMC-${symbol}-${eqLow.barIndex}-${pIdx}`, symbol, timeframe, type: 'EQL', priceLevel: Number(avg.toFixed(4)), firstPivotIndex: eqLow.barIndex, secondPivotIndex: pIdx, tolerance: Number(tol.toFixed(4)), isSwept });
            }
            eqLow.lastLevel    = eqLow.currentLevel;
            eqLow.currentLevel = pc.low;
            eqLow.barIndex     = pIdx;
            eqLow.barTime      = pc.timestamp;
          } else {
            if (!isNaN(eqHigh.currentLevel) && Math.abs(eqHigh.currentLevel - pc.high) < tol) {
              const avg = (eqHigh.currentLevel + pc.high) / 2;
              let isSwept = false;
              for (let m = pIdx + 1; m < candles.length; m++) {
                if (candles[m]!.high > avg + tol) { isSwept = true; break; }
              }
              equalHighLows.push({ id: `EQH-SMC-${symbol}-${eqHigh.barIndex}-${pIdx}`, symbol, timeframe, type: 'EQH', priceLevel: Number(avg.toFixed(4)), firstPivotIndex: eqHigh.barIndex, secondPivotIndex: pIdx, tolerance: Number(tol.toFixed(4)), isSwept });
            }
            eqHigh.lastLevel    = eqHigh.currentLevel;
            eqHigh.currentLevel = pc.high;
            eqHigh.barIndex     = pIdx;
            eqHigh.barTime      = pc.timestamp;
          }
        }
        prevEqLeg = eqLegVal;
      }
    }

    // Post-process: apply High/Low mitigation
    this.applyMitigation(orderBlocks, candles);

    return {
      pivotsSwing,
      pivotsInternal,
      structureEvents,
      internalEvents,
      swingEvents,
      orderBlocks,
      equalHighLows,
      swingTrend:    swingTrend.bias >= 0    ? 'BULLISH' : 'BEARISH',
      internalTrend: internalTrend.bias >= 0 ? 'BULLISH' : 'BEARISH',
      trailingExtremes: trailing,
      atr200,
    };
  }

  // ============================================================
  // storeOrderBlock — Pine Script storeOrderBlock()
  //
  // LuxAlgo SMC OB zone definition:
  //   BULLISH OB (Demand): last bearish candle before BOS break.
  //     Search backwards from breakIdx for a down-candle (close < open).
  //     Zone: low (bottom) to high (top).  Body top = open = lower bound.
  //   BEARISH OB (Supply): last bullish candle before BOS break.
  //     Search backwards from breakIdx for an up-candle (close > open).
  //     Zone: low (bottom) to high (top).  Body bottom = open = upper bound.
  //
  // This matches what the TradingView LuxAlgo SMC script draws.
  // Volatility filter: skip if (high-low) >= 2 * ATR(200)
  // ============================================================
  private static storeOrderBlock(
    orderBlocks: OrderBlockDto[],
    symbol: string,
    timeframe: TradingTimeframe,
    bias: 'BULLISH' | 'BEARISH',
    legStartIdx: number,
    breakIdx: number,
    parsedHighs: number[],
    parsedLows: number[],
    candles: CandleDto[],
    atr200: number,
    isInternal: boolean
  ): void {
    const start = Math.max(0, legStartIdx);
    const end   = Math.min(breakIdx, candles.length - 1);
    if (start >= end) return;

    // Search window: look back up to 10 bars before the break for the last opposite-color candle
    const searchStart = Math.max(start, end - 10);
    let obBarIdx = -1;

    if (bias === 'BULLISH') {
      // Demand OB: find the last BEARISH candle (close < open) before the break
      for (let k = end - 1; k >= searchStart; k--) {
        if (candles[k]!.close < candles[k]!.open) {
          obBarIdx = k;
          break;
        }
      }
      // Fallback: if no bearish candle found, use the candle with lowest low in the range
      if (obBarIdx === -1) {
        let minVal = Infinity;
        for (let k = searchStart; k < end; k++) {
          if (parsedLows[k]! < minVal) { minVal = parsedLows[k]!; obBarIdx = k; }
        }
      }
    } else {
      // Supply OB: find the last BULLISH candle (close > open) before the break
      for (let k = end - 1; k >= searchStart; k--) {
        if (candles[k]!.close > candles[k]!.open) {
          obBarIdx = k;
          break;
        }
      }
      // Fallback: if no bullish candle found, use the candle with highest high in the range
      if (obBarIdx === -1) {
        let maxVal = -Infinity;
        for (let k = searchStart; k < end; k++) {
          if (parsedHighs[k]! > maxVal) { maxVal = parsedHighs[k]!; obBarIdx = k; }
        }
      }
    }

    if (obBarIdx === -1) return;

    const obCandle    = candles[obBarIdx]!;
    const candleRange = obCandle.high - obCandle.low;

    // LuxAlgo volatility filter
    if (atr200 > 0 && candleRange >= 2 * atr200) return;

    // OB zone = full candle range (high to low), matching TradingView LuxAlgo box
    const upperPrice = Number(obCandle.high.toFixed(4));
    const lowerPrice = Number(obCandle.low.toFixed(4));

    const source = isInternal ? 'PAT' : 'SMC';
    const prefix = isInternal ? 'INT' : 'SWG';
    const obId   = `OB-${prefix}-${bias}-${symbol}-${obBarIdx}-${breakIdx}`;

    const ob = OrderBlockWidthEngine.enrichOrderBlock(
      obId, symbol, timeframe, bias,
      upperPrice, lowerPrice,
      obBarIdx, breakIdx,
      false, false, 0,
      source, obCandle.timestamp
    );
    orderBlocks.push(ob);
  }

  // ============================================================
  // applyMitigation — Pine Script deleteOrderBlocks()
  //
  // bearishOrderBlockMitigationSource = high  (High/Low mode)
  // bullishOrderBlockMitigationSource = low
  //
  // Bearish OB mitigated: high > ob.barHigh
  // Bullish OB mitigated: low  < ob.barLow
  // ============================================================
  private static applyMitigation(orderBlocks: OrderBlockDto[], candles: CandleDto[]): void {
    for (const ob of orderBlocks) {
      const searchStart = ob.breakCandleIndex + 1;
      let isMitigated      = false;
      let mitigatedAtIndex: number | undefined;

      for (let m = searchStart; m < candles.length; m++) {
        const c = candles[m]!;
        if (ob.type === 'BULLISH' && c.low < ob.lowerPrice) {
          isMitigated      = true;
          mitigatedAtIndex = m;
          break;
        }
        if (ob.type === 'BEARISH' && c.high > ob.upperPrice) {
          isMitigated      = true;
          mitigatedAtIndex = m;
          break;
        }
      }

      (ob as any).isMitigated      = isMitigated;
      (ob as any).mitigatedAtIndex = mitigatedAtIndex;
      if (isMitigated) (ob as any).touchCount = 1;
    }
  }
}
