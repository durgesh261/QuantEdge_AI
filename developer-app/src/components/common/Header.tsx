import React, { useState, useEffect } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { useUIStore } from '../../stores/uiStore'
import {
  Terminal,
  Menu,
  LogOut,
  Activity,
} from 'lucide-react'

export const Header: React.FC = () => {
  const { user, logout } = useAuthStore()
  const { toggleSidebar } = useUIStore()
  const [syncTime, setSyncTime] = useState<string>(new Date().toLocaleTimeString())

  useEffect(() => {
    const timer = setInterval(() => {
      setSyncTime(new Date().toLocaleTimeString())
    }, 5000)
    return () => clearInterval(timer)
  }, [])

  return (
    <header className="h-14 bg-background-surface/95 backdrop-blur-md border-b border-terminal-border px-4 flex items-center justify-between sticky top-0 z-30 font-mono">
      {/* Left: Brand & Sidebar Toggle */}
      <div className="flex items-center gap-3">
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded hover:bg-background-elevated text-slate-400 hover:text-white transition-colors"
          title="Toggle Navigation"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-dev-accent/15 border border-dev-accent/30 text-dev-accent">
            <Terminal className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-white tracking-wider text-sm">QUANTEDGE</span>
              <span className="px-1.5 py-0.2 rounded bg-dev-accent/20 border border-dev-accent/40 text-[10px] text-dev-accent font-bold">
                DEV/OPS CONSOLE
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Center: System Telemetry Indicator */}
      <div className="hidden md:flex items-center gap-4 text-xs">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-background border border-terminal-border text-slate-300">
          <span className="w-2 h-2 rounded-full bg-dev-accent animate-pulse"></span>
          <span>ENGINE: <strong>ONLINE</strong></span>
        </div>
        <div className="flex items-center gap-1.5 text-slate-400 text-[11px]">
          <Activity className="w-3.5 h-3.5 text-dev-cyan" />
          <span>Synced: <strong className="text-slate-200">{syncTime}</strong></span>
        </div>
      </div>

      {/* Right: Operator Identity & Logout */}
      <div className="flex items-center gap-3">
        {user && (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded bg-background/80 border border-terminal-border text-xs">
            <span className="text-slate-400 truncate max-w-[120px]">{user.name || user.email}</span>
            <span className="px-1.5 py-0.2 rounded bg-dev-purple/15 border border-dev-purple/30 text-dev-purple font-bold text-[10px]">
              {user.role || 'DEVELOPER'}
            </span>
          </div>
        )}

        <button
          onClick={logout}
          className="p-1.5 rounded hover:bg-bearish/20 text-slate-400 hover:text-bearish transition-colors"
          title="Sign Out of Console"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  )
}
