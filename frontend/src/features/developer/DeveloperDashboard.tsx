import { useEffect, useState } from 'react'
import {
  Cpu,
  Server,
  Activity,
  AlertTriangle,
  RefreshCw,
  MemoryStick,
  ShieldCheck
} from 'lucide-react'
import { developerService, DeveloperStatusResponse } from '@/services/developerService'

export function DeveloperDashboard() {
  const [status, setStatus] = useState<DeveloperStatusResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = async () => {
    try {
      setIsLoading(true)
      setError(null)
      const data = await developerService.getSystemStatus()
      setStatus(data)
    } catch (err: any) {
      setError(err.response?.data?.message || err.message || 'Failed to fetch developer status')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight font-mono">
              Developer System Overview
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-amber-500/10 border border-amber-500/30 text-amber-400">
              OPERATIONAL
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm font-mono">
            Low-level diagnostic metrics, service topologies, and execution health.
          </p>
        </div>
        <button
          onClick={fetchStatus}
          disabled={isLoading}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-amber-300 text-xs font-mono font-bold rounded-lg border border-amber-500/30 transition disabled:opacity-50"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          {isLoading ? 'Refreshing...' : 'Refresh Metrics'}
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 font-mono text-xs flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider font-mono">Uptime</p>
              <p className="text-xl font-bold font-mono text-white mt-1">
                {status ? `${Math.floor(status.uptimeSeconds / 3600)}h ${Math.floor((status.uptimeSeconds % 3600) / 60)}m` : '--'}
              </p>
              <p className="text-[11px] text-slate-500 font-mono mt-1">JVM Process Lifecycle</p>
            </div>
            <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400">
              <Cpu size={22} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider font-mono">Heap Memory</p>
              <p className="text-xl font-bold font-mono text-emerald-400 mt-1">
                {status?.memory ? `${status.memory.usedHeapMb} MB / ${status.memory.maxHeapMb} MB` : '--'}
              </p>
              <p className="text-[11px] text-slate-500 font-mono mt-1">
                {status?.memory ? `${status.memory.heapUsagePercent}% Allocated` : '--'}
              </p>
            </div>
            <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400">
              <MemoryStick size={22} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider font-mono">Active Threads</p>
              <p className="text-xl font-bold font-mono text-cyan-400 mt-1">
                {status?.threads ? status.threads.activeThreadCount : '--'}
              </p>
              <p className="text-[11px] text-slate-500 font-mono mt-1">
                Peak: {status?.threads ? status.threads.peakThreadCount : '--'} Threads
              </p>
            </div>
            <div className="p-3 bg-cyan-500/10 border border-cyan-500/20 rounded-xl text-cyan-400">
              <Activity size={22} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider font-mono">Live Gate Status</p>
              <p className="text-lg font-bold font-mono text-amber-400 mt-1">Isolated</p>
              <p className="text-[11px] text-slate-500 font-mono mt-1">Real Orders Decoupled</p>
            </div>
            <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400">
              <ShieldCheck size={22} />
            </div>
          </div>
        </div>
      </div>

      {/* Services Health Grid */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-amber-400" />
            <h2 className="text-lg font-bold text-white font-mono">Service Topologies & Latency</h2>
          </div>
          <span className="text-xs font-mono text-slate-400">Auto-Ping Enabled</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {status?.services.map((srv, idx) => (
            <div key={idx} className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-3 font-mono">
              <div className="flex items-center justify-between">
                <span className="text-sm font-bold text-white">{srv.serviceName}</span>
                <span
                  className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                    srv.status === 'HEALTHY' || srv.status === 'REACHABLE'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                      : srv.status === 'DEGRADED'
                      ? 'bg-amber-950 text-amber-400 border border-amber-800'
                      : 'bg-red-950 text-red-400 border border-red-800'
                  }`}
                >
                  {srv.status}
                </span>
              </div>
              <p className="text-xs text-slate-400 truncate">{srv.endpoint}</p>
              <div className="flex items-center justify-between text-xs pt-2 border-t border-slate-800/80 text-slate-500">
                <span>Latency:</span>
                <span className="text-emerald-400 font-bold">{srv.latencyMs} ms</span>
              </div>
              <p className="text-[11px] text-slate-500">{srv.details}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
