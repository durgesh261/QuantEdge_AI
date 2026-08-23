import { useEffect } from 'react'
import { Briefcase, RefreshCw, AlertCircle, ArrowUpRight, ArrowDownRight } from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'

export function Positions() {
  const { positions, isSyncing, error, fetchSummary, status } = useAccountStore()

  useEffect(() => {
    fetchSummary()
  }, [fetchSummary])

  return (
    <div className="space-y-6 max-w-7xl font-sans">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white tracking-tight">Active Positions</h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
              {positions.length} ACTIVE
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm">
            Live open contracts, mark valuations, and protective stop margins on Delta Exchange India.
          </p>
        </div>

        <button
          onClick={() => fetchSummary(status?.accountId)}
          disabled={isSyncing}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-emerald-400 text-xs font-mono font-bold rounded-lg border border-slate-800 hover:border-slate-700 transition disabled:opacity-50 self-start sm:self-auto"
        >
          <RefreshCw size={14} className={isSyncing ? 'animate-spin' : ''} />
          {isSyncing ? 'Synchronizing...' : 'Refresh Positions'}
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 text-xs font-mono flex items-center gap-3">
          <AlertCircle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-xl overflow-hidden">
        {positions.length === 0 ? (
          <div className="py-16 text-center text-slate-500 text-sm font-mono space-y-2">
            <Briefcase size={36} className="mx-auto text-slate-600 mb-2" />
            <p className="text-slate-400 font-semibold">No Open Positions</p>
            <p className="text-xs text-slate-600">Your account is currently completely flat with zero market exposure.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 bg-slate-950/60 text-slate-400 uppercase text-[10px] tracking-wider">
                  <th className="py-3.5 px-4 font-bold">Contract</th>
                  <th className="py-3.5 px-4 font-bold">Side</th>
                  <th className="py-3.5 px-4 font-bold">Size</th>
                  <th className="py-3.5 px-4 font-bold">Entry Price</th>
                  <th className="py-3.5 px-4 font-bold">Mark Price</th>
                  <th className="py-3.5 px-4 font-bold">Leverage</th>
                  <th className="py-3.5 px-4 font-bold">Margin</th>
                  <th className="py-3.5 px-4 font-bold">Unrealized PnL</th>
                  <th className="py-3.5 px-4 font-bold">Liquidation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {positions.map((pos, idx) => (
                  <tr key={idx} className="hover:bg-slate-950/40 transition">
                    <td className="py-3.5 px-4 text-white font-bold">{pos.symbol}</td>
                    <td className="py-3.5 px-4">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${
                          pos.side === 'LONG'
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            : 'bg-red-950 text-red-400 border border-red-800'
                        }`}
                      >
                        {pos.side === 'LONG' ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                        {pos.side}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 text-slate-200">{pos.size} contracts</td>
                    <td className="py-3.5 px-4 text-slate-300 font-bold">${pos.entryPrice?.toLocaleString()}</td>
                    <td className="py-3.5 px-4 text-slate-300 font-bold">${pos.markPrice?.toLocaleString()}</td>
                    <td className="py-3.5 px-4 text-indigo-400 font-bold">{pos.leverage}x</td>
                    <td className="py-3.5 px-4 text-slate-400">${pos.margin?.toLocaleString()}</td>
                    <td className="py-3.5 px-4">
                      <span className={`font-bold ${pos.unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl?.toLocaleString()}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 text-amber-400">
                      {pos.liquidationPrice ? `$${pos.liquidationPrice.toLocaleString()}` : '--'}
                    </td>
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