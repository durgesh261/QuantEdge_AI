// backend/src/modules/scanner/services/scannerEngine.service.ts
// DEFINITIVE FIX — NO FAKE TRADES:
// 1. ONLY evaluates DecisionEngine on FIRST-TOUCHED OBs
// 2. feedStrategyEngine ONLY records signal to DB — ZERO order execution
// 3. triggerExecution is the SOLE path that calls placeOrder (confidence >= 85 required)
// 4. triggerSignal in tick() DOES NOT call feedStrategyEngine (no double-run)
// 5. Paper trading mode guard on executionEngineService

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
import { PositionMonitorService } from '../../position-monitor/services/PositionMonitorService.js';
import crypto from 'crypto';

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
    CanonicalOBRegistry.loadFromDb().catch((err: any) =>
      logger.error('[ScannerEngine] Failed to load OB registry from DB:', err)
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

      // ── Update scanner heartbeat ──────────────────────────────
      await prisma.scannerState.update({
        where: { id: globalState.id },
        data: { lastHeartbeat: new Date() },
      });
      // └─────────────────────────────────────────────────────────

      const pairs = await prisma.scannerPair.findMany({
        where: { isActive: true, isPaused: false, status: 'ENGINE' },
      });

      if (pairs.length === 0) {
        logger.warn('[ScannerEngine] No active pairs to scan');
        return;
      }

      await Promise.all(
        pairs.map((pair) =>
          this.processPair(pair.symbol).catch((err) => {
            logger.error(`[ScannerEngine] Pair ${pair.symbol} crashed:`, err);
            return null;
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
    const ticker = await this.fetchDeltaTicker(symbol);
    if (!ticker) {
      await prisma.scannerPair.update({
        where: { symbol },
        data: { lastTickAt: new Date() },
      }).catch(() => { });
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
        // ═══════════════════════════════════════════════════════════════════
        // 1. RUN CANONICAL INDICATOR ENGINE (LuxAlgo SMC ONLY)
        // ═══════════════════════════════════════════════════════════════════
        indicators = IndicatorEngineService.computeIndicators(candles, '1H', symbol);

        // ═══════════════════════════════════════════════════════════════════
        // 2. SYNC CANONICAL OB REGISTRY with current indicator state
        // ═══════════════════════════════════════════════════════════════════
        CanonicalOBRegistry.syncFromIndicator(symbol, indicators.orderBlocks || []);

        // ═══════════════════════════════════════════════════════════════════
        // 3. LIVE PRICE TOUCH DETECTION (candle-based, WebSocket handles real-time)
        // ═══════════════════════════════════════════════════════════════════
        const livePrice = ticker.price;
        const touchedNow = CanonicalOBRegistry.checkLiveTouch(
          symbol, livePrice, new Date().toISOString()
        );

        for (const touched of touchedNow) {
          logger.info(`[ScannerEngine] TICK FIRST-TOUCH ${touched.id} @ ${livePrice}`);
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

        // ═══════════════════════════════════════════════════════════════════
        // 4. READ ACTIVE OBs FROM CANONICAL REGISTRY
        // ═══════════════════════════════════════════════════════════════════
        const activeEntries = CanonicalOBRegistry.getActive(symbol);
        activeOBs = activeEntries.length;

        if (activeOBs > 0) {
          const avgWidth = activeEntries.reduce(
            (sum, e) => sum + (((e.upperPrice - e.lowerPrice) / e.upperPrice) * 100), 0
          ) / activeOBs;
          obWidthPct = Number(avgWidth.toFixed(3));
        }

        // ═══════════════════════════════════════════════════════════════════
        // 5. DECISION ENGINE — ONLY evaluate FIRST-TOUCHED OBs
        //    NEVER evaluate non-touched active OBs to avoid fake signals
        // ═══════════════════════════════════════════════════════════════════
        const touchedEntries = CanonicalOBRegistry.getTouched(symbol);

        // GUARD: only proceed if there is an actual first-touch event
        if (touchedEntries.length > 0) {
          // Phase 3 Rule: Stop scanning for new entries if a trade is already open
          const openPositionCount = deltaSyncService.getPositions().length;
          if (openPositionCount > 0) {
            logger.info(`[ScannerEngine] Skipping decision — ${openPositionCount} open position(s)`);
          } else {
            const candidate = touchedEntries[touchedEntries.length - 1]!;

            const activeZone = {
              id: candidate.id,
              symbol: candidate.symbol,
              type: candidate.direction === 'BULLISH' ? 'DEMAND' : 'SUPPLY',
              upperPrice: candidate.upperPrice,
              lowerPrice: candidate.lowerPrice,
              touchCount: 1,
              freshness: 100,
            };

            logger.info(
              `[ScannerEngine] DECISION EVAL: ${symbol} OB=${candidate.id} price=${livePrice} type=${activeZone.type}`
            );

            const decision = await DecisionEngineService.evaluateDecision({
              symbol,
              timeframe: '1H',
              currentPrice: livePrice,
              indicators,
              activeZone: activeZone as any,
            });

            bestScore = decision.confidenceScore;
            bestOB = candidate;

            logger.info(
              `[ScannerEngine] DECISION RESULT: ${symbol} confidence=${bestScore} state=${decision.state} reasons=${decision.reasonCodes.join(',')}`
            );

            // ═══════════════════════════════════════════════════════════════
            // 6. EXECUTE — ONLY if APPROVED AND confidence >= 85
            //    This is the SOLE execution path. feedStrategyEngine does NOT execute.
            // ═══════════════════════════════════════════════════════════════
            if ((decision.state as any) === 'APPROVED' && bestScore >= 85) {
              CanonicalOBRegistry.markTraded(candidate.id);
              await this.triggerExecution(symbol, livePrice, decision, candidate.id);
            } else {
              logger.info(
                `[ScannerEngine] REJECTED: ${symbol} confidence=${bestScore} < 85 OR state=${decision.state} — NO TRADE`
              );
              // Record signal to DB for analysis (NO order placed here)
              await this.recordRejectedSignal(symbol, livePrice, bestScore, decision.reasonCodes);
            }
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

  /**
   * SOLE EXECUTION PATH — Called ONLY when:
   *   - decision.state === 'APPROVED'
   *   - confidence >= 85
   *   - OB was first-touched (never on non-touched active OBs)
   */
  private static async triggerExecution(
    symbol: string,
    price: number,
    decision: any,
    orderBlockId: string
  ) {
    try {
      const contractQuantity = decision.contractQuantity ?? decision.positionSize ?? 0;
      logger.info(
        `[Scanner→Execution] PLACING ORDER: ${symbol} @ ${price} side=${decision.outcome} size=${contractQuantity} confidence=${decision.confidenceScore}`
      );

      const clientOrderId = `AUTO-${symbol}-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;

      await executionEngineService.placeOrder({
        symbol,
        side: decision.outcome === 'BUY' || decision.outcome === StrategySignalOutcome.BUY ? 'buy' : 'sell',
        orderType: 'market',
        size: contractQuantity,
        leverage: decision.leverage,
        stopLossPrice: decision.stopLossPrice,
        takeProfitPrice: decision.takeProfitPrice,
        clientOrderId,
      });

      // Create TradeLedger entry for the executed trade
      const tradeId = clientOrderId;
      const balances = deltaSyncService.getBalances();
      const usdtBalance = balances.find((b) => b.asset_symbol === 'USDT' || b.asset_symbol === 'USD');
      const accountBalance = usdtBalance ? parseFloat(usdtBalance.balance || '0') : 0;
      const entryPrice = price;
      const notional = entryPrice * contractQuantity;
      const marginUsed = notional / (decision.leverage || 1);
      const riskPercent = decision.stopLossPrice
        ? (Math.abs(entryPrice - decision.stopLossPrice) * contractQuantity) / accountBalance * 100
        : 0;

      await prisma.tradeLedger.create({
        data: {
          tradeId,
          exchangeOrderId: clientOrderId,
          symbol,
          timeframe: '1H',
          executionMode: 'LIVE',
          side: (decision.outcome === 'BUY' || decision.outcome === StrategySignalOutcome.BUY) ? 'LONG' : 'SHORT',
          entryPrice,
          quantity: contractQuantity,
          marginUsed,
          leverage: decision.leverage || 1,
          riskPercent,
          stopLoss: decision.stopLossPrice ?? 0,
          takeProfit: decision.takeProfitPrice ?? 0,
          decisionConfidence: decision.confidenceScore ?? 0,
          decisionExplanation: `Auto-execution from scanner: ${decision.reasonCodes?.join(', ') || 'N/A'}`,
          resultStatus: 'OPEN',
          syncStatus: 'SYNCED',
          executedAt: new Date(),
        },
      });

      // Add position to monitoring for SL/TP
      const side: 'LONG' | 'SHORT' = (decision.outcome === 'BUY' || decision.outcome === StrategySignalOutcome.BUY) ? 'LONG' : 'SHORT';
      const positions = deltaSyncService.getPositions();
      const deltaPos = positions.find((p: any) =>
        p.product_symbol === symbol &&
        ((side === 'LONG' && p.side === 'buy') || (side === 'SHORT' && p.side === 'sell'))
      );

      if (deltaPos) {
        PositionMonitorService.addPosition({
          symbol,
          side,
          entryPrice,
          stopLossPrice: decision.stopLossPrice ?? 0,
          takeProfitPrice: decision.takeProfitPrice ?? 0,
          quantity: contractQuantity,
          leverage: decision.leverage || 1,
          tradeId,
          orderBlockId,
          entryTime: new Date().toISOString(),
          deltaProductSymbol: deltaPos.product_symbol,
          deltaPositionId: deltaPos.product_id,
        });
      } else {
        logger.warn(`[ScannerEngine] Position not found on Delta after execution for ${symbol}`);
      }

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

      logger.info(`[Scanner→Execution] Trade EXECUTED for ${symbol} @ ${price}`);
    } catch (err) {
      logger.error(`[Scanner→Execution] Failed for ${symbol}:`, err);
    }
  }

  /**
   * Records a rejected signal to the DB for analysis.
   * DOES NOT place any order — purely for audit trail.
   */
  private static async recordRejectedSignal(
    symbol: string,
    price: number,
    score: number | null,
    reasonCodes: string[]
  ) {
    try {
      await prisma.strategySignalRecord.create({
        data: {
          symbol,
          timeframe: TIMEFRAME,
          outcome: 'REJECTED',
          price,
          rationale: `REJECTED confidence=${score ?? 0} reasons=${reasonCodes.join(',')}`,
          confidenceScore: (score ?? 0) / 100,
        },
      });
    } catch {
      // ignore DB errors for signal recording
    }
  }

  private static async fetchDeltaTicker(symbol: string) {
    try {
      const restClient = deltaSyncService.getRestClient();
      if (restClient.isConfigured()) {
        try {
          const tData = await restClient.getTicker(symbol);
          if (tData?.mark_price || tData?.close || tData?.spot_price) {
            const p = parseFloat(tData.close || tData.mark_price || tData.spot_price);
            if (!isNaN(p) && p > 0) {
              return { price: p, change_24h: parseFloat(tData.change_24h || '0') };
            }
          }
        } catch {
          // REST client error - no fallback
        }
      }
      
      // NO fake/fallback market data - return null if Delta unavailable
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

  static async globalPause() {
    await prisma.scannerState.updateMany({ data: { isPaused: true } });
    this.io?.of('/scanner').emit('control', { action: 'GLOBAL_PAUSE' });
  }

  static async globalResume() {
    await prisma.scannerState.updateMany({ data: { isPaused: false } });
    this.io?.of('/scanner').emit('control', { action: 'GLOBAL_RESUME' });
  }

  static async globalStop() {
    await prisma.scannerState.updateMany({ data: { isRunning: false } });
    this.stop();
    this.io?.of('/scanner').emit('control', { action: 'GLOBAL_STOP' });
  }

  static async globalStart() {
    await prisma.scannerState.updateMany({ data: { isRunning: true } });
    this.start();
    this.io?.of('/scanner').emit('control', { action: 'GLOBAL_START' });
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
    const blocks: any[] = [];
    this.io?.of('/scanner').emit('inspect', { symbol, pair, blocks });
  }
}
