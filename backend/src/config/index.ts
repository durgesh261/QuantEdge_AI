import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });
dotenv.config();

export const config = {
  env: process.env['NODE_ENV'] ?? 'development',
  port: parseInt(process.env['APP_PORT'] ?? '4000', 10),
  publicUrl: process.env['APP_PUBLIC_URL'] ?? 'http://localhost:3000',
  databaseUrl: process.env['DATABASE_URL'] ?? '',
  corsOrigin: (process.env['CORS_ORIGIN'] ?? 'http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:5173').split(','),
  correlationHeader: process.env['CORRELATION_HEADER'] ?? 'X-Request-Id',
};
