/**
 * Phase C.6.3.1 — F-1, F-2, F-3 Regression Tests
 *
 * T1-T3:  F-1 HTTP isEmergencyClose injection blocked (ExecutionController)
 * T4-T7:  F-2 DeltaSyncService.closePosition() position integrity
 * T8-T10: Kill-switch interaction & order payload integrity
 * T11-T12: F-3 DeltaAdapter hardening
 */

// ─── Module mocks (hoisted) ───────────────────────────────────────────────────

const mockEvaluateSafety = jest.fn();
jest.mock('../src/modules/production/services/liveTradingGuard.js', () => ({
  LiveTradingGuard: {
    evaluateSafety: mockEvaluateSafety,
    setExplicitUserConfirmed: jest.fn(),
    setLiveModeActive: jest.fn(),
  },
}));

const mockEngineRestPlaceOrder = jest.fn();
const mockGetProduct = jest.fn();
const mockGetPositionsSync = jest.fn().mockReturnValue([]);
const mockGetBalances = jest.fn().mockReturnValue([
  { asset_symbol: 'USDT', balance: '10000', available_balance: '10000' },
]);
const mockGetRestClient = jest.fn().mockReturnValue({
  placeOrder: mockEngineRestPlaceOrder,
  getProduct: mockGetProduct,
  isConfigured: jest.fn().mockReturnValue(true),
});

jest.mock('../src/modules/delta-exchange/index.js', () => ({
  deltaSyncService: {
    getPositions: mockGetPositionsSync,
    getBalances: mockGetBalances,
    getRestClient: mockGetRestClient,
    onPriceTick: jest.fn(),
  },
}));

jest.mock('../src/db.js', () => ({
  prisma: {
    systemSettings: { findFirst: jest.fn().mockResolvedValue(null) },
    tradeLedger: { create: jest.fn().mockResolvedValue({}) },
    orderRecord: { create: jest.fn().mockResolvedValue({}) },
  },
}));

jest.mock('../src/modules/execution-engine/services/OrderLifecycleService.js', () => ({
  orderLifecycleService: {
    createOrderRecord: jest.fn(),
    getOrder: jest.fn().mockReturnValue(null),
    transition: jest.fn(),
  },
}));

jest.mock('../src/modules/trade-accounting/TradeAccountingTrigger.js', () => ({
  tradeAccountingTrigger: { recordExecution: jest.fn(), initialize: jest.fn() },
}));

jest.mock('../src/engine/CandleEngine.js', () => ({
  candleEngine: { getLiveCandle: jest.fn().mockReturnValue({ close: 95000 }) },
}));

jest.mock('../src/services/EventBus.js', () => ({
  eventBus: { emit: jest.fn(), on: jest.fn() },
}));

jest.mock('../src/modules/execution/adapters/delta/emergencyKillSwitch.js', () => ({
  EmergencyKillSwitch: { isKillSwitchActive: jest.fn().mockReturnValue(false) },
}));

// ─── Imports (static) ─────────────────────────────────────────────────────────

import { ExecutionEngineService } from '../src/modules/execution-engine/services/ExecutionEngineService.js';
import { ExecutionController } from '../src/modules/execution-engine/execution.controller.js';
import { EmergencyKillSwitch } from '../src/modules/execution/adapters/delta/emergencyKillSwitch.js';
import { DeltaSyncService } from '../src/modules/delta-exchange/services/DeltaSyncService.js';
import { DeltaAdapter } from '../src/modules/execution/adapters/deltaAdapter.js';

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

const SAFETY_FAIL = {
  isAllowed: false,
  checks: { ...SAFETY_PASS.checks, explicitUserConfirmed: false, liveModeActive: false },
  rejectionReasons: ['Explicit user confirmation is missing.', 'Live Mode not activated.'],
  timestamp: new Date().toISOString(),
};

const MOCK_PRODUCT = { id: 84, symbol: 'BTCUSD.P', contract_value: '0.001' };

function makeMockReq(body: Record<string, any>) {
  return { body } as any;
}

