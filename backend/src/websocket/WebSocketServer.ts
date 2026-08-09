import { Server as HttpServer } from 'http';
import { WebSocket, WebSocketServer as WSServer, RawData } from 'ws';
import { eventBus } from '../services/EventBus.js';
import { logger } from '../logger/index.js';

interface ClientMessage {
  type: string;
  channel?: string;
  symbols?: string[];
}

/**
 * WebSocket Server that broadcasts live market data, indicator output,
 * zone updates, and trade events to connected frontend clients.
 * 
 * Strategy §4, §5, §26: Backend is single source of truth.
 * Frontend receives processed data, not raw Delta ticks.
 */
export class WebSocketServer {
  private wss: WSServer | null = null;
  private clients = new Set<WebSocket>();
  private clientSubscriptions = new Map<WebSocket, Set<string>>();
  private readonly HEARTBEAT_INTERVAL = 30000;

  constructor(private server: HttpServer) {}

  public initialize(): void {
    this.wss = new WSServer({ server: this.server, path: '/ws' });

    this.wss.on('connection', (ws: WebSocket) => {
      logger.info('Frontend WebSocket client connected');
      this.clients.add(ws);
      this.clientSubscriptions.set(ws, new Set());

      ws.on('message', (raw: RawData) => {
        try {
          const msg: ClientMessage = JSON.parse(raw.toString());
          this.handleClientMessage(ws, msg);
        } catch (err) {
          logger.warn({ err }, 'Invalid WebSocket message from client');
        }
      });

      ws.on('close', () => {
        logger.info('Frontend WebSocket client disconnected');
        this.clients.delete(ws);
        this.clientSubscriptions.delete(ws);
      });

      ws.on('error', (err) => {
        logger.error({ err }, 'WebSocket client error');
        this.clients.delete(ws);
        this.clientSubscriptions.delete(ws);
      });

      // Send initial connection ack
      ws.send(JSON.stringify({ type: 'connected', message: 'QuantEdge AI Backend WebSocket' }));
    });

    // Subscribe to EventBus events and broadcast to clients
    this.subscribeToEvents();

    // Start heartbeat
    setInterval(() => this.heartbeat(), this.HEARTBEAT_INTERVAL);

    logger.info('WebSocket server initialized on /ws');
  }

  private handleClientMessage(ws: WebSocket, msg: ClientMessage): void {
    if (msg.type === 'subscribe' && msg.channel) {
      const subs = this.clientSubscriptions.get(ws);
      if (subs) {
        if (msg.channel === 'all') {
          subs.add('ticker');
          subs.add('candle');
          subs.add('zones');
          subs.add('signals');
          subs.add('trades');
          subs.add('portfolio');
        } else {
          subs.add(msg.channel);
        }
      }
      ws.send(JSON.stringify({ type: 'subscribed', channel: msg.channel }));
    }

    if (msg.type === 'unsubscribe' && msg.channel) {
      const subs = this.clientSubscriptions.get(ws);
      if (subs) {
        subs.delete(msg.channel);
      }
    }

    if (msg.type === 'ping') {
      ws.send(JSON.stringify({ type: 'pong', timestamp: Date.now() }));
    }
  }

  private subscribeToEvents(): void {
    // Live ticker from Delta → broadcast processed ticker
    eventBus.on('ticker:live', (data: any) => {
      this.broadcast('ticker', {
        type: 'ticker',
        symbol: data.symbol,
        markPrice: parseFloat(data.price || 0),
        timestamp: Date.now(),
      });
    });

    // Candle updates
    eventBus.on('candle:1H:update', (data: any) => {
      this.broadcast('candle', {
        type: 'candle',
        symbol: data.symbol,
        timeframe: '1H',
        candle: data.candle,
        isNew: data.isNew,
        timestamp: Date.now(),
      });
    });

    // Zone updates
    eventBus.on('zones:updated', (data: any) => {
      this.broadcast('zones', {
        type: 'zones',
        symbol: data.symbol,
        zones: data.zones,
        timestamp: Date.now(),
      });
    });

    // Signal triggered
    eventBus.on('scanner:trade:executed', (data: any) => {
      this.broadcast('signals', {
        type: 'signal',
        ...data,
      });
    });

    // Trade closed
    eventBus.on('trade:accounted', (data: any) => {
      this.broadcast('trades', {
        type: 'trade_closed',
        symbol: data.symbol,
        realizedPnl: data.netPnL,
        tradeId: data.tradeId,
        timestamp: Date.now(),
      });
    });

    // Portfolio sync
    eventBus.on('delta:synced', (data: any) => {
      this.broadcast('portfolio', {
        type: 'portfolio',
        balances: data.balances,
        positions: data.positions,
        orders: data.orders,
        timestamp: Date.now(),
      });
    });

    // Scanner state changes
    eventBus.on('scanner:state', (data: any) => {
      this.broadcast('signals', {
        type: 'scanner_state',
        ...data,
      });
    });
  }

  private broadcast(channel: string, payload: any): void {
    const message = JSON.stringify(payload);
    for (const [client, subs] of this.clientSubscriptions.entries()) {
      if (client.readyState === WebSocket.OPEN && subs.has(channel)) {
        try {
          client.send(message);
        } catch (err) {
          logger.warn({ err }, 'Failed to send WebSocket message');
        }
      }
    }
  }

  private heartbeat(): void {
    const pingMsg = JSON.stringify({ type: 'ping', timestamp: Date.now() });
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) {
        try {
          client.send(pingMsg);
        } catch {
          this.clients.delete(client);
          this.clientSubscriptions.delete(client);
        }
      }
    }
  }

  public shutdown(): void {
    for (const client of this.clients) {
      client.close(1000, 'Server shutting down');
    }
    this.clients.clear();
    this.clientSubscriptions.clear();
    this.wss?.close();
  }
}
