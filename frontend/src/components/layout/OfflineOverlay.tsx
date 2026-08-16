import React, { useState } from 'react';
import { WifiOff, Server, RefreshCw, X } from 'lucide-react';
import { useConnectionManager } from '../../hooks/useConnectionManager';

export const OfflineOverlay: React.FC = () => {
  const { status, forceReconnect, nextRetryIn, retryCount } = useConnectionManager();
  const [dismissed, setDismissed] = useState(false);

  // Allow user to dismiss and keep using the app
  if (status !== 'disconnected' || dismissed) return null;

  return (
    <div className="fixed inset-0 z-[90] bg-[#0B0E14]/90 backdrop-blur-sm flex items-center justify-center">
      <div className="relative max-w-sm w-full mx-4 text-center">
        
        {/* Dismiss button */}
        <button
          onClick={() => setDismissed(true)}
          className="absolute -top-2 -right-2 w-7 h-7 rounded-full bg-[#1E293B] border border-[#334155] flex items-center justify-center text-[#64748B] hover:text-white transition-colors"
          title="Continue in offline mode"
        >
          <X className="w-3.5 h-3.5" />
        </button>

        {/* Icon */}
        <div className="relative w-16 h-16 mx-auto mb-6">
          <div className="absolute inset-0 rounded-full bg-[#F6465D]/20 animate-ping" />
          <div className="relative w-16 h-16 rounded-full bg-[#F6465D]/10 border border-[#F6465D]/30 flex items-center justify-center">
            <WifiOff className="w-8 h-8 text-[#F6465D]" />
          </div>
        </div>

        <h2 className="text-lg font-bold text-[#F8FAFC] mb-2">Backend Disconnected</h2>
        <p className="text-[11px] text-[#94A3B8] mb-6 leading-relaxed">
          QuantEdge AI cannot reach the backend API. Live data, trading, and AI signals are unavailable.
        </p>

        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 mb-6 text-left space-y-2">
          <div className="flex items-center space-x-2 text-[10px] text-[#94A3B8]">
            <Server className="w-3.5 h-3.5 text-[#64748B]" />
            <span>Expected: <code className="text-[#F8FAFC] bg-[#0B0E14] px-1 py-0.5 rounded">/api/v1</code></span>
          </div>
          <div className="flex items-center space-x-2 text-[10px] text-[#94A3B8]">
            <WifiOff className="w-3.5 h-3.5 text-[#64748B]" />
            <span>Status: <span className="text-[#F6465D] font-bold">Connection Refused</span></span>
          </div>
          <div className="flex items-center space-x-2 text-[10px] text-[#94A3B8]">
            <RefreshCw className="w-3.5 h-3.5 text-[#64748B]" />
            <span>
              Auto-retry: {nextRetryIn > 0 ? <span className="text-[#F59E0B] font-mono">{nextRetryIn}s</span> : 'now'}
              {' '}(attempt {retryCount})
            </span>
          </div>
        </div>

        <button
          onClick={forceReconnect}
          className="w-full py-2.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white rounded-xl font-bold text-[11px] uppercase tracking-wider transition-colors flex items-center justify-center space-x-2"
        >
          <RefreshCw className="w-4 h-4" />
          <span>Reconnect Now</span>
        </button>

        <button
          onClick={() => setDismissed(true)}
          className="mt-3 text-[10px] text-[#64748B] hover:text-[#94A3B8] transition-colors"
        >
          Continue in offline mode →
        </button>
      </div>
    </div>
  );
};
