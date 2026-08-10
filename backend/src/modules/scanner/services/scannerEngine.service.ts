import { OrderBlockService } from './orderBlock.service.js';
import { CanonicalOBRegistry } from '../../indicator-engine/services/canonicalOBRegistry.js';
import { Server } from 'socket.io';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { CandleStoreService } from '../../market-data/services/candleStore.service.js';
import { IndicatorEngineService } from '../../indicator-engine/services/indicatorEngine.service.js';
import { deltaSyncService } from '../../delta-exchange/index.js';
import { DecisionEngineService } from '../../decision/services/decisionEngine.service.js';
import { executionEngineService } from '../../execution-engine/services/ExecutionEngineService.js';
import { StrategySignalOutcome } from '@algoapp/shared';
import { eventBus } from '../../../services/EventBus.js';

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

    // Subscribe to real-time Delta WebSocket price ticks for immediate touch detection
    deltaSyncService.onPriceTick((tick: { symbol: string; price: number; timestamp: number }) => {
      if (!this.isRunning) return;
      this.handleLivePriceTick(tick.symbol, tick.price);
    });
  }

  private static async handleLivePriceTick(symbol: string, price: number): Promise<void> {
    const touchedEntries = CanonicalOBRegistry.checkLiveTouch(
      symbol,
      price,
      new Date().toISOString()
    );

    for (const ob of touchedEntries) {
      logger.info(`[ScannerEngine] WS REAL-TIME FIRST-TOUCH ${ob.id} for ${symbol} @ ${price}`);
      eventBus.emit('ob:touched', {
        symbol,
        orderBlockId: ob.id,
        touchPrice: price,
        type: ob.direction,
        upperPrice: ob.upperPrice,
        lowerPrice: ob.lowerPrice,
        isUsed: true,
        timestamp: new Date().toISOString(),
      });
    }
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
    } else {
      const state = await prisma.scannerState.findFirst();
      if (state?.isRunning && !state?.isPaused) {
        this.start();
      }
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

      const globalState = await prisma.scannerState.findFirst();
      if (globalState) {
        await prisma.scannerState.update({
          where: { id: globalState.id },
          data: { signalsTotal: { increment: 1 } },
        });
      }

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
    if (!ticker) {
      // If ticker fails, update pair status without breaking
      await prisma.scannerPair.update({
        where: { symbol },
        data: { lastTickAt: new Date() },
      }).catch(() => {});
      return null;
    }

    let activeOBs = 0;
    let obWidthPct: number | null = null;
    let bestScore: number | null = null;
    let bestOB: any = null;
    let indicators: any = null;
    let priceChange24h = ticker.change_24h || 0;

    try {
      const candles = await CandleStoreService.getCandles(symbol, '1H', 300);
      if (candles.length >= 2 && priceChange24h === 0) {
        const slice = candles.slice(-24);
        const first = slice[0]!;
        const last = slice[slice.length - 1]!;
        if (first.open && first.open > 0) {
          priceChange24h = Number((((last.close - first.open) / first.open) * 100).toFixed(2));
        }
      }

      if (candles.length >= 10) {
        // ═════════════════════════════════════════════════════════════════════
        // 1. RUN CANONICAL INDICATOR ENGINE (LuxAlgo SMC ONLY)
        // ═════════════════════════════════════════════════════════════════════
        indicators = IndicatorEngineService.computeIndicators(candles, '1H', symbol);

        // ═════════════════════════════════════════════════════════════════════
        // 2. SYNC CANONICAL OB REGISTRY with current indicator state
        // ═════════════════════════════════════════════════════════════════════
        CanonicalOBRegistry.syncFromIndicator(symbol, indicators.orderBlocks || []);

        // ═════════════════════════════════════════════════════════════════════
        // 3. LIVE PRICE TOUCH DETECTION (WebSocket-driven, not candle-close)
        // ═════════════════════════════════════════════════════════════════════
        const livePrice = ticker.price;
        const touchedNow = CanonicalOBRegistry.checkLiveTouch(
          symbol, livePrice, new Date().toISOString()
        );

        for (const touched of touchedNow) {
          eventBus.emit('ob:touched', {
            symbol,
            orderBlockId: touched.id,
            touchPrice: livePrice,
            type: touched.direction,
            upperPrice: touched.upperPrice,
            lowerPrice: touched.lowerPrice,
            timestamp: new Date().toISOString(),
          });
        }

        // ═════════════════════════════════════════════════════════════════════
        // 4. READ ACTIVE OBs FROM CANONICAL REGISTRY
        // ═════════════════════════════════════════════════════════════════════
        const activeEntries = CanonicalOBRegistry.getActive(symbol);
        activeOBs = activeEntries.length;

        // Calculate average width for display
        if (activeOBs > 0) {
          const avgWidth = activeEntries.reduce(
            (sum, e) => sum + (((e.upperPrice - e.lowerPrice) / e.upperPrice) * 100), 0
          ) / activeOBs;
          obWidthPct = Number(avgWidth.toFixed(3));
        }

        // ═════════════════════════════════════════════════════════════════════
        // 5. DECISION ENGINE — evaluate touched or best active OB
        // ═════════════════════════════════════════════════════════════════════
        const touchedEntries = CanonicalOBRegistry.getTouched(symbol);
        const candidate = touchedEntries.length > 0
          ? touchedEntries[touchedEntries.length - 1]!
          : activeEntries[0];

        if (candidate) {
          const activeZone = {
            id: candidate.id,
            symbol: candidate.symbol,
            type: candidate.direction === 'BULLISH' ? 'DEMAND' : 'SUPPLY',
            upperPrice: candidate.upperPrice,
            lowerPrice: candidate.lowerPrice,
            touchCount: candidate.touched ? 1 : 0,
            freshness: 100,
          };

          const decision = await DecisionEngineService.evaluateDecision({
            symbol,
            timeframe: '1H',
            currentPrice: livePrice,
            indicators,
            activeZone: activeZone as any,
          });

          bestScore = decision.confidenceScore;
          bestOB = candidate;

          if ((decision.state as any) === 'APPROVED' && bestScore >= 85) {
            CanonicalOBRegistry.markTraded(candidate.id);
            await this.triggerExecution(symbol, livePrice, decision);
          }
        }

        // Sync OrderBlockService for legacy frontend compatibility
        OrderBlockService.syncFromIndicators(symbol, activeEntries.map(e => ({
          id: e.id,
          symbol: e.symbol,
          timeframe: e.timeframe,
          type: e.direction,
          upperPrice: e.upperPrice,
          lowerPrice: e.lowerPrice,
          widthPercent: ((e.upperPrice - e.lowerPrice) / e.upperPrice) * 100,
          baseCandleIndex: e.baseCandleIndex,
          breakCandleIndex: e.breakCandleIndex,
          source: e.sourceType,
          createdAt: e.createdAt,
          isMitigated: e.mitigated,
          isInvalidated: false,
          isUsed: e.traded,
          touchCount: e.touched ? 1 : 0,
        })), {});
      }
    } catch (obErr) {
      logger.warn(`[ScannerEngine] OB detection failed for ${symbol}:`, obErr);
    }

    const updatedPair = await prisma.scannerPair.update({
      where: { symbol },
      data: {
        livePrice: ticker.price,
        priceChange24h,
        activeOBs,
        obWidthPct,
        aiScore: bestScore,
        lastTickAt: new Date(),
        ticksProcessed: { increment: 1 },
      },
    });

    await prisma.scannerTick.create({
      data: { symbol, price: ticker.price, source: 'delta' },
    });

    await this.incrementGlobalTicks();

    this.io?.of('/scanner').emit('tick', {
      symbol,
      price: ticker.price,
      change24h: updatedPair.priceChange24h,
      activeOBs: updatedPair.activeOBs,
      obWidthPct: updatedPair.obWidthPct,
      aiScore: updatedPair.aiScore,
      status: updatedPair.status,
      timestamp: new Date().toISOString(),
    });

    return { symbol, ticker, bestScore, activeOBs, bestOB, indicators };
  }

  private static async triggerExecution(
    symbol: string,
    price: number,
    decision: any
  ) {
    try {
      await executionEngineService.placeOrder({
        symbol,
        side: decision.outcome === 'BUY' || decision.outcome === StrategySignalOutcome.BUY ? 'buy' : 'sell',
        orderType: 'market',
        size: decision.positionSize ?? 0,
        leverage: decision.leverage,
        stopLossPrice: decision.stopLossPrice,
        takeProfitPrice: decision.takeProfitPrice,
      });

      const globalState = await prisma.scannerState.findFirst();
      if (globalState) {
        await prisma.scannerState.update({
          where: { id: globalState.id },
          data: { tradesTotal: { increment: 1 } },
        });
      }
      await prisma.scannerPair.update({
        where: { symbol },
        data: { tradesExecuted: { increment: 1 } },
      });

      logger.info(`[Scanner→Execution] Trade executed for ${symbol} @ ${price}`);
    } catch (err) {
      logger.error(`[Scanner→Execution] Failed for ${symbol}:`, err);
    }
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

        const globalState = await prisma.scannerState.findFirst();
        if (globalState) {
          await prisma.scannerState.update({
            where: { id: globalState.id },
            data: { tradesTotal: { increment: 1 } },
          });
        }
        await prisma.scannerPair.update({
          where: { symbol },
          data: { tradesExecuted: { increment: 1 } },
        });

        // NOTE: OB is already marked used on first-touch above.
        // markUsed is NOT called here again — touch already consumed the OB.
      } else {
        logger.warn(`[Scanner→Execution] Signal REJECTED for ${symbol}. Reason: ${decision.reasonCodes.join(', ')}`);
        // OB was already consumed on first-touch above. No re-entry possible.
      }

    } catch (err) {
      logger.error('[Scanner→Strategy/Algo] Failed to feed signal:', err);
    }
  }

  private static async fetchDeltaTicker(symbol: string) {
    try {
      const deltaSymbol = symbol.replace('.P', '');
      const restClient = deltaSyncService.getRestClient();
      if (restClient.isConfigured()) {
        try {
          const tData = await restClient.getTicker(deltaSymbol);
          if (tData?.mark_price || tData?.close || tData?.spot_price) {
            const p = parseFloat(tData.close || tData.mark_price || tData.spot_price);
            if (!isNaN(p) && p > 0) {
              return { price: p, change_24h: parseFloat(tData.change_24h || '0') };
            }
          }
        } catch {
          // ignore REST client error and try MarketSnapshotService
        }
      }

      const { MarketSnapshotService } = await import('../../market-data/services/marketSnapshot.service.js');
      const snap = await MarketSnapshotService.getSnapshot(symbol);
      if (snap && snap.currentPrice > 0) {
        return { price: snap.currentPrice, change_24h: 0 };
      }
      return null;
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
          data: { ticksTotal: { increment: 1 } },
        });
      }
    } catch (e) {
      // ignore
    }
  }

  static async startPair(symbol: string) {
    await prisma.scannerPair.update({
      where: { symbol },
      data: { isActive: true, isPaused: false, status: 'ENGINE' },
    });
    this.io?.of('/scanner').emit('control', { action: 'START_PAIR', symbol });
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


