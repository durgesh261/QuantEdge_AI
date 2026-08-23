import React, { useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { Cpu, Lock, Mail, ArrowRight, AlertCircle, Shield, Eye, EyeOff } from 'lucide-react'
import { useAuthStore } from '../../stores/authStore'

export const Login: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuthStore()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  // Show success message passed from reset-password redirect
  const successMsg = (location.state as any)?.successMessage as string | undefined

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    setIsSubmitting(true)

    try {
      await login(email.trim(), password)
      navigate('/')
    } catch (err: any) {
      setLocalError(err.message || 'Login failed. Please check your credentials.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col justify-center py-12 sm:px-6 lg:px-8 relative overflow-hidden">
      {/* Background glowing gradients */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-brand-cyan/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-brand-blue/10 rounded-full blur-3xl pointer-events-none" />

      <div className="sm:mx-auto sm:w-full sm:max-w-md relative z-10 text-center">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-brand-cyan to-brand-blue shadow-lg shadow-brand-cyan/20 mb-4">
          <Cpu className="w-6 h-6 text-background" />
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-white">Sign in to QuantEdge AI</h2>
        <p className="mt-2 text-xs text-slate-400 font-mono">
          Institutional Algorithmic Trading &amp; Smart Money Intelligence
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md relative z-10 px-4">
        <div className="glass-panel py-8 px-6 shadow-2xl rounded-xl sm:px-10">
          {/* Success message from password reset */}
          {successMsg && (
            <div className="mb-6 p-3 rounded-lg bg-bullish/10 border border-bullish/20 flex items-center gap-2.5 text-xs text-bullish">
              <Shield className="w-4 h-4 shrink-0" />
              <span>{successMsg}</span>
            </div>
          )}

          {localError && (
            <div className="mb-6 p-3 rounded-lg bg-bearish/10 border border-bearish/20 flex items-center gap-2.5 text-xs text-bearish">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{localError}</span>
            </div>
          )}

          <form className="space-y-5" onSubmit={handleSubmit}>
            {/* Email */}
            <div>
              <label htmlFor="login-email" className="block text-xs font-medium text-slate-300">
                Email address
              </label>
              <div className="mt-1 relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                  <Mail className="w-4 h-4" />
                </div>
                <input
                  id="login-email"
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="block w-full pl-9 pr-3 py-2 text-xs font-mono bg-background/80 border border-terminal-border rounded-md text-white placeholder-slate-500 focus:outline-none focus:border-brand-cyan transition-colors"
                  placeholder="you@gmail.com"
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="login-password" className="block text-xs font-medium text-slate-300">
                  Password
                </label>
                <Link
                  to="/forgot-password"
                  className="text-xs text-brand-cyan hover:underline font-medium"
                >
                  Forgot password?
                </Link>
              </div>
              <div className="relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                  <Lock className="w-4 h-4" />
                </div>
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full pl-9 pr-10 py-2 text-xs font-mono bg-background/80 border border-terminal-border rounded-md text-white placeholder-slate-500 focus:outline-none focus:border-brand-cyan transition-colors"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  title={showPassword ? 'Hide password' : 'Show password'}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-500 hover:text-slate-300 transition-colors"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full flex justify-center items-center gap-2 py-2.5 px-4 border border-transparent rounded-md shadow-sm text-xs font-semibold text-background bg-brand-cyan hover:bg-brand-cyan/90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-cyan disabled:opacity-50 transition-all cursor-pointer"
            >
              {isSubmitting ? (
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 border-2 border-background/30 border-t-background rounded-full animate-spin" />
                  Authenticating...
                </span>
              ) : (
                <>
                  <span>Sign In</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-6 flex items-center justify-between text-xs text-slate-400">
            <span>Don't have an account?</span>
            <Link to="/signup" className="text-brand-cyan hover:underline font-medium">
              Create account
            </Link>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-center gap-2 text-[11px] text-slate-500 font-mono">
          <Shield className="w-3.5 h-3.5 text-bullish" />
          <span>Stateless HttpOnly Cookie Security</span>
        </div>
      </div>
    </div>
  )
}
