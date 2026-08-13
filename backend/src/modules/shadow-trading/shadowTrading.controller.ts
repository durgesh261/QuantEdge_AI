import { Request, Response } from 'express';
import { ApiResponse } from '@algoapp/shared';
import { ShadowTradingEngineService } from './services/shadowTradingEngine.service.js';
import { prisma } from '../../db.js';
import { deltaSyncService } from '../../modules/delta-exchange/index.js';

export const getShadowDashboard = async (req: Request, res: Response): Promise<void> => {
  const data = await ShadowTradingEngineService.getDashboardData();

  const response: ApiResponse<typeof data> = {
    success: true,
    data,
    meta: {
      requestId: (req as any).correlationId || 'req-shadow-dashboard',
      timestamp: new Date().toISOString(),
    },
  };

  res.json(response);
};

export const triggerShadowCycle = async (req: Request, res: Response): Promise<void> => {
  const result = await ShadowTradingEngineService.runShadowCycle();

  const response: ApiResponse<typeof result> = {
    success: true,
    data: result,
    meta: {
      requestId: (req as any).correlationId || 'req-shadow-cycle',
      timestamp: new Date().toISOString(),
    },
  };

  res.status(201).json(response);
};

export const getShadowPositions = async (req: Request, res: Response): Promise<void> => {
  const positions = await prisma.shadowPosition.findMany({
    where: { status: 'OPEN' },
    orderBy: { createdAt: 'desc' },
  });

  // Enrich with current mark prices and P&L
  const enrichedPositions = await Promise.all(
    positions.map(async (position) => {
      let currentPrice: number | null = null;
      try {
        currentPrice = await deltaSyncService.getMarkPrice(position.symbol);
      } catch {
        currentPrice = null;
      }

      let hypotheticalPnl = 0;
      let hypotheticalPnlPercent = 0;
      if (currentPrice !== null) {
        if (position.side === 'LONG') {
          hypotheticalPnl = (currentPrice - position.entryPrice) * position.quantity;
          hypotheticalPnlPercent = ((currentPrice - position.entryPrice) / position.entryPrice) * 100 * position.leverage;
        } else {
          hypotheticalPnl = (position.entryPrice - currentPrice) * position.quantity;
          hypotheticalPnlPercent = ((position.entryPrice - currentPrice) / position.entryPrice) * 100 * position.leverage;
        }
      }

      return {
        ...position,
        currentPrice,
        hypotheticalPnl,
        hypotheticalPnlPercent,
      };
    })
  );

  const response: ApiResponse<typeof enrichedPositions> = {
    success: true,
    data: enrichedPositions,
    meta: {
      requestId: (req as any).correlationId || 'req-shadow-positions',
      timestamp: new Date().toISOString(),
    },
  };

  res.json(response);
};

export const getShadowOutcomes = async (req: Request, res: Response): Promise<void> => {
  const outcomes = await prisma.marketOutcomeValidation.findMany({
    orderBy: { createdAt: 'desc' },
    take: 50,
  });

  // Join with shadow positions to get symbol, side, entry, exit info
  const enrichedOutcomes = await Promise.all(
    outcomes.map(async (outcome) => {
      const position = await prisma.shadowPosition.findUnique({
        where: { decisionId: outcome.decisionId },
      });

      return {
        ...outcome,
        symbol: position?.symbol || 'UNKNOWN',
        side: position?.side || 'UNKNOWN',
        entryPrice: position?.entryPrice || 0,
        exitPrice: outcome.tpHit ? position?.takeProfitPrice : outcome.slHit ? position?.stopLossPrice : 0,
        quantity: position?.quantity || 0,
        leverage: position?.leverage || 1,
      };
    })
  );

  const response: ApiResponse<typeof enrichedOutcomes> = {
    success: true,
    data: enrichedOutcomes,
    meta: {
      requestId: (req as any).correlationId || 'req-shadow-outcomes',
      timestamp: new Date().toISOString(),
    },
  };

  res.json(response);
};
