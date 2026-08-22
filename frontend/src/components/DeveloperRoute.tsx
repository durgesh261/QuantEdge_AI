import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

export function DeveloperRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, user } = useAuthStore()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-400 font-mono text-sm">
        Authenticating Developer Console...
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  const isDeveloper = user?.role === 'DEVELOPER' || user?.role === 'ADMIN'

  if (!isDeveloper) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
