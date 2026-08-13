export type AppEnvironment = 'development' | 'testing' | 'sandbox' | 'production';

export interface LiveTradingSafetyCheckDto {
  isAllowed: boolean;
  checks: {
    explicitUserConfirmed: boolean;
    validEnvironment: boolean;
    productionApiKeysPresent: boolean;
    killSwitchInactive: boolean;
    challengeGuardEnabled: boolean;
    liveModeActive: boolean;
    deltaConnectionHealthy: boolean;
    tradingViewConnectionHealthy: boolean;
  };
  rejectionReasons: string[];
  timestamp: string;
}

export interface ProductionMetricsDto {
  cpuUsagePercent: number;
  memoryUsageMb: number;
  apiLatencyMs: number;
  pipelineLatencyMs: number;
  executionLatencyMs: number;
  reconnectCount: number;
  errorCount: number;
  uptimeSeconds: number;
  timestamp: string;
}

export interface BackupStatusDto {
  databaseBackupAt: string;
  journalBackupAt: string;
  replayBackupAt: string;
  configBackupAt: string;
  totalBackupSizeMb: number;
  status: 'SUCCESS' | 'RUNNING' | 'FAILED';
}

export interface ProductionOverviewDto {
  environment: AppEnvironment;
  activeExecutionMode: 'PAPER' | 'LIVE';
  isLiveTradingAllowed: boolean;
  safetyCheck: LiveTradingSafetyCheckDto;
  metrics: ProductionMetricsDto;
  backupStatus: BackupStatusDto;
  updatedAt: string;
}
