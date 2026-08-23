import React, { useEffect, useState, useCallback } from 'react'
import { developerService } from '../../services/developerService'
import { LogEntry } from '../../types/developer'
import { SkeletonCard } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  ScrollText,
  Filter,
  Search,
  RefreshCw,
  AlertCircle,
  Terminal,
  ShieldCheck,
} from 'lucide-react'

export const LogViewerPage: React.FC = () => {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [levelFilter, setLevelFilter] = useState<'ALL' | 'INFO' | 'WARN' | 'ERROR'>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)

  const fetchLogs = useCallback(async () => {
    try {
      setError(null)
      const data = await developerService.getSanitizedLogs()
      setLogs(data)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to fetch developer system logs')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchLogs()
    if (!autoRefresh) return
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [fetchLogs, autoRefresh])

  const filteredLogs = logs.filter((l) => {
    const matchesLevel = levelFilter === 'ALL' || l.level?.toUpperCase() === levelFilter
    const matchesSearch =
      !searchQuery ||
      l.message?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      l.source?.toLowerCase().includes(searchQuery.toLowerCase())
    return matchesLevel && matchesSearch
  })

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <ScrollText className="w-5 h-5 text-dev-accent" />
            <span>Sanitized System Audit & Operational Logs</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Live in-memory FIFO log stream with automatic credential, key & token redaction
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded bg-background border-terminal-border text-dev-accent focus:ring-0"
            />
            <span>Auto-Refresh (5s)</span>
          </label>

          <button
            onClick={fetchLogs}
            className="p-2 rounded bg-background border border-terminal-border hover:bg-background-elevated text-slate-300 hover:text-white transition-colors"
            title="Refresh Logs"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Safety Notice */}
      <div className="p-3 rounded-lg bg-dev-accent/10 border border-dev-accent/30 text-xs flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-dev-accent shrink-0" />
        <span className="text-slate-300 font-sans">
          <strong>REDACTION ENFORCED:</strong> Any detected bearer tokens, JWT secrets, passwords, or exchange keys are automatically masked server-side before delivery.
        </span>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/30 text-bearish text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Controls Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 rounded-lg bg-background-surface border border-terminal-border">
        {/* Level Filters */}
        <div className="flex items-center gap-1.5 text-xs">
          <Filter className="w-3.5 h-3.5 text-slate-500 mr-1" />
          {(['ALL', 'INFO', 'WARN', 'ERROR'] as const).map((lvl) => (
            <button
              key={lvl}
              onClick={() => setLevelFilter(lvl)}
              className={`px-2.5 py-1 rounded font-bold transition-all ${
                levelFilter === lvl
                  ? lvl === 'ERROR'
                    ? 'bg-bearish/20 text-bearish border border-bearish/40'
                    : lvl === 'WARN'
                    ? 'bg-warning/20 text-warning border border-warning/40'
                    : 'bg-dev-accent/20 text-dev-accent border border-dev-accent/40'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>

        {/* Search Bar */}
        <div className="relative max-w-xs w-full">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search log stream..."
            className="w-full bg-background border border-terminal-border rounded pl-8 pr-3 py-1 text-xs text-white placeholder:text-slate-600 focus:outline-none focus:border-dev-accent transition-colors"
          />
        </div>
      </div>

      {/* Log Console Window */}
      <div className="glass-panel rounded-lg border border-terminal-border overflow-hidden">
        <div className="p-3 bg-background/90 border-b border-terminal-border/80 flex items-center justify-between text-xs text-slate-400">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-dev-accent" />
            <span className="font-bold text-white uppercase tracking-wider">Log Output Buffer</span>
          </div>
          <span>Showing {filteredLogs.length} of {logs.length} events</span>
        </div>

        {isLoading && logs.length === 0 ? (
          <div className="p-6">
            <SkeletonCard rows={6} />
          </div>
        ) : filteredLogs.length > 0 ? (
          <div className="p-3 font-mono text-[11px] space-y-1.5 max-h-[550px] overflow-y-auto bg-background/50">
            {filteredLogs.map((log) => {
              const isErr = log.level?.toUpperCase() === 'ERROR'
              const isWarn = log.level?.toUpperCase() === 'WARN'

              return (
                <div
                  key={log.id}
                  className="flex items-start gap-3 p-1.5 rounded hover:bg-background-elevated/60 transition-colors border-b border-terminal-border/30 last:border-0"
                >
                  <span className="text-slate-500 shrink-0 text-[10px]">
                    {new Date(log.timestamp).toLocaleTimeString()}
                  </span>

                  <span
                    className={`px-1.5 py-0.2 rounded font-bold text-[9px] shrink-0 ${
                      isErr
                        ? 'bg-bearish/20 text-bearish'
                        : isWarn
                        ? 'bg-warning/20 text-warning'
                        : 'bg-dev-cyan/15 text-dev-cyan'
                    }`}
                  >
                    {log.level}
                  </span>

                  <span className="text-dev-purple font-semibold shrink-0">
                    [{log.source}]
                  </span>

                  <span className="text-slate-200 break-all">{log.message}</span>
                </div>
              )
            })}
          </div>
        ) : (
          <EmptyState
            icon={ScrollText}
            title="No Matching Logs"
            description="System log messages matching your filter criteria will appear here."
          />
        )}
      </div>
    </div>
  )
}
