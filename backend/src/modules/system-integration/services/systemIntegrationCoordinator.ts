import {
  PipelineTraceDto,
  ExecutionMode,
  RunPipelineInput,
} from '@algoapp/shared';

import { CandleStoreService } from '../../market-data/services/candleStore.service.js';
import { ZoneDetectorService } from '../../strategy/services/zoneDetector.service.js';
import { StrategySignalService } from '../../strategy/services/strategySignal.service.js';
import { DecisionEngineService } from '../../decision/services/decisionEngine.service.js';
import { AIDecisionCenterService } from '../../ai-decision/services/aiDecisionCenter.service.js';
import { ExecutionEngineService } from '../../execution/services/executionEngine.service.js';
import { PipelineTraceService } from './pipelineTraceService.js';

export class SystemIntegrationCoordinator {
  public static async processCandlePipeline(input: RunPipelineInput): Promise<PipelineTraceDto> {
    const pipelineStart = Date.now();
    const mode = input.mode || ExecutionMode.SHADOW;
    const symbol = input.symbol;

    // 1. Stage 1: Market Data Engine - REAL Delta data only
    const t0 = Date.now();
    const candles = await CandleStoreService.getCandles(symbol, 1);
    
    // NO fake candle fallback - fail if no real data
    if (!candles || candles.length === 0) {
      throw new Error(`No real market data available for ${symbol} from Delta Exchange. Pipeline aborted.`);
    }
    
    const candle = candles[0]!;
    const marketDataLatencyMs = Date.now() - t0;

    // 2. Stage 2: Market Structure Engine
    const t1 = Date.now();
    const zones = await ZoneDetectorService.getZones(symbol);
    const marketStructureLatencyMs = Date.now() - t1;

    // 3. Stage 3: Trading Rules Engine
    const t2 = Date.now();
    const tradingRulesLatencyMs = Date.now() - t2;

    // 4. Stage 4: Strategy Engine
    const t3 = Date.now();
    const signal = await StrategySignalService.evaluateSignal(symbol, candle.close);
    const strategyLatencyMs = Date.now() - t3;

    // 5. Stage 5: Decision Engine
    const t4 = Date.now();
    
    const { IndicatorEngineService } = await import('../../indicator-engine/services/indicatorEngine.service.js');
    const indicators = IndicatorEngineService.computeIndicators(candles, '1H');

    // Default decision if no signal
    let decision;
    if (!signal) {
      decision = {
        id: `DEC-${Date.now()}`,
        symbol,
        timeframe: '1H',
        state: 'SKIP',
        confidenceScore: 0,
        reasonCodes: [],
        inputSnapshotHash: '',
        createdAt: new Date().toISOString(),
      } as any;
    } else {
      decision = await DecisionEngineService.evaluateDecision({
        symbol: signal.symbol,
        timeframe: '1H',
        currentPrice: signal.price,
        indicators,
        outcome: signal.outcome,
        candleTimestamp: candle.timestamp,
      });
    }
    const decisionLatencyMs = Date.now() - t4;

    // 6. Stage 6: AI Decision Center
    const t5 = Date.now();
    const explanation = await AIDecisionCenterService.explainDecision(decision.id);
    const aiDecisionLatencyMs = Date.now() - t5;

    // 6. Stage 6 & 7: Execution Engine & Paper Adapter
    const t6 = Date.now();

    // Map SHADOW mode to route to Paper Adapter without live exchange risks
    const executionInput = {
      decisionId: decision.id,
      symbol: decision.symbol,
      side: signal && signal.outcome === 'BUY' ? ('LONG' as const) : ('SHORT' as const),
      mode: mode === ExecutionMode.LIVE ? ExecutionMode.LIVE : ExecutionMode.PAPER,
      quantity: input.quantity || decision.contractQuantity || decision.positionSize || 0.1,
      price: candle.close,
    };

    const executionOutcome = await ExecutionEngineService.submitExecution(executionInput);
    const executionLatencyMs = Date.now() - t6;

    const totalLatencyMs = Date.now() - pipelineStart;

    // 8. Stage 8: Pipeline Trace Recording
    const trace: PipelineTraceDto = {
      id: `TRACE-${Date.now()}`,
      traceId: `TRC-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
      symbol,
      timeframe: '1H',
      mode,
      candle,
      marketSnapshot: {
        id: `SNAP-${symbol}-UNAVAILABLE`,
        symbol,
        currentPrice: 0,
        spread: 0,
        session: 'UNAVAILABLE',
        trend: 'NEUTRAL',
        volatility: 'UNKNOWN',
        timestamp: new Date().toISOString(),
      }, // Real-time data comes from WS; this indicates no REST snapshot available
      zones,
      strategySignal: signal as any,
      decision,
      explanation,
      executionResult: executionOutcome.result,
      stageLatenciesMs: {
        marketData: marketDataLatencyMs,
        marketStructure: marketStructureLatencyMs,
        tradingRules: tradingRulesLatencyMs,
        strategy: strategyLatencyMs,
        decision: decisionLatencyMs,
        aiDecision: aiDecisionLatencyMs,
        execution: executionLatencyMs,
        total: totalLatencyMs,
      },
      timestamp: new Date().toISOString(),
    };

    await PipelineTraceService.recordTrace(trace);
    return trace;
  }
}