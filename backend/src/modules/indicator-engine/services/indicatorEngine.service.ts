import {
  CandleDto,
  DemandZone,
  IndicatorEngineOutput,
  SupplyZone,
  TradingTimeframe,
} from "@algoapp/shared";

// Authoritative indicator engines (Pine Script faithful ports)
import { SmcLegEngine } from "../engines/smcLegEngine.js";
import { PatLegEngine } from "../engines/patLegEngine.js";

// Zone post-processing pipeline
import { ZoneMergerService } from "../../strategy/services/zoneMerger.service.js";
import { ZoneLifecycleEngine } from "../engines/zoneLifecycleEngine.js";
import { FreshnessEngine } from "../engines/freshnessEngine.js";
import { TouchEngine } from "../engines/touchEngine.js";
import { ZoneScoreEngine } from "../engines/zoneScoreEngine.js";
import { PremiumDiscountEngine } from "../engines/premiumDiscountEngine.js";
import { FvgEngine } from "../engines/fvgEngine.js";
import { OrderBlockWidthEngine } from "../engines/orderBlockWidthEngine.js";
import { CandleStoreService } from "../../market-data/services/candleStore.service.js";

export class IndicatorEngineService {

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

    const latestCandle = candles[candles.length - 1]!;

    // 1. LuxAlgo SMC Engine (swing + internal OBs, EQH/EQL, BOS/CHoCH)
    const smcResult = SmcLegEngine.run(symbol, candles, timeframe);

    // 2. UAlgo Price Action Toolkit (zigzag, PAT OBs, liquidity sweeps)
    const patResult = PatLegEngine.run(symbol, candles, timeframe);

    // 3. FVG disabled per strategy (FVG_ENABLED=false in fvgEngine.ts)
    const fairValueGaps = FvgEngine.detectFvgs(symbol, candles, timeframe);

    // 4. Combine OBs from both sources, preserve SMC/PAT source labels
    const allRawOBs = [...smcResult.orderBlocks, ...patResult.orderBlocks];

    // Re-enrich through OrderBlockWidthEngine (applies 0.6% rule, single-use tracking)
    const allOrderBlocks = allRawOBs.map((ob) =>
      OrderBlockWidthEngine.enrichOrderBlock(
        ob.id, ob.symbol, ob.timeframe, ob.type,
        ob.upperPrice, ob.lowerPrice,
        ob.baseCandleIndex, ob.breakCandleIndex,
        (ob as any).isMitigated ?? false,
        (ob as any).isInvalidated ?? false,
        (ob as any).touchCount ?? 0,
        ob.source, ob.createdAt,
        (ob as any).mitigatedAtIndex,
      )
    );

    // 5. Build Supply/Demand zones from active (non-mitigated, non-used) OBs
    const supplyZonesRaw: SupplyZone[] = allOrderBlocks
      .filter((ob) => ob.type === "BEARISH" && !ob.isMitigated && !ob.isInvalidated && !ob.isUsed)
      .map((ob) => ({
        id: `ZONE-SUP-${ob.id}`,
        symbol, timeframe,
        type: "SUPPLY" as const,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        patStrength: ob.source === "PAT" ? 80.0 : 0.0,
        smcStrength: ob.source === "SMC" ? 90.0 : 0.0,
        mergedStrength: ob.source === "SMC" ? 90.0 : 80.0,
        width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
        freshness: ob.touchCount === 0 ? 100.0 : Math.max(0, 100 - ob.touchCount * 20),
        touchCount: ob.touchCount,
        age: candles.length - 1 - ob.baseCandleIndex,
        confidence: ob.source === "SMC" ? 90.0 : 80.0,
        status: ob.touchCount === 0 ? ("NEW" as any) : ("TRADED" as any),
        source: ob.source as any,
        createdAt: ob.createdAt,
        updatedAt: ob.createdAt,
      }));

    const demandZonesRaw: DemandZone[] = allOrderBlocks
      .filter((ob) => ob.type === "BULLISH" && !ob.isMitigated && !ob.isInvalidated && !ob.isUsed)
      .map((ob) => ({
        id: `ZONE-DEM-${ob.id}`,
        symbol, timeframe,
        type: "DEMAND" as const,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        patStrength: ob.source === "PAT" ? 80.0 : 0.0,
        smcStrength: ob.source === "SMC" ? 90.0 : 0.0,
        mergedStrength: ob.source === "SMC" ? 90.0 : 80.0,
        width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
        freshness: ob.touchCount === 0 ? 100.0 : Math.max(0, 100 - ob.touchCount * 20),
        touchCount: ob.touchCount,
        age: candles.length - 1 - ob.baseCandleIndex,
        confidence: ob.source === "SMC" ? 90.0 : 80.0,
        status: ob.touchCount === 0 ? ("NEW" as any) : ("TRADED" as any),
        source: ob.source as any,
        createdAt: ob.createdAt,
        updatedAt: ob.createdAt,
      }));

