import { Request, Response } from 'express';
import { ApiResponse, getIsoUtcTimestamp } from '@algoapp/shared';
import { SettingsService } from './services/settings.service.js';
import { config } from '../../config/index.js';

export class SettingsController {
  public static async getSettings(req: Request, res: Response): Promise<void> {
    const requestId = (req.headers[config.correlationHeader.toLowerCase()] as string) || 'req-settings-get';
    const settings = await SettingsService.getSettings();

    const response: ApiResponse<typeof settings> = {
      success: true,
      data: settings,
      meta: {
        requestId,
        timestamp: getIsoUtcTimestamp(),
      },
    };

    res.status(200).json(response);
  }

  public static async saveDeltaCredentials(req: Request, res: Response): Promise<void> {
    const requestId = (req.headers[config.correlationHeader.toLowerCase()] as string) || 'req-settings-save-delta';
    const { apiKey, apiSecret } = req.body;

    if (!apiKey || typeof apiKey !== 'string') {
      res.status(400).json({
        success: false,
        error: { code: 'INVALID_INPUT', message: 'API Key is required' },
        meta: { requestId, timestamp: getIsoUtcTimestamp() },
      });
      return;
    }

    if (!apiSecret || typeof apiSecret !== 'string') {
      res.status(400).json({
        success: false,
        error: { code: 'INVALID_INPUT', message: 'API Secret is required' },
        meta: { requestId, timestamp: getIsoUtcTimestamp() },
      });
      return;
    }

    const result = await SettingsService.saveDeltaCredentials({
      apiKey,
      apiSecret,
      environment: 'PRODUCTION',
    });

    const response: ApiResponse<typeof result> = {
      success: true,
      data: result,
      meta: {
        requestId,
        timestamp: getIsoUtcTimestamp(),
      },
    };

    res.status(200).json(response);
  }

  public static async testDeltaCredentials(req: Request, res: Response): Promise<void> {
    const requestId = (req.headers[config.correlationHeader.toLowerCase()] as string) || 'req-settings-test-delta';
    const { apiKey, apiSecret } = req.body;

    if (!apiKey || typeof apiKey !== 'string') {
      res.status(400).json({
        success: false,
        error: { code: 'INVALID_INPUT', message: 'API Key is required to test connection' },
        meta: { requestId, timestamp: getIsoUtcTimestamp() },
      });
      return;
    }

    if (!apiSecret || typeof apiSecret !== 'string') {
      res.status(400).json({
        success: false,
        error: { code: 'INVALID_INPUT', message: 'API Secret is required to test connection' },
        meta: { requestId, timestamp: getIsoUtcTimestamp() },
      });
      return;
    }

    const result = await SettingsService.testDeltaCredentials({
      apiKey,
      apiSecret,
      environment: 'PRODUCTION',
    });

    const response: ApiResponse<typeof result> = {
      success: true,
      data: result,
      meta: {
        requestId,
        timestamp: getIsoUtcTimestamp(),
      },
    };

    res.status(200).json(response);
  }

  public static async deleteDeltaCredentials(req: Request, res: Response): Promise<void> {
    const requestId = (req.headers[config.correlationHeader.toLowerCase()] as string) || 'req-settings-del-delta';
    const result = await SettingsService.deleteDeltaCredentials();

    const response: ApiResponse<typeof result> = {
      success: true,
      data: result,
      meta: {
        requestId,
        timestamp: getIsoUtcTimestamp(),
      },
    };

    res.status(200).json(response);
  }
}
