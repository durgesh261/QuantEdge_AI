import {
  IndicatorEngineOutput,
  StrategyPipelineResultDto,
  TradingTimeframe,
  CandleDto,
} from '@algoapp/shared';

import { IndicatorEngineService } from '../../indicator-engine/services/indicatorEngine.service.js';
import { DecisionEngineService } from '../../decision/services/decisionEngine.service.js';
import { eventBus } from '../../../services/EventBus.js';
import { ZoneDetectorService } from './zoneDetector.service.js';

export interface RunPipelineOptions {
  symbol: string;
  timeframe: TradingTimeframe;
  candles: CandleDto[];
  autoExecute?: boolean | undefined;
}

export class StrategyPipelineService {
  public static async runPipeline(options: RunPipelineOptions): Promise<StrategyPipelineResultDto> {
    const { symbol, timeframe, candles, autoExecute = true } = options;

    if (!candles || candles.length === 0) {
      throw new Error(`Cannot run strategy pipeline for ${symbol}: empty candles array.`);
    }

    const latestCandle = candles[candles.length - 1]!;
    const currentPrice = latestCandle.close;
    const candleTimestamp = latestCandle.timestamp;

    // 1. Run Indicator Engine
    const indicators: IndicatorEngineOutput = IndicatorEngineService.computeIndicators(
      candles,
      timeframe
    );

    // 3. Get active zones and find best candidate
    const zones = await ZoneDetectorService.detectZones(symbol);
    let activeZone = zones.find(z => 
      z.status === 'FRESH' || z.status === 'TOUCHED'
    );

    // 4. Evaluate Decision with REAL data
    const decision = await DecisionEngineService.evaluateDecision({
      symbol,
      timeframe,
      currentPrice,
      indicators,
      activeZone,
      candleTimestamp,
    });

    // 5. Build Result
    const result: StrategyPipelineResultDto = {
      id: `PIPE-${Date.now()}`,
      symbol,
      timeframe,
      decisionState: decision.state as any,
      entryPrice: decision.entryPrice,
      stopLossPrice: decision.stopLossPrice,
      takeProfitPrice: decision.takeProfitPrice,
      positionSize: decision.positionSize,
      leverage: decision.leverage,
      confidenceScore: decision.confidenceScore,
      reasonCodes: decision.reasonCodes || [],
      executedAt: (decision.state as any) === 'APPROVED' ? new Date().toISOString() : undefined,
      createdAt: new Date().toISOString(),
    };

    // 6. Execution disabled in diagnostic pipeline — ScannerEngine is sole live execution path
    if (autoExecute) {
      // Diagnostic mode only — live trades executed solely via ScannerEngine
    }

    eventBus.emit('pipeline:completed', result);
    return result;
  }
}
