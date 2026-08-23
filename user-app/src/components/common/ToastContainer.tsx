import React from 'react'
import { useToastStore, ToastType } from '../../stores/toastStore'
import { CheckCircle2, AlertOctagon, AlertTriangle, Info, X } from 'lucide-react'

export const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useToastStore()

  if (toasts.length === 0) return null

  const getIcon = (type: ToastType) => {
    switch (type) {
      case 'success':
        return <CheckCircle2 className="w-4 h-4 text-bullish shrink-0" />
      case 'error':
        return <AlertOctagon className="w-4 h-4 text-bearish shrink-0" />
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
      default:
        return <Info className="w-4 h-4 text-brand-cyan shrink-0" />
    }
  }

  const getBorderColor = (type: ToastType) => {
    switch (type) {
      case 'success':
        return 'border-bullish/30 bg-bullish/5'
      case 'error':
        return 'border-bearish/30 bg-bearish/5'
      case 'warning':
        return 'border-warning/30 bg-warning/5'
      default:
        return 'border-brand-cyan/30 bg-brand-cyan/5'
    }
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none px-3 sm:px-0">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto p-3.5 rounded-lg border backdrop-blur-md shadow-2xl transition-all duration-200 animate-slide-in flex items-start justify-between gap-3 ${getBorderColor(
            t.type
          )} bg-background-surface/95`}
          style={{ minWidth: '280px' }}
        >
          <div className="flex items-start gap-2.5 min-w-0">
            <div className="mt-0.5">{getIcon(t.type)}</div>
            <div className="min-w-0 space-y-0.5">
              <div className="text-xs font-bold text-white font-mono">{t.title}</div>
              {t.message && (
                <div className="text-[11px] text-slate-300 font-sans leading-relaxed break-words">
                  {t.message}
                </div>
              )}
            </div>
          </div>

          <button
            onClick={() => removeToast(t.id)}
            className="p-1 rounded hover:bg-background text-slate-400 hover:text-white shrink-0 transition-colors"
            title="Dismiss notification"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
