import { Component, ErrorInfo, ReactNode } from 'react'
import { AlertOctagon, RefreshCw, Home, Copy, Check, ChevronDown, ChevronUp, Shield } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  copied: boolean
  showDetails: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      copied: false,
      showDetails: false,
    }
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo })
    console.error('Unhandled UI Exception caught by ErrorBoundary:', error, errorInfo)
  }

  handleReload = () => {
    window.location.reload()
  }

  handleGoHome = () => {
    window.location.href = '/'
  }

  handleCopyDiagnostics = () => {
    const { error, errorInfo } = this.state
    const payload = {
      timestamp: new Date().toISOString(),
      url: window.location.href,
      errorMessage: error?.message,
      stack: error?.stack,
      componentStack: errorInfo?.componentStack,
      userAgent: navigator.userAgent,
    }
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
    this.setState({ copied: true })
    setTimeout(() => this.setState({ copied: false }), 2500)
  }

  render() {
    if (this.state.hasError) {
      const { error, errorInfo, copied, showDetails } = this.state

      return (
        <div className="min-h-screen bg-background text-slate-200 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 sm:p-8 rounded-xl max-w-xl w-full border border-bearish/30 shadow-2xl space-y-6">
            {/* Header */}
            <div className="flex items-center gap-3 pb-4 border-b border-terminal-border">
              <div className="p-2.5 rounded-lg bg-bearish/10 border border-bearish/20 text-bearish">
                <AlertOctagon className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-lg font-bold text-white font-mono">Terminal Runtime Exception</h1>
                <p className="text-xs text-slate-400 font-mono">
                  Fault Isolation Active — Core Trading Authority Protected
                </p>
              </div>
            </div>

            {/* Explanatory Context */}
            <div className="space-y-2">
              <p className="text-xs text-slate-300 font-sans leading-relaxed">
                The visual trading interface encountered an unexpected rendering error. Your backend orders and algorithmic execution state remain unaffected.
              </p>
              <div className="p-3 rounded bg-background/80 border border-terminal-border text-xs font-mono text-bearish break-words">
                {error?.message || 'Unknown runtime error occurred.'}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                onClick={this.handleReload}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-cyan hover:bg-brand-cyan/90 text-background font-mono text-xs font-bold transition-all shadow-md shadow-brand-cyan/20"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Reload Terminal</span>
              </button>

              <button
                onClick={this.handleGoHome}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-background-elevated hover:bg-slate-700 text-white font-mono text-xs font-semibold transition-all border border-terminal-border"
              >
                <Home className="w-3.5 h-3.5" />
                <span>Return to Dashboard</span>
              </button>

              <button
                onClick={this.handleCopyDiagnostics}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-background hover:bg-background-elevated text-slate-300 font-mono text-xs transition-all border border-terminal-border ml-auto"
                title="Copy error details for technical support"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-bullish" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copied ? 'Copied' : 'Copy Diagnostics'}</span>
              </button>
            </div>

            {/* Technical Stack Trace Accordion */}
            <div className="pt-2 border-t border-terminal-border/60">
              <button
                onClick={() => this.setState((prev) => ({ showDetails: !prev.showDetails }))}
                className="flex items-center justify-between w-full text-[11px] font-mono text-slate-400 hover:text-white transition-colors"
              >
                <span>Technical Stack & Trace Details</span>
                {showDetails ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>

              {showDetails && (
                <div className="mt-2 p-3 rounded bg-background border border-terminal-border space-y-2 text-[10px] font-mono text-slate-400 overflow-x-auto max-h-48">
                  {error?.stack && (
                    <div>
                      <span className="text-slate-500 uppercase font-bold">Error Stack:</span>
                      <pre className="text-slate-300 mt-1 whitespace-pre-wrap">{error.stack}</pre>
                    </div>
                  )}
                  {errorInfo?.componentStack && (
                    <div className="pt-2 border-t border-terminal-border/40">
                      <span className="text-slate-500 uppercase font-bold">Component Stack:</span>
                      <pre className="text-slate-400 mt-1 whitespace-pre-wrap">{errorInfo.componentStack}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Footer Invariant Assurance */}
            <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500 pt-1">
              <Shield className="w-3.5 h-3.5 text-bullish shrink-0" />
              <span>QuantEdge Server Invariant: Backend state machine remains authoritative.</span>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
