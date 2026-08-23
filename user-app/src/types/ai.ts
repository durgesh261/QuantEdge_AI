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
