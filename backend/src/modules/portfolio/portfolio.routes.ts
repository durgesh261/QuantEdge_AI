import { Router } from 'express';
import { PortfolioController } from './portfolio.controller.js';
import { PortfolioAggregationService } from './PortfolioAggregationService.js';
import { deltaPortfolioService } from '../delta-exchange/index.js';

export function createPortfolioRouter(portfolioService: PortfolioAggregationService): Router {
  const router = Router();
  const controller = new PortfolioController(portfolioService);

  router.get('/summary', controller.getSummary);
  router.get('/wallet', controller.getWallet);
  router.get('/positions', controller.getPositions);
  router.get('/orders', controller.getOrders);
  router.get('/pnl', controller.getPnl);
  router.get('/analytics', controller.getAnalytics);
  router.get('/funding', controller.getFunding);

  // GET /api/v1/portfolio/account-state — Live Delta Exchange account data
  router.get('/account-state', (_req, res) => {
    try {
      const portfolio = deltaPortfolioService.getPortfolio();

      res.json({
        success: true,
        data: {
          balance: portfolio.totalEquity,
          equity: portfolio.totalEquity,
          usedMargin: portfolio.positionMargin + portfolio.orderMargin,
          availableMargin: portfolio.availableMargin,
          unrealizedPnl: portfolio.unrealizedPnl,
          realizedPnl: portfolio.realizedPnl,
          todayPnl: portfolio.todayPnl,
          openPositions: portfolio.openPositionsCount,
          openOrders: portfolio.openOrdersCount,
          balances: portfolio.balances,
        },
      });
    } catch (err: any) {
      console.error('[Portfolio] account-state error:', err);
      res.status(500).json({ success: false, error: err.message });
    }
  });

  return router;
}

