import { Router } from 'express';
import { ScannerEngine } from '../modules/scanner/services/scannerEngine.service.js';
import { prisma } from '../db.js';

const router = Router();

// GET /api/v1/scanner/state — Global state + all pairs
router.get('/state', async (_req, res) => {
  const [state, pairs] = await Promise.all([
    prisma.scannerState.findFirst(),
    prisma.scannerPair.findMany({ orderBy: { symbol: 'asc' } }),
  ]);

  const signals = await prisma.scannerSignal.findMany({
    where: { createdAt: { gte: new Date(Date.now() - 86400000) } }, // Last 24h
    orderBy: { createdAt: 'desc' },
    take: 50,
  });

  res.json({
    success: true,
    data: {
      global: state,
      pairs,
      signals,
    },
  });
});

// POST /api/v1/scanner/control — Global controls
router.post('/control', async (req, res) => {
  const { action } = req.body; // PAUSE_ALL, RESUME_ALL, STOP_ALL, START_ALL

  switch (action) {
    case 'PAUSE_ALL':
      await ScannerEngine.globalPause();
      break;
    case 'RESUME_ALL':
      await ScannerEngine.globalResume();
      break;
    case 'STOP_ALL':
      await ScannerEngine.globalStop();
      break;
    case 'START_ALL':
      await ScannerEngine.globalStart();
      break;
    default:
      return res.status(400).json({ success: false, error: 'Invalid action' });
  }

  res.json({ success: true, action });
});

// POST /api/v1/scanner/pair/:symbol/control — Per-pair controls
router.post('/pair/:symbol/control', async (req, res) => {
  const { symbol } = req.params;
  const { action } = req.body; // PAUSE, RESUME, STOP, INSPECT

  switch (action) {
    case 'PAUSE':
      await ScannerEngine.pausePair(symbol);
      break;
    case 'RESUME':
      await ScannerEngine.resumePair(symbol);
      break;
    case 'STOP':
      await ScannerEngine.stopPair(symbol);
      break;
    case 'INSPECT':
      await ScannerEngine.inspectPair(symbol);
      break;
    default:
      return res.status(400).json({ success: false, error: 'Invalid action' });
  }

  res.json({ success: true, symbol, action });
});

export default router;
