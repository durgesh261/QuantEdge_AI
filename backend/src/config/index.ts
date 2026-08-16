import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });
dotenv.config();

export const config = {
  env: process.env['NODE_ENV'] ?? 'development',
  port: parseInt(process.env['APP_PORT'] ?? '4000', 10),
  publicUrl: process.env['APP_PUBLIC_URL'] || 'https://app.algoapp.ai',
  databaseUrl: process.env['DATABASE_URL'] ?? '',
  corsOrigin: (process.env['CORS_ORIGIN'] ?? '').split(','),
  correlationHeader: process.env['CORRELATION_HEADER'] ?? 'X-Request-Id',
};
