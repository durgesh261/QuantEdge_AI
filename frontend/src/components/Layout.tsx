import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { LayoutDashboard, FileText, TrendingUp, ListChecks, Briefcase, BookOpen, BarChart3, Settings, LogOut, Menu, X } from 'lucide-react'
import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Live Trading', href: '/live-trading', icon: TrendingUp },
  { name: 'Orders', href: '/orders', icon: ListChecks },
  { name: 'Positions', href: '/positions', icon: Briefcase },
  { name: 'Journal', href: '/journal', icon: BookOpen },
  { name: 'Analytics', href: '/analytics', icon: BarChart3 },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const { logout } = useAuthStore()

  return (
    <div className="min-h-screen bg-slate-950">
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-slate-900 border-r border-slate-800 transform transition-transform duration-300 lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          <div className="flex items-center justify-between h-16 px-4 border-b border-slate-800">
            <h1 className="text-xl font-bold text-white">QuantEdge AI</h1>
            <button
              className="lg:hidden p-2 text-slate-400 hover:text-white"
              onClick={() => setSidebarOpen(false)}
            >
              <X size={24} />
            </button>
          </div>
          <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
            {navigation.map((item) => (
              <NavLink
                key={item.name}
                to={item.href}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-slate-800 text-white'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800'
                  }`
                }
                onClick={() => setSidebarOpen(false)}
              >
                <item.icon size={20} />
                {item.name}
              </NavLink>
            ))}
          </nav>
          <div className="p-4 border-t border-slate-800">
            <button
              onClick={logout}
              className="flex items-center gap-3 w-full px-3 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              <LogOut size={20} />
              Sign Out
            </button>
          </div>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-40 h-16 bg-slate-900/80 backdrop-blur-sm border-b border-slate-800">
          <div className="flex items-center justify-between h-full px-4 lg:px-6">
            <button
              className="lg:hidden p-2 text-slate-400 hover:text-white"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={24} />
            </button>
            <div className="flex items-center gap-4">
              <div className="hidden sm:block text-sm text-slate-400">
                QuantEdge AI V2
              </div>
            </div>
          </div>
        </header>
        <main className="p-4 lg:p-6">
          <Outlet />
        </main>
      </div>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
    </div>
  )
}