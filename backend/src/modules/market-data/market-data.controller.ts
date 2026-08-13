import { Request, Response } from 'express';
import { ApiResponse } from '@algoapp/shared';
import { ingestCandleSchema, getMarketCandlesQuerySchema } from '@algoapp/shared';
import { CandleStoreService } from './services/candleStore.service.js';
import { MarketSnapshotService } from './services/marketSnapshot.service.js';
import { MarketEventGenerator } from './services/marketEventGenerator.js';

export const getMarketSnapshot = async (req: Request, res: Response): Promise<void> => {
  const symbol = (req.query['symbol'] as string) || 'BTCUSD.P';
  const snapshot = await MarketSnapshotService.getSnapshot(symbol);

  if (!snapshot) {
    const response = {
      success: false,
      data: null,
      error: 'MARKET_DATA_UNAVAILABLE',
      message: `Real-time market data for ${symbol} unavailable from Delta Exchange India`,
      meta: {
        requestId: (req as any).correlationId || 'req-market-snapshot',
        timestamp: new Date().toISOString(),
      },
    };
    res.status(503).json(response);
    return;
  }

  const response: ApiResponse<typeof snapshot> = {
    success: true,
    data: snapshot,
    meta: {
      requestId: (req as any).correlationId || 'req-market-snapshot',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const getMarketCandles = async (req: Request, res: Response): Promise<void> => {
  const query = getMarketCandlesQuerySchema.parse(req.query);
  const candles = await CandleStoreService.getCandles(query.symbol, query.timeframe, query.limit);

  const response: ApiResponse<typeof candles> = {
    success: true,
    data: candles,
    meta: {
      requestId: (req as any).correlationId || 'req-market-candles',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const ingestCandle = async (req: Request, res: Response): Promise<void> => {
  const validated = ingestCandleSchema.parse(req.body);
  const candle = await CandleStoreService.ingestCandle(validated);

  const response: ApiResponse<typeof candle> = {
    success: true,
    data: candle,
    meta: {
      requestId: (req as any).correlationId || 'req-ingest-candle',
      timestamp: new Date().toISOString(),
    },
  };
  res.status(201).json(response);
};

export const getMarketEvents = async (req: Request, res: Response): Promise<void> => {
  const events = await MarketEventGenerator.getEvents();

  const response: ApiResponse<typeof events> = {
    success: true,
    data: events,
    meta: {
      requestId: (req as any).correlationId || 'req-market-events',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};

export const getMarketDataStatus = async (req: Request, res: Response): Promise<void> => {
  const symbol = (req.query['symbol'] as string) || 'BTCUSD.P';
  const status = MarketSnapshotService.getDataStatus(symbol);
  
  const response: ApiResponse<typeof status> = {
    success: true,
    data: status,
    meta: {
      requestId: (req as any).correlationId || 'req-market-status',
      timestamp: new Date().toISOString(),
    },
  };
  res.json(response);
};
