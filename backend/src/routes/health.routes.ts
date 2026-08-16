import { Router } from 'express';
import { prisma } from '../db.js';
import { logger } from '../logger/index.js';

const router = Router();

// ── Liveness: is the backend process alive? ────────────────────────────────
const liveness = {
  status: 'healthy',
  timestamp: new Date().toISOString(),
  uptime: process.uptime(),
};

// ── Readiness: can the trading algorithm safely operate? ──────────────────
const readiness = {
  status: 'HEALTHY' as 'HEALTHY' | 'DEGRADED' | 'NOT_READY',
  executionMode: 'PAPER' as 'PAPER' | 'LIVE',
  liveAuthorized: false,
  scannerRunning: false,
  scannerLastHeartbeat: '' as string,
  marketDataFresh: false,
  database: 'unknown' as 'connected' | 'disconnected',
  deltaExchange: 'UNCONFIGURED' as 'CONNECTED' | 'DEGRADED' | 'ERROR' | 'UNCONFIGURED',
  deltaRestLastSuccess: '' as string,
};

router.get('/', async (_req, res) => {
  // Liveness — always healthy if process is alive
  res.status(200).json({ ...liveness, type: 'liveness' });
});

router.get('/ready', async (_req, res) => {
  // Readiness — evaluate all subsystems from persisted state
  try {
    // ── Database ───────────────────────────────────────────────
    let database: 'connected' | 'disconnected' = 'disconnected';
    try {
      await prisma.$queryRaw`SELECT 1`;
      database = 'connected';
    } catch {
      database = 'disconnected';
    }

    // ── Delta Exchange WebSocket health (lightweight check) ───
    // Use the same HEAD check pattern as the existing /health endpoint.
    let deltaExchange: 'CONNECTED' | 'DEGRADED' | 'ERROR' | 'UNCONFIGURED' = 'UNCONFIGURED';
    try {
      const deltaRes = await fetch('https://api.delta.exchange/v2/products', {
        method: 'HEAD',
        signal: AbortSignal.timeout(5000),
      });
      if (deltaRes.ok) {
        deltaExchange = 'CONNECTED';
      } else if (deltaRes.status >= 500) {
        deltaExchange = 'ERROR';
      } else {
        deltaExchange = 'DEGRADED';
      }
    } catch {
      deltaExchange = 'UNCONFIGURED' as const;
    }

    // ── Scanner state ─────────────────────────────────────────
    const scannerState = await prisma.scannerState.findFirst();
    const scannerRunning = scannerState?.isRunning === true;
    // lastHeartbeat added in C.12; fallback to updatedAt if missing
    const scannerLastHeartbeat =
      scannerState?.lastHeartbeat instanceof Date
        ? scannerState.lastHeartbeat.toISOString()
        : scannerState?.lastHeartbeat
          ? String(scannerState.lastHeartbeat)
          : '';

    // ── Determine overall readiness ─────────────────────────────
    let readyStatus: 'HEALTHY' | 'DEGRADED' | 'NOT_READY' = 'HEALTHY';
    const checks: Record<string, boolean> = {
      databaseConnected: database === 'connected',
      deltaExchangeHealthy: deltaExchange === 'CONNECTED',
      marketDataFresh: false, // no automatic stale-tick threshold in this release
      scannerRunning: scannerRunning,
    };

    const allPassed = Object.values(checks).every((v) => v === true);
    if (!allPassed) {
      readyStatus = 'DEGRADED';
      if (!scannerRunning) {
        readyStatus = 'NOT_READY';
      }
    }

    // Update readiness state
    ({
      status: readiness.status,
      executionMode: readiness.executionMode,
      liveAuthorized: readiness.liveAuthorized,
      scannerRunning: readiness.scannerRunning,
      scannerLastHeartbeat: readiness.scannerLastHeartbeat,
      marketDataFresh: readiness.marketDataFresh,
      database: readiness.database,
      deltaExchange: readiness.deltaExchange,
      deltaRestLastSuccess: readiness.deltaRestLastSuccess,
    } = {
      status: readyStatus,
      executionMode: 'PAPER' as const,
      liveAuthorized: process.env.ALLOW_LIVE_TRADING === 'true',
      scannerRunning,
      scannerLastHeartbeat,
      marketDataFresh: false,
      database,
      deltaExchange,
      deltaRestLastSuccess: '',
    });
  } catch (err) {
    logger.error({ err }, '[Readiness] Failed to evaluate readiness');
    readiness.status = 'NOT_READY' as 'HEALTHY' | 'DEGRADED' | 'NOT_READY';
    readiness.database = 'disconnected' as 'connected' | 'disconnected';
  }

  res.status(200).json({
    status: readiness.status,
    executionMode: readiness.executionMode,
    liveAuthorized: readiness.liveAuthorized,
    scannerRunning: readiness.scannerRunning,
    scannerLastHeartbeat: readiness.scannerLastHeartbeat,
    marketDataFresh: readiness.marketDataFresh,
    database: readiness.database,
    deltaExchange: readiness.deltaExchange,
    deltaRestLastSuccess: readiness.deltaRestLastSuccess,
    type: 'readiness',
  });
});

export default router;