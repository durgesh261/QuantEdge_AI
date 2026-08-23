import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '',
  withCredentials: true, // Attach HttpOnly JWT cookies automatically
  headers: {
    'Content-Type': 'application/json',
  },
})

// Response interceptor to handle session expiration gracefully
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    if (error.response?.status === 401 && !originalRequest._retry && !originalRequest.url?.includes('/api/v1/auth/')) {
      originalRequest._retry = true
      try {
        await axios.post('/api/v1/auth/refresh', {}, { withCredentials: true })
        return apiClient(originalRequest)
      } catch (refreshError) {
        // Refresh failed, session expired
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)
