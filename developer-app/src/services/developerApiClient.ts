import axios from 'axios'

export const developerApiClient = axios.create({
  baseURL: '',
  withCredentials: true, // Automatically sends HttpOnly JWT cookies
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 15000,
})

// Response Interceptor for session recovery, RBAC 403 warnings, and server errors
developerApiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // 1. Session Expiry / 401 handling with single-retry flag
    if (error.response?.status === 401 && !originalRequest?._retry && !originalRequest?.url?.includes('/api/v1/auth/')) {
      originalRequest._retry = true
      try {
        await axios.post('/api/v1/auth/refresh', {}, { withCredentials: true })
        return developerApiClient(originalRequest)
      } catch (refreshError) {
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)
