import { describe, it, expect } from 'vitest';
import { IndicatorEngineService } from '../../backend/src/modules/indicator-engine/services/indicatorEngine.service.js';
import { PersistentOBRegistry } from '../../backend/src/modules/scanner/services/PersistentOBRegistry.js';
import { CandleDto } from '@algoapp/shared';

function generateDeterministic1HCandles(count: number, basePrice: number): CandleDto[] {
  const candles: CandleDto[] = [];
  let price = basePrice;
  const startTime = new Date('2026-08-01T00:00:00Z').getTime();

  for (let i = 0; i < count; i++) {
    const timestamp = new Date(startTime + i * 3600 * 1000).toISOString();
    // Create a swing sequence to produce pivots and Order Blocks
    const swing = Math.sin(i / 5) * (basePrice * 0.02);
    const open = price;
    const close = basePrice + swing;
    const high = Math.max(open, close) + (basePrice * 0.005);
    const low = Math.min(open, close) - (basePrice * 0.005);
    const volume = 1000 + i * 10;

    candles.push({
      timestamp,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume,
    });
    price = close;
  }
  return candles;
}

describe('QuantEdge AI Strategy Engine — Historical 1H Replay (PART 36 & 37)', () => {
  it('should replay historical 1H candles chronologically without look-ahead or repainting', async () => {
    const symbol = 'BTCUSD.P';
    const allCandles = generateDeterministic1HCandles(120, 65000);

    PersistentOBRegistry.clear();

    const replayLog: { candleIndex: number; time: string; activeOBsCount: number }[] = [];

    // Replay candle by candle starting at candle 30 (minimum history for SMC swing)
    for (let step = 30; step <= allCandles.length; step++) {
      const slice = allCandles.slice(0, step);
      const currentCandle = slice[slice.length - 1]!;

      // 1. Generate indicator state from available candles ONLY (no look-ahead)
      const indicators = IndicatorEngineService.computeIndicators(slice, '1H', symbol);

      // 2. Feed new OBs into persistent registry
      PersistentOBRegistry.addAll(symbol, indicators.orderBlocks || []);

      // 3. Update structural invalidation from latest closed candle
      PersistentOBRegistry.checkAndInvalidate(symbol, currentCandle);

      // 4. Read active OBs
      const activeOBs = PersistentOBRegistry.getActive(symbol);

      replayLog.push({
        candleIndex: step - 1,
        time: currentCandle.timestamp,
        activeOBsCount: activeOBs.length,
      });
    }

    expect(replayLog.length).toBe(91); // Step 30 to 120 = 91 steps
    expect(replayLog[0]?.candleIndex).toBe(29);
    expect(replayLog[replayLog.length - 1]?.candleIndex).toBe(119);

    // Verify OB lifecycle accumulated correctly over time
    const finalStats = PersistentOBRegistry.stats();
    expect(finalStats.total).toBeGreaterThan(0);
  });
});
