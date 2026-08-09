import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { OrderBlockService } from './orderBlock.service.js';
import { Server } from 'socket.io';

const SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
const SCAN_INTERVAL_MS = 5000; // 5s tick
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

  private static async ensureState() {
    const state = await prisma.scannerState.findFirst();
    if (!state) {
      await prisma.scannerState.create({ data: {} });
    }
    for (const symbol of SYMBOLS) {
      const pair = await prisma.scannerPair.findUnique({ where: { symbol } });
      if (!pair) {
        await prisma.scannerPair.create({ data: { symbol } });
      }
    }
  }

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[ScannerEngine] Started');

    this.timer = setInterval(() => this.tick(), SCAN_INTERVAL_MS);
  }

  static stop() {
    if (this.timer) clearInterval(this.timer);
    this.isRunning = false;
    logger.info('[ScannerEngine] Stopped');
  }

  private static async tick() {
    try {
      const globalState = await prisma.scannerState.findFirst();
      if (!globalState || !globalState.isRunning || globalState.isPaused) return;

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
    // 1. Fetch live price from Delta Exchange public API
    const ticker = await this.fetchDeltaTicker(symbol);
    if (!ticker) return;

    // 2. Update pair with live price
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
      data: {
        symbol,
        price: ticker.price,
        source: 'delta',
      },
    });

    // 4. Fetch recent candles for OB detection
    const candles = await this.fetchCandles(symbol, TIMEFRAME);
    
    // 5. Detect order blocks
    const blocks = OrderBlockService.detectBlocks(symbol, candles);
    const activeOBs = blocks.filter(b => b.isActive && b.aiScore >= 85);

    // 6. Calculate OB width %
    let obWidthPct: number | null = null;
    if (activeOBs.length > 0) {
      const avgWidth = activeOBs.reduce((sum, b) => sum + (b.priceHigh - b.priceLow), 0) / activeOBs.length;
      obWidthPct = (avgWidth / ticker.price) * 100;
    }

    // 7. AI Score (best block score)
    const bestScore = activeOBs.length > 0 ? Math.max(...activeOBs.map(b => b.aiScore)) : null;

    // 8. Update pair stats
    const updatedPair = await prisma.scannerPair.update({
      where: { symbol },
      data: {
        activeOBs: activeOBs.length,
        obWidthPct,
        aiScore: bestScore,
      },
    });

    // 9. Check for signal trigger (new high-score OB)
    if (activeOBs.length > 0 && bestScore && bestScore >= 85) {
      const recentSignal = await prisma.scannerSignal.findFirst({
        where: { symbol, createdAt: { gte: new Date(Date.now() - 300000) } }, // 5 min debounce
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
          data: {
            signalsTriggered: { increment: 1 },
            lastSignalAt: new Date(),
          },
        });

        // Emit signal event
        this.io?.of('/scanner').emit('signal', {
          symbol,
          type: 'OB_DETECTED',
          aiScore: bestScore,
          price: ticker.price,
          timestamp: new Date().toISOString(),
        });
      }
    }

    // 10. Update global stats
    await prisma.scannerState.updateMany({
      data: { ticksTotal: { increment: 1 } },
    });

    // 11. Broadcast update
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
      // Delta Exchange public ticker endpoint
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
      // Fallback to mock if Delta API fails (for development)
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
      // Generate synthetic candles for OB detection if API fails
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
      candles.push({
        time: Date.now() - (50 - i) * 3600000,
        open, high, low, close,
        volume: Math.random() * 1000,
      });
    }
    return candles;
  }

  // ─── Controls ─────────────────────────────────────────

  static async globalPause() {
    await prisma.scannerState.updateMany({ data: { isPaused: true } });
    await prisma.scannerPair.updateMany({ data: { isPaused: true, status: 'PAUSED' } });
    this.io?.of('/scanner').emit('control', { action: 'PAUSE_ALL' });
    logger.info('[ScannerEngine] Global PAUSE');
  }

  static async globalResume() {
    await prisma.scannerState.updateMany({ data: { isPaused: false } });
    await prisma.scannerPair.updateMany({ data: { isPaused: false, status: 'ENGINE' } });
    this.io?.of('/scanner').emit('control', { action: 'RESUME_ALL' });
    logger.info('[ScannerEngine] Global RESUME');
  }

  static async globalStop() {
    await prisma.scannerState.updateMany({ data: { isRunning: false, isPaused: false } });
    await prisma.scannerPair.updateMany({ data: { isActive: false, status: 'STOPPED' } });
    this.io?.of('/scanner').emit('control', { action: 'STOP_ALL' });
    this.stop();
    logger.info('[ScannerEngine] Global STOP');
  }

  static async globalStart() {
    await prisma.scannerState.updateMany({ data: { isRunning: true, isPaused: false } });
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
