import { AppEnvironment } from '@algoapp/shared';

export interface EnvConfig {
  nodeEnv: AppEnvironment;
  port: number;
  databaseUrl: string;
  tradingViewSecret: string;
  deltaApiKey?: string | undefined;
  deltaApiSecret?: string | undefined;
}

export class EnvValidator {
  public static validateEnv(): EnvConfig {
    const nodeEnv = (process.env.NODE_ENV as AppEnvironment) || 'development';
    const port = parseInt(process.env.PORT || '4000', 10);
    const databaseUrl = process.env.DATABASE_URL || 'file:./algoapp.db';
    const tradingViewSecret = process.env.TRADINGVIEW_WEBHOOK_SECRET;

    if (!tradingViewSecret) {
      throw new Error('TRADINGVIEW_WEBHOOK_SECRET environment variable is required');
    }

    return {
      nodeEnv,
      port,
      databaseUrl,
      tradingViewSecret,
      deltaApiKey: process.env.DELTA_API_KEY,
      deltaApiSecret: process.env.DELTA_API_SECRET,
    };
  }
}
