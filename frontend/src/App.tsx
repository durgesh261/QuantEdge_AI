import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { DeveloperLayout } from '@/components/DeveloperLayout'
import { DeveloperRoute } from '@/components/DeveloperRoute'
import { Dashboard } from '@/features/dashboard/Dashboard'
import { LiveTrading } from '@/features/trading/LiveTrading'
import { Orders } from '@/features/orders/Orders'
import { Positions } from '@/features/positions/Positions'
import { Journal } from '@/features/journal/Journal'
import { Analytics } from '@/features/analytics/Analytics'
import { Settings } from '@/features/settings/Settings'
import { Login } from '@/features/auth/Login'
import { Signup } from '@/features/auth/Signup'
import { DeveloperDashboard } from '@/features/developer/DeveloperDashboard'
import { SandboxLab } from '@/features/developer/SandboxLab'
import { ApiDiagnostics } from '@/features/developer/ApiDiagnostics'
import { LogsViewer } from '@/features/developer/LogsViewer'
import { SystemHealth } from '@/features/developer/SystemHealth'
import { useAuthStore } from '@/stores/authStore'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400 font-mono text-sm">Loading QuantEdge...</div>
  }

  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400 font-mono text-sm">Loading QuantEdge...</div>
  }

  return !isAuthenticated ? <>{children}</> : <Navigate to="/" replace />
}

export function App() {
  return (
    <Routes>
      {/* Public Authentication Routes */}
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />

      {/* Developer Restricted Application */}
      <Route
        path="/developer/*"
        element={
          <DeveloperRoute>
            <DeveloperLayout>
              <Routes>
                <Route path="" element={<DeveloperDashboard />} />
                <Route path="sandbox" element={<SandboxLab />} />
                <Route path="diagnostics" element={<ApiDiagnostics />} />
                <Route path="logs" element={<LogsViewer />} />
                <Route path="system" element={<SystemHealth />} />
                <Route path="*" element={<Navigate to="/developer" replace />} />
              </Routes>
            </DeveloperLayout>
          </DeveloperRoute>
        }
      />

      {/* Production User Trading Web App */}
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <Layout>
              <Routes>
                <Route path="" element={<Dashboard />} />
                <Route path="live-trading" element={<LiveTrading />} />
                <Route path="orders" element={<Orders />} />
                <Route path="positions" element={<Positions />} />
                <Route path="journal" element={<Journal />} />
                <Route path="analytics" element={<Analytics />} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Layout>
          </PrivateRoute>
        }
      />
    </Routes>
  )
}

export default App