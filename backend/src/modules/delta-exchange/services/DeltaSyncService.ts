import { DeltaRestClient, DeltaWalletBalance, DeltaPosition, DeltaOrder } from './DeltaRestClient.js';
import { DeltaWebSocketClient } from './DeltaWebSocketClient.js';
import { candleEngine } from '../../../engine/CandleEngine.js';
import { eventBus } from '../../../services/EventBus.js';
import { HistoricalBackfillService } from '../../market-data/services/historicalBackfill.service.js';
import { MarketSnapshotService } from '../../market-data/services/marketSnapshot.service.js';
import { logger } from '../../../logger/index.js';
import { EmergencyKillSwitch } from '../../execution/adapters/delta/emergencyKillSwitch.js';

export interface DeltaHealthStatus {
  status: 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING' | 'ERROR';
  restStatus: 'CONNECTED' | 'DEGRADED' | 'ERROR' | 'UNCONFIGURED';
  wsStatus: 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING';
  lastSyncTime: string;
  reconcileCount: number;
}

export type WsPositionCallback = (position: DeltaPosition) => void;

export class DeltaSyncService {
  private rest: DeltaRestClient;
  private ws: DeltaWebSocketClient;
  private syncTimer: NodeJS.Timeout | null = null;
  private reconcileCount = 0;

  private latestBalances: DeltaWalletBalance[] = [];
  private latestPositions: DeltaPosition[] = [];
  private latestOrders: DeltaOrder[] = [];
  private latestHistory: any[] = [];

  private priceTickCallbacks: ((tick: { symbol: string; price: number; timestamp: number }) => void)[] = [];
  private wsPositionCallbacks: WsPositionCallback[] = [];

  private health: DeltaHealthStatus = {
    status: 'DISCONNECTED',
    restStatus: 'UNCONFIGURED',
    wsStatus: 'DISCONNECTED',
    lastSyncTime: new Date().toISOString(),
    reconcileCount: 0,
  };

  constructor(credentials: { apiKey: string; apiSecret: string }) {
    this.rest = new DeltaRestClient(credentials);
    this.ws = new DeltaWebSocketClient(
      credentials,
      {
        onTicker: (data) => this.handleTicker(data),
        onPosition: (data) => this.handleWsPosition(data),
        onOrder: (data) => this.handleWsOrder(data),
        onWallet: (data) => this.handleWsWallet(data),
        onConnect: () => {
          this.health.wsStatus = 'CONNECTED';
          this.updateAggregateStatus();
          const pairs = this.rest.getAllSupportedPairs();
          const allSymbols = Array.from(new Set([...pairs, 'BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P']));
          this.ws.subscribe('v2/ticker', allSymbols);
          this.ws.subscribe('v2/positions');
          this.ws.subscribe('v2/orders');
          this.ws.subscribe('v2/wallet');
          eventBus.emit('delta:ws:connected');
        },
        onDisconnect: () => {
          this.health.wsStatus = 'DISCONNECTED';
          this.updateAggregateStatus();
          eventBus.emit('delta:ws:disconnected');
        },
        onError: () => {
          this.health.wsStatus = 'RECONNECTING';
          this.updateAggregateStatus();
        },
      }
    );
  }

  private updateAggregateStatus(): void {
    if (this.health.wsStatus === 'CONNECTED' && this.health.restStatus === 'CONNECTED') {
      this.health.status = 'CONNECTED';
    } else if (this.health.wsStatus === 'RECONNECTING') {
      this.health.status = 'RECONNECTING';
    } else {
      this.health.status = 'DISCONNECTED';
    }
  }

  public async start(): Promise<void> {
    try {
      await this.rest.loadProducts();
      this.health.restStatus = 'CONNECTED';
    } catch {
      this.health.restStatus = 'DEGRADED';
    }

    this.ws.connect();

    const pairs = this.rest.getAllSupportedPairs();
    this.ws.subscribe('v2/ticker', pairs);
    this.ws.subscribe('v2/positions');
    this.ws.subscribe('v2/orders');
    this.ws.subscribe('v2/wallet');

    const hasData = await HistoricalBackfillService.hasSufficientData();
    if (!hasData) {
      console.log('[DeltaSyncService] Insufficient historical data, running backfill...');
      await HistoricalBackfillService.backfillAll(this.rest);
    }

    await this.reconcile();
    this.syncTimer = setInterval(() => this.reconcile(), 30000);
  }

