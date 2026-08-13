export enum ExecutionMode {
  PAPER = 'PAPER',
  LIVE = 'LIVE',
  SHADOW = 'SHADOW',
}

export enum ExecutionStatus {
  QUEUED = 'QUEUED',
  VALIDATED = 'VALIDATED',
  SUBMITTED = 'SUBMITTED',
  PARTIALLY_FILLED = 'PARTIALLY_FILLED',
  FILLED = 'FILLED',
  CANCELLED = 'CANCELLED',
  REJECTED = 'REJECTED',
  FAILED = 'FAILED',
}

export interface ExecutionSessionDto {
  id: string;
  mode: ExecutionMode;
  pair: string;
  timeframe: '1H';
  decisionId: string;
  ruleVersion: string;
  configVersion: string;
  adapter: string;
  createdAt: string;
}

export interface ExecutionObservabilityDto {
  queueTimeMs: number;
  validationLatencyMs: number;
  adapterLatencyMs: number;
  totalLifecycleTimeMs: number;
}

export interface ExecutionRequestDto {
  id: string;
  sessionId: string;
  idempotencyKey: string;
  decisionId: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  mode: ExecutionMode;
  ruleVersion: string;
  configVersion: string;
  orderType: 'MARKET' | 'LIMIT';
  price?: number | undefined;
  stopPrice?: number | undefined;
  quantity: number;
  stopLoss?: number | undefined;
  takeProfit?: number | undefined;
  timestamp: string;
}

export interface ExecutionResultDto {
  id: string;
  requestId: string;
  sessionId: string;
  adapter: string;
  status: ExecutionStatus;
  fillPrice?: number | undefined;
  filledQuantity: number;
  observability: ExecutionObservabilityDto;
  message?: string | undefined;
  timestamp: string;
}

export interface ExecutionJournalDto {
  id: string;
  sessionId: string;
  requestId: string;
  resultId?: string | undefined;
  adapter: string;
  fromState: ExecutionStatus;
  toState: ExecutionStatus;
  action: string;
  details: string;
  latencyMs: number;
  timestamp: string;
}

export interface SubmitExecutionInput {
  decisionId: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  mode?: ExecutionMode | undefined;
  idempotencyKey?: string | undefined;
  quantity: number;
  price?: number | undefined;
  stopLoss?: number | undefined;
  takeProfit?: number | undefined;
}
