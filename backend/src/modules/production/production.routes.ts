import { Router } from 'express';
import authMiddleware from '../../middleware/authenticate';
import { asyncHandler } from '../../middleware/asyncHandler.js';
import {
  getProductionOverview,
  setExecutionMode,
  triggerBackup,
} from './production.controller.js';

const router = Router();

router.get('/overview', asyncHandler(getProductionOverview));
router.post('/mode', authMiddleware, asyncHandler(setExecutionMode));
router.post('/backup', authMiddleware, asyncHandler(triggerBackup));

export const productionRouter = router;
