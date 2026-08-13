import { deltaSyncService } from '../../delta-exchange/index.js';
import { prisma } from '../../../db.js';
import { OutcomeValidatorService } from './outcomeValidator.service.js';
import { eventBus } from '../../../services/EventBus.js';
import { logger } from '../../../logger/index.js';

interface ShadowPositionRecord {
  id: string;
  decisionId: string;
  symbol: string;
  timeframe: string;
  side: string;
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  quantity: number;
  leverage: number;
  riskPercent: number;
  confidenceScore: number;
  reasonCodesJson: string;
  status: string;
  tpHitAt: Date | null;
  slHitAt: Date | null;
  holdDurationMinutes: number | null;
  mfe: number | null;
  mae: number | null;
  createdAt: Date;
  updatedAt: Date;
}

export class ShadowPositionMonitor {
  private static isMonitoring = false;
  private static monitorTimer: NodeJS.Timeout | null = null;
  private static priceTickCallback: ((tick: { symbol: string; price: number; timestamp: number }) => void) | null = null;

  public static async start(): Promise<void> {
    if (this.isMonitoring) return;
    this.isMonitoring = true;
    logger.info('[ShadowPositionMonitor] Starting real-time SL/TP monitoring');

    // Subscribe to real-time price ticks from Delta WS
    this.priceTickCallback = (tick: { symbol: string; price: number; timestamp: number }) => {
      this.handlePriceTick(tick.symbol, tick.price, tick.timestamp);
    };
    deltaSyncService.onPriceTick(this.priceTickCallback);

    // Also run periodic check as safety net (every 5 seconds)
    this.monitorTimer = setInterval(() => {
      this.checkAllPositions();
    }, 5000);

    // Initial load of open positions
    await this.loadOpenPositions();
  }

  public static stop(): void {
    if (!this.isMonitoring) return;
    this.isMonitoring = false;
    if (this.monitorTimer) {
      clearInterval(this.monitorTimer);
      this.monitorTimer = null;
    }
    if (this.priceTickCallback) {
      // Note: deltaSyncService doesn't have an off method, but the callback will be ignored when isMonitoring=false
    }
    logger.info('[ShadowPositionMonitor] Stopped SL/TP monitoring');
  }

  private static async loadOpenPositions(): Promise<void> {
    const positions = await prisma.shadowPosition.findMany({
      where: { status: 'OPEN' },
    });
    logger.info(`[ShadowPositionMonitor] Loaded ${positions.length} open shadow positions`);
  }

  private static async handlePriceTick(symbol: string, price: number, timestamp: number): Promise<void> {
    if (!this.isMonitoring) return;

    const positions = await prisma.shadowPosition.findMany({
      where: { symbol, status: 'OPEN' },
    });

    for (const position of positions) {
      await this.checkSlTpHit(position, price, timestamp);
    }
  }

  private static async checkAllPositions(): Promise<void> {
    if (!this.isMonitoring) return;

    try {
      const positions = await prisma.shadowPosition.findMany({
        where: { status: 'OPEN' },
      });

      for (const position of positions) {
        try {
          const markPrice = await deltaSyncService.getMarkPrice(position.symbol);
          if (markPrice !== null) {
            await this.checkSlTpHit(position, markPrice, Date.now());
          }
        } catch (err) {
          logger.error(`[ShadowPositionMonitor] Error checking position ${position.id}:`, err);
        }
      }
    } catch (err) {
      logger.error('[ShadowPositionMonitor] Error fetching open positions:', err);
    }
  }

  private static async checkSlTpHit(
    position: ShadowPositionRecord,
    currentPrice: number,
    timestamp: number
  ): Promise<void> {
    const { side, stopLossPrice, takeProfitPrice } = position;

    let hitSl = false;
    let hitTp = false;
    let exitPrice = currentPrice;
    let exitReason = '';

    if (side === 'LONG') {
      if (currentPrice <= stopLossPrice) {
        hitSl = true;
        exitPrice = stopLossPrice;
        exitReason = 'SL_HIT';
      } else if (currentPrice >= takeProfitPrice) {
        hitTp = true;
        exitPrice = takeProfitPrice;
        exitReason = 'TP_HIT';
      }
    } else if (side === 'SHORT') {
      if (currentPrice >= stopLossPrice) {
        hitSl = true;
        exitPrice = stopLossPrice;
        exitReason = 'SL_HIT';
      } else if (currentPrice <= takeProfitPrice) {
        hitTp = true;
        exitPrice = takeProfitPrice;
        exitReason = 'TP_HIT';
      }
    }

    if (hitSl || hitTp) {
      await this.closeShadowPosition(position, exitPrice, currentPrice, exitReason, timestamp);
    }
  }

