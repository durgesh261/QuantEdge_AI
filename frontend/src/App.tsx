import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Dashboard } from '@/features/dashboard/Dashboard'
import { LiveTrading } from '@/features/trading/LiveTrading'
import { Orders } from '@/features/orders/Orders'
import { Positions } from '@/features/positions/Positions'
import { Journal } from '@/features/journal/Journal'
import { Analytics } from '@/features/analytics/Analytics'
import { Settings } from '@/features/settings/Settings'
import { Login } from '@/features/auth/Login'
import { Signup } from '@/features/auth/Signup'
import { useAuthStore } from '@/stores/authStore'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>
  }

  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>
  }

  return !isAuthenticated ? <>{children}</> : <Navigate to="/" replace />
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
      <Route
        path="/"
        element={
          <PrivateRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="live-trading" element={<LiveTrading />} />
                <Route path="orders" element={<Orders />} />
                <Route path="positions" element={<Positions />} />
                <Route path="journal" element={<Journal />} />
                <Route path="analytics" element={<Analytics />} />
                <Route path="settings" element={<Settings />} />
              </Routes>
            </Layout>
          </PrivateRoute>
        }
      />
    </Routes>
  )
}