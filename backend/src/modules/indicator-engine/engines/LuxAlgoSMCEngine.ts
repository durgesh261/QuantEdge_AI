import {
  CandleDto,
  MarketStructureEventDto,
  PivotPointDto,
  TradingTimeframe,
  EqualHighLowDto,
} from '@algoapp/shared';

export interface LuxAlgoConfig {
  mode: 'HISTORICAL' | 'PRESENT';
  style: 'COLORED' | 'MONOCHROME';
  showInternals: boolean;
  showInternalBull: 'ALL' | 'BOS' | 'CHOCH';
  showInternalBear: 'ALL' | 'BOS' | 'CHOCH';
  internalFilterConfluence: boolean;
  showStructure: boolean;
  showSwingBull: 'ALL' | 'BOS' | 'CHOCH';
  showSwingBear: 'ALL' | 'BOS' | 'CHOCH';
  showInternalOrderBlocks: boolean;
  internalOrderBlocksSize: number;
  showSwingOrderBlocks: boolean;
  swingOrderBlocksSize: number;
  orderBlockFilter: 'ATR' | 'RANGE';
  orderBlockMitigation: 'CLOSE' | 'HIGHLOW';
  swingLength: number; // default 50
  internalLength: number; // default 5
  eqhEqlLength: number; // default 3
  eqhEqlThreshold: number; // default 0.1 (* ATR(200))
  showEqualHighsLows: boolean;
  showHighLowSwings: boolean;
  showPremiumDiscountZones: boolean;
  showTrend: boolean;
}

export interface LuxAlgoOrderBlock {
  id: string;
  symbol: string;
  timeframe: string;
  type: 'BULLISH' | 'BEARISH';
  sourceType: 'INTERNAL' | 'SWING';
  upperPrice: number;
  lowerPrice: number;
  barHigh: number;
  barLow: number;
  barTime: string;
  barIndex: number;
  baseCandleIndex: number;
  breakCandleIndex: number;
  createdAt: string;
  widthPercent: number;
  mitigated: boolean;
  mitigatedAt?: string | undefined;
  touched: boolean;
  traded: boolean;
}

export interface TrailingExtremes {
  top: number;
  bottom: number;
  lastTopTime: string;
  lastBottomTime: string;
  lastTopIndex: number;
  lastBottomIndex: number;
}

export interface LuxAlgoSMCResult {
  symbol: string;
  timeframe: TradingTimeframe;
  internalOrderBlocks: LuxAlgoOrderBlock[];
  swingOrderBlocks: LuxAlgoOrderBlock[];
  structureEvents: MarketStructureEventDto[];
  pivotsInternal: PivotPointDto[];
  pivotsSwing: PivotPointDto[];
  equalHighLows: EqualHighLowDto[];
  swingTrend: 'BULLISH' | 'BEARISH';
  internalTrend: 'BULLISH' | 'BEARISH';
  atr200: number;
  trailingExtremes: TrailingExtremes;
  premiumZone: { top: number; bottom: number };
  equilibriumZone: { top: number; bottom: number };
  discountZone: { top: number; bottom: number };
}

const BULLISH_LEG = 1;
const BEARISH_LEG = 0;

