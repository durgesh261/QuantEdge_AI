import { Router } from 'express';
import { ScannerEngine } from '../modules/scanner/services/scannerEngine.service.js';
import { prisma } from '../db.js';

const router = Router();

const FALLBACK_PAIRS = [
  { symbol: 'BTCUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'ETHUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'SOLUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
  { symbol: 'XRPUSD.P', isActive: true, isPaused: false, status: 'ENGINE', livePrice: 0, priceChange24h: 0, activeOBs: 0, obWidthPct: null, aiScore: null, ticksProcessed: 0, signalsTriggered: 0, tradesExecuted: 0 },
];

// GET /api/v1/scanner/state
// Always guarantees a valid response — creates state row if missing
router.get('/state', async (_req, res) => {
  try {
    // Guarantee a state row exists
    let state = await prisma.scannerState.findFirst();
    if (!state) {
      state = await prisma.scannerState.create({ data: {} });
    }

    const pairs = await prisma.scannerPair.findMany({ orderBy: { symbol: 'asc' } });

    const signals = await prisma.scannerSignal.findMany({
      where: { createdAt: { gte: new Date(Date.now() - 86400000) } },
      orderBy: { createdAt: 'desc' },
      take: 50,
    });

    res.json({
      success: true,
      data: {
        global: state,
        pairs: pairs.length ? pairs : FALLBACK_PAIRS,
        signals,
      },
    });
  } catch (err) {
    // Even if DB is down, return fallback so frontend never stays loading
    res.json({
      success: true,
      data: {
        global: { isRunning: true, isPaused: false, ticksTotal: 0, signalsTotal: 0, tradesTotal: 0 },
        pairs: FALLBACK_PAIRS,
        signals: [],
      },
    });
  }
});

// POST /api/v1/scanner/control — Global controls
router.post('/control', async (req, res) => {
  const { action } = req.body;

  switch (action) {
    case 'PAUSE_ALL':  await ScannerEngine.globalPause();  break;
    case 'RESUME_ALL': await ScannerEngine.globalResume(); break;
    case 'STOP_ALL':   await ScannerEngine.globalStop();   break;
    case 'START_ALL':  await ScannerEngine.globalStart();  break;
    default:
      return res.status(400).json({ success: false, error: 'Invalid action' });
  }

  res.json({ success: true, action });
});

// POST /api/v1/scanner/pair/:symbol/control — Per-pair controls
router.post('/pair/:symbol/control', async (req, res) => {
  const { symbol } = req.params;
  const { action } = req.body;

  switch (action) {
    case 'PAUSE':   await ScannerEngine.pausePair(symbol);   break;
    case 'RESUME':  await ScannerEngine.resumePair(symbol);  break;
    case 'STOP':    await ScannerEngine.stopPair(symbol);    break;
    case 'INSPECT': await ScannerEngine.inspectPair(symbol); break;
    default:
      return res.status(400).json({ success: false, error: 'Invalid action' });
  }

  res.json({ success: true, symbol, action });
});

export default router;
