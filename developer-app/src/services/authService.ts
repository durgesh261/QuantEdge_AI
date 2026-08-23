import { developerApiClient } from './developerApiClient'
import { AuthResponse } from '../types/auth'

export const authService = {
  async login(email: string, password: string): Promise<AuthResponse> {
    const { data } = await developerApiClient.post<AuthResponse>('/api/v1/auth/login', { email, password })
    return data
  },

  async logout(): Promise<void> {
    await developerApiClient.post('/api/v1/auth/logout')
  },

  async getMe(): Promise<AuthResponse> {
    const { data } = await developerApiClient.get<AuthResponse>('/api/v1/auth/me')
    return data
  },

  async refresh(): Promise<AuthResponse> {
    const { data } = await developerApiClient.post<AuthResponse>('/api/v1/auth/refresh')
    return data
  },
}
