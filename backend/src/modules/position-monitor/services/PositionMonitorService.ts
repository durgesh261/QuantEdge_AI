import { DeltaPosition } from '../../delta-exchange/services/DeltaRestClient.js';
import { deltaSyncService } from '../../delta-exchange/index.js';
import { eventBus } from '../../../services/EventBus.js';
import { prisma } from '../../../db.js';
import { TradeAccountingService } from '../../trade-accounting/services/tradeAccounting.service.js';
import { WalletEngineService } from '../../trade-accounting/services/walletEngine.service.js';
import { CanonicalOBRegistry } from '../../indicator-engine/services/canonicalOBRegistry.js';
import { PositionRecoveryService } from './PositionRecoveryService.js';
import { logger } from '../../../logger/index.js';

const walletService = new WalletEngineService();

interface MonitoredPosition {
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  quantity: number;
  leverage: number;
  tradeId: string;
  orderBlockId: string;
  entryTime: string;
  deltaProductSymbol: string;
  deltaPositionId: number;
}

export class PositionMonitorService {
  private static monitoredPositions = new Map<string, MonitoredPosition>();
  private static isMonitoring = false;
  private static monitorTimer: NodeJS.Timeout | null = null;

  /**
   * Start monitoring positions for SL/TP hits
   */
  public static async start(): Promise<void> {
    if (this.isMonitoring) return;
    this.isMonitoring = true;
    logger.info('[PositionMonitor] Starting real-time SL/TP monitoring');

    // Subscribe to real-time position updates from Delta WS
    deltaSyncService.onWsPositionUpdate((position: DeltaPosition) => {
      this.handlePositionUpdate(position);
    });

    // Also run periodic check as safety net (every 5 seconds)
    this.monitorTimer = setInterval(() => {
      this.checkAllPositions();
    }, 5000);

    // Recover positions using comprehensive reconciliation
    await PositionRecoveryService.recoverPositions().catch(err =>
      logger.error('[PositionMonitor] Position recovery failed:', err)
    );
  }

  /**
   * Stop monitoring
   */
  public static stop(): void {
    if (!this.isMonitoring) return;
    this.isMonitoring = false;
    if (this.monitorTimer) {
      clearInterval(this.monitorTimer);
      this.monitorTimer = null;
    }
    logger.info('[PositionMonitor] Stopped SL/TP monitoring');
  }

  /**
   * Handle real-time position update from Delta WS
   */
  private static async handlePositionUpdate(position: DeltaPosition): Promise<void> {
    const symbol = this.normalizeSymbol(position.product_symbol);
    const side = position.side === 'buy' ? 'LONG' : position.side === 'sell' ? 'SHORT' : position.side;
    const monitoredKey = `${symbol}_${side}`;

    const monitored = this.monitoredPositions.get(monitoredKey);
    if (!monitored) return; // Not a position we're monitoring

    // Check if position was closed/reduced on Delta
    const currentSize = Math.abs(position.size);
    if (currentSize === 0 || currentSize < monitored.quantity * 0.99) {
      // Position fully closed or significantly reduced on Delta
      logger.info(`[PositionMonitor] Position closed on Delta: ${symbol} ${side}`);
      await this.handlePositionClosed(monitored, position, 'delta_closed');
      return;
    }

    // Check SL/TP hit using mark price from ticker
    const markPrice = await deltaSyncService.getMarkPrice(symbol);
    if (markPrice === null) return;

    this.checkSlTpHit(monitored, markPrice);
  }

  /**
   * Check all monitored positions (safety net)
   */
  private static async checkAllPositions(): Promise<void> {
    for (const [key, monitored] of this.monitoredPositions.entries()) {
      try {
        const positions = deltaSyncService.getPositions();
        const foundPos = positions.find((p: any) =>
          this.normalizeSymbol(p.product_symbol) === monitored.symbol &&
          ((monitored.side === 'LONG' && p.side === 'buy') ||
            (monitored.side === 'SHORT' && p.side === 'sell'))
        );

        if (!foundPos) {
          // Position no longer exists on Delta
          logger.warn(`[PositionMonitor] Position disappeared from Delta: ${key}`);
          await this.handlePositionClosed(monitored, null, 'disappeared');
          continue;
        }

        const markPrice = await deltaSyncService.getMarkPrice(monitored.symbol);
        if (markPrice !== null) {
          this.checkSlTpHit(monitored, markPrice);
        }
      } catch (err) {
        logger.error(`[PositionMonitor] Error checking position ${key}:`, err);
      }
    }
  }

