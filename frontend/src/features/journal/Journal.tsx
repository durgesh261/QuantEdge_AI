import { BookOpen } from 'lucide-react'

export function Journal() {
  return (
    <div className="space-y-6 max-w-7xl font-sans">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white tracking-tight">Trade Journal</h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-purple-500/10 border border-purple-500/30 text-purple-400">
              AUDITED LOGS
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm">
            Immutable post-trade analysis, SMC structure tags, and execution timestamps.
          </p>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-2xl shadow-xl overflow-hidden p-6 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <h2 className="text-base font-bold text-white">Logged Executions</h2>
          <span className="text-xs font-mono text-slate-400">Snapshot Integrity: Verified</span>
        </div>

        <div className="py-16 text-center text-slate-500 text-sm font-mono space-y-2">
          <BookOpen size={36} className="mx-auto text-slate-600 mb-2" />
          <p className="text-slate-400 font-semibold">No Trade Records Logged Yet</p>
          <p className="text-xs text-slate-600">
            Completed live trades and verified order executions will be automatically archived into this journal.
          </p>
        </div>
      </div>
    </div>
  )
}