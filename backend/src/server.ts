import { createApp } from './app.js';
import { config } from './config/index.js';
import { logger } from './logger/index.js';
import { JournalAutomationService } from './modules/journal/services/journalAutomation.service.js';
import { MarketScannerService } from './modules/live-trading/services/MarketScannerService.js';
import { WebSocketServer } from './websocket/WebSocketServer.js';
import { Server as SocketIOServer } from 'socket.io';
import { setupNewsWebSocket } from './modules/news/services/wsNews.service.js';
import { ScannerEngine } from './modules/scanner/services/scannerEngine.service.js';
import { tradeAccountingTrigger } from './modules/trade-accounting/TradeAccountingTrigger.js';
import { OrderBlockWidthEngine } from './modules/indicator-engine/engines/orderBlockWidthEngine.js';
import { PersistentOBRegistry } from './modules/scanner/services/PersistentOBRegistry.js';
import { NewsService } from './modules/news/services/NewsService.js';
import { EconomicCalendarService } from './modules/news/services/EconomicCalendarService.js';
import { PositionMonitorService } from './modules/position-monitor/services/PositionMonitorService.js';
import { ShadowTriggerService } from './modules/shadow-trading/services/ShadowTriggerService.js';

const app = createApp();

JournalAutomationService.initialize();
MarketScannerService.initialize();

import { initializeExecutionModeFromPersistence } from './modules/production/production.controller.js';

Promise.all([
  OrderBlockWidthEngine.loadUsedFromDb(),
  PersistentOBRegistry.loadFromDb(),
  initializeExecutionModeFromPersistence(),
]).then(([_, __, mode]) => {
  logger.info(`[Boot] OB lifecycle and execution mode (${mode}) state restored from DB`);
}).catch((err) => {
  logger.warn({ err }, '[Boot] OB lifecycle state partial load — proceeding');
});

import { prisma } from './db.js';
import { deltaSyncService } from './modules/delta-exchange/index.js';

async function bootstrapDelta(): Promise<void> {
  try {
    const settings = await prisma.systemSettings.findUnique({
      where: { id: 'default-settings' },
    });

    const apiKey = settings?.deltaApiKey || process.env['DELTA_API_KEY'] || '';
    const apiSecret = settings?.deltaApiSecret || process.env['DELTA_API_SECRET'] || '';

    if (apiKey && apiSecret) {
      process.env['DELTA_API_KEY'] = apiKey;
      process.env['DELTA_API_SECRET'] = apiSecret;
      await deltaSyncService.updateCredentials({ apiKey, apiSecret });
      logger.info({ apiKeyPrefix: apiKey.substring(0, 6) }, 'Delta Exchange daemon initialized from configuration');
    } else {
      logger.info('Delta Exchange not yet configured. Users can input API keys in Settings.');
    }
  } catch (err) {
    logger.warn({ err }, 'Delta initialization notice');
  }
}

void bootstrapDelta();

const server = app.listen(config.port, () => {
  logger.info({
    port: config.port,
    environment: config.env,
    publicUrl: config.publicUrl,
  }, 'QuantEdge AI Backend Server Initialized');
});

setTimeout(() => {
  PositionMonitorService.start().catch(err =>
    logger.error('[PositionMonitor] Failed to start:', err)
  );
  ShadowTriggerService.start().catch(err =>
    logger.error('[ShadowTrigger] Failed to start:', err)
  );
}, 5000);

const wsServer = new WebSocketServer(server);
wsServer.initialize();

const io = new SocketIOServer(server, {
  cors: {
    origin: '*',
    methods: ['GET', 'POST']
  }
});
setupNewsWebSocket(io);
ScannerEngine.initialize(io);
tradeAccountingTrigger.initialize();

NewsService.start();
EconomicCalendarService.start();
let shutdownInProgress = false;

function handleShutdown(signal: string): void {
  // Idempotent guard — only run once per shutdown sequence
  if (shutdownInProgress) return;
  shutdownInProgress = true;

  logger.info(`Received ${signal}. Initiating graceful shutdown...`);

  // 1. Explicit Prisma disconnect — ensures DB connections are released
  //    before the process exits. Wrapped in try/catch so a failure
  //    never blocks shutdown or enables LIVE.
  //    Sanitized logging only — no credentials leaked.
  try {
    prisma.$disconnect();
  } catch (err) {
    logger.error(`[Shutdown] Prisma disconnect warning (non-fatal): ${err instanceof Error ? err.message : err}`);
  }

  // 2. WebSocket graceful shutdown
  wsServer.shutdown();

  // 3. Delta sync service stop
  deltaSyncService.stop();

  // 4. Shadow trigger stop
  ShadowTriggerService.stop();

  // 5. Position monitor stop
  PositionMonitorService.stop();

  // 6. HTTP server close — waits for in-flight requests to complete
  server.close(() => {
    logger.info('HTTP server closed successfully.');
    // 7. Release Prisma client after HTTP server is fully closed
    //    (best-effort; if already released by #1, this is a no-op)
    try {
      prisma.$disconnect().catch(() => {});
    } catch {
      // best-effort only
    }
    process.exit(0);
  });

  // 8. Force timeout if shutdown takes too long
  setTimeout(() => {
    logger.error('Forcefully shutting down server due to timeout.');
    process.exit(1);
  }, 10000);
}

process.on('SIGTERM', () => handleShutdown('SIGTERM'));
process.on('SIGINT', () => handleShutdown('SIGINT'));

// Unhandled promise rejection — log and gracefully shut down
process.on('unhandledRejection', (reason) => {
  logger.error({ reason: reason instanceof Error ? reason.message : reason }, 'Unhandled promise rejection');
  // Attempt graceful shutdown to avoid state corruption
  handleShutdown('UNHANDLED_REJECTION');
});

// Uncaught exception — log and gracefully shut down
process.on('uncaughtException', (err) => {
  logger.error({ err: err instanceof Error ? err.message : err }, 'Uncaught exception');
  // Attempt graceful shutdown to avoid state corruption
  handleShutdown('UNCAUGHT_EXCEPTION');
});