/**
 * Phase C.6.2.1 — ExecutionEngineService LIVE Guard Tests
 *
 * Verifies that ExecutionEngineService.placeOrder() now enforces
 * LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE) on every normal
 * order path, while allowing emergency close orders through a strictly
 * constrained exemption.
 */

// ─── Mocks (hoisted before imports) ───────────────────────────────────────────

// Mock LiveTradingGuard so we control evaluateSafety() output
const mockEvaluateSafety = jest.fn();
jest.mock('../src/modules/production/services/liveTradingGuard.js', () => ({
  LiveTradingGuard: {
    evaluateSafety: mockEvaluateSafety,
    setExplicitUserConfirmed: jest.fn(),
    setLiveModeActive: jest.fn(),
  },
}));

// Mock deltaSyncService — controls balances, positions, restClient
const mockPlaceOrder = jest.fn();
const mockGetProduct = jest.fn();
const mockIsConfigured = jest.fn().mockReturnValue(true);
const mockGetPositions = jest.fn().mockReturnValue([]);
const mockGetBalances = jest.fn().mockReturnValue([
  { asset_symbol: 'USDT', balance: '10000', available_balance: '10000' },
]);
const mockGetRestClient = jest.fn().mockReturnValue({
  placeOrder: mockPlaceOrder,
  getProduct: mockGetProduct,
  isConfigured: mockIsConfigured,
});

jest.mock('../src/modules/delta-exchange/index.js', () => ({
  deltaSyncService: {
    getPositions: mockGetPositions,
    getBalances: mockGetBalances,
    getRestClient: mockGetRestClient,
    onPriceTick: jest.fn(),
  },
}));

// Mock db / prisma
jest.mock('../src/db.js', () => ({
  prisma: {
    systemSettings: { findFirst: jest.fn().mockResolvedValue(null) },
    tradeLedger: { create: jest.fn().mockResolvedValue({}) },
    orderRecord: { create: jest.fn().mockResolvedValue({}) },
  },
}));

// Mock OrderLifecycleService
jest.mock('../src/modules/execution-engine/services/OrderLifecycleService.js', () => ({
  orderLifecycleService: {
    createOrderRecord: jest.fn(),
    getOrder: jest.fn().mockReturnValue(null),
    transition: jest.fn(),
  },
}));

// Mock TradeAccountingTrigger
jest.mock('../src/modules/trade-accounting/TradeAccountingTrigger.js', () => ({
  tradeAccountingTrigger: { recordExecution: jest.fn(), initialize: jest.fn() },
}));

// Mock CandleEngine — provide live price so margin rule passes
jest.mock('../src/engine/CandleEngine.js', () => ({
  candleEngine: {
    getLiveCandle: jest.fn().mockReturnValue({ close: 95000 }),
  },
}));

// Mock EventBus
jest.mock('../src/services/EventBus.js', () => ({
  eventBus: { emit: jest.fn(), on: jest.fn() },
}));

// Mock EmergencyKillSwitch (used inside ExecutionEngineService.validateOrder via rule 2)
jest.mock('../src/modules/execution/adapters/delta/emergencyKillSwitch.js', () => ({
  EmergencyKillSwitch: { isKillSwitchActive: jest.fn().mockReturnValue(false) },
}));

// ─── Imports (after mocks) ────────────────────────────────────────────────────

import { ExecutionEngineService } from '../src/modules/execution-engine/services/ExecutionEngineService.js';

// ─── Helpers ──────────────────────────────────────────────────────────────────

// A LIVE safety result that is fully approved
const SAFETY_PASS = {
  isAllowed: true,
  checks: {
    explicitUserConfirmed: true,
    validEnvironment: true,
    productionApiKeysPresent: true,
    allowLiveTradingEnvSet: true,
    killSwitchInactive: true,
    challengeGuardEnabled: true,
    liveModeActive: true,
    deltaConnectionHealthy: true,
    tradingViewConnectionHealthy: true,
  },
  rejectionReasons: [],
  timestamp: new Date().toISOString(),
};

// A LIVE safety result that is blocked
const SAFETY_FAIL = {
  isAllowed: false,
  checks: { ...SAFETY_PASS.checks, explicitUserConfirmed: false, liveModeActive: false },
  rejectionReasons: [
    'Explicit user confirmation is missing for Live Trading.',
    'Live Mode has not been activated by user.',
  ],
  timestamp: new Date().toISOString(),
};

// A valid product mock
const MOCK_PRODUCT = { id: 84, symbol: 'BTCUSD.P', contract_value: '0.001' };

