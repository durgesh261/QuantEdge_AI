import React, { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useNotificationStore } from '../../stores/notificationStore'
import {
  Bell,
  Check,
  CheckCheck,
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Info,
  ArrowRight,
  X,
} from 'lucide-react'

interface NotificationDropdownProps {
  isOpen: boolean
  onClose: () => void
}

export const NotificationDropdown: React.FC<NotificationDropdownProps> = ({ isOpen, onClose }) => {
  const { notifications, unreadCount, markAsRead, markAllAsRead, fetchNotifications } = useNotificationStore()
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (isOpen) {
      fetchNotifications(false, 20)
    }
  }, [isOpen, fetchNotifications])

  // Close on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        onClose()
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  const getSeverityIcon = (severity: string) => {
    switch (severity?.toUpperCase()) {
      case 'CRITICAL':
      case 'ERROR':
        return <AlertOctagon className="w-3.5 h-3.5 text-bearish shrink-0" />
      case 'WARNING':
        return <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0" />
      case 'SUCCESS':
        return <CheckCircle2 className="w-3.5 h-3.5 text-bullish shrink-0" />
      default:
        return <Info className="w-3.5 h-3.5 text-brand-cyan shrink-0" />
    }
  }

  return (
    <div
      ref={dropdownRef}
      className="absolute right-0 top-11 w-80 sm:w-96 glass-panel-elevated rounded-xl shadow-2xl border border-terminal-border z-50 overflow-hidden flex flex-col max-h-[480px]"
    >
      {/* Dropdown Header */}
      <div className="p-3 border-b border-terminal-border flex items-center justify-between bg-background-surface/90">
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4 text-brand-cyan" />
          <h3 className="text-xs font-bold text-white font-mono">Notifications</h3>
          {unreadCount > 0 && (
            <span className="px-1.5 py-0.2 rounded-full bg-brand-cyan text-background text-[10px] font-bold font-mono">
              {unreadCount} new
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {unreadCount > 0 && (
            <button
              onClick={() => markAllAsRead()}
              className="text-[11px] font-mono text-slate-400 hover:text-brand-cyan transition-colors flex items-center gap-1"
              title="Mark all as read"
            >
              <CheckCheck className="w-3 h-3" />
              <span>Mark all read</span>
            </button>
          )}
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-background text-slate-400 hover:text-white"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Notification Items List */}
      <div className="flex-1 overflow-y-auto divide-y divide-terminal-border/50">
        {notifications.length > 0 ? (
          notifications.slice(0, 8).map((item) => (
            <div
              key={item.id}
              onClick={() => !item.isRead && markAsRead(item.id)}
              className={`p-3 transition-colors flex items-start justify-between gap-3 cursor-pointer ${
                item.isRead
                  ? 'bg-transparent hover:bg-background-elevated/30 opacity-75'
                  : 'bg-brand-cyan/5 hover:bg-brand-cyan/10 border-l-2 border-brand-cyan'
              }`}
            >
              <div className="flex items-start gap-2.5 min-w-0">
                <div className="mt-0.5">{getSeverityIcon(item.severity)}</div>
                <div className="min-w-0 space-y-0.5">
                  <div className="text-xs font-semibold text-white truncate font-mono">
                    {item.title}
                  </div>
                  <p className="text-[11px] text-slate-300 font-sans line-clamp-2 leading-relaxed">
                    {item.message}
                  </p>
                  <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500 pt-0.5">
                    <span>{new Date(item.createdAt).toLocaleTimeString()}</span>
                    {item.referenceId && (
                      <span className="text-brand-cyan font-mono truncate max-w-[120px]">
                        Ref: {item.referenceId}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {!item.isRead && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    markAsRead(item.id)
                  }}
                  className="p-1 rounded hover:bg-background text-slate-400 hover:text-brand-cyan shrink-0"
                  title="Mark as read"
                >
                  <Check className="w-3 h-3" />
                </button>
              )}
            </div>
          ))
        ) : (
          <div className="py-12 text-center text-slate-500 font-mono text-xs space-y-2">
            <Bell className="w-6 h-6 mx-auto text-slate-600" />
            <div>No notifications yet</div>
          </div>
        )}
      </div>

      {/* Dropdown Footer */}
      <div className="p-2.5 border-t border-terminal-border bg-background-surface/80 text-center">
        <Link
          to="/activity"
          onClick={onClose}
          className="text-xs font-mono font-semibold text-brand-cyan hover:text-white transition-colors flex items-center justify-center gap-1.5"
        >
          <span>View All Activity & Audit Logs</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </div>
  )
}
