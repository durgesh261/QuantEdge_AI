import React from 'react'

export const SkeletonStat: React.FC = () => (
  <div className="glass-panel p-4 rounded-lg border border-terminal-border animate-pulse space-y-2">
    <div className="h-3 bg-slate-800 rounded w-20"></div>
    <div className="h-6 bg-slate-700/80 rounded w-32"></div>
    <div className="h-2.5 bg-slate-800 rounded w-24"></div>
  </div>
)

export const SkeletonCard: React.FC<{ rows?: number }> = ({ rows = 3 }) => (
  <div className="glass-panel p-4 rounded-lg border border-terminal-border animate-pulse space-y-3">
    <div className="h-4 bg-slate-700 rounded w-1/3 mb-4"></div>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="h-3 bg-slate-800 rounded w-full"></div>
    ))}
  </div>
)

export const SkeletonTable: React.FC<{ rows?: number; cols?: number }> = ({
  rows = 5,
  cols = 6,
}) => (
  <div className="glass-panel p-3 rounded-lg border border-terminal-border animate-pulse overflow-x-auto">
    <div className="flex gap-4 pb-3 border-b border-terminal-border/60">
      {Array.from({ length: cols }).map((_, i) => (
        <div key={i} className="h-3 bg-slate-700 rounded flex-1"></div>
      ))}
    </div>
    <div className="space-y-3 pt-3">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: cols }).map((_, c) => (
            <div key={c} className="h-3 bg-slate-800 rounded flex-1"></div>
          ))}
        </div>
      ))}
    </div>
  </div>
)
