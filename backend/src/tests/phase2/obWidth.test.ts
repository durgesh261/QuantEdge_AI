import { describe, it, expect } from 'vitest';
import { OrderBlockWidthEngine } from '../../modules/indicator-engine/engines/orderBlockWidthEngine.js';

describe('OrderBlockWidthEngine Phase 2 Tests', () => {
  it('should correctly calculate entry and stop loss prices based on order block width rules', () => {
    // Test 1: Bullish OB <= 0.6% width (edge entry)
    // Upper: 100, Lower: 99.5. Width: 0.5%
    const ob1 = OrderBlockWidthEngine.enrichOrderBlock(
      'ob1', 'BTC', '1H', 'BULLISH', 100, 99.5, 0, 0, false, false, 0, 'SMC', ''
    );
    expect(ob1.widthPercent).toBe(0.5);
    expect(ob1.entryPrice).toBe(100);
    expect(ob1.stopLossPrice).toBe(99.5);

    // Test 2: Bullish OB > 0.6% width (25% deep entry)
    // Upper: 100, Lower: 99. Width: 1%
    const ob2 = OrderBlockWidthEngine.enrichOrderBlock(
      'ob2', 'BTC', '1H', 'BULLISH', 100, 99, 0, 0, false, false, 0, 'SMC', ''
    );
    expect(ob2.widthPercent).toBe(1.0);
    expect(ob2.entryPrice).toBe(99.75);

    // Test 3: Bearish OB <= 0.6% width (edge entry)
    // Upper: 100.5, Lower: 100. Width: 0.5% (approx)
    const ob3 = OrderBlockWidthEngine.enrichOrderBlock(
      'ob3', 'BTC', '1H', 'BEARISH', 100.5, 100, 0, 0, false, false, 0, 'SMC', ''
    );
    expect(ob3.entryPrice).toBe(100);
    expect(ob3.stopLossPrice).toBe(100.5);

    // Test 4: Bearish OB > 0.6% width (25% deep entry)
    // Upper: 101, Lower: 100. Width: 1% (approx)
    const ob4 = OrderBlockWidthEngine.enrichOrderBlock(
      'ob4', 'BTC', '1H', 'BEARISH', 101, 100, 0, 0, false, false, 0, 'SMC', ''
    );
    expect(ob4.entryPrice).toBe(100.25);
  });
});
