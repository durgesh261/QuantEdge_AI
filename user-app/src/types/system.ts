export interface ComponentHealth {
  name: string
  status: 'ONLINE' | 'DEGRADED' | 'OFFLINE' | 'NOT_CONFIGURED'
  latencyMs: number
  details: string
  lastCheckedAt: string
}

export interface SystemDiagnosticsDto {
  overallStatus: 'HEALTHY' | 'DEGRADED' | 'OFFLINE'
  buildVersion: string
  uptimeSeconds: number
  timestamp: string
  api: ComponentHealth
  database: ComponentHealth
  deltaExchange: ComponentHealth
  pythonEngine: ComponentHealth
  aiEngine: ComponentHealth
  newsService: ComponentHealth
  macroCalendar: ComponentHealth
  totalAccounts: number
  totalAiEnrichments: number
}
