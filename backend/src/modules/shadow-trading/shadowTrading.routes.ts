import { Router } from 'express';
import { asyncHandler } from '../../middleware/asyncHandler.js';
import { getShadowDashboard, triggerShadowCycle, getShadowPositions, getShadowOutcomes } from './shadowTrading.controller.js';

const router = Router();

router.get('/dashboard', asyncHandler(getShadowDashboard));
router.post('/cycle', asyncHandler(triggerShadowCycle));
router.get('/positions', asyncHandler(getShadowPositions));
router.get('/outcomes', asyncHandler(getShadowOutcomes));

export const shadowTradingRouter = router;
