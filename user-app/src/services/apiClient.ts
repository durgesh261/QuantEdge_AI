import axios from 'axios'
import { toast } from '../stores/toastStore'

export const apiClient = axios.create({
  baseURL: '',
  withCredentials: true, // Attach HttpOnly JWT cookies automatically
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 15000,
})

// Response interceptor to handle session expiration and common HTTP errors gracefully
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // 1. Session Expiry / 401 Handling with automatic refresh attempt
    if (error.response?.status === 401 && !originalRequest?._retry && !originalRequest?.url?.includes('/api/v1/auth/')) {
      originalRequest._retry = true
      try {
        await axios.post('/api/v1/auth/refresh', {}, { withCredentials: true })
        return apiClient(originalRequest)
      } catch (refreshError) {
        toast.warning('Session Expired', 'Your session has ended. Please sign in to resume trading.')
        return Promise.reject(refreshError)
      }
    }

    // 2. Network Failure / Connection Refused
    if (!error.response) {
      if (error.code === 'ECONNABORTED') {
        toast.error('Request Timeout', 'The server took too long to respond. Please retry.')
      } else {
        toast.error('Network Error', 'Unable to reach the QuantEdge backend server.')
      }
      return Promise.reject(error)
    }

    const status = error.response.status
    const message = error.response.data?.message || error.response.data?.error || error.message

    // 3. Status-Specific Notifications (suppress on quiet background probes)
    const isQuietEndpoint = originalRequest?.url?.includes('/api/v1/market/ticker') || originalRequest?.url?.includes('/api/v1/auth/me')

    if (!isQuietEndpoint) {
      switch (status) {
        case 403:
          toast.error('Access Restricted', message || 'You do not have permission for this trading operation.')
          break
        case 404:
          // Optional: only log or show if not a standard missing resource check
          break
        case 409:
          toast.warning('Trade State Conflict', message || 'Action conflict: active trade lock or concurrent state change.')
          break
        case 429:
          toast.warning('Rate Limit Exceeded', 'Too many requests. Please slow down and wait a few moments.')
          break
        case 500:
        case 502:
        case 503:
        case 504:
          toast.error('Backend Server Error', message || 'Internal gateway error. Trading engine safety invariants remain active.')
          break
      }
    }

    return Promise.reject(error)
  }
)
