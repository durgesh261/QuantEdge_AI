import React from 'react';
import {
  WifiOff, AlertTriangle, RefreshCw,
  Server, Activity
} from 'lucide-react';
import { useConnectionManager } from '../../hooks/useConnectionManager';

export const ConnectionBanner: React.FC = () => {
  const {
    status,
    lastError,
    nextRetryIn,
    forceReconnect,
    isBackendReachable,
    isDeltaReachable,
    isOffline,
  } = useConnectionManager();

  if (status === 'connected') return null;

  const isRetrying = status === 'connecting' || (status === 'disconnected' && nextRetryIn > 0);

  return (
    <div className={`w-full shrink-0 border-b ${
      isOffline
        ? 'bg-[#F6465D]/10 border-[#F6465D]/30'
        : 'bg-[#F59E0B]/10 border-[#F59E0B]/30'
    }`}>
      <div className="px-4 py-2 flex items-center justify-between">
        <div className="flex items-center space-x-3 min-w-0">
          {/* Icon */}
          <div className={`shrink-0 w-6 h-6 rounded-full flex items-center justify-center ${
            isOffline ? 'bg-[#F6465D]/20' : 'bg-[#F59E0B]/20'
          }`}>
            {isOffline ? (
              <WifiOff className="w-3.5 h-3.5 text-[#F6465D]" />
            ) : (
              <AlertTriangle className="w-3.5 h-3.5 text-[#F59E0B]" />
            )}
          </div>

          {/* Text */}
          <div className="min-w-0">
            <div className="flex items-center space-x-2">
              <span className={`text-[11px] font-bold ${
                isOffline ? 'text-[#F6465D]' : 'text-[#F59E0B]'
              }`}>
                {isOffline ? 'Backend Offline' : 'Service Degraded'}
              </span>

              {/* Service pills */}
              <div className="hidden sm:flex items-center space-x-1">
                <span className={`flex items-center space-x-1 px-1.5 py-0.5 rounded text-[8px] font-bold border ${
                  isBackendReachable
                    ? 'bg-[#00C896]/10 text-[#00C896] border-[#00C896]/20'
                    : 'bg-[#F6465D]/10 text-[#F6465D] border-[#F6465D]/20'
                }`}>
                  <Server className="w-2.5 h-2.5" />
                  <span>API</span>
                </span>
                <span className={`flex items-center space-x-1 px-1.5 py-0.5 rounded text-[8px] font-bold border ${
                  isDeltaReachable
                    ? 'bg-[#00C896]/10 text-[#00C896] border-[#00C896]/20'
                    : 'bg-[#F6465D]/10 text-[#F6465D] border-[#F6465D]/20'
                }`}>
                  <Activity className="w-2.5 h-2.5" />
                  <span>DELTA</span>
                </span>
              </div>
            </div>

            <p className="text-[10px] text-[#94A3B8] truncate">
              {isRetrying && nextRetryIn > 0
                ? `Reconnecting in ${nextRetryIn}s...`
                : lastError || 'Connection lost. Retrying automatically...'
              }
            </p>
          </div>
        </div>

        {/* Retry button */}
        <div className="flex items-center space-x-2 shrink-0 ml-3">
          {isRetrying && nextRetryIn > 0 && (
            <span className="hidden sm:inline text-[10px] text-[#64748B] font-mono">
              {nextRetryIn}s
            </span>
          )}

          <button
            onClick={forceReconnect}
            disabled={isRetrying && nextRetryIn > 0}
            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-bold border transition-all disabled:opacity-50 ${
              isOffline
                ? 'bg-[#F6465D]/20 text-[#F6465D] border-[#F6465D]/30 hover:bg-[#F6465D]/30'
                : 'bg-[#F59E0B]/20 text-[#F59E0B] border-[#F59E0B]/30 hover:bg-[#F59E0B]/30'
            }`}
          >
            <RefreshCw className={`w-3 h-3 ${isRetrying ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">
              {isRetrying ? 'Retrying' : 'Retry Now'}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
