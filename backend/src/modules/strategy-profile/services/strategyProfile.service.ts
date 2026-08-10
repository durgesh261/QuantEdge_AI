import { StrategyProfileDto, CreateStrategyProfileInput } from '@algoapp/shared';

const default1HProfile: StrategyProfileDto = {
  id: 'DEF-1H-PROF',
  name: 'Default 1H Institutional Profile',
  description: 'Standard 1H market structure profile optimized for perpetual pairs',
  version: '1.0.0',
  isActive: true,
  pair: 'BTCUSD.P',
  timeframe: '1H',
  patConfig: {
    zigzagLen: 9,
    liquidityLen: 30,
    atrPeriod: 14,
    obShowCount: 2,
    trendLineLen: 20,
  },
  smcConfig: {
    swingLen: 50,
    internalShow: true,
    swingShow: true,
    atrFilterThreshold: 2.0,
    mitigationSource: 'High/Low',
    obSize: 5,
  },
  riskConfig: {
    challengeMode: false,
    maxRiskPerTradePercent: 35.0,
    maxDailyDrawdownPercent: 100.0,
    maxOpenPositions: 1,
    dynamicLeverage: true,
  },
  executionConfig: {
    defaultMode: 'PAPER',
  },
  indicatorConfig: {
    mergeThreshold: 0.4,
    freshnessDecay: 0.015,
    maxTouches: 1,
    scoreWeights: {
      zoneStrength: 0.3,
      freshness: 0.25,
      trend: 0.2,
      liquidity: 0.15,
      merged: 0.1,
    },
  },
  decisionConfig: {
    confidenceThreshold: 85.0,
    minZoneScore: 70.0,
    momentumRules: true,
  },
  createdAt: '2026-08-03T12:00:00Z',
  updatedAt: '2026-08-03T12:00:00Z',
};

const default15MProfile: StrategyProfileDto = {
  id: 'DEF-15M-PROF',
  name: 'Default 15M Scalping Profile',
  description: 'Fast 15M market structure profile optimized for intraday momentum',
  version: '1.0.0',
  isActive: true,
  pair: 'BTCUSD.P',
  timeframe: '15M',
  patConfig: {
    zigzagLen: 5,
    liquidityLen: 15,
    atrPeriod: 14,
    obShowCount: 2,
    trendLineLen: 10,
  },
  smcConfig: {
    swingLen: 20,
    internalShow: true,
    swingShow: true,
    atrFilterThreshold: 2.0,
    mitigationSource: 'High/Low',
    obSize: 5,
  },
  riskConfig: {
    challengeMode: false,
    maxRiskPerTradePercent: 35.0,
    maxDailyDrawdownPercent: 100.0,
    maxOpenPositions: 1,
    dynamicLeverage: true,
  },
  executionConfig: {
    defaultMode: 'PAPER',
  },
  indicatorConfig: {
    mergeThreshold: 0.4,
    freshnessDecay: 0.02,
    maxTouches: 1,
    scoreWeights: {
      zoneStrength: 0.3,
      freshness: 0.25,
      trend: 0.2,
      liquidity: 0.15,
      merged: 0.1,
    },
  },
  decisionConfig: {
    confidenceThreshold: 85.0,
    minZoneScore: 70.0,
    momentumRules: true,
  },
  createdAt: '2026-08-03T12:00:00Z',
  updatedAt: '2026-08-03T12:00:00Z',
};

let profilesStore: StrategyProfileDto[] = [default1HProfile, default15MProfile];

export class StrategyProfileService {
  public async getProfiles(): Promise<StrategyProfileDto[]> {
    return profilesStore;
  }

  public async getProfileById(id: string): Promise<StrategyProfileDto | undefined> {
    return profilesStore.find((p) => p.id === id) || default1HProfile;
  }

  public async createProfile(input: CreateStrategyProfileInput): Promise<StrategyProfileDto> {
    const profile: StrategyProfileDto = {
      id: `PROF-${Date.now()}`,
      name: input.name,
      description: input.description,
      version: '1.0.0',
      isActive: true,
      pair: input.pair || 'BTCUSD.P',
      timeframe: input.timeframe,
      patConfig: { ...default1HProfile.patConfig, ...input.patConfig },
      smcConfig: { ...default1HProfile.smcConfig, ...input.smcConfig },
      riskConfig: { ...default1HProfile.riskConfig, ...input.riskConfig },
      executionConfig: { ...default1HProfile.executionConfig, ...input.executionConfig },
      indicatorConfig: { ...default1HProfile.indicatorConfig, ...input.indicatorConfig },
      decisionConfig: { ...default1HProfile.decisionConfig, ...input.decisionConfig },
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    profilesStore.push(profile);
    return profile;
  }

  public async updateProfile(id: string, updates: Partial<StrategyProfileDto>): Promise<StrategyProfileDto> {
    const index = profilesStore.findIndex((p) => p.id === id);
    if (index === -1) {
      throw new Error(`StrategyProfile ${id} not found.`);
    }

    const existing = profilesStore[index]!;
    const updated: StrategyProfileDto = {
      ...existing,
      ...updates,
      updatedAt: new Date().toISOString(),
    };

    profilesStore[index] = updated;
    return updated;
  }
}
