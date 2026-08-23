import React from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/authStore'
import { ShieldAlert, LogOut } from 'lucide-react'

export const PrivateRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isDeveloperOrAdmin, isLoading, user, logout } = useAuthStore()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-slate-400 font-mono text-sm">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-full border-2 border-dev-accent/20 border-t-dev-accent animate-spin"></div>
          <span>Authenticating Developer Session...</span>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // RBAC Gating: User must hold ROLE_DEVELOPER or ROLE_ADMIN
  if (!isDeveloperOrAdmin) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <div className="glass-panel-elevated p-8 rounded-xl max-w-md w-full border border-bearish/40 text-center space-y-4">
          <div className="p-3 rounded-full bg-bearish/10 border border-bearish/30 text-bearish w-fit mx-auto">
            <ShieldAlert className="w-8 h-8" />
          </div>
          <div className="space-y-1">
            <h2 className="text-base font-bold text-white font-mono uppercase tracking-wider">
              403 Developer Access Restricted
            </h2>
            <p className="text-xs text-slate-400 font-sans">
              Your account (<strong className="text-slate-200">{user?.email}</strong>) has role{' '}
              <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono font-bold text-[10px]">
                {user?.role}
              </span>
              . Only <strong className="text-dev-accent">ROLE_DEVELOPER</strong> or{' '}
              <strong className="text-dev-accent">ROLE_ADMIN</strong> can access the developer console.
            </p>
          </div>
          <div className="pt-3 border-t border-terminal-border flex justify-center">
            <button
              onClick={logout}
              className="px-4 py-2 rounded bg-background border border-terminal-border text-xs font-mono text-slate-300 hover:text-white hover:bg-background-elevated transition-colors flex items-center gap-2 cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>Sign Out & Switch Account</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  return <>{children}</>
}

export const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isDeveloperOrAdmin, isLoading } = useAuthStore()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-slate-400 font-mono text-sm">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-full border-2 border-dev-accent/20 border-t-dev-accent animate-spin"></div>
          <span>Loading...</span>
        </div>
      </div>
    )
  }

  return !isAuthenticated || !isDeveloperOrAdmin ? <>{children}</> : <Navigate to="/" replace />
}
