import { Router } from 'express';
import { asyncHandler } from '../../middleware/asyncHandler.js';
import {
  getMarketSnapshot,
  getMarketCandles,
  ingestCandle,
  getMarketEvents,
  getMarketDataStatus,
} from './market-data.controller.js';

const router = Router();

router.get('/snapshot', asyncHandler(getMarketSnapshot));
router.get('/candles', asyncHandler(getMarketCandles));
router.post('/candles', asyncHandler(ingestCandle));
router.get('/events', asyncHandler(getMarketEvents));
router.get('/status', asyncHandler(getMarketDataStatus));

export const marketDataRouter = router;
