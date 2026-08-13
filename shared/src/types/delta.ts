import { ExecutionResultDto, SubmitExecutionInput } from './execution.js';
import { PaperOrderDto, PaperPositionDto } from './paper.js';

export enum DeltaEnvironment {
  PRODUCTION = 'PRODUCTION',
}

export enum DeltaConnectionState {
  DISCONNECTED = 'DISCONNECTED',
  CONNECTING = 'CONNECTING',
  CONNECTED = 'CONNECTED',
  DEGRADED = 'DEGRADED',
  RECONNECTING = 'RECONNECTING',
}

export enum DeltaErrorCategory {
  AUTHENTICATION = 'AUTHENTICATION',
  NETWORK = 'NETWORK',
  EXCHANGE = 'EXCHANGE',
  VALIDATION = 'VALIDATION',
  RATE_LIMIT = 'RATE_LIMIT',
  UNKNOWN = 'UNKNOWN',
}

export interface DeltaHealthDto {
  environment: DeltaEnvironment;
  connectionState: DeltaConnectionState;
  apiLatencyMs: number;
  wsLatencyMs: number;
  reconnectCount: number;
  rateLimitEvents: number;
  heartbeatAgeMs: number;
  isKillSwitchActive: boolean;
  timestamp: string;
}

export interface DeltaConfigDto {
  environment: DeltaEnvironment;
  apiKey?: string | undefined;
  apiSecret?: string | undefined;
  restUrl: string;
  wsUrl: string;
  maxRetries: number;
  isMockMode: boolean;
}

export interface DeltaSyncStatusDto {
  isSynchronized: boolean;
  lastSyncAt: string;
  ordersCount: number;
  positionsCount: number;
  balanceUsd: number;
  availableMarginUsd: number;
}

export interface DeltaStateReconciliationDto {
  matched: boolean;
  localOrdersCount: number;
  remoteOrdersCount: number;
  localPositionsCount: number;
  remotePositionsCount: number;
  mismatches: Array<{ id: string; type: string; details: string }>;
  timestamp: string;
}

export interface DeltaRecoveryTestDto {
  scenario: 'WS_DISCONNECT' | 'DUPLICATE_MESSAGE' | 'DELAYED_ACK';
  success: boolean;
  recoveryTimeMs: number;
  details: string;
  timestamp: string;
}

export interface IDeltaExecutionAdapter {
  connect(): Promise<boolean>;
  disconnect(): Promise<boolean>;
  health(): Promise<DeltaHealthDto>;
  submitOrder(input: SubmitExecutionInput): Promise<ExecutionResultDto>;
  modifyOrder(orderId: string, input: Partial<SubmitExecutionInput>): Promise<ExecutionResultDto>;
  cancelOrder(orderId: string): Promise<ExecutionResultDto>;
  closePosition(symbol: string): Promise<ExecutionResultDto>;
  getOrder(orderId: string): Promise<PaperOrderDto | null>;
  getPosition(symbol: string): Promise<PaperPositionDto | null>;
  sync(): Promise<boolean>;
}
