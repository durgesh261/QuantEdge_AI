export interface AiEnrichmentDto {
  id: string
  setupId: string
  accountId: string | null
  symbol: string
  direction: string
  intelligenceVersion: string
  patternScore: number
  signalScore: number
  confidence: number
  marketRegime: string
  marketContext: string
  modelMetadata: string | null
  featureSummary: string | null
  generatedAt: string
}

export interface AiDecisionResultDto {
  decision: 'REJECTED' | 'WATCH' | 'QUALIFIED' | 'EXECUTION_ELIGIBLE' | 'BLOCKED_BY_RISK' | 'BLOCKED_BY_SYSTEM' | 'BLOCKED_BY_AI_CONFIDENCE' | 'BLOCKED_BY_MARKET'
  reason: string
  riskDetail: string | null
  latencyMs: number
}

export interface AiDecisionEvaluationRequest {
  setupId: string
  accountId?: string
  killSwitchActive: boolean
  algoEnabled: boolean
}

export interface AiDecisionAuditDto {
  id: string
  setupId: string
  symbol: string
  direction: string
  modelName: string
  modelVersion: string
  featureVersion: string
  smcDirection: string | null
  smcRiskReward: number | null
  smcConfidence: number | null
  smcSetupState: string | null
  aiPatternScore: number | null
  aiSignalScore: number | null
  aiConfidence: number | null
  aiMarketRegime: string | null
  aiExplanation: string | null
  supportingFactors: string | null
  riskFactors: string | null
  combinedDecision: string
  decisionReason: string | null
  riskDecision: string | null
  executionDecision: string | null
  inferenceLatencyMs: number | null
  featureVectorHash: string | null
  decisionTimestamp: string
}
