import React from 'react';
import { Bell, X, CheckCheck, Trash2, Info, CheckCircle, AlertTriangle, AlertOctagon } from 'lucide-react';
import { useNotificationStore, Notification } from '../../store/useNotificationStore';

const typeConfig = {
  info: { icon: Info, color: 'text-[#3B82F6]', bg: 'bg-[#3B82F6]/10', border: 'border-[#3B82F6]/20' },
  success: { icon: CheckCircle, color: 'text-[#00C896]', bg: 'bg-[#00C896]/10', border: 'border-[#00C896]/20' },
  warning: { icon: AlertTriangle, color: 'text-[#F59E0B]', bg: 'bg-[#F59E0B]/10', border: 'border-[#F59E0B]/20' },
  error: { icon: AlertOctagon, color: 'text-[#F6465D]', bg: 'bg-[#F6465D]/10', border: 'border-[#F6465D]/20' },
};

function timeAgo(date: Date) {
  const now = Date.now();
  const diff = Math.floor((now - new Date(date).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const NotificationPanel: React.FC = () => {
  const { notifications, markAllAsRead, clearAll, setPanelOpen, unreadCount } = useNotificationStore();

  return (
    <div className="absolute top-full right-0 mt-2 w-[360px] bg-[#161D2A] border border-[#1E293B] rounded-xl shadow-2xl shadow-black/50 z-[9999] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1E293B]">
        <div className="flex items-center space-x-2">
          <Bell className="w-4 h-4 text-[#3B82F6]" />
          <span className="text-[12px] font-bold text-[#F8FAFC]">Notifications</span>
          {unreadCount > 0 && (
            <span className="px-1.5 py-0.5 bg-[#3B82F6] rounded-full text-[9px] font-bold text-white">
              {unreadCount}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          {unreadCount > 0 && (
            <button
              onClick={markAllAsRead}
              className="flex items-center space-x-1 text-[10px] text-[#64748B] hover:text-[#94A3B8] transition-colors"
            >
              <CheckCheck className="w-3 h-3" />
              <span>Mark all read</span>
            </button>
          )}
          {notifications.length > 0 && (
            <button
              onClick={clearAll}
              className="p-1 hover:bg-[#F6465D]/10 rounded text-[#64748B] hover:text-[#F6465D] transition-colors"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          )}
          <button
            onClick={() => setPanelOpen(false)}
            className="p-1 hover:bg-[#1E293B] rounded text-[#64748B] hover:text-[#94A3B8] transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Notifications list */}
      <div className="max-h-[400px] overflow-y-auto">
        {notifications.length === 0 ? (
          <div className="py-12 text-center">
            <Bell className="w-8 h-8 text-[#334155] mx-auto mb-3" />
            <p className="text-[11px] text-[#64748B]">No notifications</p>
            <p className="text-[9px] text-[#475569] mt-1">You're all caught up!</p>
          </div>
        ) : (
          notifications.map((n: Notification) => {
            const cfg = typeConfig[n.type] || typeConfig.info;
            const Icon = cfg.icon;
            return (
              <div
                key={n.id}
                className={`px-4 py-3 border-b border-[#1E293B] last:border-0 hover:bg-[#1E293B]/50 transition-colors ${!n.read ? 'bg-[#1E293B]/30' : ''}`}
              >
                <div className="flex items-start space-x-3">
                  <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${cfg.bg} border ${cfg.border}`}>
                    <Icon className={`w-3.5 h-3.5 ${cfg.color}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold text-[#F8FAFC] truncate">{n.title}</span>
                      {!n.read && (
                        <span className="w-1.5 h-1.5 rounded-full bg-[#3B82F6] flex-shrink-0 ml-2" />
                      )}
                    </div>
                    <p className="text-[10px] text-[#94A3B8] mt-0.5 leading-relaxed">{n.message}</p>
                    <span className="text-[9px] text-[#475569] mt-1 block">{timeAgo(n.timestamp)}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
