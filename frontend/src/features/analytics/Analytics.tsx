import { BarChart3, TrendingUp, Percent, ShieldCheck, DollarSign, Activity } from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'

export function Analytics() {
  const { summary } = useAccountStore()

  return (
    <div className="space-y-6 max-w-7xl font-sans">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white tracking-tight">Performance Analytics</h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-purple-500/10 border border-purple-500/30 text-purple-400">
              LIVE METRICS
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm">
            Quantitative metrics, risk-reward ratios, and equity curve telemetry on Delta Exchange India.
          </p>
        </div>
      </div>

      {/* Metrics Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Total Equity</span>
            <DollarSign size={20} className="text-emerald-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">
            ${summary?.totalEquity != null ? summary.totalEquity.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
          </p>
          <span className="text-xs text-slate-500 mt-1 block">Live Collateral Valuation</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Win Rate</span>
            <Percent size={20} className="text-blue-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">0.0%</p>
          <span className="text-xs text-slate-500 mt-1 block">Based on completed trades</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Profit Factor</span>
            <TrendingUp size={20} className="text-purple-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-white mt-2">--</p>
          <span className="text-xs text-slate-500 mt-1 block">Gross Profit / Gross Loss</span>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase font-bold text-slate-400 tracking-wider">Max Drawdown</span>
            <ShieldCheck size={20} className="text-amber-400" />
          </div>
          <p className="text-2xl font-bold font-mono text-emerald-400 mt-2">0.00%</p>
          <span className="text-xs text-slate-500 mt-1 block">Peak-to-trough decline</span>
        </div>
      </div>

      {/* Analytics Summary Panel */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-xl p-6 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2">
            <BarChart3 size={20} className="text-purple-400" />
            <h2 className="text-base font-bold text-white">Equity & Trade Distribution</h2>
          </div>
          <span className="text-xs font-mono text-slate-400">Delta India Live Stream</span>
        </div>

        <div className="py-16 text-center text-slate-500 text-sm font-mono space-y-2">
          <Activity size={36} className="mx-auto text-slate-600 mb-2" />
          <p className="text-slate-400 font-semibold">Accumulating Execution Telemetry</p>
          <p className="text-xs text-slate-600">
            Equity curve visualizations and risk distribution models will populate as trades are executed.
          </p>
        </div>
      </div>
    </div>
  )
}