  private static async closeShadowPosition(
    position: ShadowPositionRecord,
    exitPrice: number,
    currentPrice: number,
    reason: string,
    timestamp: number
  ): Promise<void> {
    const { id, decisionId, side, entryPrice, stopLossPrice, takeProfitPrice, quantity, leverage, createdAt } = position;

    // Calculate hold duration
    const entryTime = new Date(createdAt).getTime();
    const holdDurationMinutes = Math.floor((timestamp - entryTime) / 60000);

    // Calculate MFE and MAE using the highest/lowest price seen during the position lifetime
    // For simplicity, we use entryPrice and max(currentPrice, exitPrice) / min(currentPrice, exitPrice)
    let mfe: number;
    let mae: number;
    if (side === 'LONG') {
      const highPrice = Math.max(entryPrice, currentPrice, exitPrice);
      const lowPrice = Math.min(entryPrice, currentPrice, exitPrice);
      mfe = Number((((highPrice - entryPrice) / entryPrice) * 100).toFixed(2));
      mae = Number((((entryPrice - lowPrice) / entryPrice) * 100).toFixed(2));
    } else {
      const highPrice = Math.max(entryPrice, currentPrice, exitPrice);
      const lowPrice = Math.min(entryPrice, currentPrice, exitPrice);
      mfe = Number((((entryPrice - lowPrice) / entryPrice) * 100).toFixed(2));
      mae = Number((((highPrice - entryPrice) / entryPrice) * 100).toFixed(2));
    }

    // Determine TP/SL hit
    const tpHit = reason === 'TP_HIT';
    const slHit = reason === 'SL_HIT';

    // Update ShadowPosition
    await prisma.shadowPosition.update({
      where: { id },
      data: {
        status: reason,
        tpHitAt: tpHit ? new Date(timestamp) : null,
        slHitAt: slHit ? new Date(timestamp) : null,
        holdDurationMinutes,
        mfe,
        mae,
        updatedAt: new Date(timestamp),
      },
    });

    // Use OutcomeValidatorService with proper high/low prices
    const highPrice = side === 'LONG' ? Math.max(entryPrice, currentPrice, exitPrice) : Math.max(entryPrice, currentPrice, exitPrice);
    const lowPrice = side === 'LONG' ? Math.min(entryPrice, currentPrice, exitPrice) : Math.min(entryPrice, currentPrice, exitPrice);
    
    const outcome = OutcomeValidatorService.validateOutcome(
      decisionId,
      entryPrice,
      highPrice,
      lowPrice,
      takeProfitPrice,
      stopLossPrice
    );

    // Persist MarketOutcomeValidation
    await prisma.marketOutcomeValidation.upsert({
      where: { decisionId },
      create: {
        decisionId,
        tpHit: outcome.tpHit,
        slHit: outcome.slHit,
        mfe: outcome.mfe,
        mae: outcome.mae,
        holdDurationMinutes: outcome.holdDurationMinutes,
        accuracyPercent: outcome.accuracyPercent,
      },
      update: {
        tpHit: outcome.tpHit,
        slHit: outcome.slHit,
        mfe: outcome.mfe,
        mae: outcome.mae,
        holdDurationMinutes: outcome.holdDurationMinutes,
        accuracyPercent: outcome.accuracyPercent,
      },
    });

    // Emit event for frontend
    eventBus.emit('shadow:outcome', {
      decisionId,
      symbol: position.symbol,
      side: position.side,
      entryPrice,
      exitPrice,
      quantity,
      leverage,
      reason,
      tpHit: outcome.tpHit,
      slHit: outcome.slHit,
      mfe: outcome.mfe,
      mae: outcome.mae,
      holdDurationMinutes: outcome.holdDurationMinutes,
      accuracyPercent: outcome.accuracyPercent,
      timestamp: new Date(timestamp).toISOString(),
    });

    logger.info(`[ShadowPositionMonitor] ${reason} for ${position.symbol} ${side} @ ${exitPrice} (current=${currentPrice})`);
  }
}