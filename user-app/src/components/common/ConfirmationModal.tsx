import React, { useEffect } from 'react'
import { AlertTriangle, AlertOctagon, Info, X } from 'lucide-react'

interface ConfirmationModalProps {
  isOpen: boolean
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'warning' | 'info'
  isLoading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export const ConfirmationModal: React.FC<ConfirmationModalProps> = ({
  isOpen,
  title,
  description,
  confirmText = 'Confirm Action',
  cancelText = 'Cancel',
  variant = 'warning',
  isLoading = false,
  onConfirm,
  onCancel,
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isLoading) {
        onCancel()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isLoading, onCancel])

  if (!isOpen) return null

  const getVariantStyles = () => {
    switch (variant) {
      case 'danger':
        return {
          icon: <AlertOctagon className="w-5 h-5 text-bearish" />,
          btn: 'bg-bearish hover:bg-bearish/90 text-white shadow-bearish/20',
          border: 'border-bearish/30',
        }
      case 'info':
        return {
          icon: <Info className="w-5 h-5 text-brand-cyan" />,
          btn: 'bg-brand-cyan hover:bg-brand-cyan/90 text-background shadow-brand-cyan/20',
          border: 'border-brand-cyan/30',
        }
      default:
        return {
          icon: <AlertTriangle className="w-5 h-5 text-warning" />,
          btn: 'bg-warning hover:bg-warning/90 text-background shadow-warning/20',
          border: 'border-warning/30',
        }
    }
  }

  const styles = getVariantStyles()

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-fade-in">
      <div
        className={`glass-panel-elevated p-6 rounded-xl max-w-md w-full border ${styles.border} shadow-2xl space-y-4 animate-scale-up`}
      >
        <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
          <div className="flex items-center gap-2.5">
            {styles.icon}
            <h3 className="text-sm font-bold text-white font-mono">{title}</h3>
          </div>
          <button
            onClick={onCancel}
            disabled={isLoading}
            className="p-1 rounded hover:bg-background text-slate-400 hover:text-white disabled:opacity-50"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="text-xs text-slate-300 font-sans leading-relaxed">
          {description}
        </div>

        <div className="flex items-center justify-end gap-3 pt-3 border-t border-terminal-border/60 font-mono text-xs">
          <button
            type="button"
            onClick={onCancel}
            disabled={isLoading}
            className="px-4 py-2 rounded-lg bg-background-elevated hover:bg-slate-700 text-slate-300 font-semibold transition-all border border-terminal-border disabled:opacity-50"
          >
            {cancelText}
          </button>

          <button
            type="button"
            onClick={onConfirm}
            disabled={isLoading}
            className={`px-4 py-2 rounded-lg font-bold transition-all shadow-md flex items-center gap-1.5 disabled:opacity-50 ${styles.btn}`}
          >
            {isLoading && <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin"></span>}
            <span>{confirmText}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
