import { Router } from 'express';
import { ScannerEngine } from '../modules/scanner/services/scannerEngine.service.js';
import { deltaSyncService } from '../modules/delta-exchange/index.js';
import { prisma } from '../db.js';

const router = Router();

// GET /api/v1/scanner/state — always returns valid data, never null
router.get('/state', async (_req, res) => {
  try {
    let state = await prisma.scannerState.findFirst();
    if (!state) {
      state = await prisma.scannerState.create({ data: {} });
    }

    let pairs = await prisma.scannerPair.findMany({ orderBy: { symbol: 'asc' } });

    // Fallback pairs if DB was wiped
    if (pairs.length === 0) {
      const defaults = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
      for (const sym of defaults) {
        pairs.push(await prisma.scannerPair.create({ data: { symbol: sym } }));
      }
    }

    const signals = await prisma.scannerSignal.findMany({
      where: { createdAt: { gte: new Date(Date.now() - 86400000) } },
      orderBy: { createdAt: 'desc' },
      take: 50,
    });

    const isDeltaConnected = deltaSyncService.isConnected();

    res.json({
      success: true,
      data: { global: state, pairs, signals, isDeltaConnected },
    });
  } catch (err: any) {
    console.error('[ScannerRoutes] GET /state error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// POST /api/v1/scanner/control
router.post('/control', async (req, res): Promise<any> => {
  const { action } = req.body;
  try {
    switch (action) {
      case 'PAUSE_ALL':
      case 'pause':
        await ScannerEngine.globalPause();
        break;
      case 'RESUME_ALL':
      case 'resume':
        await ScannerEngine.globalResume();
        break;
      case 'STOP_ALL':
      case 'stop':
        await ScannerEngine.globalStop();
        break;
      case 'START_ALL':
      case 'start':
        await ScannerEngine.globalStart();
        break;
      default:
        return res.status(400).json({ success: false, error: 'Invalid action' });
    }
    res.json({ success: true, action });
  } catch (err: any) {
    console.error('[ScannerRoutes] Control error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// POST /api/v1/scanner/pair/:symbol/control
router.post('/pair/:symbol/control', async (req, res): Promise<any> => {
  const { symbol } = req.params;
  const { action } = req.body;
  try {
    switch (action) {
      case 'START':
      case 'START_PAIR':
        await ScannerEngine.startPair(symbol);
        break;
      case 'PAUSE':
      case 'PAUSE_PAIR':
        await ScannerEngine.pausePair(symbol);
        break;
      case 'RESUME':
      case 'RESUME_PAIR':
        await ScannerEngine.resumePair(symbol);
        break;
      case 'STOP':
      case 'STOP_PAIR':
        await ScannerEngine.stopPair(symbol);
        break;
      case 'INSPECT':
        await ScannerEngine.inspectPair(symbol);
        break;
      default:
        return res.status(400).json({ success: false, error: 'Invalid action' });
    }
    res.json({ success: true, symbol, action });
  } catch (err: any) {
    console.error('[ScannerRoutes] Pair control error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

export default router;
