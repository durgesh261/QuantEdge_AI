import { eventBus } from '../../../services/EventBus.js';
import { prisma } from '../../../db.js';
import { ShadowTradingEngineService } from './shadowTradingEngine.service.js';
import { logger } from '../../../logger/index.js';

interface CandleUpdateEvent {
  symbol: string;
  candle: {
    id: string;
    symbol: string;
    timeframe: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    timestamp: string;
  };
  isNew: boolean;
}

type EventHandler = (payload: unknown) => Promise<void>;

export class ShadowTriggerService {
  private static isRunning = false;
  private static processedCandles = new Set<string>();

  public static clearProcessedCandlesForTesting(): void {
    this.processedCandles.clear();
  }

  public static async start(): Promise<void> {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[ShadowTriggerService] Starting automatic Shadow pipeline trigger on 1H candle close');

    const handler: EventHandler = async (payload: unknown) => {
      const data = payload as CandleUpdateEvent;
      await this.handleCandleUpdate(data);
    };
    eventBus.on('candle:1H:update', handler);
  }

  public static stop(): void {
    if (!this.isRunning) return;
    this.isRunning = false;
    // Note: eventBus doesn't provide easy off() for specific handlers, 
    // but we check isRunning flag in handler
    logger.info('[ShadowTriggerService] Stopped automatic Shadow pipeline trigger');
  }

  private static async handleCandleUpdate(data: CandleUpdateEvent): Promise<void> {
    if (!this.isRunning) return;
    if (!data.isNew) return;

    const { symbol, candle } = data;
    const candleKey = `${symbol}:${candle.timestamp}`;

    // Prevent duplicate processing
    if (this.processedCandles.has(candleKey)) {
      logger.debug(`[ShadowTriggerService] Skipping duplicate candle: ${candleKey}`);
      return;
    }

    // Check if this symbol is an active trading pair
    const activePair = await prisma.scannerPair.findUnique({
      where: { symbol },
      select: { isActive: true, isPaused: true, status: true },
    });

    if (!activePair || !activePair.isActive || activePair.isPaused || activePair.status !== 'ENGINE') {
      logger.debug(`[ShadowTriggerService] Symbol ${symbol} is not an active trading pair`);
      return;
    }

    // Mark as processed
    this.processedCandles.add(candleKey);

    // Keep only recent candles (last 1000) to prevent memory growth
    if (this.processedCandles.size > 1000) {
      const firstKey = this.processedCandles.values().next().value;
      if (firstKey) this.processedCandles.delete(firstKey);
    }

    logger.info(`[ShadowTriggerService] New 1H candle for ${symbol}, triggering Shadow pipeline`);

    try {
      await ShadowTradingEngineService.runShadowCycle(symbol);
      logger.info(`[ShadowTriggerService] Shadow pipeline completed for ${symbol}`);
    } catch (err) {
      logger.error(`[ShadowTriggerService] Shadow pipeline failed for ${symbol}:`, err);
    }
  }
}