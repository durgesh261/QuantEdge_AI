import { useState, useEffect } from 'react'
import {
  ShieldCheck,
  Key,
  RefreshCw,
  PowerOff,
  CheckCircle2,
  AlertTriangle,
  Lock,
  DollarSign,
  Sliders,
  Save,
  Layers,
  Zap,
  AlertOctagon,
} from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'
import { accountService, AlgoConfigResponse } from '@/services/accountService'
import { tradeService } from '@/services/tradeService'

export function Settings() {
  const {
    status,
    summary,
    isLoading,
    isConnecting,
    isSyncing,
    error,
    fetchStatus,
    fetchSummary,
    connectAccount,
    verifyAccount,
    disconnectAccount,
    clearError,
  } = useAccountStore()

  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [accountName, setAccountName] = useState('Delta Live Account')
  const [showConnectModal, setShowConnectModal] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // Algo Trading Configuration State
  const [algoConfig, setAlgoConfig] = useState<AlgoConfigResponse | null>(null)
  const [takeProfitPct, setTakeProfitPct] = useState<number>(2.0)
  const [stopLossPct, setStopLossPct] = useState<number>(1.0)
  const [riskPerTradePct, setRiskPerTradePct] = useState<number>(1.0)
  const [maxDailyLossPct, setMaxDailyLossPct] = useState<number>(5.0)
  const [maxLeverage, setMaxLeverage] = useState<number>(100)
  const [isSavingConfig, setIsSavingConfig] = useState(false)
  const [configSuccessMsg, setConfigSuccessMsg] = useState<string | null>(null)
  const [configErrorMsg, setConfigErrorMsg] = useState<string | null>(null)
  const [isTogglingAlgo, setIsTogglingAlgo] = useState(false)
  const [isTogglingKillSwitch, setIsTogglingKillSwitch] = useState(false)

  useEffect(() => {
    fetchStatus()
    fetchSummary()
    fetchAlgoConfig()
  }, [fetchStatus, fetchSummary])

  const fetchAlgoConfig = async () => {
    try {
      const res = await accountService.getAlgoConfig(status?.accountId)
      if (res.success) {
        setAlgoConfig(res)
        setTakeProfitPct(res.takeProfitPercent)
        setStopLossPct(res.stopLossPercent)
        setRiskPerTradePct(res.riskPerTradePercent)
        setMaxDailyLossPct(res.maxDailyLossPercent)
        setMaxLeverage(res.maxLeverage)
      }
    } catch (e) {
      // Ignored if account not yet connected
    }
  }

  const handleSaveAlgoConfig = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSavingConfig(true)
    setConfigSuccessMsg(null)
    setConfigErrorMsg(null)

    try {
      const res = await accountService.updateAlgoConfig({
        accountId: status?.accountId,
        takeProfitPercent: takeProfitPct,
        stopLossPercent: stopLossPct,
        riskPerTradePercent: riskPerTradePct,
        maxDailyLossPercent: maxDailyLossPct,
        maxLeverage: maxLeverage,
      })

      if (res.success) {
        setAlgoConfig(res)
        setConfigSuccessMsg(`Configuration updated to Version ${res.version}. New trades will use these parameters.`)
      } else {
        setConfigErrorMsg(res.message || 'Failed to update algorithm configuration')
      }
    } catch (err: any) {
      setConfigErrorMsg(err.response?.data?.message || err.message || 'Error updating configuration')
    } finally {
      setIsSavingConfig(false)
    }
  }

  const handleToggleAlgo = async () => {
    if (!status?.accountId) return
    setIsTogglingAlgo(true)
    try {
      const newEnabled = !status.algoEnabled
      await tradeService.toggleAlgo(newEnabled, status.accountId)
      await fetchStatus(status.accountId)
    } catch (err: any) {
      setConfigErrorMsg(err.response?.data?.message || err.message || 'Failed to toggle algorithm trading')
    } finally {
      setIsTogglingAlgo(false)
    }
  }

  const handleToggleKillSwitch = async () => {
    if (!status?.accountId) return
    setIsTogglingKillSwitch(true)
    try {
      if (status.killSwitchActive) {
        await tradeService.resetKillSwitch(status.accountId)
      } else {
        await tradeService.activateKillSwitch(status.accountId, 'Operator manually triggered emergency kill switch')
      }
      await fetchStatus(status.accountId)
    } catch (err: any) {
      setConfigErrorMsg(err.response?.data?.message || err.message || 'Failed to update kill switch state')
    } finally {
      setIsTogglingKillSwitch(false)
    }
  }

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault()
    setFormError(null)

    if (!apiKey.trim() || !apiSecret.trim()) {
      setFormError('Please enter both API Key and API Secret')
      return
    }

    const success = await connectAccount({
      apiKey: apiKey.trim(),
      apiSecret: apiSecret.trim(),
      name: accountName.trim() || 'Delta Live Account',
    })

    if (success) {
      setShowConnectModal(false)
      setApiKey('')
      setApiSecret('')
      fetchAlgoConfig()
    }
  }

  const handleDisconnect = async () => {
    if (window.confirm('Are you sure you want to disconnect your Delta Exchange India account? Automated trading will be deactivated.')) {
      await disconnectAccount(status?.accountId)
      setAlgoConfig(null)
    }
  }

  const isConnected = status?.connected ?? false

  return (
    <div className="space-y-8 max-w-5xl font-sans">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-white tracking-tight">Settings & Integration</h1>
        <p className="text-slate-400 mt-1 text-sm">
          Manage exchange connectivity, automated algorithm risk brackets, and emergency controls for Delta Exchange India.
        </p>
      </div>

      {/* Global Errors */}
      {error && (
        <div className="p-4 rounded-xl bg-red-950/50 border border-red-800 text-red-300 flex items-center justify-between text-sm">
          <div className="flex items-center gap-3">
            <AlertTriangle className="text-red-400 shrink-0" size={18} />
            <span>{error}</span>
          </div>
          <button onClick={clearError} className="text-xs text-red-400 hover:text-red-300 font-bold uppercase tracking-wider">
            Dismiss
          </button>
        </div>
      )}

      {/* SECTION 1: Delta Exchange Connection */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-600/10 text-blue-400 border border-blue-500/20">
              <Key size={22} />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Delta Exchange India Connection</h2>
              <p className="text-xs text-slate-400">REST & WebSocket API Integration</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {isConnected ? (
              <>
                <button
                  onClick={() => verifyAccount(status?.accountId)}
                  disabled={isSyncing}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold rounded-lg border border-slate-700 transition disabled:opacity-50"
                >
                  <RefreshCw size={13} className={isSyncing ? 'animate-spin text-blue-400' : ''} />
                  {isSyncing ? 'Verifying...' : 'Re-verify'}
                </button>
                <button
                  onClick={handleDisconnect}
                  disabled={isLoading}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-red-950/40 hover:bg-red-900/60 text-red-400 text-xs font-bold rounded-lg border border-red-800/80 transition disabled:opacity-50"
                >
                  <PowerOff size={13} />
                  Disconnect
                </button>
              </>
            ) : (
              <button
                onClick={() => setShowConnectModal(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg shadow-lg shadow-blue-600/20 transition"
              >
                <Key size={14} />
                Connect Delta Account
              </button>
            )}
          </div>
        </div>

        {/* Connection Status Details */}
        {isConnected ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-1">
                <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Connection Status</span>
                <div className="flex items-center gap-2 pt-1">
                  <CheckCircle2 size={16} className="text-emerald-400" />
                  <span className="text-sm font-bold text-emerald-400">Connected & Verified</span>
                </div>
                <p className="text-[11px] text-slate-500 font-mono">Account ID: {status?.accountId}</p>
              </div>

              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-1">
                <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Active API Key</span>
                <div className="flex items-center gap-2 pt-1">
                  <Lock size={15} className="text-blue-400" />
                  <span className="text-sm font-mono text-slate-200">{status?.maskedApiKey || '••••••••'}</span>
                </div>
                <p className="text-[11px] text-slate-500">Encrypted AES-256-GCM Server-Side</p>
              </div>

              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-1">
                <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Collateral & Margin</span>
                <div className="flex items-center gap-2 pt-1">
                  <DollarSign size={16} className="text-purple-400" />
                  <span className="text-sm font-mono font-bold text-white">
                    ${summary?.totalEquity != null ? summary.totalEquity.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
                  </span>
                </div>
                <p className="text-[11px] text-slate-500">
                  Avail: ${summary?.availableBalance != null ? summary.availableBalance.toLocaleString(undefined, { minimumFractionDigits: 2 }) : '0.00'}
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between p-3 rounded-lg bg-blue-950/20 border border-blue-900/40 text-xs text-blue-300">
              <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-blue-400 shrink-0" />
                <span>Private credentials are encrypted and held in-memory server-side. Zero raw secret exposure.</span>
              </div>
              <span className="font-mono text-[10px] text-slate-400">TLS 1.3 Strict</span>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center space-y-3">
            <div className="inline-flex p-3 rounded-full bg-slate-800/80 text-slate-400 mb-1">
              <Key size={24} />
            </div>
            <h3 className="text-sm font-bold text-white">No Delta India Account Connected</h3>
            <p className="text-xs text-slate-400 max-w-md mx-auto leading-relaxed">
              Connect your Delta Exchange India API credentials to enable automated algorithmic trading and live risk reconciliation.
            </p>
            <button
              onClick={() => setShowConnectModal(true)}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg shadow-lg shadow-blue-600/20 transition"
            >
              Connect Account Now
            </button>
          </div>
        )}
      </div>

      {/* SECTION 2: Trading Controls (Algo Toggle & Kill Switch) */}
      {isConnected && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Algo Trading State */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2.5">
                <Zap size={20} className={status?.algoEnabled ? 'text-emerald-400' : 'text-slate-400'} />
                <h3 className="text-sm font-bold text-white">Automated Algorithmic Execution</h3>
              </div>
              <span
                className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                  status?.algoEnabled ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-800 text-slate-400'
                }`}
              >
                {status?.algoEnabled ? 'EXECUTION ENABLED' : 'ALGORITHM DISABLED'}
              </span>
            </div>

            <p className="text-xs text-slate-400 leading-relaxed">
              When enabled, verified strategy signals meeting authoritative risk qualification will automatically execute on Delta Exchange India.
            </p>

            <button
              onClick={handleToggleAlgo}
              disabled={isTogglingAlgo}
              className={`w-full py-2.5 px-4 rounded-xl text-xs font-bold transition flex items-center justify-center gap-2 ${
                status?.algoEnabled
                  ? 'bg-amber-950/40 hover:bg-amber-900/60 text-amber-400 border border-amber-800/80'
                  : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20'
              }`}
            >
              {isTogglingAlgo ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : status?.algoEnabled ? (
                'Disable Automated Execution'
              ) : (
                'Enable Automated Execution'
              )}
            </button>
          </div>

          {/* Emergency Kill Switch */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2.5">
                <AlertOctagon size={20} className={status?.killSwitchActive ? 'text-red-400' : 'text-slate-400'} />
                <h3 className="text-sm font-bold text-white">Emergency Kill Switch</h3>
              </div>
              <span
                className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                  status?.killSwitchActive ? 'bg-red-950 text-red-400 border border-red-800' : 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                }`}
              >
                {status?.killSwitchActive ? 'KILL SWITCH ACTIVE' : 'KILL SWITCH INACTIVE'}
              </span>
            </div>

            <p className="text-xs text-slate-400 leading-relaxed">
              When activated, all real order execution is immediately blocked server-side regardless of strategy signals.
            </p>

            <button
              onClick={handleToggleKillSwitch}
              disabled={isTogglingKillSwitch}
              className={`w-full py-2.5 px-4 rounded-xl text-xs font-bold transition flex items-center justify-center gap-2 ${
                status?.killSwitchActive
                  ? 'bg-blue-600 hover:bg-blue-500 text-white'
                  : 'bg-red-950/60 hover:bg-red-900/80 text-red-300 border border-red-800/80'
              }`}
            >
              {isTogglingKillSwitch ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : status?.killSwitchActive ? (
                'Reset Kill Switch (Restore Trading)'
              ) : (
                'Activate Emergency Kill Switch'
              )}
            </button>
          </div>
        </div>
      )}

      {/* SECTION 3: Algorithm Risk & Bracket Configuration */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-purple-600/10 text-purple-400 border border-purple-500/20">
              <Sliders size={22} />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Algorithm Trading Configuration</h2>
              <p className="text-xs text-slate-400">Authoritative Risk Parameters & Sizing</p>
            </div>
          </div>
          {algoConfig && (
            <span className="text-xs font-mono bg-slate-800 text-slate-300 px-2.5 py-1 rounded-md border border-slate-700">
              Config v{algoConfig.version}
            </span>
          )}
        </div>

        {configSuccessMsg && (
          <div className="p-3 rounded-lg bg-emerald-950/40 border border-emerald-800 text-emerald-400 text-xs font-medium flex items-center gap-2">
            <CheckCircle2 size={16} />
            <span>{configSuccessMsg}</span>
          </div>
        )}

        {configErrorMsg && (
          <div className="p-3 rounded-lg bg-red-950/40 border border-red-800 text-red-400 text-xs font-medium flex items-center gap-2">
            <AlertTriangle size={16} />
            <span>{configErrorMsg}</span>
          </div>
        )}

        <form onSubmit={handleSaveAlgoConfig} className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="text-xs font-bold text-slate-300 flex items-center justify-between">
                <span>Risk Per Trade (% of Equity)</span>
                <span className="text-purple-400 font-mono">{riskPerTradePct}%</span>
              </label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="5.0"
                value={riskPerTradePct}
                onChange={(e) => setRiskPerTradePct(parseFloat(e.target.value) || 0)}
                disabled={!isConnected}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-purple-500 disabled:opacity-50"
              />
              <p className="text-[11px] text-slate-500">Authoritative position sizing based on risk distance to OB-Edge.</p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-bold text-slate-300 flex items-center justify-between">
                <span>Max Leverage Limit</span>
                <span className="text-purple-400 font-mono">{maxLeverage}x</span>
              </label>
              <input
                type="number"
                step="1"
                min="1"
                max="100"
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(parseInt(e.target.value) || 1)}
                disabled={!isConnected}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-purple-500 disabled:opacity-50"
              />
              <p className="text-[11px] text-slate-500">Hard leverage cap enforced during order calculation.</p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-bold text-slate-300 flex items-center justify-between">
                <span>Authoritative Take Profit Ratio (% Target)</span>
                <span className="text-purple-400 font-mono">{takeProfitPct}%</span>
              </label>
              <input
                type="number"
                step="0.1"
                min="0.5"
                max="50.0"
                value={takeProfitPct}
                onChange={(e) => setTakeProfitPct(parseFloat(e.target.value) || 0)}
                disabled={!isConnected}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-purple-500 disabled:opacity-50"
              />
              <p className="text-[11px] text-slate-500">Calculates authoritative TP limit brackets.</p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-bold text-slate-300 flex items-center justify-between">
                <span>Authoritative Stop Loss Threshold (% Max Loss)</span>
                <span className="text-purple-400 font-mono">{stopLossPct}%</span>
              </label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="10.0"
                value={stopLossPct}
                onChange={(e) => setStopLossPct(parseFloat(e.target.value) || 0)}
                disabled={!isConnected}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-purple-500 disabled:opacity-50"
              />
              <p className="text-[11px] text-slate-500">Calculates protective stop brackets at OB invalidation boundaries.</p>
            </div>
          </div>

          <div className="flex items-center justify-between pt-4 border-t border-slate-800">
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Layers size={15} />
              <span>Immutable versioned persistence in PostgreSQL backend.</span>
            </div>

            <button
              type="submit"
              disabled={!isConnected || isSavingConfig}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-purple-600 hover:bg-purple-500 text-white text-xs font-bold rounded-xl shadow-lg shadow-purple-600/20 transition disabled:opacity-50"
            >
              <Save size={14} />
              {isSavingConfig ? 'Saving Parameters...' : 'Save Configuration'}
            </button>
          </div>
        </form>
      </div>

      {/* Connect Modal */}
      {showConnectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-blue-600/20 text-blue-400">
                  <Key size={20} />
                </div>
                <h3 className="font-bold text-white text-base">Connect Delta Exchange India</h3>
              </div>
              <button
                onClick={() => setShowConnectModal(false)}
                className="text-slate-500 hover:text-white transition"
              >
                ✕
              </button>
            </div>

            {formError && (
              <div className="p-3 rounded-lg bg-red-950/40 border border-red-800 text-red-400 text-xs font-medium flex items-center gap-2">
                <AlertTriangle size={15} />
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleConnect} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-slate-300">Account Label</label>
                <input
                  type="text"
                  value={accountName}
                  onChange={(e) => setAccountName(e.target.value)}
                  placeholder="Delta Live Account"
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-bold text-slate-300">API Key</label>
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter Delta Exchange API Key"
                  required
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-bold text-slate-300">API Secret</label>
                <input
                  type="password"
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="Enter Delta Exchange API Secret"
                  required
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-white font-mono text-sm focus:outline-none focus:border-blue-500"
                />
                <p className="text-[11px] text-slate-500">Encrypted with AES-256-GCM upon receipt. Never logged or exposed.</p>
              </div>

              <div className="pt-2 flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowConnectModal(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isConnecting}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg shadow-lg shadow-blue-600/20 transition disabled:opacity-50"
                >
                  {isConnecting ? 'Verifying...' : 'Save & Connect'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}