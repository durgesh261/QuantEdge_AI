import { useEffect, useState } from 'react'
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
  Radio,
  Activity,
  ShieldAlert,
  ShieldCheck,
  Play,
  Square,
  CheckCircle2,
  Lock
} from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'
import { tradeService } from '@/services/tradeService'
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

  const [isActing, setIsActing] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  useEffect(() => {
    fetchStatus()
    fetchSummary()
  }, [fetchStatus, fetchSummary])

  const isConnected = status?.connected || status?.connectionStatus === 'CONNECTED'
  const wsStatus = status?.wsStatus || summary?.wsStatus || (isConnected ? 'CONNECTED' : 'DISCONNECTED')
  const streamHealth = status?.streamHealth || summary?.streamHealth || (isConnected ? 'HEALTHY' : 'OFFLINE')
  const killSwitchActive = status?.killSwitchActive ?? summary?.killSwitchActive ?? true
  const algoEnabled = status?.algoEnabled ?? summary?.algoEnabled ?? false

  const handleActivateKillSwitch = async () => {
    try {
      setIsActing(true)
      setActionMessage(null)
      const res = await tradeService.activateKillSwitch(status?.accountId, 'Manual Operator Trigger')
      setActionMessage(res.message)
      await fetchStatus(status?.accountId)
      await fetchSummary(status?.accountId)
    } catch (err: any) {
      setActionMessage(err.response?.data?.message || err.message || 'Failed to activate kill switch')
    } finally {
      setIsActing(false)
    }
  }

  const handleResetKillSwitch = async () => {
    try {
      setIsActing(true)
      setActionMessage(null)
      const res = await tradeService.resetKillSwitch(status?.accountId)
      setActionMessage(res.message)
      await fetchStatus(status?.accountId)
      await fetchSummary(status?.accountId)
    } catch (err: any) {
      setActionMessage(err.response?.data?.message || err.message || 'Failed to reset kill switch')
    } finally {
      setIsActing(false)
    }
  }

  const handleToggleAlgo = async (enable: boolean) => {
    try {
      setIsActing(true)
      setActionMessage(null)
      const res = await tradeService.toggleAlgo(enable, status?.accountId)
      setActionMessage(res.message)
      await fetchStatus(status?.accountId)
      await fetchSummary(status?.accountId)
    } catch (err: any) {
      setActionMessage(err.response?.data?.message || err.message || 'Failed to toggle algorithmic trading')
    } finally {
      setIsActing(false)
    }
  }

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white tracking-tight">Live Trading Monitor</h1>
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${
                  isConnected
                    ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/80'
                    : 'bg-red-950/80 text-red-400 border border-red-800/80'
                }`}
              >
                <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
                REST: {status?.connectionStatus || 'DISCONNECTED'}
              </span>

              <span
                className={`inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${
                  wsStatus === 'CONNECTED'
                    ? 'bg-blue-950/80 text-blue-400 border border-blue-800/80'
                    : wsStatus === 'STALE'
                    ? 'bg-amber-950/80 text-amber-400 border border-amber-800/80'
                    : 'bg-slate-800 text-slate-400 border border-slate-700'
                }`}
              >
                <Radio size={12} className={wsStatus === 'CONNECTED' ? 'animate-pulse text-blue-400' : ''} />
                Private WS: {wsStatus}
              </span>
            </div>
          </div>
          <p className="text-slate-400 mt-1 text-sm">
            Phase 5.13: Delta Exchange Production Execution, Authoritative OB-Edge SL, 35% Max Loss Dynamic Leverage, 60% ROE TP & Continuous Reconciliation.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {isConnected ? (
            <button
              onClick={() => verifyAccount(status?.accountId)}
              disabled={isSyncing || isActing}
              className="inline-flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-lg border border-slate-700 transition disabled:opacity-50"
            >
              <RefreshCw size={16} className={isSyncing ? 'animate-spin text-blue-400' : ''} />
              {isSyncing ? 'Synchronizing...' : 'Reconcile Delta'}
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

      {/* Safety Control Panel */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={`p-3 rounded-xl ${killSwitchActive ? 'bg-red-950/80 border border-red-800 text-red-400' : 'bg-emerald-950/80 border border-emerald-800 text-emerald-400'}`}>
              {killSwitchActive ? <ShieldAlert size={28} /> : <ShieldCheck size={28} />}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-white">Execution Safety Control</h3>
                <span className={`px-2 py-0.5 text-xs font-mono font-bold rounded ${killSwitchActive ? 'bg-red-950 text-red-300 border border-red-800' : 'bg-emerald-950 text-emerald-300 border border-emerald-800'}`}>
                  KILL SWITCH: {killSwitchActive ? 'ARMED' : 'DISARMED'}
                </span>
                <span className={`px-2 py-0.5 text-xs font-mono font-bold rounded ${algoEnabled ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-amber-950 text-amber-300 border border-amber-800'}`}>
                  ALGO: {algoEnabled ? 'ENABLED' : 'DISABLED'}
                </span>
                <span className="px-2 py-0.5 text-xs font-mono font-bold rounded bg-blue-950 text-blue-300 border border-blue-800">
                  SINGLE TRADE: 100% CAPITAL
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                {killSwitchActive
                  ? 'All automated trade submissions are hard-blocked. SL/TP bracket protection remains active on open positions.'
                  : 'Safety kill switch is disarmed. Algorithmic trade execution permitted when enabled.'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {killSwitchActive ? (
              <button
                onClick={handleResetKillSwitch}
                disabled={isActing}
                className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg shadow transition disabled:opacity-50"
              >
                <Lock size={14} />
                Disarm Kill Switch
              </button>
            ) : (
              <button
                onClick={handleActivateKillSwitch}
                disabled={isActing}
                className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg shadow-lg shadow-red-600/30 transition disabled:opacity-50"
              >
                <Square size={14} />
                Emergency Kill Switch
              </button>
            )}

            {algoEnabled ? (
              <button
                onClick={() => handleToggleAlgo(false)}
                disabled={isActing}
                className="inline-flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-amber-400 border border-slate-700 text-xs font-bold uppercase tracking-wider rounded-lg transition disabled:opacity-50"
              >
                <Square size={14} />
                Disable Algo
              </button>
            ) : (
              <button
                onClick={() => handleToggleAlgo(true)}
                disabled={isActing || killSwitchActive}
                className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg shadow transition disabled:opacity-50"
              >
                <Play size={14} />
                Enable Algo
              </button>
            )}
          </div>
        </div>

        {actionMessage && (
          <div className="mt-4 p-3 bg-slate-800/80 border border-slate-700 rounded-lg text-xs font-mono text-slate-300 flex items-center gap-2">
            <CheckCircle2 size={16} className="text-emerald-400 shrink-0" />
            <span>{actionMessage}</span>
          </div>
        )}
      </div>

      {/* Stream & Protection Notice Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 text-slate-300">
        <div className="flex items-start gap-3">
          <Activity size={20} className="text-emerald-400 shrink-0 mt-0.5" />
          <div className="text-sm">
            <span className="font-semibold text-white">Delta Exchange Production Execution & Protection Architecture</span>
            <p className="text-slate-400 mt-0.5">
              Authoritative SMC Order Block SL, dynamic leverage capped at 35% max planned loss, 60% ROE default TP, and 100% compounded capital per single active trade.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs font-mono text-slate-400 shrink-0 border-t md:border-t-0 pt-2 md:pt-0 border-slate-800">
          <div>
            <span className="text-slate-500 block">Stream Health:</span>
            <span className={streamHealth === 'HEALTHY' ? 'text-emerald-400 font-bold' : 'text-amber-400'}>
              {streamHealth}
            </span>
          </div>
          <div>
            <span className="text-slate-500 block">Daily Loss Guard:</span>
            <span className="text-emerald-400 font-bold">$0.00 / $500.00</span>
          </div>
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
          <span className="text-xs text-slate-500 font-mono">Stream Synchronized</span>
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
          <span className="text-xs text-slate-500 font-mono">Stream Synchronized</span>
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