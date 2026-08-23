import React from 'react'
import { Link } from 'react-router-dom'
import { LucideIcon, Inbox } from 'lucide-react'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description: string
  actionLabel?: string
  onAction?: () => void
  actionLink?: string
  className?: string
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon = Inbox,
  title,
  description,
  actionLabel,
  onAction,
  actionLink,
  className = 'py-16',
}) => {
  return (
    <div className={`text-center flex flex-col items-center justify-center p-6 space-y-3 ${className}`}>
      <div className="p-3 rounded-full bg-background border border-terminal-border text-slate-500">
        <Icon className="w-6 h-6" />
      </div>
      <div className="space-y-1 max-w-sm">
        <h4 className="text-sm font-bold text-slate-300 font-mono">{title}</h4>
        <p className="text-xs text-slate-400 font-sans leading-relaxed">{description}</p>
      </div>

      {actionLabel && (
        <div className="pt-2">
          {actionLink ? (
            <Link
              to={actionLink}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20"
            >
              {actionLabel}
            </Link>
          ) : onAction ? (
            <button
              onClick={onAction}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20"
            >
              {actionLabel}
            </button>
          ) : null}
        </div>
      )}
    </div>
  )
}
