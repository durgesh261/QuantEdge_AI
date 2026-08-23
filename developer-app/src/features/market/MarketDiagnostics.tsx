import React, { useEffect, useState, useCallback } from 'react'
import { developerService } from '../../services/developerService'
import { marketService } from '../../services/marketService'
import { ApiDiagnosticsResponse } from '../../types/developer'
import { ProductDto, MarketStatusDto } from '../../types/market'
import { MetricCard } from '../../components/common/MetricCard'
import { SkeletonCard, SkeletonTable } from '../../components/common/Skeleton'
import {
  Activity,
  Radio,
  RefreshCw,
  AlertCircle,
  Database,
  Globe,
} from 'lucide-react'

export const MarketDiagnostics: React.FC = () => {
  const [diagnostics, setDiagnostics] = useState<ApiDiagnosticsResponse | null>(null)
  const [products, setProducts] = useState<ProductDto[]>([])
  const [marketStatus, setMarketStatus] = useState<MarketStatusDto | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [diagRes, prodRes, statusRes] = await Promise.all([
        developerService.getApiDiagnostics(),
        marketService.getProducts().catch(() => []),
        marketService.getMarketStatus('BTCUSD').catch(() => null),
      ])
      setDiagnostics(diagRes)
      setProducts(prodRes)
      setMarketStatus(statusRes)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to fetch market diagnostics')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 8000)
    return () => clearInterval(interval)
  }, [fetchData])

  if (isLoading && !diagnostics) {
    return (
      <div className="space-y-6">
        <SkeletonCard rows={3} />
        <SkeletonTable rows={4} cols={5} />
      </div>
    )
  }

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <Activity className="w-5 h-5 text-dev-cyan" />
            <span>Market Data & Gateway Diagnostics</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Exchange REST/WebSocket gateway health, ticker freshness & contract lot specifications
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

      {/* Gateway Telemetry Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="DELTA GATEWAY PING"
          value={`${diagnostics?.deltaPingMs ?? 0} ms`}
          subtext={diagnostics?.deltaApiStatus || 'ONLINE'}
          icon={Globe}
          variant="cyan"
        />
        <MetricCard
          title="PYTHON ENGINE RPC"
          value={`${diagnostics?.pythonEnginePingMs ?? 0} ms`}
          subtext={diagnostics?.pythonEngineStatus || 'ONLINE'}
          icon={Radio}
          variant="purple"
        />
        <MetricCard
          title="DB CONNECTION POOL"
          value={`${diagnostics?.databasePoolActive ?? 0} / ${diagnostics?.databasePoolTotal ?? 10}`}
          subtext="Active / Max Hikari Connections"
          icon={Database}
          variant="emerald"
        />
        <MetricCard
          title="BTCUSD 24H VOLUME"
          value={marketStatus?.volume24h ? `$${(marketStatus.volume24h / 1_000_000).toFixed(2)}M` : '—'}
          subtext={`Status: ${marketStatus?.tradingActive ? 'ACTIVE' : 'IDLE'}`}
          icon={Activity}
          variant="blue"
        />
      </div>

      {/* Contract Specifications Table */}
      <div className="glass-panel rounded-lg border border-terminal-border overflow-hidden">
        <div className="p-4 border-b border-terminal-border/80 flex items-center justify-between">
          <h3 className="font-bold text-white text-xs uppercase tracking-wider">
            Exchange Tradable Products & Lot Specifications
          </h3>
          <span className="text-[11px] text-slate-400">{products.length} Products Loaded</span>
        </div>

        <div className="overflow-x-auto p-2">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                <th className="py-2.5 px-3">Symbol</th>
                <th className="py-2.5 px-3">Contract Type</th>
                <th className="py-2.5 px-3">Tick Size</th>
                <th className="py-2.5 px-3">Lot Size</th>
                <th className="py-2.5 px-3">Price Band (Low - High)</th>
                <th className="py-2.5 px-3">Exchange State</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-terminal-border/50 text-slate-200">
              {products.map((p) => (
                <tr key={p.symbol} className="hover:bg-background-elevated/40 transition-colors">
                  <td className="py-2.5 px-3 font-bold text-white">{p.symbol}</td>
                  <td className="py-2.5 px-3 text-slate-400">{p.contractType}</td>
                  <td className="py-2.5 px-3 font-bold text-dev-cyan">{p.tickSize}</td>
                  <td className="py-2.5 px-3">{p.lotSize}</td>
                  <td className="py-2.5 px-3 text-slate-300">
                    {p.priceBandLow && p.priceBandHigh ? `$${p.priceBandLow} - $${p.priceBandHigh}` : 'Dynamic'}
                  </td>
                  <td className="py-2.5 px-3">
                    <span className="px-2 py-0.5 rounded bg-bullish/15 text-bullish font-bold text-[10px]">
                      {p.tradingStatus || 'LIVE'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
