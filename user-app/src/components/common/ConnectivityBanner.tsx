import React from 'react'
import { useConnectivity } from '../../hooks/useConnectivity'
import { WifiOff, ServerCrash, RefreshCw } from 'lucide-react'

export const ConnectivityBanner: React.FC = () => {
  const { isOnline, isBackendReachable, checkHealth } = useConnectivity()

  if (isOnline && isBackendReachable) {
    return null
  }

  const isNetworkOffline = !isOnline

  return (
    <div className="w-full bg-bearish/90 border-b border-bearish text-white px-4 py-2 text-xs font-mono flex items-center justify-between shadow-lg sticky top-0 z-40 backdrop-blur-md">
      <div className="flex items-center gap-2.5">
        {isNetworkOffline ? (
          <WifiOff className="w-4 h-4 text-white animate-pulse" />
        ) : (
          <ServerCrash className="w-4 h-4 text-warning animate-pulse" />
        )}
        <div className="font-semibold">
          {isNetworkOffline
            ? 'Network Disconnected: You are currently offline. Market updates are suspended.'
            : 'Backend Gateway Unreachable: Retrying connection to QuantEdge trading server...'}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="hidden sm:inline text-[11px] text-white/80">
          {isNetworkOffline ? 'Check your internet connection' : 'Polling automatic reconnect'}
        </span>
        <button
          onClick={() => checkHealth()}
          className="flex items-center gap-1 px-2.5 py-1 rounded bg-white/20 hover:bg-white/30 text-white text-[11px] font-bold transition-all border border-white/30"
        >
          <RefreshCw className="w-3 h-3" />
          <span>Retry Now</span>
        </button>
      </div>
    </div>
  )
}
