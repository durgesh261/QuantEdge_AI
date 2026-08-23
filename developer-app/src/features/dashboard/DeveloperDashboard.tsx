import React, { useEffect, useState, useCallback } from 'react'
import { developerService } from '../../services/developerService'
import { DeveloperStatusResponse, AccountHealthSummary } from '../../types/developer'
import { ServiceStatusCard } from '../../components/common/ServiceStatusCard'
import { MetricCard } from '../../components/common/MetricCard'
import { SkeletonStat, SkeletonCard } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  Activity,
  Server,
  Cpu,
  Layers,
  RefreshCw,
  AlertCircle,
  Users,
  ShieldCheck,
  Zap,
} from 'lucide-react'

export const DeveloperDashboard: React.FC = () => {
  const [status, setStatus] = useState<DeveloperStatusResponse | null>(null)
  const [accounts, setAccounts] = useState<AccountHealthSummary[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date>(new Date())

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [statusRes, accountsRes] = await Promise.all([
        developerService.getSystemStatus(),
        developerService.getAccountsHealthSummary().catch(() => []),
      ])
      setStatus(statusRes)
      setAccounts(accountsRes)
      setLastRefreshed(new Date())
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to connect to developer diagnostics service')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 8000)
    return () => clearInterval(interval)
  }, [fetchData])

  const formatUptime = (seconds: number) => {
    const d = Math.floor(seconds / 86400)
    const h = Math.floor((seconds % 86400) / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    return `${d > 0 ? d + 'd ' : ''}${h}h ${m}m ${s}s`
  }

  if (isLoading && !status) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
        </div>
        <SkeletonCard rows={4} />
      </div>
    )
  }

  return (
    <div className="space-y-6 font-mono">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <Server className="w-5 h-5 text-dev-accent" />
            <span>Developer & Operations Mission Control</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Real-time infrastructure health, JVM runtime metrics, multi-tenant state & service latencies
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-[11px] text-slate-500">
            Updated: {lastRefreshed.toLocaleTimeString()}
          </span>
          <button
            onClick={fetchData}
            className="p-2 rounded bg-background border border-terminal-border hover:bg-background-elevated text-slate-300 hover:text-white transition-colors"
            title="Refresh Diagnostics"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/30 text-bearish text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* KPI Overview Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="SYSTEM HEALTH"
          value={status?.status || 'HEALTHY'}
          subtext="Spring Boot Core & Gateways"
          icon={ShieldCheck}
          variant={status?.status === 'HEALTHY' ? 'emerald' : 'amber'}
        />
        <MetricCard
          title="SERVER UPTIME"
          value={status?.uptimeSeconds ? formatUptime(status.uptimeSeconds) : '—'}
          subtext="Continuous Execution Loop"
          icon={Zap}
          variant="cyan"
        />
        <MetricCard
          title="JVM HEAP MEMORY"
          value={`${status?.memory?.usedHeapMb ?? 0} MB / ${status?.memory?.maxHeapMb ?? 0} MB`}
          subtext={`Usage: ${(status?.memory?.heapUsagePercent ?? 0).toFixed(1)}%`}
          icon={Cpu}
          variant="purple"
        />
        <MetricCard
          title="ACTIVE THREADS"
          value={status?.threads?.activeThreadCount ?? 0}
          subtext={`Peak: ${status?.threads?.peakThreadCount ?? 0} | Total: ${status?.threads?.totalStartedThreadCount ?? 0}`}
          icon={Activity}
          variant="blue"
        />
      </div>

      {/* Subsystem Health Cards Grid */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Layers className="w-4 h-4 text-dev-cyan" />
            <span>Core Subsystem Status & Ping Latencies</span>
          </h2>
          <span className="text-[11px] text-slate-500 font-sans">Automated probe every 8s</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {status?.services?.map((service, idx) => (
            <ServiceStatusCard key={idx} service={service} />
          ))}
        </div>
      </div>

      {/* Accounts & Tenants Health Summary */}
      <div className="glass-panel rounded-lg border border-terminal-border overflow-hidden">
        <div className="p-4 border-b border-terminal-border/80 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="w-4 h-4 text-dev-accent" />
            <h3 className="font-bold text-white text-xs uppercase tracking-wider">
              Connected Trading Accounts & Execution Tenancy
            </h3>
          </div>
          <span className="text-[11px] text-slate-400">
            {accounts.length} Active Accounts
          </span>
        </div>

        <div className="overflow-x-auto p-2">
          {accounts.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2.5 px-3">Account ID</th>
                  <th className="py-2.5 px-3">Name</th>
                  <th className="py-2.5 px-3">Environment</th>
                  <th className="py-2.5 px-3">Balance</th>
                  <th className="py-2.5 px-3">Algo Engine</th>
                  <th className="py-2.5 px-3">Kill Switch</th>
                  <th className="py-2.5 px-3">Last Sync</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {accounts.map((acc) => (
                  <tr key={acc.accountId} className="hover:bg-background-elevated/40 transition-colors">
                    <td className="py-2.5 px-3 font-bold text-dev-cyan">{acc.accountId}</td>
                    <td className="py-2.5 px-3 text-white font-semibold">{acc.name}</td>
                    <td className="py-2.5 px-3">
                      <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300">
                        {acc.environment}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-bold text-white">${acc.currentBalance?.toFixed(2)}</td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          acc.algoEnabled
                            ? 'bg-bullish/15 text-bullish border border-bullish/30'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {acc.algoEnabled ? 'ENABLED' : 'PAUSED'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          acc.killSwitchActive
                            ? 'bg-bearish/15 text-bearish border border-bearish/30'
                            : 'bg-bullish/15 text-bullish'
                        }`}
                      >
                        {acc.killSwitchActive ? 'ENGAGED' : 'NORMAL'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-slate-400 text-[11px]">
                      {acc.lastSyncedAt ? new Date(acc.lastSyncedAt).toLocaleTimeString() : 'Live'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              icon={Users}
              title="No Accounts Discovered"
              description="Trading accounts configured in PostgreSQL will appear here with live execution metrics."
            />
          )}
        </div>
      </div>
    </div>
  )
}
