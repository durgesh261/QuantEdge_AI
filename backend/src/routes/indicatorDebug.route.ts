import { Router } from 'express';
import { LuxAlgoSMCEngine, LuxAlgoConfig } from '../modules/indicator-engine/engines/LuxAlgoSMCEngine.js';
import { CandleStoreService } from '../modules/market-data/services/candleStore.service.js';

const router = Router();

const DEFAULT_CONFIG: LuxAlgoConfig = {
  mode: 'HISTORICAL',
  style: 'COLORED',
  showInternals: true,
  showInternalBull: 'ALL',
  showInternalBear: 'ALL',
  internalFilterConfluence: false,
  showStructure: true,
  showSwingBull: 'ALL',
  showSwingBear: 'ALL',
  showInternalOrderBlocks: true,
  internalOrderBlocksSize: 5,
  showSwingOrderBlocks: false,
  swingOrderBlocksSize: 5,
  orderBlockFilter: 'ATR',
  orderBlockMitigation: 'HIGHLOW',
  swingLength: 50,
  internalLength: 5,
  eqhEqlLength: 3,
  eqhEqlThreshold: 0.1,
  showEqualHighsLows: true,
  showHighLowSwings: true,
  showPremiumDiscountZones: false,
  showTrend: false,
};

router.get('/debug/smc/:symbol', async (req, res) => {
  const symbol = req.params.symbol || 'BTCUSD.P';
  const candles = await CandleStoreService.getCandles(symbol, '1H', 300);
  const result = LuxAlgoSMCEngine.run(symbol, candles, '1H', DEFAULT_CONFIG);

  const report = result.internalOrderBlocks.map((ob) => ({
    id: ob.id,
    sourceBar: ob.createdAt,
    type: ob.type,
    high: ob.upperPrice,
    low: ob.lowerPrice,
    baseIndex: ob.baseCandleIndex,
    breakIndex: ob.breakCandleIndex,
    widthPct: ob.widthPercent,
    mitigated: ob.isMitigated,
  }));

  res.json({
    symbol,
    candleCount: candles.length,
    atr200: result.atr200,
    swingTrend: result.swingTrend,
    internalTrend: result.internalTrend,
    structureEvents: result.structureEvents.slice(-10),
    equalHighLows: result.equalHighLows,
    activeInternalOBs: report,
  });
});

export default router;
