import { useState, useEffect } from 'react'
import {
  Search,
  RefreshCw,
  AlertTriangle,
  ShieldCheck,
  Filter
} from 'lucide-react'
import { developerService, LogEntry } from '@/services/developerService'

export function LogsViewer() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [filterLevel, setFilterLevel] = useState<string>('ALL')
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchLogs = async () => {
    try {
      setIsLoading(true)
      setError(null)
      const data = await developerService.getSanitizedLogs()
      setLogs(data)
    } catch (err: any) {
      setError(err.message || 'Failed to fetch logs')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchLogs()
  }, [])

  const filteredLogs = logs.filter(log => {
    const matchesLevel = filterLevel === 'ALL' || log.level === filterLevel
    const matchesSearch =
      searchQuery === '' ||
      log.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
      log.source.toLowerCase().includes(searchQuery.toLowerCase())
    return matchesLevel && matchesSearch
  })

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight font-mono">
              Sanitized Audit & System Logs
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-amber-500/10 border border-amber-500/30 text-amber-400">
              SCRUBBED STREAM
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm font-mono">
            Real-time server log stream. Sensitive API secrets, JWT tokens, and encryption keys are strictly scrubbed.
          </p>
        </div>
        <button
          onClick={fetchLogs}
          disabled={isLoading}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-amber-300 text-xs font-mono font-bold rounded-lg border border-amber-500/30 transition disabled:opacity-50"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          {isLoading ? 'Loading Logs...' : 'Refresh Stream'}
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 font-mono text-xs flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Controls Bar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row items-center justify-between gap-4 font-mono text-xs">
        <div className="flex items-center gap-3 w-full sm:w-auto">
          <div className="relative w-full sm:w-64">
            <Search size={14} className="absolute left-3 top-2.5 text-slate-500" />
            <input
              type="text"
              placeholder="Search logs..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-2 text-white focus:outline-none focus:border-amber-500"
            />
          </div>

          <div className="flex items-center gap-1.5">
            <Filter size={14} className="text-slate-400" />
            <select
              value={filterLevel}
              onChange={e => setFilterLevel(e.target.value)}
              className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-amber-500"
            >
              <option value="ALL">All Levels</option>
              <option value="INFO">INFO</option>
              <option value="WARN">WARN</option>
              <option value="ERROR">ERROR</option>
              <option value="DEBUG">DEBUG</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-1.5 text-slate-400 text-[11px]">
          <ShieldCheck size={14} className="text-emerald-400" />
          <span>Regex secret redaction active (AES keys, API secrets, tokens)</span>
        </div>
      </div>

      {/* Logs Table / Console */}
      <div className="bg-slate-950 border border-slate-800 rounded-2xl p-4 shadow-xl font-mono text-xs space-y-2 max-h-[600px] overflow-y-auto">
        {filteredLogs.length === 0 ? (
          <div className="py-12 text-center text-slate-500">
            No log entries matching the current filter criteria.
          </div>
        ) : (
          filteredLogs.map(log => (
            <div
              key={log.id}
              className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 hover:border-slate-700 flex flex-col sm:flex-row sm:items-center justify-between gap-2"
            >
              <div className="flex items-start sm:items-center gap-3">
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    log.level === 'INFO'
                      ? 'bg-blue-950 text-blue-400 border border-blue-800'
                      : log.level === 'WARN'
                      ? 'bg-amber-950 text-amber-400 border border-amber-800'
                      : log.level === 'ERROR'
                      ? 'bg-red-950 text-red-400 border border-red-800'
                      : 'bg-slate-800 text-slate-400 border border-slate-700'
                  }`}
                >
                  {log.level}
                </span>
                <span className="text-amber-400 font-bold">[{log.source}]</span>
                <span className="text-slate-200">{log.message}</span>
              </div>
              <span className="text-slate-500 text-[11px] shrink-0">
                {new Date(log.timestamp).toLocaleTimeString()}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
