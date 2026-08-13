import { ExecutionMode } from '@algoapp/shared';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';

export class ProductionModeStore {
  /**
   * Get the persisted execution mode preference from DB.
   * Defaults to ExecutionMode.PAPER if uninitialized or DB error.
   */
  public static async getPersistedExecutionMode(): Promise<ExecutionMode> {
    try {
      const settings = await prisma.systemSettings.findFirst();
      if (settings?.executionMode === ExecutionMode.LIVE) {
        return ExecutionMode.LIVE;
      }
      return ExecutionMode.PAPER;
    } catch (err) {
      logger.warn('[ProductionModeStore] Failed to read persisted execution mode from DB, falling back to PAPER:', err);
      return ExecutionMode.PAPER;
    }
  }

  /**
   * Persist the execution mode preference to DB.
   */
  public static async persistExecutionMode(mode: ExecutionMode): Promise<void> {
    try {
      const existing = await prisma.systemSettings.findFirst();
      if (existing) {
        await prisma.systemSettings.update({
          where: { id: existing.id },
          data: { executionMode: mode },
        });
      } else {
        await prisma.systemSettings.create({
          data: {
            id: 'default-settings',
            executionMode: mode,
          },
        });
      }
      logger.info(`[ProductionModeStore] Persisted execution mode: ${mode}`);
    } catch (err) {
      logger.error('[ProductionModeStore] Failed to persist execution mode to DB:', err);
    }
  }
}
