import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, Mail, ArrowRight, AlertCircle, CheckCircle2, ArrowLeft } from 'lucide-react'
import { apiClient } from '../../services/apiClient'

export const ForgotPassword: React.FC = () => {
  const [email, setEmail] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsSubmitting(true)

    try {
      await apiClient.post('/api/v1/auth/forgot-password', { email: email.trim().toLowerCase() })
      setSubmitted(true)
    } catch (err: any) {
      const status = err.response?.status
      if (status && status >= 500) {
        setError('Service unavailable. Please try again in a moment.')
      } else {
        // Show success regardless — prevents email enumeration
        setSubmitted(true)
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col justify-center py-12 sm:px-6 lg:px-8 relative overflow-hidden">
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-brand-cyan/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-brand-blue/10 rounded-full blur-3xl pointer-events-none" />

      <div className="sm:mx-auto sm:w-full sm:max-w-md relative z-10 text-center">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-brand-cyan to-brand-blue shadow-lg shadow-brand-cyan/20 mb-4">
          <Cpu className="w-6 h-6 text-background" />
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-white">Forgot Password</h2>
        <p className="mt-2 text-xs text-slate-400 font-mono">
          Enter your email — we'll send a secure reset link.
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md relative z-10 px-4">
        <div className="glass-panel py-8 px-6 shadow-2xl rounded-xl sm:px-10">
          {submitted ? (
            /* Success state */
            <div className="text-center space-y-5">
              <div className="flex justify-center">
                <div className="w-16 h-16 rounded-full bg-bullish/10 border border-bullish/30 flex items-center justify-center">
                  <CheckCircle2 className="w-8 h-8 text-bullish" />
                </div>
              </div>
              <div className="space-y-2">
                <h3 className="text-base font-semibold text-white">Check your inbox</h3>
                <p className="text-xs text-slate-400 leading-relaxed">
                  If <span className="text-slate-200 font-mono">{email}</span> matches an account,
                  a password reset link has been sent. Check your spam folder if it doesn't arrive within a few minutes.
                </p>
              </div>
              <p className="text-xs text-slate-500 font-mono">The reset link expires in <strong className="text-slate-300">30 minutes</strong>.</p>
              <Link
                to="/login"
                className="inline-flex items-center gap-2 text-xs text-brand-cyan hover:underline"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                Back to Sign In
              </Link>
            </div>
          ) : (
            /* Form state */
            <>
              {error && (
                <div className="mb-5 p-3 rounded-lg bg-bearish/10 border border-bearish/20 flex items-center gap-2.5 text-xs text-bearish">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <form className="space-y-5" onSubmit={handleSubmit}>
                <div>
                  <label htmlFor="fp-email" className="block text-xs font-medium text-slate-300 mb-1">
                    Email address
                  </label>
                  <div className="relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                      <Mail className="w-4 h-4" />
                    </div>
                    <input
                      id="fp-email"
                      type="email"
                      required
                      autoComplete="email"
                      autoFocus
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="block w-full pl-9 pr-3 py-2 text-xs font-mono bg-background/80 border border-terminal-border rounded-md text-white placeholder-slate-500 focus:outline-none focus:border-brand-cyan transition-colors"
                      placeholder="you@gmail.com"
                    />
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
                      Sending reset link…
                    </span>
                  ) : (
                    <>
                      <span>Send Reset Link</span>
                      <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </button>
              </form>

              <div className="mt-6 text-center">
                <Link
                  to="/login"
                  className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-brand-cyan transition-colors"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Sign In
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
