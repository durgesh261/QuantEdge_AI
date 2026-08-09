import { describe, it, expect } from 'vitest';
import { PositionSizingEngine } from '../../../src/modules/decision/sizing/positionSizingEngine.js';

describe('Phase 3: Position Sizing Engine', () => {
  it('should calculate 35% risk and exactly 60% TP for BUY', () => {
    const result = PositionSizingEngine.calculatePositionSize({
      symbol: 'BTCUSD',
      accountBalance: 1000,
      entryPrice: 50000,
      stopLossPrice: 40000,
      takeProfitPrice: 60000, // Dummy
      riskPercent: 35.0,
      maxLeverageCap: 100,
    });

    // Risk should be 350 (35% of 1000)
    expect(result.riskAmount).toBeCloseTo(350);

    // SL distance = 10000. Position size = Risk / SL_distance = 350 / 10000 = 0.035
    expect(result.contractQuantity).toBeCloseTo(0.035);

    // Leverage = (Size * Entry) / AccountBalance = (0.035 * 50000) / 1000 = 1.75
    expect(result.leverage).toBeCloseTo(1.75);

    // TP profit = 600 (60% of 1000). Price move = 600 / 0.035 = 17142.857
    // TP price = 50000 + 17142.857 = 67142.857
    expect(result.takeProfitPrice).toBeCloseTo(67142.857, 3);
  });

  it('should cap leverage at 100x and adjust risk if necessary', () => {
    const result = PositionSizingEngine.calculatePositionSize({
      symbol: 'ETHUSD',
      accountBalance: 1000,
      entryPrice: 3000,
      stopLossPrice: 2990,
      takeProfitPrice: 3000,
      riskPercent: 35.0,
      maxLeverageCap: 100,
    });

    expect(result.leverage).toBe(100);
  });
});