  /**
   * Check if SL or TP was hit
   */
  private static checkSlTpHit(monitored: MonitoredPosition, markPrice: number): void {
    const { side, stopLossPrice, takeProfitPrice, symbol } = monitored;

    let hitSl = false;
    let hitTp = false;
    let exitPrice = markPrice;
    let exitReason = '';

    if (side === 'LONG') {
      if (markPrice <= stopLossPrice) {
        hitSl = true;
        exitPrice = stopLossPrice;
        exitReason = 'SL_HIT';
      } else if (markPrice >= takeProfitPrice) {
        hitTp = true;
        exitPrice = takeProfitPrice;
        exitReason = 'TP_HIT';
      }
    } else { // SHORT
      if (markPrice >= stopLossPrice) {
        hitSl = true;
        exitPrice = stopLossPrice;
        exitReason = 'SL_HIT';
      } else if (markPrice <= takeProfitPrice) {
        hitTp = true;
        exitPrice = takeProfitPrice;
        exitReason = 'TP_HIT';
      }
    }

    if (hitSl || hitTp) {
      logger.info(`[PositionMonitor] ${exitReason} for ${symbol} ${side} @ ${exitPrice} (mark=${markPrice})`);
      this.closePosition(monitored, exitPrice, exitReason);
    }
  }

  /**
   * Close position via Delta and record trade
   */
  private static async closePosition(monitored: MonitoredPosition, exitPrice: number, reason: string): Promise<void> {
    const key = `${monitored.symbol}_${monitored.side}`;
    this.monitoredPositions.delete(key);

    try {
      // Close position via Delta
      const closeResult = await deltaSyncService.closePosition(monitored.symbol);
      if (!closeResult.success) {
        throw new Error(closeResult.error || 'Close position failed');
      }

      // Calculate actual fees and PnL
      const accounting = TradeAccountingService.calculateAccounting({
        tradeId: monitored.tradeId,
        symbol: monitored.symbol,
        side: monitored.side,
        entryPrice: monitored.entryPrice,
        exitPrice,
        quantity: monitored.quantity,
        leverage: monitored.leverage,
        isEntryMaker: false, // Market order
        isExitMaker: false,  // Market order
        stopLoss: monitored.stopLossPrice,
        takeProfit: monitored.takeProfitPrice,
      });

      // Update wallet with net PnL
      await walletService.applyTradeResult(
        accounting.netPnL,
        accounting.grossPnL,
        monitored.quantity * monitored.entryPrice / monitored.leverage // margin released
      );

      // Update trade ledger with actual results
      await prisma.tradeLedger.update({
        where: { tradeId: monitored.tradeId },
        data: {
          exitPrice,
          grossPnL: accounting.grossPnL,
          tradingFee: accounting.tradingFee,
          fundingFee: accounting.fundingFee,
          tax: accounting.tax,
          netPnL: accounting.netPnL,
          resultStatus: accounting.resultStatus,
          closedAt: new Date(),
          syncStatus: 'SYNCED',
        },
      });

      // Mark Order Block as used
      CanonicalOBRegistry.markTraded(monitored.orderBlockId);

      // Emit event for frontend
      eventBus.emit('trade:closed', {
        tradeId: monitored.tradeId,
        symbol: monitored.symbol,
        side: monitored.side,
        entryPrice: monitored.entryPrice,
        exitPrice,
        quantity: monitored.quantity,
        leverage: monitored.leverage,
        grossPnL: accounting.grossPnL,
        netPnL: accounting.netPnL,
        fees: accounting.tradingFee + accounting.fundingFee,
        tax: accounting.tax,
        reason,
        closedAt: new Date().toISOString(),
      });

      // Update scanner state
      await prisma.scannerState.updateMany({
        data: { tradesTotal: { increment: 1 } },
      });

      logger.info(`[PositionMonitor] Trade closed: ${monitored.symbol} ${reason} netPnL=${accounting.netPnL}`);
    } catch (err) {
      logger.error(`[PositionMonitor] Failed to close position ${key}:`, err);
      // Re-add to monitoring if close failed
      this.monitoredPositions.set(key, monitored);
    }
  }

