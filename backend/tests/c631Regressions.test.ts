/**
 * Phase C.6.3.1 — F-1, F-2, F-3 Regression Tests
 *
 * T1-T3:  F-1 HTTP isEmergencyClose injection blocked (ExecutionController)
 * T4-T7:  F-2 DeltaSyncService.closePosition() position integrity
 * T8-T10: Kill-switch interaction & order payload integrity
 * T11-T12: F-3 DeltaAdapter hardening
 */

// ─── Module mocks (hoisted) ───────────────────────────────────────────────────

const mockEvaluateSafety = vi.fn();
vi.mock('../src/modules/production/services/liveTradingGuard.js', () => ({
  LiveTradingGuard: {
    evaluateSafety: mockEvaluateSafety,
    setExplicitUserConfirmed: vi.fn(),
    setLiveModeActive: vi.fn(),
  },
}));

const mockEngineRestPlaceOrder = vi.fn();
const mockGetProduct = vi.fn();
const mockGetPositionsSync = vi.fn().mockReturnValue([]);
const mockGetBalances = vi.fn().mockReturnValue([
  { asset_symbol: 'USDT', balance: '10000', available_balance: '10000' },
]);
const mockGetRestClient = vi.fn().mockReturnValue({
  placeOrder: mockEngineRestPlaceOrder,
  getProduct: mockGetProduct,
  isConfigured: vi.fn().mockReturnValue(true),
});

vi.mock('../src/modules/delta-exchange/index.js', () => ({
  deltaSyncService: {
    getPositions: mockGetPositionsSync,
    getBalances: mockGetBalances,
    getRestClient: mockGetRestClient,
    onPriceTick: vi.fn(),
  },
}));

vi.mock('../src/db.js', () => ({
  prisma: {
    systemSettings: { findFirst: vi.fn().mockResolvedValue(null) },
    tradeLedger: { create: vi.fn().mockResolvedValue({}) },
    orderRecord: { create: vi.fn().mockResolvedValue({}) },
  },
}));

vi.mock('../src/modules/execution-engine/services/OrderLifecycleService.js', () => ({
  orderLifecycleService: {
    createOrderRecord: vi.fn(),
    getOrder: vi.fn().mockReturnValue(null),
    transition: vi.fn(),
  },
}));

vi.mock('../src/modules/trade-accounting/TradeAccountingTrigger.js', () => ({
  tradeAccountingTrigger: { recordExecution: vi.fn(), initialize: vi.fn() },
}));

vi.mock('../src/engine/CandleEngine.js', () => ({
  getLiveCandle: vi.fn(),
}));

vi.mock('../src/modules/execution/adapters/delta/emergencyKillSwitch.js', () => ({
  EmergencyKillSwitch: { isKillSwitchActive: vi.fn().mockReturnValue(false) },
});

vi.mock('../src/modules/tradingview-adapter/services/tradingViewHealthMonitor.js', () => ({
  TradingViewHealthMonitor: { getHealth: vi.fn().mockReturnValue({ status: 'CONNECTED' }) },
));

// ── Imports ───────────────────────────────────────────────────────────────────

import { ExecutionMode } from '@algoapp/shared';
import {
  evaluateSafety,
  setExplicitUserConfirmed,
  setLiveModeActive,
} from '../src/modules/production/services/liveTradingGuard.js';
import { LiveTradingGuard } from '../src/modules/production/services/liveTradingGuard.js';
import { ExecutionMode } from '@algoapp/shared';
import { OrderLifecycleService } from '../src/modules/execution-engine/services/OrderLifecycleService.js';
import { DeltaSyncService } from '../src/modules/delta-exchange/index.js';
import { tradeAccountingTrigger } from '../../trade-accounting/TradeAccountingTrigger.js';
import { candleEngine } from '../../../engine/CandleEngine.js';
import { eventBus } from '../../../services/EventBus.js';

// ── Mocks (hoisted before source imports) ────────────────────────────────────

const mockGetPersistedMode = vi.fn();
const mockPersistMode = vi.fn().mockResolvedValue(undefined);

vi.mock('../src/modules/production/services/productionModeStore.js', () => ({
  ProductionModeStore: {
    getPersistedExecutionMode: vi.fn(),
    persistExecutionMode: vi.fn().mockResolvedValue(undefined),
  },

);

vi.vi.mock('../src/db.js', () => ({
  prisma: {
    systemSettings: {
      findFirst: vi.fn().mockResolvedValue(null),
      upsert: vi.fn().mockResolvedValue({}),
    },
  });

vi.mock('../src/modules/execution/adapters/delta/emergencyKillSwitch.js', () => ({
  EmergencyKillSwitch: { isKillSwitchActive: vi.fn().mockReturnValue(false) },
));

vi.mock('../src/modules/tradingview-adapter/services/tradingViewHealthMonitor.js', () => ({
  TradingViewHealthMonitor: { getHealth: vi.fn().mockResolvedValue({ status: 'CONNECTED' }) },
));

// ── Imports ───────────────────────────────────────────────────────────────────

import { ExecutionMode } from '@algoapp/shared';
import {
  evaluateSafety,
  initializeExecutionModeFromPersistence,
  setExecutionMode,
} from '../src/modules/production/production.controller.js';
import { LiveTradingGuard } from '../src/modules/production/services/liveTradingGuard.js';