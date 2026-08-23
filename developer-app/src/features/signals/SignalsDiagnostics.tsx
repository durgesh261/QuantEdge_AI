import React, { useEffect, useState, useCallback } from 'react'
import { tradingService } from '../../services/tradingService'
import { SignalSetupDto } from '../../types/trading'
import { AiEnrichmentDto } from '../../types/market'
import { MetricCard } from '../../components/common/MetricCard'
import { SkeletonCard, SkeletonTable } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  Radio,
  Sparkles,
  TrendingUp,
  RefreshCw,
  AlertCircle,
  Cpu,
} from 'lucide-react'

export const SignalsDiagnostics: React.FC = () => {
  const [signals, setSignals] = useState<SignalSetupDto[]>([])
  const [enrichments, setEnrichments] = useState<Record<string, AiEnrichmentDto>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const sigs = await tradingService.getSignals(undefined, undefined, 50)
      setSignals(sigs)

      // Fetch enrichments for active/qualified setups
      const enrichmentMap: Record<string, AiEnrichmentDto> = {}
      for (const s of sigs) {
        try {
          const ai = await tradingService.getAiIntelligence(s.setupId)
          if (ai) enrichmentMap[s.setupId] = ai
        } catch {
          // Optional AI payload per setup
        }
      }
      setEnrichments(enrichmentMap)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to fetch strategy signals diagnostics')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 8000)
    return () => clearInterval(interval)
  }, [fetchData])

  const qualifiedCount = signals.filter((s) => s.setupState === 'QUALIFIED').length
  const activeCount = signals.filter((s) => s.setupState === 'ACTIVE').length

  if (isLoading && signals.length === 0) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <SkeletonCard rows={2} />
          <SkeletonCard rows={2} />
          <SkeletonCard rows={2} />
        </div>
        <SkeletonTable rows={5} cols={6} />
      </div>
    )
  }

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <Radio className="w-5 h-5 text-dev-purple" />
            <span>SMC Strategy & Signal Diagnostics</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Deterministic 1H order block setups, state machine qualification & AI conviction telemetry
          </p>
        </div>

        <button
          onClick={fetchData}
          className="p-2 rounded bg-background border border-terminal-border hover:bg-background-elevated text-slate-300 hover:text-white transition-colors"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/30 text-bearish text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* KPI Overview */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <MetricCard
          title="TOTAL DISCOVERED SETUPS"
          value={signals.length}
          subtext="1H Stream Monitoring"
          icon={Radio}
          variant="purple"
        />
        <MetricCard
          title="QUALIFIED FOR EXECUTION"
          value={qualifiedCount}
          subtext="Passed Structural Invariants"
          icon={TrendingUp}
          variant="emerald"
        />
        <MetricCard
          title="ACTIVE POSITIONS / ORDERS"
          value={activeCount}
          subtext="In-Flight Engine Trades"
          icon={Cpu}
          variant="cyan"
        />
      </div>

      {/* Signals Diagnostics Table */}
      <div className="glass-panel rounded-lg border border-terminal-border overflow-hidden">
        <div className="p-4 border-b border-terminal-border/80 flex items-center justify-between">
          <h3 className="font-bold text-white text-xs uppercase tracking-wider">
            1H Deterministic SMC Signal Telemetry
          </h3>
          <span className="text-[11px] text-slate-400">{signals.length} Setups Tracked</span>
        </div>

        <div className="overflow-x-auto p-2">
          {signals.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2.5 px-3">Setup ID</th>
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Direction</th>
                  <th className="py-2.5 px-3">State</th>
                  <th className="py-2.5 px-3">Entry Price</th>
                  <th className="py-2.5 px-3">SL / TP</th>
                  <th className="py-2.5 px-3">AI Conviction</th>
                  <th className="py-2.5 px-3">Discovered At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {signals.map((s) => {
                  const isLong = s.direction === 'LONG' || s.direction === 'BUY'
                  const ai = enrichments[s.setupId]
                  const confidence = ai?.confidence
                    ? Math.round(Number(ai.confidence) * (Number(ai.confidence) <= 1 ? 100 : 1))
                    : Math.round(s.confidence * (s.confidence <= 1 ? 100 : 1))

                  return (
                    <tr key={s.id} className="hover:bg-background-elevated/40 transition-colors">
                      <td className="py-2.5 px-3 font-bold text-dev-cyan truncate max-w-[140px]">
                        {s.setupId}
                      </td>
                      <td className="py-2.5 px-3 font-bold text-white">{s.symbol}</td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            isLong ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'
                          }`}
                        >
                          {s.direction}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">
                        <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300">
                          {s.setupState}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 font-semibold text-white">${s.entryPrice?.toFixed(2)}</td>
                      <td className="py-2.5 px-3 text-slate-400 text-[11px]">
                        <span className="text-bearish">${s.stopLoss?.toFixed(2)}</span> /{' '}
                        <span className="text-bullish">${s.takeProfit?.toFixed(2)}</span>
                      </td>
                      <td className="py-2.5 px-3">
                        <span className="font-bold text-dev-purple flex items-center gap-1">
                          <Sparkles className="w-3 h-3" />
                          {confidence}%
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-slate-500 text-[10px]">
                        {new Date(s.createdAt).toLocaleTimeString()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState
              icon={Radio}
              title="No SMC Setups Discovered"
              description="Signals identified by the deterministic Python SMC loop will be visible here."
            />
          )}
        </div>
      </div>
    </div>
  )
}