export class LuxAlgoSMCEngine {
  public static run(
    symbol: string,
    candles: CandleDto[],
    timeframe: TradingTimeframe,
    config: LuxAlgoConfig
  ): LuxAlgoSMCResult {
    if (!candles || candles.length < 10) {
      return this.emptyResult(symbol, timeframe);
    }

    const atr200Series = this.computeATR(candles, 200);
    const cumMeanRangeSeries = this.computeCumulativeMeanRange(candles);
    const atr200 = atr200Series[atr200Series.length - 1] || 1.0;

    const parsedHighs: number[] = [];
    const parsedLows: number[] = [];
    const highs: number[] = [];
    const lows: number[] = [];
    const times: string[] = [];

    for (let i = 0; i < candles.length; i++) {
      const c = candles[i]!;
      const volMeasure = config.orderBlockFilter === 'ATR'
        ? (atr200Series[i] || (c.high - c.low))
        : (cumMeanRangeSeries[i] || (c.high - c.low));
      const isHighVol = (c.high - c.low) >= (2 * volMeasure);

      parsedHighs.push(isHighVol ? c.low : c.high);
      parsedLows.push(isHighVol ? c.high : c.low);
      highs.push(c.high);
      lows.push(c.low);
      times.push(c.timestamp);
    }

    const pivotsSwing: PivotPointDto[] = [];
    const pivotsInternal: PivotPointDto[] = [];
    const structureEvents: MarketStructureEventDto[] = [];
    const equalHighLows: EqualHighLowDto[] = [];
    const internalOrderBlocks: LuxAlgoOrderBlock[] = [];
    const swingOrderBlocks: LuxAlgoOrderBlock[] = [];

    let swingTrend: 'BULLISH' | 'BEARISH' = 'BULLISH';
    let internalTrend: 'BULLISH' | 'BEARISH' = 'BULLISH';

    let currentSwingHigh = { level: 0, barIndex: 0, barTime: '', crossed: false };
    let currentSwingLow = { level: 0, barIndex: 0, barTime: '', crossed: false };
    let currentInternalHigh = { level: 0, barIndex: 0, barTime: '', crossed: false };
    let currentInternalLow = { level: 0, barIndex: 0, barTime: '', crossed: false };
    let currentEqualHigh = { level: 0, barIndex: 0, barTime: '' };
    let currentEqualLow = { level: 0, barIndex: 0, barTime: '' };

    let trailingExtremes: TrailingExtremes = {
      top: candles[0]!.high,
      bottom: candles[0]!.low,
      lastTopTime: candles[0]!.timestamp,
      lastBottomTime: candles[0]!.timestamp,
      lastTopIndex: 0,
      lastBottomIndex: 0,
    };

    // Calculate Leg state series for swing size (50) and internal size (5)
    const swingLegs = this.computeLegSeries(candles, config.swingLength);
    const internalLegs = this.computeLegSeries(candles, config.internalLength);
    const eqhEqlLegs = this.computeLegSeries(candles, config.eqhEqlLength);

    for (let i = 1; i < candles.length; i++) {
      const c = candles[i]!;

      // Update trailing extremes
      if (c.high >= trailingExtremes.top) {
        trailingExtremes.top = c.high;
        trailingExtremes.lastTopTime = c.timestamp;
        trailingExtremes.lastTopIndex = i;
      }
      if (c.low <= trailingExtremes.bottom) {
        trailingExtremes.bottom = c.low;
        trailingExtremes.lastBottomTime = c.timestamp;
        trailingExtremes.lastBottomIndex = i;
      }

      // Check order block mitigations on each candle
      this.mitigateOrderBlocks(internalOrderBlocks, c, config.orderBlockMitigation);
      this.mitigateOrderBlocks(swingOrderBlocks, c, config.orderBlockMitigation);

      // Evaluate EQH / EQL
      if (config.showEqualHighsLows && eqhEqlLegs[i] !== eqhEqlLegs[i - 1]) {
        const isBullishLeg = eqhEqlLegs[i] === BULLISH_LEG;
        const targetIdx = i - config.eqhEqlLength;
        if (targetIdx >= 0 && targetIdx < candles.length) {
          const targetCandle = candles[targetIdx]!;
          const currentAtr = atr200Series[i] || 1.0;
          if (isBullishLeg) {
            // Pivot Low
            if (currentEqualLow.level > 0 && Math.abs(currentEqualLow.level - targetCandle.low) < config.eqhEqlThreshold * currentAtr) {
              equalHighLows.push({
                id: `EQL-${symbol}-${targetIdx}`,
                symbol,
                timeframe,
                type: 'EQL',
                priceLevel: targetCandle.low,
                firstPivotIndex: currentEqualLow.barIndex,
                secondPivotIndex: targetIdx,
                tolerance: config.eqhEqlThreshold,
                isSwept: false,
              });
            }
            currentEqualLow = { level: targetCandle.low, barIndex: targetIdx, barTime: targetCandle.timestamp };
          } else {
            // Pivot High
            if (currentEqualHigh.level > 0 && Math.abs(currentEqualHigh.level - targetCandle.high) < config.eqhEqlThreshold * currentAtr) {
              equalHighLows.push({
                id: `EQH-${symbol}-${targetIdx}`,
                symbol,
                timeframe,
                type: 'EQH',
                priceLevel: targetCandle.high,
                firstPivotIndex: currentEqualHigh.barIndex,
                secondPivotIndex: targetIdx,
                tolerance: config.eqhEqlThreshold,
                isSwept: false,
              });
            }
            currentEqualHigh = { level: targetCandle.high, barIndex: targetIdx, barTime: targetCandle.timestamp };
          }
        }
      }

      // Process Internal Structure
      if (internalLegs[i] !== internalLegs[i - 1]) {
        const isBullishLeg = internalLegs[i] === BULLISH_LEG;
        const targetIdx = i - config.internalLength;
        if (targetIdx >= 0 && targetIdx < candles.length) {
          const targetCandle = candles[targetIdx]!;
          if (isBullishLeg) {
            currentInternalLow = { level: targetCandle.low, barIndex: targetIdx, barTime: targetCandle.timestamp, crossed: false };
            pivotsInternal.push({
              index: targetIdx,
              time: targetCandle.timestamp,
              price: targetCandle.low,
              type: 'LOW',
              length: config.internalLength,
              isSwing: false,
              confirmedAtIndex: i,
            });
          } else {
            currentInternalHigh = { level: targetCandle.high, barIndex: targetIdx, barTime: targetCandle.timestamp, crossed: false };
            pivotsInternal.push({
              index: targetIdx,
              time: targetCandle.timestamp,
              price: targetCandle.high,
              type: 'HIGH',
              length: config.internalLength,
              isSwing: false,
              confirmedAtIndex: i,
            });
          }
        }
      }

      // Check Internal Breakouts (BOS / CHoCH)
      if (currentInternalHigh.level > 0 && !currentInternalHigh.crossed && c.close > currentInternalHigh.level) {
        const eventType = internalTrend === 'BEARISH' ? 'CHOCH' : 'BOS';
        currentInternalHigh.crossed = true;
        internalTrend = 'BULLISH';

        structureEvents.push({
          index: i,
          time: c.timestamp,
          type: eventType,
          direction: 'BULLISH',
          brokenLevel: currentInternalHigh.level,
          isInternal: true,
          confirmationCandleIndex: i,
        });

        if (config.showInternalOrderBlocks) {
          this.storeOrderBlock(
            symbol, timeframe, candles, parsedHighs, parsedLows, times,
            currentInternalHigh.barIndex, i, 'BULLISH', 'INTERNAL',
            internalOrderBlocks, config.internalOrderBlocksSize
          );
        }
      }

      if (currentInternalLow.level > 0 && !currentInternalLow.crossed && c.close < currentInternalLow.level) {
        const eventType = internalTrend === 'BULLISH' ? 'CHOCH' : 'BOS';
        currentInternalLow.crossed = true;
        internalTrend = 'BEARISH';

        structureEvents.push({
          index: i,
          time: c.timestamp,
          type: eventType,
          direction: 'BEARISH',
          brokenLevel: currentInternalLow.level,
          isInternal: true,
          confirmationCandleIndex: i,
        });

        if (config.showInternalOrderBlocks) {
          this.storeOrderBlock(
            symbol, timeframe, candles, parsedHighs, parsedLows, times,
            currentInternalLow.barIndex, i, 'BEARISH', 'INTERNAL',
            internalOrderBlocks, config.internalOrderBlocksSize
          );
        }
      }

      // Process Swing Structure
      if (swingLegs[i] !== swingLegs[i - 1]) {
        const isBullishLeg = swingLegs[i] === BULLISH_LEG;
        const targetIdx = i - config.swingLength;
        if (targetIdx >= 0 && targetIdx < candles.length) {
          const targetCandle = candles[targetIdx]!;
          if (isBullishLeg) {
            currentSwingLow = { level: targetCandle.low, barIndex: targetIdx, barTime: targetCandle.timestamp, crossed: false };
            pivotsSwing.push({
              index: targetIdx,
              time: targetCandle.timestamp,
              price: targetCandle.low,
              type: 'LOW',
              length: config.swingLength,
              isSwing: true,
              confirmedAtIndex: i,
            });
          } else {
            currentSwingHigh = { level: targetCandle.high, barIndex: targetIdx, barTime: targetCandle.timestamp, crossed: false };
            pivotsSwing.push({
              index: targetIdx,
              time: targetCandle.timestamp,
              price: targetCandle.high,
              type: 'HIGH',
              length: config.swingLength,
              isSwing: true,
              confirmedAtIndex: i,
            });
          }
        }
      }

      // Check Swing Breakouts (BOS / CHoCH)
      if (currentSwingHigh.level > 0 && !currentSwingHigh.crossed && c.close > currentSwingHigh.level) {
        const eventType = swingTrend === 'BEARISH' ? 'CHOCH' : 'BOS';
        currentSwingHigh.crossed = true;
        swingTrend = 'BULLISH';

        structureEvents.push({
          index: i,
          time: c.timestamp,
          type: eventType,
          direction: 'BULLISH',
          brokenLevel: currentSwingHigh.level,
          isInternal: false,
          confirmationCandleIndex: i,
        });

        if (config.showSwingOrderBlocks) {
          this.storeOrderBlock(
            symbol, timeframe, candles, parsedHighs, parsedLows, times,
            currentSwingHigh.barIndex, i, 'BULLISH', 'SWING',
            swingOrderBlocks, config.swingOrderBlocksSize
          );
        }
      }

      if (currentSwingLow.level > 0 && !currentSwingLow.crossed && c.close < currentSwingLow.level) {
        const eventType = swingTrend === 'BULLISH' ? 'CHOCH' : 'BOS';
        currentSwingLow.crossed = true;
        swingTrend = 'BEARISH';

        structureEvents.push({
          index: i,
          time: c.timestamp,
          type: eventType,
          direction: 'BEARISH',
          brokenLevel: currentSwingLow.level,
          isInternal: false,
          confirmationCandleIndex: i,
        });

        if (config.showSwingOrderBlocks) {
          this.storeOrderBlock(
            symbol, timeframe, candles, parsedHighs, parsedLows, times,
            currentSwingLow.barIndex, i, 'BEARISH', 'SWING',
            swingOrderBlocks, config.swingOrderBlocksSize
          );
        }
      }
    }

    const top = trailingExtremes.top;
    const bottom = trailingExtremes.bottom;
    const premiumZone = { top, bottom: 0.95 * top + 0.05 * bottom };
    const equilibriumZone = { top: 0.525 * top + 0.475 * bottom, bottom: 0.525 * bottom + 0.475 * top };
    const discountZone = { top: 0.95 * bottom + 0.05 * top, bottom };

    return {
      symbol,
      timeframe,
      internalOrderBlocks,
      swingOrderBlocks,
      structureEvents,
      pivotsInternal,
      pivotsSwing,
      equalHighLows,
      swingTrend,
      internalTrend,
      atr200,
      trailingExtremes,
      premiumZone,
      equilibriumZone,
      discountZone,
    };
  }

