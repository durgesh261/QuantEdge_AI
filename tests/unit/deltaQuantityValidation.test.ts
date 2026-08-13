import { describe, it, expect, vi } from 'vitest';
import { ExecutionEngineService } from '../../backend/src/modules/execution-engine/services/ExecutionEngineService.js';
import { DeltaAdapter } from '../../backend/src/modules/execution/adapters/deltaAdapter.js';
import { DeltaRestClient, DeltaProduct } from '../../backend/src/modules/delta-exchange/services/DeltaRestClient.js';

describe('Phase B.2: Fail-Closed Contract Metadata & Quantity Validation Tests', () => {
  const mockProduct = (symbol: string, contractValue?: any): DeltaProduct => ({
    id: 100,
    symbol,
    contract_value: contractValue,
  });

  describe('1. Valid contract_value Floor Rounding', () => {
    it('floor-rounds BTCUSD (contract_value = "0.001") 0.0357 down to 0.035', () => {
      const prod = mockProduct('BTCUSD', '0.001');
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.0357, prod);
      expect(res.isValid).toBe(true);
      expect(res.normalizedQuantity).toBe(0.035);
      expect(res.step).toBe(0.001);
    });

    it('floor-rounds ETHUSD (contract_value = "0.01") 1.258 down to 1.25', () => {
      const prod = mockProduct('ETHUSD', '0.01');
      const res = ExecutionEngineService.normalizeContractQuantity('ETHUSD.P', 1.258, prod);
      expect(res.isValid).toBe(true);
      expect(res.normalizedQuantity).toBe(1.25);
      expect(res.step).toBe(0.01);
    });

    it('floor-rounds SOLUSD (contract_value = "1") 15.99 down to 15.0', () => {
      const prod = mockProduct('SOLUSD', '1');
      const res = ExecutionEngineService.normalizeContractQuantity('SOLUSD.P', 15.99, prod);
      expect(res.isValid).toBe(true);
      expect(res.normalizedQuantity).toBe(15.0);
      expect(res.step).toBe(1);
    });

    it('floor-rounds XRPUSD (contract_value = "1") 250.8 down to 250.0', () => {
      const prod = mockProduct('XRPUSD', '1');
      const res = ExecutionEngineService.normalizeContractQuantity('XRPUSD.P', 250.8, prod);
      expect(res.isValid).toBe(true);
      expect(res.normalizedQuantity).toBe(250.0);
      expect(res.step).toBe(1);
    });
  });

  describe('2. Fail-Closed Safety: Missing or Invalid contract_value Rejections', () => {
    it('rejects order when product is missing from cache', () => {
      const res = ExecutionEngineService.normalizeContractQuantity('UNKNOWN.P', 1.0, undefined);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
      expect(res.reason).toContain('Authoritative product metadata unavailable');
    });

    it('rejects order when contract_value is undefined', () => {
      const prod = mockProduct('BTCUSD', undefined);
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.1, prod);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('rejects order when contract_value is null', () => {
      const prod = mockProduct('BTCUSD', null);
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.1, prod);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('rejects order when contract_value is zero ("0")', () => {
      const prod = mockProduct('BTCUSD', '0');
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.1, prod);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('rejects order when contract_value is negative ("-0.001")', () => {
      const prod = mockProduct('BTCUSD', '-0.001');
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.1, prod);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('rejects order when contract_value is non-numeric ("invalid")', () => {
      const prod = mockProduct('BTCUSD', 'invalid');
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.1, prod);
      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });
  });

  describe('3. LONG and SHORT Both Fail Closed in DeltaAdapter', () => {
    it('fails closed for LONG order when product metadata is corrupted', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct('BTCUSD', null));
      const adapter = new DeltaAdapter(restClient);

      const val = await adapter.validate({
        id: 'REQ-1',
        sessionId: 'SES-1',
        idempotencyKey: 'IDEM-1',
        decisionId: 'DEC-1',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        mode: 'LIVE' as any,
        ruleVersion: 'v2',
        configVersion: 'cfg',
        orderType: 'MARKET',
        quantity: 0.1,
        timestamp: new Date().toISOString(),
      });

      expect(val.valid).toBe(false);
      expect(val.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('fails closed for SHORT order when product metadata is corrupted', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct('BTCUSD', '0'));
      const adapter = new DeltaAdapter(restClient);

      const val = await adapter.validate({
        id: 'REQ-2',
        sessionId: 'SES-2',
        idempotencyKey: 'IDEM-2',
        decisionId: 'DEC-2',
        symbol: 'BTCUSD.P',
        side: 'SHORT',
        mode: 'LIVE' as any,
        ruleVersion: 'v2',
        configVersion: 'cfg',
        orderType: 'MARKET',
        quantity: 0.1,
        timestamp: new Date().toISOString(),
      });

      expect(val.valid).toBe(false);
      expect(val.reason).toContain('MISSING_EXCHANGE_METADATA');
    });
  });

  describe('4. Proof of Zero Order Submission on Invalid Metadata', () => {
    it('never calls restClient.placeOrder when product metadata is invalid', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct('BTCUSD', 'invalid_step'));
      const placeOrderSpy = vi.spyOn(restClient, 'placeOrder');

      const adapter = new DeltaAdapter(restClient);
      const res = await adapter.submit({
        id: 'REQ-SUB-1',
        sessionId: 'SES-1',
        idempotencyKey: 'IDEM-SUB-1',
        decisionId: 'DEC-SUB-1',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        mode: 'LIVE' as any,
        ruleVersion: 'v2',
        configVersion: 'cfg',
        orderType: 'MARKET',
        quantity: 0.1,
        timestamp: new Date().toISOString(),
      });

      expect(res.status).toBe('REJECTED');
      expect(res.message).toContain('MISSING_EXCHANGE_METADATA');
      expect(placeOrderSpy).not.toHaveBeenCalled();
    });
  });
});
