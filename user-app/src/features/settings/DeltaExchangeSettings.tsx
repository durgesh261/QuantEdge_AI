import React, { useState, useEffect, useCallback } from 'react'
import { settingsService } from '../../services/settingsService'
import { DeltaSettingsDto } from '../../types/settings'
import { toast } from '../../stores/toastStore'
import {
  Key,
  Lock,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Trash2,
  ShieldCheck,
  Zap,
  HelpCircle,
  Clock,
  ExternalLink,
} from 'lucide-react'

interface DeltaExchangeSettingsProps {
  onConnectionChange?: () => void
}

export const DeltaExchangeSettings: React.FC<DeltaExchangeSettingsProps> = ({ onConnectionChange }) => {
  const [settings, setSettings] = useState<DeltaSettingsDto | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [isDisconnecting, setIsDisconnecting] = useState(false)
  const [showDisconnectModal, setShowDisconnectModal] = useState(false)

  // Form states
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [showSecret, setShowSecret] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  const fetchSettings = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await settingsService.getDeltaSettings()
      setSettings(data)
      setErrorMessage(null)
    } catch (err: any) {
      console.warn('Delta settings fetch notice', err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchSettings()
  }, [fetchSettings])

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMessage(null)
    setSuccessMessage(null)

    const trimmedKey = apiKey.trim()
    const trimmedSecret = apiSecret.trim()

    if (!trimmedKey || !trimmedSecret) {
      setErrorMessage('Please provide both Delta API Key and API Secret.')
      return
    }

    try {
      setIsSubmitting(true)
      const res = await settingsService.connectDelta({
        apiKey: trimmedKey,
        apiSecret: trimmedSecret,
      })

      setSettings(res)
      setApiKey('')
      setApiSecret('')
      setIsEditing(false)
      setSuccessMessage('Delta Exchange account connected and verified successfully.')
      toast.success('Delta Account Connected', 'Your Delta Exchange credentials have been encrypted and verified.')
      onConnectionChange?.()
    } catch (err: any) {
      const msg =
        err?.response?.data?.lastError ||
        err?.response?.data?.message ||
        'Failed to connect Delta Exchange account. Please check your credentials and permissions.'
      setErrorMessage(msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleTestConnection = async () => {
    setErrorMessage(null)
    setSuccessMessage(null)
    try {
      setIsTesting(true)
      const res = await settingsService.testDeltaConnection()
      setSettings(res)
      setSuccessMessage('Delta API connection test passed! Live balances & market feeds are active.')
      toast.success('Connection Verified', 'Delta Exchange credentials verified successfully.')
      onConnectionChange?.()
    } catch (err: any) {
      const msg =
        err?.response?.data?.lastError ||
        err?.response?.data?.message ||
        'Delta connection test failed. Exchange API may be unreachable or credentials expired.'
      setErrorMessage(msg)
    } finally {
      setIsTesting(false)
    }
  }

  const handleDisconnect = async () => {
    setErrorMessage(null)
    setSuccessMessage(null)
    try {
      setIsDisconnecting(true)
      const res = await settingsService.disconnectDelta()
      setSettings(res)
      setShowDisconnectModal(false)
      setIsEditing(false)
      setApiKey('')
      setApiSecret('')
      setSuccessMessage('Delta Exchange account disconnected. Live automated trading has been safely paused.')
      toast.info('Account Disconnected', 'Delta Exchange credentials have been revoked and cleared.')
      onConnectionChange?.()
    } catch (err: any) {
      const msg = err?.response?.data?.message || 'Failed to disconnect account. Please try again.'
      setErrorMessage(msg)
    } finally {
      setIsDisconnecting(false)
    }
  }

  const isConnected = settings?.connected && settings?.status === 'CONNECTED'

  return (
    <div className="glass-panel p-5 rounded-lg space-y-5 border border-terminal-border relative">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-terminal-border">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-md bg-brand-cyan/10 border border-brand-cyan/20">
            <Key className="w-5 h-5 text-brand-cyan" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white font-mono flex items-center gap-2">
              <span>Delta Exchange India API Integration</span>
            </h3>
            <p className="text-[11px] text-slate-400 font-sans">
              Authoritative gateway for order placement, real-time position telemetry, and balance synchronization.
            </p>
          </div>
        </div>

        {/* Status Badge */}
        <div className="flex items-center gap-2">
          {isLoading ? (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-background-elevated text-slate-400 text-xs font-mono">
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>Checking...</span>
            </span>
          ) : isConnected ? (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-bullish/15 text-bullish text-xs font-mono font-bold border border-bullish/30">
              <span className="w-2 h-2 rounded-full bg-bullish animate-pulse"></span>
              <span>● CONNECTED</span>
            </span>
          ) : settings?.status === 'ERROR' ? (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-bearish/15 text-bearish text-xs font-mono font-bold border border-bearish/30">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>● CONNECTION ERROR</span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-800 text-slate-400 text-xs font-mono font-bold border border-slate-700">
              <span className="w-2 h-2 rounded-full bg-slate-500"></span>
              <span>● NOT CONNECTED</span>
            </span>
          )}
        </div>
      </div>

      {/* Alerts */}
      {errorMessage && (
        <div className="p-3 rounded-lg bg-bearish/10 border border-bearish/30 text-xs text-bearish font-mono flex items-start gap-2 animate-fadeIn">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <div className="font-bold">Delta Connection Notice</div>
            <div className="text-[11px] text-rose-300 font-sans leading-relaxed">{errorMessage}</div>
          </div>
        </div>
      )}

      {successMessage && (
        <div className="p-3 rounded-lg bg-bullish/10 border border-bullish/30 text-xs text-bullish font-mono flex items-center gap-2 animate-fadeIn">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successMessage}</span>
        </div>
      )}

      {/* CONNECTED VIEW */}
      {isConnected && !isEditing ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono text-xs">
            <div className="p-3 rounded bg-background/80 border border-terminal-border space-y-1">
              <div className="text-slate-400">Account Identity:</div>
              <div className="text-white font-semibold flex items-center gap-1.5">
                <span>{settings?.accountName || 'Delta Live Account'}</span>
                <span className="px-1.5 py-0.2 rounded bg-brand-cyan/20 text-brand-cyan text-[10px]">LIVE</span>
              </div>
              <div className="text-[11px] text-slate-500 truncate">ID: {settings?.accountId || 'Default'}</div>
            </div>

            <div className="p-3 rounded bg-background/80 border border-terminal-border space-y-1">
              <div className="text-slate-400">Last Verified:</div>
              <div className="text-white font-semibold flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                <span>
                  {settings?.lastVerifiedAt ? new Date(settings.lastVerifiedAt).toLocaleString() : 'Just now'}
                </span>
              </div>
              <div className="text-[11px] text-bullish flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" />
                <span>API Handshake Validated</span>
              </div>
            </div>

            <div className="p-3 rounded bg-background/80 border border-terminal-border space-y-1">
              <div className="text-slate-400">Configured API Key:</div>
              <div className="text-white font-semibold font-mono tracking-wider">
                {settings?.apiKeyMasked || '••••••••••••••••'}
              </div>
              <div className="text-[11px] text-slate-500">Read & Trading Permissions</div>
            </div>

            <div className="p-3 rounded bg-background/80 border border-terminal-border space-y-1">
              <div className="text-slate-400">Configured API Secret:</div>
              <div className="text-slate-300 font-mono tracking-widest">••••••••••••••••••••</div>
              <div className="text-[11px] text-bullish flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" />
                <span>AES-256-GCM Encrypted</span>
              </div>
            </div>
          </div>

          {/* Connected Actions */}
          <div className="pt-2 border-t border-terminal-border/60 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={isTesting}
                className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-mono font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? 'animate-spin text-brand-cyan' : ''}`} />
                <span>{isTesting ? 'Testing Handshake...' : 'Test Connection'}</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  setIsEditing(true)
                  setErrorMessage(null)
                  setSuccessMessage(null)
                }}
                className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-mono font-semibold text-brand-cyan transition-all border border-terminal-border"
              >
                <Zap className="w-3.5 h-3.5" />
                <span>Update Credentials</span>
              </button>
            </div>

            <button
              type="button"
              onClick={() => setShowDisconnectModal(true)}
              className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-bearish/10 hover:bg-bearish/20 text-xs font-mono font-semibold text-bearish transition-all border border-bearish/30"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Disconnect Account</span>
            </button>
          </div>
        </div>
      ) : (
        /* DISCONNECTED / EDITING FORM */
        <form onSubmit={handleConnect} className="space-y-4">
          {/* Permission Guidelines */}
          <div className="p-3.5 rounded-lg bg-background/90 border border-terminal-border space-y-2 text-xs">
            <div className="flex items-center gap-2 text-white font-mono font-bold">
              <ShieldCheck className="w-4 h-4 text-brand-cyan" />
              <span>Required Delta Exchange India Permissions</span>
            </div>
            <p className="text-[11px] text-slate-300 font-sans leading-relaxed">
              Create an API key in your{' '}
              <a
                href="https://india.delta.exchange/app/api-keys"
                target="_blank"
                rel="noreferrer"
                className="text-brand-cyan hover:underline inline-flex items-center gap-0.5 font-mono"
              >
                Delta Exchange Portal <ExternalLink className="w-3 h-3 inline" />
              </a>{' '}
              with <strong className="text-white font-mono">Read & Trade (Orders & Positions)</strong> enabled.
            </p>
            <div className="p-2 rounded bg-bearish/10 border border-bearish/20 text-[11px] text-rose-300 font-sans flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 text-bearish shrink-0 mt-0.5" />
              <span>
                <strong className="text-white">Security Rule:</strong> Do NOT enable withdrawal permissions. QuantEdge
                AI only requires trading access and will reject withdrawal keys.
              </span>
            </div>
          </div>

          <div className="space-y-3">
            {/* API Key */}
            <div className="space-y-1.5">
              <label className="text-xs font-mono text-slate-300 font-semibold flex items-center justify-between">
                <span>Delta API Key</span>
                <span className="text-[10px] text-slate-500 font-sans">Required</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="e.g. 5a89b4c27f91..."
                  autoComplete="off"
                  spellCheck="false"
                  disabled={isSubmitting}
                  className="w-full pl-9 pr-3 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white placeholder-slate-600 focus:outline-none focus:border-brand-cyan transition-colors"
                />
                <Key className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
              </div>
            </div>

            {/* API Secret */}
            <div className="space-y-1.5">
              <label className="text-xs font-mono text-slate-300 font-semibold flex items-center justify-between">
                <span>Delta API Secret</span>
                <span className="text-[10px] text-slate-500 font-sans">Never logged or returned</span>
              </label>
              <div className="relative">
                <input
                  type={showSecret ? 'text' : 'password'}
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="e.g. 9b7d41f028e3..."
                  autoComplete="new-password"
                  spellCheck="false"
                  disabled={isSubmitting}
                  className="w-full pl-9 pr-10 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white placeholder-slate-600 focus:outline-none focus:border-brand-cyan transition-colors"
                />
                <Lock className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
                <button
                  type="button"
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-2.5 text-slate-400 hover:text-white transition-colors"
                  tabIndex={-1}
                >
                  {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>

          {/* Form Actions */}
          <div className="pt-3 border-t border-terminal-border/60 flex items-center justify-between gap-3">
            <div className="text-[11px] text-slate-500 font-mono flex items-center gap-1">
              <HelpCircle className="w-3.5 h-3.5 text-slate-400" />
              <span>AES-256-GCM Server Encryption</span>
            </div>

            <div className="flex items-center gap-2">
              {isEditing && (
                <button
                  type="button"
                  onClick={() => {
                    setIsEditing(false)
                    setApiKey('')
                    setApiSecret('')
                    setErrorMessage(null)
                  }}
                  disabled={isSubmitting}
                  className="px-3 py-2 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-mono text-slate-300 font-semibold transition-all border border-terminal-border"
                >
                  Cancel
                </button>
              )}

              <button
                type="submit"
                disabled={isSubmitting || !apiKey.trim() || !apiSecret.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSubmitting ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Verifying with Delta...</span>
                  </>
                ) : (
                  <>
                    <Zap className="w-3.5 h-3.5" />
                    <span>{isEditing ? 'Save New Credentials' : 'Connect Delta Account'}</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </form>
      )}

      {/* DISCONNECT CONFIRMATION MODAL */}
      {showDisconnectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="glass-panel p-6 rounded-xl border border-bearish/40 max-w-md w-full space-y-4 shadow-2xl bg-background-card">
            <div className="flex items-center gap-3 text-bearish pb-2 border-b border-terminal-border">
              <div className="p-2 rounded-full bg-bearish/15">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div>
                <h4 className="text-sm font-bold text-white font-mono">Disconnect Delta Exchange?</h4>
                <p className="text-xs text-slate-400 font-sans">Active trading and telemetry will stop</p>
              </div>
            </div>

            <p className="text-xs text-slate-300 font-sans leading-relaxed">
              Disconnecting will revoke stored credentials from server memory, disarm active algorithmic execution, and
              pause automated trade execution.
            </p>

            <div className="pt-2 flex items-center justify-end gap-2.5">
              <button
                type="button"
                onClick={() => setShowDisconnectModal(false)}
                disabled={isDisconnecting}
                className="px-3.5 py-2 rounded-lg bg-background-elevated hover:bg-slate-700 text-xs font-mono font-semibold text-slate-300 transition-all border border-terminal-border"
              >
                Keep Connected
              </button>

              <button
                type="button"
                onClick={handleDisconnect}
                disabled={isDisconnecting}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-bearish hover:bg-bearish/90 text-white text-xs font-mono font-bold transition-all shadow-md shadow-bearish/20 disabled:opacity-50"
              >
                {isDisconnecting ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Disconnecting...</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Confirm Disconnect</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