  // ═══════════════════════════════════════════════════════════════════════
  // CANONICAL: storeOrderBlock (Pine Script Exact Port)
  //
  // Pine Script:
  //   if bias == BEARISH:
  //     array = parsedHighs.slice(p_ivot.barIndex, bar_index)
  //     parsedIndex = p_ivot.barIndex + array.indexof(array.max())
  //   else:
  //     array = parsedLows.slice(p_ivot.barIndex, bar_index)
  //     parsedIndex = p_ivot.barIndex + array.indexof(array.min())
  // ═══════════════════════════════════════════════════════════════════════
  private static storeOrderBlock(
    symbol: string,
    timeframe: string,
    candles: CandleDto[],
    parsedHighs: number[],
    parsedLows: number[],
    times: string[],
    startIdx: number,
    endIdx: number,
    bias: 'BULLISH' | 'BEARISH',
    sourceType: 'INTERNAL' | 'SWING',
    orderBlocks: LuxAlgoOrderBlock[],
    maxSize: number
  ): void {
    if (startIdx < 0 || endIdx >= candles.length || startIdx >= endIdx) return;

    const sliceHighs = parsedHighs.slice(startIdx, endIdx);
    const sliceLows = parsedLows.slice(startIdx, endIdx);

    let barIndex: number;
    let barHigh: number;
    let barLow: number;

    if (bias === 'BEARISH') {
      const maxVal = Math.max(...sliceHighs);
      const localIdx = sliceHighs.indexOf(maxVal);
      barIndex = startIdx + localIdx;
      barHigh = parsedHighs[barIndex]!;
      barLow = parsedLows[barIndex]!;
    } else {
      const minVal = Math.min(...sliceLows);
      const localIdx = sliceLows.indexOf(minVal);
      barIndex = startIdx + localIdx;
      barHigh = parsedHighs[barIndex]!;
      barLow = parsedLows[barIndex]!;
    }

    const upperPrice = barHigh;
    const lowerPrice = barLow;
    const width = Math.max(0.0001, upperPrice - lowerPrice);
    const widthPercent = Number(((width / Math.max(0.0001, upperPrice)) * 100).toFixed(3));

    const ob: LuxAlgoOrderBlock = {
      id: `LUX-${sourceType.charAt(0)}-${bias}-${symbol}-${times[barIndex]}`,
      symbol,
      timeframe,
      type: bias,
      sourceType,
      upperPrice,
      lowerPrice,
      barHigh,
      barLow,
      barTime: times[barIndex]!,
      barIndex,
      baseCandleIndex: barIndex,
      breakCandleIndex: endIdx,
      createdAt: times[endIdx]!,
      widthPercent,
      mitigated: false,
      touched: false,
      traded: false,
    };

    orderBlocks.unshift(ob);
    if (orderBlocks.length > maxSize) {
      orderBlocks.pop();
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  // CANONICAL: deleteOrderBlocks (Pine Script Exact Port)
  //
  // Pine Script:
  //   if bearishOrderBlockMitigationSource > eachOrderBlock.barHigh and bias == BEARISH: crossed := true
  //   else if bullishOrderBlockMitigationSource < eachOrderBlock.barLow and bias == BULLISH: crossed := true
  // ═══════════════════════════════════════════════════════════════════════
  private static mitigateOrderBlocks(
    orderBlocks: LuxAlgoOrderBlock[],
    candle: CandleDto,
    mitigationMode: 'CLOSE' | 'HIGHLOW'
  ): void {
    const bearishSource = mitigationMode === 'CLOSE' ? candle.close : candle.high;
    const bullishSource = mitigationMode === 'CLOSE' ? candle.close : candle.low;

    for (let i = orderBlocks.length - 1; i >= 0; i--) {
      const ob = orderBlocks[i]!;
      let isMitigated = false;

      if (ob.type === 'BEARISH' && bearishSource > ob.barHigh) {
        isMitigated = true;
      } else if (ob.type === 'BULLISH' && bullishSource < ob.barLow) {
        isMitigated = true;
      }

      if (isMitigated) {
        ob.mitigated = true;
        ob.mitigatedAt = candle.timestamp;
        orderBlocks.splice(i, 1);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  // CANONICAL: leg(size) (Pine Script Exact Port)
  //
  // Pine Script:
  //   newLegHigh = high[size] > ta.highest(size)
  //   newLegLow  = low[size]  < ta.lowest(size)
  // ═══════════════════════════════════════════════════════════════════════
  private static computeLegSeries(candles: CandleDto[], size: number): number[] {
    const legs: number[] = new Array(candles.length).fill(BEARISH_LEG);
    let currentLeg = BEARISH_LEG;

    for (let i = size; i < candles.length; i++) {
      const targetHigh = candles[i - size]!.high;
      const targetLow = candles[i - size]!.low;

      const slice = candles.slice(i - size + 1, i + 1);
      const highest = Math.max(...slice.map((c) => c.high));
      const lowest = Math.min(...slice.map((c) => c.low));

      const newLegHigh = targetHigh > highest;
      const newLegLow = targetLow < lowest;

      if (newLegHigh) {
        currentLeg = BEARISH_LEG;
      } else if (newLegLow) {
        currentLeg = BULLISH_LEG;
      }

      legs[i] = currentLeg;
    }

    return legs;
  }

  private static computeATR(candles: CandleDto[], period: number): number[] {
    const atr: number[] = [];
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i]!;
      if (i === 0) {
        atr.push(c.high - c.low);
        continue;
      }
      const prev = candles[i - 1]!;
      const tr = Math.max(
        c.high - c.low,
        Math.abs(c.high - prev.close),
        Math.abs(c.low - prev.close)
      );
      const lastAtr = atr[i - 1]!;
      if (i < period) {
        atr.push((lastAtr * i + tr) / (i + 1));
      } else {
        atr.push((lastAtr * (period - 1) + tr) / period);
      }
    }
    return atr;
  }

  private static computeCumulativeMeanRange(candles: CandleDto[]): number[] {
    const result: number[] = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      sum += candles[i]!.high - candles[i]!.low;
      result.push(sum / (i + 1));
    }
    return result;
  }

  private static emptyResult(symbol: string, timeframe: TradingTimeframe): LuxAlgoSMCResult {
    return {
      symbol,
      timeframe,
      internalOrderBlocks: [],
      swingOrderBlocks: [],
      structureEvents: [],
      pivotsInternal: [],
      pivotsSwing: [],
      equalHighLows: [],
      swingTrend: 'BULLISH',
      internalTrend: 'BULLISH',
      atr200: 1.0,
      trailingExtremes: { top: 0, bottom: 0, lastTopTime: '', lastBottomTime: '', lastTopIndex: 0, lastBottomIndex: 0 },
      premiumZone: { top: 0, bottom: 0 },
      equilibriumZone: { top: 0, bottom: 0 },
      discountZone: { top: 0, bottom: 0 },
    };
  }
}
