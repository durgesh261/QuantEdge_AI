import React, { useEffect, useState, useCallback } from 'react'
import { tradingService } from '../../services/tradingService'
import { accountService } from '../../services/accountService'
import { TradingSystemStatusDto, AccountSummaryDto } from '../../types/trading'
import { AlgoConfigResponse } from '../../types/risk'
import { toast } from '../../stores/toastStore'
import { SkeletonStat, SkeletonCard } from '../../components/common/Skeleton'
import {
  ShieldAlert,
  Power,
  Sliders,
  AlertOctagon,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Lock,
  Unlock,
  Flame,
  X,
} from 'lucide-react'

export const RiskAlgoPage: React.FC = () => {
  const [tradingStatus, setTradingStatus] = useState<TradingSystemStatusDto | null>(null)
  const [accountSummary, setAccountSummary] = useState<AccountSummaryDto | null>(null)
  const [algoConfig, setAlgoConfig] = useState<AlgoConfigResponse | null>(null)

  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  // Risk Form State
  const [riskPerTrade, setRiskPerTrade] = useState<number>(1.0)
  const [maxLeverage, setMaxLeverage] = useState<number>(10)
  const [maxDailyLoss, setMaxDailyLoss] = useState<number>(5.0)
  const [takeProfit, setTakeProfit] = useState<number>(4.0)
  const [stopLoss, setStopLoss] = useState<number>(2.0)

  // Modal confirmation state
  const [killModalOpen, setKillModalOpen] = useState(false)
  const [resetModalOpen, setResetModalOpen] = useState(false)
  const [algoModalOpen, setAlgoModalOpen] = useState(false)

  const loadData = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)
      const [statusRes, summaryRes, configRes] = await Promise.allSettled([
        tradingService.getTradingStatus(),
        accountService.getAccountSummary(),
        accountService.getAlgoConfig(),
      ])

      if (statusRes.status === 'fulfilled') setTradingStatus(statusRes.value)
      if (summaryRes.status === 'fulfilled') setAccountSummary(summaryRes.value)
      if (configRes.status === 'fulfilled') {
        const cfg = configRes.value
        setAlgoConfig(cfg)
        if (cfg.riskPerTradePercent) setRiskPerTrade(cfg.riskPerTradePercent)
        if (cfg.maxLeverage) setMaxLeverage(cfg.maxLeverage)
        if (cfg.maxDailyLossPercent) setMaxDailyLoss(cfg.maxDailyLossPercent)
        if (cfg.takeProfitPercent) setTakeProfit(cfg.takeProfitPercent)
        if (cfg.stopLossPercent) setStopLoss(cfg.stopLossPercent)
      }
    } catch (err: any) {
      console.warn('Failed to load risk controls', err)
      setError(err.response?.data?.message || 'Error communicating with backend trading engine')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 10000)
    return () => clearInterval(interval)
  }, [loadData])

  // Handle Algo Toggle
  const handleToggleAlgo = async () => {
    setAlgoModalOpen(false)
    try {
      setIsSaving(true)
      setError(null)
      const nextState = !tradingStatus?.algoEnabled
      await tradingService.toggleAlgo(nextState)
      const msg = `Algorithmic engine ${nextState ? 'ENABLED' : 'PAUSED'} successfully.`
      setSuccessMessage(msg)
      toast.success(nextState ? 'Algo Engine Active' : 'Algo Engine Paused', msg)
      await loadData()
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Failed to toggle algorithmic engine'
      setError(msg)
      toast.error('Algo Toggle Failed', msg)
    } finally {
      setIsSaving(false)
    }
  }

  // Handle Trigger Kill-Switch
  const handleTriggerKillSwitch = async () => {
    setKillModalOpen(false)
    try {
      setIsSaving(true)
      setError(null)
      await tradingService.triggerKillSwitch('Manual user emergency halt from Risk Dashboard')
      const msg = 'EMERGENCY KILL-SWITCH ENGAGED: All execution halted and system locked.'
      setSuccessMessage(msg)
      toast.error('Emergency Kill-Switch Engaged', msg)
      await loadData()
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Failed to trigger emergency kill-switch'
      setError(msg)
      toast.error('Kill-Switch Action Failed', msg)
    } finally {
      setIsSaving(false)
    }
  }

  // Handle Reset Kill-Switch
  const handleResetKillSwitch = async () => {
    setResetModalOpen(false)
    try {
      setIsSaving(true)
      setError(null)
      await tradingService.resetKillSwitch()
      const msg = 'Emergency kill-switch has been RESET. System restored to normal state.'
      setSuccessMessage(msg)
      toast.success('Kill-Switch Restored', msg)
      await loadData()
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Failed to reset emergency kill-switch'
      setError(msg)
      toast.error('Kill-Switch Reset Failed', msg)
    } finally {
      setIsSaving(false)
    }
  }

  // Handle Save Config
  const handleSaveRiskConfig = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      setIsSaving(true)
      setError(null)
      setSuccessMessage(null)

      const res = await accountService.updateAlgoConfig({
        riskPerTradePercent: riskPerTrade,
        maxLeverage: maxLeverage,
        maxDailyLossPercent: maxDailyLoss,
        takeProfitPercent: takeProfit,
        stopLossPercent: stopLoss,
      })

      if (res.success) {
        const msg = 'Risk and algorithmic parameters updated successfully.'
        setSuccessMessage(msg)
        toast.success('Risk Limits Updated', msg)
        setAlgoConfig(res)
      } else {
        const msg = res.message || 'Failed to update risk parameters'
        setError(msg)
        toast.error('Update Failed', msg)
      }
    } catch (err: any) {
      const msg = err.response?.data?.message || 'Failed to save risk configuration'
      setError(msg)
      toast.error('Configuration Error', msg)
    } finally {
      setIsSaving(false)
    }
  }

  const isKillActive = tradingStatus?.killSwitchActive || algoConfig?.killSwitchActive
  const isAlgoActive = tradingStatus?.algoEnabled && !isKillActive

  if (isLoading && !tradingStatus && !accountSummary) {
    return (
      <div className="space-y-6">
        <div className="h-8 bg-slate-800/60 rounded-lg w-72 animate-pulse"></div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <SkeletonCard rows={4} />
          <SkeletonCard rows={4} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-warning" />
            <span>Risk Management & Master Algo Controls</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Emergency Execution Circuit Breakers, Position Sizing Limits & Algorithmic Loops
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-warning' : ''}`} />
            <span>Refresh State</span>
          </button>
        </div>
      </div>

      {/* Notifications */}
      {successMessage && (
        <div className="p-3 rounded-lg bg-bullish/10 border border-bullish/20 text-xs text-bullish font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{successMessage}</span>
          </div>
          <button onClick={() => setSuccessMessage(null)} className="text-slate-400 hover:text-white">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-slate-400 hover:text-white">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* 4 Summary State Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Master Engine State</div>
          <div className="mt-2 flex items-center gap-2 font-mono">
            <span
              className={`w-3 h-3 rounded-full ${
                isKillActive
                  ? 'bg-bearish animate-ping'
                  : isAlgoActive
                  ? 'bg-bullish shadow-[0_0_8px_#10B981]'
                  : 'bg-slate-500'
              }`}
            ></span>
            <span
              className={`text-xl font-bold ${
                isKillActive ? 'text-bearish' : isAlgoActive ? 'text-bullish' : 'text-slate-400'
              }`}
            >
              {isKillActive ? 'KILLED' : isAlgoActive ? 'ACTIVE' : 'PAUSED'}
            </span>
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">
            {isKillActive ? 'Circuit breaker active' : isAlgoActive ? '1H autonomous scanning' : 'Manual / stand-by'}
          </div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Emergency Circuit Breaker</div>
          <div className="mt-2 text-xl font-bold font-mono text-white flex items-center gap-2">
            {isKillActive ? (
              <span className="text-bearish flex items-center gap-1.5">
                <AlertOctagon className="w-5 h-5 text-bearish animate-pulse" />
                ENGAGED
              </span>
            ) : (
              <span className="text-bullish flex items-center gap-1.5">
                <CheckCircle2 className="w-5 h-5 text-bullish" />
                ARMED & READY
              </span>
            )}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Instant kill-switch status</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Active Single-Trade Lock</div>
          <div className="mt-2 text-xl font-bold font-mono text-white flex items-center gap-2">
            {tradingStatus?.hasActiveTradeLock ? (
              <span className="text-warning flex items-center gap-1.5 text-sm truncate">
                <Lock className="w-4 h-4 text-warning" />
                LOCKED ({tradingStatus.activeLockSetupId})
              </span>
            ) : (
              <span className="text-slate-300 flex items-center gap-1.5 text-sm">
                <Unlock className="w-4 h-4 text-slate-400" />
                NO LOCK (0/1)
              </span>
            )}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Max 1 simultaneous setup execution</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Available Trading Collateral</div>
          <div className="mt-2 text-xl font-bold font-mono text-brand-cyan">
            ${accountSummary?.availableBalance ? Number(accountSummary.availableBalance).toFixed(2) : '0.00'}
          </div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">
            Total: ${accountSummary?.balance ? Number(accountSummary.balance).toFixed(2) : '0.00'}
          </div>
        </div>
      </div>

      {/* Main Grid: Left Execution Controls & Right Configuration Form */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* LEFT COLUMN: Master Execution & Emergency Kill Panel (5 cols) */}
        <div className="lg:col-span-5 space-y-6">
          {/* Master Algo Engine Toggle Card */}
          <div className="glass-panel p-5 rounded-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Power className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">Algorithmic Execution Loop</h3>
              </div>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${
                  isAlgoActive ? 'bg-bullish/15 text-bullish' : 'bg-slate-800 text-slate-400'
                }`}
              >
                {isAlgoActive ? 'RUNNING' : 'STAND-BY'}
              </span>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed">
              When enabled, the backend autonomously consumes 1H SMC setups from the Python engine, verifies risk limits, and executes real orders via <code className="text-brand-cyan font-mono text-[11px]">OrderExecutionService.java</code>.
            </p>

            {isKillActive ? (
              <div className="p-3 rounded bg-bearish/15 border border-bearish/30 text-xs text-bearish font-mono flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>Cannot enable algo trading while Emergency Kill Switch is engaged.</span>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setAlgoModalOpen(true)}
                disabled={isSaving}
                className={`w-full py-3 rounded-lg font-mono text-xs font-bold transition-all flex items-center justify-center gap-2 shadow-lg ${
                  isAlgoActive
                    ? 'bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-600'
                    : 'bg-bullish hover:bg-bullish/90 text-background shadow-bullish/20'
                }`}
              >
                <Power className="w-4 h-4" />
                <span>{isAlgoActive ? 'PAUSE ALGORITHMIC TRADING' : 'ENABLE ALGORITHMIC TRADING'}</span>
              </button>
            )}
          </div>

          {/* Emergency Kill Switch Card */}
          <div className="glass-panel p-5 rounded-lg space-y-4 border-bearish/30 bg-bearish/5">
            <div className="flex items-center justify-between pb-3 border-b border-bearish/20">
              <div className="flex items-center gap-2 text-bearish">
                <AlertOctagon className="w-5 h-5" />
                <h3 className="text-sm font-bold font-mono">Emergency Kill Switch</h3>
              </div>
              <span className="px-2 py-0.5 rounded bg-bearish/20 text-bearish text-[10px] font-mono font-bold">
                HIGH RISK ACTION
              </span>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed">
              Engaging the Kill Switch immediately aborts the active trading loop, cancels all working orders, prevents any new signal ingestion, and locks the account state.
            </p>

            {isKillActive ? (
              <button
                type="button"
                onClick={() => setResetModalOpen(true)}
                disabled={isSaving}
                className="w-full py-3 rounded-lg bg-warning hover:bg-warning/90 text-background font-mono text-xs font-bold transition-all flex items-center justify-center gap-2 shadow-lg shadow-warning/20"
              >
                <Unlock className="w-4 h-4" />
                <span>RESET EMERGENCY KILL-SWITCH</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setKillModalOpen(true)}
                disabled={isSaving}
                className="w-full py-3 rounded-lg bg-bearish hover:bg-bearish/90 text-white font-mono text-xs font-bold transition-all flex items-center justify-center gap-2 shadow-lg shadow-bearish/30"
              >
                <Flame className="w-4 h-4" />
                <span>TRIGGER EMERGENCY KILL-SWITCH</span>
              </button>
            )}
          </div>
        </div>

        {/* RIGHT COLUMN: Risk Configuration Form (7 cols) */}
        <div className="lg:col-span-7">
          <form onSubmit={handleSaveRiskConfig} className="glass-panel p-5 rounded-lg space-y-5">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Sliders className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">Institutional Risk Limits</h3>
              </div>
              <span className="text-slate-400 font-mono text-xs">
                Config Version: #{algoConfig?.version || 1}
              </span>
            </div>

            {/* Risk per trade */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <label className="text-slate-300 font-semibold">Risk Per Trade (% of Equity)</label>
                <span className="text-brand-cyan font-bold">{riskPerTrade}%</span>
              </div>
              <input
                type="range"
                min="0.1"
                max="5.0"
                step="0.1"
                value={riskPerTrade}
                onChange={(e) => setRiskPerTrade(parseFloat(e.target.value))}
                className="w-full h-1.5 bg-background rounded-lg appearance-none cursor-pointer accent-brand-cyan"
              />
              <p className="text-[11px] text-slate-500 font-sans">
                Controls dynamic position sizing. Position size is sized such that Stop Loss hit equals this exact % of total balance.
              </p>
            </div>

            {/* Max Leverage */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <label className="text-slate-300 font-semibold">Maximum Allowed Leverage</label>
                <span className="text-white font-bold">{maxLeverage}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="50"
                step="1"
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(parseInt(e.target.value))}
                className="w-full h-1.5 bg-background rounded-lg appearance-none cursor-pointer accent-brand-cyan"
              />
              <p className="text-[11px] text-slate-500 font-sans">
                Hard ceiling for order leverage dispatched to the exchange. Orders exceeding this limit are rejected by the backend pre-trade filter.
              </p>
            </div>

            {/* Max Daily Loss */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <label className="text-slate-300 font-semibold">Max Daily Loss Limit (% Drawdown)</label>
                <span className="text-bearish font-bold">{maxDailyLoss}%</span>
              </div>
              <input
                type="range"
                min="1.0"
                max="15.0"
                step="0.5"
                value={maxDailyLoss}
                onChange={(e) => setMaxDailyLoss(parseFloat(e.target.value))}
                className="w-full h-1.5 bg-background rounded-lg appearance-none cursor-pointer accent-bearish"
              />
              <p className="text-[11px] text-slate-500 font-sans">
                If daily cumulative drawdown exceeds this threshold, the backend circuit breaker automatically halts all trading for the day.
              </p>
            </div>

            {/* Take Profit & Stop Loss Percent Targets */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-slate-300 font-semibold">Default Take Profit Target (%)</label>
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="50.0"
                  value={takeProfit}
                  onChange={(e) => setTakeProfit(parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white focus:outline-none focus:border-brand-cyan"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-mono text-slate-300 font-semibold">Default Stop Loss Target (%)</label>
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="20.0"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white focus:outline-none focus:border-brand-cyan"
                />
              </div>
            </div>

            {/* Submit Action */}
            <div className="pt-4 border-t border-terminal-border/60 flex items-center justify-between">
              <span className="text-[11px] font-mono text-slate-400">
                Last Updated: {algoConfig?.updatedAt ? new Date(algoConfig.updatedAt).toLocaleDateString() : 'Active'}
              </span>
              <button
                type="submit"
                disabled={isSaving}
                className="px-5 py-2.5 rounded-lg bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20 disabled:opacity-50"
              >
                {isSaving ? 'Saving...' : 'Save Risk Parameters'}
              </button>
            </div>
          </form>
        </div>
      </div>

      {/* Confirmation Modals */}
      {/* 1. Kill Switch Modal */}
      {killModalOpen && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-md w-full space-y-4 border-bearish/40 shadow-2xl">
            <div className="flex items-center gap-3 text-bearish">
              <AlertOctagon className="w-7 h-7 shrink-0" />
              <div>
                <h3 className="text-sm font-bold font-mono">Confirm Emergency Kill-Switch</h3>
                <p className="text-[11px] text-slate-400 font-sans">Immediate execution circuit breaker</p>
              </div>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed bg-background/60 p-3 rounded border border-terminal-border">
              Are you sure you want to engage the emergency kill-switch? This will immediately halt all automated order execution and lock the system against any new trades.
            </p>

            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setKillModalOpen(false)}
                className="flex-1 py-2 rounded bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleTriggerKillSwitch}
                className="flex-1 py-2 rounded bg-bearish hover:bg-bearish/90 text-xs font-bold text-white transition-colors"
              >
                Engage Kill-Switch
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 2. Reset Kill Switch Modal */}
      {resetModalOpen && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-md w-full space-y-4 border-warning/40 shadow-2xl">
            <div className="flex items-center gap-3 text-warning">
              <Unlock className="w-7 h-7 shrink-0" />
              <div>
                <h3 className="text-sm font-bold font-mono">Confirm Kill-Switch Reset</h3>
                <p className="text-[11px] text-slate-400 font-sans">Restore trading capabilities</p>
              </div>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed bg-background/60 p-3 rounded border border-terminal-border">
              Resetting the kill-switch will restore the trading system to normal operation. Algorithmic loops will remain paused until explicitly enabled.
            </p>

            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setResetModalOpen(false)}
                className="flex-1 py-2 rounded bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleResetKillSwitch}
                className="flex-1 py-2 rounded bg-warning hover:bg-warning/90 text-xs font-bold text-background transition-colors"
              >
                Reset Circuit Breaker
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 3. Toggle Algo Modal */}
      {algoModalOpen && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-md w-full space-y-4 border-brand-cyan/40 shadow-2xl">
            <div className="flex items-center gap-3 text-brand-cyan">
              <Power className="w-7 h-7 shrink-0" />
              <div>
                <h3 className="text-sm font-bold font-mono">
                  {isAlgoActive ? 'Pause Algorithmic Execution?' : 'Enable Autonomous Algo Trading?'}
                </h3>
                <p className="text-[11px] text-slate-400 font-sans">1H SMC Execution Engine</p>
              </div>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed bg-background/60 p-3 rounded border border-terminal-border">
              {isAlgoActive
                ? 'Pausing will stop the backend from taking new trade setups. Existing open positions will remain managed.'
                : 'Enabling will activate autonomous execution on qualified 1H SMC setups subject to pre-trade risk checks.'}
            </p>

            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setAlgoModalOpen(false)}
                className="flex-1 py-2 rounded bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleToggleAlgo}
                className="flex-1 py-2 rounded bg-brand-cyan hover:bg-brand-cyan/90 text-xs font-bold text-background transition-colors"
              >
                Confirm Toggle
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
