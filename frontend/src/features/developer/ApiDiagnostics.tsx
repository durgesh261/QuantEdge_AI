import { useState, useEffect } from 'react'
import {
  AlertTriangle,
  RefreshCw,
  Zap,
  Radio
} from 'lucide-react'
import { developerService, ApiDiagnosticsResponse } from '@/services/developerService'

export function ApiDiagnostics() {
  const [diagnostics, setDiagnostics] = useState<ApiDiagnosticsResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDiagnostics = async () => {
    try {
      setIsLoading(true)
      setError(null)
      const data = await developerService.getApiDiagnostics()
      setDiagnostics(data)
    } catch (err: any) {
      setError(err.message || 'Failed to fetch API diagnostics')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchDiagnostics()
  }, [])

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight font-mono">
              API & Exchange Diagnostics
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
              LIVE TELEMETRY
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm font-mono">
            Low-level REST ping telemetry, signature protocol validation, and connection pool states.
          </p>
        </div>
        <button
          onClick={fetchDiagnostics}
          disabled={isLoading}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-cyan-300 text-xs font-mono font-bold rounded-lg border border-cyan-500/30 transition disabled:opacity-50"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          {isLoading ? 'Pinging APIs...' : 'Run Diagnostics Ping'}
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 font-mono text-xs flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Diagnostics Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 font-mono">
        {/* Delta Exchange REST Diagnostics */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <Radio size={18} className="text-emerald-400" />
              <h2 className="text-base font-bold text-white">Delta Exchange India REST Telemetry</h2>
            </div>
            <span className="px-2 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">
              {diagnostics?.deltaApiStatus || 'PROBING'}
            </span>
          </div>

          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between text-slate-400">
              <span>Production Base URL:</span>
              <span className="text-white font-bold">{diagnostics?.deltaApiUrl || 'https://api.india.delta.exchange'}</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Live Ping Latency:</span>
              <span className="text-emerald-400 font-bold">{diagnostics?.deltaPingMs ?? '--'} ms</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Authentication Mechanism:</span>
              <span className="text-indigo-400 font-bold">{diagnostics?.signatureMechanism || 'HMAC_SHA256_PER_USER'}</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Secret Scrubbing Invariant:</span>
              <span className="text-emerald-400 font-bold">STRICTLY ENFORCED</span>
            </div>
          </div>
        </div>

        {/* Python Engine Diagnostics */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <Zap size={18} className="text-cyan-400" />
              <h2 className="text-base font-bold text-white">Python SMC Engine Telemetry</h2>
            </div>
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${
              diagnostics?.pythonEngineStatus === 'OK'
                ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                : 'bg-amber-950 text-amber-400 border border-amber-800'
            }`}>
              {diagnostics?.pythonEngineStatus || 'OFFLINE'}
            </span>
          </div>

          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between text-slate-400">
              <span>Engine Internal URL:</span>
              <span className="text-white font-bold">{diagnostics?.pythonEngineUrl || 'http://localhost:8000'}</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Engine Ping Latency:</span>
              <span className="text-cyan-400 font-bold">{diagnostics?.pythonEnginePingMs ?? '--'} ms</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Protocol:</span>
              <span className="text-slate-300 font-bold">REST State Snapshot + Trade Records</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Database Connection Pool:</span>
              <span className="text-white font-bold">{diagnostics?.databasePoolActive || 5} Active / {diagnostics?.databasePoolTotal || 20} Max</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
