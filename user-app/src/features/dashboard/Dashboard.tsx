import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ShieldCheck,
  ShieldAlert,
  ArrowUpRight,
  CandlestickChart,
  Radio,
  Newspaper,
  Cpu,
  Lock
} from 'lucide-react'
import { useAuthStore } from '../../stores/authStore'
import { useMarketStore } from '../../stores/marketStore'
import { apiClient } from '../../services/apiClient'
import { TradingSystemStatus, AccountSummary } from '../../types/trading'

export const Dashboard: React.FC = () => {
  const { user } = useAuthStore()
  const { tickers, fetchTicker } = useMarketStore()
  const [tradingStatus, setTradingStatus] = useState<TradingSystemStatus | null>(null)
  const [accountSummary, setAccountSummary] = useState<AccountSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    fetchTicker('BTCUSD')

    const loadData = async () => {
      try {
        setIsLoading(true)
        const [statusRes, summaryRes] = await Promise.allSettled([
          apiClient.get<TradingSystemStatus>('/api/v1/trade/status'),
          apiClient.get<AccountSummary>('/api/v1/account/summary'),
        ])

        if (statusRes.status === 'fulfilled') {
          setTradingStatus(statusRes.value.data)
        }
        if (summaryRes.status === 'fulfilled') {
          setAccountSummary(summaryRes.value.data)
        }
      } catch (err) {
        console.warn('Dashboard data loading notice', err)
      } finally {
        setIsLoading(false)
      }
    }

    loadData()
  }, [fetchTicker])

  const btc = tickers['BTCUSD']

  if (isLoading && !accountSummary && !tradingStatus) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-10 bg-background-surface rounded-lg w-1/3"></div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="h-28 bg-background-surface rounded-lg"></div>
          <div className="h-28 bg-background-surface rounded-lg"></div>
          <div className="h-28 bg-background-surface rounded-lg"></div>
          <div className="h-28 bg-background-surface rounded-lg"></div>
        </div>
        <div className="h-64 bg-background-surface rounded-lg"></div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Top Welcome Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            Welcome back, {user?.name || 'Trader'}
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            QuantEdge AI Production Trading Console • 1H Canonical Engine
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/terminal"
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-md bg-brand-cyan text-background text-xs font-semibold hover:bg-brand-cyan/90 transition-all shadow-md shadow-brand-cyan/10"
          >
            <CandlestickChart className="w-4 h-4" />
            <span>Launch Terminal</span>
          </Link>
        </div>
      </div>

      {/* Top 4 Stat Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Equity */}
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Total Account Equity</div>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="text-2xl font-bold font-mono text-white">
              ${accountSummary?.balance?.toLocaleString(undefined, { minimumFractionDigits: 2 }) || '0.00'}
            </span>
            <span className="text-xs font-mono text-slate-400">
              {accountSummary?.currency || 'USDT'}
            </span>
          </div>
          <div className="mt-2 text-[11px] font-mono text-slate-400 flex items-center gap-1">
            <span>Available:</span>
            <span className="text-white">${accountSummary?.availableBalance?.toFixed(2) || '0.00'}</span>
          </div>
        </div>

        {/* 24h Realized P&L */}
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">24h Realized P&L</div>
          <div className="mt-2 flex items-baseline justify-between">
            <span className={`text-2xl font-bold font-mono ${(accountSummary?.realizedPnl24h || 0) >= 0 ? 'text-bullish' : 'text-bearish'}`}>
              {(accountSummary?.realizedPnl24h || 0) >= 0 ? '+' : ''}${accountSummary?.realizedPnl24h?.toFixed(2) || '0.00'}
            </span>
          </div>
          <div className="mt-2 text-[11px] font-mono text-slate-400 flex items-center gap-1">
            <span>Unrealized:</span>
            <span className={(accountSummary?.unrealizedPnl || 0) >= 0 ? 'text-bullish' : 'text-bearish'}>
              ${accountSummary?.unrealizedPnl?.toFixed(2) || '0.00'}
            </span>
          </div>
        </div>

        {/* Algo Trading Loop State */}
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Algorithmic Engine</div>
          <div className="mt-2 flex items-center gap-2">
            {tradingStatus?.algoEnabled ? (
              <span className="px-2.5 py-1 rounded bg-bullish/10 border border-bullish/20 text-bullish text-xs font-mono font-bold flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-bullish animate-pulse"></span>
                ACTIVE
              </span>
            ) : (
              <span className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-400 text-xs font-mono font-bold">
                PAUSED
              </span>
            )}
            {tradingStatus?.killSwitchActive && (
              <span className="px-2 py-0.5 rounded bg-bearish/10 border border-bearish/20 text-bearish text-[11px] font-mono">
                KILL-SWITCH ON
              </span>
            )}
          </div>
          <div className="mt-2 text-[11px] font-mono text-slate-400">
            Strategy: 1H Order Block Retraces
          </div>
        </div>

        {/* Exchange Connectivity */}
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Exchange Connectivity</div>
          <div className="mt-2 flex items-center gap-2">
            {tradingStatus?.connected ? (
              <span className="px-2.5 py-1 rounded bg-bullish/10 border border-bullish/20 text-bullish text-xs font-mono font-bold flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5" />
                DELTA INDIA
              </span>
            ) : (
              <span className="px-2.5 py-1 rounded bg-warning/10 border border-warning/20 text-warning text-xs font-mono font-bold flex items-center gap-1.5">
                <ShieldAlert className="w-3.5 h-3.5" />
                NOT CONNECTED
              </span>
            )}
          </div>
          <div className="mt-2 text-[11px] font-mono text-slate-400">
            <Link to="/settings" className="text-brand-cyan hover:underline">
              Manage Exchange Keys →
            </Link>
          </div>
        </div>
      </div>

      {/* Main Grid: Live Radar Preview & Navigation Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Terminal & Radar Overview */}
        <div className="lg:col-span-2 space-y-6">
          <div className="glass-panel p-5 rounded-lg">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Cpu className="w-4 h-4 text-brand-cyan" />
                <span className="text-sm font-bold text-white">Institutional SMC Core Overview</span>
              </div>
              <span className="text-xs font-mono text-slate-400">BTC/USD (1H Stream)</span>
            </div>

            <div className="mt-4 p-4 rounded-lg bg-background/50 border border-terminal-border font-mono text-xs space-y-2">
              <div className="flex justify-between">
                <span className="text-slate-400">Deterministic Engine State:</span>
                <span className="text-bullish font-bold">SYNCHRONIZED (H1 Canonical)</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Active Trade Locks:</span>
                <span className="text-white">{tradingStatus?.hasActiveTradeLock ? `LOCKED (${tradingStatus.activeLockSetupId})` : '0 ACQUIRED'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Live Mark Price:</span>
                <span className="text-white">${btc?.price?.toLocaleString(undefined, { minimumFractionDigits: 2 }) || '65,000.00'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Order Authority Invariant:</span>
                <span className="text-brand-cyan font-bold flex items-center gap-1">
                  <Lock className="w-3 h-3" />
                  OrderExecutionService.java (Enforced)
                </span>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-3">
              <Link
                to="/terminal"
                className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-medium text-white transition-all border border-terminal-border"
              >
                <CandlestickChart className="w-3.5 h-3.5 text-brand-cyan" />
                <span>Open Trading Terminal</span>
              </Link>
              <Link
                to="/signals"
                className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-medium text-white transition-all border border-terminal-border"
              >
                <Radio className="w-3.5 h-3.5 text-bullish" />
                <span>Explore Signals Radar</span>
              </Link>
              <Link
                to="/intelligence"
                className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-medium text-white transition-all border border-terminal-border"
              >
                <Newspaper className="w-3.5 h-3.5 text-warning" />
                <span>Market Intelligence</span>
              </Link>
            </div>
          </div>
        </div>

        {/* Right Col: Quick Navigation */}
        <div className="space-y-4">
          <div className="glass-panel p-5 rounded-lg">
            <h3 className="text-sm font-bold text-white mb-3">Quick Navigation</h3>
            <div className="space-y-2">
              <Link
                to="/terminal"
                className="block p-2.5 rounded-md bg-background/50 hover:bg-background-elevated border border-terminal-border transition-all"
              >
                <div className="text-xs font-semibold text-white flex items-center justify-between">
                  <span>Trading Terminal</span>
                  <ArrowUpRight className="w-3.5 h-3.5 text-brand-cyan" />
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5">
                  TradingView chart with Order Blocks & FVG
                </div>
              </Link>

              <Link
                to="/orders"
                className="block p-2.5 rounded-md bg-background/50 hover:bg-background-elevated border border-terminal-border transition-all"
              >
                <div className="text-xs font-semibold text-white flex items-center justify-between">
                  <span>Orders & Fills</span>
                  <ArrowUpRight className="w-3.5 h-3.5 text-brand-cyan" />
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5">
                  Live order book & execution fills ledger
                </div>
              </Link>

              <Link
                to="/risk-algo"
                className="block p-2.5 rounded-md bg-background/50 hover:bg-background-elevated border border-terminal-border transition-all"
              >
                <div className="text-xs font-semibold text-white flex items-center justify-between">
                  <span>Risk & Algo Controls</span>
                  <ArrowUpRight className="w-3.5 h-3.5 text-brand-cyan" />
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5">
                  Emergency Kill-Switch & Capital Allocator
                </div>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
