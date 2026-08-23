import React, { useEffect, useState, useCallback, useMemo } from 'react'
import { useNotificationStore } from '../../stores/notificationStore'
import { tradingService } from '../../services/tradingService'
import {
  Activity,
  Filter,
  RefreshCw,
  CheckCheck,
  Radio,
  BookOpen,
  ShieldAlert,
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Info,
  Clock,
  ArrowUpDown,
  TrendingUp,
  TrendingDown,
  X,
  Shield,
} from 'lucide-react'

interface UnifiedActivityItem {
  id: string
  category: 'TRADING' | 'SIGNALS' | 'SYSTEM' | 'RISK'
  type: string
  title: string
  description: string
  severity: 'CRITICAL' | 'WARNING' | 'SUCCESS' | 'INFO' | 'ERROR'
  symbol?: string
  direction?: string
  referenceId?: string | null
  timestamp: string
  isRead?: boolean
  raw?: any
}

export const ActivityCenter: React.FC = () => {
  const { unreadCount, markAllAsRead } = useNotificationStore()

  // State
  const [unifiedEvents, setUnifiedEvents] = useState<UnifiedActivityItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<UnifiedActivityItem | null>(null)

  // Filters
  const [categoryFilter, setCategoryFilter] = useState<string>('ALL')
  const [severityFilter, setSeverityFilter] = useState<string>('ALL')
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL')
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc')

  const loadAllActivity = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)

      const [, ordersRes, signalsRes, historyRes] = await Promise.allSettled([
        useNotificationStore.getState().fetchNotifications(false, 100),
        tradingService.getOrders(undefined, undefined, 50),
        tradingService.getSignals(undefined, undefined, 50),
        tradingService.getTradeHistory(50),
      ])

      const items: UnifiedActivityItem[] = []

      // 1. In-App Notifications
      const currentNotifs = useNotificationStore.getState().notifications
      for (const n of currentNotifs) {
        let cat: 'TRADING' | 'SIGNALS' | 'SYSTEM' | 'RISK' = 'SYSTEM'
        if (n.type.includes('ORDER') || n.type.includes('POSITION') || n.type.includes('FILL')) cat = 'TRADING'
        else if (n.type.includes('SIGNAL') || n.type.includes('SMC')) cat = 'SIGNALS'
        else if (n.type.includes('KILL_SWITCH') || n.type.includes('RISK') || n.type.includes('LIMIT')) cat = 'RISK'

        items.push({
          id: `notif_${n.id}`,
          category: cat,
          type: n.type,
          title: n.title,
          description: n.message,
          severity: (n.severity as any) || 'INFO',
          referenceId: n.referenceId,
          timestamp: n.createdAt,
          isRead: n.isRead,
          raw: n,
        })
      }

      // 2. Orders Stream
      if (ordersRes.status === 'fulfilled' && ordersRes.value) {
        for (const o of ordersRes.value) {
          items.push({
            id: `order_${o.id}`,
            category: 'TRADING',
            type: o.status === 'FILLED' ? 'ORDER_FILLED' : `ORDER_${o.status}`,
            title: `${o.side} ${o.symbol} (${o.orderType})`,
            description: `Order ${o.clientOrderId} for ${o.quantity} units at ${o.price ? '$' + o.price.toFixed(2) : 'MARKET'} is ${o.status}.`,
            severity: o.status === 'FILLED' ? 'SUCCESS' : o.status === 'REJECTED' || o.status === 'FAILED' ? 'ERROR' : 'INFO',
            symbol: o.symbol,
            direction: o.side === 'BUY' ? 'LONG' : 'SHORT',
            referenceId: o.clientOrderId,
            timestamp: o.placedAt,
            isRead: true,
            raw: o,
          })
        }
      }

      // 3. Signals Stream
      if (signalsRes.status === 'fulfilled' && signalsRes.value) {
        for (const s of signalsRes.value) {
          items.push({
            id: `signal_${s.id}`,
            category: 'SIGNALS',
            type: s.setupState === 'QUALIFIED' ? 'SIGNAL_QUALIFIED' : `SIGNAL_${s.setupState}`,
            title: `1H SMC ${s.direction} on ${s.symbol}`,
            description: `Setup ${s.setupId} identified with Entry $${s.entryPrice?.toFixed(2)}, SL $${s.stopLoss?.toFixed(2)}, TP $${s.takeProfit?.toFixed(2)} (RR ${s.riskReward?.toFixed(2)}).`,
            severity: s.setupState === 'QUALIFIED' ? 'SUCCESS' : s.setupState === 'INVALIDATED' ? 'WARNING' : 'INFO',
            symbol: s.symbol,
            direction: s.direction,
            referenceId: s.setupId,
            timestamp: s.createdAt,
            isRead: true,
            raw: s,
          })
        }
      }

      // 4. Closed Trade History
      if (historyRes.status === 'fulfilled' && historyRes.value) {
        for (const h of historyRes.value) {
          const isProfit = Number(h.netPnl) >= 0
          items.push({
            id: `hist_${h.id}`,
            category: 'TRADING',
            type: 'POSITION_CLOSED',
            title: `Closed ${h.direction} ${h.symbol} (${h.closeReason})`,
            description: `Position closed with Net P&L ${isProfit ? '+' : ''}$${Number(h.netPnl).toFixed(2)}. Entry: $${h.entryPrice.toFixed(2)}, Exit: $${h.exitPrice.toFixed(2)}.`,
            severity: isProfit ? 'SUCCESS' : 'WARNING',
            symbol: h.symbol,
            direction: h.direction,
            referenceId: h.setupId || h.id,
            timestamp: h.closedAt,
            isRead: true,
            raw: h,
          })
        }
      }

      // Deduplicate by id and sort
      const unique = Array.from(new Map(items.map((i) => [i.id, i])).values())
      setUnifiedEvents(unique)
    } catch (err: any) {
      console.warn('Failed to load unified activity', err)
      setError(err.response?.data?.message || 'Unable to connect to backend activity stream')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAllActivity()
    const interval = setInterval(loadAllActivity, 10000)
    return () => clearInterval(interval)
  }, [loadAllActivity])

  // Filter & Sort
  const filteredEvents = useMemo(() => {
    return unifiedEvents
      .filter((item) => {
        if (categoryFilter !== 'ALL' && item.category !== categoryFilter) return false
        if (severityFilter !== 'ALL' && item.severity?.toUpperCase() !== severityFilter) return false
        if (symbolFilter !== 'ALL' && item.symbol?.toUpperCase() !== symbolFilter) return false
        return true
      })
      .sort((a, b) => {
        const timeA = new Date(a.timestamp).getTime()
        const timeB = new Date(b.timestamp).getTime()
        return sortOrder === 'desc' ? timeB - timeA : timeA - timeB
      })
  }, [unifiedEvents, categoryFilter, severityFilter, symbolFilter, sortOrder])

  // Icon Resolver
  const getEventIcon = (severity: string, category: string) => {
    switch (severity?.toUpperCase()) {
      case 'CRITICAL':
      case 'ERROR':
        return <AlertOctagon className="w-4 h-4 text-bearish" />
      case 'WARNING':
        return <AlertTriangle className="w-4 h-4 text-warning" />
      case 'SUCCESS':
        return <CheckCircle2 className="w-4 h-4 text-bullish" />
      default:
        if (category === 'SIGNALS') return <Radio className="w-4 h-4 text-brand-cyan" />
        if (category === 'TRADING') return <BookOpen className="w-4 h-4 text-brand-cyan" />
        if (category === 'RISK') return <ShieldAlert className="w-4 h-4 text-warning" />
        return <Info className="w-4 h-4 text-slate-400" />
    }
  }

  return (
    <div className="space-y-6 max-w-6xl mx-auto pb-12">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Activity className="w-5 h-5 text-brand-cyan" />
            <span>Activity Center & Audit Stream</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Institutional Execution Audit Trail, Algorithmic Lifecycle Events & System Telemetry
          </p>
        </div>

        <div className="flex items-center gap-2">
          {unreadCount > 0 && (
            <button
              onClick={() => markAllAsRead()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-all border border-terminal-border"
            >
              <CheckCheck className="w-3.5 h-3.5 text-brand-cyan" />
              <span>Mark All Read</span>
            </button>
          )}

          <button
            onClick={loadAllActivity}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
            <span>Refresh Activity</span>
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Total Logged Events</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">{unifiedEvents.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Unified audit stream</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Trading & Orders</div>
          <div className="mt-2 text-2xl font-bold font-mono text-brand-cyan">
            {unifiedEvents.filter((e) => e.category === 'TRADING').length}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Orders, fills & positions</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">SMC Signal Events</div>
          <div className="mt-2 text-2xl font-bold font-mono text-bullish">
            {unifiedEvents.filter((e) => e.category === 'SIGNALS').length}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Qualifications & alerts</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Unread System Alerts</div>
          <div className="mt-2 text-2xl font-bold font-mono text-warning">{unreadCount}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Active notifications</div>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="glass-panel p-3 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
        <div className="flex flex-wrap items-center gap-3">
          {/* Category Filter */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400 font-semibold flex items-center gap-1">
              <Filter className="w-3.5 h-3.5" />
              Category:
            </span>
            <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
              {['ALL', 'TRADING', 'SIGNALS', 'RISK', 'SYSTEM'].map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategoryFilter(cat)}
                  className={`px-2.5 py-1 rounded text-[11px] transition-all ${
                    categoryFilter === cat
                      ? 'bg-brand-cyan text-background font-bold shadow-sm'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* Severity Filter */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400">Severity:</span>
            <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
              {['ALL', 'CRITICAL', 'WARNING', 'SUCCESS', 'INFO'].map((sev) => (
                <button
                  key={sev}
                  onClick={() => setSeverityFilter(sev)}
                  className={`px-2 py-0.5 rounded text-[11px] transition-all ${
                    severityFilter === sev
                      ? 'bg-background-elevated text-white font-bold border border-slate-600'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {sev}
                </button>
              ))}
            </div>
          </div>

          {/* Symbol Filter */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400">Symbol:</span>
            <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
              {['ALL', 'BTCUSD', 'ETHUSD', 'SOLUSD'].map((sym) => (
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

        {/* Sort Order Toggle */}
        <button
          onClick={() => setSortOrder((prev) => (prev === 'desc' ? 'asc' : 'desc'))}
          className="flex items-center gap-1 px-2.5 py-1 rounded bg-background/80 border border-terminal-border text-slate-300 hover:text-white transition-colors"
        >
          <ArrowUpDown className="w-3 h-3 text-brand-cyan" />
          <span>{sortOrder === 'desc' ? 'Newest First' : 'Oldest First'}</span>
        </button>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={loadAllActivity}
            className="px-2.5 py-1 rounded bg-bearish/20 text-white font-bold"
          >
            Retry
          </button>
        </div>
      )}

      {/* Main Activity Timeline Stream */}
      <div className="glass-panel rounded-lg overflow-hidden flex flex-col min-h-[420px]">
        <div className="p-3 border-b border-terminal-border/80 flex items-center justify-between bg-background-surface/80 text-xs font-mono text-slate-400">
          <span>Showing {filteredEvents.length} of {unifiedEvents.length} Events</span>
          <span>Tenant Isolated Telemetry</span>
        </div>

        <div className="flex-1 divide-y divide-terminal-border/40 overflow-y-auto">
          {isLoading && unifiedEvents.length === 0 ? (
            <div className="p-12 text-center text-slate-400 font-mono text-xs animate-pulse">
              Loading unified trading & system activity stream...
            </div>
          ) : filteredEvents.length > 0 ? (
            filteredEvents.map((event) => {
              const isUnread = event.isRead === false
              return (
                <div
                  key={event.id}
                  onClick={() => setSelectedEvent(event)}
                  className={`p-4 transition-all flex items-start justify-between gap-4 cursor-pointer hover:bg-background-elevated/40 ${
                    isUnread ? 'bg-brand-cyan/5 border-l-4 border-brand-cyan' : ''
                  }`}
                >
                  <div className="flex items-start gap-3.5 min-w-0">
                    <div className="mt-0.5 p-2 rounded-lg bg-background border border-terminal-border shrink-0">
                      {getEventIcon(event.severity, event.category)}
                    </div>

                    <div className="min-w-0 space-y-1">
                      {/* Top Badges Row */}
                      <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
                        <span className="font-bold text-white text-sm">{event.title}</span>

                        <span className="px-2 py-0.2 rounded bg-background border border-terminal-border text-[10px] text-slate-400">
                          {event.type}
                        </span>

                        {event.symbol && (
                          <span className="px-2 py-0.2 rounded bg-background border border-terminal-border text-[10px] font-bold text-white">
                            {event.symbol}
                          </span>
                        )}

                        {event.direction && (
                          <span
                            className={`px-1.5 py-0.2 rounded text-[10px] font-bold flex items-center gap-0.5 ${
                              event.direction?.toUpperCase() === 'LONG' || event.direction?.toUpperCase() === 'BUY'
                                ? 'bg-bullish/15 text-bullish'
                                : 'bg-bearish/15 text-bearish'
                            }`}
                          >
                            {event.direction?.toUpperCase() === 'LONG' ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />}
                            {event.direction}
                          </span>
                        )}

                        <span
                          className={`px-2 py-0.2 rounded text-[10px] font-bold ${
                            event.severity === 'CRITICAL' || event.severity === 'ERROR'
                              ? 'bg-bearish/20 text-bearish border border-bearish/30'
                              : event.severity === 'WARNING'
                              ? 'bg-warning/20 text-warning border border-warning/30'
                              : event.severity === 'SUCCESS'
                              ? 'bg-bullish/15 text-bullish'
                              : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {event.severity}
                        </span>
                      </div>

                      {/* Event Description */}
                      <p className="text-xs text-slate-300 font-sans leading-relaxed">
                        {event.description}
                      </p>

                      {/* Footer: Timestamp & Reference */}
                      <div className="flex items-center gap-3 text-[11px] font-mono text-slate-500 pt-0.5">
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {new Date(event.timestamp).toLocaleString()}
                        </span>
                        {event.referenceId && (
                          <span className="text-brand-cyan truncate max-w-[200px]">
                            Ref: {event.referenceId}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })
          ) : (
            <div className="py-16 text-center text-slate-500 font-mono text-xs space-y-2">
              <Activity className="w-8 h-8 mx-auto text-slate-600" />
              <div className="font-bold text-slate-400">No Activity Events Found</div>
              <div className="text-[11px] text-slate-500">
                No events match your selected filters. Try broadening your criteria.
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-2.5 border-t border-terminal-border/60 bg-background/50 flex items-center justify-between text-[11px] font-mono text-slate-500">
          <span className="flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-bullish" />
            <span>Audit Invariant: Database events recorded with immutable UTC timestamps</span>
          </span>
          <span className="text-slate-400">Continuous Logging</span>
        </div>
      </div>

      {/* Event Details Dialog Modal */}
      {selectedEvent && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-lg w-full space-y-4 shadow-2xl border border-terminal-border">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">
                  Event Telemetry: {selectedEvent.type}
                </h3>
              </div>
              <button
                onClick={() => setSelectedEvent(null)}
                className="p-1 rounded hover:bg-background text-slate-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="p-3 rounded bg-background/80 border border-terminal-border space-y-1.5">
                <div className="font-bold text-white text-sm">{selectedEvent.title}</div>
                <p className="text-slate-300 font-sans leading-relaxed">{selectedEvent.description}</p>
              </div>

              <div className="grid grid-cols-2 gap-2 p-3 rounded bg-background/60 border border-terminal-border">
                <div>
                  <span className="text-slate-400">Category:</span> <strong className="text-white">{selectedEvent.category}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Severity:</span> <strong className="text-brand-cyan">{selectedEvent.severity}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Timestamp:</span> <strong className="text-white">{new Date(selectedEvent.timestamp).toLocaleString()}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Reference:</span> <strong className="text-brand-cyan truncate">{selectedEvent.referenceId || 'N/A'}</strong>
                </div>
              </div>

              {selectedEvent.raw && (
                <div className="p-3 rounded bg-background/90 border border-terminal-border space-y-1">
                  <div className="text-[10px] text-slate-500 uppercase font-bold">Raw Telemetry Payload</div>
                  <pre className="text-[11px] text-slate-300 overflow-x-auto max-h-40 p-2 bg-background rounded border border-terminal-border/60">
                    {JSON.stringify(selectedEvent.raw, null, 2)}
                  </pre>
                </div>
              )}
            </div>

            <div className="pt-2 flex justify-end">
              <button
                type="button"
                onClick={() => setSelectedEvent(null)}
                className="px-4 py-2 rounded bg-brand-cyan text-background font-bold text-xs hover:bg-brand-cyan/90 transition-colors font-mono"
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
