import { Router } from 'express';
import { prisma } from '../db.js';

const router = Router();

// GET /api/v1/portfolio/account-state — Real account equity & margin
router.get('/account-state', async (_req, res) => {
  try {
    // 1. Get the active trading account (paper or live)
    const account = await prisma.tradingAccount.findFirst({
      where: { isActive: true },
      orderBy: { updatedAt: 'desc' },
    });

    // 2. Calculate open position margin usage
    const positions = await prisma.position.findMany({
      where: { status: 'OPEN' },
    });

    const usedMargin = positions.reduce((sum, pos) => sum + (pos.marginUsed || 0), 0);
    const unrealizedPnl = positions.reduce((sum, pos) => sum + (pos.unrealizedPnl || 0), 0);

    // 3. Compute real values
    const balance = account?.balance || 0;
    const equity = balance + unrealizedPnl;
    const availableMargin = Math.max(0, equity - usedMargin);

    res.json({
      success: true,
      data: {
        balance,
        equity,
        usedMargin,
        availableMargin,
        unrealizedPnl,
        openPositions: positions.length,
      },
    });
  } catch (err: any) {
    console.error('[Portfolio] account-state error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

export default router;
