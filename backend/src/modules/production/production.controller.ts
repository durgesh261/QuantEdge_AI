import { Request, Response } from 'express';
import { ApiResponse, ExecutionMode, AppEnvironment } from '@algoapp/shared';
import { LiveTradingGuard } from './services/liveTradingGuard.js';
import { ProductionMetricsService } from './services/productionMetricsService.js';
import { BackupManager } from './services/backupManager.js';
import { EnvValidator } from '../../config/envValidator.js';

import { ProductionModeStore } from './services/productionModeStore.js';

let activeExecutionMode: ExecutionMode = ExecutionMode.PAPER;

export const initializeExecutionModeFromPersistence = async (): Promise<ExecutionMode> => {
  const savedMode = await ProductionModeStore.getPersistedExecutionMode();
  if (savedMode === ExecutionMode.LIVE) {
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    activeExecutionMode = ExecutionMode.LIVE;
    if (!safety.isAllowed) {
      LiveTradingGuard.setExplicitUserConfirmed(false);
      LiveTradingGuard.setLiveModeActive(false);
    }
  } else {
    activeExecutionMode = ExecutionMode.PAPER;
    LiveTradingGuard.setExplicitUserConfirmed(false);
    LiveTradingGuard.setLiveModeActive(false);
  }
  return activeExecutionMode;
};

export const getActiveExecutionMode = (): ExecutionMode => activeExecutionMode;

export const setActiveExecutionModeForTest = (mode: ExecutionMode): void => {
  activeExecutionMode = mode;
};

export const getProductionOverview = async (req: Request, res: Response): Promise<void> => {
  const envConfig = EnvValidator.validateEnv();
  const safetyCheck = await LiveTradingGuard.evaluateSafety(activeExecutionMode);
  const metrics = await ProductionMetricsService.getMetrics();
  const backupStatus = await BackupManager.getBackupStatus();

  const overview = {
    environment: envConfig.nodeEnv as AppEnvironment,
    activeExecutionMode,
    isLiveTradingAllowed: safetyCheck.isAllowed && activeExecutionMode === ExecutionMode.LIVE,
    safetyCheck,
    metrics,
    backupStatus,
    updatedAt: new Date().toISOString(),
  };

  const response: ApiResponse<typeof overview> = {
    success: true,
    data: overview,
    meta: {
      requestId: (req as any).correlationId || 'req-get-prod-overview',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const setExecutionMode = async (req: Request, res: Response): Promise<void> => {
  const { mode, userConfirmed } = req.body;

  if (mode === ExecutionMode.LIVE) {
    if (userConfirmed !== 'CONFIRM_LIVE_TRADING') {
      res.status(400).json({
        success: false,
        error: 'LIVE_MODE_REJECTED: Explicit user confirmation phrase "CONFIRM_LIVE_TRADING" required to enable Live Trading.',
        meta: { requestId: (req as any).correlationId || 'req-set-mode', timestamp: new Date().toISOString() },
      });
      return;
    }

    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);

    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    if (!safety.isAllowed) {
      LiveTradingGuard.setExplicitUserConfirmed(false);
      LiveTradingGuard.setLiveModeActive(false);
      res.status(400).json({
        success: false,
        error: `LIVE_MODE_REJECTED: Safety evaluation failed: ${safety.rejectionReasons.join('; ')}`,
        meta: { requestId: (req as any).correlationId || 'req-set-mode', timestamp: new Date().toISOString() },
      });
      return;
    }

    activeExecutionMode = ExecutionMode.LIVE;
    await ProductionModeStore.persistExecutionMode(ExecutionMode.LIVE);
  } else {
    activeExecutionMode = ExecutionMode.PAPER;
    LiveTradingGuard.setExplicitUserConfirmed(false);
    LiveTradingGuard.setLiveModeActive(false);
    await ProductionModeStore.persistExecutionMode(ExecutionMode.PAPER);
  }

  const overview = {
    activeExecutionMode,
    userConfirmed: userConfirmed === 'CONFIRM_LIVE_TRADING',
    updatedAt: new Date().toISOString(),
  };

  const response: ApiResponse<typeof overview> = {
    success: true,
    data: overview,
    meta: {
      requestId: (req as any).correlationId || 'req-set-mode',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const triggerBackup = async (req: Request, res: Response): Promise<void> => {
  const backupStatus = await BackupManager.triggerBackup();

  const response: ApiResponse<typeof backupStatus> = {
    success: true,
    data: backupStatus,
    meta: {
      requestId: (req as any).correlationId || 'req-trigger-backup',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};
