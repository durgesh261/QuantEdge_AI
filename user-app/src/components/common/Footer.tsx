import React from 'react'
import { Wifi, Lock } from 'lucide-react'

export const Footer: React.FC = () => {
  return (
    <footer className="h-7 bg-background-surface border-t border-terminal-border px-4 flex items-center justify-between text-[11px] font-mono text-slate-400 select-none">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5 text-bullish">
          <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse"></span>
          Engine: ONLINE
        </span>
        <span className="text-slate-600">|</span>
        <span className="text-slate-300">Timeframe: 1H (H1 Stream)</span>
        <span className="text-slate-600">|</span>
        <span className="text-slate-300">Strategy: Order Block + RR ≥ 2.0</span>
      </div>

      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1 text-slate-400">
          <Lock className="w-3 h-3 text-brand-cyan" />
          Execution: OrderExecutionService.java (Authoritative)
        </span>
        <span className="text-slate-600">|</span>
        <span className="flex items-center gap-1 text-slate-400">
          <Wifi className="w-3 h-3 text-bullish" />
          DELTAIN Feed: 42ms
        </span>
      </div>
    </footer>
  )
}
