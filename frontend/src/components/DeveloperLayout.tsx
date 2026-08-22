import { Outlet, NavLink, Link } from 'react-router-dom'
import {
  Terminal,
  Cpu,
  FlaskConical,
  Activity,
  FileText,
  Server,
  ArrowLeft,
  LogOut,
  ShieldCheck,
  Menu,
  X,
  Lock
} from 'lucide-react'
import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'

const devNavigation = [
  { name: 'Developer Overview', href: '/developer', icon: Cpu, end: true },
  { name: 'Sandbox & Simulation', href: '/developer/sandbox', icon: FlaskConical, end: false },
  { name: 'API Diagnostics', href: '/developer/diagnostics', icon: Activity, end: false },
  { name: 'Sanitized Logs', href: '/developer/logs', icon: FileText, end: false },
  { name: 'System & Accounts', href: '/developer/system', icon: Server, end: false },
]

export function DeveloperLayout({ children }: { children?: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { user, logout } = useAuthStore()

  return (
    <div className="min-h-screen bg-slate-950 font-sans text-slate-100 selection:bg-amber-500/30">
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-slate-900 border-r border-amber-500/30 transform transition-transform duration-300 lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Brand Header */}
          <div className="flex items-center justify-between h-16 px-4 border-b border-amber-500/20 bg-amber-950/20">
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 rounded-lg bg-amber-500/20 text-amber-400 border border-amber-500/40">
                <Terminal size={18} />
              </div>
              <div>
                <span className="text-sm font-bold tracking-tight text-white block">QuantEdge Dev</span>
                <span className="text-[10px] font-mono text-amber-400/90 uppercase tracking-wider block">Internal Console</span>
              </div>
            </div>
            <button
              className="lg:hidden p-2 text-slate-400 hover:text-white"
              onClick={() => setSidebarOpen(false)}
            >
              <X size={20} />
            </button>
          </div>

          {/* Role Status Tag */}
          <div className="px-4 py-3 bg-slate-950/80 border-b border-slate-800/80">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-400 flex items-center gap-1">
                <Lock size={12} className="text-amber-400" />
                Access Tier:
              </span>
              <span className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 font-bold">
                {user?.role || 'DEVELOPER'}
              </span>
            </div>
          </div>

          {/* Navigation */}
          <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
            {devNavigation.map((item) => (
              <NavLink
                key={item.name}
                to={item.href}
                end={item.end}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors font-mono ${
                    isActive
                      ? 'bg-amber-500/15 text-amber-300 font-semibold border border-amber-500/30'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`
                }
                onClick={() => setSidebarOpen(false)}
              >
                <item.icon size={18} className="shrink-0" />
                <span>{item.name}</span>
              </NavLink>
            ))}
          </nav>

          {/* Bottom Actions */}
          <div className="p-3 border-t border-slate-800/80 space-y-2 bg-slate-950/40">
            <Link
              to="/"
              className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-xs font-mono font-medium text-cyan-400 hover:text-cyan-300 hover:bg-cyan-950/30 border border-cyan-500/20 transition"
            >
              <ArrowLeft size={14} />
              Return to User Trading App
            </Link>
            <button
              onClick={logout}
              className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-xs font-mono font-medium text-slate-400 hover:text-red-300 hover:bg-red-950/20 transition"
            >
              <LogOut size={14} />
              Sign Out ({user?.email})
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="lg:pl-64 flex flex-col min-h-screen">
        {/* Top Restricted Header */}
        <header className="sticky top-0 z-40 h-16 bg-slate-900/90 backdrop-blur-md border-b border-amber-500/30 px-4 lg:px-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              className="lg:hidden p-2 text-slate-400 hover:text-white"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={22} />
            </button>
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-amber-950/80 border border-amber-700/80 text-amber-300">
                RESTRICTED DEVELOPER ENVIRONMENT
              </span>
              <span className="hidden sm:inline-flex items-center gap-1 text-xs font-mono text-slate-400">
                <ShieldCheck size={14} className="text-emerald-400" />
                Live Order Gate Decoupled
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
            >
              <ArrowLeft size={14} />
              User App
            </Link>
          </div>
        </header>

        {/* Content Body */}
        <main className="flex-1 p-4 lg:p-6 bg-slate-950">
          {children || <Outlet />}
        </main>
      </div>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
    </div>
  )
}
