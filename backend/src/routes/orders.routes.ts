import { Router } from 'express';
import { prisma } from '../db.js';

const router = Router();

// Delta state mirror (set by frontend toggle)
let deltaEnabled = false;

router.post('/delta-state', (req, res) => {
  const { enabled } = req.body;
  deltaEnabled = !!enabled;
  console.log('[Delta State]', deltaEnabled ? 'ENABLED' : 'DISABLED');
  res.json({ success: true, deltaEnabled });
});

router.get('/delta-state', (_req, res) => {
  res.json({ success: true, deltaEnabled });
});

router.post('/', async (req, res) => {
  const { symbol, side, type, size, leverage, price, stop_loss, take_profit, source } = req.body;

  // Gate 1: Delta must be ON
  if (!deltaEnabled) {
    return res.status(403).json({
      success: false,
      error: 'DELTA_OFF',
      message: 'Trading is disabled. Enable Delta connection to place orders.',
    });
  }

  // Gate 2: If source is 'manual' and algo is running, block it
  // The frontend sends source: 'manual' or 'algo'
  const algoRunning = await prisma.scannerState.findFirst().then(s => s?.isRunning && !s?.isPaused);
  
  if (source === 'manual' && algoRunning) {
    return res.status(403).json({
      success: false,
      error: 'ALGO_ACTIVE',
      message: 'Manual execution is disabled while Algo Trading is active.',
    });
  }

  // TODO: Integrate with Delta Exchange API
  console.log('[ORDER]', { symbol, side, type, size, leverage, price, stop_loss, take_profit, source });

  res.json({
    success: true,
    data: {
      order_id: `order-${Date.now()}`,
      status: 'PENDING',
      symbol,
      side,
      size,
      price: price || 'MARKET',
      source: source || 'manual',
    },
  });
});

export default router;
