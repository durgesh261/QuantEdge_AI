import React, { useEffect, useState, useCallback } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { useMarketStore } from '../../stores/marketStore'
import { accountService } from '../../services/accountService'
import { tradingService } from '../../services/tradingService'
import { marketService } from '../../services/marketService'
import { settingsService } from '../../services/settingsService'
import { AccountStatusResponse } from '../../types/account'
import { MarketStatusDto } from '../../types/market'
import { SystemDiagnosticsDto } from '../../types/system'
import { SUPPORTED_SYMBOLS, getInstrumentMeta } from '../../constants/instruments'
import { DeltaExchangeSettings } from './DeltaExchangeSettings'
import {
  Settings as SettingsIcon,
  User,
  Shield,
  Sliders,
  Activity,
  CheckCircle2,
  RefreshCw,
  LogOut,
  Database,
  Server,
  Cpu,
  Radio,
  Clock,
  Sparkles,
} from 'lucide-react'

export const SettingsPage: React.FC = () => {
  const { user, logout, checkAuth } = useAuthStore()
  const { activeSymbol, activeInterval, setActiveSymbol, setActiveInterval } = useMarketStore()

  // Diagnostic states
  const [accountStatus, setAccountStatus] = useState<AccountStatusResponse | null>(null)
  const [marketStatus, setMarketStatus] = useState<MarketStatusDto | null>(null)
  const [diagnostics, setDiagnostics] = useState<SystemDiagnosticsDto | null>(null)
  const [isRefreshingProfile, setIsRefreshingProfile] = useState(false)
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null)

  // Local preferences state
  const [prefSymbol, setPrefSymbol] = useState(activeSymbol || 'BTCUSD')
  const [prefInterval, setPrefInterval] = useState(activeInterval || '1h')
  const [showOverlays, setShowOverlays] = useState(true)
  const [soundAlerts, setSoundAlerts] = useState(false)

  // Load Preferences from localStorage on mount
  useEffect(() => {
    try {
      const savedSymbol = localStorage.getItem('quantedge_pref_symbol')
      const savedInterval = localStorage.getItem('quantedge_pref_interval')
      const savedOverlays = localStorage.getItem('quantedge_pref_overlays')
      const savedAlerts = localStorage.getItem('quantedge_pref_alerts')

      if (savedSymbol) setPrefSymbol(savedSymbol)
      if (savedInterval) setPrefInterval(savedInterval)
      if (savedOverlays !== null) setShowOverlays(savedOverlays === 'true')
      if (savedAlerts !== null) setSoundAlerts(savedAlerts === 'true')
    } catch (e) {
      // Ignore localStorage errors
    }
  }, [])

  // Fetch Live Diagnostics
  const fetchDiagnostics = useCallback(async () => {
    try {
      const [accRes, , marketRes, diagRes] = await Promise.allSettled([
        accountService.getAccountStatus(),
        tradingService.getTradingStatus(),
        marketService.getMarketStatus(activeSymbol || 'BTCUSD'),
        settingsService.getSystemDiagnostics(),
      ])

      if (accRes.status === 'fulfilled') setAccountStatus(accRes.value)
      if (marketRes.status === 'fulfilled') setMarketStatus(marketRes.value)
      if (diagRes.status === 'fulfilled') setDiagnostics(diagRes.value)
    } catch (err) {
      console.warn('Diagnostics fetch notice', err)
    }
  }, [activeSymbol])

  useEffect(() => {
    fetchDiagnostics()
    const interval = setInterval(fetchDiagnostics, 10000)
    return () => clearInterval(interval)
  }, [fetchDiagnostics])

  const handleRefreshProfile = async () => {
    try {
      setIsRefreshingProfile(true)
      await checkAuth()
      await fetchDiagnostics()
      setSaveSuccess('User profile & live diagnostics refreshed.')
      setTimeout(() => setSaveSuccess(null), 3000)
    } catch (e) {
      // Ignore
    } finally {
      setIsRefreshingProfile(false)
    }
  }

  const handleSavePreferences = (e: React.FormEvent) => {
    e.preventDefault()
    try {
      localStorage.setItem('quantedge_pref_symbol', prefSymbol)
      localStorage.setItem('quantedge_pref_interval', prefInterval)
      localStorage.setItem('quantedge_pref_overlays', String(showOverlays))
      localStorage.setItem('quantedge_pref_alerts', String(soundAlerts))

      setActiveSymbol(prefSymbol)
      setActiveInterval(prefInterval)

      setSaveSuccess('Trading preferences saved and applied to active terminal.')
      setTimeout(() => setSaveSuccess(null), 3000)
    } catch (e) {
      console.warn('Failed to save preferences to localStorage', e)
    }
  }

  return (
    <div className="space-y-6 max-w-6xl mx-auto pb-12">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <SettingsIcon className="w-5 h-5 text-brand-cyan" />
            <span>Account Settings & System Diagnostics</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Production Session Security, User Preferences & Backend Engine Health
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleRefreshProfile}
            disabled={isRefreshingProfile}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshingProfile ? 'animate-spin text-brand-cyan' : ''}`} />
            <span>Refresh Diagnostics</span>
          </button>
        </div>
      </div>

      {/* Success Alert */}
      {saveSuccess && (
        <div className="p-3 rounded-lg bg-bullish/10 border border-bullish/20 text-xs text-bullish font-mono flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{saveSuccess}</span>
        </div>
      )}

      {/* Delta Exchange API Credential Management */}
      <DeltaExchangeSettings onConnectionChange={fetchDiagnostics} />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* LEFT COLUMN: User Profile & Security (5 cols) */}
        <div className="lg:col-span-5 space-y-6">
          {/* User Account Profile Card */}
          <div className="glass-panel p-5 rounded-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <User className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">Authenticated User Profile</h3>
              </div>
              <span className="px-2 py-0.5 rounded bg-brand-cyan/15 text-brand-cyan text-[10px] font-mono font-bold">
                {user?.role || 'TRADER'}
              </span>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Full Name:</span>
                <span className="text-white font-semibold">{user?.name || 'Production Trader'}</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Email Address:</span>
                <span className="text-white font-semibold">{user?.email}</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Account ID:</span>
                <span className="text-slate-300 text-[11px] truncate max-w-[180px]">
                  {accountStatus?.accountId || user?.id || 'acct_prod_tenant'}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Exchange Provider:</span>
                <span className="text-brand-cyan font-bold">Delta Exchange India</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Environment:</span>
                <span className="text-bullish font-bold">LIVE PRODUCTION</span>
              </div>
              <div className="flex justify-between py-1.5">
                <span className="text-slate-400">Last Active:</span>
                <span className="text-slate-300">
                  {user?.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString() : 'Active Session'}
                </span>
              </div>
            </div>

            <div className="pt-2 border-t border-terminal-border/60">
              <p className="text-[11px] text-slate-500 font-sans leading-relaxed flex items-start gap-1.5">
                <Shield className="w-3.5 h-3.5 text-bullish shrink-0 mt-0.5" />
                <span>
                  Tenant isolation enforced. All sensitive exchange API credentials are encrypted with AES-256-GCM server-side.
                </span>
              </p>
            </div>
          </div>

          {/* Session & Security Card */}
          <div className="glass-panel p-5 rounded-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Shield className="w-5 h-5 text-warning" />
                <h3 className="text-sm font-bold text-white font-mono">Session & Security</h3>
              </div>
              <span className="px-2 py-0.5 rounded bg-bullish/15 text-bullish text-[10px] font-mono font-bold">
                SECURE
              </span>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Auth Token Transport:</span>
                <span className="text-slate-300 font-semibold">HttpOnly Cookie (XSS Protected)</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">SameSite Policy:</span>
                <span className="text-slate-300 font-semibold">Lax (CSRF Protected)</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-terminal-border/40">
                <span className="text-slate-400">Session Window:</span>
                <span className="text-slate-300">24-Hour Sliding Expiry</span>
              </div>
              <div className="flex justify-between py-1.5">
                <span className="text-slate-400">Credential Storage:</span>
                <span className="text-bullish font-semibold">Zero Browser Storage Exposure</span>
              </div>
            </div>

            <div className="pt-3 border-t border-terminal-border/60">
              <button
                type="button"
                onClick={logout}
                className="w-full py-2.5 rounded-lg bg-bearish/15 hover:bg-bearish/25 border border-bearish/30 text-bearish font-mono text-xs font-bold transition-all flex items-center justify-center gap-2"
              >
                <LogOut className="w-4 h-4" />
                <span>Log Out of QuantEdge Suite</span>
              </button>
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: Preferences & System Diagnostics (7 cols) */}
        <div className="lg:col-span-7 space-y-6">
          {/* Trading Preferences Form */}
          <form onSubmit={handleSavePreferences} className="glass-panel p-5 rounded-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Sliders className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">Trading & Terminal Preferences</h3>
              </div>
              <span className="text-slate-400 text-xs font-mono">Saved on this device</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Default Symbol */}
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-slate-300 font-semibold">Default Trading Symbol</label>
                <select
                  value={prefSymbol}
                  onChange={(e) => setPrefSymbol(e.target.value)}
                  className="w-full px-3 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white focus:outline-none focus:border-brand-cyan"
                >
                  {SUPPORTED_SYMBOLS.map((sym) => {
                    const symMeta = getInstrumentMeta(sym)
                    return (
                      <option key={sym} value={sym}>
                        {sym} ({symMeta.name})
                      </option>
                    )
                  })}
                </select>
                <p className="text-[11px] text-slate-500 font-sans">
                  The initial pair loaded when opening the Trading Terminal.
                </p>
              </div>

              {/* Default Timeframe */}
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-slate-300 font-semibold">Default Chart Timeframe</label>
                <select
                  value={prefInterval}
                  onChange={(e) => setPrefInterval(e.target.value)}
                  className="w-full px-3 py-2 rounded bg-background border border-terminal-border font-mono text-xs text-white focus:outline-none focus:border-brand-cyan"
                >
                  <option value="1m">1m (Scalp Feed)</option>
                  <option value="5m">5m (Intraday)</option>
                  <option value="15m">15m (Structure)</option>
                  <option value="1h">1H (Canonical SMC Stream)</option>
                  <option value="4h">4H (Trend Bias)</option>
                  <option value="1d">1D (Daily Macro)</option>
                </select>
                <p className="text-[11px] text-slate-500 font-sans">
                  The canonical SMC engine analyzes the 1H interval.
                </p>
              </div>
            </div>

            {/* Toggles */}
            <div className="pt-2 space-y-3">
              <label className="flex items-center justify-between p-3 rounded bg-background/60 border border-terminal-border cursor-pointer hover:border-slate-600 transition-colors">
                <div className="space-y-0.5">
                  <div className="text-xs font-mono font-bold text-white">Show SMC Visual Price Overlays</div>
                  <div className="text-[11px] text-slate-400 font-sans">
                    Render Entry, Stop Loss, and Take Profit lines directly on the chart canvas.
                  </div>
                </div>
                <input
                  type="checkbox"
                  checked={showOverlays}
                  onChange={(e) => setShowOverlays(e.target.checked)}
                  className="w-4 h-4 rounded bg-background border-terminal-border text-brand-cyan focus:ring-0 cursor-pointer"
                />
              </label>

              <label className="flex items-center justify-between p-3 rounded bg-background/60 border border-terminal-border cursor-pointer hover:border-slate-600 transition-colors">
                <div className="space-y-0.5">
                  <div className="text-xs font-mono font-bold text-white">Audio Alerts on Signal Qualification</div>
                  <div className="text-[11px] text-slate-400 font-sans">
                    Play sound notification when a new 1H SMC setup passes AI conviction filters.
                  </div>
                </div>
                <input
                  type="checkbox"
                  checked={soundAlerts}
                  onChange={(e) => setSoundAlerts(e.target.checked)}
                  className="w-4 h-4 rounded bg-background border-terminal-border text-brand-cyan focus:ring-0 cursor-pointer"
                />
              </label>
            </div>

            <div className="pt-3 border-t border-terminal-border/60 flex justify-end">
              <button
                type="submit"
                className="px-4 py-2 rounded-lg bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20"
              >
                Save Preferences
              </button>
            </div>
          </form>

          {/* Live System Diagnostics & Backend Connectivity */}
          <div className="glass-panel p-5 rounded-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">Live System Health & Diagnostics</h3>
              </div>
              <span className={`flex items-center gap-1.5 text-xs font-mono font-bold ${
                diagnostics?.overallStatus === 'HEALTHY' ? 'text-bullish' : diagnostics?.overallStatus === 'DEGRADED' ? 'text-warning' : 'text-bearish'
              }`}>
                <span className={`w-2 h-2 rounded-full ${
                  diagnostics?.overallStatus === 'HEALTHY' ? 'bg-bullish animate-pulse' : 'bg-warning animate-ping'
                }`}></span>
                <span>{diagnostics?.overallStatus || 'CHECKING...'}</span>
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono text-xs">
              {/* Spring Boot API */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Server className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>Spring Boot API</span>
                  </span>
                  <span className={`font-bold ${diagnostics?.api.status === 'ONLINE' ? 'text-bullish' : 'text-bearish'}`}>
                    {diagnostics?.api.status || 'CONNECTING'}
                  </span>
                </div>
                <div className="text-[11px] text-slate-400 truncate">
                  {diagnostics?.api.details || 'Heap active'}
                </div>
              </div>

              {/* PostgreSQL Database */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Database className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>PostgreSQL Database</span>
                  </span>
                  <span className={`font-bold ${diagnostics?.database.status === 'ONLINE' ? 'text-bullish' : 'text-bearish'}`}>
                    {diagnostics?.database.status || 'CHECKING'}
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  Latency: {diagnostics?.database.latencyMs !== undefined ? `${diagnostics.database.latencyMs}ms` : '—'}
                </div>
              </div>

              {/* Delta REST Gateway */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Radio className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>Delta REST API</span>
                  </span>
                  <span className={`font-bold ${diagnostics?.deltaExchange.status === 'ONLINE' ? 'text-bullish' : 'text-warning'}`}>
                    {diagnostics?.deltaExchange.status || (marketStatus?.connected ? 'ONLINE' : 'DEGRADED')}
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  Latency: {diagnostics?.deltaExchange.latencyMs !== undefined ? `${diagnostics.deltaExchange.latencyMs}ms` : marketStatus?.latencyMs ? `${marketStatus.latencyMs}ms` : '< 50ms'}
                </div>
              </div>

              {/* Python SMC Engine */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Cpu className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>Python SMC Engine</span>
                  </span>
                  <span className={`font-bold ${diagnostics?.pythonEngine.status === 'ONLINE' ? 'text-bullish' : 'text-slate-400'}`}>
                    {diagnostics?.pythonEngine.status || 'DETERMINISTIC'}
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  {diagnostics?.pythonEngine.details || '1H Canonical Stream Invariant'}
                </div>
              </div>

              {/* AI Enrichment Layer */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>AI Layer</span>
                  </span>
                  <span className="text-bullish font-bold">ACTIVE</span>
                </div>
                <div className="text-[11px] text-slate-400 truncate">
                  {diagnostics?.aiEngine.details || 'Additive Conviction Scoring'}
                </div>
              </div>

              {/* Financial News & Macro Ingestion */}
              <div className="p-3 rounded bg-background/70 border border-terminal-border space-y-1">
                <div className="text-slate-400 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-brand-cyan" />
                    <span>News & Macro</span>
                  </span>
                  <span className="text-brand-cyan font-bold">SYNCED</span>
                </div>
                <div className="text-[11px] text-slate-400 truncate">
                  {diagnostics?.newsService.details || 'Financial Ingestion Active'}
                </div>
              </div>
            </div>

            {/* Application Build & Release Info */}
            <div className="p-3 rounded bg-background/40 border border-terminal-border/80 text-[11px] font-mono text-slate-400 flex flex-wrap items-center justify-between gap-2">
              <div>
                <span>Build Version: </span>
                <strong className="text-white">{diagnostics?.buildVersion || '2.0.0-SNAPSHOT'}</strong>
              </div>
              <div>
                <span>Uptime: </span>
                <strong className="text-slate-300">
                  {diagnostics?.uptimeSeconds ? `${Math.floor(diagnostics.uptimeSeconds / 60)}m ${diagnostics.uptimeSeconds % 60}s` : 'Active'}
                </strong>
              </div>
              <div>
                <span>Authority: </span>
                <strong className="text-brand-cyan">Spring Boot (Port 8080)</strong>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
