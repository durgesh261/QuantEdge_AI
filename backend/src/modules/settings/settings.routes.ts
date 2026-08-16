import { Router } from 'express';
import authMiddleware from '../../middleware/authenticate.js';
import { asyncHandler } from '../../middleware/asyncHandler.js';
import { SettingsController } from './settings.controller.js';
import { ApiResponse, getIsoUtcTimestamp } from '@algoapp/shared';
import { config } from '../../config/index.js';

export const settingsRouter = Router();

// Legacy status endpoint
settingsRouter.get('/status', (req, res) => {
  const requestId = (req.headers[config.correlationHeader.toLowerCase()] as string) || 'unknown';
  const response: ApiResponse<{ module: string; status: string }> = {
    success: true,
    data: {
      module: 'settings',
      status: 'initialized',
    },
    meta: {
      requestId,
      timestamp: getIsoUtcTimestamp(),
    },
  };
  res.status(200).json(response);
});

// Settings & Delta API Key Management
// These endpoints require authentication
settingsRouter.post('/delta-credentials', authMiddleware, asyncHandler(SettingsController.saveDeltaCredentials));
settingsRouter.post('/delta-credentials/test', authMiddleware, asyncHandler(SettingsController.testDeltaCredentials));
settingsRouter.delete('/delta-credentials', authMiddleware, asyncHandler(SettingsController.deleteDeltaCredentials));
