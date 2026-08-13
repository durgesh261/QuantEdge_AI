import WebSocket from 'ws';
import crypto from 'crypto';

export interface DeltaWsCallbacks {
  onTicker?: ((data: any) => void) | undefined;
  onPosition?: ((data: any) => void) | undefined;
  onOrder?: ((data: any) => void) | undefined;
  onWallet?: ((data: any) => void) | undefined;
  onConnect?: (() => void) | undefined;
  onDisconnect?: (() => void) | undefined;
  onError?: ((err: Error) => void) | undefined;
}

export class DeltaWebSocketClient {
  private ws: WebSocket | null = null;
  private pingInterval: NodeJS.Timeout | null = null;
  private pongTimeout: NodeJS.Timeout | null = null;
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private isExplicitDisconnect = false;
  private reconnectAttempts = 0;
  private readonly wsUrl = 'wss://socket.india.delta.exchange';

  constructor(
    private credentials: { apiKey: string; apiSecret: string },
    private callbacks: DeltaWsCallbacks
  ) {}

  public connect(): void {
    this.isExplicitDisconnect = false;
    try {
      this.ws = new WebSocket(this.wsUrl);

      this.ws.on('open', () => {
        this.reconnectAttempts = 0;
        this.callbacks.onConnect?.();
        this.startHeartbeat();
        if (this.credentials.apiKey && this.credentials.apiSecret) {
          this.authenticate();
        }
      });

      this.ws.on('pong', () => {
        this.resetPongTimeout();
      });

      this.ws.on('message', (raw: WebSocket.Data) => {
        try {
          const msg = JSON.parse(raw.toString());
          this.handleMessage(msg);
        } catch (err) {
          console.error('[DeltaWS] JSON Parse error:', err);
        }
      });

      this.ws.on('close', () => {
        this.stopHeartbeat();
        this.callbacks.onDisconnect?.();
        if (!this.isExplicitDisconnect) {
          this.scheduleReconnect();
        }
      });

      this.ws.on('error', (err: Error) => {
        console.error('[DeltaWS] Socket Error:', err.message);
        this.callbacks.onError?.(err);
      });
    } catch (err) {
      console.error('[DeltaWS] Connection exception:', err);
      this.scheduleReconnect();
    }
  }

  private authenticate(): void {
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const payload = 'GET' + timestamp + '/live';
    const signature = crypto
      .createHmac('sha256', this.credentials.apiSecret)
      .update(payload)
      .digest('hex');

    this.send({
      type: 'auth',
      payload: {
        'api-key': this.credentials.apiKey,
        signature: signature,
        timestamp: timestamp,
      },
    });
  }

  public subscribe(channel: string, symbols?: string[] | undefined): void {
    this.send({
      type: 'subscribe',
      payload: {
        channels: [
          {
            name: channel,
            symbols: symbols && symbols.length > 0 ? symbols : undefined,
          },
        ],
      },
    });
  }

  private handleMessage(msg: any): void {
    if (!msg) return;
    if (msg.type === 'v2/ticker') {
      this.callbacks.onTicker?.(msg);
    } else if (msg.type === 'v2/positions') {
      this.callbacks.onPosition?.(msg);
    } else if (msg.type === 'v2/orders') {
      this.callbacks.onOrder?.(msg);
    } else if (msg.type === 'v2/wallet') {
      this.callbacks.onWallet?.(msg);
    }
  }

  private startHeartbeat(): void {
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping();
        this.setPongTimeout();
      }
    }, 15000);
  }

  private setPongTimeout(): void {
    if (this.pongTimeout) clearTimeout(this.pongTimeout);
    this.pongTimeout = setTimeout(() => {
      console.warn('[DeltaWS] Heartbeat timeout (30s). Reconnecting...');
      this.ws?.terminate();
    }, 30000);
  }

  private resetPongTimeout(): void {
    if (this.pongTimeout) {
      clearTimeout(this.pongTimeout);
      this.pongTimeout = null;
    }
  }

  private stopHeartbeat(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    this.resetPongTimeout();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.reconnectAttempts++;
    const delay = Math.min(30000, Math.pow(1.5, this.reconnectAttempts) * 1000);
    console.log(`[DeltaWS] Reconnecting in ${(delay / 1000).toFixed(1)}s (attempt ${this.reconnectAttempts})...`);
    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private send(data: any): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  public disconnect(): void {
    this.isExplicitDisconnect = true;
    this.stopHeartbeat();
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.ws?.close();
    this.ws = null;
  }
}