import { useState, useEffect } from 'react'
import {
  ShieldCheck,
  Key,
  RefreshCw,
  PowerOff,
  CheckCircle2,
  AlertTriangle,
  Lock,
  ExternalLink,
  Activity,
  DollarSign,
  Sliders,
  Save,
  Layers,
  Zap,
  AlertOctagon,
} from 'lucide-react'
import { useAccountStore } from '@/stores/accountStore'
import {
  accountService,
  AlgoConfigResponse,
  LiveTestPrepareResponse,
  LiveTestConfirmResponse,
  LiveTestCloseResponse,
} from '@/services/accountService'

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

  // Live Order Test State
  const [testSymbol, setTestSymbol] = useState('ETHUSD')
  const [isPreparingTest, setIsPreparingTest] = useState(false)
  const [isConfirmingTest, setIsConfirmingTest] = useState(false)
  const [isClosingTest, setIsClosingTest] = useState(false)
  const [liveTestPrepareData, setLiveTestPrepareData] = useState<LiveTestPrepareResponse | null>(null)
  const [liveTestConfirmData, setLiveTestConfirmData] = useState<LiveTestConfirmResponse | null>(null)
  const [liveTestCloseData, setLiveTestCloseData] = useState<LiveTestCloseResponse | null>(null)
  const [liveTestError, setLiveTestError] = useState<string | null>(null)
  const [showConfirmOrderModal, setShowConfirmOrderModal] = useState(false)

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

  const handlePrepareLiveTest = async () => {
    setIsPreparingTest(true)
    setLiveTestError(null)
    setLiveTestConfirmData(null)
    setLiveTestCloseData(null)

    try {
      const res = await accountService.prepareLiveTest(status?.accountId, testSymbol)
      if (res.ready) {
        setLiveTestPrepareData(res)
        setShowConfirmOrderModal(true)
      } else {
        setLiveTestError(res.error || 'Live order test preparation failed')
      }
    } catch (err: any) {
      setLiveTestError(err.response?.data?.error || err.message || 'Failed to prepare live order test')
    } finally {
      setIsPreparingTest(false)
    }
  }

  const handleConfirmLiveTest = async () => {
    if (!liveTestPrepareData?.confirmationToken) return

    setIsConfirmingTest(true)
    setLiveTestError(null)

    try {
      const res = await accountService.confirmLiveTest(
        liveTestPrepareData.confirmationToken,
        status?.accountId
      )
      setLiveTestConfirmData(res)
      setShowConfirmOrderModal(false)
      if (!res.success) {
        setLiveTestError(res.error || res.message || 'Live order execution failed')
      }
      // Refresh summary
      await fetchSummary(status?.accountId)
    } catch (err: any) {
      setLiveTestError(err.response?.data?.error || err.message || 'Failed to confirm live order')
    } finally {
      setIsConfirmingTest(false)
    }
  }

  const handleCloseLiveTest = async () => {
    setIsClosingTest(true)
    setLiveTestError(null)

    try {
      const res = await accountService.closeLiveTest(status?.accountId, testSymbol)
      setLiveTestCloseData(res)
      if (res.success) {
        // Refresh summary
        await fetchSummary(status?.accountId)
      } else {
        setLiveTestError(res.error || res.message || 'Failed to close live test position')
      }
    } catch (err: any) {
      setLiveTestError(err.response?.data?.error || err.message || 'Failed to close position')
    } finally {
      setIsClosingTest(false)
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
      setApiKey('')
      setApiSecret('')
      setShowConnectModal(false)
    }
  }

  const isConnected = status?.connected || status?.connectionStatus === 'CONNECTED'

  return (
    <div className="space-y-8 max-w-6xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Account & Exchange Settings</h1>
          <p className="text-slate-400 mt-1">
            Manage your real-trading connection with Delta Exchange India.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isConnected && (
            <button
              onClick={() => verifyAccount(status?.accountId)}
              disabled={isSyncing}
              className="inline-flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-lg border border-slate-700 transition disabled:opacity-50"
            >
              <RefreshCw size={16} className={isSyncing ? 'animate-spin text-blue-400' : ''} />
              {isSyncing ? 'Synchronizing...' : 'Sync Live Account'}
            </button>
          )}
          <button
            onClick={() => setShowConnectModal(true)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-blue-600/20 transition"
          >
            <Key size={16} />
            {isConnected ? 'Update Credentials' : 'Connect Delta Account'}
          </button>
        </div>
      </div>

      {/* Global Error Banner */}
      {(error || status?.lastError) && (
        <div className="bg-red-950/40 border border-red-800/80 rounded-xl p-4 flex items-start justify-between gap-3 text-red-300">
          <div className="flex items-start gap-3">
            <AlertTriangle size={20} className="text-red-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-red-200">Exchange Error / Disconnection</p>
              <p className="text-sm text-red-300 mt-0.5">{error || status?.lastError}</p>
            </div>
          </div>
          <button
            onClick={clearError}
            className="text-xs text-red-400 hover:text-red-200 uppercase font-bold tracking-wider px-2 py-1"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Connection Overview Card */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-2xl p-6 sm:p-8 backdrop-blur-sm shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-600/20 to-indigo-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 shadow-inner">
              <ShieldCheck size={32} />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-xl font-bold text-white">Delta Exchange India</h2>
                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${
                    isConnected
                      ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/80'
                      : status?.connectionStatus === 'ERROR'
                      ? 'bg-red-950/80 text-red-400 border border-red-800/80'
                      : 'bg-slate-800 text-slate-400 border border-slate-700'
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      isConnected ? 'bg-emerald-400 animate-pulse' : status?.connectionStatus === 'ERROR' ? 'bg-red-400' : 'bg-slate-500'
                    }`}
                  />
                  {status?.connectionStatus || 'DISCONNECTED'}
                </span>
              </div>
              <p className="text-sm text-slate-400 font-mono mt-1">
                Endpoint: <span className="text-slate-300">https://api.india.delta.exchange</span> (Production)
              </p>
            </div>
          </div>

          {isConnected && (
            <button
              onClick={() => disconnectAccount(status?.accountId)}
              disabled={isLoading}
              className="inline-flex items-center gap-2 px-4 py-2 bg-red-950/30 hover:bg-red-900/40 text-red-300 text-sm font-medium rounded-lg border border-red-800/50 transition self-start md:self-auto"
            >
              <PowerOff size={16} />
              Disconnect
            </button>
          )}
        </div>

        {/* Credentials & Details Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-6">
          <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
            <p className="text-xs uppercase font-bold text-slate-500 tracking-wider">Masked API Key</p>
            <div className="flex items-center gap-2 mt-2 font-mono text-sm text-slate-200">
              <Key size={16} className="text-slate-400" />
              <span>{status?.maskedApiKey || 'No Key Configured'}</span>
            </div>
            <p className="text-xs text-slate-500 mt-2">API Secret is stored securely encrypted with AES-256-GCM.</p>
          </div>

          <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
            <p className="text-xs uppercase font-bold text-slate-500 tracking-wider">Live Wallet Equity</p>
            <div className="flex items-center gap-2 mt-2 font-mono text-xl font-bold text-emerald-400">
              <DollarSign size={20} className="text-emerald-400" />
              <span>{summary?.totalEquity != null ? `$${summary.totalEquity.toLocaleString()}` : '$0.00'}</span>
            </div>
            <p className="text-xs text-slate-500 mt-2">
              Available: ${summary?.availableBalance != null ? summary.availableBalance.toLocaleString() : '0.00'}
            </p>
          </div>

          <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
            <p className="text-xs uppercase font-bold text-slate-500 tracking-wider">Safety Status</p>
            <div className="flex items-center gap-3 mt-2">
              <span className="px-2 py-0.5 bg-yellow-950/60 border border-yellow-800/80 text-yellow-400 text-xs font-semibold rounded">
                Algo: {status?.algoEnabled ? 'ENABLED' : 'DISABLED (Safe)'}
              </span>
              <span className="px-2 py-0.5 bg-red-950/60 border border-red-800/80 text-red-400 text-xs font-semibold rounded">
                Kill Switch: {status?.killSwitchActive ? 'ACTIVE (Safe)' : 'INACTIVE'}
              </span>
            </div>
            <p className="text-xs text-slate-500 mt-2">Default safe flags prevent automated executions.</p>
          </div>
        </div>
      </div>

      {/* Algorithm Trading Configuration Panel */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800/80 pb-5">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
              <Sliders size={22} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-bold text-white">Algorithm Trading Configuration</h2>
                <span className="px-2 py-0.5 text-xs font-mono font-bold rounded bg-indigo-950 text-indigo-300 border border-indigo-800">
                  Version {algoConfig?.version ?? 1}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Authoritative parameters for automated strategy order submission and bracket protection.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 bg-slate-950/60 border border-slate-800 px-3 py-1.5 rounded-lg">
            <Layers size={14} className="text-indigo-400" />
            <span>Immutable Trade Snapshots: Active</span>
          </div>
        </div>

        {/* Informational Guidance Notice */}
        <div className="bg-blue-950/40 border border-blue-800/60 rounded-xl p-4 text-xs text-blue-200 flex items-start gap-3">
          <CheckCircle2 size={16} className="text-blue-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold text-blue-100">
              These settings apply to new trades. Existing positions keep their original trade parameters.
            </p>
            <p className="text-slate-300">
              When a trade signal is generated, an immutable snapshot of this version is locked to that trade. Updating parameters increments the version and only governs future orders.
            </p>
          </div>
        </div>

        {configSuccessMsg && (
          <div className="bg-emerald-950/40 border border-emerald-800/80 rounded-xl p-3.5 text-xs font-mono text-emerald-300 flex items-center gap-2">
            <CheckCircle2 size={16} className="text-emerald-400 shrink-0" />
            <span>{configSuccessMsg}</span>
          </div>
        )}

        {configErrorMsg && (
          <div className="bg-red-950/40 border border-red-800/80 rounded-xl p-3.5 text-xs font-mono text-red-300 flex items-center gap-2">
            <AlertTriangle size={16} className="text-red-400 shrink-0" />
            <span>{configErrorMsg}</span>
          </div>
        )}

        <form onSubmit={handleSaveAlgoConfig} className="space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Take Profit */}
            <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-2">
              <label className="text-xs font-bold text-slate-300 uppercase tracking-wider block">
                Take Profit (TP %)
              </label>
              <div className="relative">
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="100"
                  value={takeProfitPct}
                  onChange={(e) => setTakeProfitPct(parseFloat(e.target.value) || 0)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-emerald-400 focus:outline-none focus:border-blue-500"
                  required
                />
                <span className="absolute right-3 top-2.5 text-xs font-mono text-slate-500">%</span>
              </div>
              <p className="text-xs text-slate-500">Auto TP bracket distance from entry.</p>
            </div>

            {/* Stop Loss */}
            <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-2">
              <label className="text-xs font-bold text-slate-300 uppercase tracking-wider block">
                Stop Loss (SL %)
              </label>
              <div className="relative">
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="100"
                  value={stopLossPct}
                  onChange={(e) => setStopLossPct(parseFloat(e.target.value) || 0)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-red-400 focus:outline-none focus:border-blue-500"
                  required
                />
                <span className="absolute right-3 top-2.5 text-xs font-mono text-slate-500">%</span>
              </div>
              <p className="text-xs text-slate-500">Auto SL bracket distance from entry.</p>
            </div>

            {/* Risk per Trade */}
            <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-2">
              <label className="text-xs font-bold text-slate-300 uppercase tracking-wider block">
                Risk Per Trade (%)
              </label>
              <div className="relative">
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="100"
                  value={riskPerTradePct}
                  onChange={(e) => setRiskPerTradePct(parseFloat(e.target.value) || 0)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-blue-400 focus:outline-none focus:border-blue-500"
                  required
                />
                <span className="absolute right-3 top-2.5 text-xs font-mono text-slate-500">%</span>
              </div>
              <p className="text-xs text-slate-500">Fraction of account equity risked.</p>
            </div>

            {/* Max Daily Loss */}
            <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-2">
              <label className="text-xs font-bold text-slate-300 uppercase tracking-wider block">
                Daily Loss Limit (%)
              </label>
              <div className="relative">
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="100"
                  value={maxDailyLossPct}
                  onChange={(e) => setMaxDailyLossPct(parseFloat(e.target.value) || 0)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-amber-400 focus:outline-none focus:border-blue-500"
                  required
                />
                <span className="absolute right-3 top-2.5 text-xs font-mono text-slate-500">%</span>
              </div>
              <p className="text-xs text-slate-500">Blocks new entries if breached.</p>
            </div>
          </div>

          <div className="flex items-center justify-between pt-2">
            <div className="text-xs font-mono text-slate-400">
              Risk/Reward Ratio: <span className="text-emerald-400 font-bold">1:{(takeProfitPct / (stopLossPct || 1)).toFixed(2)}</span>
            </div>
            <button
              type="submit"
              disabled={isSavingConfig}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg shadow-lg shadow-blue-600/20 transition disabled:opacity-50"
            >
              <Save size={15} />
              {isSavingConfig ? 'Saving Version...' : 'Save Configuration'}
            </button>
          </div>
        </form>
      </div>

      {/* Live Order Test Panel */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800/80 pb-5">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-amber-600/20 text-amber-400 border border-amber-500/30">
              <Zap size={22} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-bold text-white">Explicit Live Order Test</h2>
                <span className="px-2 py-0.5 text-xs font-mono font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">
                  Verification Gate
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Deliberate two-step test order with dynamic smallest lot sizing, live bracket protection, and position closure.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 bg-slate-950/60 border border-slate-800 px-3 py-1.5 rounded-lg">
            <ShieldCheck size={14} className="text-emerald-400" />
            <span>SMC Strategy Disconnected</span>
          </div>
        </div>

        {/* Warning Banner */}
        <div className="bg-amber-950/40 border border-amber-800/70 rounded-xl p-4 text-xs text-amber-200 flex items-start gap-3">
          <AlertOctagon size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-bold text-amber-100 uppercase tracking-wide">
              WARNING: This will place a REAL order using your connected Delta Exchange account.
            </p>
            <p className="text-slate-300">
              This workflow submits the smallest allowable live order, immediately establishes reduce-only Stop Loss and Take Profit orders, and provides verified position closure. Automated algorithms will NEVER trigger this test.
            </p>
          </div>
        </div>

        {liveTestError && (
          <div className="bg-red-950/40 border border-red-800/80 rounded-xl p-3.5 text-xs font-mono text-red-300 flex items-center gap-2">
            <AlertTriangle size={16} className="text-red-400 shrink-0" />
            <span>{liveTestError}</span>
          </div>
        )}

        {liveTestConfirmData && (
          <div className={`p-4 rounded-xl border space-y-3 ${
            liveTestConfirmData.success
              ? 'bg-emerald-950/30 border-emerald-800/60 text-emerald-300'
              : 'bg-red-950/30 border-red-800/60 text-red-300'
          }`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {liveTestConfirmData.success ? (
                  <CheckCircle2 size={18} className="text-emerald-400" />
                ) : (
                  <AlertTriangle size={18} className="text-red-400" />
                )}
                <span className="font-bold font-mono text-sm">
                  {liveTestConfirmData.status}
                </span>
              </div>
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-700">
                Position: {liveTestConfirmData.positionStatus}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono pt-2 border-t border-slate-800">
              <div>
                <span className="text-slate-500 block">Exchange Order ID</span>
                <span className="text-slate-200 font-bold">{liveTestConfirmData.exchangeOrderId || 'N/A'}</span>
              </div>
              <div>
                <span className="text-slate-500 block">Filled Quantity</span>
                <span className="text-slate-200 font-bold">{liveTestConfirmData.filledQuantity} Lot</span>
              </div>
              <div>
                <span className="text-slate-500 block">Fill Price</span>
                <span className="text-emerald-400 font-bold">${liveTestConfirmData.fillPrice}</span>
              </div>
              <div>
                <span className="text-slate-500 block">Protective SL / TP</span>
                <span className="text-slate-200">
                  SL: ${liveTestConfirmData.stopLossPrice} | TP: ${liveTestConfirmData.takeProfitPrice}
                </span>
              </div>
            </div>

            {liveTestConfirmData.positionStatus === 'OPEN' && (
              <div className="pt-2 flex justify-end">
                <button
                  onClick={handleCloseLiveTest}
                  disabled={isClosingTest}
                  className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-bold rounded-lg shadow-lg shadow-red-600/20 transition disabled:opacity-50 flex items-center gap-2"
                >
                  <PowerOff size={14} />
                  {isClosingTest ? 'Closing Test Position...' : 'Close Test Position (Flatten)'}
                </button>
              </div>
            )}
          </div>
        )}

        {liveTestCloseData && (
          <div className="bg-blue-950/30 border border-blue-800/60 rounded-xl p-4 text-xs font-mono text-blue-200 space-y-2">
            <div className="flex items-center gap-2 font-bold text-blue-100">
              <CheckCircle2 size={16} className="text-blue-400" />
              <span>{liveTestCloseData.status}: {liveTestCloseData.message}</span>
            </div>
            <p className="text-slate-400">
              Final Position Size: <strong className="text-white">{liveTestCloseData.finalPosition ?? 0}</strong> | Available Balance: <strong className="text-emerald-400">${liveTestCloseData.finalAvailableBalance ?? 'N/A'}</strong>
            </p>
          </div>
        )}

        {/* Live Test Trigger Form */}
        <div className="flex flex-col sm:flex-row items-end gap-4 pt-2">
          <div className="w-full sm:w-64 space-y-1.5">
            <label className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
              Test Contract Symbol
            </label>
            <select
              value={testSymbol}
              onChange={(e) => setTestSymbol(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-amber-500 font-mono"
            >
              <option value="ETHUSD">ETHUSD (Ethereum Perpetual)</option>
              <option value="BTCUSD">BTCUSD (Bitcoin Perpetual)</option>
            </select>
          </div>

          <button
            onClick={handlePrepareLiveTest}
            disabled={!isConnected || isPreparingTest}
            className="w-full sm:w-auto px-6 py-2.5 bg-amber-600 hover:bg-amber-500 text-white text-sm font-bold rounded-lg shadow-lg shadow-amber-600/20 transition disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <Zap size={16} />
            {isPreparingTest ? 'Inspecting Exchange...' : 'Prepare Live Test'}
          </button>
        </div>
      </div>

      {/* Security Architecture & Guide */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6">
          <div className="flex items-center gap-3 text-blue-400 mb-3">
            <Lock size={20} />
            <h3 className="font-semibold text-white">Bank-Grade Credential Protection</h3>
          </div>
          <ul className="space-y-2.5 text-sm text-slate-400">
            <li className="flex items-start gap-2">
              <CheckCircle2 size={16} className="text-emerald-400 shrink-0 mt-0.5" />
              <span>
                API secrets are encrypted server-side with <strong className="text-slate-200">AES-256-GCM</strong>.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <CheckCircle2 size={16} className="text-emerald-400 shrink-0 mt-0.5" />
              <span>Decryption occurs solely in server memory at request dispatch time.</span>
            </li>
            <li className="flex items-start gap-2">
              <CheckCircle2 size={16} className="text-emerald-400 shrink-0 mt-0.5" />
              <span>Frontend payloads never carry credentials; secrets are never logged.</span>
            </li>
          </ul>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6">
          <div className="flex items-center gap-3 text-indigo-400 mb-3">
            <Activity size={20} />
            <h3 className="font-semibold text-white">Explicit Live Execution Verification</h3>
          </div>
          <p className="text-sm text-slate-400 leading-relaxed">
            The Live Execution Test allows you to explicitly verify order placement on Delta Exchange India with the smallest valid contract lot, protective SL/TP brackets, and verified position closure.
          </p>
          <a
            href="https://india.delta.exchange"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 font-medium mt-4 transition"
          >
            Delta Exchange India Portal <ExternalLink size={12} />
          </a>
        </div>
      </div>

      {/* Explicit Order Confirmation Modal */}
      {showConfirmOrderModal && liveTestPrepareData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-amber-600/60 rounded-2xl max-w-lg w-full p-6 sm:p-8 shadow-2xl space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-amber-600/20 text-amber-400 border border-amber-500/30">
                  <AlertOctagon size={20} />
                </div>
                <h3 className="text-lg font-bold text-white">Confirm REAL Live Order</h3>
              </div>
              <button
                onClick={() => setShowConfirmOrderModal(false)}
                className="text-slate-500 hover:text-slate-300 text-sm font-bold p-1"
              >
                ✕
              </button>
            </div>

            <div className="bg-red-950/40 border border-red-800/80 rounded-xl p-3.5 text-xs font-mono text-red-200">
              <p className="font-bold uppercase tracking-wider mb-1">
                ATTENTION: REAL FUNDS AT RISK
              </p>
              <p className="text-red-300">
                {liveTestPrepareData.warning}
              </p>
            </div>

            <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2.5 text-xs font-mono">
              <div className="flex justify-between text-slate-400">
                <span>Exchange:</span>
                <span className="text-white font-bold">{liveTestPrepareData.exchange}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Product & Side:</span>
                <span className="text-amber-400 font-bold">{liveTestPrepareData.symbol} ({liveTestPrepareData.side})</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Minimum Quantity:</span>
                <span className="text-white font-bold">{liveTestPrepareData.minimumQuantity} Lot</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Current Mark Price:</span>
                <span className="text-emerald-400 font-bold">${liveTestPrepareData.markPrice}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Estimated Margin:</span>
                <span className="text-white font-bold">${liveTestPrepareData.estimatedMargin}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Available Collateral:</span>
                <span className="text-emerald-400 font-bold">${liveTestPrepareData.availableBalance}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Risk & Margin Validation:</span>
                <span className="text-emerald-400 font-bold">{liveTestPrepareData.riskCheck}</span>
              </div>
              <div className="flex justify-between text-slate-400 pt-2 border-t border-slate-800">
                <span>Token Expiration:</span>
                <span className="text-slate-400">{new Date(liveTestPrepareData.expiresAt || '').toLocaleTimeString()}</span>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
              <button
                type="button"
                onClick={() => setShowConfirmOrderModal(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-medium rounded-lg transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmLiveTest}
                disabled={isConfirmingTest}
                className="px-5 py-2.5 bg-gradient-to-r from-red-600 to-amber-600 hover:from-red-500 hover:to-amber-500 text-white text-sm font-bold rounded-lg shadow-lg shadow-red-600/30 transition disabled:opacity-50 flex items-center gap-2"
              >
                <Zap size={16} />
                {isConfirmingTest ? 'Submitting Real Order...' : 'Confirm REAL Order Now'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Connect Account Modal */}
      {showConnectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 sm:p-8 shadow-2xl space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-blue-600/20 text-blue-400 border border-blue-500/30">
                  <Key size={20} />
                </div>
                <h3 className="text-lg font-bold text-white">Connect Delta India Account</h3>
              </div>
              <button
                onClick={() => setShowConnectModal(false)}
                className="text-slate-500 hover:text-slate-300 text-sm font-bold p-1"
              >
                ✕
              </button>
            </div>

            {formError && (
              <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg text-sm text-red-300">
                {formError}
              </div>
            )}

            <form onSubmit={handleConnect} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                  Account Name
                </label>
                <input
                  type="text"
                  value={accountName}
                  onChange={(e) => setAccountName(e.target.value)}
                  placeholder="Delta Live Account"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                  API Key <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Paste Delta India API Key"
                  required
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                  API Secret <span className="text-red-400">*</span>
                </label>
                <input
                  type="password"
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="Paste Delta India API Secret"
                  required
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-blue-500 font-mono"
                />
                <p className="text-xs text-slate-500 mt-1">
                  Secret will be encrypted immediately and never returned or exposed.
                </p>
              </div>

              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setShowConnectModal(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-medium rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isConnecting}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-blue-600/20 transition disabled:opacity-50"
                >
                  {isConnecting ? 'Verifying...' : 'Save & Verify'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}