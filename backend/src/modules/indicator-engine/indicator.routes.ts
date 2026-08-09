import { Router } from 'express';
import { asyncHandler } from '../../middleware/asyncHandler.js';
import { evaluateIndicator, getOrderBlocks } from './indicator.controller.js';

const router = Router();

router.get('/evaluate', asyncHandler(evaluateIndicator));
router.post('/evaluate', asyncHandler(evaluateIndicator));

router.get('/order-blocks', asyncHandler(getOrderBlocks));

export const indicatorRouter = router;
