import { Router } from 'express';
import { OrderBlockService } from '../modules/scanner/services/orderBlock.service.js';
import { CandleStoreService } from '../modules/market-data/services/candleStore.service.js';
import { IndicatorEngineService } from '../modules/indicator-engine/services/indicatorEngine.service.js';
import { OrderBlockWidthEngine } from '../modules/indicator-engine/engines/orderBlockWidthEngine.js';

const router = Router();

// GET /api/v1/order-blocks?symbol=BTCUSD.P
// Returns ONLY active canonical Order Blocks (merged, not used/mitigated/invalidated).
router.get('/', async (req, res) => {
  const symbol = (req.query.symbol as string) || '';

  if (symbol) {
    try {
      const candles = await CandleStoreService.getCandles(symbol, '1H', 200);
      if (candles.length >= 10) {
        const indicators = IndicatorEngineService.computeIndicators(candles, '1H', symbol);
        // indicators.orderBlocks is already canonical (merged + used-filtered).
        // Double-filter here as safety net against any stale in-memory state.
        const activeOBs = (indicators.orderBlocks || []).filter(
          (ob: any) => !ob.isMitigated && !ob.isInvalidated && !ob.isUsed
            && !OrderBlockWidthEngine.isUsed(ob.id)
        );
        // Sync to in-memory cache for scanner
        OrderBlockService.syncFromIndicators(symbol, activeOBs, indicators.zoneScores);

        res.json({
          success: true,
          data: activeOBs,
          meta: { symbol, count: activeOBs.length, scannedAt: new Date().toISOString(), canonical: true }
        });
        return;
      }
    } catch (_err) {
      // Fall through to empty response on error — never fake OBs
    }

    res.json({ success: true, data: [], meta: { symbol, count: 0, scannedAt: new Date().toISOString() } });
    return;
  }

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

export default router;
