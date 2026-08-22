import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '@/services/api'

interface User {
  id: string
  email: string
  name: string
  role?: string
}

interface AuthState {
  user: User | null
  accessToken: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  signup: (name: string, email: string, password: string) => Promise<void>
  logout: () => void
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: true,

      login: async (email: string, password: string) => {
        const response = await api.post('/api/v1/auth/login', { email, password })
        const { user, accessToken } = response.data
        set({ user, accessToken, isAuthenticated: true })
        api.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`
      },

      signup: async (name: string, email: string, password: string) => {
        const response = await api.post('/api/v1/auth/signup', { name, email, password })
        const { user, accessToken } = response.data
        set({ user, accessToken, isAuthenticated: true })
        api.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`
      },

      logout: () => {
        set({ user: null, accessToken: null, isAuthenticated: false })
        delete api.defaults.headers.common['Authorization']
      },

      checkAuth: async () => {
        const accessToken = get().accessToken
        if (!accessToken) {
          set({ isLoading: false })
          return
        }
        try {
          api.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`
          const response = await api.get('/api/v1/auth/me')
          set({ user: response.data, isAuthenticated: true, isLoading: false })
        } catch {
          set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false })
          delete api.defaults.headers.common['Authorization']
        }
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        accessToken: state.accessToken,
      }),
    }
  )
)