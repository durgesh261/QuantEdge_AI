import { deltaSyncService } from '../../delta-exchange/index.js';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { PositionMonitorService } from './PositionMonitorService.js';
import { TradeAccountingService } from '../../trade-accounting/services/tradeAccounting.service.js';
import { WalletEngineService } from '../../trade-accounting/services/walletEngine.service.js';
import { eventBus } from '../../../services/EventBus.js';

const walletService = new WalletEngineService();

export interface PersistedPosition {
  tradeId: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  quantity: number;
  leverage: number;
  executedAt: Date;
}

export interface DeltaPositionInfo {
  symbol: string;
  side: 'LONG' | 'SHORT';
  size: number;
  entryPrice: number;
  markPrice: number;
  liquidationPrice: number;
  unrealizedPnl: number;
  productId: number;
}

export interface ReconciliationResult {
  matched: PositionMatch[];
  deltaOnly: DeltaPositionInfo[];
  localOnly: PersistedPosition[];
  errors: string[];
}

export interface PositionMatch {
  local: PersistedPosition;
  delta: DeltaPositionInfo;
  action: 'CONTINUE_MONITORING' | 'RECONCILE_CLOSED';
}

export class PositionRecoveryService {
  private static readonly RECONCILIATION_MAX_RETRIES = 3;
  private static readonly RECONCILIATION_RETRY_DELAY_MS = 5000;
  private static isRecovering = false;
  private static reconciliationStatus: 'IDLE' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'DELTA_UNAVAILABLE' = 'IDLE';
  private static lastReconciliationTime: Date | null = null;

  /**
   * Main entry point - called at startup to recover positions
   */
  public static async recoverPositions(): Promise<ReconciliationResult> {
    if (this.isRecovering) {
      logger.warn('[PositionRecovery] Recovery already in progress, skipping');
      return { matched: [], deltaOnly: [], localOnly: [], errors: ['Recovery already in progress'] };
    }

    this.isRecovering = true;
    this.reconciliationStatus = 'RUNNING';
    const errors: string[] = [];

    try {
      logger.info('[PositionRecovery] Starting position recovery...');

      // Step 1: Wait for Delta connection (with retries)
      const deltaConnected = await this.waitForDeltaConnection();
      if (!deltaConnected) {
        this.reconciliationStatus = 'DELTA_UNAVAILABLE';
        errors.push('Delta Exchange unavailable - recovery deferred');
        logger.warn('[PositionRecovery] Delta unavailable, recovery deferred');
        return { matched: [], deltaOnly: [], localOnly: [], errors };
      }

      // Step 2: Load persisted open positions from DB
      const localPositions = await this.loadPersistedPositions();
      logger.info(`[PositionRecovery] Loaded ${localPositions.length} open positions from DB`);

      // Step 3: Fetch current positions from Delta
      const deltaPositions = await this.fetchDeltaPositions();
      logger.info(`[PositionRecovery] Found ${deltaPositions.length} open positions on Delta`);

      // Step 4: Reconcile
      const result = await this.reconcilePositions(localPositions, deltaPositions);

      // Step 5: Process results
      await this.processReconciliationResult(result);

      this.reconciliationStatus = 'COMPLETED';
      this.lastReconciliationTime = new Date();
      logger.info(`[PositionRecovery] Recovery complete: ${result.matched.length} matched, ${result.deltaOnly.length} delta-only, ${result.localOnly.length} local-only`);

      return result;
    } catch (err) {
      this.reconciliationStatus = 'FAILED';
      const errorMsg = err instanceof Error ? err.message : (typeof err === 'string' ? err : JSON.stringify(err));
      errors.push(`Recovery failed: ${errorMsg}`);
      logger.error('[PositionRecovery] Recovery failed:', err);
      return { matched: [], deltaOnly: [], localOnly: [], errors };
    } finally {
      this.isRecovering = false;
    }
  }

  /**
   * Wait for Delta connection to be established
   */
  private static async waitForDeltaConnection(): Promise<boolean> {
    for (let attempt = 1; attempt <= this.RECONCILIATION_MAX_RETRIES; attempt++) {
      const status = deltaSyncService.getConnectionStatus();
      if (status === 'CONNECTED') {
        // Also verify we can actually fetch positions
        try {
          const positions = deltaSyncService.getPositions();
          if (positions !== undefined) {
            logger.info('[PositionRecovery] Delta connection verified');
            return true;
          }
        } catch {
          // Continue retrying
        }
      }

      if (attempt < this.RECONCILIATION_MAX_RETRIES) {
        logger.info(`[PositionRecovery] Delta not ready (attempt ${attempt}/${this.RECONCILIATION_MAX_RETRIES}), waiting...`);
        await new Promise(resolve => setTimeout(resolve, this.RECONCILIATION_RETRY_DELAY_MS));
      }
    }
    return false;
  }

