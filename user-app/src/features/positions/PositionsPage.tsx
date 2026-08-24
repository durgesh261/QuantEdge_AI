import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { tradingService } from '../../services/tradingService'
import { useMarketStore } from '../../stores/marketStore'
import { PositionDto, TradeHistoryDto } from '../../types/trading'
import { SkeletonTable } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import { formatPrice } from '../../constants/instruments'
import {
  Layers,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Clock,
  ArrowRight,
  AlertCircle,
  ShieldCheck,
} from 'lucide-react'

export const PositionsPage: React.FC = () => {
  const navigate = useNavigate()
  const { setActiveSymbol } = useMarketStore()

  const [activeTab, setActiveTab] = useState<'open' | 'history'>('open')
  const [positions, setPositions] = useState<PositionDto[]>([])
  const [history, setHistory] = useState<TradeHistoryDto[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)

      const [positionsRes, historyRes] = await Promise.allSettled([
        tradingService.getPositions('OPEN'),
        tradingService.getTradeHistory(100),
      ])

      if (positionsRes.status === 'fulfilled') setPositions(positionsRes.value)
      if (historyRes.status === 'fulfilled') setHistory(historyRes.value)
    } catch (err: any) {
      console.warn('Failed to fetch positions data', err)
      setError(err.response?.data?.message || 'Unable to connect to positions service')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [fetchData])

  // Summary Metrics
  const totalUnrealizedPnl = positions.reduce((acc, p) => acc + (Number(p.unrealizedPnl) || 0), 0)
  const totalRealizedPnl = history.reduce((acc, h) => acc + (Number(h.netPnl) || 0), 0)
  const totalMarginUsed = positions.reduce((acc, p) => acc + (Number(p.marginUsed) || 0), 0)

  const handleOpenTerminal = (symbol: string) => {
    setActiveSymbol(symbol)
    navigate('/terminal')
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Layers className="w-5 h-5 text-brand-cyan" />
            <span>Positions & P&L Portfolio</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Active Leveraged Positions, Liquidation Risk & Historical Realized Performance
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchData}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
            <span>Refresh Portfolio</span>
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Unrealized P&L</div>
          <div
            className={`mt-2 text-2xl font-bold font-mono ${
              totalUnrealizedPnl >= 0 ? 'text-bullish' : 'text-bearish'
            }`}
          >
            {totalUnrealizedPnl >= 0 ? '+' : ''}${totalUnrealizedPnl.toFixed(2)}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Active positions mark-to-market</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Realized P&L (Total)</div>
          <div
            className={`mt-2 text-2xl font-bold font-mono ${
              totalRealizedPnl >= 0 ? 'text-bullish' : 'text-bearish'
            }`}
          >
            {totalRealizedPnl >= 0 ? '+' : ''}${totalRealizedPnl.toFixed(2)}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Closed trade net return</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Active Margin Allocated</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">${totalMarginUsed.toFixed(2)}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Collateral locked in positions</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Active Open Positions</div>
          <div className="mt-2 text-2xl font-bold font-mono text-brand-cyan">{positions.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Max 1 simultaneous position lock</div>
        </div>
      </div>

      {/* Main Container */}
      <div className="glass-panel rounded-lg overflow-hidden flex flex-col min-h-[400px]">
        {/* Navigation Tabs */}
        <div className="p-3 border-b border-terminal-border/80 flex items-center justify-between bg-background-surface/80">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab('open')}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
                activeTab === 'open'
                  ? 'bg-brand-cyan text-background font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Open Positions ({positions.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('history')}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
                activeTab === 'history'
                  ? 'bg-brand-cyan text-background font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Clock className="w-3.5 h-3.5" />
              <span>Closed History ({history.length})</span>
            </button>
          </div>

          <span className="text-slate-500 font-mono text-xs hidden sm:inline">
            Authoritative Server State
          </span>
        </div>

        {/* Error Notice */}
        {error && (
          <div className="m-3 p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Table Content */}
        <div className="flex-1 overflow-x-auto p-2">
          {isLoading && positions.length === 0 && history.length === 0 ? (
            <SkeletonTable rows={5} cols={8} />
          ) : (
            <>
              {/* TAB 1: OPEN POSITIONS */}
              {activeTab === 'open' && (
                positions.length > 0 ? (
                  <table className="w-full text-left text-xs font-mono">
                    <thead>
                      <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                        <th className="py-3 px-3">Symbol</th>
                        <th className="py-3 px-3">Side</th>
                        <th className="py-3 px-3">Size</th>
                        <th className="py-3 px-3">Entry Price</th>
                        <th className="py-3 px-3">Mark Price</th>
                        <th className="py-3 px-3">Unrealized P&L</th>
                        <th className="py-3 px-3">Margin</th>
                        <th className="py-3 px-3">Leverage</th>
                        <th className="py-3 px-3">Liq. Price</th>
                        <th className="py-3 px-3 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                      {positions.map((p) => {
                        const isLong = p.side?.toUpperCase() === 'LONG' || p.side?.toUpperCase() === 'BUY'
                        const pnl = Number(p.unrealizedPnl) || 0
                        const notional = p.entryPrice * p.quantity
                        const pnlPct = notional > 0 ? (pnl / notional) * 100 : 0
                        const mark = p.currentPrice ?? p.entryPrice

                        return (
                          <tr key={p.id} className="hover:bg-background-elevated/40 transition-colors">
                            <td className="py-3 px-3 font-bold text-white text-sm">{p.symbol}</td>
                            <td className="py-3 px-3">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold flex items-center gap-1 w-fit ${
                                  isLong
                                    ? 'bg-bullish/15 text-bullish'
                                    : 'bg-bearish/15 text-bearish'
                                }`}
                              >
                                {isLong ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                                {p.side}
                              </span>
                            </td>
                            <td className="py-3 px-3 font-bold">{p.quantity}</td>
                            <td className="py-3 px-3">${formatPrice(p.entryPrice, p.symbol)}</td>
                            <td className="py-3 px-3 font-bold text-white">${formatPrice(mark, p.symbol)}</td>
                            <td className="py-3 px-3">
                              <div className={`font-bold ${pnl >= 0 ? 'text-bullish' : 'text-bearish'}`}>
                                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
                              </div>
                            </td>
                            <td className="py-3 px-3">${(p.marginUsed ?? 0).toFixed(2)}</td>
                            <td className="py-3 px-3">{p.leverage}x</td>
                            <td className="py-3 px-3 text-bearish font-semibold">
                              ${p.liquidationPrice ? formatPrice(p.liquidationPrice, p.symbol) : '—'}
                            </td>
                            <td className="py-3 px-3 text-right">
                              <button
                                onClick={() => handleOpenTerminal(p.symbol)}
                                className="px-2.5 py-1 rounded bg-brand-cyan/15 hover:bg-brand-cyan/25 text-brand-cyan text-xs font-bold transition-all inline-flex items-center gap-1"
                              >
                                <span>Terminal</span>
                                <ArrowRight className="w-3 h-3" />
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState
                    icon={Layers}
                    title="No Active Open Positions"
                    description="When the SMC engine qualifies and fills an order, active exposure appears here."
                    actionLabel="Launch Trading Terminal"
                    actionLink="/terminal"
                  />
                )
              )}

              {/* TAB 2: CLOSED HISTORY */}
              {activeTab === 'history' && (
                history.length > 0 ? (
                  <table className="w-full text-left text-xs font-mono">
                    <thead>
                      <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                        <th className="py-2.5 px-3">Closed Time</th>
                        <th className="py-2.5 px-3">Symbol</th>
                        <th className="py-2.5 px-3">Direction</th>
                        <th className="py-2.5 px-3">Entry</th>
                        <th className="py-2.5 px-3">Exit</th>
                        <th className="py-2.5 px-3">Quantity</th>
                        <th className="py-2.5 px-3">Realized P&L</th>
                        <th className="py-2.5 px-3">Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                      {history.map((h) => {
                        const isLong = h.direction?.toUpperCase() === 'LONG' || h.direction?.toUpperCase() === 'BUY'
                        const pnl = Number(h.netPnl) || 0

                        return (
                          <tr key={h.id} className="hover:bg-background-elevated/40 transition-colors">
                            <td className="py-2.5 px-3 text-slate-400">
                              {new Date(h.closedAt).toLocaleString()}
                            </td>
                            <td className="py-2.5 px-3 font-bold text-white">{h.symbol}</td>
                            <td className="py-2.5 px-3">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  isLong
                                    ? 'bg-bullish/15 text-bullish'
                                    : 'bg-bearish/15 text-bearish'
                                }`}
                              >
                                {h.direction}
                              </span>
                            </td>
                            <td className="py-2.5 px-3">${h.entryPrice.toFixed(2)}</td>
                            <td className="py-2.5 px-3">${h.exitPrice.toFixed(2)}</td>
                            <td className="py-2.5 px-3">{h.quantity}</td>
                            <td className="py-2.5 px-3">
                              <span className={`font-bold ${pnl >= 0 ? 'text-bullish' : 'text-bearish'}`}>
                                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                              </span>
                            </td>
                            <td className="py-2.5 px-3">
                              <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300">
                                {h.closeReason}
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState
                    icon={Clock}
                    title="No Closed Trades in History"
                    description="When positions are closed (via Take Profit, Stop Loss, or manual exit), trade records appear here."
                  />
                )
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-2.5 border-t border-terminal-border/60 bg-background/50 flex items-center justify-between text-[11px] font-mono text-slate-500">
          <span className="flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-bullish" />
            <span>Database Table: positions & trade_history (Synchronized)</span>
          </span>
          <span className="text-slate-400">Mark-to-Market Realtime</span>
        </div>
      </div>
    </div>
  )
}
