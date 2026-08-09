type WebSocketState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'DISCONNECTED';

export interface LiveTicker {
  markPrice: number;
  indexPrice: number;
  symbol: string;
}

type EventCallback = (data: any) => void;

class ChartWebSocketService {
  private ws: WebSocket | null = null;
  // Connect to YOUR backend WebSocket, NOT directly to Delta
  private url = (import.meta as any).env?.VITE_WS_URL || 'ws://localhost:4000/ws';
  private state: WebSocketState = 'DISCONNECTED';
  private currentSymbol: string | null = null;
  private listeners: Record<'stateChange' | 'ticker' | 'candle' | 'signal' | 'trade_closed' | 'portfolio' | 'zones', EventCallback[]> = {
    stateChange: [],
    ticker: [],
    candle: [],
    signal: [],
    trade_closed: [],
    portfolio: [],
    zones: [],
  };
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  public connect(symbol: string) {
    if (this.ws && (this.state === 'CONNECTED' || this.state === 'CONNECTING')) {
      if (this.currentSymbol === symbol) return;
      this.currentSymbol = symbol;
      this.subscribeSymbol(symbol);
      return;
    }

    this.currentSymbol = symbol;
    this.setState('CONNECTING');

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.setState('CONNECTED');
        this.subscribeSymbol(symbol);
        this.startPing();
      };

      this.ws.onmessage = (event) => this.handleMessage(event);

      this.ws.onclose = () => {
        this.cleanup();
        this.setState('DISCONNECTED');
        this.scheduleReconnect();
      };

      this.ws.onerror = (error) => {
        console.error('Backend WebSocket Error:', error);
        this.ws?.close();
      };
    } catch (e) {
      console.error('Failed to create WebSocket:', e);
      this.setState('DISCONNECTED');
      this.scheduleReconnect();
    }
  }

  public disconnect() {
    this.cleanup();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.setState('DISCONNECTED');
  }

  public on(event: 'stateChange' | 'ticker' | 'candle' | 'signal' | 'trade_closed' | 'portfolio' | 'zones', callback: EventCallback) {
    this.listeners[event].push(callback);
    if (event === 'stateChange') callback(this.state);
  }

  public off(event: 'stateChange' | 'ticker' | 'candle' | 'signal' | 'trade_closed' | 'portfolio' | 'zones', callback: EventCallback) {
    this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
  }

  private setState(newState: WebSocketState) {
    this.state = newState;
    this.emit('stateChange', newState);
  }

  private emit(event: 'stateChange' | 'ticker' | 'candle' | 'signal' | 'trade_closed' | 'portfolio' | 'zones', data: any) {
    (this.listeners[event] || []).forEach(cb => cb(data));
  }

  private subscribeSymbol(symbol: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({
      type: 'subscribe',
      channel: 'all',
      symbols: [symbol],
    }));
  }

  private handleMessage(event: MessageEvent) {
    try {
      const data = JSON.parse(event.data);

      if (data.type === 'ticker' && data.symbol) {
        const ticker: LiveTicker = {
          markPrice: parseFloat(data.markPrice || 0),
          indexPrice: parseFloat(data.indexPrice || 0),
          symbol: data.symbol,
        };
        if (ticker.markPrice > 0) {
          this.emit('ticker', ticker);
        }
      }

      if (data.type === 'candle' && data.symbol) {
        this.emit('candle', data);
      }
      
      if (data.type === 'signal') {
        this.emit('signal', data);
      }
      if (data.type === 'trade_closed') {
        this.emit('trade_closed', data);
      }
      if (data.type === 'portfolio') {
        this.emit('portfolio', data);
      }
      if (data.type === 'zones') {
        this.emit('zones', data);
      }

    } catch (e) {
      // Ignore parse errors
    }
  }

  private startPing() {
    this.cleanup();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  }

  private cleanup() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.state === 'RECONNECTING') return;
    this.setState('RECONNECTING');
    this.reconnectTimer = setTimeout(() => {
      if (this.currentSymbol) {
        this.connect(this.currentSymbol);
      }
    }, 3000);
  }
}

export const chartWebSocketService = new ChartWebSocketService();


