import axios from 'axios'

const getBaseUrl = () => {
  try {
    return (import.meta as any).env?.VITE_API_URL || 'http://localhost:8080'
  } catch {
    return 'http://localhost:8080'
  }
}

export const api = axios.create({
  baseURL: getBaseUrl(),
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token expired or invalid
      // Auth store will handle logout
    }
    return Promise.reject(error)
  }
)