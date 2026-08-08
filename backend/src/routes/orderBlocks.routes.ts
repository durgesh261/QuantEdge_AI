import { Router } from 'express';
import { OrderBlockService } from '../modules/scanner/services/orderBlock.service.js';

const router = Router();

// GET /api/v1/order-blocks?symbol=BTCUSD.P
router.get('/', (req, res) => {
  const { symbol } = req.query;
  
  if (symbol) {
    const blocks = OrderBlockService.getBlocksForSymbol(symbol as string);
    res.json({ 
      success: true, 
      data: blocks,
      meta: { symbol, count: blocks.length, scannedAt: new Date().toISOString() }
    });
    return;
  }

  const allBlocks = OrderBlockService.getAllBlocks();
  res.json({ 
    success: true, 
    data: allBlocks,
    meta: { count: allBlocks.length }
  });
});

// POST /api/v1/order-blocks/scan — Triggered by your scanner engine
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
