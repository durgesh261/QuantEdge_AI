export type TradingTimeframe = '15M' | '1H';

export interface PatConfig {
  zigzagLen: number;
  liquidityLen: number;
  atrPeriod: number;
  obShowCount: number;
  trendLineLen: number;
}

export interface SmcConfig {
  swingLen: number;        // Pine Script leg(swingLen) — default 50
  internalLen?: number;    // Pine Script leg(internalLen) — default 5 (v5.1: config only, not UI)
  internalShow: boolean;
  swingShow: boolean;
  atrFilterThreshold: number;
  mitigationSource: 'High/Low' | 'Close';
  obSize: number;
}

export interface RiskConfig {
  challengeMode: boolean;
  maxRiskPerTradePercent: number;
  maxDailyDrawdownPercent: number;
  maxOpenPositions: number;
  dynamicLeverage: boolean;
}

export interface ExecutionConfig {
  defaultMode: 'PAPER' | 'LIVE';
}

export interface IndicatorConfig {
  mergeThreshold: number;
  freshnessDecay: number;
  maxTouches: number;
  scoreWeights: {
    zoneStrength: number;
    freshness: number;
    trend: number;
    liquidity: number;
    merged: number;
  };
}

export interface DecisionConfig {
  confidenceThreshold: number;
  minZoneScore: number;
  momentumRules: boolean;
}

export interface StrategyProfileDto {
  id: string;
  name: string;
  description: string;
  version: string;
  isActive: boolean;
  pair: string;
  timeframe: TradingTimeframe;
  patConfig: PatConfig;
  smcConfig: SmcConfig;
  riskConfig: RiskConfig;
  executionConfig: ExecutionConfig;
  indicatorConfig: IndicatorConfig;
  decisionConfig: DecisionConfig;
  createdAt: string;
  updatedAt: string;
}

export interface CreateStrategyProfileInput {
  name: string;
  description: string;
  pair: string;
  timeframe: TradingTimeframe;
  patConfig?: Partial<PatConfig> | undefined;
  smcConfig?: Partial<SmcConfig> | undefined;
  riskConfig?: Partial<RiskConfig> | undefined;
  executionConfig?: Partial<ExecutionConfig> | undefined;
  indicatorConfig?: Partial<IndicatorConfig> | undefined;
  decisionConfig?: Partial<DecisionConfig> | undefined;
}
