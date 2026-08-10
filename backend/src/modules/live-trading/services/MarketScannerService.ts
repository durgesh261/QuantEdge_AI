import { eventBus } from '../../../services/EventBus.js';

const SCANNER_SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];

export interface PairTelemetry {
  symbol: string;
  livePrice: number;
  activeOrderBlocksCount: number;
  orderBlockWidthPercent: number;
  scanState: 'IDLE' | 'SCANNING' | 'EVALUATING' | 'SIGNAL_TRIGGERED' | 'ERROR' | 'NO_DATA';
  latestConfidenceScore: number;
  lastScanAt: string;
  error?: string;
  userStatus?: 'RUNNING' | 'PAUSED' | 'STOPPED';
}

export class MarketScannerService {
  private static pairTelemetry: Record<string, PairTelemetry> = {};
  private static ticksProcessed = 0;
  private static signalsTriggered = 0;
  private static tradesExecuted = 0;
  private static state: 'IDLE' | 'RUNNING' | 'PAUSED' | 'STOPPED' = 'IDLE';

  public static initialize(): void {
    // Initialize telemetry for all 4 pairs
    for (const symbol of SCANNER_SYMBOLS) {
      this.pairTelemetry[symbol] = {
        symbol,
        livePrice: 0,
        activeOrderBlocksCount: 0,
        orderBlockWidthPercent: 0,
        scanState: 'IDLE',
        latestConfidenceScore: 0,
        lastScanAt: new Date().toISOString(),
        userStatus: 'RUNNING',
      };
    }

    // Listen to tick events from Delta WebSocket for telemetry tracking
    eventBus.on('ticker:live', (data: any) => {
      this.ticksProcessed++;
      const sym = this.normalizeSymbol(data.symbol);
      if (this.pairTelemetry[sym]) {
        this.pairTelemetry[sym].livePrice = parseFloat(data.price);
        this.pairTelemetry[sym].lastScanAt = new Date().toISOString();
      }
    });

    // Listen to OB touched events from canonical ScannerEngine
    eventBus.on('ob:touched', (data: any) => {
      const sym = this.normalizeSymbol(data.symbol);
      if (this.pairTelemetry[sym]) {
        this.pairTelemetry[sym].scanState = 'EVALUATING';
      }
    });
  }

  public static updateTelemetry(symbol: string, data: Partial<PairTelemetry>): void {
    const sym = this.normalizeSymbol(symbol);
    if (this.pairTelemetry[sym]) {
      Object.assign(this.pairTelemetry[sym]!, data, { lastScanAt: new Date().toISOString() });
    }
  }

  public static getTelemetry(): PairTelemetry[] {
    return SCANNER_SYMBOLS.map((s) => this.pairTelemetry[s]).filter((t): t is PairTelemetry => t !== undefined);
  }

  public static getStats(): { ticks: number; signals: number; trades: number } {
    return {
      ticks: this.ticksProcessed,
      signals: this.signalsTriggered,
      trades: this.tradesExecuted,
    };
  }

  public static getState(): string {
    return this.state;
  }

  public static setState(state: 'IDLE' | 'RUNNING' | 'PAUSED' | 'STOPPED'): void {
    this.state = state;
    eventBus.emit('scanner:state_changed', { state });
  }

  public static setPairStatus(symbol: string, status: 'RUNNING' | 'PAUSED' | 'STOPPED'): void {
    const sym = this.normalizeSymbol(symbol);
    if (this.pairTelemetry[sym]) {
      this.pairTelemetry[sym].userStatus = status;
    }
  }

  private static normalizeSymbol(symbol: string): string {
    const map: Record<string, string> = {
      'BTCUSD': 'BTCUSD.P',
      'ETHUSD': 'ETHUSD.P',
      'SOLUSD': 'SOLUSD.P',
      'XRPUSD': 'XRPUSD.P',
    };
    return map[symbol] || symbol;
  }

  public static recordTradeExecuted(): void {
    this.tradesExecuted++;
  }
}
