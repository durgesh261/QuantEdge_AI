import { Router } from 'express';
import { prisma } from '../db.js';
import { logger } from '../logger/index.js';

const router = Router();

/**
 * KILL SWITCH — Irreversible data wipe.
 * Requires X-Dev-Mode: true header.
 */
router.post('/kill-switch', async (req, res) => {
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
      prisma.executionJournal?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.tradeAccountingLedger?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.pendingOrder?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.position?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.orderHistory?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.tradeHistory?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.journalEntry?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // News & Macro
      prisma.newsArticle?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.macroEvent?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // Strategy & Analytics
      prisma.strategyProfile?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.analyticsSnapshot?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      prisma.scannerLog?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
      
      // Notifications
      prisma.notification?.deleteMany() ?? prisma.$queryRaw`SELECT 1`,
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
