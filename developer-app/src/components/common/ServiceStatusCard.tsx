import React from 'react'
import { ServiceHealth } from '../../types/developer'
import { CheckCircle2, AlertTriangle, XCircle, Clock } from 'lucide-react'

export const ServiceStatusCard: React.FC<{ service: ServiceHealth }> = ({ service }) => {
  const isHealthy = service.status === 'HEALTHY'
  const isDegraded = service.status === 'DEGRADED'
  const isDown = service.status === 'DOWN'

  return (
    <div className="glass-panel p-4 rounded-lg border border-terminal-border flex flex-col justify-between space-y-3">
      {/* Header: Service Name & Status Badge */}
      <div className="flex items-center justify-between pb-2 border-b border-terminal-border/80">
        <span className="font-bold text-white text-sm font-mono">{service.serviceName}</span>
        <span
          className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold flex items-center gap-1 ${
            isHealthy
              ? 'bg-bullish/15 text-bullish border border-bullish/30'
              : isDegraded
              ? 'bg-warning/15 text-warning border border-warning/30'
              : 'bg-bearish/15 text-bearish border border-bearish/30'
          }`}
        >
          {isHealthy && <CheckCircle2 className="w-3 h-3" />}
          {isDegraded && <AlertTriangle className="w-3 h-3" />}
          {isDown && <XCircle className="w-3 h-3" />}
          <span>{service.status}</span>
        </span>
      </div>

      {/* Latency & Endpoint */}
      <div className="space-y-1 text-xs font-mono">
        <div className="flex items-center justify-between text-slate-400">
          <span>Latency:</span>
          <span className={`font-bold flex items-center gap-1 ${service.latencyMs > 150 ? 'text-warning' : 'text-dev-cyan'}`}>
            <Clock className="w-3 h-3" />
            {service.latencyMs}ms
          </span>
        </div>
        <div className="flex items-center justify-between text-slate-400">
          <span>Endpoint:</span>
          <span className="text-slate-300 truncate max-w-[160px]">{service.endpoint}</span>
        </div>
      </div>

      {/* Details Description */}
      <div className="p-2 rounded bg-background/60 border border-terminal-border text-[11px] font-mono text-slate-400 truncate">
        {service.details || 'Operating within normal parameters'}
      </div>
    </div>
  )
}
