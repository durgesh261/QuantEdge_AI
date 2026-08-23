import React, { useEffect, useState, useCallback } from 'react'
import { tradingService } from '../../services/tradingService'
import { OrderDto, PositionDto, OrderFillDto, TradeHistoryDto } from '../../types/trading'
import { SkeletonTable } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  BookOpen,
  Layers,
  RefreshCw,
  AlertCircle,
} from 'lucide-react'

export const ExecutionMonitor: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'orders' | 'positions' | 'fills' | 'history'>('orders')
  const [orders, setOrders] = useState<OrderDto[]>([])
  const [positions, setPositions] = useState<PositionDto[]>([])
  const [fills, setFills] = useState<OrderFillDto[]>([])
  const [history, setHistory] = useState<TradeHistoryDto[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [ordersRes, posRes, fillsRes, histRes] = await Promise.allSettled([
        tradingService.getOrders(undefined, undefined, 50),
        tradingService.getPositions('OPEN'),
        tradingService.getFills(undefined, 50),
        tradingService.getTradeHistory(50),
      ])

      if (ordersRes.status === 'fulfilled') setOrders(ordersRes.value)
      if (posRes.status === 'fulfilled') setPositions(posRes.value)
      if (fillsRes.status === 'fulfilled') setFills(fillsRes.value)
      if (histRes.status === 'fulfilled') setHistory(histRes.value)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load execution ledger data')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 6000)
    return () => clearInterval(interval)
  }, [fetchData])

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-dev-accent" />
            <span>Authoritative Execution & Order Ledger</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            PostgreSQL persistent trade state, reconciliation timestamps & exchange fill telemetry
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

      {/* Tabs */}
      <div className="flex gap-2 border-b border-terminal-border pb-2 text-xs">
        <button
          onClick={() => setActiveTab('orders')}
          className={`px-3 py-1.5 rounded font-bold transition-all ${
            activeTab === 'orders'
              ? 'bg-dev-accent/15 text-dev-accent border border-dev-accent/30'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          All Orders ({orders.length})
        </button>
        <button
          onClick={() => setActiveTab('positions')}
          className={`px-3 py-1.5 rounded font-bold transition-all ${
            activeTab === 'positions'
              ? 'bg-dev-accent/15 text-dev-accent border border-dev-accent/30'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          Open Positions ({positions.length})
        </button>
        <button
          onClick={() => setActiveTab('fills')}
          className={`px-3 py-1.5 rounded font-bold transition-all ${
            activeTab === 'fills'
              ? 'bg-dev-accent/15 text-dev-accent border border-dev-accent/30'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          Fills Telemetry ({fills.length})
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`px-3 py-1.5 rounded font-bold transition-all ${
            activeTab === 'history'
              ? 'bg-dev-accent/15 text-dev-accent border border-dev-accent/30'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          Trade History ({history.length})
        </button>
      </div>

      {/* Tables Container */}
      <div className="glass-panel rounded-lg border border-terminal-border overflow-hidden">
        {isLoading && orders.length === 0 && positions.length === 0 ? (
          <SkeletonTable rows={5} cols={7} />
        ) : (
          <div className="overflow-x-auto p-2">
            {/* ORDERS TAB */}
            {activeTab === 'orders' && (
              orders.length > 0 ? (
                <table className="w-full text-left text-xs font-mono">
                  <thead>
                    <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                      <th className="py-2.5 px-3">Placed Time</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3">Type</th>
                      <th className="py-2.5 px-3">Price</th>
                      <th className="py-2.5 px-3">Quantity</th>
                      <th className="py-2.5 px-3">Filled</th>
                      <th className="py-2.5 px-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                    {orders.map((o) => (
                      <tr key={o.id} className="hover:bg-background-elevated/40 transition-colors">
                        <td className="py-2.5 px-3 text-slate-400">
                          {new Date(o.placedAt).toLocaleTimeString()}
                        </td>
                        <td className="py-2.5 px-3 font-bold text-white">{o.symbol}</td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              o.side === 'BUY' ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'
                            }`}
                          >
                            {o.side}
                          </span>
                        </td>
                        <td className="py-2.5 px-3">{o.orderType}</td>
                        <td className="py-2.5 px-3 font-bold text-white">
                          ${o.price ? o.price.toFixed(2) : 'MARKET'}
                        </td>
                        <td className="py-2.5 px-3">{o.quantity}</td>
                        <td className="py-2.5 px-3">{o.filledQuantity || 0}</td>
                        <td className="py-2.5 px-3">
                          <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-dev-cyan font-bold">
                            {o.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState
                  icon={BookOpen}
                  title="No Orders in Ledger"
                  description="Working and archived orders recorded by the Java backend will appear here."
                />
              )
            )}

            {/* POSITIONS TAB */}
            {activeTab === 'positions' && (
              positions.length > 0 ? (
                <table className="w-full text-left text-xs font-mono">
                  <thead>
                    <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3">Size</th>
                      <th className="py-2.5 px-3">Entry Price</th>
                      <th className="py-2.5 px-3">Mark Price</th>
                      <th className="py-2.5 px-3">Unrealized P&L</th>
                      <th className="py-2.5 px-3">Liquidation</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                    {positions.map((p) => {
                      const pnl = Number(p.unrealizedPnl) || 0
                      return (
                        <tr key={p.id} className="hover:bg-background-elevated/40 transition-colors">
                          <td className="py-2.5 px-3 font-bold text-white">{p.symbol}</td>
                          <td className="py-2.5 px-3 font-bold text-dev-cyan">{p.side}</td>
                          <td className="py-2.5 px-3">{p.quantity}</td>
                          <td className="py-2.5 px-3">${p.entryPrice?.toFixed(2)}</td>
                          <td className="py-2.5 px-3 font-bold text-white">
                            ${(p.currentPrice || p.entryPrice)?.toFixed(2)}
                          </td>
                          <td className="py-2.5 px-3">
                            <span className={`font-bold ${pnl >= 0 ? 'text-bullish' : 'text-bearish'}`}>
                              {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-bearish">
                            ${p.liquidationPrice ? p.liquidationPrice.toFixed(2) : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <EmptyState
                  icon={Layers}
                  title="No Open Exposure"
                  description="Active position contracts will be displayed here in real time."
                />
              )
            )}

            {/* FILLS TAB */}
            {activeTab === 'fills' && (
              fills.length > 0 ? (
                <table className="w-full text-left text-xs font-mono">
                  <thead>
                    <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                      <th className="py-2.5 px-3">Fill Time</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3">Fill Price</th>
                      <th className="py-2.5 px-3">Quantity</th>
                      <th className="py-2.5 px-3">Fee</th>
                      <th className="py-2.5 px-3">Exchange ID</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                    {fills.map((f) => (
                      <tr key={f.id} className="hover:bg-background-elevated/40 transition-colors">
                        <td className="py-2.5 px-3 text-slate-400">
                          {new Date(f.filledAt).toLocaleTimeString()}
                        </td>
                        <td className="py-2.5 px-3 font-bold text-white">{f.symbol}</td>
                        <td className="py-2.5 px-3 font-bold">{f.side}</td>
                        <td className="py-2.5 px-3 font-bold text-dev-cyan">${f.fillPrice?.toFixed(2)}</td>
                        <td className="py-2.5 px-3">{f.fillQuantity}</td>
                        <td className="py-2.5 px-3 text-warning">
                          ${f.fee?.toFixed(4)} {f.feeAsset}
                        </td>
                        <td className="py-2.5 px-3 text-slate-500 text-[10px] truncate max-w-[120px]">
                          {f.exchangeFillId || f.id}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState
                  icon={BookOpen}
                  title="No Fills Recorded"
                  description="Exchange matched fills and fee records will appear here."
                />
              )
            )}

            {/* HISTORY TAB */}
            {activeTab === 'history' && (
              history.length > 0 ? (
                <table className="w-full text-left text-xs font-mono">
                  <thead>
                    <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                      <th className="py-2.5 px-3">Closed Time</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Direction</th>
                      <th className="py-2.5 px-3">Entry / Exit</th>
                      <th className="py-2.5 px-3">Quantity</th>
                      <th className="py-2.5 px-3">Realized P&L</th>
                      <th className="py-2.5 px-3">Close Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                    {history.map((h) => {
                      const pnl = Number(h.netPnl) || 0
                      return (
                        <tr key={h.id} className="hover:bg-background-elevated/40 transition-colors">
                          <td className="py-2.5 px-3 text-slate-400">
                            {new Date(h.closedAt).toLocaleTimeString()}
                          </td>
                          <td className="py-2.5 px-3 font-bold text-white">{h.symbol}</td>
                          <td className="py-2.5 px-3 font-bold">{h.direction}</td>
                          <td className="py-2.5 px-3">
                            ${h.entryPrice?.toFixed(2)} → ${h.exitPrice?.toFixed(2)}
                          </td>
                          <td className="py-2.5 px-3">{h.quantity}</td>
                          <td className="py-2.5 px-3">
                            <span className={`font-bold ${pnl >= 0 ? 'text-bullish' : 'text-bearish'}`}>
                              {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-slate-300 text-[10px]">
                            {h.closeReason || 'Manual / TP / SL'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <EmptyState
                  icon={BookOpen}
                  title="No Closed Trades"
                  description="Completed round-trip trades and P&L statements will appear here."
                />
              )
            )}
          </div>
        )}
      </div>
    </div>
  )
}
