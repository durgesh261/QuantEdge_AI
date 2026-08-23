import { create } from 'zustand'
import { User } from '../types/auth'
import { authService } from '../services/authService'

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  isDeveloperOrAdmin: boolean
  isLoading: boolean
  error: string | null
  login: (email: string, pass: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isDeveloperOrAdmin: false,
  isLoading: true,
  error: null,

  login: async (email, password) => {
    set({ isLoading: true, error: null })
    try {
      const res = await authService.login(email, password)
      const role = (res.user.role || '').toUpperCase()
      const isDevOrAdmin = role === 'DEVELOPER' || role === 'ADMIN'

      set({
        user: res.user,
        isAuthenticated: true,
        isDeveloperOrAdmin: isDevOrAdmin,
        isLoading: false,
        error: null,
      })
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Authentication failed'
      set({ error: msg, isLoading: false })
      throw new Error(msg)
    }
  },

  logout: async () => {
    set({ isLoading: true })
    try {
      await authService.logout()
    } catch (err) {
      console.warn('Logout request error', err)
    } finally {
      set({
        user: null,
        isAuthenticated: false,
        isDeveloperOrAdmin: false,
        isLoading: false,
        error: null,
      })
    }
  },

  checkAuth: async () => {
    set({ isLoading: true })
    try {
      const res = await authService.getMe()
      const role = (res.user.role || '').toUpperCase()
      const isDevOrAdmin = role === 'DEVELOPER' || role === 'ADMIN'

      set({
        user: res.user,
        isAuthenticated: true,
        isDeveloperOrAdmin: isDevOrAdmin,
        isLoading: false,
        error: null,
      })
    } catch (err) {
      set({
        user: null,
        isAuthenticated: false,
        isDeveloperOrAdmin: false,
        isLoading: false,
        error: null,
      })
    }
  },
}))
