import { Router } from 'express';
import { OrderBlockService } from '../modules/scanner/services/orderBlock.service.js';
import { PersistentOBRegistry } from '../modules/scanner/services/PersistentOBRegistry.js';
import { CandleStoreService } from '../modules/market-data/services/candleStore.service.js';
import { IndicatorEngineService } from '../modules/indicator-engine/services/indicatorEngine.service.js';
import { OrderBlockWidthEngine } from '../modules/indicator-engine/engines/orderBlockWidthEngine.js';

const router = Router();

// GET /api/v1/order-blocks?symbol=BTCUSD.P
// Returns ONLY active canonical Order Blocks from the PersistentOBRegistry.
// The registry is the source of truth: it contains ALL historically-detected
// untouched OBs, not just what the latest indicator tick produced.
router.get('/', async (req, res) => {
  const symbol = (req.query.symbol as string) || '';

  if (symbol) {
    // Primary source: persistent registry (remembers untouched historical OBs)
    let activeOBs = PersistentOBRegistry.getActive(symbol);

    if (activeOBs.length === 0) {
      // Registry is empty for this symbol (e.g. first request after restart).
      // Seed it from a fresh indicator run, then return registry result.
      try {
        const candles = await CandleStoreService.getCandles(symbol, '1H', 200);
        if (candles.length >= 10) {
          const indicators = IndicatorEngineService.computeIndicators(candles, '1H', symbol);
          PersistentOBRegistry.addAll(symbol, indicators.orderBlocks || []);
          // Structural break check on latest candle
          if (candles.length > 0) {
            PersistentOBRegistry.checkAndInvalidate(symbol, candles[candles.length - 1]!);
          }
          activeOBs = PersistentOBRegistry.getActive(symbol);
          OrderBlockService.syncFromIndicators(symbol, activeOBs, indicators.zoneScores);
        }
      } catch (_err) {
        // Fall through — return empty
      }
    }

    // Double-filter: cross-check against width engine used cache
    const filtered = activeOBs.filter(
      (ob: any) => !OrderBlockWidthEngine.isUsed(ob.id) && !PersistentOBRegistry.isConsumed(ob.id)
    );

    res.json({
      success: true,
      data: filtered,
      meta: {
        symbol,
        count: filtered.length,
        scannedAt: new Date().toISOString(),
        canonical: true,
        registry: PersistentOBRegistry.stats(),
      }
    });
    return;
  }

  // No symbol — return all in-memory blocks
  const allBlocks = OrderBlockService.getAllBlocks();
  res.json({ success: true, data: allBlocks, meta: { count: allBlocks.length } });
});

// POST /api/v1/order-blocks/scan
router.post('/scan', async (req, res) => {
  try {
    const { symbol, candles } = req.body;
    if (!symbol || !Array.isArray(candles)) {
      res.status(400).json({ success: false, error: 'symbol and candles[] required' });
      return;
    }
    const blocks = await OrderBlockService.updateFromScanner(symbol, candles);
    res.json({ success: true, data: blocks });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// GET /api/v1/order-blocks/registry-stats — diagnostics endpoint
router.get('/registry-stats', (_req, res) => {
  res.json({ success: true, data: PersistentOBRegistry.stats() });
});

export default router;
