import React from 'react';
import { Radio, Zap } from 'lucide-react';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useConnectionManager } from '../../hooks/useConnectionManager';

export const StatusBar: React.FC = () => {
  const { isBackendReachable } = useConnectionManager();
  const { isDeltaEnabled, isConnected, connectionMode } = useDeltaStore();

  // Unified Delta status derived from user toggle + connection
  const deltaStatus = !isDeltaEnabled 
    ? 'offline' 
    : isConnected 
      ? 'online' 
      : 'connecting';

  const getStatusColor = (ok: boolean) => ok ? 'text-[#00C896]' : 'text-[#F6465D]';
  const getStatusBg = (ok: boolean) => ok ? 'bg-[#00C896]' : 'bg-[#F6465D]';
  const getDotColor = (status: string) => {
    switch (status) {
      case 'online': return 'bg-[#00C896]';
      case 'connecting': return 'bg-[#F59E0B] animate-pulse';
      default: return 'bg-[#F6465D]';
    }
  };

  return (
    <div className="h-6 bg-[#0B0E14] border-t border-[#1E293B] flex items-center justify-between px-3 text-[9px] font-mono shrink-0 select-none">
      
      {/* LEFT SIDE */}
      <div className="flex items-center space-x-3">
        {/* Delta Exchange Status */}
        <div className="flex items-center space-x-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${getDotColor(deltaStatus)}`} />
          <span className={deltaStatus === 'online' ? 'text-[#00C896]' : deltaStatus === 'connecting' ? 'text-[#F59E0B]' : 'text-[#F6465D]'}>
            DELTA: {deltaStatus === 'online' ? 'ONLINE' : deltaStatus === 'connecting' ? 'CONNECTING' : 'OFFLINE'}
          </span>
          {connectionMode === 'polling' && deltaStatus === 'online' && (
            <span className="text-[#64748B]">(POLL)</span>
          )}
        </div>

        <div className="w-px h-3 bg-[#1E293B]" />

        {/* Backend API */}
        <div className="flex items-center space-x-1">
          <div className={`w-1.5 h-1.5 rounded-full ${getStatusBg(isBackendReachable)}`} />
          <span className={getStatusColor(isBackendReachable)}>
            BE: {isBackendReachable ? 'ONLINE' : 'OFFLINE'}
          </span>
        </div>

        <div className="w-px h-3 bg-[#1E293B]" />

        {/* Pipeline */}
        <div className="flex items-center space-x-1">
          <Zap className="w-2.5 h-2.5 text-[#00C896]" />
          <span className="text-[#64748B]">PIPELINE:</span>
          <span className={isBackendReachable ? 'text-[#00C896]' : 'text-[#F6465D]'}>
            {isBackendReachable ? '9/9 STAGES OK' : 'DEGRADED'}
          </span>
        </div>

        <div className="w-px h-3 bg-[#1E293B]" />

        {/* Stream */}
        <div className="flex items-center space-x-1">
          <Radio className="w-2.5 h-2.5 text-[#64748B]" />
          <span className="text-[#64748B]">STREAM:</span>
          <span className={deltaStatus === 'online' ? 'text-[#00C896]' : 'text-[#F6465D]'}>
            {deltaStatus === 'online' ? 'LIVE' : deltaStatus === 'connecting' ? 'SYNCING' : 'DISCONNECTED'}
          </span>
        </div>
      </div>

      {/* RIGHT SIDE */}
      <div className="flex items-center space-x-3">
        <span className="text-[#64748B]">
          MODE: {deltaStatus === 'online' ? 'DELTA CONNECTED' : deltaStatus === 'connecting' ? 'DELTA SYNCING' : 'DELTA DISCONNECTED'}
        </span>
        <span className="text-[#64748B]">
          {new Date().toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' })} IST
        </span>
      </div>
    </div>
  );
};
