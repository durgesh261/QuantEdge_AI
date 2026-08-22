import { api } from './api'

export interface ServiceHealth {
  serviceName: string
  status: 'HEALTHY' | 'DEGRADED' | 'DOWN' | 'OFFLINE' | 'REACHABLE' | 'UNREACHABLE'
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
  status: string
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
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG'
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
  detectedOrderBlockType: string
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
  lastSyncedAt?: string
}

export const developerService = {
  async getSystemStatus(): Promise<DeveloperStatusResponse> {
    const response = await api.get<DeveloperStatusResponse>('/api/v1/developer/status')
    return response.data
  },

  async getApiDiagnostics(): Promise<ApiDiagnosticsResponse> {
    const response = await api.get<ApiDiagnosticsResponse>('/api/v1/developer/diagnostics')
    return response.data
  },

  async getSanitizedLogs(): Promise<LogEntry[]> {
    const response = await api.get<LogEntry[]>('/api/v1/developer/logs')
    return response.data
  },

  async getSandboxInfo(): Promise<SandboxInfoResponse> {
    const response = await api.get<SandboxInfoResponse>('/api/v1/developer/sandbox/info')
    return response.data
  },

  async simulateTick(symbol?: string, price?: number): Promise<SimulatedTickResult> {
    const response = await api.post<SimulatedTickResult>('/api/v1/developer/sandbox/simulate-tick', {
      symbol,
      price,
    })
    return response.data
  },

  async getAccountsHealthSummary(): Promise<AccountHealthSummary[]> {
    const response = await api.get<AccountHealthSummary[]>('/api/v1/developer/system/accounts')
    return response.data
  },
}