  /**
   * Load persisted open positions from trade_ledger
   */
  private static async loadPersistedPositions(): Promise<PersistedPosition[]> {
    const openTrades = await prisma.tradeLedger.findMany({
      where: { 
        exitPrice: null,
        syncStatus: { not: 'CLOSED' } // Exclude already reconciled
      },
      orderBy: { executedAt: 'asc' },
    });

    return openTrades.map(trade => ({
      tradeId: trade.tradeId,
      symbol: trade.symbol,
      side: trade.side as 'LONG' | 'SHORT',
      entryPrice: trade.entryPrice,
      stopLoss: trade.stopLoss,
      takeProfit: trade.takeProfit,
      quantity: trade.quantity,
      leverage: trade.leverage,
      executedAt: trade.executedAt,
    }));
  }

  /**
   * Fetch current positions from Delta Exchange
   */
  private static async fetchDeltaPositions(): Promise<DeltaPositionInfo[]> {
    try {
      const positions = deltaSyncService.getPositions();
      const result: DeltaPositionInfo[] = [];

      for (const pos of positions) {
        const size = Math.abs(pos.size);
        if (size === 0) continue;

        const symbol = this.normalizeSymbol(pos.product_symbol);
        const side = pos.side === 'buy' ? 'LONG' : 'SHORT';
        const entryPrice = parseFloat(pos.entry_price);
        
        // Fetch mark price from ticker
        const markPrice = await deltaSyncService.getMarkPrice(symbol);
        
        const liquidationPrice = parseFloat(pos.liquidation_price || '0');
        const unrealizedPnl = parseFloat(pos.unrealized_pnl || '0');

        if (!isNaN(entryPrice) && markPrice !== null && size > 0) {
          result.push({
            symbol,
            side,
            size,
            entryPrice,
            markPrice,
            liquidationPrice: isNaN(liquidationPrice) ? 0 : liquidationPrice,
            unrealizedPnl: isNaN(unrealizedPnl) ? 0 : unrealizedPnl,
            productId: pos.product_id,
          });
        }
      }

      return result;
    } catch (err) {
      logger.error('[PositionRecovery] Failed to fetch Delta positions:', err);
      return [];
    }
  }

  /**
   * Reconcile local positions with Delta positions
   */
  private static async reconcilePositions(
    localPositions: PersistedPosition[],
    deltaPositions: DeltaPositionInfo[]
  ): Promise<ReconciliationResult> {
    const matched: PositionMatch[] = [];
    const deltaOnly: DeltaPositionInfo[] = [];
    const localOnly: PersistedPosition[] = [];
    const errors: string[] = [];

    // Create lookup maps
    const deltaByKey = new Map<string, DeltaPositionInfo>();
    for (const dp of deltaPositions) {
      const key = `${dp.symbol}_${dp.side}`;
      deltaByKey.set(key, dp);
    }

    // Match local positions to Delta
    for (const local of localPositions) {
      const key = `${local.symbol}_${local.side}`;
      const delta = deltaByKey.get(key);

      if (delta) {
        // Position exists in both - verify consistency
        const sizeMatch = Math.abs(delta.size - local.quantity) < local.quantity * 0.01; // 1% tolerance
        const entryMatch = Math.abs(delta.entryPrice - local.entryPrice) < local.entryPrice * 0.001; // 0.1% tolerance

        if (sizeMatch && entryMatch) {
          matched.push({
            local,
            delta,
            action: 'CONTINUE_MONITORING',
          });
        } else {
          // Mismatch - log but continue monitoring with Delta as authority
          logger.warn(`[PositionRecovery] Position mismatch for ${key}: local qty=${local.quantity} delta qty=${delta.size}, local entry=${local.entryPrice} delta entry=${delta.entryPrice}`);
          matched.push({
            local,
            delta,
            action: 'CONTINUE_MONITORING', // Delta is source of truth
          });
        }
        deltaByKey.delete(key);
      } else {
        // Local position not found on Delta
        localOnly.push(local);
      }
    }

    // Remaining Delta positions not in local DB
    for (const delta of deltaByKey.values()) {
      deltaOnly.push(delta);
    }

    return { matched, deltaOnly, localOnly, errors };
  }

