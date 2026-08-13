import crypto from 'crypto';
import axios, { AxiosInstance, InternalAxiosRequestConfig } from 'axios';

export interface DeltaProduct {
  id: number;
  symbol: string;
  underlying_asset?: { symbol: string } | undefined;
  quoting_asset?: { symbol: string } | undefined;
  tick_size?: string | undefined;
  contract_value?: string | undefined;
  initial_margin?: string | undefined;
  maintenance_margin?: string | undefined;
}

export interface DeltaPlaceOrderRequest {
  product_id: number;
  product_symbol: string;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit' | 'stop_market' | 'stop_limit';
  size: number;
  price?: number | undefined;
  stop_price?: number | undefined;
  stop_loss?: number | undefined;
  take_profit?: number | undefined;
  client_order_id?: string | undefined;
  time_in_force?: 'gtc' | 'ioc' | 'fok' | undefined;
  reduce_only?: boolean | undefined;
  post_only?: boolean | undefined;
}

export interface DeltaWalletBalance {
  asset_id: number;
  asset_symbol: string;
  balance: string;
  available_balance: string;
  order_margin: string;
  position_margin: string;
  unrealized_pnl: string;
}

export interface DeltaPosition {
  product_id: number;
  product_symbol: string;
  size: number;
  entry_price: string;
  margin: string;
  liquidation_price: string;
  bankruptcy_price: string;
  unrealized_pnl: string;
  realized_pnl: string;
  side: 'buy' | 'sell';
}

export interface DeltaOrder {
  id: number;
  product_id: number;
  product_symbol: string;
  side: 'buy' | 'sell';
  order_type: string;
  size: number;
  unfilled_size: number;
  price: string;
  stop_price?: string | undefined;
  state: 'open' | 'pending' | 'closed' | 'cancelled' | 'rejected';
  created_at: string;
  updated_at: string;
}

export interface DeltaCandle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface SymbolMapping {
  internal: string;
  exchange: string;
}

export const PRODUCT_METADATA_TTL_MS = 60 * 60 * 1000; // 1 hour TTL

export class DeltaRestClient {
  private client: AxiosInstance;
  private productsCache = new Map<string, DeltaProduct>();
  private lastProductsRefreshTimestamp = 0;
  private readonly baseUrl = 'https://api.india.delta.exchange';

  private readonly symbolMappings: SymbolMapping[] = [
    { internal: 'BTCUSD.P', exchange: 'BTCUSD' },
    { internal: 'ETHUSD.P', exchange: 'ETHUSD' },
    { internal: 'SOLUSD.P', exchange: 'SOLUSD' },
    { internal: 'XRPUSD.P', exchange: 'XRPUSD' },
  ];

