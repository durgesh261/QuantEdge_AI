import { eventBus } from '../../../services/EventBus.js';
import { executionEngineService } from '../../execution-engine/services/ExecutionEngineService.js';
import { MarketScannerService } from '../../live-trading/services/MarketScannerService.js';
import { deltaSyncService } from '../../delta-exchange/index.js';
import { logger } from '../../../logger/index.js';

export type KillSwitchState = 'ACTIVE' | 'INACTIVE';

/**
 * Emergency Kill Switch Service
 * 
 * When activated:
 * 1. Stops the Market Scanner immediately
 * 2. Prevents any new order execution
 * 3. Optionally closes all open positions (configurable)
 * 4. Broadcasts state to all connected frontends
 * 
 * Strategy §27: Risk management is mandatory. Every decision must be logged.
 */
export class KillSwitchService {
  private static state: KillSwitchState = 'INACTIVE';
  public static activate(closePositions: boolean = false): void {
    if (this.state === 'ACTIVE') return;

    this.state = 'ACTIVE';

    logger.error('🚨 KILL SWITCH ACTIVATED — All trading halted');

    // 1. Stop scanner
    MarketScannerService.setState('STOPPED');

    // 2. Activate execution engine kill switch
    executionEngineService.setKillSwitch(true);

    // 3. Close all open positions if configured
    if (closePositions) {
      this.emergencyCloseAllPositions();
    }

    // 4. Broadcast to frontend
    eventBus.emit('kill_switch:activated', {
      timestamp: new Date().toISOString(),
      closePositions,
      reason: 'Manual emergency activation',
    });

    // 5. Log to system
    this.logKillSwitchEvent('ACTIVATED', closePositions);
  }

  public static deactivate(): void {
    if (this.state === 'INACTIVE') return;

    this.state = 'INACTIVE';

    logger.info('✅ Kill Switch DEACTIVATED — Trading resumed');

    executionEngineService.setKillSwitch(false);

    eventBus.emit('kill_switch:deactivated', {
      timestamp: new Date().toISOString(),
    });

    this.logKillSwitchEvent('DEACTIVATED', false);
  }

  public static getState(): KillSwitchState {
    return this.state;
  }

  public static isActive(): boolean {
    return this.state === 'ACTIVE';
  }

  private static async emergencyCloseAllPositions(): Promise<void> {
    try {
      const positions = deltaSyncService.getPositions();

      for (const position of positions) {
        const symbol = position.product_symbol;
        // Reverse the open side to flatten the position
        const side = position.side === 'buy' ? 'sell' : 'buy';
        const size = Number(position.size || 0);

        if (size > 0 && symbol) {
          await executionEngineService.placeOrder({
            symbol,
            side,
            orderType: 'market',
            size,
            reduceOnly: true,         // Never expand — flatten only
            isEmergencyClose: true,   // Grants emergency exemption from LIVE auth guard
            clientOrderId: `KILL-${symbol}-${Date.now()}`,
          });

          logger.warn({ symbol, size, side }, 'Emergency position closure executed');
        }
      }
    } catch (err) {
      logger.error({ err }, 'Failed to emergency close positions');
    }
  }

  private static async logKillSwitchEvent(action: string, closePositions: boolean): Promise<void> {
    try {
      const { prisma } = await import('../../../db.js');
      await prisma.systemLog.create({
        data: {
          level: action === 'ACTIVATED' ? 'CRITICAL' : 'INFO',
          service: 'KILL_SWITCH',
          message: `Kill Switch ${action}. Close positions: ${closePositions}`,
          metaJson: JSON.stringify({
            state: this.state,
            closePositions,
            timestamp: new Date().toISOString(),
          }),
        },
      });
    } catch (err) {
      logger.error({ err }, 'Failed to log kill switch event');
    }
  }
}
