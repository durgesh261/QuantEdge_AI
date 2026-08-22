import { useState, useEffect } from 'react'
import {
  Server,
  AlertTriangle,
  RefreshCw
} from 'lucide-react'
import { developerService, AccountHealthSummary } from '@/services/developerService'

export function SystemHealth() {
  const [accounts, setAccounts] = useState<AccountHealthSummary[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAccounts = async () => {
    try {
      setIsLoading(true)
      setError(null)
      const data = await developerService.getAccountsHealthSummary()
      setAccounts(data)
    } catch (err: any) {
      setError(err.message || 'Failed to fetch accounts health summary')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchAccounts()
  }, [])

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight font-mono">
              System & Multi-Account Health
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-amber-500/10 border border-amber-500/30 text-amber-400">
              AUDIT OVERVIEW
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm font-mono">
            System-wide account statuses, fail-safe kill switch flags, and balance health.
          </p>
        </div>
        <button
          onClick={fetchAccounts}
          disabled={isLoading}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-amber-300 text-xs font-mono font-bold rounded-lg border border-amber-500/30 transition disabled:opacity-50"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          {isLoading ? 'Scanning Accounts...' : 'Refresh Accounts'}
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 font-mono text-xs flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Summary Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 font-mono">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <p className="text-xs uppercase font-bold text-slate-400">Registered Accounts</p>
          <p className="text-2xl font-bold text-white mt-1">{accounts.length}</p>
          <p className="text-[11px] text-slate-500 mt-1">Multi-tenant Database Entries</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <p className="text-xs uppercase font-bold text-slate-400">Kill Switch Protected</p>
          <p className="text-2xl font-bold text-amber-400 mt-1">
            {accounts.filter(a => a.killSwitchActive).length} / {accounts.length}
          </p>
          <p className="text-[11px] text-slate-500 mt-1">Active Safety Halts</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <p className="text-xs uppercase font-bold text-slate-400">Algo Trading Status</p>
          <p className="text-2xl font-bold text-cyan-400 mt-1">
            {accounts.filter(a => a.algoEnabled).length} Enabled
          </p>
          <p className="text-[11px] text-slate-500 mt-1">Automated Execution Authorizations</p>
        </div>
      </div>

      {/* Accounts Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4 font-mono">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-amber-400" />
            <h2 className="text-base font-bold text-white">Accounts Inventory</h2>
          </div>
          <span className="text-xs text-slate-500">Credentials Excluded</span>
        </div>

        {accounts.length === 0 ? (
          <div className="py-12 text-center text-slate-500 text-xs">
            No accounts currently registered in the database.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                  <th className="pb-3 font-bold">Account Name / ID</th>
                  <th className="pb-3 font-bold">Type</th>
                  <th className="pb-3 font-bold">Algo Status</th>
                  <th className="pb-3 font-bold">Kill Switch</th>
                  <th className="pb-3 font-bold">Balance</th>
                  <th className="pb-3 font-bold">Last Synced</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {accounts.map(a => (
                  <tr key={a.accountId} className="hover:bg-slate-950/40 transition">
                    <td className="py-3">
                      <span className="font-bold text-white block">{a.name}</span>
                      <span className="text-slate-500 text-[10px] block">{a.accountId}</span>
                    </td>
                    <td className="py-3">
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300">
                        {a.environment}
                      </span>
                    </td>
                    <td className="py-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        a.algoEnabled
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : 'bg-slate-800 text-slate-400'
                      }`}>
                        {a.algoEnabled ? 'ENABLED' : 'DISABLED'}
                      </span>
                    </td>
                    <td className="py-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        a.killSwitchActive
                          ? 'bg-amber-950 text-amber-400 border border-amber-800'
                          : 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                      }`}>
                        {a.killSwitchActive ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                    </td>
                    <td className="py-3 text-emerald-400 font-bold">
                      ${a.currentBalance?.toLocaleString() ?? '0.00'}
                    </td>
                    <td className="py-3 text-slate-400 text-[11px]">
                      {a.lastSyncedAt ? new Date(a.lastSyncedAt).toLocaleString() : 'Never'}
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
