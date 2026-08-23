export interface AccountStatusResponse {
  accountId: string | null
  name: string | null
  connected: boolean
  connectionStatus: string
  wsStatus: string
  streamHealth: string
  maskedApiKey: string | null
  environment: string | null
  lastConnectedAt: string | null
  lastSyncedAt: string | null
  lastEventAt: string | null
  reconnectCount: number
  algoEnabled: boolean
  killSwitchActive: boolean
  lastError: string | null
}
