import { describe, it, expect, beforeEach } from 'vitest';
import { ExecutionMode } from '@algoapp/shared';
import { EnvValidator } from '../../backend/src/config/envValidator.js';
import { LiveTradingGuard } from '../../backend/src/modules/production/services/liveTradingGuard.js';
import { ProductionMetricsService } from '../../backend/src/modules/production/services/productionMetricsService.js';
import { BackupManager } from '../../backend/src/modules/production/services/backupManager.js';
import { EmergencyKillSwitch } from '../../backend/src/modules/execution/adapters/delta/emergencyKillSwitch.js';

describe('Production Deployment & Live Activation E2E Integration Tests', () => {
  beforeEach(() => {
    process.env.NODE_ENV = 'production';
    process.env.DELTA_API_KEY = 'sandbox_test_key_001';
    process.env.DELTA_API_SECRET = 'sandbox_test_secret_999';
    process.env.ALLOW_LIVE_TRADING = 'true';
    EmergencyKillSwitch.setKillSwitch(false);
    LiveTradingGuard.setExplicitUserConfirmed(false);
    LiveTradingGuard.setLiveModeActive(false);
  });

  it('should validate startup environment configuration successfully', () => {
    const config = EnvValidator.validateEnv();
    expect(config.port).toBe(4000);
    expect(config.databaseUrl).toBeDefined();
  });

  it('should REJECT Live Trading when explicit user confirmation is missing', async () => {
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.isAllowed).toBe(false);
    expect(safety.rejectionReasons.length).toBeGreaterThan(0);
    expect(safety.rejectionReasons.some((r) => r.includes('confirmation is missing'))).toBe(true);
  });

  it('should APPROVE Live Trading when all 8 safety guards are satisfied', async () => {
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);

    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    if (!safety.isAllowed) {
      console.log('SAFETY CHECKS FAILED:', JSON.stringify(safety, null, 2));
    }
    expect(safety.isAllowed).toBe(true);
    expect(safety.rejectionReasons.length).toBe(0);
  });

  it('should REJECT execution request in ExecutionValidator when Emergency Kill Switch is ACTIVE', async () => {
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);
    EmergencyKillSwitch.setKillSwitch(true);

    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.isAllowed).toBe(false);
    expect(safety.rejectionReasons.some((r) => r.includes('Kill Switch is ACTIVE'))).toBe(true);
  });

  it('should collect production metrics (CPU, Memory, Latency, Uptime)', async () => {
    const metrics = await ProductionMetricsService.getMetrics();
    expect(metrics.cpuUsagePercent).toBeGreaterThanOrEqual(0);
    expect(metrics.memoryUsageMb).toBeGreaterThan(0);
    expect(metrics.uptimeSeconds).toBeGreaterThanOrEqual(0);
  });

  it('should trigger and verify backup procedures', async () => {
    const status = await BackupManager.triggerBackup();
    expect(status.status).toBe('SUCCESS');
    expect(status.totalBackupSizeMb).toBeGreaterThan(0);
  });
});
