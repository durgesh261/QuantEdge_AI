import { Router } from 'express';
import { prisma } from '../db.js';
import { logger } from '../logger/index.js';

const router = Router();

/**
 * KILL SWITCH — Irreversible data wipe.
 * Requires X-Dev-Mode: true header.
 */
router.post('/kill-switch', async (req, res): Promise<any> => {
  const devMode = req.headers['x-dev-mode'] === 'true';
  if (!devMode) {
    return res.status(403).json({ 
      success: false, 
      error: 'Kill Switch is only available in Developer Mode' 
    });
  }

  try {
    logger.warn('[KILL SWITCH] Initiating full data wipe...');

    await prisma.$transaction([
      // Trading & Execution
      (prisma as any).executionJournal?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).tradeAccountingLedger?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).pendingOrder?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).position?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).orderHistory?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).tradeHistory?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).journalEntry?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // News & Macro
      (prisma as any).newsArticle?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).macroEvent?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // Strategy & Analytics
      (prisma as any).strategyProfile?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).analyticsSnapshot?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      (prisma as any).scannerLog?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // Notifications
      (prisma as any).notification?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
    ].filter(Boolean));

    logger.warn('[KILL SWITCH] All application data wiped.');

    res.json({ 
      success: true, 
      message: 'Kill Switch activated. All trading data, history, and settings have been permanently deleted.',
      timestamp: new Date().toISOString()
    });
  } catch (err: any) {
    logger.error('[KILL SWITCH] Error:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

export default router;
