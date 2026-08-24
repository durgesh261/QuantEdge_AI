export interface DeltaSettingsDto {
  connected: boolean
  status: 'CONNECTED' | 'DISCONNECTED' | 'ERROR' | string
  apiKeyMasked: string | null
  accountId: string | null
  accountName: string | null
  environment: string | null
  lastVerifiedAt: string | null
  lastError: string | null
}

export interface DeltaConnectRequest {
  apiKey: string
  apiSecret: string
}
