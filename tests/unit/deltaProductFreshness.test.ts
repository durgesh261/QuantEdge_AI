import { describe, it, expect, vi } from 'vitest';
import { DeltaRestClient, DeltaProduct, PRODUCT_METADATA_TTL_MS } from '../../backend/src/modules/delta-exchange/services/DeltaRestClient.js';
import { ExecutionEngineService } from '../../backend/src/modules/execution-engine/services/ExecutionEngineService.js';
import { DeltaAdapter } from '../../backend/src/modules/execution/adapters/deltaAdapter.js';

describe('Phase B.3.2: Product Metadata Freshness, Atomic Refresh & Recovery Tests', () => {
  const mockProduct = (id: number, symbol: string, contractValue: string = '0.001'): DeltaProduct => ({
    id,
    symbol,
    contract_value: contractValue,
  });

  describe('1. Atomic Cache Replacement & Orphan Removal', () => {
    it('populates cache atomically on successful loadProducts()', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      vi.spyOn(restClient['client'], 'get').mockResolvedValue({
        data: {
          result: [mockProduct(1, 'BTCUSD', '0.001'), mockProduct(2, 'ETHUSD', '0.01')],
        },
      });

      await restClient.loadProducts();
      expect(restClient.isProductsCacheFresh()).toBe(true);
      expect(restClient.getProduct('BTCUSD.P')?.id).toBe(1);
      expect(restClient.getProduct('ETHUSD.P')?.id).toBe(2);
    });

    it('removes orphaned symbols atomically when a new authoritative response excludes them', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      const getSpy = vi.spyOn(restClient['client'], 'get');

      // First load has BTCUSD and SOLUSD
      getSpy.mockResolvedValueOnce({
        data: {
          result: [mockProduct(1, 'BTCUSD', '0.001'), mockProduct(3, 'SOLUSD', '1.0')],
        },
      });
      await restClient.loadProducts();
      expect(restClient.getProduct('SOLUSD.P')?.id).toBe(3);

      // Second load has BTCUSD and ETHUSD (SOLUSD removed)
      getSpy.mockResolvedValueOnce({
        data: {
          result: [mockProduct(1, 'BTCUSD', '0.001'), mockProduct(2, 'ETHUSD', '0.01')],
        },
      });
      await restClient.loadProducts();

      expect(restClient.getProduct('BTCUSD.P')?.id).toBe(1);
      expect(restClient.getProduct('ETHUSD.P')?.id).toBe(2);
      expect(restClient.getProduct('SOLUSD.P')).toBeUndefined(); // SOLUSD atomically removed
    });
  });

  describe('2. Fail-Closed Error Handling & Uncorrupted Cache on Failed Refresh', () => {
    it('rejects empty product response and throws error', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      vi.spyOn(restClient['client'], 'get').mockResolvedValue({
        data: { result: [] },
      });

      await expect(restClient.loadProducts()).rejects.toThrow('Invalid or empty product metadata response');
      expect(restClient.isProductsCacheFresh()).toBe(false);
    });

    it('does not corrupt existing fresh cache if a subsequent load fails', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      const getSpy = vi.spyOn(restClient['client'], 'get');

      // Valid load
      getSpy.mockResolvedValueOnce({
        data: { result: [mockProduct(1, 'BTCUSD', '0.001')] },
      });
      await restClient.loadProducts();
      expect(restClient.getProduct('BTCUSD.P')?.id).toBe(1);

      // Subsequent failed load (500 error)
      vi.spyOn(restClient as any, 'executeWithRetry').mockRejectedValueOnce(new Error('HTTP 500 Internal Server Error'));
      await restClient.loadProducts(); // Catches and retains fresh cache

      // Existing fresh cache is still preserved and usable
      expect(restClient.isProductsCacheFresh()).toBe(true);
      expect(restClient.getProduct('BTCUSD.P')?.id).toBe(1);
    });
  });

  describe('3. TTL Freshness & Stale Cache Fail-Closed Behavior', () => {
    it('marks cache as stale after PRODUCT_METADATA_TTL_MS', () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct(1, 'BTCUSD', '0.001'), Date.now() - (PRODUCT_METADATA_TTL_MS + 1000));

      expect(restClient.isProductsCacheFresh()).toBe(false);
      expect(restClient.getProduct('BTCUSD.P')).toBeUndefined();
    });

    it('fails closed in ExecutionEngineService when cache is stale', () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct(1, 'BTCUSD', '0.001'), Date.now() - (PRODUCT_METADATA_TTL_MS + 5000));

      // Product lookup via restClient returns undefined because cache is stale
      const prod = restClient.getProduct('BTCUSD.P');
      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.035, prod);

      expect(res.isValid).toBe(false);
      expect(res.reason).toContain('MISSING_EXCHANGE_METADATA');
    });

    it('fails closed in DeltaAdapter when cache is stale', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      restClient.setProduct(mockProduct(1, 'BTCUSD', '0.001'), Date.now() - (PRODUCT_METADATA_TTL_MS + 5000));
      const adapter = new DeltaAdapter(restClient);

      const val = await adapter.validate({
        id: 'REQ-STALE-1',
        sessionId: 'SES-1',
        idempotencyKey: 'IDEM-STALE-1',
        decisionId: 'DEC-1',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        mode: 'LIVE' as any,
        ruleVersion: 'v2',
        configVersion: 'cfg',
        orderType: 'MARKET',
        quantity: 0.035,
        timestamp: new Date().toISOString(),
      });

      expect(val.valid).toBe(false);
      expect(val.reason).toContain('MISSING_EXCHANGE_METADATA');
    });
  });

  describe('4. Recovery Behavior After Stale/Failed State', () => {
    it('recovers successfully after a new valid loadProducts() call', async () => {
      const restClient = new DeltaRestClient({ apiKey: 'key', apiSecret: 'sec' });
      // Initially uninitialized/stale
      expect(restClient.isProductsCacheFresh()).toBe(false);

      // Now recovery load succeeds
      vi.spyOn(restClient['client'], 'get').mockResolvedValueOnce({
        data: { result: [mockProduct(10, 'BTCUSD', '0.001')] },
      });
      await restClient.loadProducts();

      expect(restClient.isProductsCacheFresh()).toBe(true);
      expect(restClient.getProduct('BTCUSD.P')?.id).toBe(10);

      const res = ExecutionEngineService.normalizeContractQuantity('BTCUSD.P', 0.035, restClient.getProduct('BTCUSD.P'));
      expect(res.isValid).toBe(true);
      expect(res.normalizedQuantity).toBe(0.035);
    });
  });
});
