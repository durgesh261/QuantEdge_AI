import React, { useEffect, useState, useCallback } from 'react'
import { developerService } from '../../services/developerService'
import { tradingService } from '../../services/tradingService'
import { ApiDiagnosticsResponse } from '../../types/developer'
import { TradingSystemStatusDto } from '../../types/trading'
import { ConfirmationModal } from '../../components/common/ConfirmationModal'
import { SkeletonCard } from '../../components/common/Skeleton'
import {
  Settings,
  ShieldAlert,
  Database,
  Power,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react'

export const SystemConfiguration: React.FC = () => {
  const [diagnostics, setDiagnostics] = useState<ApiDiagnosticsResponse | null>(null)
  const [status, setStatus] = useState<TradingSystemStatusDto | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // Confirmation Modals State
  const [modalState, setModalState] = useState<{
    open: boolean
    title: string
    description: string
    variant: 'danger' | 'warning' | 'primary'
    action: () => Promise<void>
  }>({
    open: false,
    title: '',
    description: '',
    variant: 'warning',
    action: async () => {},
  })
  const [isProcessing, setIsProcessing] = useState(false)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [diagRes, statusRes] = await Promise.all([
        developerService.getApiDiagnostics(),
        tradingService.getTradingStatus().catch(() => null),
      ])
      setDiagnostics(diagRes)
      setStatus(statusRes)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load system diagnostics')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleToggleAlgo = () => {
    const nextState = !status?.algoEnabled
    setModalState({
      open: true,
      title: nextState ? 'Enable Global Algorithmic Execution' : 'Pause Global Algorithmic Execution',
      description: nextState
        ? 'Enabling the algorithmic trading loop will allow verified 1H SMC setups to dispatch orders automatically.'
        : 'Pausing the algorithmic trading loop will prevent any new positions from opening.',
      variant: nextState ? 'primary' : 'warning',
      action: async () => {
        setIsProcessing(true)
        try {
          const res = await tradingService.toggleAlgo(nextState)
          setSuccessMsg(res.message || 'Algo state updated successfully')
          await fetchData()
        } catch (err: any) {
          setError(err.response?.data?.message || 'Failed to toggle algo state')
        } finally {
          setIsProcessing(false)
          setModalState((prev) => ({ ...prev, open: false }))
        }
      },
    })
  }

  const handleKillSwitch = () => {
    const isActive = status?.killSwitchActive
    setModalState({
      open: true,
      title: isActive ? 'Reset Emergency Circuit Breaker' : 'ENGAGE EMERGENCY KILL SWITCH',
      description: isActive
        ? 'Resetting the emergency circuit breaker will restore normal trading operations.'
        : 'CRITICAL: Engaging the kill switch will immediately halt all strategy execution, cancel open orders, and lock trading.',
      variant: isActive ? 'primary' : 'danger',
      action: async () => {
        setIsProcessing(true)
        try {
          if (isActive) {
            const res = await tradingService.resetKillSwitch()
            setSuccessMsg(res.message || 'Kill switch reset successfully')
          } else {
            const res = await tradingService.activateKillSwitch('Operator manual kill-switch engagement')
            setSuccessMsg(res.message || 'Kill switch engaged successfully')
          }
          await fetchData()
        } catch (err: any) {
          setError(err.response?.data?.message || 'Failed to update kill switch state')
        } finally {
          setIsProcessing(false)
          setModalState((prev) => ({ ...prev, open: false }))
        }
      },
    })
  }

  if (isLoading && !diagnostics) {
    return (
      <div className="space-y-6">
        <SkeletonCard rows={4} />
      </div>
    )
  }

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <Settings className="w-5 h-5 text-dev-cyan" />
            <span>System Configuration & Circuit Breakers</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Gateway URLs, cryptographic signature verification & emergency execution controls
          </p>
        </div>

        <button
          onClick={fetchData}
          className="p-2 rounded bg-background border border-terminal-border hover:bg-background-elevated text-slate-300 hover:text-white transition-colors"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {successMsg && (
        <div className="p-3 rounded-lg bg-bullish/15 border border-bullish/30 text-bullish text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg(null)} className="text-slate-400 hover:text-white">✕</button>
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/30 text-bearish text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-slate-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Emergency Controls Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Algo Control Panel */}
        <div className="glass-panel p-5 rounded-lg border border-terminal-border space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-terminal-border/80">
            <div className="flex items-center gap-2">
              <Power className="w-4 h-4 text-dev-accent" />
              <h3 className="font-bold text-white text-xs uppercase tracking-wider">
                Algorithmic Engine Loop State
              </h3>
            </div>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                status?.algoEnabled ? 'bg-bullish/15 text-bullish' : 'bg-slate-800 text-slate-400'
              }`}
            >
              {status?.algoEnabled ? 'ONLINE / ENABLED' : 'PAUSED'}
            </span>
          </div>

          <p className="text-xs text-slate-400 font-sans leading-relaxed">
            Governs whether validated 1H Smart Money Concept trading signals are allowed to proceed to order placement.
          </p>

          <button
            onClick={handleToggleAlgo}
            className={`w-full py-2.5 rounded-lg font-bold text-xs flex items-center justify-center gap-2 transition-all shadow-md ${
              status?.algoEnabled
                ? 'bg-warning/15 hover:bg-warning/25 text-warning border border-warning/30'
                : 'bg-dev-accent hover:bg-dev-accent/90 text-background'
            }`}
          >
            <Power className="w-4 h-4" />
            <span>{status?.algoEnabled ? 'PAUSE ALGORITHMIC LOOP' : 'ENABLE ALGORITHMIC LOOP'}</span>
          </button>
        </div>

        {/* Emergency Kill Switch */}
        <div className="glass-panel p-5 rounded-lg border border-bearish/30 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-terminal-border/80">
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-bearish" />
              <h3 className="font-bold text-white text-xs uppercase tracking-wider">
                Emergency Execution Circuit Breaker
              </h3>
            </div>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                status?.killSwitchActive ? 'bg-bearish/20 text-bearish' : 'bg-bullish/15 text-bullish'
              }`}
            >
              {status?.killSwitchActive ? 'ENGAGED / HALTED' : 'NORMAL'}
            </span>
          </div>

          <p className="text-xs text-slate-400 font-sans leading-relaxed">
            Immediate operator fail-safe. Instantly pauses all active order processing and locks the execution engine.
          </p>

          <button
            onClick={handleKillSwitch}
            className={`w-full py-2.5 rounded-lg font-bold text-xs flex items-center justify-center gap-2 transition-all shadow-md ${
              status?.killSwitchActive
                ? 'bg-dev-cyan hover:bg-dev-cyan/90 text-background'
                : 'bg-bearish hover:bg-bearish/90 text-white'
            }`}
          >
            <ShieldAlert className="w-4 h-4" />
            <span>{status?.killSwitchActive ? 'RESET EMERGENCY KILL SWITCH' : 'ENGAGE EMERGENCY KILL SWITCH'}</span>
          </button>
        </div>
      </div>

      {/* System Gateway Configuration Parameters */}
      <div className="glass-panel rounded-lg border border-terminal-border p-5 space-y-4">
        <h3 className="font-bold text-white text-xs uppercase tracking-wider pb-3 border-b border-terminal-border/80 flex items-center gap-2">
          <Database className="w-4 h-4 text-dev-cyan" />
          <span>Gateway Endpoints & Cryptographic Verification</span>
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-1">
            <span className="text-slate-400 text-[10px] block">DELTA EXCHANGE API GATEWAY</span>
            <div className="font-bold text-white truncate">{diagnostics?.deltaApiUrl || 'https://api.india.delta.exchange'}</div>
            <span className="text-[10px] text-dev-accent flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Verified Status: {diagnostics?.deltaApiStatus} ({diagnostics?.deltaPingMs}ms)
            </span>
          </div>

          <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-1">
            <span className="text-slate-400 text-[10px] block">PYTHON SMC ENGINE RPC</span>
            <div className="font-bold text-white truncate">{diagnostics?.pythonEngineUrl || 'http://localhost:8000'}</div>
            <span className="text-[10px] text-dev-purple flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Verified Status: {diagnostics?.pythonEngineStatus} ({diagnostics?.pythonEnginePingMs}ms)
            </span>
          </div>

          <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-1">
            <span className="text-slate-400 text-[10px] block">CRYPTOGRAPHIC SIGNATURE MECHANISM</span>
            <div className="font-bold text-white">{diagnostics?.signatureMechanism || 'HMAC_SHA256'}</div>
            <span className="text-[10px] text-slate-400">Strict Nonce & SHA-256 HMAC Header Validation</span>
          </div>

          <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-1">
            <span className="text-slate-400 text-[10px] block">SECRETS SANITIZATION & LOG REDACTION</span>
            <div className="font-bold text-dev-accent">
              {diagnostics?.secretsSanitized ? 'ACTIVE & ENFORCED' : 'OFFLINE'}
            </div>
            <span className="text-[10px] text-slate-400">Zero sensitive keys or credentials exposed</span>
          </div>
        </div>
      </div>

      {/* Confirmation Safeguard Modal */}
      <ConfirmationModal
        isOpen={modalState.open}
        title={modalState.title}
        description={modalState.description}
        variant={modalState.variant}
        isLoading={isProcessing}
        onConfirm={modalState.action}
        onCancel={() => setModalState((prev) => ({ ...prev, open: false }))}
      />
    </div>
  )
}
