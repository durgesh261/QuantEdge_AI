import React, { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { 
  Bell, 
  LogOut, 
  Menu,
  Cpu
} from 'lucide-react'
import { useAuthStore } from '../../stores/authStore'
import { useMarketStore } from '../../stores/marketStore'
import { useUIStore } from '../../stores/uiStore'

export const Header: React.FC = () => {
  const { user, logout } = useAuthStore()
  const { tickers, fetchTicker } = useMarketStore()
  const { toggleSidebar } = useUIStore()

  useEffect(() => {
    fetchTicker('BTCUSD')
    fetchTicker('ETHUSD')
    fetchTicker('SOLUSD')

    const interval = setInterval(() => {
      fetchTicker('BTCUSD')
      fetchTicker('ETHUSD')
      fetchTicker('SOLUSD')
    }, 5000)

    return () => clearInterval(interval)
  }, [fetchTicker])

  const btc = tickers['BTCUSD']
  const eth = tickers['ETHUSD']
  const sol = tickers['SOLUSD']

  return (
    <header className="h-14 border-b border-terminal-border bg-background-surface/90 backdrop-blur-md px-4 flex items-center justify-between sticky top-0 z-30">
      {/* Left: Brand & Sidebar Toggle */}
      <div className="flex items-center gap-4">
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-white transition-colors"
          title="Toggle Navigation"
        >
          <Menu className="w-5 h-5" />
        </button>

        <Link to="/" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-cyan to-brand-blue flex items-center justify-center shadow-lg shadow-brand-cyan/20">
            <Cpu className="w-5 h-5 text-background font-bold" />
          </div>
          <div>
            <span className="font-bold tracking-tight text-white flex items-center gap-1.5">
              QuantEdge <span className="text-brand-cyan text-xs font-mono px-1.5 py-0.5 rounded bg-brand-cyan/10 border border-brand-cyan/20">AI</span>
            </span>
          </div>
        </Link>
      </div>

      {/* Middle: Live Market Ticker Marquee */}
      <div className="hidden md:flex items-center gap-6 text-xs font-mono">
        {btc && (
          <div className="flex items-center gap-2 bg-background/60 px-2.5 py-1 rounded-md border border-terminal-border/80">
            <span className="text-slate-400 font-semibold">BTC/USD</span>
            <span className="text-white font-bold">${(btc.markPrice ?? btc.lastPrice)?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            <span className={`flex items-center ${(btc.priceChangePercent24h ?? 0) >= 0 ? 'text-bullish' : 'text-bearish'}`}>
              {(btc.priceChangePercent24h ?? 0) >= 0 ? '+' : ''}{btc.priceChangePercent24h?.toFixed(2) ?? '0.00'}%
            </span>
          </div>
        )}

        {eth && (
          <div className="flex items-center gap-2 bg-background/60 px-2.5 py-1 rounded-md border border-terminal-border/80">
            <span className="text-slate-400 font-semibold">ETH/USD</span>
            <span className="text-white font-bold">${(eth.markPrice ?? eth.lastPrice)?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            <span className={`flex items-center ${(eth.priceChangePercent24h ?? 0) >= 0 ? 'text-bullish' : 'text-bearish'}`}>
              {(eth.priceChangePercent24h ?? 0) >= 0 ? '+' : ''}{eth.priceChangePercent24h?.toFixed(2) ?? '0.00'}%
            </span>
          </div>
        )}

        {sol && (
          <div className="flex items-center gap-2 bg-background/60 px-2.5 py-1 rounded-md border border-terminal-border/80">
            <span className="text-slate-400 font-semibold">SOL/USD</span>
            <span className="text-white font-bold">${(sol.markPrice ?? sol.lastPrice)?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            <span className={`flex items-center ${(sol.priceChangePercent24h ?? 0) >= 0 ? 'text-bullish' : 'text-bearish'}`}>
              {(sol.priceChangePercent24h ?? 0) >= 0 ? '+' : ''}{sol.priceChangePercent24h?.toFixed(2) ?? '0.00'}%
            </span>
          </div>
        )}
      </div>

      {/* Right: User Status & Actions */}
      <div className="flex items-center gap-3">
        {/* Stream Health Indicator */}
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-bullish/10 border border-bullish/20 text-bullish text-xs font-mono">
          <span className="w-2 h-2 rounded-full bg-bullish animate-pulse"></span>
          <span>1H SMC LIVE</span>
        </div>

        {/* Notifications Icon */}
        <button className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-white transition-colors relative">
          <Bell className="w-4 h-4" />
          <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-brand-cyan"></span>
        </button>

        {/* User Menu & Logout */}
        <div className="flex items-center gap-2 pl-2 border-l border-terminal-border">
          <div className="hidden sm:block text-right">
            <div className="text-xs font-medium text-white">{user?.name || 'Trader'}</div>
            <div className="text-[10px] font-mono text-slate-400">{user?.role || 'ROLE_USER'}</div>
          </div>
          <button
            onClick={() => logout()}
            className="p-1.5 rounded-md hover:bg-bearish/10 text-slate-400 hover:text-bearish transition-colors"
            title="Sign Out"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  )
}
