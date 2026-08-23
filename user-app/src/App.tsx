import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/common/Layout'
import { PrivateRoute, PublicRoute } from './components/common/PrivateRoute'
import { Login } from './features/auth/Login'
import { Signup } from './features/auth/Signup'
import { Dashboard } from './features/dashboard/Dashboard'

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
                {/* Subsequent Step Placeholders redirecting gracefully to Dashboard */}
                <Route path="terminal" element={<Dashboard />} />
                <Route path="signals" element={<Dashboard />} />
                <Route path="intelligence" element={<Dashboard />} />
                <Route path="orders" element={<Dashboard />} />
                <Route path="positions" element={<Dashboard />} />
                <Route path="risk-algo" element={<Dashboard />} />
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
