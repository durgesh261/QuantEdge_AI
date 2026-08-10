// backend/src/modules/indicator-engine/services/indicatorEngine.service.ts
import {
  CandleDto,
  IndicatorEngineOutput,
  TradingTimeframe,
} from "@algoapp/shared";

import { LuxAlgoSMCEngine, LuxAlgoConfig } from "../engines/LuxAlgoSMCEngine.js";
import { CandleStoreService } from "../../market-data/services/candleStore.service.js";

/**
 * IndicatorEngineService — CANONICAL SINGLE SOURCE OF TRUTH
 *
 * RULE: Only LuxAlgo SMC Engine runs. No PAT, no UAlgo, no competing sources.
 * All Order Blocks come from the exact Pine Script port.
 */
export class IndicatorEngineService {

  private static readonly DEFAULT_CONFIG: LuxAlgoConfig = {
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

  private static runPipeline(
    symbol: string,
    candles: CandleDto[],
    timeframe: TradingTimeframe,
  ): IndicatorEngineOutput {
    if (!candles || candles.length === 0) {
      return {
        symbol, timeframe,
        supplyZones: [], demandZones: [], zoneScores: {},
        marketStructure: { symbol, timeframe, trend: "BULLISH", internalTrend: "BULLISH", swingTrend: "BULLISH", liquiditySwept: false },
        pivotsInternal: [], pivotsSwing: [], zigzagLegs: [], structureEvents: [],
        orderBlocks: [], liquiditySweeps: [], fairValueGaps: [], equalHighLows: [],
        atr14: 0, atr200: 0, evaluatedAt: new Date().toISOString(),
      };
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CANONICAL: ONLY LuxAlgo SMC Engine
    // ═══════════════════════════════════════════════════════════════════════
    const smcResult = LuxAlgoSMCEngine.run(symbol, candles, timeframe, this.DEFAULT_CONFIG);

    // Format internal OBs for shared output compatibility
    const formattedOrderBlocks = smcResult.internalOrderBlocks.map((ob) => ({
      id: ob.id,
      symbol,
      timeframe,
      type: ob.type,
      upperPrice: ob.upperPrice,
      lowerPrice: ob.lowerPrice,
      baseCandleIndex: ob.baseCandleIndex,
      breakCandleIndex: ob.breakCandleIndex,
      createdAt: ob.createdAt,
      source: 'SMC' as const,
      widthPercent: ob.widthPercent,
      isMitigated: ob.isMitigated,
      isInvalidated: false,
      isUsed: ob.isUsed,
      touchCount: ob.touchCount,
      strength: 90.0,
      width: ob.upperPrice - ob.lowerPrice,
      freshness: 100.0,
      status: 'FRESH' as any,
    }));

    // Build Supply/Demand zones for frontend compatibility
    const supplyZonesRaw = smcResult.internalOrderBlocks
      .filter(ob => ob.type === "BEARISH")
      .map(ob => ({
        id: `ZONE-SUP-${ob.id}`,
        symbol, timeframe,
        type: "SUPPLY" as const,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        patStrength: 0.0,
        smcStrength: 90.0,
        mergedStrength: 90.0,
        width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
        freshness: 100.0,
        touchCount: 0,
        age: 0,
        confidence: 90.0,
        status: "NEW" as any,
        source: "SMC" as const,
        createdAt: ob.createdAt,
        updatedAt: ob.createdAt,
      }));

    const demandZonesRaw = smcResult.internalOrderBlocks
      .filter(ob => ob.type === "BULLISH")
      .map(ob => ({
        id: `ZONE-DEM-${ob.id}`,
        symbol, timeframe,
        type: "DEMAND" as const,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        patStrength: 0.0,
        smcStrength: 90.0,
        mergedStrength: 90.0,
        width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
        freshness: 100.0,
        touchCount: 0,
        age: 0,
        confidence: 90.0,
        status: "NEW" as any,
        source: "SMC" as const,
        createdAt: ob.createdAt,
        updatedAt: ob.createdAt,
      }));

    const lastBosEvt = smcResult.structureEvents.filter(e => e.type === "BOS").slice(-1)[0];
    const lastChochEvt = smcResult.structureEvents.filter(e => e.type === "CHOCH").slice(-1)[0];
    const lastPivot = smcResult.pivotsSwing.slice(-1)[0];

    const marketStructure = {
      symbol, timeframe,
      trend: smcResult.swingTrend,
      internalTrend: smcResult.internalTrend,
      swingTrend: smcResult.swingTrend,
      liquiditySwept: false,
      lastBosTime: lastBosEvt?.time,
      lastChochTime: lastChochEvt?.time,
      lastPivotType: lastPivot?.type,
      lastPivotPrice: lastPivot?.price,
    };

    return {
      symbol, timeframe,
      supplyZones: supplyZonesRaw,
      demandZones: demandZonesRaw,
      zoneScores: {},
      marketStructure,
      pivotsInternal: smcResult.pivotsInternal,
      pivotsSwing: smcResult.pivotsSwing,
      zigzagLegs: [],
      structureEvents: smcResult.structureEvents,
      orderBlocks: formattedOrderBlocks as any,
      liquiditySweeps: [],
      fairValueGaps: [],
      equalHighLows: smcResult.equalHighLows,
      atr14: 0,
      atr200: smcResult.atr200,
      evaluatedAt: new Date().toISOString(),
    };
  }

  public static computeIndicators(
    candles: CandleDto[],
    timeframe: TradingTimeframe = "1H",
    symbol: string = "BTCUSD.P",
  ): IndicatorEngineOutput {
    return IndicatorEngineService.runPipeline(symbol, candles, timeframe);
  }

  public async evaluateSymbol(
    symbol: string = "BTCUSD.P",
    timeframe: TradingTimeframe = "1H",
    _profileId?: string,
    inputCandles?: CandleDto[],
  ): Promise<IndicatorEngineOutput> {
    const candles = inputCandles ?? (await CandleStoreService.getCandles(symbol, timeframe, 300));
    return IndicatorEngineService.runPipeline(symbol, candles, timeframe);
  }
}
