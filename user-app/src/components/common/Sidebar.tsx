import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  CandlestickChart,
  Radio,
  Newspaper,
  BookOpen,
  Layers,
  ShieldCheck,
  Activity,
  Settings,
  HelpCircle,
} from 'lucide-react'
import { useUIStore } from '../../stores/uiStore'
import { useNotificationStore } from '../../stores/notificationStore'

export const Sidebar: React.FC = () => {
  const { sidebarOpen } = useUIStore()
  const { unreadCount } = useNotificationStore()

  const navItems = [
    { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/terminal', label: 'Trading Terminal', icon: CandlestickChart },
    { to: '/signals', label: 'Signals Radar', icon: Radio },
    { to: '/intelligence', label: 'Market Intelligence', icon: Newspaper },
    { to: '/orders', label: 'Orders & Fills', icon: BookOpen },
    { to: '/positions', label: 'Positions & P&L', icon: Layers },
    { to: '/risk-algo', label: 'Risk & Algo Controls', icon: ShieldCheck },
    { to: '/activity', label: 'Activity & Audit', icon: Activity, badge: unreadCount },
    { to: '/settings', label: 'Settings & Keys', icon: Settings },
  ]

  return (
    <aside
      className={`fixed lg:static top-14 bottom-0 left-0 z-20 flex flex-col justify-between bg-background-surface border-r border-terminal-border transition-all duration-300 ${
        sidebarOpen ? 'w-56' : 'w-16'
      }`}
    >
      <div className="p-3 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `flex items-center justify-between px-3 py-2.5 rounded-md text-xs font-medium transition-all ${
                isActive
                  ? 'bg-brand-cyan/10 text-brand-cyan border border-brand-cyan/20 font-semibold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-background-elevated'
              }`
            }
            title={!sidebarOpen ? item.label : undefined}
          >
            <div className="flex items-center gap-3">
              <item.icon className="w-4 h-4 shrink-0" />
              {sidebarOpen && <span className="truncate">{item.label}</span>}
            </div>

            {item.badge && item.badge > 0 ? (
              sidebarOpen ? (
                <span className="px-1.5 py-0.2 rounded-full bg-brand-cyan text-background text-[10px] font-bold font-mono">
                  {item.badge}
                </span>
              ) : (
                <span className="w-2 h-2 rounded-full bg-brand-cyan animate-pulse"></span>
              )
            ) : null}
          </NavLink>
        ))}
      </div>

      {/* Footer Info */}
      <div className="p-3 border-t border-terminal-border/80">
        <div
          className={`flex items-center gap-2 text-[11px] text-slate-500 font-mono ${
            !sidebarOpen ? 'justify-center' : ''
          }`}
        >
          <HelpCircle className="w-3.5 h-3.5 shrink-0" />
          {sidebarOpen && <span>Delta Exchange India</span>}
        </div>
      </div>
    </aside>
  )
}
