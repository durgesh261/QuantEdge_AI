import { useEffect, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/common/Layout'
import { PrivateRoute, PublicRoute } from './components/common/PrivateRoute'
import { useAuthStore } from './stores/authStore'

// Lazy-loaded route components for optimal production bundle splitting
const Login = lazy(() => import('./features/auth/Login').then((m) => ({ default: m.Login })))
const Signup = lazy(() => import('./features/auth/Signup').then((m) => ({ default: m.Signup })))
const ForgotPassword = lazy(() => import('./features/auth/ForgotPassword').then((m) => ({ default: m.ForgotPassword })))
const ResetPassword = lazy(() => import('./features/auth/ResetPassword').then((m) => ({ default: m.ResetPassword })))
const Dashboard = lazy(() => import('./features/dashboard/Dashboard').then((m) => ({ default: m.Dashboard })))
const TradingTerminal = lazy(() => import('./features/terminal/TradingTerminal').then((m) => ({ default: m.TradingTerminal })))
const SignalsRadar = lazy(() => import('./features/signals/SignalsRadar').then((m) => ({ default: m.SignalsRadar })))
const MarketIntelligence = lazy(() => import('./features/intelligence/MarketIntelligence').then((m) => ({ default: m.MarketIntelligence })))
const OrdersPage = lazy(() => import('./features/orders/OrdersPage').then((m) => ({ default: m.OrdersPage })))
const PositionsPage = lazy(() => import('./features/positions/PositionsPage').then((m) => ({ default: m.PositionsPage })))
const RiskAlgoPage = lazy(() => import('./features/risk/RiskAlgoPage').then((m) => ({ default: m.RiskAlgoPage })))
const ActivityCenter = lazy(() => import('./features/activity/ActivityCenter').then((m) => ({ default: m.ActivityCenter })))
const SettingsPage = lazy(() => import('./features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))

const RouteFallback = () => (
  <div className="flex items-center justify-center min-h-[400px] text-slate-400 font-mono text-xs">
    <div className="flex flex-col items-center gap-2">
      <div className="w-6 h-6 rounded-full border-2 border-brand-cyan/20 border-t-brand-cyan animate-spin"></div>
      <span>Loading module...</span>
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
        {/* Public Authentication Routes */}
        <Route
          path="/login"
          element={
            <PublicRoute>
              <Login />
            </PublicRoute>
          }
        />
        <Route
          path="/signup"
          element={
            <PublicRoute>
              <Signup />
            </PublicRoute>
          }
        />

        {/* Password Reset — always public, even when authenticated */}
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />

        {/* Protected Production Trading Routes */}
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <Layout>
                <Suspense fallback={<RouteFallback />}>
                  <Routes>
                    <Route path="" element={<Dashboard />} />
                    <Route path="terminal" element={<TradingTerminal />} />
                    <Route path="signals" element={<SignalsRadar />} />
                    <Route path="intelligence" element={<MarketIntelligence />} />
                    <Route path="orders" element={<OrdersPage />} />
                    <Route path="positions" element={<PositionsPage />} />
                    <Route path="risk-algo" element={<RiskAlgoPage />} />
                    <Route path="activity" element={<ActivityCenter />} />
                    <Route path="settings" element={<SettingsPage />} />
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
