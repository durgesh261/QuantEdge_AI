import React from 'react'
import { LucideIcon, Inbox } from 'lucide-react'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon = Inbox,
  title,
  description,
  actionLabel,
  onAction,
}) => {
  return (
    <div className="py-12 px-4 text-center flex flex-col items-center justify-center space-y-3 font-mono">
      <div className="p-3 rounded-full bg-background-elevated border border-terminal-border text-slate-500">
        <Icon className="w-6 h-6" />
      </div>
      <div className="space-y-1 max-w-sm">
        <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wide">{title}</h4>
        {description && <p className="text-[11px] text-slate-500 font-sans">{description}</p>}
      </div>
      {actionLabel && onAction && (
        <button
          onClick={onAction}
          className="mt-2 px-3 py-1.5 rounded bg-dev-accent/15 border border-dev-accent/30 text-dev-accent hover:bg-dev-accent/25 text-xs font-bold transition-colors"
        >
          {actionLabel}
        </button>
      )}
    </div>
  )
}
