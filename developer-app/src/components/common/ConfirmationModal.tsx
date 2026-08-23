import React from 'react'
import { X, Check, Shield } from 'lucide-react'

interface ConfirmationModalProps {
  isOpen: boolean
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'warning' | 'primary'
  isLoading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export const ConfirmationModal: React.FC<ConfirmationModalProps> = ({
  isOpen,
  title,
  description,
  confirmLabel = 'Confirm Action',
  cancelLabel = 'Cancel',
  variant = 'warning',
  isLoading = false,
  onConfirm,
  onCancel,
}) => {
  if (!isOpen) return null

  const isDanger = variant === 'danger'
  const isPrimary = variant === 'primary'

  return (
    <div className="fixed inset-0 bg-background/85 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="glass-panel-elevated p-6 rounded-xl max-w-md w-full border border-terminal-border shadow-2xl space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
          <div className="flex items-center gap-2">
            <Shield className={`w-5 h-5 ${isDanger ? 'text-bearish' : isPrimary ? 'text-dev-cyan' : 'text-warning'}`} />
            <h3 className="font-bold text-white text-sm font-mono">{title}</h3>
          </div>
          <button
            onClick={onCancel}
            disabled={isLoading}
            className="text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-slate-300 font-sans leading-relaxed">
          {description}
        </p>

        <div className="flex items-center justify-end gap-3 pt-3 border-t border-terminal-border">
          <button
            onClick={onCancel}
            disabled={isLoading}
            className="px-3 py-1.5 rounded bg-background border border-terminal-border text-xs font-mono text-slate-300 hover:text-white hover:bg-background-elevated transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={isLoading}
            className={`px-4 py-1.5 rounded text-xs font-mono font-bold flex items-center gap-1.5 transition-all shadow-md ${
              isDanger
                ? 'bg-bearish hover:bg-bearish/90 text-white'
                : isPrimary
                ? 'bg-dev-cyan hover:bg-dev-cyan/90 text-background font-bold'
                : 'bg-warning hover:bg-warning/90 text-background font-bold'
            }`}
          >
            {isLoading ? (
              <span className="w-3.5 h-3.5 border-2 border-background/40 border-t-background rounded-full animate-spin"></span>
            ) : (
              <Check className="w-3.5 h-3.5" />
            )}
            <span>{confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
