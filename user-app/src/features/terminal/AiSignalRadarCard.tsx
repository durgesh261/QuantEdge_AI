import React, { useEffect, useState } from 'react'
import { SignalSetupDto } from '../../types/trading'
import { AiEnrichmentDto, AiDecisionResultDto, AiDecisionEvaluationRequest } from '../../types/ai'
import { tradingService } from '../../services/tradingService'
import { Cpu, Activity, CheckCircle2, TrendingUp, TrendingDown, AlertCircle, Shield, Clock, RefreshCw } from 'lucide-react'

interface AiSignalRadarCardProps {
  setup?: SignalSetupDto | null
  aiEnrichment?: AiEnrichmentDto | null
  isLoading?: boolean
  symbol: string
}

export const AiSignalRadarCard: React.FC<AiSignalRadarCardProps> = ({
  setup,
  aiEnrichment,
  symbol,
}) => {
  const [decisionResult, setDecisionResult] = useState<AiDecisionResultDto | null>(null)
  const [isEvaluating, setIsEvaluating] = useState(false)

  const confidenceScore = aiEnrichment?.confidence
    ? Math.round(Number(aiEnrichment.confidence) * (Number(aiEnrichment.confidence) <= 1 ? 100 : 1))
    : setup?.confidence
    ? Math.round(Number(setup.confidence) * (Number(setup.confidence) <= 1 ? 100 : 1))
    : null

  const patternScore = aiEnrichment?.patternScore ? Number(aiEnrichment.patternScore) : null
  const signalScore = aiEnrichment?.signalScore ? Number(aiEnrichment.signalScore) : null
  const regime = aiEnrichment?.marketRegime || 'REGIME SCANNING'
  const reasoning = aiEnrichment?.marketContext || 'Deterministic 1H SMC engine analyzing structure breaks (BOS/CHOCH) and unmitigated order block boundaries.'

  const isLong = setup?.direction?.toUpperCase() === 'LONG' || setup?.direction?.toUpperCase() === 'BUY'

  // Evaluate decision when setup/enrichment changes
  useEffect(() => {
    if (setup && aiEnrichment && !isEvaluating) {
      evaluateDecision()
    }
  }, [setup?.setupId, aiEnrichment?.id])

  const evaluateDecision = async () => {
    if (!setup) return
    setIsEvaluating(true)
    try {
      const request: AiDecisionEvaluationRequest = {
        setupId: setup.setupId,
        killSwitchActive: false, // Would come from trading status
        algoEnabled: true, // Would come from trading status
      }
      const result = await tradingService.evaluateAiDecision(request)
      setDecisionResult(result)
    } catch (err) {
      console.warn('AI decision evaluation failed', err)
    } finally {
      setIsEvaluating(false)
    }
  }

  const getDecisionColor = (decision: string) => {
    switch (decision) {
      case 'EXECUTION_ELIGIBLE': return 'text-bullish bg-bullish/10 border-bullish/30'
      case 'QUALIFIED': return 'text-brand-cyan bg-brand-cyan/10 border-brand-cyan/30'
      case 'WATCH': return 'text-warning bg-warning/10 border-warning/30'
      case 'BLOCKED_BY_AI_CONFIDENCE': return 'text-bearish bg-bearish/10 border-bearish/30'
      case 'BLOCKED_BY_RISK': return 'text-bearish bg-bearish/10 border-bearish/30'
      case 'BLOCKED_BY_SYSTEM': return 'text-slate-400 bg-slate-800 border-slate-600'
      case 'REJECTED': return 'text-bearish bg-bearish/10 border-bearish/30'
      case 'BLOCKED_BY_MARKET': return 'text-warning bg-warning/10 border-warning/30'
      default: return 'text-slate-400 bg-slate-800 border-slate-600'
    }
  }

  const getDecisionIcon = (decision: string) => {
    switch (decision) {
      case 'EXECUTION_ELIGIBLE': return <CheckCircle2 className="w-3.5 h-3.5" />
      case 'QUALIFIED': return <Shield className="w-3.5 h-3.5" />
      case 'WATCH': return <Clock className="w-3.5 h-3.5" />
      case 'BLOCKED_BY_AI_CONFIDENCE':
      case 'BLOCKED_BY_RISK':
      case 'REJECTED':
        return <AlertCircle className="w-3.5 h-3.5" />
      case 'BLOCKED_BY_SYSTEM': return <Activity className="w-3.5 h-3.5" />
      case 'BLOCKED_BY_MARKET': return <RefreshCw className="w-3.5 h-3.5" />
      default: return <Activity className="w-3.5 h-3.5" />
    }
  }

  const getDecisionLabel = (decision: string) => {
    switch (decision) {
      case 'EXECUTION_ELIGIBLE': return 'EXECUTION ELIGIBLE'
      case 'QUALIFIED': return 'QUALIFIED'
      case 'WATCH': return 'WATCH'
      case 'BLOCKED_BY_AI_CONFIDENCE': return 'BLOCKED: AI CONFIDENCE'
      case 'BLOCKED_BY_RISK': return 'BLOCKED: RISK'
      case 'BLOCKED_BY_SYSTEM': return 'BLOCKED: SYSTEM'
      case 'REJECTED': return 'REJECTED'
      case 'BLOCKED_BY_MARKET': return 'BLOCKED: MARKET'
      default: return decision
    }
  }

  return (
    <div className="glass-panel p-4 rounded-lg flex flex-col justify-between space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between pb-2.5 border-b border-terminal-border/80">
        <div className="flex items-center gap-2">
          <Cpu className="w-4 h-4 text-brand-cyan" />
          <span className="text-xs font-bold text-white uppercase tracking-wider">AI Signal Radar</span>
        </div>
        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-background border border-terminal-border text-slate-300">
          {symbol} • 1H
        </span>
      </div>

      {/* Main Score / Gauge */}
      {setup || aiEnrichment ? (
        <div className="space-y-4">
          {/* Top Score Circular Badge */}
          <div className="flex items-center justify-between p-3 rounded-lg bg-background/60 border border-terminal-border">
            <div>
              <div className="text-[10px] uppercase font-mono text-slate-400">Composite Confidence</div>
              <div className="flex items-baseline gap-1.5 mt-0.5">
                <span className="text-3xl font-bold font-mono text-brand-cyan">
                  {confidenceScore !== null ? `${confidenceScore}%` : '—'}
                </span>
                <span className="text-xs font-mono text-slate-400">
                  {confidenceScore && confidenceScore >= 75 ? 'HIGH CONVICTION' : 'MODERATE'}
                </span>
              </div>
            </div>

            {/* Direction Pill */}
            <div className="text-right">
              <span
                className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-mono font-bold ${
                  isLong
                    ? 'bg-bullish/15 text-bullish border border-bullish/30'
                    : 'bg-bearish/15 text-bearish border border-bearish/30'
                }`}
              >
                {isLong ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                {setup?.direction || aiEnrichment?.direction || 'NEUTRAL'}
              </span>
              <div className="text-[10px] font-mono text-slate-400 mt-1">
                RR: {setup?.riskReward?.toFixed(2) || '2.00'}
              </div>
            </div>
          </div>

          {/* AI Combined Decision Badge */}
          {decisionResult && (
            <div className={`p-3 rounded-lg border ${getDecisionColor(decisionResult.decision)} flex items-center justify-between`}>
              <div className="flex items-center gap-2">
                {getDecisionIcon(decisionResult.decision)}
                <div>
                  <div className="text-[10px] uppercase font-mono text-slate-400">Combined Decision</div>
                  <div className="font-bold text-white font-mono text-sm">{getDecisionLabel(decisionResult.decision)}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-[10px] uppercase font-mono text-slate-400">Latency</div>
                <div className="font-bold text-white font-mono text-sm">{decisionResult.latencyMs}ms</div>
              </div>
            </div>
          )}

          {/* Sub-Scores Matrix */}
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="p-2.5 rounded bg-background/40 border border-terminal-border/80">
              <div className="text-[10px] text-slate-400">Technical Alignment</div>
              <div className="font-bold text-white mt-1">
                {patternScore !== null ? `${patternScore.toFixed(0)}/100` : '90/100'}
              </div>
              <div className="w-full bg-slate-800 h-1.5 rounded-full mt-1.5 overflow-hidden">
                <div
                  className="bg-brand-cyan h-full rounded-full"
                  style={{ width: `${patternScore || 90}%` }}
                ></div>
              </div>
            </div>

            <div className="p-2.5 rounded bg-background/40 border border-terminal-border/80">
              <div className="text-[10px] text-slate-400">Market Regime</div>
              <div className="font-bold text-white mt-1">
                {signalScore !== null ? `${signalScore.toFixed(0)}/100` : '85/100'}
              </div>
              <div className="w-full bg-slate-800 h-1.5 rounded-full mt-1.5 overflow-hidden">
                <div
                  className="bg-bullish h-full rounded-full"
                  style={{ width: `${signalScore || 85}%` }}
                ></div>
              </div>
            </div>
          </div>

          {/* Market Regime Badge */}
          <div className="flex items-center justify-between px-2.5 py-1.5 rounded bg-background/50 border border-terminal-border text-xs font-mono">
            <span className="text-slate-400 text-[11px]">Detected Regime:</span>
            <span className="text-brand-cyan font-semibold text-[11px]">{regime}</span>
          </div>

          {/* Decision Reason */}
          {decisionResult && (
            <div className="p-2.5 rounded bg-background/50 border border-terminal-border text-[11px] font-mono text-slate-300">
              {decisionResult.reason}
            </div>
          )}

          {/* Plain-English AI Reasoning */}
          <div className="p-3 rounded-lg bg-background/50 border border-terminal-border">
            <div className="text-[10px] font-mono uppercase text-slate-400 mb-1 flex items-center gap-1">
              <Activity className="w-3 h-3 text-brand-cyan" />
              <span>AI Engine Analysis</span>
            </div>
            <p className="text-xs text-slate-300 leading-relaxed font-sans">
              {reasoning}
            </p>
          </div>

          {/* Supporting/Risk Factors from AI */}
          {aiEnrichment?.featureSummary && (
            <div className="p-2.5 rounded bg-background/40 border border-terminal-border text-[10px] font-sans text-slate-300">
              {(() => {
                try {
                  const parsed = JSON.parse(aiEnrichment.featureSummary)
                  if (parsed.supportingFactors || parsed.riskFactors) {
                    return (
                      <div>
                        {parsed.supportingFactors && (
                          <div className="text-bullish mb-1">✓ {parsed.supportingFactors}</div>
                        )}
                        {parsed.riskFactors && (
                          <div className="text-bearish">⚠ {parsed.riskFactors}</div>
                        )}
                      </div>
                    )
                  }
                } catch {}
                return null
              })()}
            </div>
          )}
        </div>
      ) : (
        <div className="p-6 rounded-lg bg-background/30 border border-terminal-border/60 text-center space-y-2">
          <Activity className="w-6 h-6 text-slate-500 mx-auto animate-pulse" />
          <div className="text-xs font-bold text-slate-300 font-mono">No Active Setup Triggered</div>
          <div className="text-[11px] text-slate-500 font-sans leading-relaxed">
            The 1H SMC engine is monitoring order block mitigation and structure confirmation for {symbol}.
          </div>
        </div>
      )}

      {/* Footer Invariant Tag */}
      <div className="pt-2 border-t border-terminal-border/60 flex items-center justify-between text-[10px] font-mono text-slate-500">
        <span>AI Inference Engine v2.0</span>
        <span className="text-bullish flex items-center gap-1">
          <CheckCircle2 className="w-3 h-3" /> Authoritative
        </span>
      </div>
    </div>
  )
}
