// backend/src/routes/indicatorDebug.route.ts
import { Router } from 'express';
import { LuxAlgoSMCEngine } from '../modules/indicator-engine/engines/LuxAlgoSMCEngine.js';
import { CandleStoreService } from '../modules/market-data/services/candleStore.service.js';
import { CanonicalOBRegistry } from '../modules/indicator-engine/services/canonicalOBRegistry.js';

const router = Router();

/**
 * GET /api/debug/smc/:symbol
 * 
 * Returns the current SMC indicator state for validation against TradingView.
 * Compare these values with your TradingView LuxAlgo indicator output.
 */
router.get('/debug/smc/:symbol', async (req, res) => {
  try {
    const symbol = req.params.symbol;
    const candles = await CandleStoreService.getCandles(symbol, '1H', 300);

    if (!candles || candles.length < 50) {
      res.status(400).json({ error: `Need >= 50 candles, got ${candles?.length ?? 0}` });
      return;
    }

    const result = LuxAlgoSMCEngine.run(symbol, candles, '1H');
    const registryActive = CanonicalOBRegistry.getActive(symbol);

    // Build detailed comparison report
    const report = {
      symbol,
      candleCount: candles.length,
      lastCandle: candles[candles.length - 1],
      atr200: result.atr200,
      swingTrend: result.swingTrend,
      internalTrend: result.internalTrend,

      // Structure events (last 10)
      recentStructureEvents: result.structureEvents.slice(-10).map(e => ({
        index: e.index,
        time: e.time,
        type: e.type,
        direction: e.direction,
        isInternal: e.isInternal,
        brokenLevel: e.brokenLevel,
      })),

      // Internal OBs (what TradingView shows as blue/green boxes)
      internalOrderBlocks: result.internalOrderBlocks.map(ob => ({
        id: ob.id,
        type: ob.type,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
        widthPercent: ob.widthPercent,
        baseCandleIndex: ob.baseCandleIndex,
        breakCandleIndex: ob.breakCandleIndex,
        sourceBarTime: ob.createdAt,
      })),

      // Registry state (what the scanner actually uses)
      registryActiveOBs: registryActive.map((e: any) => ({
        id: e.id,
        direction: e.direction,
        upperPrice: e.upperPrice,
        lowerPrice: e.lowerPrice,
        status: e.status,
        touched: e.touched,
        firstTouchPrice: e.firstTouchPrice,
      })),

      // Pivot points
      recentPivotsInternal: result.pivotsInternal.slice(-5),
      recentPivotsSwing: result.pivotsSwing.slice(-5),
    };

    res.json(report);
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

/**
 * GET /api/debug/trace/:symbol
 * 
 * Step-by-step trace of the last 30 bars showing:
 * - Bar time, close
 * - Leg detection result
 * - Pivot detection
 * - Structure break detection
 * - OB creation
 * - Mitigation
 */
router.get('/debug/trace/:symbol', async (req, res) => {
  try {
    const symbol = req.params.symbol;
    const candles = await CandleStoreService.getCandles(symbol, '1H', 300);

    if (!candles || candles.length < 50) {
      res.status(400).json({ error: `Need >= 50 candles, got ${candles?.length ?? 0}` });
      return;
    }

    const result = LuxAlgoSMCEngine.run(symbol, candles, '1H');

    // Build trace of last 30 bars
    const traceStart = Math.max(0, candles.length - 30);
    const trace = [];

    for (let i = traceStart; i < candles.length; i++) {
      const c = candles[i]!;
      const relatedEvents = result.structureEvents.filter(e => e.index === i);
      const relatedOBs = result.internalOrderBlocks.filter(ob => ob.breakCandleIndex === i);

      trace.push({
        index: i,
        time: c.timestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        events: relatedEvents.map(e => ({ type: e.type, direction: e.direction, isInternal: e.isInternal })),
        newOBs: relatedOBs.map(ob => ({ type: ob.type, high: ob.upperPrice, low: ob.lowerPrice })),
      });
    }

    res.json({ symbol, trace, totalInternalOBs: result.internalOrderBlocks.length });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

export default router;
