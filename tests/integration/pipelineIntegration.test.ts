import { describe, it, expect } from 'vitest';
import { ExecutionMode, ExecutionStatus } from '@algoapp/shared';
import { TradingViewAdapterService } from '../../backend/src/modules/tradingview-adapter/services/tradingViewAdapter.service.js';
import { SystemIntegrationCoordinator } from '../../backend/src/modules/system-integration/services/systemIntegrationCoordinator.js';
import { SystemHealthAggregator } from '../../backend/src/modules/system-integration/services/systemHealthAggregator.js';

describe('System Integration & Shadow Mode End-to-End Integration Tests', () => {
  it('should process TradingView webhook, execute full 9-stage pipeline in SHADOW mode, and route execution to Paper Adapter', async () => {
    const tvService = new TradingViewAdapterService();
    const webhookPayload = {
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      open: 64000.0,
      high: 64600.0,
      low: 63900.0,
      close: 64350.0,
      volume: 1800.0,
      timestamp: '2026-08-02T21:00:00Z',
    };

    const tvResult = await tvService.receiveWebhook(webhookPayload);
    expect(tvResult.success).toBe(true);

    const trace = await SystemIntegrationCoordinator.processCandlePipeline({
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      mode: ExecutionMode.SHADOW,
      price: 64350.0,
      quantity: 0.001,
    });

    expect(trace.symbol).toBe('BTCUSD.P');
    expect(trace.mode).toBe(ExecutionMode.SHADOW);
    expect(trace.executionResult.adapter).toBe('PAPER_ADAPTER');
    expect([ExecutionStatus.FILLED, ExecutionStatus.SUBMITTED, ExecutionStatus.REJECTED]).toContain(trace.executionResult.status);
    expect(trace.explanation.shortSummary).toBeDefined();
    expect(trace.stageLatenciesMs.total).toBeGreaterThanOrEqual(0);
  });

  it('should verify deterministic output reproducibility for identical inputs', async () => {
    const input = {
      symbol: 'ETHUSD.P',
      timeframe: '1H' as const,
      mode: ExecutionMode.SHADOW,
      price: 3350.0,
      quantity: 0.5,
    };

    const trace1 = await SystemIntegrationCoordinator.processCandlePipeline(input);
    const trace2 = await SystemIntegrationCoordinator.processCandlePipeline(input);

    expect(trace1.decision.decisionState).toBe(trace2.decision.decisionState);
    expect(trace1.decision.confidenceScore).toBe(trace2.decision.confidenceScore);
    expect(trace1.strategySignal?.outcome).toBe(trace2.strategySignal?.outcome);
  });

  it('should monitor system health across all 9 pipeline modules', async () => {
    const overview = await SystemHealthAggregator.getSystemOverview(ExecutionMode.SHADOW);

    expect(overview.isShadowModeActive).toBe(true);
    expect(overview.modulesHealth.length).toBe(9);
    expect(overview.modulesHealth.every((m) => m.status === 'HEALTHY' || m.status === 'DEGRADED')).toBe(true);
  });
});
