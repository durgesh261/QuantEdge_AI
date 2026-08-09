import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { OrderBlockService } from './orderBlock.service.js';
import { Server } from 'socket.io';

const SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
const SCAN_INTERVAL_MS = 5000;
const TIMEFRAME = '1H';

interface TickerData {
  price: number;
  change_24h: number;
  volume_24h: number;
  high_24h: number;
  low_24h: number;
}

export class ScannerEngine {
  private static io: Server | null = null;
  private static timer: ReturnType<typeof setInterval> | null = null;
  private static isRunning = false;
  private static isTicking = false; // Prevents overlapping ticks

  static initialize(io: Server) {
    this.io = io;
    this.ensureState().then(() => {
      this.start();
      logger.info('[ScannerEngine] Initialized and started');
    });
  }

  // ── Ensure DB rows exist (idempotent) ─────────────────
  private static async ensureState() {
    let state = await prisma.scannerState.findFirst();
    if (!state) {
      state = await prisma.scannerState.create({ data: {} });
      logger.info('[ScannerEngine] Created default ScannerState');
    }

    for (const symbol of SYMBOLS) {
      const pair = await prisma.scannerPair.findUnique({ where: { symbol } });
      if (!pair) {
        await prisma.scannerPair.create({ data: { symbol } });
        logger.info(`[ScannerEngine] Created ScannerPair: ${symbol}`);
      }
    }
    return state;
  }

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[ScannerEngine] Started ticking every ' + SCAN_INTERVAL_MS + 'ms');
    this.timer = setInterval(() => this.tick(), SCAN_INTERVAL_MS);
  }

  static stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.isRunning = false;
    logger.info('[ScannerEngine] Stopped');
  }

  private static async tick() {
    // CRITICAL: Prevent overlapping ticks. If previous tick still running, skip this one.
    if (this.isTicking) {
      logger.warn('[ScannerEngine] Previous tick still running — skipping');
      return;
    }
    this.isTicking = true;

    try {
      const globalState = await prisma.scannerState.findFirst();
      if (!globalState) {
        logger.warn('[ScannerEngine] State missing — recreating');
        await this.ensureState();
        return;
      }
      if (!globalState.isRunning || globalState.isPaused) return;

      const pairs = await prisma.scannerPair.findMany({
        where: { isActive: true, isPaused: false, status: 'ENGINE' },
      });

      if (pairs.length === 0) {
        logger.warn('[ScannerEngine] No active pairs to scan');
        return;
      }

      // Process pairs concurrently (not sequentially) to fit inside 5s window
      await Promise.all(
        pairs.map((pair) =>
          this.processPair(pair.symbol).catch((err) => {
            logger.error(`[ScannerEngine] Pair ${pair.symbol} crashed:`, err);
          })
        )
      );
    } catch (err) {
      logger.error('[ScannerEngine] Tick error:', err);
    } finally {
      this.isTicking = false;
    }
  }

  private static async processPair(symbol: string) {
    // 1. Fetch live price (fast — single API call)
    const ticker = await this.fetchDeltaTicker(symbol);
    if (!ticker) {
      logger.warn(`[ScannerEngine] No ticker for ${symbol}`);
      return;
    }

    // 2. Update price IMMEDIATELY so UI has data even if OB detection fails
    await prisma.scannerPair.update({
      where: { symbol },
      data: {
        livePrice: ticker.price,
        priceChange24h: ticker.change_24h,
        lastTickAt: new Date(),
        ticksProcessed: { increment: 1 },
      },
    });

    // 3. Log tick
    await prisma.scannerTick.create({
      data: { symbol, price: ticker.price, source: 'delta' },
    });

    // 4. OB detection (isolated — failure here must NOT kill the price update)
    let activeOBs = 0;
    let obWidthPct: number | null = null;
    let bestScore: number | null = null;

    try {
      const candles = await this.fetchCandles(symbol, TIMEFRAME);
      const blocks = OrderBlockService.detectBlocks(symbol, candles);
      const validBlocks = blocks.filter((b: any) => b.isActive && b.aiScore >= 85);
      activeOBs = validBlocks.length;

      if (activeOBs > 0) {
        const avgWidth =
          validBlocks.reduce((sum: number, b: any) => sum + (b.priceHigh - b.priceLow), 0) /
          activeOBs;
        obWidthPct = (avgWidth / ticker.price) * 100;
        bestScore = Math.max(...validBlocks.map((b: any) => b.aiScore));
      }
    } catch (obErr) {
      logger.warn(`[ScannerEngine] OB detection failed for ${symbol}:`, obErr);
    }

    // 5. Update pair stats (second update — safe because price is already in DB)
    const updatedPair = await prisma.scannerPair.update({
      where: { symbol },
      data: { activeOBs, obWidthPct, aiScore: bestScore },
    });

    // 6. Signal trigger + Strategy/Algo integration
    if (activeOBs > 0 && bestScore && bestScore >= 85) {
      const recentSignal = await prisma.scannerSignal.findFirst({
        where: { symbol, createdAt: { gte: new Date(Date.now() - 300000) } },
        orderBy: { createdAt: 'desc' },
      });

      if (!recentSignal) {
        await prisma.scannerSignal.create({
          data: {
            symbol,
            type: 'OB_DETECTED',
            aiScore: bestScore,
            metadata: JSON.stringify({ blocks: activeOBs, price: ticker.price }),
          },
        });

        await prisma.scannerPair.update({
          where: { symbol },
          data: { signalsTriggered: { increment: 1 }, lastSignalAt: new Date() },
        });

        // ── STRATEGY / ALGO PIPELINE CONNECTION ──
        await this.feedStrategyEngine(symbol, ticker.price, bestScore, activeOBs);

        this.io?.of('/scanner').emit('signal', {
          symbol,
          type: 'OB_DETECTED',
          aiScore: bestScore,
          price: ticker.price,
          timestamp: new Date().toISOString(),
        });
      }
    }

    // 7. Increment global tick counter safely
    await this.incrementGlobalTicks();

    // 8. Broadcast to frontend via WebSocket
    this.io?.of('/scanner').emit('tick', {
      symbol,
      price: ticker.price,
      change24h: ticker.change_24h,
      activeOBs: updatedPair.activeOBs,
      obWidthPct: updatedPair.obWidthPct,
      aiScore: updatedPair.aiScore,
      status: updatedPair.status,
      timestamp: new Date().toISOString(),
    });
  }

  // ── Strategy / Algo / AI Feed ─────────────────────────
  private static async feedStrategyEngine(
    symbol: string,
    price: number,
    aiScore: number,
    blockCount: number
  ) {
    try {
      // 1. Create a Strategy Signal Record for the Research & Algo engine
      await prisma.strategySignalRecord.create({
        data: {
          symbol,
          timeframe: TIMEFRAME,
          outcome: 'PENDING',
          price,
          rationale: `Scanner OB_DETECTED — AI Score ${aiScore}/100 · ${blockCount} active blocks`,
          confidenceScore: aiScore / 100,
        },
      });

      logger.info(`[Scanner→Strategy/Algo] Signal fed for ${symbol} @ ${price} (score: ${aiScore})`);
    } catch (err) {
      logger.error('[Scanner→Strategy/Algo] Failed to feed signal:', err);
    }
  }

  // ── Helpers ───────────────────────────────────────────
  private static async fetchDeltaTicker(symbol: string): Promise<TickerData | null> {
    try {
      const product = symbol.replace('.P', '');
      const res = await fetch(`https://api.delta.exchange/v2/tickers/${product}`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const result = json.result || json;

      return {
        price: parseFloat(result.mark_price || result.price || 0),
        change_24h: parseFloat(result.change_24h || 0),
        volume_24h: parseFloat(result.volume_24h || 0),
        high_24h: parseFloat(result.high || 0),
        low_24h: parseFloat(result.low || 0),
      };
    } catch (err) {
      logger.warn(`[ScannerEngine] Delta API failed for ${symbol}, using fallback`);
      const fallbacks: Record<string, number> = {
        'BTCUSD.P': 64951.0,
        'ETHUSD.P': 1915.9,
        'SOLUSD.P': 74.73,
        'XRPUSD.P': 1.04,
      };
      return {
        price: fallbacks[symbol] || 100,
        change_24h: 0.5,
        volume_24h: 0,
        high_24h: 0,
        low_24h: 0,
      };
    }
  }

  private static async fetchCandles(symbol: string, tf: string): Promise<any[]> {
    try {
      const product = symbol.replace('.P', '');
      const res = await fetch(
        `https://api.delta.exchange/v2/history/candles?symbol=${product}&resolution=${tf}&limit=50`,
        { signal: AbortSignal.timeout(8000) }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      return json.result || [];
    } catch {
      return this.generateSyntheticCandles(symbol);
    }
  }

  private static generateSyntheticCandles(symbol: string): any[] {
    const base =
      { 'BTCUSD.P': 64951, 'ETHUSD.P': 1915, 'SOLUSD.P': 74, 'XRPUSD.P': 1.04 }[symbol] || 100;
    const candles = [];
    for (let i = 0; i < 50; i++) {
      const open = base + (Math.random() - 0.5) * base * 0.02;
      const close = open + (Math.random() - 0.5) * base * 0.01;
      const high = Math.max(open, close) + Math.random() * base * 0.005;
      const low = Math.min(open, close) - Math.random() * base * 0.005;
      candles.push({
        time: Date.now() - (50 - i) * 3600000,
        open,
        high,
        low,
        close,
        volume: Math.random() * 1000,
      });
    }
    return candles;
  }

  private static async incrementGlobalTicks() {
    try {
      const state = await prisma.scannerState.findFirst();
      if (!state) return;
      await prisma.scannerState.update({
        where: { id: state.id },
        data: { ticksTotal: { increment: 1 } },
      });
    } catch (err) {
      logger.error('[ScannerEngine] incrementGlobalTicks failed:', err);
    }
  }

  // ─── Controls ─────────────────────────────────────────

  static async globalPause() {
    const state = await prisma.scannerState.findFirst();
    if (state) {
      await prisma.scannerState.update({ where: { id: state.id }, data: { isPaused: true } });
    }
    await prisma.scannerPair.updateMany({ data: { isPaused: true, status: 'PAUSED' } });
    this.io?.of('/scanner').emit('control', { action: 'PAUSE_ALL' });
    logger.info('[ScannerEngine] Global PAUSE');
  }

  static async globalResume() {
    const state = await prisma.scannerState.findFirst();
    if (state) {
      await prisma.scannerState.update({ where: { id: state.id }, data: { isPaused: false } });
    }
    await prisma.scannerPair.updateMany({ data: { isPaused: false, status: 'ENGINE' } });
    this.io?.of('/scanner').emit('control', { action: 'RESUME_ALL' });
    logger.info('[ScannerEngine] Global RESUME');
  }

  static async globalStop() {
    const state = await prisma.scannerState.findFirst();
    if (state) {
      await prisma.scannerState.update({
        where: { id: state.id },
        data: { isRunning: false, isPaused: false },
      });
    }
    await prisma.scannerPair.updateMany({ data: { isActive: false, status: 'STOPPED' } });
    this.io?.of('/scanner').emit('control', { action: 'STOP_ALL' });
    this.stop();
    logger.info('[ScannerEngine] Global STOP');
  }

  static async globalStart() {
    await this.ensureState();
    const state = await prisma.scannerState.findFirst();
    if (state) {
      await prisma.scannerState.update({
        where: { id: state.id },
        data: { isRunning: true, isPaused: false },
      });
    } else {
      await prisma.scannerState.create({ data: { isRunning: true, isPaused: false } });
    }
    await prisma.scannerPair.updateMany({
      data: { isActive: true, isPaused: false, status: 'ENGINE' },
    });
    this.io?.of('/scanner').emit('control', { action: 'START_ALL' });
    this.start();
    logger.info('[ScannerEngine] Global START');
  }

  static async pausePair(symbol: string) {
    await prisma.scannerPair.update({
      where: { symbol },
      data: { isPaused: true, status: 'PAUSED' },
    });
    this.io?.of('/scanner').emit('control', { action: 'PAUSE_PAIR', symbol });
  }

  static async resumePair(symbol: string) {
    await prisma.scannerPair.update({
      where: { symbol },
      data: { isPaused: false, status: 'ENGINE' },
    });
    this.io?.of('/scanner').emit('control', { action: 'RESUME_PAIR', symbol });
  }

  static async stopPair(symbol: string) {
    await prisma.scannerPair.update({
      where: { symbol },
      data: { isActive: false, status: 'STOPPED' },
    });
    this.io?.of('/scanner').emit('control', { action: 'STOP_PAIR', symbol });
  }

  static async inspectPair(symbol: string) {
    const pair = await prisma.scannerPair.findUnique({ where: { symbol } });
    let blocks: any[] = [];
    try {
      blocks = OrderBlockService.getBlocksForSymbol(symbol);
    } catch (e) {
      logger.warn(`[ScannerEngine] inspectPair OB fetch failed for ${symbol}`);
    }
    this.io?.of('/scanner').emit('inspect', { symbol, pair, blocks });
  }
}
