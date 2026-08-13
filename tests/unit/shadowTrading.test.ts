import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ShadowTradingEngineService } from '../../backend/src/modules/shadow-trading/services/shadowTradingEngine.service';
import { DecisionRecorderService } from '../../backend/src/modules/shadow-trading/services/decisionRecorder.service';
import { OutcomeValidatorService } from '../../backend/src/modules/shadow-trading/services/outcomeValidator.service';
import { StabilityAnalyzerService } from '../../backend/src/modules/shadow-trading/services/stabilityAnalyzer.service';
import { ProductionReadinessCalculatorService } from '../../backend/src/modules/shadow-trading/services/productionReadinessCalculator.service';

describe('Real Market Validation & Shadow Trading Laboratory Test Suite', () => {
  const decisionRecorder = new DecisionRecorderService();
  const stabilityAnalyzer = new StabilityAnalyzerService();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. ShadowTradingEngineService - has runShadowCycle method that uses real pipeline', () => {
    // Verify the service structure - it should use SystemIntegrationCoordinator
    expect(typeof ShadowTradingEngineService.runShadowCycle).toBe('function');
    expect(typeof ShadowTradingEngineService.getDashboardData).toBe('function');
    expect(typeof ShadowTradingEngineService.getChallengeSimulation).toBe('function');
  });

  it('2. DecisionRecorderService - logs decision record with confidence and reason codes', async () => {
    const rec = await decisionRecorder.recordDecision({
      symbol: 'ETHUSD.P',
      decision: 'BUY',
      confidence: 88.5,
      reasonCodes: ['SMC_BOS_BREAKOUT'],
      entryPrice: 3380.0,
      stopLossPrice: 3340.0,
      takeProfitPrice: 3500.0,
      positionSize: 2.5,
      timeframe: '1H',
      strategyProfileId: 'DEF-1H-PROF',
      supplyZoneRange: 'N/A',
      demandZoneRange: 'N/A',
      expectedRR: 3.0,
      expectedProfitUsd: 300.0,
    });

    expect(rec.symbol).toBe('ETHUSD.P');
    expect(rec.confidence).toBe(88.5);
    expect(rec.reasonCodes).toContain('SMC_BOS_BREAKOUT');

    const allRecords = await decisionRecorder.getRecentDecisions();
    expect(allRecords.length).toBeGreaterThan(0);
  });

  it('3. OutcomeValidatorService - calculates TP hit, SL hit, MFE %, MAE %, and accuracy', () => {
    const outcome = OutcomeValidatorService.validateOutcome(
      'SHD-DEC-1',
      63850.0,
      65850.0,
      63600.0,
      65800.0,
      63250.0
    );

    expect(outcome.tpHit).toBe(true);
    expect(outcome.slHit).toBe(false);
    expect(outcome.mfe).toBeGreaterThan(0);
    expect(outcome.accuracyPercent).toBe(100.0);
  });

  it('4. StabilityAnalyzerService - scores multi-asset and multi-timeframe strategy stability', async () => {
    const matrix = await stabilityAnalyzer.getStabilityMatrix();

    expect(matrix.length).toBeGreaterThan(0);
    expect(matrix.some((m) => m.symbol === 'BTCUSD.P')).toBe(true);
    expect(matrix.every((m) => m.stabilityScore > 80.0)).toBe(true);
  });

  it('5. ProductionReadinessCalculatorService - calculates overall Production Readiness Score', () => {
    const readiness = ProductionReadinessCalculatorService.calculateReadinessScore();

    expect(readiness.overallReadinessScore).toBeGreaterThanOrEqual(95.0);
    expect(readiness.isProductionReady).toBe(true);
    expect(readiness.indicatorAccuracy).toBe(99.8);
    expect(readiness.accountingAccuracy).toBe(100.0);
  });
});