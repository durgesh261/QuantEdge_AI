import React, { useEffect, useState, useCallback } from 'react'
import { developerService } from '../../services/developerService'
import { tradingService } from '../../services/tradingService'
import { SandboxInfoResponse, SimulatedTickResult } from '../../types/developer'
import { TradingSystemStatusDto } from '../../types/trading'
import { MetricCard } from '../../components/common/MetricCard'
import { SkeletonCard } from '../../components/common/Skeleton'
import {
  Cpu,
  Play,
  Shield,
  Layers,
  Activity,
  RefreshCw,
  AlertCircle,
  Zap,
} from 'lucide-react'

export const EngineMonitor: React.FC = () => {
  const [sandbox, setSandbox] = useState<SandboxInfoResponse | null>(null)
  const [tradingStatus, setTradingStatus] = useState<TradingSystemStatusDto | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Simulation Form State
  const [simSymbol, setSimSymbol] = useState('BTCUSD')
  const [simPrice, setSimPrice] = useState('65420.50')
  const [isSimulating, setIsSimulating] = useState(false)
  const [simResult, setSimResult] = useState<SimulatedTickResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const [sandboxRes, statusRes] = await Promise.all([
        developerService.getSandboxInfo(),
        tradingService.getTradingStatus().catch(() => null),
      ])
      setSandbox(sandboxRes)
      setTradingStatus(statusRes)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to fetch engine & sandbox telemetry')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleSimulateTick = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSimulating(true)
    setError(null)

    try {
      const result = await developerService.simulateTick(simSymbol, Number(simPrice))
      setSimResult(result)
      // Refresh sandbox info to update tick counter
      const updatedSandbox = await developerService.getSandboxInfo()
      setSandbox(updatedSandbox)
    } catch (err: any) {
      setError(err.response?.data?.message || 'Tick simulation failed')
    } finally {
      setIsSimulating(false)
    }
  }

  if (isLoading && !sandbox) {
    return (
      <div className="space-y-6">
        <SkeletonCard rows={4} />
      </div>
    )
  }

  return (
    <div className="space-y-6 font-mono">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <Cpu className="w-5 h-5 text-dev-purple" />
            <span>SMC Engine & Isolated Sandbox Monitor</span>
          </h1>
          <p className="text-xs text-slate-400 font-sans mt-0.5">
            Python Engine state machine, deterministic SMC block validation & decoupled price tick simulator
          </p>
        </div>

        <button
          onClick={fetchData}
          className="p-2 rounded bg-background border border-terminal-border hover:bg-background-elevated text-slate-300 hover:text-white transition-colors self-start sm:self-auto"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Safety Notice Banner */}
      <div className="p-3 rounded-lg bg-dev-purple/10 border border-dev-purple/30 text-xs flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="w-5 h-5 text-dev-purple shrink-0" />
          <div>
            <strong className="text-dev-purple">SAFETY INVARIANT ENFORCED: </strong>
            <span className="text-slate-300 font-sans">
              {sandbox?.safetyNotice || 'Sandbox mode is completely decoupled. Zero real Delta Exchange order execution.'}
            </span>
          </div>
        </div>
        {tradingStatus && (
          <span className="px-2 py-0.5 rounded bg-dev-accent/15 border border-dev-accent/30 text-dev-accent text-[10px] font-bold">
            STREAM: {tradingStatus.streamHealth || '1H SMC LIVE'}
          </span>
        )}
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-bearish/15 border border-bearish/30 text-bearish text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Overview Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="SANDBOX MODE"
          value={sandbox?.mode || 'ISOLATED_SIMULATOR'}
          subtext="Decoupled from Live Engine"
          icon={Cpu}
          variant="purple"
        />
        <MetricCard
          title="REAL EXECUTION"
          value={sandbox?.realExecutionBlocked ? 'BLOCKED (SAFE)' : 'LIVE'}
          subtext="Safety Guardrail Active"
          icon={Shield}
          variant="emerald"
        />
        <MetricCard
          title="SIMULATED BALANCE"
          value={`$${(sandbox?.simulatedBalance ?? 100000).toLocaleString()}`}
          subtext="Virtual Paper Capital"
          icon={Zap}
          variant="cyan"
        />
        <MetricCard
          title="TICKS PROCESSED"
          value={sandbox?.simulatedTicksCount ?? 0}
          subtext={`Last: ${sandbox?.lastSimulatedTickAt ? new Date(sandbox.lastSimulatedTickAt).toLocaleTimeString() : 'Never'}`}
          icon={Activity}
          variant="amber"
        />
      </div>

      {/* Simulation Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Tick Injector Form */}
        <div className="glass-panel p-5 rounded-lg border border-terminal-border space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-terminal-border/80">
            <Play className="w-4 h-4 text-dev-accent" />
            <h3 className="font-bold text-white text-xs uppercase tracking-wider">
              Inject Price Tick into SMC Simulator
            </h3>
          </div>

          <form onSubmit={handleSimulateTick} className="space-y-4 text-xs">
            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold block">TRADING SYMBOL</label>
              <select
                value={simSymbol}
                onChange={(e) => setSimSymbol(e.target.value)}
                className="w-full bg-background/80 border border-terminal-border rounded-lg px-3 py-2 text-white focus:outline-none focus:border-dev-purple transition-colors"
              >
                <option value="BTCUSD">BTCUSD (Bitcoin / USD)</option>
                <option value="ETHUSD">ETHUSD (Ethereum / USD)</option>
                <option value="SOLUSD">SOLUSD (Solana / USD)</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold block">SIMULATED TICK PRICE ($)</label>
              <input
                type="number"
                step="0.01"
                required
                value={simPrice}
                onChange={(e) => setSimPrice(e.target.value)}
                className="w-full bg-background/80 border border-terminal-border rounded-lg px-3 py-2 text-white focus:outline-none focus:border-dev-purple transition-colors"
              />
            </div>

            <button
              type="submit"
              disabled={isSimulating}
              className="w-full py-2.5 rounded-lg bg-dev-purple hover:bg-dev-purple/90 text-white font-bold text-xs flex items-center justify-center gap-2 transition-all shadow-md disabled:opacity-50"
            >
              {isSimulating ? (
                <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin"></span>
              ) : (
                <>
                  <Zap className="w-4 h-4" />
                  <span>RUN SMC PATTERN DETECTOR</span>
                </>
              )}
            </button>
          </form>
        </div>

        {/* Right: Simulation Telemetry Result */}
        <div className="glass-panel p-5 rounded-lg border border-terminal-border space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-terminal-border/80">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-dev-cyan" />
              <h3 className="font-bold text-white text-xs uppercase tracking-wider">
                SMC Detection Telemetry
              </h3>
            </div>
            {simResult && (
              <span className="text-[10px] text-slate-400">
                {new Date(simResult.timestamp).toLocaleTimeString()}
              </span>
            )}
          </div>

          {simResult ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 p-3 rounded bg-background/60 border border-terminal-border text-xs">
                <div>
                  <span className="text-slate-400 block text-[10px]">EVALUATED SYMBOL</span>
                  <strong className="text-white font-bold text-sm">{simResult.symbol}</strong>
                </div>
                <div>
                  <span className="text-slate-400 block text-[10px]">TICK PRICE</span>
                  <strong className="text-dev-cyan font-bold text-sm">${simResult.price?.toFixed(2)}</strong>
                </div>
              </div>

              <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">ORDER BLOCK DETECTED:</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      simResult.detectedOrderBlockType?.includes('BULLISH')
                        ? 'bg-bullish/15 text-bullish'
                        : simResult.detectedOrderBlockType?.includes('BEARISH')
                        ? 'bg-bearish/15 text-bearish'
                        : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {simResult.detectedOrderBlockType || 'NONE'}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-slate-400">OB PRICE BOUNDARY:</span>
                  <span className="text-white font-bold">
                    ${simResult.orderBlockLow?.toFixed(2)} - ${simResult.orderBlockHigh?.toFixed(2)}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-slate-400">SIGNAL ACTION:</span>
                  <strong className="text-dev-accent font-bold">{simResult.signal}</strong>
                </div>
              </div>
            </div>
          ) : (
            <div className="py-10 text-center text-slate-500 text-xs">
              Inject a price tick on the left to test the deterministic Order Block engine in real time.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