  /**
   * Process reconciliation results and take actions
   */
  private static async processReconciliationResult(result: ReconciliationResult): Promise<void> {
    // 1. Matched positions - continue monitoring
    for (const match of result.matched) {
      const { local, delta } = match;
      
      PositionMonitorService.addPosition({
        symbol: local.symbol,
        side: local.side,
        entryPrice: local.entryPrice,
        stopLossPrice: local.stopLoss,
        takeProfitPrice: local.takeProfit,
        quantity: delta.size, // Use Delta's actual size
        leverage: local.leverage,
        tradeId: local.tradeId,
        orderBlockId: '', // Will be populated by CanonicalOBRegistry if available
        entryTime: local.executedAt.toISOString(),
        deltaProductSymbol: delta.symbol,
        deltaPositionId: delta.productId,
      });

      logger.info(`[PositionRecovery] Resumed monitoring: ${local.symbol} ${local.side} (qty=${delta.size})`);
    }

    // 2. Delta-only positions - recover them
    for (const delta of result.deltaOnly) {
      // Find if there's a trade ledger entry we missed
      const existingTrade = await prisma.tradeLedger.findFirst({
        where: {
          symbol: delta.symbol,
          side: delta.side,
          exitPrice: null,
        },
        orderBy: { executedAt: 'desc' },
      });

      if (existingTrade) {
        // Trade exists in DB but wasn't loaded - update and monitor
        PositionMonitorService.addPosition({
          symbol: delta.symbol,
          side: delta.side,
          entryPrice: delta.entryPrice,
          stopLossPrice: existingTrade.stopLoss,
          takeProfitPrice: existingTrade.takeProfit,
          quantity: delta.size,
          leverage: existingTrade.leverage,
          tradeId: existingTrade.tradeId,
          orderBlockId: '',
          entryTime: existingTrade.executedAt.toISOString(),
          deltaProductSymbol: delta.symbol,
          deltaPositionId: delta.productId,
        });
        logger.info(`[PositionRecovery] Recovered delta-only position: ${delta.symbol} ${delta.side}`);
      } else {
        // No trade record - create a minimal tracking entry
        // This shouldn't happen in normal operation but we handle it gracefully
        logger.warn(`[PositionRecovery] Delta-only position with no trade record: ${delta.symbol} ${delta.side}`);
      }
    }

    // 3. Local-only positions - reconcile as closed
    for (const local of result.localOnly) {
      logger.warn(`[PositionRecovery] Local position not on Delta, marking closed: ${local.symbol} ${local.side} (${local.tradeId})`);

      // Get mark price for accounting
      const markPrice = await deltaSyncService.getMarkPrice(local.symbol);
      const exitPrice = markPrice ?? local.stopLoss;

      const accounting = TradeAccountingService.calculateAccounting({
        tradeId: local.tradeId,
        symbol: local.symbol,
        side: local.side,
        entryPrice: local.entryPrice,
        exitPrice,
        quantity: local.quantity,
        leverage: local.leverage,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: local.stopLoss,
        takeProfit: local.takeProfit,
      });

      await walletService.applyTradeResult(
        accounting.netPnL,
        accounting.grossPnL,
        local.quantity * local.entryPrice / local.leverage
      );

      await prisma.tradeLedger.update({
        where: { tradeId: local.tradeId },
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

      // Mark OB as used if we have it
      if (local.tradeId) {
        // We'd need the orderBlockId - for now just log
        logger.info(`[PositionRecovery] Marked local-only trade as closed: ${local.tradeId}`);
      }

      // Emit event
      eventBus.emit('trade:closed', {
        tradeId: local.tradeId,
        symbol: local.symbol,
        side: local.side,
        entryPrice: local.entryPrice,
        exitPrice,
        quantity: local.quantity,
        leverage: local.leverage,
        grossPnL: accounting.grossPnL,
        netPnL: accounting.netPnL,
        fees: accounting.tradingFee + accounting.fundingFee,
        tax: accounting.tax,
        reason: 'EXTERNAL_RECONCILED',
        closedAt: new Date().toISOString(),
      });
    }

    // 4. Log any errors
    for (const error of result.errors) {
      logger.error(`[PositionRecovery] ${error}`);
    }
  }

  /**
   * Normalize symbol (BTCUSD -> BTCUSD.P)
   */
  private static normalizeSymbol(symbol: string): string {
    if (symbol.endsWith('.P')) return symbol;
    return `${symbol}.P`;
  }

  /**
   * Get current reconciliation status
   */
  public static getReconciliationStatus(): { status: string; lastTime: Date | null; isRecovering: boolean } {
    return {
      status: this.reconciliationStatus,
      lastTime: this.lastReconciliationTime,
      isRecovering: this.isRecovering,
    };
  }

  /**
   * Force re-reconciliation (manual trigger)
   */
  public static async forceReconcile(): Promise<ReconciliationResult> {
    return this.recoverPositions();
  }
}