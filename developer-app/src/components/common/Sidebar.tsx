import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Cpu,
  Radio,
  BookOpen,
  Activity,
  ScrollText,
  Settings,
  ShieldAlert,
} from 'lucide-react'
import { useUIStore } from '../../stores/uiStore'

export const Sidebar: React.FC = () => {
  const { sidebarOpen } = useUIStore()

  const navItems = [
    { to: '/', label: 'System Overview', icon: LayoutDashboard, end: true },
    { to: '/engine', label: 'Engine & Sandbox', icon: Cpu },
    { to: '/market', label: 'Market Diagnostics', icon: Activity },
    { to: '/execution', label: 'Execution Ledger', icon: BookOpen },
    { to: '/signals', label: 'Signal Diagnostics', icon: Radio },
    { to: '/logs', label: 'System Audit Logs', icon: ScrollText },
    { to: '/system', label: 'Config & Diagnostics', icon: Settings },
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
                  ? 'bg-dev-accent/10 text-dev-accent border border-dev-accent/25 font-bold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-background-elevated'
              }`
            }
            title={!sidebarOpen ? item.label : undefined}
          >
            <div className="flex items-center gap-3">
              <item.icon className="w-4 h-4 shrink-0" />
              {sidebarOpen && <span className="truncate">{item.label}</span>}
            </div>
          </NavLink>
        ))}
      </div>

      {/* Footer Info */}
      <div className="p-3 border-t border-terminal-border/80 text-[11px] font-mono text-slate-500">
        {sidebarOpen ? (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-dev-accent font-semibold">
              <ShieldAlert className="w-3.5 h-3.5" />
              <span>RBAC Developer Tier</span>
            </div>
            <div className="text-[10px] text-slate-500">Spring Boot v3.2.3</div>
          </div>
        ) : (
          <div className="flex justify-center">
            <ShieldAlert className="w-4 h-4 text-dev-accent" />
          </div>
        )}
      </div>
    </aside>
  )
}