  public async reconcile(): Promise<void> {
    try {
      if (!this.rest.isProductsCacheFresh()) {
        try {
          await this.rest.loadProducts();
        } catch (err) {
          console.warn('[DeltaSyncService] Product metadata refresh notice during reconcile:', err instanceof Error ? err.message : err);
        }
      }

      const [balances, positions, orders, history] = await Promise.all([
        this.rest.getWalletBalances().catch(() => []),
        this.rest.getPositions().catch(() => []),
        this.rest.getOrders({ status: 'open' }).catch(() => []),
        this.rest.getHistory({ limit: 50 }).catch(() => []),
      ]);

      this.latestBalances = balances;
      this.latestPositions = positions;
      this.latestOrders = orders;
      this.latestHistory = history;

      this.reconcileCount++;
      this.health.reconcileCount = this.reconcileCount;
      this.health.lastSyncTime = new Date().toISOString();
      this.health.restStatus = 'CONNECTED';
      this.updateAggregateStatus();

      eventBus.emit('delta:synced', {
        balances: this.latestBalances,
        positions: this.latestPositions,
        orders: this.latestOrders,
      });
    } catch (err) {
      this.health.restStatus = 'ERROR';
      this.updateAggregateStatus();
      console.warn('[DeltaSyncService] Reconciliation notice:', err instanceof Error ? err.message : err);
    }
  }

  public getBalances(): DeltaWalletBalance[] {
    return this.latestBalances;
  }

  public getPositions(): DeltaPosition[] {
    return this.latestPositions;
  }

  public getOrders(): DeltaOrder[] {
    return this.latestOrders;
  }

  public getHistory(): any[] {
    return this.latestHistory;
  }

  public getHealth(): DeltaHealthStatus {
    return this.health;
  }

  public isConnected(): boolean {
    return this.health.wsStatus === 'CONNECTED';
  }

  public getConnectionStatus(): 'CONNECTED' | 'DISCONNECTED' | 'CONNECTING' {
    if (this.health.wsStatus === 'CONNECTED') return 'CONNECTED';
    if (this.health.wsStatus === 'RECONNECTING') return 'CONNECTING';
    return 'DISCONNECTED';
  }

  public getRestClient(): DeltaRestClient {
    return this.rest;
  }

  public onPriceTick(callback: (tick: { symbol: string; price: number; timestamp: number }) => void): void {
    this.priceTickCallbacks.push(callback);
  }

  public onWsPositionUpdate(callback: WsPositionCallback): void {
    this.wsPositionCallbacks.push(callback);
  }

  public async getMarkPrice(symbol: string): Promise<number | null> {
    try {
      const ticker = await this.rest.getTicker(symbol);
      if (ticker?.mark_price) {
        return parseFloat(ticker.mark_price);
      }
      if (ticker?.close) {
        return parseFloat(ticker.close);
      }
      if (ticker?.spot_price) {
        return parseFloat(ticker.spot_price);
      }
    } catch (err) {
      logger.warn(`[DeltaSyncService] Failed to get mark price for ${symbol}:`, err);
    }
    return null;
  }

  public async closePosition(symbol: string): Promise<{ success: boolean; error?: string }> {
    try {
      // ── F-2 Position integrity: verify the position exists FIRST ────────────────
      const positions = this.getPositions();
      const pos = positions.find(p => this.rest.toInternalSymbol(p.product_symbol) === symbol);

      if (!pos || Math.abs(pos.size) === 0) {
        return { success: false, error: 'No open position to close' };
      }

      // Compute and enforce the exact reverse side (never open, never increase)
      const closeSide = pos.side === 'buy' ? 'sell' : 'buy';
      // Clamp to actual position size — never exceed what is open
      const size = Math.abs(pos.size);

      // Product ID comes from the authoritative position record (not user input)
      const product = this.rest.getProduct(pos.product_symbol);
      if (!product || !product.id) {
        return { success: false, error: `Product metadata not found for ${pos.product_symbol} — cannot close safely` };
      }

      // ── F-2 Safety check: ALLOW_LIVE_TRADING must be set ────────────────────
      // Protective closes of existing positions are exempt from the
      // isExplicitUserConfirmed re-confirmation gate (which is cleared on restart)
      // because the position itself constitutes prior authorization.
      // However, ALLOW_LIVE_TRADING must still be configured.
      const isAllowLiveSet = process.env.ALLOW_LIVE_TRADING === 'true';
      if (!isAllowLiveSet) {
        logger.warn(`[DeltaSyncService] Protective close blocked — ALLOW_LIVE_TRADING not set for ${symbol}`);
        return { success: false, error: 'PROTECTIVE_CLOSE_REJECTED: ALLOW_LIVE_TRADING is not enabled' };
      }

      // Kill-switch check: protective closes are ALLOWED through the kill switch
      // (the kill switch itself triggers closes), but log a warning if active.
      if (EmergencyKillSwitch.isKillSwitchActive()) {
        logger.warn(`[DeltaSyncService] Protective close proceeding through ACTIVE kill switch for ${symbol}`);
      }

      // Submit: always reduce_only, authoritative product_id, exact actual size
      const result = await this.rest.placeOrder({
        product_id: product.id,             // from authoritative product cache
        product_symbol: pos.product_symbol, // from authoritative position
        side: closeSide,                    // exact reverse of open position
        order_type: 'market',
        size,                               // clamped to actual position size
        reduce_only: true,                  // never increase exposure
      });

      logger.info(`[DeltaSyncService] Close position order placed: ${symbol} ${closeSide} ${size}`, result);
      return { success: true };
    } catch (err: any) {
      const errorMsg = err?.response?.data?.error?.message || err?.message || 'Failed to close position';
      logger.error(`[DeltaSyncService] Close position failed for ${symbol}:`, errorMsg);
      return { success: false, error: errorMsg };
    }
  }

