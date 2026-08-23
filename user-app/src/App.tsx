import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/common/Layout'
import { PrivateRoute, PublicRoute } from './components/common/PrivateRoute'
import { Login } from './features/auth/Login'
import { Signup } from './features/auth/Signup'
import { Dashboard } from './features/dashboard/Dashboard'
import { TradingTerminal } from './features/terminal/TradingTerminal'
import { SignalsRadar } from './features/signals/SignalsRadar'
import { MarketIntelligence } from './features/intelligence/MarketIntelligence'
import { OrdersPage } from './features/orders/OrdersPage'
import { PositionsPage } from './features/positions/PositionsPage'
import { RiskAlgoPage } from './features/risk/RiskAlgoPage'

export function App() {
  return (
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

      {/* Protected Production Trading Routes */}
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <Layout>
              <Routes>
                <Route path="" element={<Dashboard />} />
                <Route path="terminal" element={<TradingTerminal />} />
                <Route path="signals" element={<SignalsRadar />} />
                <Route path="intelligence" element={<MarketIntelligence />} />
                <Route path="orders" element={<OrdersPage />} />
                <Route path="positions" element={<PositionsPage />} />
                <Route path="risk-algo" element={<RiskAlgoPage />} />
                <Route path="settings" element={<Dashboard />} />
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