    // 6. Zone post-processing pipeline
    const mergedSupply = ZoneMergerService.detectAndMergeZones(supplyZonesRaw) as SupplyZone[];
    const mergedDemand = ZoneMergerService.detectAndMergeZones(demandZonesRaw) as DemandZone[];
    const lifecycleSupply = ZoneLifecycleEngine.updateLifecycle(mergedSupply, latestCandle);
    const lifecycleDemand = ZoneLifecycleEngine.updateLifecycle(mergedDemand, latestCandle);
    const freshSupply = FreshnessEngine.updateFreshness(lifecycleSupply);
    const freshDemand = FreshnessEngine.updateFreshness(lifecycleDemand);
    const finalSupply = TouchEngine.evaluateTouches(freshSupply, latestCandle);
    const finalDemand = TouchEngine.evaluateTouches(freshDemand, latestCandle);

    // 7. Market Structure from SMC engine
    const liquiditySwept = patResult.liquiditySweeps.length > 0;
    const lastBosEvt   = smcResult.structureEvents.filter((e) => e.type === "BOS").slice(-1)[0];
    const lastChochEvt = smcResult.structureEvents.filter((e) => e.type === "CHOCH").slice(-1)[0];
    const lastPivot    = smcResult.pivotsSwing.slice(-1)[0];

    const marketStructure = {
      symbol, timeframe,
      trend:          smcResult.swingTrend,
      internalTrend:  smcResult.internalTrend,
      swingTrend:     smcResult.swingTrend,
      liquiditySwept,
      lastBosTime:    lastBosEvt?.time,
      lastChochTime:  lastChochEvt?.time,
      lastPivotType:  lastPivot?.type,
      lastPivotPrice: lastPivot?.price,
    };

    // 8. Zone Scoring
    const supplyScores = ZoneScoreEngine.scoreZones(finalSupply, marketStructure);
    const demandScores = ZoneScoreEngine.scoreZones(finalDemand, marketStructure);
    const zoneScores   = { ...supplyScores, ...demandScores };
    const premiumDiscountZones = PremiumDiscountEngine.calculateZones(candles);

    // 9. Real ATR values (were hardcoded 0 — now computed from actual candles)
    const atr14  = PatLegEngine.calculateAtr14(candles);
    const atr200 = SmcLegEngine.calculateAtr200(candles);

    return {
      symbol, timeframe,
      supplyZones:     finalSupply,
      demandZones:     finalDemand,
      zoneScores,
      marketStructure,
      premiumDiscountZones,
      pivotsInternal:  smcResult.pivotsInternal,
      pivotsSwing:     smcResult.pivotsSwing,
      zigzagLegs:      patResult.zigzagLegs,
      structureEvents: smcResult.structureEvents,
      orderBlocks:     allOrderBlocks,
      liquiditySweeps: patResult.liquiditySweeps,
      fairValueGaps,
      equalHighLows:   smcResult.equalHighLows,
      atr14,
      atr200,
      evaluatedAt: new Date().toISOString(),
    };
  }

  /**
   * Synchronous compute used by ScannerEngine + StrategyPipelineService.
   * Backed by SmcLegEngine + PatLegEngine (Pine Script faithful ports).
   */
  public static computeIndicators(
    candles: CandleDto[],
    timeframe: TradingTimeframe = "1H",
    symbol: string = "BTCUSD.P",
  ): IndicatorEngineOutput {
    return IndicatorEngineService.runPipeline(symbol, candles, timeframe);
  }

  /**
   * Async variant used by the indicator controller (API requests).
   * Fetches 200 candles so SMC_SWING_SIZE=50 produces valid pivots.
   */
  public async evaluateSymbol(
    symbol: string = "BTCUSD.P",
    timeframe: TradingTimeframe = "1H",
    _profileId?: string,
    inputCandles?: CandleDto[],
  ): Promise<IndicatorEngineOutput> {
    const candles = inputCandles ?? (await CandleStoreService.getCandles(symbol, timeframe, 200));
    return IndicatorEngineService.runPipeline(symbol, candles, timeframe);
  }
}