  private handleTicker(data: any): void {
    if (!data?.symbol || !data?.price) return;
    const price = parseFloat(data.price);
    const volume = parseFloat(data.volume_24h || '0');
    candleEngine.ingestTick(data.symbol, price, volume, new Date());
    eventBus.emit('ticker:live', data);

    MarketSnapshotService.updateSnapshot(data.symbol, price);

    const tickObj = { symbol: data.symbol, price, timestamp: Date.now() };
    for (const cb of this.priceTickCallbacks) {
      try { cb(tickObj); } catch { /* ignore error */ }
    }
  }

  private handleWsPosition(data: any): void {
    eventBus.emit('position:live', data);
    void this.reconcile();

    try {
      const position: DeltaPosition = data;
      for (const cb of this.wsPositionCallbacks) {
        try { cb(position); } catch { /* ignore */ }
      }
    } catch { /* ignore */ }
  }

  private handleWsOrder(data: any): void {
    eventBus.emit('order:live', data);
    void this.reconcile();
  }

  private handleWsWallet(data: any): void {
    eventBus.emit('wallet:live', data);
    void this.reconcile();
  }

  public async updateCredentials(
    credentials: { apiKey: string; apiSecret: string }
  ): Promise<{ success: boolean; message?: string }> {
    this.stop();

    this.rest = new DeltaRestClient(credentials);
    this.ws = new DeltaWebSocketClient(
      credentials,
      {
        onTicker: (data) => this.handleTicker(data),
        onPosition: (data) => this.handleWsPosition(data),
        onOrder: (data) => this.handleWsOrder(data),
        onWallet: (data) => this.handleWsWallet(data),
        onConnect: () => {
          this.health.wsStatus = 'CONNECTED';
          this.updateAggregateStatus();
          eventBus.emit('delta:ws:connected');
        },
        onDisconnect: () => {
          this.health.wsStatus = 'DISCONNECTED';
          this.updateAggregateStatus();
          eventBus.emit('delta:ws:disconnected');
        },
        onError: () => {
          this.health.wsStatus = 'RECONNECTING';
          this.updateAggregateStatus();
        },
      }
    );

    if (!credentials.apiKey || !credentials.apiSecret) {
      this.health.status = 'DISCONNECTED';
      this.health.restStatus = 'UNCONFIGURED';
      this.health.wsStatus = 'DISCONNECTED';
      this.latestBalances = [];
      this.latestPositions = [];
      this.latestOrders = [];
      return { success: true, message: 'Credentials cleared' };
    }

    try {
      await this.start();
      return { success: true, message: 'Delta Exchange connection initialized' };
    } catch (err: any) {
      this.health.status = 'ERROR';
      this.health.restStatus = 'ERROR';
      return { success: false, message: err?.message || 'Failed to start Delta sync' };
    }
  }

  public static async testCredentials(
    credentials: { apiKey: string; apiSecret: string }
  ): Promise<{ success: boolean; latencyMs: number; message: string; data?: any }> {
    const startTime = Date.now();
    try {
      const testRest = new DeltaRestClient(credentials);
      await testRest.loadProducts();
      const balances = await testRest.getWalletBalances();
      const latencyMs = Date.now() - startTime;
      return {
        success: true,
        latencyMs,
        message: `Successfully connected to Delta Exchange (Live India). Retrieved ${balances.length} balance assets.`,
        data: {
          balancesCount: balances.length,
          productsCount: testRest.getAllSupportedPairs().length,
        },
      };
    } catch (err: any) {
      const latencyMs = Date.now() - startTime;
      const errorMsg = err?.response?.data?.error?.message || err?.message || 'Authentication failed. Please verify API Key & Secret.';
      return {
        success: false,
        latencyMs,
        message: `Connection failed: ${errorMsg}`,
      };
    }
  }

  public stop(): void {
    this.ws.disconnect();
    if (this.syncTimer) {
      clearInterval(this.syncTimer);
      this.syncTimer = null;
    }
  }
}