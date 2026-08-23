import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

export interface ConnectivityState {
  isOnline: boolean
  isBackendReachable: boolean
  latencyMs: number | null
  lastChecked: Date | null
  checkHealth: () => Promise<void>
}

export function useConnectivity(): ConnectivityState {
  const [isOnline, setIsOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true
  )
  const [isBackendReachable, setIsBackendReachable] = useState<boolean>(true)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)

  const checkHealth = useCallback(async () => {
    if (!navigator.onLine) {
      setIsOnline(false)
      setIsBackendReachable(false)
      setLatencyMs(null)
      setLastChecked(new Date())
      return
    }

    setIsOnline(true)
    const startTime = performance.now()

    try {
      // Lightweight health probe using /api/v1/auth/me or /api/v1/market/status
      await axios.get('/api/v1/auth/me', {
        timeout: 4000,
        withCredentials: true,
      })
      const latency = Math.round(performance.now() - startTime)
      setIsBackendReachable(true)
      setLatencyMs(latency)
      setLastChecked(new Date())
    } catch (err: any) {
      const isExpectedAuthStatus = err.response?.status === 401 || err.response?.status === 403 || err.response?.status === 200
      if (isExpectedAuthStatus) {
        // Backend answered with valid HTTP status
        const latency = Math.round(performance.now() - startTime)
        setIsBackendReachable(true)
        setLatencyMs(latency)
      } else {
        // Network timeout / ERR_CONNECTION_REFUSED
        setIsBackendReachable(false)
        setLatencyMs(null)
      }
      setLastChecked(new Date())
    }
  }, [])

  useEffect(() => {
    const handleOnline = () => {
      setIsOnline(true)
      checkHealth()
    }
    const handleOffline = () => {
      setIsOnline(false)
      setIsBackendReachable(false)
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    // Initial check
    checkHealth()

    // Periodic heartbeat every 20s
    const interval = setInterval(checkHealth, 20000)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
      clearInterval(interval)
    }
  }, [checkHealth])

  return {
    isOnline,
    isBackendReachable,
    latencyMs,
    lastChecked,
    checkHealth,
  }
}
