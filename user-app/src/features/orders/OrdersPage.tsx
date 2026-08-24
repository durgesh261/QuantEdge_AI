import React, { useEffect, useState, useCallback } from 'react'
import { tradingService } from '../../services/tradingService'
import { OrderDto, OrderFillDto } from '../../types/trading'
import { SkeletonTable } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  BookOpen,
  Filter,
  RefreshCw,
  AlertCircle,
  Shield,
  Layers,
} from 'lucide-react'

export const OrdersPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'open' | 'history' | 'fills'>('open')

  // State
  const [orders, setOrders] = useState<OrderDto[]>([])
  const [fills, setFills] = useState<OrderFillDto[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL')
  const [statusFilter, setStatusFilter] = useState<string>('ALL')

  const fetchData = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)

      const [ordersRes, fillsRes] = await Promise.allSettled([
        tradingService.getOrders(
          symbolFilter !== 'ALL' ? symbolFilter : undefined,
          statusFilter !== 'ALL' ? statusFilter : undefined,
          100
        ),
        tradingService.getFills(
          symbolFilter !== 'ALL' ? symbolFilter : undefined,
          100
        ),
      ])

      if (ordersRes.status === 'fulfilled') setOrders(ordersRes.value)
      if (fillsRes.status === 'fulfilled') setFills(fillsRes.value)
    } catch (err: any) {
      console.warn('Failed to load orders ledger', err)
      setError(err.response?.data?.message || 'Unable to connect to orders execution ledger')
    } finally {
      setIsLoading(false)
    }
  }, [symbolFilter, statusFilter])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 6000)
    return () => clearInterval(interval)
  }, [fetchData])

  // Filtered lists
  const openOrders = orders.filter((o) => o.status === 'OPEN' || o.status === 'PENDING')
  const orderHistory = orders

  const totalFees = fills.reduce((acc, f) => acc + (Number(f.fee) || 0), 0)

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-brand-cyan" />
            <span>Orders & Fills Audit Ledger</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Full Execution History, Active Limit Orders & Exchange Fill Reconciliation
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchData}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
            <span>Refresh Ledger</span>
          </button>
        </div>
      </div>

      {/* Summary Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Open Working Orders</div>
          <div className="mt-2 text-2xl font-bold font-mono text-brand-cyan">{openOrders.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Limit & stop orders</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Total Orders Logged</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">{orders.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Lifetime account ledger</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Total Execution Fills</div>
          <div className="mt-2 text-2xl font-bold font-mono text-bullish">{fills.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Verified exchange fills</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Total Trading Fees</div>
          <div className="mt-2 text-2xl font-bold font-mono text-slate-300">${totalFees.toFixed(4)}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Exchange taker/maker fees</div>
        </div>
      </div>

      {/* Main Ledger Container */}
      <div className="glass-panel rounded-lg overflow-hidden flex flex-col min-h-[400px]">
        {/* Tab & Filter Header */}
        <div className="p-3 border-b border-terminal-border/80 flex flex-wrap items-center justify-between gap-3 bg-background-surface/80">
          {/* Tab Navigation */}
          <div className="flex items-center gap-1 sm:gap-2">
            <button
              onClick={() => setActiveTab('open')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
                activeTab === 'open'
                  ? 'bg-brand-cyan text-background font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <span>Open Orders</span>
              <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
                {openOrders.length}
              </span>
            </button>

            <button
              onClick={() => setActiveTab('history')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
                activeTab === 'history'
                  ? 'bg-brand-cyan text-background font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <span>Order History</span>
              <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
                {orderHistory.length}
              </span>
            </button>

            <button
              onClick={() => setActiveTab('fills')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
                activeTab === 'fills'
                  ? 'bg-brand-cyan text-background font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <span>Execution Fills</span>
              <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
                {fills.length}
              </span>
            </button>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3 text-xs font-mono">
            {/* Status Filter (for orders/history) */}
            {activeTab !== 'fills' && (
              <div className="flex items-center gap-1.5">
                <span className="text-slate-400">Status:</span>
                <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                  {['ALL', 'OPEN', 'FILLED', 'CANCELLED', 'REJECTED'].map((st) => (
                    <button
                      key={st}
                      onClick={() => setStatusFilter(st)}
                      className={`px-2 py-0.5 rounded text-[11px] transition-all ${
                        statusFilter === st
                          ? 'bg-background-elevated text-brand-cyan font-bold border border-brand-cyan/30'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {st}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Symbol Filter */}
            <div className="flex items-center gap-1.5">
              <span className="text-slate-400 flex items-center gap-1">
                <Filter className="w-3 h-3" />
                Symbol:
              </span>
              <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                {['ALL', 'BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD'].map((sym) => (
                  <button
                    key={sym}
                    onClick={() => setSymbolFilter(sym)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-all ${
                      symbolFilter === sym
                        ? 'bg-background-elevated text-brand-cyan font-bold border border-brand-cyan/30'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {sym}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Error Notice */}
        {error && (
          <div className="m-3 p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Tables Content */}
        <div className="flex-1 overflow-x-auto p-2">
          {isLoading && orders.length === 0 && fills.length === 0 ? (
            <SkeletonTable rows={6} cols={7} />
          ) : (
            <>
              {/* TAB 1: OPEN ORDERS */}
              {activeTab === 'open' && (
                openOrders.length > 0 ? (
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
                        <th className="py-2.5 px-3">Leverage</th>
                        <th className="py-2.5 px-3">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                      {openOrders.map((o) => (
                        <tr key={o.id} className="hover:bg-background-elevated/40 transition-colors">
                          <td className="py-2.5 px-3 text-slate-400">
                            {new Date(o.placedAt).toLocaleTimeString()}
                          </td>
                          <td className="py-2.5 px-3 font-bold text-white">{o.symbol}</td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                o.side === 'BUY'
                                  ? 'bg-bullish/15 text-bullish'
                                  : 'bg-bearish/15 text-bearish'
                              }`}
                            >
                              {o.side}
                            </span>
                          </td>
                          <td className="py-2.5 px-3">{o.orderType}</td>
                          <td className="py-2.5 px-3 font-semibold text-white">
                            ${o.price ? o.price.toFixed(2) : 'MARKET'}
                          </td>
                          <td className="py-2.5 px-3">{o.quantity}</td>
                          <td className="py-2.5 px-3">{o.filledQuantity || 0}</td>
                          <td className="py-2.5 px-3">{o.leverage}x</td>
                          <td className="py-2.5 px-3">
                            <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-brand-cyan font-bold">
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
                    title="No Open Working Orders"
                    description="Limit and conditional orders will appear here when active in the order book."
                    actionLabel="Open Trading Terminal"
                    actionLink="/terminal"
                  />
                )
              )}

              {/* TAB 2: ORDER HISTORY */}
              {activeTab === 'history' && (
                orderHistory.length > 0 ? (
                  <table className="w-full text-left text-xs font-mono">
                    <thead>
                      <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                        <th className="py-2.5 px-3">Placed Time</th>
                        <th className="py-2.5 px-3">Symbol</th>
                        <th className="py-2.5 px-3">Side</th>
                        <th className="py-2.5 px-3">Type</th>
                        <th className="py-2.5 px-3">Price</th>
                        <th className="py-2.5 px-3">Quantity</th>
                        <th className="py-2.5 px-3">Avg Fill</th>
                        <th className="py-2.5 px-3">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                      {orderHistory.map((o) => (
                        <tr key={o.id} className="hover:bg-background-elevated/40 transition-colors">
                          <td className="py-2.5 px-3 text-slate-400">
                            {new Date(o.placedAt).toLocaleString()}
                          </td>
                          <td className="py-2.5 px-3 font-bold text-white">{o.symbol}</td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                o.side === 'BUY'
                                  ? 'bg-bullish/15 text-bullish'
                                  : 'bg-bearish/15 text-bearish'
                              }`}
                            >
                              {o.side}
                            </span>
                          </td>
                          <td className="py-2.5 px-3">{o.orderType}</td>
                          <td className="py-2.5 px-3">${o.price ? o.price.toFixed(2) : 'MARKET'}</td>
                          <td className="py-2.5 px-3">{o.quantity}</td>
                          <td className="py-2.5 px-3 text-brand-cyan">
                            {o.averageFillPrice ? `$${o.averageFillPrice.toFixed(2)}` : '—'}
                          </td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                o.status === 'FILLED'
                                  ? 'bg-bullish/15 text-bullish'
                                  : o.status === 'CANCELLED'
                                  ? 'bg-slate-800 text-slate-400'
                                  : 'bg-background border border-terminal-border text-slate-300'
                              }`}
                            >
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
                    title="No Order History Found"
                    description="Closed, filled, or cancelled orders will be recorded here."
                  />
                )
              )}

              {/* TAB 3: EXECUTION FILLS */}
              {activeTab === 'fills' && (
                fills.length > 0 ? (
                  <table className="w-full text-left text-xs font-mono">
                    <thead>
                      <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                        <th className="py-2.5 px-3">Fill Time</th>
                        <th className="py-2.5 px-3">Symbol</th>
                        <th className="py-2.5 px-3">Side</th>
                        <th className="py-2.5 px-3">Fill Price</th>
                        <th className="py-2.5 px-3">Fill Quantity</th>
                        <th className="py-2.5 px-3">Fee Paid</th>
                        <th className="py-2.5 px-3">Exchange Fill ID</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                      {fills.map((f) => (
                        <tr key={f.id} className="hover:bg-background-elevated/40 transition-colors">
                          <td className="py-2.5 px-3 text-slate-400">
                            {new Date(f.filledAt).toLocaleString()}
                          </td>
                          <td className="py-2.5 px-3 font-bold text-white">{f.symbol}</td>
                          <td className="py-2.5 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                f.side === 'BUY'
                                  ? 'bg-bullish/15 text-bullish'
                                  : 'bg-bearish/15 text-bearish'
                              }`}
                            >
                              {f.side}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 font-semibold text-white">
                            ${f.fillPrice ? f.fillPrice.toFixed(2) : '0.00'}
                          </td>
                          <td className="py-2.5 px-3">{f.fillQuantity}</td>
                          <td className="py-2.5 px-3 text-warning">
                            ${f.fee ? f.fee.toFixed(4) : '0.0000'} {f.feeAsset || 'USDT'}
                          </td>
                          <td className="py-2.5 px-3 text-slate-400 text-[11px] truncate max-w-[120px]">
                            {f.exchangeFillId || f.id}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState
                    icon={Layers}
                    title="No Execution Fills Recorded"
                    description="Verified exchange fills and execution fee records will be displayed here."
                  />
                )
              )}
            </>
          )}
        </div>

        {/* Footer Security Badge */}
        <div className="p-2.5 border-t border-terminal-border/60 bg-background/50 flex items-center justify-between text-[11px] font-mono text-slate-500">
          <span className="flex items-center gap-1">
            <Shield className="w-3.5 h-3.5 text-bullish" />
            <span>Authoritative Persistence: orders & order_fills tables</span>
          </span>
          <span className="text-slate-400">Tenant Isolated</span>
        </div>
      </div>
    </div>
  )
}
