import React, { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Cpu, Lock, CheckCircle2, Eye, EyeOff, ShieldAlert } from 'lucide-react'
import { apiClient } from '../../services/apiClient'

export const ResetPassword: React.FC = () => {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''

  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // If no token in URL, redirect immediately
  useEffect(() => {
    if (!token) {
      navigate('/login', { replace: true })
    }
  }, [token, navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setIsSubmitting(true)

    try {
      await apiClient.post('/v1/auth/reset-password', { token, newPassword: password })
      setSuccess(true)

      // Auto-redirect to login with success message after 3 seconds
      setTimeout(() => {
        navigate('/login', {
          replace: true,
          state: { successMessage: 'Password reset successfully. You may now sign in with your new password.' },
        })
      }, 3000)
    } catch (err: any) {
      const status = err.response?.status
      const msg = err.response?.data?.message || ''

      if (status === 400 || status === 422) {
        setError(msg || 'This reset link is invalid or has already been used.')
      } else if (status === 404) {
        setError('This reset link is invalid or has expired. Please request a new one.')
      } else {
        setError('Something went wrong. Please try again or request a new reset link.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  // Strength indicator
  const strength = (() => {
    if (password.length === 0) return null
    let score = 0
    if (password.length >= 8) score++
    if (password.length >= 12) score++
    if (/[A-Z]/.test(password)) score++
    if (/[0-9]/.test(password)) score++
    if (/[^A-Za-z0-9]/.test(password)) score++
    if (score <= 2) return { label: 'Weak', color: 'bg-bearish', width: 'w-1/4' }
    if (score <= 3) return { label: 'Fair', color: 'bg-yellow-500', width: 'w-1/2' }
    if (score === 4) return { label: 'Strong', color: 'bg-brand-cyan', width: 'w-3/4' }
    return { label: 'Very Strong', color: 'bg-bullish', width: 'w-full' }
  })()

  if (!token) return null

  return (
    <div className="min-h-screen bg-background flex flex-col justify-center py-12 sm:px-6 lg:px-8 relative overflow-hidden">
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-brand-cyan/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-brand-blue/10 rounded-full blur-3xl pointer-events-none" />

      <div className="sm:mx-auto sm:w-full sm:max-w-md relative z-10 text-center">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-brand-cyan to-brand-blue shadow-lg shadow-brand-cyan/20 mb-4">
          <Cpu className="w-6 h-6 text-background" />
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-white">Set New Password</h2>
        <p className="mt-2 text-xs text-slate-400 font-mono">Choose a strong, unique password for your account.</p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md relative z-10 px-4">
        <div className="glass-panel py-8 px-6 shadow-2xl rounded-xl sm:px-10">
          {success ? (
            /* Success state */
            <div className="text-center space-y-5">
              <div className="flex justify-center">
                <div className="w-16 h-16 rounded-full bg-bullish/10 border border-bullish/30 flex items-center justify-center">
                  <CheckCircle2 className="w-8 h-8 text-bullish" />
                </div>
              </div>
              <div className="space-y-2">
                <h3 className="text-base font-semibold text-white">Password Updated</h3>
                <p className="text-xs text-slate-400 leading-relaxed">
                  Your password has been reset successfully. Redirecting you to sign in…
                </p>
              </div>
              <div className="w-5 h-5 border-2 border-brand-cyan/30 border-t-brand-cyan rounded-full animate-spin mx-auto" />
            </div>
          ) : (
            <>
              {error && (
                <div className="mb-5 p-3 rounded-lg bg-bearish/10 border border-bearish/20 flex items-start gap-2.5 text-xs text-bearish">
                  <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{error}</span>
                </div>
              )}

              <form className="space-y-5" onSubmit={handleSubmit}>
                {/* New Password */}
                <div>
                  <label htmlFor="rp-password" className="block text-xs font-medium text-slate-300 mb-1">
                    New Password
                  </label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                      <Lock className="w-4 h-4" />
                    </div>
                    <input
                      id="rp-password"
                      type={showPassword ? 'text' : 'password'}
                      required
                      minLength={8}
                      maxLength={128}
                      autoComplete="new-password"
                      autoFocus
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="block w-full pl-9 pr-10 py-2 text-xs font-mono bg-background/80 border border-terminal-border rounded-md text-white placeholder-slate-500 focus:outline-none focus:border-brand-cyan transition-colors"
                      placeholder="••••••••"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? 'Hide password' : 'Show password'}
                      className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-500 hover:text-slate-300 transition-colors"
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>

                  {/* Strength bar */}
                  {strength && (
                    <div className="mt-2 space-y-1">
                      <div className="h-1 w-full bg-terminal-border rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-300 ${strength.color} ${strength.width}`} />
                      </div>
                      <p className="text-[10px] font-mono text-slate-500">
                        Strength: <span className="text-slate-300">{strength.label}</span>
                      </p>
                    </div>
                  )}
                </div>

                {/* Confirm Password */}
                <div>
                  <label htmlFor="rp-confirm" className="block text-xs font-medium text-slate-300 mb-1">
                    Confirm New Password
                  </label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                      <Lock className="w-4 h-4" />
                    </div>
                    <input
                      id="rp-confirm"
                      type={showConfirm ? 'text' : 'password'}
                      required
                      minLength={8}
                      maxLength={128}
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className={`block w-full pl-9 pr-10 py-2 text-xs font-mono bg-background/80 border rounded-md text-white placeholder-slate-500 focus:outline-none transition-colors ${
                        confirmPassword && confirmPassword !== password
                          ? 'border-bearish/60 focus:border-bearish'
                          : 'border-terminal-border focus:border-brand-cyan'
                      }`}
                      placeholder="••••••••"
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirm((v) => !v)}
                      aria-label={showConfirm ? 'Hide password' : 'Show password'}
                      className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-500 hover:text-slate-300 transition-colors"
                    >
                      {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  {confirmPassword && confirmPassword !== password && (
                    <p className="mt-1 text-[10px] text-bearish font-mono">Passwords do not match.</p>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={isSubmitting || (confirmPassword.length > 0 && password !== confirmPassword)}
                  className="w-full flex justify-center items-center gap-2 py-2.5 px-4 border border-transparent rounded-md shadow-sm text-xs font-semibold text-background bg-brand-cyan hover:bg-brand-cyan/90 focus:outline-none disabled:opacity-50 transition-all cursor-pointer"
                >
                  {isSubmitting ? (
                    <span className="flex items-center gap-2">
                      <span className="w-3.5 h-3.5 border-2 border-background/30 border-t-background rounded-full animate-spin" />
                      Updating password…
                    </span>
                  ) : (
                    'Reset Password'
                  )}
                </button>
              </form>

              <div className="mt-6 text-center">
                <Link to="/forgot-password" className="text-xs text-slate-400 hover:text-brand-cyan transition-colors">
                  Request a new reset link
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
