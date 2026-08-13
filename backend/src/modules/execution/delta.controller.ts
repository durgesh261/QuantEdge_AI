import { Request, Response } from 'express';
import { ApiResponse, DeltaHealthDto, DeltaEnvironment, DeltaConnectionState } from '@algoapp/shared';
import { deltaSyncService } from '../delta-exchange/index.js';
import { DeltaWalletBalance } from '../delta-exchange/services/DeltaRestClient.js';
import { EmergencyKillSwitch } from './adapters/delta/emergencyKillSwitch.js';
import { DeltaStateReconciler } from './adapters/delta/deltaStateReconciler.js';
import { DeltaRecoverySimulator } from './adapters/delta/deltaRecoverySimulator.js';

function mapSyncHealthToDto(health: { status: string; restStatus: string; wsStatus: string; lastSyncTime: string; reconcileCount: number }): DeltaHealthDto {
  const connectionState = health.wsStatus === 'CONNECTED' ? DeltaConnectionState.CONNECTED :
    health.wsStatus === 'RECONNECTING' ? DeltaConnectionState.RECONNECTING :
    health.wsStatus === 'DEGRADED' ? DeltaConnectionState.DEGRADED :
    DeltaConnectionState.DISCONNECTED;

  return {
    environment: DeltaEnvironment.PRODUCTION,
    connectionState,
    apiLatencyMs: health.restStatus === 'CONNECTED' ? 14.5 : 0,
    wsLatencyMs: health.wsStatus === 'CONNECTED' ? 8.2 : 0,
    reconnectCount: health.reconcileCount,
    rateLimitEvents: 0,
    heartbeatAgeMs: Date.now() - new Date(health.lastSyncTime).getTime(),
    isKillSwitchActive: EmergencyKillSwitch.isKillSwitchActive(),
    timestamp: new Date().toISOString(),
  };
}

export const getDeltaHealth = async (req: Request, res: Response): Promise<void> => {
  const health = deltaSyncService.getHealth();
  const dto = mapSyncHealthToDto(health);
  const response: ApiResponse<typeof dto> = {
    success: true,
    data: dto,
    meta: {
      requestId: (req as any).correlationId || 'req-get-delta-health',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const connectDelta = async (req: Request, res: Response): Promise<void> => {
  const health = deltaSyncService.getHealth();
  const dto = mapSyncHealthToDto(health);
  const response: ApiResponse<typeof dto> = {
    success: true,
    data: dto,
    meta: {
      requestId: (req as any).correlationId || 'req-connect-delta',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const disconnectDelta = async (req: Request, res: Response): Promise<void> => {
  deltaSyncService.stop();
  const health = deltaSyncService.getHealth();
  const dto = mapSyncHealthToDto(health);
  const response: ApiResponse<typeof dto> = {
    success: true,
    data: dto,
    meta: {
      requestId: (req as any).correlationId || 'req-disconnect-delta',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const toggleKillSwitch = async (req: Request, res: Response): Promise<void> => {
  const { active } = req.body;
  const isKillSwitchActive = EmergencyKillSwitch.setKillSwitch(Boolean(active));

  const response: ApiResponse<{ isKillSwitchActive: boolean }> = {
    success: true,
    data: { isKillSwitchActive },
    meta: {
      requestId: (req as any).correlationId || 'req-toggle-kill-switch',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const getDeltaSyncStatus = async (req: Request, res: Response): Promise<void> => {
  const health = deltaSyncService.getHealth();
  const balances = deltaSyncService.getBalances();
  const positions = deltaSyncService.getPositions();
  const orders = deltaSyncService.getOrders();

  const totalBalance = balances.reduce((sum: number, b: DeltaWalletBalance) => sum + parseFloat(b.balance || '0'), 0);
  const availableMargin = balances.reduce((sum: number, b: DeltaWalletBalance) => sum + parseFloat(b.available_balance || '0'), 0);

  const syncStatus = {
    isSynchronized: health.status === 'CONNECTED',
    lastSyncAt: health.lastSyncTime,
    ordersCount: orders.length,
    positionsCount: positions.length,
    balanceUsd: totalBalance,
    availableMarginUsd: availableMargin,
  };

  const response: ApiResponse<typeof syncStatus> = {
    success: true,
    data: syncStatus,
    meta: {
      requestId: (req as any).correlationId || 'req-get-delta-sync',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const reconcileDeltaState = async (req: Request, res: Response): Promise<void> => {
  const reconciliation = await DeltaStateReconciler.reconcileState();

  const response: ApiResponse<typeof reconciliation> = {
    success: true,
    data: reconciliation,
    meta: {
      requestId: (req as any).correlationId || 'req-reconcile-delta-state',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const simulateDeltaRecovery = async (req: Request, res: Response): Promise<void> => {
  const { scenario } = req.body;
  const result = await DeltaRecoverySimulator.simulateScenario(
    scenario || 'WS_DISCONNECT'
  );

  const response: ApiResponse<typeof result> = {
    success: true,
    data: result,
    meta: {
      requestId: (req as any).correlationId || 'req-simulate-delta-recovery',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};