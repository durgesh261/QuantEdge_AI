import { apiClient } from './apiClient'
import { DeltaSettingsDto, DeltaConnectRequest } from '../types/settings'
import { SystemDiagnosticsDto } from '../types/system'

export const settingsService = {
  async getDeltaSettings(): Promise<DeltaSettingsDto> {
    const { data } = await apiClient.get<DeltaSettingsDto>('/api/v1/settings/delta')
    return data
  },

  async connectDelta(request: DeltaConnectRequest): Promise<DeltaSettingsDto> {
    const { data } = await apiClient.post<DeltaSettingsDto>('/api/v1/settings/delta/connect', request)
    return data
  },

  async disconnectDelta(): Promise<DeltaSettingsDto> {
    const { data } = await apiClient.delete<DeltaSettingsDto>('/api/v1/settings/delta')
    return data
  },

  async testDeltaConnection(): Promise<DeltaSettingsDto> {
    const { data } = await apiClient.post<DeltaSettingsDto>('/api/v1/settings/delta/test', {})
    return data
  },

  async getSystemDiagnostics(): Promise<SystemDiagnosticsDto> {
    const { data } = await apiClient.get<SystemDiagnosticsDto>('/api/v1/system/diagnostics')
    return data
  },
}
