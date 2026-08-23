import { useEffect, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/common/Layout'
import { PrivateRoute, PublicRoute } from './components/common/PrivateRoute'
import { useAuthStore } from './stores/authStore'

// Lazy-loaded developer console routes
const DeveloperLogin = lazy(() => import('./features/auth/DeveloperLogin').then((m) => ({ default: m.DeveloperLogin })))
const DeveloperDashboard = lazy(() => import('./features/dashboard/DeveloperDashboard').then((m) => ({ default: m.DeveloperDashboard })))
const EngineMonitor = lazy(() => import('./features/engine/EngineMonitor').then((m) => ({ default: m.EngineMonitor })))
const MarketDiagnostics = lazy(() => import('./features/market/MarketDiagnostics').then((m) => ({ default: m.MarketDiagnostics })))
const ExecutionMonitor = lazy(() => import('./features/execution/ExecutionMonitor').then((m) => ({ default: m.ExecutionMonitor })))
const SignalsDiagnostics = lazy(() => import('./features/signals/SignalsDiagnostics').then((m) => ({ default: m.SignalsDiagnostics })))
const LogViewerPage = lazy(() => import('./features/logs/LogViewerPage').then((m) => ({ default: m.LogViewerPage })))
const SystemConfiguration = lazy(() => import('./features/system/SystemConfiguration').then((m) => ({ default: m.SystemConfiguration })))

const RouteFallback = () => (
  <div className="flex items-center justify-center min-h-[400px] text-slate-400 font-mono text-xs">
    <div className="flex flex-col items-center gap-2">
      <div className="w-6 h-6 rounded-full border-2 border-dev-accent/20 border-t-dev-accent animate-spin"></div>
      <span>Loading developer module...</span>
    </div>
  </div>
)

export function App() {
  const checkAuth = useAuthStore((s) => s.checkAuth)

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        {/* Public Login Route */}
        <Route
          path="/login"
          element={
            <PublicRoute>
              <DeveloperLogin />
            </PublicRoute>
          }
        />

        {/* RBAC Protected Developer Routes */}
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <Layout>
                <Suspense fallback={<RouteFallback />}>
                  <Routes>
                    <Route path="" element={<DeveloperDashboard />} />
                    <Route path="engine" element={<EngineMonitor />} />
                    <Route path="market" element={<MarketDiagnostics />} />
                    <Route path="execution" element={<ExecutionMonitor />} />
                    <Route path="signals" element={<SignalsDiagnostics />} />
                    <Route path="logs" element={<LogViewerPage />} />
                    <Route path="system" element={<SystemConfiguration />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Suspense>
              </Layout>
            </PrivateRoute>
          }
        />
      </Routes>
    </Suspense>
  )
}

export default App