  constructor(
    private credentials: { apiKey: string; apiSecret: string }
  ) {
    this.client = axios.create({
      baseURL: this.baseUrl,
      timeout: 10000,
    });

    this.client.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
      await this.waitForRateLimitToken();
      return this.signRequest(config);
    });
  }

  public isConfigured(): boolean {
    return !!(this.credentials.apiKey && this.credentials.apiSecret);
  }

  public isProductsCacheFresh(): boolean {
    if (this.productsCache.size === 0) return false;
    if (this.lastProductsRefreshTimestamp === 0) return false;
    const age = Date.now() - this.lastProductsRefreshTimestamp;
    return age >= 0 && age < PRODUCT_METADATA_TTL_MS;
  }

  public getLastProductsRefreshTimestamp(): number {
    return this.lastProductsRefreshTimestamp;
  }

  private async waitForRateLimitToken(): Promise<void> {
    const now = Date.now();
    const elapsed = (now - this.lastTokenRefill) / 1000;
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillRatePerSec);
    this.lastTokenRefill = now;

    if (this.tokens < 1) {
      const waitTime = ((1 - this.tokens) / this.refillRatePerSec) * 1000;
      await new Promise((resolve) => setTimeout(resolve, waitTime));
      this.tokens = 0;
    } else {
      this.tokens -= 1;
    }
  }

  private tokens: number = 10;
  private lastTokenRefill: number = Date.now();
  private readonly maxTokens: number = 10;
  private readonly refillRatePerSec: number = 10;

  private signRequest(reqConfig: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
    if (!this.credentials.apiKey || !this.credentials.apiSecret) {
      return reqConfig;
    }

    const timestamp = Math.floor(Date.now() / 1000).toString();
    const method = reqConfig.method?.toUpperCase() || 'GET';
    const path = reqConfig.url || '';
    const query = reqConfig.params
      ? '?' + new URLSearchParams(reqConfig.params).toString()
      : '';
    const body = reqConfig.data ? JSON.stringify(reqConfig.data) : '';

    const payload = method + timestamp + path + query + body;
    const signature = crypto
      .createHmac('sha256', this.credentials.apiSecret)
      .update(payload)
      .digest('hex');

    reqConfig.headers.set('api-key', this.credentials.apiKey);
    reqConfig.headers.set('signature', signature);
    reqConfig.headers.set('timestamp', timestamp);
    reqConfig.headers.set('Content-Type', 'application/json');
    reqConfig.headers.set('User-Agent', 'AlgoApp-Enterprise-v2');

    return reqConfig;
  }

  private async executeWithRetry<T>(fn: () => Promise<T>, maxRetries: number = 3): Promise<T> {
    let attempt = 0;
    while (attempt < maxRetries) {
      try {
        return await fn();
      } catch (err: any) {
        attempt++;
        if (attempt >= maxRetries || (err.response && err.response.status >= 400 && err.response.status < 500 && err.response.status !== 429)) {
          throw err;
        }
        const backoff = Math.pow(2, attempt) * 200 + Math.random() * 100;
        await new Promise((r) => setTimeout(r, backoff));
      }
    }
    throw new Error('Max retries exceeded');
  }

  public async loadProducts(): Promise<void> {
    try {
      const res = await this.executeWithRetry(() => this.client.get('/v2/products'));
      if (!res.data || !Array.isArray(res.data.result) || res.data.result.length === 0) {
        throw new Error('Invalid or empty product metadata response from Delta Exchange');
      }

      // Build a new temporary Map first (Atomic Build)
      const newProductsCache = new Map<string, DeltaProduct>();
      for (const p of res.data.result) {
        if (p && p.symbol) {
          newProductsCache.set(p.symbol, p);
        }
      }

      if (newProductsCache.size === 0) {
        throw new Error('No valid products parsed from Delta Exchange response');
      }

      // Atomic Swap ONLY after complete validation
      this.productsCache = newProductsCache;
      this.lastProductsRefreshTimestamp = Date.now();
    } catch (err) {
      console.warn('[DeltaRestClient] Load products notice:', err instanceof Error ? err.message : err);
      if (!this.isProductsCacheFresh()) {
        throw err;
      }
    }
  }

  public getProduct(symbol: string): DeltaProduct | undefined {
    if (!this.isProductsCacheFresh()) {
      return undefined;
    }
    const exchangeSymbol = this.toExchangeSymbol(symbol);
    return this.productsCache.get(exchangeSymbol);
  }

  public setProduct(product: DeltaProduct, timestamp?: number): void {
    if (product && product.symbol) {
      this.productsCache.set(product.symbol, product);
      this.lastProductsRefreshTimestamp = timestamp ?? Date.now();
    }
  }

  public getAllSupportedPairs(): string[] {
    const list = Array.from(this.productsCache.keys());
    if (list.length > 0) {
      const targetSymbols = ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD'];
      return list.filter((s) => targetSymbols.includes(s));
    }
    return ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD'];
  }

  public toExchangeSymbol(internalSymbol: string): string {
    const mapping = this.symbolMappings.find(m => m.internal === internalSymbol);
    return mapping?.exchange ?? internalSymbol;
  }

  public toInternalSymbol(exchangeSymbol: string): string {
    const mapping = this.symbolMappings.find(m => m.exchange === exchangeSymbol);
    return mapping?.internal ?? exchangeSymbol;
  }

  public getSymbolMappings(): SymbolMapping[] {
    return [...this.symbolMappings];
  }

  public async getWalletBalances(): Promise<DeltaWalletBalance[]> {
    const res = await this.executeWithRetry(() => this.client.get('/v2/wallet/balances'));
    const raw = res.data;
    if (Array.isArray(raw?.result)) {
      return raw.result;
    }
    if (raw?.result && typeof raw.result === 'object') {
      return Object.entries(raw.result).map(([assetSymbol, data]: [string, any]) => ({
        asset_id: data.asset_id || 0,
        asset_symbol: assetSymbol,
        balance: String(data.balance || data.wallet_balance || '0'),
        available_balance: String(data.available_balance || data.available_margin || '0'),
        order_margin: String(data.order_margin || '0'),
        position_margin: String(data.position_margin || '0'),
        unrealized_pnl: String(data.unrealized_pnl || '0'),
      }));
    }
    return [];
  }

  public async getPositions(): Promise<DeltaPosition[]> {
    const res = await this.executeWithRetry(() => this.client.get('/v2/positions/margined'));
    const raw = res.data;
    if (Array.isArray(raw?.result)) return raw.result;
    if (raw?.result && typeof raw.result === 'object') {
      return Object.values(raw.result) as DeltaPosition[];
    }
    return [];
  }

  public async getOrders(params?: { status?: string | undefined }): Promise<DeltaOrder[]> {
    const res = await this.executeWithRetry(() => this.client.get('/v2/orders', { params }));
    return res.data?.result || [];
  }

  public async getHistory(params?: { limit?: number | undefined }): Promise<any[]> {
    const res = await this.executeWithRetry(() => this.client.get('/v2/orders/history', { params }));
    return res.data?.result || [];
  }

  public async placeOrder(order: DeltaPlaceOrderRequest): Promise<any> {
    const res = await this.executeWithRetry(() => this.client.post('/v2/orders', order), 2);
    return res.data?.result;
  }

  public async cancelOrder(orderId: number, productId?: number | undefined): Promise<any> {
    const res = await this.executeWithRetry(() =>
      this.client.delete('/v2/orders', {
        data: { id: orderId, product_id: productId },
      }),
      2
    );
    return res.data?.result;
  }

  public async getTicker(symbol: string): Promise<any> {
    const exchangeSymbol = this.toExchangeSymbol(symbol);
    const res = await this.executeWithRetry(() => this.client.get(`/v2/tickers/${exchangeSymbol}`));
    return res.data?.result;
  }

  public async getHistoricalCandles(
    symbol: string,
    resolution: '15' | '60' | '240' | 'D' = '60',
    from: number,
    to: number
  ): Promise<DeltaCandle[]> {
    const exchangeSymbol = this.toExchangeSymbol(symbol);
    const res = await this.executeWithRetry(() =>
      this.client.get('/v2/chart/history', {
        params: {
          resolution,
          symbol: exchangeSymbol,
          from,
          to,
        },
      })
    );

    const data = res.data;
    if (!data?.success || !data.result || !Array.isArray(data.result.c)) {
      return [];
    }

    const candles: DeltaCandle[] = [];
    for (let i = 0; i < data.result.c.length; i++) {
      candles.push({
        t: data.result.t[i],
        o: data.result.o[i],
        h: data.result.h[i],
        l: data.result.l[i],
        c: data.result.c[i],
        v: data.result.v[i],
      });
    }

    return candles;
  }
}