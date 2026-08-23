import { create } from 'zustand'
import { User } from '../types/auth'
import { authService } from '../services/authService'

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  login: (email: string, pass: string) => Promise<void>
  signup: (name: string, email: string, pass: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  error: null,

  login: async (email, password) => {
    set({ isLoading: true, error: null })
    try {
      const res = await authService.login(email, password)
      set({ user: res.user, isAuthenticated: true, isLoading: false, error: null })
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Login failed. Please check your credentials.'
      set({ error: msg, isLoading: false })
      throw new Error(msg)
    }
  },

  signup: async (name, email, password) => {
    set({ isLoading: true, error: null })
    try {
      const res = await authService.signup(name, email, password)
      set({ user: res.user, isAuthenticated: true, isLoading: false, error: null })
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Registration failed. Please try again.'
      set({ error: msg, isLoading: false })
      throw new Error(msg)
    }
  },

  logout: async () => {
    set({ isLoading: true })
    try {
      await authService.logout()
    } catch (err) {
      console.warn('Logout request completed with error', err)
    } finally {
      set({ user: null, isAuthenticated: false, isLoading: false, error: null })
    }
  },

  checkAuth: async () => {
    set({ isLoading: true })
    try {
      const res = await authService.getMe()
      set({ user: res.user, isAuthenticated: true, isLoading: false, error: null })
    } catch (err) {
      set({ user: null, isAuthenticated: false, isLoading: false, error: null })
    }
  },
}))