function makeMockRes() {
  const json = jest.fn();
  const status = jest.fn().mockReturnValue({ json });
  return { json, status, statusCode: 200 } as any;
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('Phase C.6.3.1 — F-1/F-2/F-3 Regression', () => {
  let engine: ExecutionEngineService;
  let controller: ExecutionController;

  beforeEach(() => {
    jest.clearAllMocks();
    engine = new ExecutionEngineService();
    controller = new ExecutionController(engine);
    mockGetProduct.mockReturnValue(MOCK_PRODUCT);
    mockEngineRestPlaceOrder.mockResolvedValue({ result: { id: 999, state: 'open' } });
    mockGetPositionsSync.mockReturnValue([]);
    mockEvaluateSafety.mockResolvedValue(SAFETY_PASS);
    (EmergencyKillSwitch.isKillSwitchActive as jest.Mock).mockReturnValue(false);
    process.env.ALLOW_LIVE_TRADING = 'true';
    process.env.DELTA_API_KEY = 'test-key';
    process.env.DELTA_API_SECRET = 'test-secret';
  });

  afterEach(() => {
    delete process.env.ALLOW_LIVE_TRADING;
  });

  // ── F-1: HTTP isEmergencyClose injection ────────────────────────────────────

  it('T1: F-1 — HTTP body with isEmergencyClose=true is stripped; LIVE guard still runs', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const req = makeMockReq({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,  // attacker injection
    });
    const res = makeMockRes();
    await controller.placeOrder(req, res);

    expect(mockEngineRestPlaceOrder).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(400);
    const responseBody = res.status.mock.results[0].value.json.mock.calls[0][0];
    expect(responseBody.success).toBe(false);
    expect(responseBody.data.message).toMatch(/LIVE_SAFETY_REJECTED/);
  });

  it('T2: F-1 — Normal authorized HTTP order proceeds (field whitelist does not break flow)', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_PASS);
    mockGetBalances.mockReturnValue([
      { asset_symbol: 'USDT', balance: '10000', available_balance: '10000' },
    ]);

    const req = makeMockReq({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.001,
      leverage: 10,
    });
    const res = makeMockRes();
    await controller.placeOrder(req, res);

    expect(mockEngineRestPlaceOrder).toHaveBeenCalledTimes(1);
    expect(res.json).toHaveBeenCalledWith(expect.objectContaining({ success: true }));
  });

  it('T3: F-1 — isEmergencyClose field is never forwarded to placeOrder from controller', async () => {
    const spy = jest.spyOn(engine, 'placeOrder');
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const req = makeMockReq({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      isEmergencyClose: true, // inject
    });
    const res = makeMockRes();
    await controller.placeOrder(req, res);

    expect(spy).toHaveBeenCalledWith(
      expect.not.objectContaining({ isEmergencyClose: true })
    );
  });

  // ── F-2: DeltaSyncService.closePosition() position integrity ────────────────

  it('T4: F-2 — Close with no matching position returns error', async () => {
    const syncService = new DeltaSyncService({ apiKey: '', apiSecret: '' });
    jest.spyOn(syncService, 'getPositions').mockReturnValue([]);

    const result = await syncService.closePosition('BTCUSD.P');

    expect(result.success).toBe(false);
    expect(result.error).toMatch(/No open position/);
    expect(mockEngineRestPlaceOrder).not.toHaveBeenCalled();
  });

  it('T5: F-2 — Close rejects when ALLOW_LIVE_TRADING is not set', async () => {
    delete process.env.ALLOW_LIVE_TRADING;
    const syncService = new DeltaSyncService({ apiKey: '', apiSecret: '' });
    jest.spyOn(syncService, 'getPositions').mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001, product_id: 84, entry_price: '95000', margin: '10', liquidation_price: '0', bankruptcy_price: '0', unrealized_pnl: '0', realized_pnl: '0' },
    ]);
    const mockRest = (syncService as any).rest;
    jest.spyOn(mockRest, 'toInternalSymbol').mockReturnValue('BTCUSD.P');
    jest.spyOn(mockRest, 'getProduct').mockReturnValue(MOCK_PRODUCT);

    const result = await syncService.closePosition('BTCUSD.P');
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/ALLOW_LIVE_TRADING/);
  });

  it('T6: F-2 — Close proceeds when kill switch is active (protective close allowed)', async () => {
    (EmergencyKillSwitch.isKillSwitchActive as jest.Mock).mockReturnValue(true);
    process.env.ALLOW_LIVE_TRADING = 'true';

    mockGetPositionsSync.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001, product_id: 84 },
    ]);
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
    });

    expect(mockEngineRestPlaceOrder).toHaveBeenCalledTimes(1);
    expect(result.success).toBe(true);
  });

  it('T7: F-2 — Normal entry is blocked when kill switch is active', async () => {
    (EmergencyKillSwitch.isKillSwitchActive as jest.Mock).mockReturnValue(true);
    mockEvaluateSafety.mockResolvedValue({
      ...SAFETY_FAIL,
      rejectionReasons: ['Platform Emergency Kill Switch is ACTIVE.'],
    });

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.001,
    });

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/LIVE_SAFETY_REJECTED/);
    expect(mockEngineRestPlaceOrder).not.toHaveBeenCalled();
  });

  it('T8: F-2 — SL/TP close cannot create new position (no existing position = rejected)', async () => {
    mockGetPositionsSync.mockReturnValue([]);

    const result = await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
    });

    expect(result.success).toBe(false);
    expect(result.message).toMatch(/EMERGENCY_CLOSE_REJECTED/);
    expect(mockEngineRestPlaceOrder).not.toHaveBeenCalled();
  });

  it('T9: F-2 — Emergency close size is clamped; cannot exceed actual position', async () => {
    mockGetPositionsSync.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001, product_id: 84 },
    ]);
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 99.999,
      reduceOnly: true,
      isEmergencyClose: true,
    });

    const payload = mockEngineRestPlaceOrder.mock.calls[0]?.[0];
    expect(Number(payload.size)).toBeLessThanOrEqual(0.001);
  });

  it('T10: F-2 — Emergency close always sets reduce_only=true in submitted payload', async () => {
    mockGetPositionsSync.mockReturnValue([
      { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.001, product_id: 84 },
    ]);
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    await engine.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'sell',
      orderType: 'market',
      size: 0.001,
      reduceOnly: true,
      isEmergencyClose: true,
    });

    const payload = mockEngineRestPlaceOrder.mock.calls[0]?.[0];
    expect(payload.reduce_only).toBe(true);
  });

  // ── F-3: DeltaAdapter hardening ─────────────────────────────────────────────

  it('T11: F-3 — DeltaAdapter.submit() blocked when LIVE guard fails', async () => {
    mockEvaluateSafety.mockResolvedValue(SAFETY_FAIL);

    const adapterRestPlaceOrder = jest.fn().mockResolvedValue({ id: 999, state: 'open' });
    const mockAdapterRestClient = {
      getProduct: jest.fn().mockReturnValue(MOCK_PRODUCT),
      placeOrder: adapterRestPlaceOrder,
      getWalletBalances: jest.fn().mockResolvedValue([]),
      getPositions: jest.fn().mockResolvedValue([]),
    } as any;

    const adapter = new DeltaAdapter(mockAdapterRestClient);
    const result = await adapter.submit({
      id: 'test-req',
      sessionId: 'sess',
      idempotencyKey: 'key-1',
      symbol: 'BTCUSD.P',
      side: 'LONG',
      orderType: 'MARKET',
      quantity: 0.001,
    } as any);

    expect(adapterRestPlaceOrder).not.toHaveBeenCalled();
    expect(result.status).toBe('REJECTED');
    expect(result.message).toMatch(/LIVE_SAFETY_REJECTED/);
  });

  it('T12: F-3 — DeltaAdapter.closePosition() rejected when no open position', async () => {
    const adapterRestPlaceOrder = jest.fn();
    const mockAdapterRestClient = {
      getProduct: jest.fn().mockReturnValue(MOCK_PRODUCT),
      placeOrder: adapterRestPlaceOrder,
      getPositions: jest.fn().mockResolvedValue([]),
    } as any;

    const adapter = new DeltaAdapter(mockAdapterRestClient);
    const result = await adapter.closePosition('BTCUSD.P', 95000);

    expect(adapterRestPlaceOrder).not.toHaveBeenCalled();
    expect(result.status).toBe('REJECTED');
    expect(result.message).toMatch(/No open position/);
  });
});
