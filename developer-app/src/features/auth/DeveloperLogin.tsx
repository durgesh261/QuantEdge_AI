import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/authStore'
import { Terminal, Lock, Mail, AlertCircle, ArrowRight, ShieldCheck } from 'lucide-react'

export const DeveloperLogin: React.FC = () => {
  const navigate = useNavigate()
  const { login } = useAuthStore()

  const [email, setEmail] = useState('developer@quantedge.internal')
  const [password, setPassword] = useState('Admin@QuantEdge2026!')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      await login(email, password)
      navigate('/')
    } catch (err: any) {
      setError(err.message || 'Login failed')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4 relative overflow-hidden">
      {/* Background Ambient Glow */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-96 h-96 bg-dev-accent/10 rounded-full blur-3xl pointer-events-none"></div>

      <div className="glass-panel-elevated p-8 rounded-xl max-w-md w-full border border-terminal-border shadow-2xl space-y-6 relative z-10">
        {/* Header */}
        <div className="text-center space-y-2">
          <div className="inline-flex p-3 rounded-xl bg-dev-accent/15 border border-dev-accent/30 text-dev-accent mb-1">
            <Terminal className="w-8 h-8" />
          </div>
          <h1 className="text-lg font-bold text-white tracking-wider font-mono">QUANTEDGE AI</h1>
          <p className="text-xs text-slate-400 font-mono">
            Developer, Operator & Administrator Gateway
          </p>
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-dev-accent/10 border border-dev-accent/20 text-dev-accent text-[10px] font-mono font-bold">
            <ShieldCheck className="w-3 h-3" />
            <span>RBAC RESTRICTED ZONE</span>
          </div>
        </div>

        {error && (
          <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/35 text-bearish text-xs font-mono flex items-start gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4 font-mono text-xs">
          <div className="space-y-1.5">
            <label className="text-slate-300 font-semibold block">OPERATOR EMAIL</label>
            <div className="relative">
              <Mail className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="developer@quantedge.internal"
                className="w-full bg-background/80 border border-terminal-border rounded-lg pl-9 pr-3 py-2.5 text-white placeholder:text-slate-600 focus:outline-none focus:border-dev-accent transition-colors"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-slate-300 font-semibold block">AUTHENTICATION KEY / PASSWORD</label>
            <div className="relative">
              <Lock className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full bg-background/80 border border-terminal-border rounded-lg pl-9 pr-3 py-2.5 text-white placeholder:text-slate-600 focus:outline-none focus:border-dev-accent transition-colors"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-2.5 rounded-lg bg-dev-accent hover:bg-dev-accent/90 text-background font-bold font-mono text-xs flex items-center justify-center gap-2 transition-all shadow-lg hover:shadow-dev-accent/20 disabled:opacity-50 mt-2"
          >
            {isLoading ? (
              <span className="w-4 h-4 border-2 border-background/40 border-t-background rounded-full animate-spin"></span>
            ) : (
              <>
                <span>ACCESS DEVELOPER CONSOLE</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </form>

        {/* Security Notice */}
        <div className="pt-4 border-t border-terminal-border/80 text-center text-[11px] font-mono text-slate-500">
          All connection attempts, diagnostic requests, and sandbox simulations are recorded in the immutable audit stream.
        </div>
      </div>
    </div>
  )
}
