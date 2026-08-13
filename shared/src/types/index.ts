export enum TerminalPage {
  DASHBOARD = 'DASHBOARD',
  PAPER_TRADING = 'PAPER_TRADING',
  LIVE_TRADING = 'LIVE_TRADING',
  ANALYSIS = 'ANALYSIS',
  TRADE_JOURNAL = 'TRADE_JOURNAL',
  ANALYTICS = 'ANALYTICS',
  CHALLENGE = 'CHALLENGE',
  SETTINGS = 'SETTINGS',
}

export enum SystemStatus {
  HEALTHY = 'HEALTHY',
  DEGRADED = 'DEGRADED',
  UNHEALTHY = 'UNHEALTHY',
  MAINTENANCE = 'MAINTENANCE',
}

export enum ErrorCode {
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  NOT_FOUND = 'NOT_FOUND',
  CONFLICT = 'CONFLICT',
  RATE_LIMITED = 'RATE_LIMITED',
  INTERNAL_ERROR = 'INTERNAL_ERROR',
}

export interface ApiMeta {
  requestId: string;
  timestamp: string;
  page?: number;
  limit?: number;
  total?: number;
}

export interface ApiResponse<T> {
  success: true;
  data: T;
  meta: ApiMeta;
}

export interface ApiErrorDetail {
  field?: string;
  message: string;
  code?: string;
}

export interface ApiErrorResponse {
  success: false;
  error: {
    code: ErrorCode;
    message: string;
    requestId: string;
    details?: ApiErrorDetail[];
  };
  meta: ApiMeta;
}

export interface SingleUserProfileDto {
  id: string;
  displayName: string;
  themePreference: 'dark';
  createdAt: string;
  updatedAt: string;
}

export interface SystemHealthStatus {
  status: SystemStatus;
  version: string;
  timestamp: string;
  database: SystemStatus;
  uptimeSeconds: number;
}

export interface SystemSettingsDto {
  id: string;
  defaultCurrency: string;
  timezone: string;
  maxStaleSignalSeconds: number;
  isKillSwitchActive: boolean;
  deltaApiKey?: string;
  hasDeltaApiSecret?: boolean;
  deltaEnvironment?: 'PRODUCTION';
  deltaHealth?: {
    status: 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING' | 'ERROR';
    restStatus: 'CONNECTED' | 'DEGRADED' | 'ERROR' | 'UNCONFIGURED';
    wsStatus: 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING';
    lastSyncTime: string;
    reconcileCount: number;
  };
  updatedAt: string;
}

export interface MarketTickerItem {
  symbol: string;
  name: string;
  price: string;
  change24h: string;
  isPositive: boolean;
}
