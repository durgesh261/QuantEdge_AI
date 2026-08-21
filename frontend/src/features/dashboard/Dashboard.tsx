import { useEffect } from 'react'
import { TrendingUp, DollarSign, BarChart3, Briefcase, Settings as SettingsIcon, ShieldCheck } from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'
import { Link } from 'react-router-dom'

export function Dashboard() {
  const { status, summary, positions, fetchStatus, fetchSummary } = useAccountStore()

  useEffect(() => {
    fetchStatus()
    fetchSummary()
  }, [fetchStatus, fetchSummary])

  const isConnected = status?.connected || status?.connectionStatus === 'CONNECTED'

  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Trading Dashboard</h1>
          <p className="text-slate-400 mt-1 text-sm">Real-trading quantitative intelligence overview.</p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider ${
              isConnected
                ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/80'
                : 'bg-slate-800 text-slate-400 border border-slate-700'
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`} />
            {isConnected ? 'Delta Live Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      {/* Primary Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Total Equity</p>
              <p className="text-2xl font-bold font-mono text-white mt-1">
                ${summary?.totalEquity != null ? summary.totalEquity.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
              </p>
              <p className="text-xs text-slate-500 mt-1">USDT Live Collateral</p>
            </div>
            <div className="p-3 bg-emerald-950/50 border border-emerald-800/60 rounded-xl text-emerald-400">
              <DollarSign size={24} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Available Balance</p>
              <p className="text-2xl font-bold font-mono text-white mt-1">
                ${summary?.availableBalance != null ? summary.availableBalance.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
              </p>
              <p className="text-xs text-slate-500 mt-1">Free Margin</p>
            </div>
            <div className="p-3 bg-blue-950/50 border border-blue-800/60 rounded-xl text-blue-400">
              <TrendingUp size={24} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Open Positions</p>
              <p className="text-2xl font-bold font-mono text-white mt-1">{positions.length}</p>
              <p className="text-xs text-slate-500 mt-1">Active Exposure</p>
            </div>
            <div className="p-3 bg-purple-950/50 border border-purple-800/60 rounded-xl text-purple-400">
              <Briefcase size={24} />
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Safety Status</p>
              <p className="text-lg font-bold text-amber-400 mt-1">Safe Mode</p>
              <p className="text-xs text-slate-500 mt-1">Algo Disabled • Kill Switch Active</p>
            </div>
            <div className="p-3 bg-amber-950/50 border border-amber-800/60 rounded-xl text-amber-400">
              <ShieldCheck size={24} />
            </div>
          </div>
        </div>
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Positions & Orders Summary */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <h2 className="text-lg font-bold text-white">Active Positions Overview</h2>
            <Link to="/trading" className="text-xs text-blue-400 hover:text-blue-300 font-semibold">
              View All →
            </Link>
          </div>

          {positions.length === 0 ? (
            <div className="py-12 text-center text-slate-500 text-sm">
              No open positions currently active on Delta Exchange India.
            </div>
          ) : (
            <div className="space-y-3">
              {positions.slice(0, 4).map((pos, idx) => (
                <div key={idx} className="flex items-center justify-between p-3.5 bg-slate-950/50 rounded-xl border border-slate-800/80 font-mono text-sm">
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-bold ${
                        pos.side === 'LONG' ? 'bg-emerald-950 text-emerald-400' : 'bg-red-950 text-red-400'
                      }`}
                    >
                      {pos.side}
                    </span>
                    <span className="font-bold text-white">{pos.symbol}</span>
                    <span className="text-slate-400 text-xs">{pos.size} contracts</span>
                  </div>
                  <div className="text-right">
                    <span className={`font-bold ${pos.unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl?.toLocaleString()}
                    </span>
                    <span className="block text-xs text-slate-500">Mark: ${pos.markPrice?.toLocaleString()}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Quick Actions */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
          <h2 className="text-lg font-bold text-white border-b border-slate-800 pb-4">Quick Navigation</h2>
          <div className="grid grid-cols-1 gap-3">
            <Link
              to="/trading"
              className="p-4 bg-slate-950/70 border border-slate-800 hover:border-blue-500 hover:bg-slate-800/50 rounded-xl transition flex items-center gap-3.5 group"
            >
              <div className="p-2.5 rounded-lg bg-blue-600/20 text-blue-400 group-hover:bg-blue-600/30">
                <TrendingUp size={20} />
              </div>
              <div>
                <p className="font-semibold text-white text-sm">Live Trading Monitor</p>
                <p className="text-xs text-slate-400">Read-only account state & positions</p>
              </div>
            </Link>

            <Link
              to="/settings"
              className="p-4 bg-slate-950/70 border border-slate-800 hover:border-emerald-500 hover:bg-slate-800/50 rounded-xl transition flex items-center gap-3.5 group"
            >
              <div className="p-2.5 rounded-lg bg-emerald-600/20 text-emerald-400 group-hover:bg-emerald-600/30">
                <SettingsIcon size={20} />
              </div>
              <div>
                <p className="font-semibold text-white text-sm">Exchange Settings</p>
                <p className="text-xs text-slate-400">Manage Delta India connection & keys</p>
              </div>
            </Link>

            <div className="p-4 bg-slate-950/40 border border-slate-800/60 rounded-xl flex items-center gap-3.5 text-slate-400">
              <div className="p-2.5 rounded-lg bg-purple-600/10 text-purple-400">
                <BarChart3 size={20} />
              </div>
              <div>
                <p className="font-semibold text-slate-300 text-sm">Phase 5.5 Active</p>
                <p className="text-xs text-slate-500">Read-Only Live Verification</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}