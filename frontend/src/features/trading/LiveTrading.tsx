import { useEffect } from 'react'
import {
  TrendingUp,
  DollarSign,
  Briefcase,
  Clock,
  RefreshCw,
  AlertCircle,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  Info
} from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'
import { Link } from 'react-router-dom'

export function LiveTrading() {
  const {
    status,
    summary,
    positions,
    openOrders,
    isSyncing,
    error,
    fetchStatus,
    fetchSummary,
    verifyAccount,
  } = useAccountStore()

  useEffect(() => {
    fetchStatus()
    fetchSummary()
  }, [fetchStatus, fetchSummary])

  const isConnected = status?.connected || status?.connectionStatus === 'CONNECTED'

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white tracking-tight">Live Trading Monitor</h1>
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${
                isConnected
                  ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/80'
                  : 'bg-red-950/80 text-red-400 border border-red-800/80'
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
              {status?.connectionStatus || 'DISCONNECTED'}
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm">
            Real-time read-only live account and order state synchronized from Delta Exchange India.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {isConnected ? (
            <button
              onClick={() => verifyAccount(status?.accountId)}
              disabled={isSyncing}
              className="inline-flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-lg border border-slate-700 transition disabled:opacity-50"
            >
              <RefreshCw size={16} className={isSyncing ? 'animate-spin text-blue-400' : ''} />
              {isSyncing ? 'Synchronizing...' : 'Refresh Live Data'}
            </button>
          ) : (
            <Link
              to="/settings"
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-blue-600/20 transition"
            >
              Connect Delta Account
            </Link>
          )}
        </div>
      </div>

      {/* Real Trading Notice Banner */}
      <div className="bg-blue-950/30 border border-blue-800/60 rounded-xl p-4 flex items-start gap-3 text-blue-300">
        <Info size={20} className="text-blue-400 shrink-0 mt-0.5" />
        <div className="text-sm">
          <span className="font-semibold text-blue-200">Phase 5.5 Active Mode: Read-Only Live Account Verification</span>
          <p className="text-blue-300/90 mt-0.5">
            This dashboard displays authoritative live exchange data from Delta Exchange India. Automated execution is held in safe mode (<strong className="text-white">Algo Disabled / Kill-Switch Armed</strong>). Zero live orders are placed in this phase.
          </p>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="bg-red-950/40 border border-red-800/80 rounded-xl p-4 flex items-center gap-3 text-red-300">
          <AlertCircle size={20} className="text-red-400 shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {/* Live Financial Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Total Equity</span>
            <DollarSign size={20} className="text-emerald-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">
            ${summary?.totalEquity != null ? summary.totalEquity.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
          </p>
          <span className="text-xs text-slate-500 mt-1 block">Base Currency: {summary?.baseCurrency || 'USDT'}</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Available Balance</span>
            <TrendingUp size={20} className="text-blue-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">
            ${summary?.availableBalance != null ? summary.availableBalance.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
          </p>
          <span className="text-xs text-slate-500 mt-1 block">Free margin for trades</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Margin Used</span>
            <Layers size={20} className="text-amber-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">
            ${summary?.marginUsed != null ? summary.marginUsed.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
          </p>
          <span className="text-xs text-slate-500 mt-1 block">Position & order collateral</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Active Positions</span>
            <Briefcase size={20} className="text-purple-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">
            {positions.length}
          </p>
          <span className="text-xs text-slate-500 mt-1 block">{openOrders.length} open orders</span>
        </div>
      </div>

      {/* Live Positions Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Briefcase size={18} className="text-purple-400" />
            <h2 className="font-bold text-white text-base">Live Margined Positions ({positions.length})</h2>
          </div>
          <span className="text-xs text-slate-500 font-mono">Authoritative Delta State</span>
        </div>

        {positions.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            <p>No active derivative positions on Delta Exchange India.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-950/60 text-xs font-semibold uppercase text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Symbol</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Entry Price</th>
                  <th className="py-3 px-4">Mark Price</th>
                  <th className="py-3 px-4">Unrealized PnL</th>
                  <th className="py-3 px-4">Leverage</th>
                  <th className="py-3 px-4">Margin</th>
                  <th className="py-3 px-4">Liq. Price</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono">
                {positions.map((pos, idx) => {
                  const isPositive = pos.unrealizedPnl >= 0
                  return (
                    <tr key={idx} className="hover:bg-slate-800/40 transition">
                      <td className="py-3.5 px-4 font-bold text-white">{pos.symbol}</td>
                      <td className="py-3.5 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold ${
                            pos.side === 'LONG'
                              ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/80'
                              : 'bg-red-950 text-red-400 border border-red-800/80'
                          }`}
                        >
                          {pos.side === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                          {pos.side}
                        </span>
                      </td>
                      <td className="py-3.5 px-4">{pos.size}</td>
                      <td className="py-3.5 px-4">${pos.entryPrice?.toLocaleString()}</td>
                      <td className="py-3.5 px-4 text-slate-200">${pos.markPrice?.toLocaleString()}</td>
                      <td className={`py-3.5 px-4 font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                        {isPositive ? '+' : ''}${pos.unrealizedPnl?.toLocaleString()}
                      </td>
                      <td className="py-3.5 px-4">{pos.leverage}x</td>
                      <td className="py-3.5 px-4">${pos.margin?.toLocaleString()}</td>
                      <td className="py-3.5 px-4 text-red-300">
                        {pos.liquidationPrice ? `$${pos.liquidationPrice.toLocaleString()}` : '-'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Live Open Orders Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Clock size={18} className="text-blue-400" />
            <h2 className="font-bold text-white text-base">Open Orders ({openOrders.length})</h2>
          </div>
          <span className="text-xs text-slate-500 font-mono">Delta India Order Book</span>
        </div>

        {openOrders.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            <p>No open orders currently pending on Delta Exchange India.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-950/60 text-xs font-semibold uppercase text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Order ID</th>
                  <th className="py-3 px-4">Symbol</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Type</th>
                  <th className="py-3 px-4">Price</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Unfilled</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Created At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono text-xs">
                {openOrders.map((ord) => (
                  <tr key={ord.id} className="hover:bg-slate-800/40 transition">
                    <td className="py-3 px-4 text-slate-400">{ord.id}</td>
                    <td className="py-3 px-4 font-bold text-white">{ord.symbol}</td>
                    <td className="py-3 px-4">
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-bold ${
                          ord.side === 'BUY' ? 'bg-emerald-950 text-emerald-400' : 'bg-red-950 text-red-400'
                        }`}
                      >
                        {ord.side}
                      </span>
                    </td>
                    <td className="py-3 px-4">{ord.orderType}</td>
                    <td className="py-3 px-4 font-semibold text-slate-200">
                      {ord.limitPrice ? `$${ord.limitPrice.toLocaleString()}` : 'MARKET'}
                    </td>
                    <td className="py-3 px-4">{ord.size}</td>
                    <td className="py-3 px-4">{ord.unfilledSize}</td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-0.5 bg-yellow-950/80 text-yellow-400 rounded">
                        {ord.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-400">{ord.createdAt || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}