// Standard normal-entry request
const NORMAL_ORDER = {
  symbol: 'BTCUSD.P',
  side: 'buy' as const,
  orderType: 'market' as const,
  size: 0.001,
  leverage: 10,
  clientOrderId: 'TEST-001',
};

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('Phase C.6.2.1 — ExecutionEngineService LIVE Guard', () => {
  let engine: ExecutionEngineService;

  beforeEach(() => {
    jest.clearAllMocks();
    engine = new ExecutionEngineService();

    // Default: product exists, placeOrder returns success
    mockGetProduct.mockReturnValue(MOCK_PRODUCT);
    mockPlaceOrder.mockResolvedValue({ result: { id: 999, state: 'open' } });
    // Default: no open positions
    mockGetPositions.mockReturnValue([]);
    // Default: LIVE guard passes
    mockEvaluateSafety.mockResolvedValue(SAFETY_PASS);
    // Default env
    process.env.ALLOW_LIVE_TRADING = 'true';
    process.env.DELTA_API_KEY = 'test-key';
    process.env.DELTA_API_SECRET = 'test-secret';
    process.env.NODE_ENV = 'development';
  });

  afterEach(() => {
    delete process.env.ALLOW_LIVE_TRADING;
  });

  // ── TEST 1: LIVE guard blocks unauthorized normal order ──────────────────

  it('T1: LIVE unauthorized — normal order rejected, placeOrder()=0', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const result = await engine.placeOrder(NORMAL_ORDER);

    expect(result.success).toBe(false);
    expect(result.state).toBe('REJECTED');
    expect(result.message).toMatch(/^LIVE_SAFETY_REJECTED:/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 2: LIVE authorized — order reaches restClient.placeOrder() ──────

  it('T2: LIVE authorized — order reaches mocked restClient.placeOrder()', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_PASS);
    // Provide a balance so margin rule (rule 7) passes
    mockGetBalances.mockReturnValue([
      { asset_symbol: 'USDT', balance: '10000', available_balance: '10000' },
    ]);

    const result = await engine.placeOrder(NORMAL_ORDER);

    // Only assert that placeOrder was invoked on the mock — not on production Delta
    expect(mockPlaceOrder).toHaveBeenCalledTimes(1);
    expect(result.success).toBe(true);
  });

  // ── TEST 3: ALLOW_LIVE_TRADING=false blocks order ────────────────────────

  it('T3: ALLOW_LIVE_TRADING=false — rejected, placeOrder()=0', async () => {
    mockEvaluateSafety.mockResolvedValue({
      ...SAFETY_FAIL,
      checks: { ...SAFETY_FAIL.checks, allowLiveTradingEnvSet: false },
      rejectionReasons: ['ALLOW_LIVE_TRADING environment variable is not set to true.'],
    });

    const result = await engine.placeOrder(NORMAL_ORDER);

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/LIVE_SAFETY_REJECTED/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 4: Kill switch active blocks normal entry ────────────────────────

  it('T4: Kill switch active — normal entry rejected, placeOrder()=0', async () => {
    mockEvaluateSafety.mockResolvedValue({
      ...SAFETY_FAIL,
      checks: { ...SAFETY_FAIL.checks, killSwitchInactive: false },
      rejectionReasons: ['Platform Emergency Kill Switch is ACTIVE.'],
    });

    const result = await engine.placeOrder(NORMAL_ORDER);

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/LIVE_SAFETY_REJECTED/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 5: Emergency close bypasses LIVE guard with position check ───────

  it('T5: Emergency close — reaches mocked placeOrder() without LIVE auth', async () => {
    // LIVE guard is blocked — normal orders would be rejected
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);
    // An existing LONG position exists
    mockGetPositions.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001 },
    ]);

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',          // reversal of 'buy' position
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
      clientOrderId: 'KILL-BTCUSD.P-001',
    });

    // Guard is bypassed, placeOrder should be called
    expect(mockPlaceOrder).toHaveBeenCalledTimes(1);
    const payload = mockPlaceOrder.mock.calls[0][0];
    expect(payload.reduce_only).toBe(true);
    expect(result.success).toBe(true);
  });

  // ── TEST 6: Ordinary reduceOnly without isEmergencyClose still requires guard

  it('T6: Ordinary reduceOnly (no isEmergencyClose) — still requires LIVE auth', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const result = await engine.placeOrder({
      ...NORMAL_ORDER,
      side: 'sell',
      reduceOnly: true,
      // isEmergencyClose NOT set
    });

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/LIVE_SAFETY_REJECTED/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 7: Emergency close without existing position — rejected ──────────

  it('T7: Emergency close + no open position — rejected, placeOrder()=0', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);
    mockGetPositions.mockReturnValue([]); // no positions

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
      clientOrderId: 'KILL-NO-POS',
    });

    expect(result.success).toBe(false);
    expect(result.state).toBe('REJECTED');
    expect(result.message).toMatch(/EMERGENCY_CLOSE_REJECTED/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 8: Emergency close with size > actual position — clamped ─────────

  it('T8: Emergency close with inflated size — clamped to actual position size', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);
    const actualSize = 0.001;
    mockGetPositions.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: actualSize },
    ]);

    await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 99,              // Attacker tries size=99
      reduceOnly: true,
      isEmergencyClose: true,
      clientOrderId: 'KILL-INFLATE',
    });

    // Submitted size must be clamped to actual position size
    const payload = mockPlaceOrder.mock.calls[0]?.[0];
    expect(mockPlaceOrder).toHaveBeenCalledTimes(1);
    expect(Number(payload.size)).toBeLessThanOrEqual(actualSize);
  });

  // ── TEST 9: Emergency close without reduceOnly — rejected ─────────────────

  it('T9: isEmergencyClose=true but reduceOnly=false — rejected', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);
    mockGetPositions.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001 },
    ]);

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: false,     // violation
      isEmergencyClose: true,
      clientOrderId: 'KILL-NO-REDUCE',
    });

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/EMERGENCY_CLOSE_REJECTED.*reduceOnly/);
    expect(mockPlaceOrder).toHaveBeenCalledTimes(0);
  });

  // ── TEST 10: Rejected result is recorded in execution history ─────────────

  it('T10: LIVE_SAFETY_REJECTED result is recorded in history', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    await engine.placeOrder(NORMAL_ORDER);

    const history = engine.getExecutionHistory();
    expect(history.length).toBeGreaterThanOrEqual(1);
    expect(history[0]!.state).toBe('REJECTED');
    expect(history[0]!.message).toMatch(/LIVE_SAFETY_REJECTED/);
  });

  // ── TEST 11: evaluateSafety is NOT called for emergency close ─────────────

  it('T11: Emergency close does not call LiveTradingGuard.evaluateSafety()', async () => {
    mockGetPositions.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001 },
    ]);

    await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
      clientOrderId: 'KILL-NO-AUTH-CHECK',
    });

    expect(mockEvaluateSafety).not.toHaveBeenCalled();
  });
});
