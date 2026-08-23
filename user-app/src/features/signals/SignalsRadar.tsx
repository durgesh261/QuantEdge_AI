import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { tradingService } from '../../services/tradingService'
import { useMarketStore } from '../../stores/marketStore'
import { SignalSetupDto } from '../../types/trading'
import { AiEnrichmentDto } from '../../types/ai'
import { SkeletonCard } from '../../components/common/Skeleton'
import { EmptyState } from '../../components/common/EmptyState'
import {
  Radio,
  Filter,
  TrendingUp,
  TrendingDown,
  Cpu,
  ArrowRight,
  RefreshCw,
  AlertCircle,
  Sparkles,
  X,
} from 'lucide-react'

export const SignalsRadar: React.FC = () => {
  const navigate = useNavigate()
  const { setActiveSymbol } = useMarketStore()

  // Data State
  const [signals, setSignals] = useState<SignalSetupDto[]>([])
  const [aiEnrichments, setAiEnrichments] = useState<Record<string, AiEnrichmentDto>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedSignal, setSelectedSignal] = useState<SignalSetupDto | null>(null)

  // Filter State
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL')
  const [directionFilter, setDirectionFilter] = useState<string>('ALL')
  const [stateFilter, setStateFilter] = useState<string>('ALL')

  const fetchSignals = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)
      const data = await tradingService.getSignals(
        symbolFilter !== 'ALL' ? symbolFilter : undefined,
        stateFilter !== 'ALL' ? stateFilter : undefined,
        50
      )
      setSignals(data)

      // Fetch AI enrichments for qualified/active setups
      const enrichmentMap: Record<string, AiEnrichmentDto> = {}
      for (const s of data) {
        try {
          const ai = await tradingService.getAiIntelligence(s.setupId)
          if (ai) enrichmentMap[s.setupId] = ai
        } catch (e) {
          // AI enrichment is optional per setup
        }
      }
      setAiEnrichments(enrichmentMap)
    } catch (err: any) {
      console.warn('Failed to load signals radar', err)
      setError(err.response?.data?.message || 'Unable to connect to SMC signals service')
    } finally {
      setIsLoading(false)
    }
  }, [symbolFilter, stateFilter])

  useEffect(() => {
    fetchSignals()
    const interval = setInterval(fetchSignals, 8000)
    return () => clearInterval(interval)
  }, [fetchSignals])

  // Filtered List
  const filteredSignals = signals.filter((s) => {
    if (symbolFilter !== 'ALL' && s.symbol.toUpperCase() !== symbolFilter.toUpperCase()) return false
    if (directionFilter !== 'ALL') {
      const isLong = s.direction?.toUpperCase() === 'LONG' || s.direction?.toUpperCase() === 'BUY'
      if (directionFilter === 'LONG' && !isLong) return false
      if (directionFilter === 'SHORT' && isLong) return false
    }
    if (stateFilter !== 'ALL' && s.setupState?.toUpperCase() !== stateFilter.toUpperCase()) return false
    return true
  })

  // Summary Metrics
  const activeCount = signals.filter((s) => s.setupState === 'ACTIVE' || s.setupState === 'QUALIFIED').length
  const avgRR = signals.length > 0
    ? (signals.reduce((acc, s) => acc + (s.riskReward || 0), 0) / signals.length).toFixed(2)
    : '2.00'
  const avgConfidence = signals.length > 0
    ? Math.round(signals.reduce((acc, s) => acc + (s.confidence || 0), 0) / signals.length * (signals[0]?.confidence <= 1 ? 100 : 1))
    : 0

  const handleOpenTerminal = (symbol: string) => {
    setActiveSymbol(symbol)
    navigate('/terminal')
  }

  return (
    <div className="space-y-6">
      {/* Top Header & Overview Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Radio className="w-5 h-5 text-brand-cyan" />
            <span>SMC Strategy Setups & AI Radar</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Algorithmic 1H Smart Money Concept Setups & Deterministic AI Conviction Scoring
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchSignals}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-white transition-all border border-terminal-border disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
            <span>Refresh Scanner</span>
          </button>
        </div>
      </div>

      {/* Top 4 Summary Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Scanned Setups</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">{signals.length}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Total 1H detections</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Qualified / Active</div>
          <div className="mt-2 text-2xl font-bold font-mono text-bullish">{activeCount}</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Ready for execution</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Average Risk/Reward</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">{avgRR} : 1</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Minimum RR ≥ 2.0 filter</div>
        </div>

        <div className="glass-panel p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-medium">Mean AI Confidence</div>
          <div className="mt-2 text-2xl font-bold font-mono text-brand-cyan">{avgConfidence}%</div>
          <div className="mt-1 text-[11px] font-mono text-slate-400">Technical + Regime composite</div>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="glass-panel p-3 rounded-lg flex flex-wrap items-center justify-between gap-4 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5 text-slate-400 font-semibold font-mono">
            <Filter className="w-3.5 h-3.5" />
            <span>Filters:</span>
          </div>

          {/* Symbol Filter */}
          <div className="flex items-center p-0.5 rounded-md bg-background/80 border border-terminal-border font-mono text-xs">
            {['ALL', 'BTCUSD', 'ETHUSD', 'SOLUSD'].map((sym) => (
              <button
                key={sym}
                onClick={() => setSymbolFilter(sym)}
                className={`px-2.5 py-1 rounded transition-all ${
                  symbolFilter === sym
                    ? 'bg-brand-cyan text-background font-bold shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>

          {/* Direction Filter */}
          <div className="flex items-center p-0.5 rounded-md bg-background/80 border border-terminal-border font-mono text-xs">
            {['ALL', 'LONG', 'SHORT'].map((dir) => (
              <button
                key={dir}
                onClick={() => setDirectionFilter(dir)}
                className={`px-2.5 py-1 rounded transition-all ${
                  directionFilter === dir
                    ? 'bg-background-elevated text-brand-cyan font-bold border border-brand-cyan/30'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {dir}
              </button>
            ))}
          </div>

          {/* State Filter */}
          <div className="flex items-center p-0.5 rounded-md bg-background/80 border border-terminal-border font-mono text-xs">
            {['ALL', 'QUALIFIED', 'ACTIVE', 'COMPLETED', 'INVALIDATED'].map((st) => (
              <button
                key={st}
                onClick={() => setStateFilter(st)}
                className={`px-2 py-1 rounded text-[11px] transition-all ${
                  stateFilter === st
                    ? 'bg-background-elevated text-white font-bold border border-slate-600'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {st}
              </button>
            ))}
          </div>
        </div>

        <div className="text-slate-400 font-mono text-[11px]">
          Showing {filteredSignals.length} of {signals.length} Setups
        </div>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="p-4 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={fetchSignals}
            className="px-2.5 py-1 rounded bg-bearish/20 hover:bg-bearish/30 text-white font-bold transition-all font-mono"
          >
            Retry
          </button>
        </div>
      )}

      {/* Signals Grid */}
      {isLoading && signals.length === 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <SkeletonCard key={i} rows={4} />
          ))}
        </div>
      ) : filteredSignals.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredSignals.map((setup) => {
            const isLong = setup.direction?.toUpperCase() === 'LONG' || setup.direction?.toUpperCase() === 'BUY'
            const ai = aiEnrichments[setup.setupId]
            const confidence = ai?.confidence
              ? Math.round(Number(ai.confidence) * (Number(ai.confidence) <= 1 ? 100 : 1))
              : Math.round(setup.confidence * (setup.confidence <= 1 ? 100 : 1))

            return (
              <div
                key={setup.id}
                onClick={() => setSelectedSignal(setup)}
                className="glass-panel p-4 rounded-lg hover:border-brand-cyan/40 transition-all cursor-pointer flex flex-col justify-between space-y-3 group"
              >
                {/* Top Row: Symbol, Direction & State */}
                <div className="flex items-center justify-between pb-2 border-b border-terminal-border/80">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-white text-sm font-mono">{setup.symbol}</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold flex items-center gap-1 ${
                        isLong ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'
                      }`}
                    >
                      {isLong ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                      {setup.direction}
                    </span>
                  </div>

                  <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] font-mono font-bold text-slate-300">
                    {setup.setupState}
                  </span>
                </div>

                {/* Setup ID & Timeframe */}
                <div className="flex items-center justify-between text-[11px] font-mono text-slate-400">
                  <span className="truncate max-w-[200px] text-brand-cyan font-semibold">{setup.setupId}</span>
                  <span>1H STREAM</span>
                </div>

                {/* Price Metrics Grid */}
                <div className="grid grid-cols-3 gap-2 p-2.5 rounded bg-background/60 border border-terminal-border text-center font-mono text-xs">
                  <div>
                    <div className="text-[10px] text-slate-400 uppercase">Entry</div>
                    <div className="font-bold text-white mt-0.5">${setup.entryPrice?.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400 uppercase">Stop Loss</div>
                    <div className="font-bold text-bearish mt-0.5">${setup.stopLoss?.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400 uppercase">Take Profit</div>
                    <div className="font-bold text-bullish mt-0.5">${setup.takeProfit?.toFixed(2)}</div>
                  </div>
                </div>

                {/* AI Conviction & RR */}
                <div className="flex items-center justify-between text-xs font-mono">
                  <div className="flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-brand-cyan" />
                    <span className="text-slate-400">AI Conviction:</span>
                    <strong className="text-brand-cyan font-bold">{confidence}%</strong>
                  </div>
                  <div>
                    <span className="text-slate-400">RR: </span>
                    <strong className="text-white font-bold">{setup.riskReward?.toFixed(2) || '2.00'}</strong>
                  </div>
                </div>

                {/* Plain-English AI Snippet */}
                {ai?.marketContext && (
                  <p className="text-[11px] text-slate-400 font-sans line-clamp-2 italic bg-background/30 p-2 rounded border border-terminal-border/60">
                    "{ai.marketContext}"
                  </p>
                )}

                {/* Bottom Action Button */}
                <div className="pt-2 border-t border-terminal-border/60 flex items-center justify-between">
                  <span className="text-[10px] font-mono text-slate-500">
                    {new Date(setup.createdAt).toLocaleTimeString()}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleOpenTerminal(setup.symbol)
                    }}
                    className="flex items-center gap-1 text-xs font-mono font-semibold text-brand-cyan hover:text-white transition-colors"
                  >
                    <span>Terminal Chart</span>
                    <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="glass-panel rounded-lg">
          <EmptyState
            icon={Radio}
            title="No Matching SMC Setups Found"
            description="The 1H deterministic SMC engine is actively monitoring order blocks and structure breaks. Try adjusting your filter criteria above."
            actionLabel="Reset Filters"
            onAction={() => {
              setSymbolFilter('ALL')
              setDirectionFilter('ALL')
              setStateFilter('ALL')
            }}
          />
        </div>
      )}

      {/* Signal Details Modal */}
      {selectedSignal && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-lg w-full space-y-4 shadow-2xl border border-terminal-border">
            <div className="flex items-center justify-between pb-3 border-b border-terminal-border">
              <div className="flex items-center gap-2">
                <Cpu className="w-5 h-5 text-brand-cyan" />
                <h3 className="text-sm font-bold text-white font-mono">
                  Setup Intelligence: {selectedSignal.setupId}
                </h3>
              </div>
              <button
                onClick={() => setSelectedSignal(null)}
                className="p-1 rounded hover:bg-background text-slate-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="grid grid-cols-2 gap-2 p-3 rounded bg-background/80 border border-terminal-border">
                <div>
                  <span className="text-slate-400">Symbol:</span> <strong className="text-white">{selectedSignal.symbol}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Direction:</span>{' '}
                  <strong className={selectedSignal.direction?.toUpperCase() === 'LONG' ? 'text-bullish' : 'text-bearish'}>
                    {selectedSignal.direction}
                  </strong>
                </div>
                <div>
                  <span className="text-slate-400">Timeframe:</span> <strong className="text-white">1H (H1 Stream)</strong>
                </div>
                <div>
                  <span className="text-slate-400">State:</span> <strong className="text-brand-cyan">{selectedSignal.setupState}</strong>
                </div>
              </div>

              {/* AI Details */}
              {aiEnrichments[selectedSignal.setupId] && (
                <div className="p-3 rounded bg-background/60 border border-terminal-border space-y-2">
                  <div className="text-[11px] uppercase font-bold text-brand-cyan flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>AI Conviction Analysis</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-slate-400">Pattern Score:</span>{' '}
                      <strong className="text-white">{aiEnrichments[selectedSignal.setupId].patternScore}/100</strong>
                    </div>
                    <div>
                      <span className="text-slate-400">Regime Score:</span>{' '}
                      <strong className="text-white">{aiEnrichments[selectedSignal.setupId].signalScore}/100</strong>
                    </div>
                  </div>
                  <div className="text-[11px] text-slate-300 font-sans leading-relaxed pt-1">
                    {aiEnrichments[selectedSignal.setupId].marketContext}
                  </div>
                </div>
              )}

              {/* Price Targets */}
              <div className="grid grid-cols-3 gap-2 p-3 rounded bg-background/80 border border-terminal-border text-center">
                <div>
                  <div className="text-[10px] text-slate-400">ENTRY</div>
                  <div className="text-sm font-bold text-white">${selectedSignal.entryPrice?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">STOP LOSS</div>
                  <div className="text-sm font-bold text-bearish">${selectedSignal.stopLoss?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">TAKE PROFIT</div>
                  <div className="text-sm font-bold text-bullish">${selectedSignal.takeProfit?.toFixed(2)}</div>
                </div>
              </div>
            </div>

            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setSelectedSignal(null)}
                className="flex-1 py-2 rounded bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors"
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => {
                  setSelectedSignal(null)
                  handleOpenTerminal(selectedSignal.symbol)
                }}
                className="flex-1 py-2 rounded bg-brand-cyan hover:bg-brand-cyan/90 text-xs font-bold text-background transition-colors flex items-center justify-center gap-1.5"
              >
                <span>Launch in Terminal</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
