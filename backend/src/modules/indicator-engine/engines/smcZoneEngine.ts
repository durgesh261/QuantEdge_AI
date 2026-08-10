import {
  CandleDto,
  DemandZone,
  MarketStructureEventDto,
  OrderBlockDto,
  SupplyZone,
  TradingTimeframe,
} from '@algoapp/shared';
import { OrderBlockWidthEngine } from './orderBlockWidthEngine.js';

export class SmcZoneEngine {
  /**
   * Calculates 200-period ATR for volatility normalization
   */
  public static calculateAtr200(candles: CandleDto[], period: number = 200): number {
    if (candles.length < 2) return 100.0;
    const trs: number[] = [];
    for (let i = 1; i < candles.length; i++) {
      const high = candles[i]!.high;
      const low = candles[i]!.low;
      const prevClose = candles[i - 1]!.close;
      const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
      trs.push(tr);
    }
    const recentTrs = trs.slice(-period);
    const sum = recentTrs.reduce((acc, v) => acc + v, 0);
    return sum / Math.max(1, recentTrs.length);
  }

  /**
   * Extracts LuxAlgo Smart Money Concepts Order Blocks & Supply/Demand zones
   * with 200-period ATR volatility filtering, mitigation and invalidation tracking.
   */
  public static extractSmcZones(
    symbol: string,
    candles: CandleDto[],
    events: MarketStructureEventDto[],
    timeframe: TradingTimeframe = '1H'
  ): {
    supplyZones: SupplyZone[];
    demandZones: DemandZone[];
    orderBlocks: OrderBlockDto[];
  } {
    const supplyZones: SupplyZone[] = [];
    const demandZones: DemandZone[] = [];
    const allOrderBlocks: OrderBlockDto[] = [];

    if (candles.length < 10) {
      return { supplyZones, demandZones, orderBlocks: allOrderBlocks };
    }

    const atr200 = this.calculateAtr200(candles, 200);

    for (const evt of events) {
      const breakIdx = evt.confirmationCandleIndex;
      if (breakIdx <= 0 || breakIdx >= candles.length) continue;

      const searchStart = Math.max(0, breakIdx - 10);

      if (evt.direction === 'BULLISH') {
        // Demand OB: find last BEARISH candle (close < open) before the break
        let baseCandleIdx = -1;
        for (let k = breakIdx - 1; k >= searchStart; k--) {
          if (candles[k]!.close < candles[k]!.open) {
            baseCandleIdx = k;
            break;
          }
        }
        // Fallback: use bar just before break
        if (baseCandleIdx === -1) baseCandleIdx = Math.max(0, breakIdx - 1);

        const baseCandle = candles[baseCandleIdx]!;
        const candleRange = baseCandle.high - baseCandle.low;

        // LuxAlgo Volatility filter: discard outsized anomaly bars
        if (candleRange >= 2.0 * atr200 && atr200 > 0) continue;

        // OB zone = full candle range (high to low), matching TradingView LuxAlgo box
        const upperPrice = Number(baseCandle.high.toFixed(4));
        const lowerPrice = Number(baseCandle.low.toFixed(4));
        const width = Number((upperPrice - lowerPrice).toFixed(4));

        // Check for subsequent mitigation / invalidation
        let isMitigated = false;
        let mitigatedAtIndex: number | undefined = undefined;
        let isInvalidated = false;

        for (let m = breakIdx; m < candles.length; m++) {
          const testCandle = candles[m]!;
          if (!isMitigated && testCandle.low <= upperPrice) {
            isMitigated = true;
            mitigatedAtIndex = m;
          }
          if (testCandle.close < lowerPrice) {
            isInvalidated = true;
            break;
          }
        }

        const zoneId = `SMC-DEM-${symbol}-${evt.index}-${baseCandleIdx}`;

        const obDto = OrderBlockWidthEngine.enrichOrderBlock(
          `OB-${zoneId}`,
          symbol,
          timeframe,
          'BULLISH',
          upperPrice,
          lowerPrice,
          baseCandleIdx,
          breakIdx,
          isMitigated,
          isInvalidated,
          isMitigated ? 1 : 0,
          'SMC',
          evt.time,
          mitigatedAtIndex
        );
        allOrderBlocks.push(obDto);

        if (!isInvalidated) {
          demandZones.push({
            id: zoneId,
            symbol,
            timeframe,
            type: 'DEMAND',
            upperPrice,
            lowerPrice,
            patStrength: 0.0,
            smcStrength: 90.0,
            mergedStrength: 90.0,
            width,
            freshness: isMitigated ? 40.0 : 100.0,
            touchCount: isMitigated ? 1 : 0,
            age: candles.length - 1 - breakIdx,
            confidence: 90.0,
            status: isMitigated ? 'TRADED' : 'NEW',
            source: 'SMC',
            createdAt: evt.time,
            updatedAt: evt.time,
          });
        }
      } else {
        // Supply OB: find last BULLISH candle (close > open) before the break
        let baseCandleIdx = -1;
        for (let k = breakIdx - 1; k >= searchStart; k--) {
          if (candles[k]!.close > candles[k]!.open) {
            baseCandleIdx = k;
            break;
          }
        }
        // Fallback: use bar just before break
        if (baseCandleIdx === -1) baseCandleIdx = Math.max(0, breakIdx - 1);

        const baseCandle = candles[baseCandleIdx]!;
        const candleRange = baseCandle.high - baseCandle.low;

        // Volatility filter
        if (candleRange >= 2.0 * atr200 && atr200 > 0) continue;

        // OB zone = full candle range (high to low), matching TradingView LuxAlgo box
        const upperPrice = Number(baseCandle.high.toFixed(4));
        const lowerPrice = Number(baseCandle.low.toFixed(4));
        const width = Number((upperPrice - lowerPrice).toFixed(4));

        let isMitigated = false;
        let mitigatedAtIndex: number | undefined = undefined;
        let isInvalidated = false;

        for (let m = breakIdx; m < candles.length; m++) {
          const testCandle = candles[m]!;
          if (!isMitigated && testCandle.high >= lowerPrice) {
            isMitigated = true;
            mitigatedAtIndex = m;
          }
          if (testCandle.close > upperPrice) {
            isInvalidated = true;
            break;
          }
        }

        const zoneId = `SMC-SUP-${symbol}-${evt.index}-${baseCandleIdx}`;

        const obDto = OrderBlockWidthEngine.enrichOrderBlock(
          `OB-${zoneId}`,
          symbol,
          timeframe,
          'BEARISH',
          upperPrice,
          lowerPrice,
          baseCandleIdx,
          breakIdx,
          isMitigated,
          isInvalidated,
          isMitigated ? 1 : 0,
          'SMC',
          evt.time,
          mitigatedAtIndex
        );
        allOrderBlocks.push(obDto);

        if (!isInvalidated) {
          supplyZones.push({
            id: zoneId,
            symbol,
            timeframe,
            type: 'SUPPLY',
            upperPrice,
            lowerPrice,
            patStrength: 0.0,
            smcStrength: 90.0,
            mergedStrength: 90.0,
            width,
            freshness: isMitigated ? 40.0 : 100.0,
            touchCount: isMitigated ? 1 : 0,
            age: candles.length - 1 - breakIdx,
            confidence: 90.0,
            status: isMitigated ? 'TRADED' : 'NEW',
            source: 'SMC',
            createdAt: evt.time,
            updatedAt: evt.time,
          });
        }
      }
    }

    // Keep top 5 active supply and demand zones
    const activeSupply = supplyZones.slice(-5);
    const activeDemand = demandZones.slice(-5);

    return { supplyZones: activeSupply, demandZones: activeDemand, orderBlocks: allOrderBlocks };
  }
}
