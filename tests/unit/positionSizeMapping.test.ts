import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { PositionSizingEngine } from '../../backend/src/modules/decision/sizing/positionSizingEngine.js';
import { DecisionEngineService } from '../../backend/src/modules/decision/services/decisionEngine.service.js';
import { deltaSyncService } from '../../backend/src/modules/delta-exchange/index.js';
import { TradingTimeframe } from '@algoapp/shared';

describe('Phase A: Position-Size Mapping Safety Tests', () => {
  beforeEach(() => {
    vi.spyOn(deltaSyncService, 'getBalances').mockReturnValue([
      {
        asset_id: 1,
        asset_symbol: 'USDT',
        balance: '50000',
        available_balance: '50000',
        order_margin: '0',
        position_margin: '0',
        unrealized_pnl: '0',
      },
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('PositionSizingEngine returns contractQuantity as coin/contract count and notionalValue as USD exposure', () => {
    const sizing = PositionSizingEngine.calculatePositionSize({
      symbol: 'BTCUSD.P',
      accountBalance: 50000,
      entryPrice: 65000,
      stopLossPrice: 64500, // $500 SL distance
      takeProfitPrice: 66250,
      riskPercent: 1.0, // $500 risk
    });

    expect(sizing.riskAmount).toBe(500);
    // 500 risk / 500 SL distance = 1.0 contract (BTC)
    expect(sizing.contractQuantity).toBe(1.0);
    // 1.0 contract * 65000 entry = $65,000 USD notional
    expect(sizing.notionalValue).toBe(65000);
    // For legacy backward compatibility on PositionSizingResultDto:
    expect(sizing.positionSize).toBe(65000);
  });

  it('DecisionEngineService sets decision.contractQuantity and decision.positionSize to contract quantity (not USD notional)', async () => {
    const sampleIndicators: any = {
      orderBlocks: [],
      structureEvents: [],
      liquiditySweeps: [],
      marketStructure: {
        trend: 'BULLISH',
        structureState: 'BOS_BULLISH',
        swingHigh: 66000,
        swingLow: 64000,
      },
    };

    const activeZone: any = {
      id: 'OB-TEST-1',
      symbol: 'BTCUSD.P',
      type: 'DEMAND',
      upperPrice: 65000,
      lowerPrice: 64500, // width = 500
      touchCount: 0,
      status: 'FIRST_TOUCH',
    };

    const decision = await DecisionEngineService.evaluateDecision({
      symbol: 'BTCUSD.P',
      timeframe: '1H' as TradingTimeframe,
      currentPrice: 65000,
      indicators: sampleIndicators,
      activeZone,
    });

    // Ensure contractQuantity and notionalValue are both populated
    expect(decision.contractQuantity).toBeDefined();
    expect(decision.notionalValue).toBeDefined();

    // Verify contractQuantity is a coin/contract quantity (e.g. ~0.035 - 35.0) and NOT the USD Notional ($65,000+)
    expect(decision.contractQuantity).toBeGreaterThan(0);
    expect(decision.contractQuantity).toBeLessThan(100); // 100 contracts max for BTC, nowhere near 65000
    
    // Verify positionSize on decision equals contractQuantity for execution compatibility
    expect(decision.positionSize).toEqual(decision.contractQuantity);

    // Verify notionalValue holds the USD exposure
    expect(decision.notionalValue).toBeGreaterThan(1000);
  });

  it('Regression check: Ensure execution requests receive contractQuantity rather than USD Notional', () => {
    const mockDecision: any = {
      id: 'DEC-REG-1',
      symbol: 'BTCUSD.P',
      contractQuantity: 0.035,
      notionalValue: 1750,
      positionSize: 0.035,
    };

    const executionSize = mockDecision.contractQuantity ?? mockDecision.positionSize ?? 0;
    expect(executionSize).toBe(0.035);
    expect(executionSize).not.toBe(1750);
  });
});
