import React from 'react'

interface SkeletonProps {
  className?: string
}

export const Skeleton: React.FC<SkeletonProps> = ({ className = 'h-4 w-full' }) => {
  return (
    <div
      className={`animate-pulse rounded bg-slate-800/80 border border-terminal-border/40 ${className}`}
    />
  )
}

export const SkeletonStat: React.FC = () => {
  return (
    <div className="glass-panel p-4 rounded-lg space-y-2 animate-pulse">
      <div className="h-3 w-24 bg-slate-800 rounded"></div>
      <div className="h-7 w-32 bg-slate-700 rounded"></div>
      <div className="h-2.5 w-20 bg-slate-800/60 rounded"></div>
    </div>
  )
}

interface SkeletonTableProps {
  rows?: number
  cols?: number
}

export const SkeletonTable: React.FC<SkeletonTableProps> = ({ rows = 5, cols = 5 }) => {
  return (
    <div className="divide-y divide-terminal-border/50 animate-pulse">
      {Array.from({ length: rows }).map((_, rIdx) => (
        <div key={rIdx} className="p-3.5 flex items-center justify-between gap-4">
          {Array.from({ length: cols }).map((_, cIdx) => (
            <div
              key={cIdx}
              className="h-3.5 bg-slate-800 rounded"
              style={{ width: `${Math.floor(60 + ((rIdx + cIdx) % 4) * 20)}px` }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export const SkeletonCard: React.FC<{ rows?: number }> = ({ rows = 4 }) => {
  return (
    <div className="glass-panel p-5 rounded-lg space-y-3 animate-pulse">
      <div className="flex justify-between items-center pb-2 border-b border-terminal-border">
        <div className="h-4 w-32 bg-slate-700 rounded"></div>
        <div className="h-3 w-16 bg-slate-800 rounded"></div>
      </div>
      <div className="space-y-2 pt-1">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex justify-between items-center py-1">
            <div className="h-3 w-24 bg-slate-800 rounded"></div>
            <div className="h-3 w-20 bg-slate-700 rounded"></div>
          </div>
        ))}
      </div>
    </div>
  )
}

export const SkeletonChart: React.FC = () => {
  return (
    <div className="w-full h-full min-h-[380px] bg-background-surface/80 rounded-lg p-4 flex flex-col justify-between animate-pulse border border-terminal-border">
      <div className="flex justify-between items-center pb-3 border-b border-terminal-border/60">
        <div className="flex gap-2">
          <div className="h-5 w-20 bg-slate-700 rounded"></div>
          <div className="h-5 w-12 bg-slate-800 rounded"></div>
        </div>
        <div className="flex gap-1.5">
          {['1m', '5m', '15m', '1H', '4H', '1D'].map((t) => (
            <div key={t} className="h-5 w-8 bg-slate-800/80 rounded"></div>
          ))}
        </div>
      </div>
      <div className="flex-1 flex items-end justify-between gap-1.5 py-6 px-4 opacity-40">
        {Array.from({ length: 24 }).map((_, idx) => (
          <div
            key={idx}
            className="w-full bg-slate-700 rounded-t"
            style={{ height: `${20 + ((idx * 17) % 70)}%` }}
          />
        ))}
      </div>
      <div className="h-3 w-full bg-slate-800/60 rounded"></div>
    </div>
  )
}
