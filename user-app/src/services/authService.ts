import { apiClient } from './apiClient'
import { AuthResponse } from '../types/auth'

export const authService = {
  async signup(name: string, email: string, password: string): Promise<AuthResponse> {
    const { data } = await apiClient.post<AuthResponse>('/api/v1/auth/signup', { name, email, password })
    return data
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    const { data } = await apiClient.post<AuthResponse>('/api/v1/auth/login', { email, password })
    return data
  },

  async logout(): Promise<void> {
    await apiClient.post('/api/v1/auth/logout')
  },

  async getMe(): Promise<AuthResponse> {
    const { data } = await apiClient.get<AuthResponse>('/api/v1/auth/me')
    return data
  },

  async refresh(): Promise<AuthResponse> {
    const { data } = await apiClient.post<AuthResponse>('/api/v1/auth/refresh')
    return data
  },
}