  /**
   * Handle position closed on Delta (not by us)
   */
  private static async handlePositionClosed(
    monitored: MonitoredPosition,
    deltaPosition: DeltaPosition | null,
    reason: string
  ): Promise<void> {
    const key = `${monitored.symbol}_${monitored.side}`;
    this.monitoredPositions.delete(key);

    // Get actual exit price from Delta if available
    let exitPrice = monitored.stopLossPrice;
    if (deltaPosition) {
      // Use mark price from ticker for the symbol
      const markPrice = await deltaSyncService.getMarkPrice(monitored.symbol);
      if (markPrice !== null) {
        exitPrice = markPrice;
      }
    }

    try {
      const accounting = TradeAccountingService.calculateAccounting({
        tradeId: monitored.tradeId,
        symbol: monitored.symbol,
        side: monitored.side,
        entryPrice: monitored.entryPrice,
        exitPrice,
        quantity: monitored.quantity,
        leverage: monitored.leverage,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: monitored.stopLossPrice,
        takeProfit: monitored.takeProfitPrice,
      });

      await walletService.applyTradeResult(
        accounting.netPnL,
        accounting.grossPnL,
        monitored.quantity * monitored.entryPrice / monitored.leverage
      );

      await prisma.tradeLedger.update({
        where: { tradeId: monitored.tradeId },
        data: {
          exitPrice,
          grossPnL: accounting.grossPnL,
          tradingFee: accounting.tradingFee,
          fundingFee: accounting.fundingFee,
          tax: accounting.tax,
          netPnL: accounting.netPnL,
          resultStatus: accounting.resultStatus,
          closedAt: new Date(),
          syncStatus: 'SYNCED',
        },
      });

      CanonicalOBRegistry.markTraded(monitored.orderBlockId);

      eventBus.emit('trade:closed', {
        tradeId: monitored.tradeId,
        symbol: monitored.symbol,
        side: monitored.side,
        entryPrice: monitored.entryPrice,
        exitPrice,
        quantity: monitored.quantity,
        leverage: monitored.leverage,
        grossPnL: accounting.grossPnL,
        netPnL: accounting.netPnL,
        fees: accounting.tradingFee + accounting.fundingFee,
        tax: accounting.tax,
        reason: `EXTERNAL_${reason}`,
        closedAt: new Date().toISOString(),
      });

      logger.info(`[PositionMonitor] External close recorded: ${monitored.symbol} netPnL=${accounting.netPnL}`);
    } catch (err) {
      logger.error('[PositionMonitor] Failed to record external close:', err);
    }
  }

  /**
   * Add a new position to monitoring
   */
  public static addPosition(position: MonitoredPosition): void {
    const key = `${position.symbol}_${position.side}`;
    this.monitoredPositions.set(key, position);
    logger.info(`[PositionMonitor] Now monitoring: ${position.symbol} ${position.side} SL=${position.stopLossPrice} TP=${position.takeProfitPrice}`);
  }

  /**
   * Normalize symbol (BTCUSD -> BTCUSD.P)
   */
  private static normalizeSymbol(symbol: string): string {
    if (symbol.endsWith('.P')) return symbol;
    return `${symbol}.P`;
  }

  /**
   * Get all currently monitored positions
   */
  public static getMonitoredPositions(): MonitoredPosition[] {
    return Array.from(this.monitoredPositions.values());
  }

  /**
   * Get reconciliation status
   */
  public static getReconciliationStatus(): { status: string; lastTime: Date | null; isRecovering: boolean } {
    return PositionRecoveryService.getReconciliationStatus();
  }
}