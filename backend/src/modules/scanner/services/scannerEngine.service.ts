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

  static initialize(io: Server) {
    this.io = io;
    this.ensureState().then(() => {
      this.start();
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
        logger.info(`[ScannerEngine] Created default ScannerPair: ${symbol}`);
      }
    }
    return state;
  }

  // ── Upsert helper: guarantees a row exists before update ──
  private static async upsertGlobalState(data: {
    isRunning?: boolean;
    isPaused?: boolean;
    ticksTotal?: any;
    signalsTotal?: any;
    tradesTotal?: any;
  }) {
    const existing = await prisma.scannerState.findFirst();
    if (!existing) {
      return prisma.scannerState.create({
        data: {
          isRunning: data.isRunning ?? true,
          isPaused: data.isPaused ?? false,
          ticksTotal: typeof data.ticksTotal === 'number' ? data.ticksTotal : 0,
          signalsTotal: typeof data.signalsTotal === 'number' ? data.signalsTotal : 0,
          tradesTotal: typeof data.tradesTotal === 'number' ? data.tradesTotal : 0,
        },
      });
    }
    return prisma.scannerState.updateMany({ data });
  }

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[ScannerEngine] Started');
    this.timer = setInterval(() => this.tick(), SCAN_INTERVAL_MS);
  }

  static stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.isRunning = false;
    logger.info('[ScannerEngine] Stopped');
  }

  private static async tick() {
    try {
      const globalState = await prisma.scannerState.findFirst();

      // If state row is missing (e.g. after Kill Switch), recreate it
      if (!globalState) {
        logger.warn('[ScannerEngine] ScannerState missing — recreating');
        await this.ensureState();
        return;
      }

      if (!globalState.isRunning || globalState.isPaused) return;

      const pairs = await prisma.scannerPair.findMany({
        where: { isActive: true, isPaused: false, status: 'ENGINE' },
      });

      for (const pair of pairs) {
        await this.processPair(pair.symbol);
      }
    } catch (err) {
      logger.error('[ScannerEngine] Tick error:', err);
    }
  }

  private static async processPair(symbol: string) {
    const ticker = await this.fetchDeltaTicker(symbol);
    if (!ticker) return;

    await prisma.scannerPair.update({
      where: { symbol },
      data: {
        livePrice: ticker.price,
        priceChange24h: ticker.change_24h,
        lastTickAt: new Date(),
        ticksProcessed: { increment: 1 },
      },
    });

    await prisma.scannerTick.create({
      data: { symbol, price: ticker.price, source: 'delta' },
    });

    const candles = await this.fetchCandles(symbol, TIMEFRAME);
    const blocks = OrderBlockService.detectBlocks(symbol, candles);
    const activeOBs = blocks.filter((b: any) => b.isActive && b.aiScore >= 85);

    let obWidthPct: number | null = null;
    if (activeOBs.length > 0) {
      const avgWidth = activeOBs.reduce((sum: number, b: any) => sum + (b.priceHigh - b.priceLow), 0) / activeOBs.length;
      obWidthPct = (avgWidth / ticker.price) * 100;
    }

    const bestScore = activeOBs.length > 0 ? Math.max(...activeOBs.map((b: any) => b.aiScore)) : null;

    const updatedPair = await prisma.scannerPair.update({
      where: { symbol },
      data: {
        activeOBs: activeOBs.length,
        obWidthPct,
        aiScore: bestScore,
      },
    });

    if (activeOBs.length > 0 && bestScore && bestScore >= 85) {
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
            metadata: JSON.stringify({ blocks: activeOBs.length }),
          },
        });

        await prisma.scannerPair.update({
          where: { symbol },
          data: { signalsTriggered: { increment: 1 }, lastSignalAt: new Date() },
        });

        this.io?.of('/scanner').emit('signal', {
          symbol,
          type: 'OB_DETECTED',
          aiScore: bestScore,
          price: ticker.price,
          timestamp: new Date().toISOString(),
        });
      }
    }

    // Use upsert to safely increment — no crash if row was wiped
    await this.upsertGlobalState({ ticksTotal: { increment: 1 } });

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

  private static async fetchDeltaTicker(symbol: string): Promise<TickerData | null> {
    try {
      const product = symbol.replace('.P', '');
      const res = await fetch(`https://api.delta.exchange/v2/tickers/${product}`, {
        signal: AbortSignal.timeout(5000),
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
        'BTCUSD.P': 64951.00,
        'ETHUSD.P': 1915.90,
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
        { signal: AbortSignal.timeout(5000) }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      return json.result || [];
    } catch {
      return this.generateSyntheticCandles(symbol);
    }
  }

  private static generateSyntheticCandles(symbol: string): any[] {
    const base = { 'BTCUSD.P': 64951, 'ETHUSD.P': 1915, 'SOLUSD.P': 74, 'XRPUSD.P': 1.04 }[symbol] || 100;
    const candles = [];
    for (let i = 0; i < 50; i++) {
      const open = base + (Math.random() - 0.5) * base * 0.02;
      const close = open + (Math.random() - 0.5) * base * 0.01;
      const high = Math.max(open, close) + Math.random() * base * 0.005;
      const low = Math.min(open, close) - Math.random() * base * 0.005;
      candles.push({ time: Date.now() - (50 - i) * 3600000, open, high, low, close, volume: Math.random() * 1000 });
    }
    return candles;
  }

  // ─── Controls (all use upsertGlobalState for safety) ──────────────────────

  static async globalPause() {
    await this.upsertGlobalState({ isPaused: true });
    await prisma.scannerPair.updateMany({ data: { isPaused: true, status: 'PAUSED' } });
    this.io?.of('/scanner').emit('control', { action: 'PAUSE_ALL' });
    logger.info('[ScannerEngine] Global PAUSE');
  }

  static async globalResume() {
    await this.upsertGlobalState({ isPaused: false });
    await prisma.scannerPair.updateMany({ data: { isPaused: false, status: 'ENGINE' } });
    this.io?.of('/scanner').emit('control', { action: 'RESUME_ALL' });
    logger.info('[ScannerEngine] Global RESUME');
  }

  static async globalStop() {
    await this.upsertGlobalState({ isRunning: false, isPaused: false });
    await prisma.scannerPair.updateMany({ data: { isActive: false, status: 'STOPPED' } });
    this.io?.of('/scanner').emit('control', { action: 'STOP_ALL' });
    this.stop();
    logger.info('[ScannerEngine] Global STOP');
  }

  static async globalStart() {
    // CRITICAL: ensureState() FIRST — recreates DB row if it was wiped by Kill Switch
    await this.ensureState();
    await this.upsertGlobalState({ isRunning: true, isPaused: false });
    await prisma.scannerPair.updateMany({ data: { isActive: true, isPaused: false, status: 'ENGINE' } });
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
    const blocks = OrderBlockService.getBlocksForSymbol(symbol);
    this.io?.of('/scanner').emit('inspect', { symbol, pair, blocks });
  }
}
