export interface ServiceHealth {
  serviceName: string
  status: 'HEALTHY' | 'DEGRADED' | 'DOWN' | string
  endpoint: string
  latencyMs: number
  details: string
}

export interface MemoryMetrics {
  usedHeapMb: number
  maxHeapMb: number
  heapUsagePercent: number
  usedNonHeapMb: number
}

export interface ThreadMetrics {
  activeThreadCount: number
  peakThreadCount: number
  totalStartedThreadCount: number
}

export interface DeveloperStatusResponse {
  status: 'HEALTHY' | 'DEGRADED' | 'DOWN' | string
  timestamp: string
  uptimeSeconds: number
  services: ServiceHealth[]
  memory: MemoryMetrics
  threads: ThreadMetrics
}

export interface ApiDiagnosticsResponse {
  deltaApiUrl: string
  deltaApiStatus: string
  deltaPingMs: number
  pythonEngineUrl: string
  pythonEngineStatus: string
  pythonEnginePingMs: number
  databasePoolActive: number
  databasePoolTotal: number
  signatureMechanism: string
  secretsSanitized: boolean
}

export interface LogEntry {
  id: string
  timestamp: string
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG' | string
  source: string
  message: string
}

export interface SandboxInfoResponse {
  mode: string
  realExecutionBlocked: boolean
  simulatedBalance: number
  activeStrategyModel: string
  simulatedTicksCount: number
  lastSimulatedTickAt: string
  safetyNotice: string
}

export interface SimulatedTickResult {
  success: boolean
  symbol: string
  price: number
  detectedOrderBlockType: 'BULLISH_OB' | 'BEARISH_OB' | 'NONE' | string
  orderBlockHigh: number
  orderBlockLow: number
  signal: string
  timestamp: string
}

export interface AccountHealthSummary {
  accountId: string
  name: string
  environment: string
  isActive: boolean
  algoEnabled: boolean
  killSwitchActive: boolean
  currentBalance: number
  totalEquity: number
  lastSyncedAt: string
}
