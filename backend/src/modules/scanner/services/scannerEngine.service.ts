import { Server } from 'socket.io';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { CandleStoreService } from '../../market-data/services/candleStore.service.js';
import { IndicatorEngineService } from '../../indicator-engine/services/indicatorEngine.service.js';
import { deltaSyncService } from '../../delta-exchange/index.js';
import { DecisionEngineService } from '../../decision/services/decisionEngine.service.js';
import { executionEngineService } from '../../execution-engine/services/ExecutionEngineService.js';
import { StrategySignalOutcome } from '@algoapp/shared';

const SCAN_INTERVAL_MS = 5000;
const TIMEFRAME = '1H';

export class ScannerEngine {
  private static timer: NodeJS.Timeout | null = null;
  private static isRunning = false;
  private static isTicking = false;
  private static io: Server | null = null;

  static initialize(io: Server) {
    this.io = io;
    this.ensureState().catch((err) =>
      logger.error('[ScannerEngine] Initial state creation failed', err)
    );
  }

  private static async ensureState() {
    const count = await prisma.scannerState.count();
    if (count === 0) {
      await prisma.scannerState.create({
        data: {
          isRunning: false,
          isPaused: false,
          
          
        },
      });
    }
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

      const results = await Promise.all(
        pairs.map((pair) =>
          this.processPair(pair.symbol).catch((err) => {
            logger.error(`[ScannerEngine] Pair ${pair.symbol} crashed:`, err);
            return null;
          })
        )
      );

      // Phase 3 Rule: Stop scanning for new entries if a trade is already open
      const openPositionCount = deltaSyncService.getPositions().length;
      if (openPositionCount > 0) {
        return;
      }

      const signals = results
        .filter((r): r is NonNullable<typeof r> => r !== null)
        .filter(r => r.activeOBs > 0 && r.bestScore && r.bestScore >= 85);

      if (signals.length > 0) {
        // Phase 3 Rule: Simultaneous Signals -> Compare confidence scores
        signals.sort((a, b) => (b.bestScore || 0) - (a.bestScore || 0));
        const topSignal = signals[0];

        await this.triggerSignal(topSignal);
      }

    } catch (err) {
      logger.error('[ScannerEngine] Tick error:', err);
    } finally {
      this.isTicking = false;
    }
  }

  private static async triggerSignal(signal: any) {
    const { symbol, ticker, bestScore, activeOBs, bestOB, indicators } = signal;

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

      await this.feedStrategyEngine(symbol, ticker.price, bestScore, bestOB, indicators);

      this.io?.of('/scanner').emit('signal', {
        symbol,
        type: 'OB_DETECTED',
        aiScore: bestScore,
        price: ticker.price,
        timestamp: new Date().toISOString(),
      });
    }
  }

  private static async processPair(symbol: string) {
    const ticker = await this.fetchDeltaTicker(symbol);
    if (!ticker) return null;

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

    let activeOBs = 0;
    let obWidthPct: number | null = null;
    let bestScore: number | null = null;
    let bestOB: any = null;
    let indicators: any = null;

    try {
      const candles = await CandleStoreService.getCandles(symbol, '1H', 200);
      if (candles.length >= 10) {
        indicators = IndicatorEngineService.computeIndicators(candles, '1H', symbol);

        const validOBs = (indicators.orderBlocks || []).filter(
          (ob: any) => !ob.isMitigated && !ob.isInvalidated && !ob.isUsed
        );
        activeOBs = validOBs.length;

        if (activeOBs > 0) {
          const avgWidth = validOBs.reduce((sum: number, b: any) => sum + b.widthPercent, 0) / activeOBs;
          obWidthPct = Number(avgWidth.toFixed(3));
          
          const result = validOBs.reduce((best: any, ob: any) => {
            const score = indicators.zoneScores[`ZONE-SUP-${ob.id}`]?.totalScore
                       ?? indicators.zoneScores[`ZONE-DEM-${ob.id}`]?.totalScore
                       ?? 70;
            return score > (best.score ?? 0) ? { ob, score } : best;
          }, {} as { ob?: any; score?: number });
          
          bestScore = result.score ?? null;
          bestOB = result.ob ?? null;
        }
      }
    } catch (obErr) {
      logger.warn(`[ScannerEngine] OB detection failed for ${symbol}:`, obErr);
    }

    const updatedPair = await prisma.scannerPair.update({
      where: { symbol },
      data: { activeOBs, obWidthPct, aiScore: bestScore },
    });

    await this.incrementGlobalTicks();

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

    return { symbol, ticker, bestScore, activeOBs, bestOB, indicators };
  }

  private static async feedStrategyEngine(
    symbol: string,
    price: number,
    aiScore: number,
    bestOB: any,
    indicators: any
  ) {
    try {
      await prisma.strategySignalRecord.create({
        data: {
          symbol,
          timeframe: TIMEFRAME,
          outcome: 'PENDING',
          price,
          rationale: `Scanner OB_DETECTED — AI Score ${aiScore}/100`,
          confidenceScore: aiScore / 100,
        },
      });

      logger.info(`[Scanner→Strategy/Algo] Signal fed for ${symbol} @ ${price} (score: ${aiScore})`);

      if (!bestOB || !indicators) {
        return;
      }

      // Step 2-4: CONFIDENCE_CHECK -> RISK_CHECK -> ENTRY_PENDING via Decision Engine
      const decision = await DecisionEngineService.evaluateDecision({
        symbol,
        timeframe: '1H',
        currentPrice: price,
        indicators,
        activeZone: bestOB,
      });

      if (decision.state === 'APPROVED' as any) {
        logger.info(`[Scanner→Execution] Signal APPROVED for ${symbol}. Dispatching order.`);
        
        // Execute the trade (ENTRY_PENDING -> OPEN)
        await executionEngineService.placeOrder({
          symbol,
          side: decision.outcome === StrategySignalOutcome.BUY ? 'buy' : 'sell',
          orderType: 'market',
          size: decision.positionSize ?? 0,
          leverage: decision.leverage,
          stopLossPrice: decision.stopLossPrice,
          takeProfitPrice: decision.takeProfitPrice,
        });

        // Mark the OB as USED (Phase 2 rule)
        const { OrderBlockWidthEngine } = await import('../../indicator-engine/engines/orderBlockWidthEngine.js');
        OrderBlockWidthEngine.markUsed(bestOB.id);
      } else {
        logger.warn(`[Scanner→Execution] Signal REJECTED for ${symbol}. Reason: ${decision.reasonCodes.join(', ')}`);
      }

    } catch (err) {
      logger.error('[Scanner→Strategy/Algo] Failed to feed signal:', err);
    }
  }

  private static async fetchDeltaTicker(symbol: string) {
    try {
      const restClient = deltaSyncService.getRestClient();
      const product = restClient.getProduct(symbol);
      if (!product) return null;
      const tData = await restClient.getTicker(symbol);
      return { price: parseFloat(tData.mark_price), change_24h: 0 };
    } catch {
      return null;
    }
  }

  private static async incrementGlobalTicks() {
    try {
      const globalState = await prisma.scannerState.findFirst();
      if (globalState) {
        await prisma.scannerState.update({
          where: { id: globalState.id },
          data: {},
        });
      }
    } catch (e) {
      // ignore
    }
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

  static async globalPause() { await prisma.scannerState.updateMany({ data: { isPaused: true } }); this.io?.of('/scanner').emit('control', { action: 'GLOBAL_PAUSE' }); } static async globalResume() { await prisma.scannerState.updateMany({ data: { isPaused: false } }); this.io?.of('/scanner').emit('control', { action: 'GLOBAL_RESUME' }); } static async globalStop() { await prisma.scannerState.updateMany({ data: { isRunning: false } }); this.stop(); this.io?.of('/scanner').emit('control', { action: 'GLOBAL_STOP' }); } static async globalStart() { await prisma.scannerState.updateMany({ data: { isRunning: true } }); this.start(); this.io?.of('/scanner').emit('control', { action: 'GLOBAL_START' }); } static async stopPair(symbol: string) {
    await prisma.scannerPair.update({
      where: { symbol },
      data: { isActive: false, status: 'STOPPED' },
    });
    this.io?.of('/scanner').emit('control', { action: 'STOP_PAIR', symbol });
  }

  static async inspectPair(symbol: string) {
    const pair = await prisma.scannerPair.findUnique({ where: { symbol } });
    const blocks: any[] = [];
    this.io?.of('/scanner').emit('inspect', { symbol, pair, blocks });
  }
}


