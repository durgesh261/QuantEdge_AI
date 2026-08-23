import { developerApiClient } from './developerApiClient'
import {
  DeveloperStatusResponse,
  ApiDiagnosticsResponse,
  LogEntry,
  SandboxInfoResponse,
  SimulatedTickResult,
  AccountHealthSummary,
} from '../types/developer'

export const developerService = {
  async getSystemStatus(): Promise<DeveloperStatusResponse> {
    const { data } = await developerApiClient.get<DeveloperStatusResponse>('/api/v1/developer/status')
    return data
  },

  async getApiDiagnostics(): Promise<ApiDiagnosticsResponse> {
    const { data } = await developerApiClient.get<ApiDiagnosticsResponse>('/api/v1/developer/diagnostics')
    return data
  },

  async getSanitizedLogs(): Promise<LogEntry[]> {
    const { data } = await developerApiClient.get<LogEntry[]>('/api/v1/developer/logs')
    return data
  },

  async getSandboxInfo(): Promise<SandboxInfoResponse> {
    const { data } = await developerApiClient.get<SandboxInfoResponse>('/api/v1/developer/sandbox/info')
    return data
  },

  async simulateTick(symbol = 'BTCUSD', price = 65000): Promise<SimulatedTickResult> {
    const { data } = await developerApiClient.post<SimulatedTickResult>('/api/v1/developer/sandbox/simulate-tick', {
      symbol,
      price,
    })
    return data
  },

  async getAccountsHealthSummary(): Promise<AccountHealthSummary[]> {
    const { data } = await developerApiClient.get<AccountHealthSummary[]>('/api/v1/developer/system/accounts')
    return data
  },
}
