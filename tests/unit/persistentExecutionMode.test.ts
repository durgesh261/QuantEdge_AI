import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  setExecutionMode,
  initializeExecutionModeFromPersistence,
  getActiveExecutionMode,
} from '../../backend/src/modules/production/production.controller.js';
import { LiveTradingGuard } from '../../backend/src/modules/production/services/liveTradingGuard.js';
import { EmergencyKillSwitch } from '../../backend/src/modules/execution/adapters/delta/emergencyKillSwitch.js';
import { ProductionModeStore } from '../../backend/src/modules/production/services/productionModeStore.js';
import { DeltaExecutionService } from '../../backend/src/modules/delta-exchange/services/DeltaExecutionService.js';
import { ExecutionEngineService } from '../../backend/src/modules/execution-engine/services/ExecutionEngineService.js';
import { DeltaRestClient } from '../../backend/src/modules/delta-exchange/services/DeltaRestClient.js';
import { deltaSyncService } from '../../backend/src/modules/delta-exchange/index.js';
import { candleEngine } from '../../backend/src/engine/CandleEngine.js';
import { ExecutionMode } from '@algoapp/shared';

describe('Phase C.5.1: Persistent Execution Mode & Restart Lifecycle Tests', () => {
  const originalEnv = { ...process.env };

  const createMockReqRes = (body: any) => {
    const req = { body, correlationId: 'test-req' } as any;
    let responseData: any = null;
    let statusCode: number = 200;

    const res = {
      status: (code: number) => {
        statusCode = code;
        return res;
      },
      json: (data: any) => {
        responseData = data;
        return res;
      },
    } as any;

    return { req, res, getResponse: () => ({ statusCode, responseData }) };
  };

  beforeEach(() => {
    process.env.NODE_ENV = 'development';
    process.env.DELTA_API_KEY = 'mock_key';
    process.env.DELTA_API_SECRET = 'mock_secret';
    process.env.ALLOW_LIVE_TRADING = 'true';

    LiveTradingGuard.setLiveModeActive(false);
    LiveTradingGuard.setExplicitUserConfirmed(false);
    EmergencyKillSwitch.setKillSwitch(false);

    const mockProduct = {
      id: 27,
      symbol: 'BTCUSD',
      contract_value: '0.001',
      tick_size: '0.5',
      contract_type: 'perpetual_futures',
    } as any;

    vi.spyOn(DeltaRestClient.prototype, 'getProduct').mockImplementation((symbol: string) => {
      if (symbol === 'BTCUSD' || symbol === 'BTCUSD.P') return mockProduct;
      return undefined;
    });

    vi.spyOn(deltaSyncService.getRestClient(), 'getProduct').mockImplementation((symbol: string) => {
      if (symbol === 'BTCUSD' || symbol === 'BTCUSD.P') return mockProduct;
      return undefined;
    });

    vi.spyOn(DeltaRestClient.prototype, 'isProductsCacheFresh').mockReturnValue(true);
    vi.spyOn(deltaSyncService.getRestClient(), 'isProductsCacheFresh').mockReturnValue(true);
    vi.spyOn(deltaSyncService.getRestClient(), 'isConfigured').mockReturnValue(true);
    vi.spyOn(deltaSyncService, 'getBalances').mockReturnValue([
      { asset_symbol: 'USDT', balance: '100000', available_balance: '100000' } as any,
    ]);
    vi.spyOn(deltaSyncService, 'getPositions').mockReturnValue([]);

    vi.spyOn(candleEngine, 'getLiveCandle').mockReturnValue({
      close: 65000,
      open: 64900,
      high: 65100,
      low: 64800,
      volume: 100,
      timestamp: Date.now(),
    } as any);
  });

  afterEach(() => {
    process.env = { ...originalEnv };
    vi.restoreAllMocks();
  });

  describe('Step 10: Server Restart & Mode Restoration Scenarios', () => {
    it('Test 1: defaults to PAPER if no saved execution mode exists', async () => {
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.PAPER);

      const restoredMode = await initializeExecutionModeFromPersistence();
      expect(restoredMode).toBe(ExecutionMode.PAPER);
      expect(getActiveExecutionMode()).toBe(ExecutionMode.PAPER);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.checks.liveModeActive).toBe(false);
    });

    it('Test 2: restores PAPER when persisted mode is PAPER', async () => {
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.PAPER);

      const restoredMode = await initializeExecutionModeFromPersistence();
      expect(restoredMode).toBe(ExecutionMode.PAPER);
      expect(getActiveExecutionMode()).toBe(ExecutionMode.PAPER);
    });

    it('Test 3: restores LIVE preference and permits LIVE when all guards pass', async () => {
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);

      const restoredMode = await initializeExecutionModeFromPersistence();
      expect(restoredMode).toBe(ExecutionMode.LIVE);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(true);
    });

    it('Test 4: blocks LIVE order execution on restart if ALLOW_LIVE_TRADING !== "true"', async () => {
      delete process.env.ALLOW_LIVE_TRADING;
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);

      await initializeExecutionModeFromPersistence();

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.rejectionReasons).toContain('ALLOW_LIVE_TRADING environment variable is not set to true.');
    });

    it('Test 5: blocks LIVE execution on restart if production API credentials are missing', async () => {
      delete process.env.DELTA_API_KEY;
      delete process.env.DELTA_API_SECRET;
      process.env.NODE_ENV = 'production';
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);

      await initializeExecutionModeFromPersistence();

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.checks.productionApiKeysPresent).toBe(false);
    });

    it('Test 6: blocks LIVE execution on restart if Emergency Kill Switch is active', async () => {
      EmergencyKillSwitch.setKillSwitch(true);
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);

      await initializeExecutionModeFromPersistence();

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.rejectionReasons).toContain('Platform Emergency Kill Switch is ACTIVE.');
    });

    it('Test 7: blocks LIVE order on restart if product metadata is stale or missing', async () => {
      vi.spyOn(DeltaRestClient.prototype, 'getProduct').mockReturnValue(undefined);
      vi.spyOn(deltaSyncService.getRestClient(), 'getProduct').mockReturnValue(undefined);
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);

      await initializeExecutionModeFromPersistence();

      const normalized = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.0357);
      expect(normalized.isValid).toBe(false);
      expect(normalized.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('Test 8: switching LIVE -> PAPER persists PAPER and blocks LIVE execution', async () => {
      const persistSpy = vi.spyOn(ProductionModeStore, 'persistExecutionMode').mockResolvedValue();

      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.PAPER });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(200);
      expect(responseData.data.activeExecutionMode).toBe(ExecutionMode.PAPER);
      expect(persistSpy).toHaveBeenCalledWith(ExecutionMode.PAPER);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
    });

    it('Test 9: switching PAPER -> LIVE with boolean userConfirmed=true rejects and does NOT persist LIVE', async () => {
      const persistSpy = vi.spyOn(ProductionModeStore, 'persistExecutionMode').mockResolvedValue();

      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: true });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
      expect(persistSpy).not.toHaveBeenCalled();
    });

    it('Test 10: switching PAPER -> LIVE with "CONFIRM_LIVE_TRADING" persists LIVE when all guards pass', async () => {
      const persistSpy = vi.spyOn(ProductionModeStore, 'persistExecutionMode').mockResolvedValue();

      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: 'CONFIRM_LIVE_TRADING' });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(200);
      expect(responseData.success).toBe(true);
      expect(responseData.data.activeExecutionMode).toBe(ExecutionMode.LIVE);
      expect(persistSpy).toHaveBeenCalledWith(ExecutionMode.LIVE);
    });

    it('Test 11 & 12: backend state remains authoritative and unchanged across browser connects', async () => {
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.PAPER);

      await initializeExecutionModeFromPersistence();
      expect(getActiveExecutionMode()).toBe(ExecutionMode.PAPER);

      const modeClientA = getActiveExecutionMode();
      const modeClientB = getActiveExecutionMode();

      expect(modeClientA).toBe(ExecutionMode.PAPER);
      expect(modeClientB).toBe(ExecutionMode.PAPER);
    });
  });

  describe('Step 11: Critical Order-Safety Mock Execution Tests', () => {
    it('permits canonical execution path when savedExecutionMode = LIVE and all guards pass', async () => {
      const mockOrderResponse = {
        id: 99999,
        product_id: 27,
        size: 35,
        side: 'buy',
        order_type: 'limit_order',
        price: '64875',
        state: 'open',
      } as any;

      vi.spyOn(DeltaRestClient.prototype, 'placeOrder').mockResolvedValue(mockOrderResponse);
      vi.spyOn(deltaSyncService.getRestClient(), 'placeOrder').mockResolvedValue(mockOrderResponse);

      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);
      await initializeExecutionModeFromPersistence();

      const restClient = deltaSyncService.getRestClient();
      const service = new DeltaExecutionService(restClient);

      const result = await service.placeOrder({
        symbol: 'BTCUSD.P',
        side: 'buy',
        orderType: 'limit',
        size: 0.0357,
        price: 64875,
      });

      expect(result.success).toBe(true);
      expect(result.orderId).toBe(99999);
    });

    it('prevents placeOrder() call when savedExecutionMode = LIVE but a safety guard fails', async () => {
      const mockPlaceOrder = vi.spyOn(DeltaRestClient.prototype, 'placeOrder').mockResolvedValue({} as any);

      EmergencyKillSwitch.setKillSwitch(true); // Fail guard
      vi.spyOn(ProductionModeStore, 'getPersistedExecutionMode').mockResolvedValue(ExecutionMode.LIVE);
      await initializeExecutionModeFromPersistence();

      const restClient = deltaSyncService.getRestClient();
      const service = new DeltaExecutionService(restClient);

      const result = await service.placeOrder({
        symbol: 'BTCUSD.P',
        side: 'buy',
        orderType: 'limit',
        size: 0.0357,
        price: 64875,
      });

      expect(result.success).toBe(false);
      expect(result.error).toContain('LIVE_SAFETY_REJECTED');
      expect(mockPlaceOrder).not.toHaveBeenCalled();
    });
  });
});
