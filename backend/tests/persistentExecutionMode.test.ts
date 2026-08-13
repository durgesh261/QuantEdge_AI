/**
 * Phase C.6.1 -- Persistent Execution Mode vs LIVE Authorization Hardening
 *
 * Contract verified:
 *   1. Persisted LIVE preference is restored as activeExecutionMode (for UI display).
 *   2. Authorization flags (isExplicitUserConfirmed, isLiveModeActive) are ALWAYS
 *      cleared on restart -- never reconstructed from the DB.
 *   3. After restart with LIVE preference, evaluateSafety() rejects LIVE execution.
 *   4. PAPER/null preference -> PAPER mode, flags cleared.
 *   5. setExecutionMode only re-activates LIVE after user confirmation + safety pass.
 */

// ── Mocks (hoisted before source imports) ────────────────────────────────────

const mockGetPersistedMode = jest.fn();
const mockPersistMode = jest.fn().mockResolvedValue(undefined);

jest.mock('../src/modules/production/services/productionModeStore.js', () => ({
  ProductionModeStore: {
    getPersistedExecutionMode: mockGetPersistedMode,
    persistExecutionMode: mockPersistMode,
  },
}));

jest.mock('../src/db.js', () => ({
  prisma: {
    systemSettings: {
      findFirst: jest.fn().mockResolvedValue(null),
      upsert: jest.fn().mockResolvedValue({}),
    },
  },
}));

jest.mock('../src/modules/execution/adapters/delta/emergencyKillSwitch.js', () => ({
  EmergencyKillSwitch: { isKillSwitchActive: jest.fn().mockReturnValue(false) },
}));

jest.mock('../src/modules/tradingview-adapter/services/tradingViewHealthMonitor.js', () => ({
  TradingViewHealthMonitor: { getHealth: jest.fn().mockResolvedValue({ status: 'CONNECTED' }) },
}));

// ── Imports ───────────────────────────────────────────────────────────────────

import { ExecutionMode } from '@algoapp/shared';
import {
  initializeExecutionModeFromPersistence,
  getActiveExecutionMode,
  setActiveExecutionModeForTest,
} from '../src/modules/production/production.controller.js';
import { LiveTradingGuard } from '../src/modules/production/services/liveTradingGuard.js';

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('Phase C.6.1 -- Persistent Execution Mode vs LIVE Authorization', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Pre-set flags to true so we can detect if init incorrectly leaves them set
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);
    setActiveExecutionModeForTest(ExecutionMode.PAPER);
    process.env.ALLOW_LIVE_TRADING = 'true';
    process.env.NODE_ENV = 'development';
    process.env.DELTA_API_KEY = 'test-key';
    process.env.DELTA_API_SECRET = 'test-secret';
  });

  afterEach(() => {
    LiveTradingGuard.setExplicitUserConfirmed(false);
    LiveTradingGuard.setLiveModeActive(false);
    delete process.env.ALLOW_LIVE_TRADING;
  });

  // Restart with LIVE preference
  it('R1: restores LIVE as activeExecutionMode', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    const result = await initializeExecutionModeFromPersistence();
    expect(result).toBe(ExecutionMode.LIVE);
    expect(getActiveExecutionMode()).toBe(ExecutionMode.LIVE);
  });

  it('R2: LIVE preference -- explicitUserConfirmed is false after restart', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.checks.explicitUserConfirmed).toBe(false);
  });

  it('R3: LIVE preference -- liveModeActive is false after restart', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.checks.liveModeActive).toBe(false);
  });

  it('R4: LIVE preference -- safety.isAllowed is false after restart (fail-closed)', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.isAllowed).toBe(false);
  });

  it('R5: LIVE preference -- rejectionReasons includes missing confirmation', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.rejectionReasons.join(' ')).toMatch(/confirmation/i);
  });

  // Restart with PAPER preference
  it('R6: PAPER preference -- activeExecutionMode is PAPER', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.PAPER);
    const result = await initializeExecutionModeFromPersistence();
    expect(result).toBe(ExecutionMode.PAPER);
    expect(getActiveExecutionMode()).toBe(ExecutionMode.PAPER);
  });

  it('R7: PAPER preference -- authorization flags are cleared', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.PAPER);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.checks.explicitUserConfirmed).toBe(false);
    expect(safety.checks.liveModeActive).toBe(false);
  });

  // No preference (fresh install)
  it('R8: null preference (fresh install) -- defaults to PAPER', async () => {
    mockGetPersistedMode.mockResolvedValue(null);
    const result = await initializeExecutionModeFromPersistence();
    expect(result).toBe(ExecutionMode.PAPER);
    expect(getActiveExecutionMode()).toBe(ExecutionMode.PAPER);
  });

  it('R9: null preference -- authorization flags cleared and LIVE is blocked', async () => {
    mockGetPersistedMode.mockResolvedValue(null);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.checks.explicitUserConfirmed).toBe(false);
    expect(safety.checks.liveModeActive).toBe(false);
    expect(safety.isAllowed).toBe(false);
  });

  // PAPER safety always passes
  it('R10: PAPER mode safety always passes regardless of authorization flags', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.PAPER);
    await initializeExecutionModeFromPersistence();
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.PAPER);
    expect(safety.isAllowed).toBe(true);
  });

  // Spy verification: function never pre-sets flags to true
  it('R11: init must never call setExplicitUserConfirmed(true) or setLiveModeActive(true)', async () => {
    mockGetPersistedMode.mockResolvedValue(ExecutionMode.LIVE);
    const confirmSpy = jest.spyOn(LiveTradingGuard, 'setExplicitUserConfirmed');
    const activeSpy = jest.spyOn(LiveTradingGuard, 'setLiveModeActive');
    await initializeExecutionModeFromPersistence();
    expect(confirmSpy.mock.calls.filter(([v]) => v === true)).toHaveLength(0);
    expect(activeSpy.mock.calls.filter(([v]) => v === true)).toHaveLength(0);
  });

  // Kill switch independence
  it('R12: kill switch blocks LIVE even when authorization flags are manually set', async () => {
    const { EmergencyKillSwitch } = require('../src/modules/execution/adapters/delta/emergencyKillSwitch.js');
    (EmergencyKillSwitch.isKillSwitchActive as jest.Mock).mockReturnValueOnce(true);
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.isAllowed).toBe(false);
    expect(safety.checks.killSwitchInactive).toBe(false);
  });

  // ALLOW_LIVE_TRADING env independence
  it('R13: missing ALLOW_LIVE_TRADING blocks LIVE even with authorization flags set', async () => {
    delete process.env.ALLOW_LIVE_TRADING;
    LiveTradingGuard.setExplicitUserConfirmed(true);
    LiveTradingGuard.setLiveModeActive(true);
    const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
    expect(safety.isAllowed).toBe(false);
    expect(safety.checks.allowLiveTradingEnvSet).toBe(false);
  });
});
