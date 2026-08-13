import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DeltaExecutionService } from '../../backend/src/modules/delta-exchange/services/DeltaExecutionService.js';
import { DeltaRestClient, DeltaProduct } from '../../backend/src/modules/delta-exchange/services/DeltaRestClient.js';
import { LiveTradingGuard } from '../../backend/src/modules/production/services/liveTradingGuard.js';
import { EmergencyKillSwitch } from '../../backend/src/modules/execution/adapters/delta/emergencyKillSwitch.js';
import { deltaSyncService } from '../../backend/src/modules/delta-exchange/index.js';

import { candleEngine } from '../../backend/src/engine/CandleEngine.js';

describe('Phase C.1.1: DeltaExecutionService Remediation & Safety Guard Tests', () => {
  const mockProduct = (id: number, symbol: string, contractValue: string = '0.001'): DeltaProduct => ({
    id,
    symbol,
    contract_value: contractValue,
  });

  beforeEach(() => {
    process.env.NODE_ENV = 'development';
    process.env.DELTA_API_KEY = 'mock_key';
    process.env.DELTA_API_SECRET = 'mock_secret';
    LiveTradingGuard.setLiveModeActive(false);
    LiveTradingGuard.setExplicitUserConfirmed(false);
    EmergencyKillSwitch.setKillSwitch(false);
  });

  it('1. rejects when LIVE mode is disabled (LIVE_SAFETY_REJECTED)', async () => {
    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');
    const service = new DeltaExecutionService(restClient);

    const res = await service.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.035,
    });

    expect(res.success).toBe(false);
    expect(res.error).toContain('LIVE_SAFETY_REJECTED');
    expect(placeOrderSpy).not.toHaveBeenCalled();
  });

  it('2. rejects without explicit user confirmation', async () => {
    LiveTradingGuard.setLiveModeActive(true);
    LiveTradingGuard.setExplicitUserConfirmed(false); // Confirmation missing

    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');
    const service = new DeltaExecutionService(restClient);

    const res = await service.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.035,
    });

    expect(res.success).toBe(false);
    expect(res.error).toContain('LIVE_SAFETY_REJECTED');
    expect(res.error).toContain('Explicit user confirmation is missing');
    expect(placeOrderSpy).not.toHaveBeenCalled();
  });

  it('3. rejects when Emergency Kill Switch is active', async () => {
    LiveTradingGuard.setLiveModeActive(true);
    LiveTradingGuard.setExplicitUserConfirmed(true);
    EmergencyKillSwitch.setKillSwitch(true);

    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');
    const service = new DeltaExecutionService(restClient);

    const res = await service.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.035,
    });

    expect(res.success).toBe(false);
    expect(res.error).toContain('LIVE_SAFETY_REJECTED');
    expect(res.error).toContain('Kill Switch is ACTIVE');
    expect(placeOrderSpy).not.toHaveBeenCalled();
  });

  it('4. rejects when pre-flight risk validation fails (e.g. invalid symbol)', async () => {
    vi.spyOn(LiveTradingGuard, 'evaluateSafety').mockResolvedValue({
      isAllowed: true,
      checks: {} as any,
      rejectionReasons: [],
      timestamp: '',
    });

    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');
    const service = new DeltaExecutionService(restClient);

    const res = await service.placeOrder({
      symbol: 'INVALID_COIN.P', // Invalid symbol
      side: 'buy',
      orderType: 'market',
      size: 0.035,
    });

    expect(res.success).toBe(false);
    expect(res.error).toContain('Validation Failed');
    expect(placeOrderSpy).not.toHaveBeenCalled();
  });

  it('5. rejects when contract metadata is missing or stale', async () => {
    vi.spyOn(LiveTradingGuard, 'evaluateSafety').mockResolvedValue({
      isAllowed: true,
      checks: {} as any,
      rejectionReasons: [],
      timestamp: '',
    });
    vi.spyOn(deltaSyncService.getRestClient(), 'isConfigured').mockReturnValue(true);
    vi.spyOn(deltaSyncService, 'getBalances').mockReturnValue([
      { asset_id: 1, asset_symbol: 'USDT', balance: '100000', available_balance: '100000', order_margin: '0', position_margin: '0', unrealized_pnl: '0' },
    ]);

    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    // Product not cached -> missing metadata
    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');
    const service = new DeltaExecutionService(restClient);

    const res = await service.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.035,
    });

    expect(res.success).toBe(false);
    expect(placeOrderSpy).not.toHaveBeenCalled();
  });

  it('6. executes order only when ALL guards, 12 pre-flight rules, and contract metadata pass', async () => {
    vi.spyOn(LiveTradingGuard, 'evaluateSafety').mockResolvedValue({
      isAllowed: true,
      checks: {} as any,
      rejectionReasons: [],
      timestamp: '',
    });

    const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
    const prod = mockProduct(105, 'BTCUSD', '0.001');
    restClient.setProduct(prod);

    // Mock sync service balance, candle price & rest configuration for 12-rule pre-flight check
    vi.spyOn(candleEngine, 'getLiveCandle').mockReturnValue({ close: 65000, open: 65000, high: 65000, low: 65000, volume: 1, timestamp: new Date() } as any);
    vi.spyOn(deltaSyncService.getRestClient(), 'isConfigured').mockReturnValue(true);
    vi.spyOn(deltaSyncService, 'getBalances').mockReturnValue([
      { asset_id: 1, asset_symbol: 'USDT', balance: '100000', available_balance: '100000', order_margin: '0', position_margin: '0', unrealized_pnl: '0' },
    ]);
    vi.spyOn(deltaSyncService, 'getRestClient').mockReturnValue(restClient);

    const placeOrderSpy = vi.spyOn(restClient, 'placeOrder').mockResolvedValue({ id: 9999, size: 0.035 });

    const service = new DeltaExecutionService(restClient);
    const res = await service.placeOrder({
      symbol: 'BTCUSD.P',
      side: 'buy',
      orderType: 'market',
      size: 0.0357, // Floor-rounds to 0.035
      price: 65000,
    });

    expect(res.success).toBe(true);
    expect(res.orderId).toBe(9999);
    expect(placeOrderSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        product_id: 105,
        product_symbol: 'BTCUSD.P',
        side: 'buy',
        size: 0.035,
      })
    );
  });
});
