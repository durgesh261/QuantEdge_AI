import { useState, useEffect } from 'react'
import {
  FlaskConical,
  Play,
  AlertTriangle,
  ShieldAlert,
  Zap,
  TrendingUp,
  TrendingDown
} from 'lucide-react'
import { developerService, SandboxInfoResponse, SimulatedTickResult } from '@/services/developerService'

export function SandboxLab() {
  const [sandboxInfo, setSandboxInfo] = useState<SandboxInfoResponse | null>(null)
  const [symbol, setSymbol] = useState('BTCUSD')
  const [price, setPrice] = useState<number>(65000)
  const [isSimulating, setIsSimulating] = useState(false)
  const [lastResult, setLastResult] = useState<SimulatedTickResult | null>(null)
  const [history, setHistory] = useState<SimulatedTickResult[]>([])
  const [error, setError] = useState<string | null>(null)

  const fetchSandboxInfo = async () => {
    try {
      const data = await developerService.getSandboxInfo()
      setSandboxInfo(data)
    } catch (err: any) {
      setError(err.message || 'Failed to fetch sandbox state')
    }
  }

  useEffect(() => {
    fetchSandboxInfo()
  }, [])

  const handleSimulateTick = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSimulating(true)
    setError(null)

    try {
      const result = await developerService.simulateTick(symbol, price)
      setLastResult(result)
      setHistory(prev => [result, ...prev.slice(0, 9)])
      await fetchSandboxInfo()
    } catch (err: any) {
      setError(err.message || 'Simulation error')
    } finally {
      setIsSimulating(false)
    }
  }

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight font-mono">
              Sandbox & Simulation Lab
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
              ISOLATED SIMULATOR
            </span>
          </div>
          <p className="text-slate-400 mt-1 text-sm font-mono">
            Purely isolated environment for market tick simulations and SMC Order Block qualification tests.
          </p>
        </div>
      </div>

      {/* Prominent Isolation Safety Notice */}
      <div className="bg-indigo-950/40 border border-indigo-500/40 rounded-xl p-4 flex items-start gap-3 font-mono text-xs text-indigo-200">
        <ShieldAlert size={18} className="text-indigo-400 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-bold text-indigo-100 uppercase tracking-wide">
            100% ISOLATED ENVIRONMENT — REAL EXECUTION DECOUPLED
          </p>
          <p className="text-slate-300">
            This developer sandbox operates strictly in server memory. It possesses ZERO access to Delta Exchange API credentials, real live orders, or active positions.
          </p>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/80 text-red-300 font-mono text-xs flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Simulator Control Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Input Form */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4 font-mono">
          <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
            <FlaskConical size={18} className="text-amber-400" />
            <h2 className="text-base font-bold text-white">Generate Mock Market Tick</h2>
          </div>

          <form onSubmit={handleSimulateTick} className="space-y-4">
            <div>
              <label className="text-xs font-bold text-slate-400 uppercase tracking-wider block mb-1">
                Symbol
              </label>
              <select
                value={symbol}
                onChange={e => setSymbol(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
              >
                <option value="BTCUSD">BTCUSD (Bitcoin Perpetual)</option>
                <option value="ETHUSD">ETHUSD (Ethereum Perpetual)</option>
                <option value="SOLUSD">SOLUSD (Solana Perpetual)</option>
              </select>
            </div>

            <div>
              <label className="text-xs font-bold text-slate-400 uppercase tracking-wider block mb-1">
                Simulated Mark Price ($)
              </label>
              <input
                type="number"
                step="0.01"
                min="0.01"
                value={price}
                onChange={e => setPrice(parseFloat(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2 text-sm text-emerald-400 font-bold focus:outline-none focus:border-amber-500"
                required
              />
            </div>

            <button
              type="submit"
              disabled={isSimulating}
              className="w-full px-4 py-2.5 bg-gradient-to-r from-amber-600 to-indigo-600 hover:from-amber-500 hover:to-indigo-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg shadow-lg shadow-amber-600/20 transition disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <Play size={14} />
              {isSimulating ? 'Processing Tick...' : 'Inject Simulated Tick'}
            </button>
          </form>

          {sandboxInfo && (
            <div className="pt-3 border-t border-slate-800 text-xs text-slate-400 space-y-1.5">
              <div className="flex justify-between">
                <span>Simulated Ticks:</span>
                <span className="text-white font-bold">{sandboxInfo.simulatedTicksCount}</span>
              </div>
              <div className="flex justify-between">
                <span>Sandbox Paper Balance:</span>
                <span className="text-emerald-400 font-bold">${sandboxInfo.simulatedBalance.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span>Active Model:</span>
                <span className="text-indigo-400 font-bold">{sandboxInfo.activeStrategyModel}</span>
              </div>
            </div>
          )}
        </div>

        {/* Latest Signal Qualification Result */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4 font-mono">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <Zap size={18} className="text-cyan-400" />
              <h2 className="text-base font-bold text-white">Simulated SMC Signal Qualification</h2>
            </div>
            {lastResult && (
              <span className="text-xs text-slate-500">{new Date(lastResult.timestamp).toLocaleTimeString()}</span>
            )}
          </div>

          {lastResult ? (
            <div className="bg-slate-950/80 border border-slate-800 rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {lastResult.signal.includes('BUY') ? (
                    <TrendingUp size={20} className="text-emerald-400" />
                  ) : (
                    <TrendingDown size={20} className="text-red-400" />
                  )}
                  <span className="text-base font-bold text-white">{lastResult.symbol}</span>
                </div>
                <span className="px-3 py-1 rounded-full text-xs font-bold bg-cyan-950 text-cyan-300 border border-cyan-800">
                  {lastResult.signal}
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-3 border-t border-slate-800/80">
                <div>
                  <span className="text-slate-500 block">Tick Price</span>
                  <span className="text-emerald-400 font-bold">${lastResult.price.toLocaleString()}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">OB Structure</span>
                  <span className="text-indigo-400 font-bold">{lastResult.detectedOrderBlockType}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">OB Upper</span>
                  <span className="text-slate-200 font-bold">${lastResult.orderBlockHigh}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">OB Lower</span>
                  <span className="text-slate-200 font-bold">${lastResult.orderBlockLow}</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="py-12 text-center text-slate-500 text-xs">
              Inject a mock market tick on the left to see instant simulated SMC Order Block qualification.
            </div>
          )}

          {/* Recent Simulation History */}
          {history.length > 0 && (
            <div className="space-y-2 pt-2">
              <h3 className="text-xs uppercase font-bold text-slate-400 tracking-wider">Recent Injected Ticks</h3>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {history.map((h, i) => (
                  <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-slate-950/40 border border-slate-800/60 text-xs">
                    <span className="text-white font-bold">{h.symbol} @ ${h.price}</span>
                    <span className="text-slate-400">{h.detectedOrderBlockType}</span>
                    <span className={h.signal.includes('BUY') ? 'text-emerald-400 font-bold' : 'text-red-400 font-bold'}>
                      {h.signal}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
