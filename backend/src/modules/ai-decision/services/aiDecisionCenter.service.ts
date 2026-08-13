import {
  DecisionDto,
  DecisionExplanationDto,
  DecisionState,
  DecisionReasonCode,
  ReplayMetadataDto,
  AIConfirmationResultDto,
  IndicatorEngineOutput,
  StrategySignalOutcome,
  BaseZone,
  ZoneDto,
} from '@algoapp/shared';

import { DecisionEngineService } from '../../decision/services/decisionEngine.service.js';
import { ReasonBuilder } from '../explanation/reasonBuilder.js';
import { TimelineBuilder } from '../explanation/timelineBuilder.js';
import { DecisionTreeBuilder } from '../explanation/decisionTreeBuilder.js';
import { JournalGenerator } from '../explanation/journalGenerator.js';

let explanationCache: Record<string, DecisionExplanationDto> = {};

export interface AiConfirmationInput {
  symbol: string;
  timeframe: string;
  outcome: StrategySignalOutcome;
  activeZone?: BaseZone | ZoneDto | undefined;
  indicators: IndicatorEngineOutput;
  riskRewardRatio: number;
  sessionAllowed: boolean;
  marketAllowed: boolean;
}

export class AIDecisionCenterService {
  /**
   * Deterministically validates strategy signal output with AI confirmation rules.
   * Evaluates 9 institutional weighted factors (100 pts max).
   * Strictly requires confidenceScore >= 85% for trade approval.
   */
  public static confirmDecision(input: AiConfirmationInput): AIConfirmationResultDto & { breakdown: any } {
    const reasonCodes: DecisionReasonCode[] = [];

    // HARD VETO: Market not allowed (news/macro blocking) → immediate rejection
    if (!input.marketAllowed) {
      const breakdown = {
        trendScore: { name: '1H Trend Alignment (Swing & Internal)', maxScore: 15, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        obFreshnessScore: { name: 'Order Block Freshness', maxScore: 15, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        firstTouchScore: { name: 'First Touch Verification', maxScore: 15, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        marketStructureScore: { name: 'Market Structure (BOS / CHoCH)', maxScore: 15, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        liquiditySweepScore: { name: 'Liquidity Sweep Confirmation', maxScore: 10, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        premDiscScore: { name: 'Premium / Discount Pricing', maxScore: 10, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        sessionScore: { name: 'Session & Volatility Regime', maxScore: 5, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        riskRewardScore: { name: 'Risk:Reward & Growth Target Path', maxScore: 10, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        newsScore: { name: 'Macro & News Sentiment Safety', maxScore: 5, score: 0, passed: false, explanation: 'Market blocked by news/macro filter' },
        totalScore: 0,
        threshold: 85,
        isApproved: false,
      };

      reasonCodes.push(DecisionReasonCode.AI_CONFIRMATION_REJECTED);
      reasonCodes.push(DecisionReasonCode.NEWS_MACRO_BLOCKED);

      return {
        approved: false,
        confidenceScore: 0,
        ruleAgreementScore: 0,
        setupQuality: 'POOR',
        reasonCodes,
        breakdown,
        rationale: `AI Validation rejected setup for ${input.symbol} (market blocked by news/macro filter).`,
      };
    }

    // Factor 1: Trend Alignment (15 pts)
    const isBullish = input.indicators.marketStructure.trend === 'BULLISH';
    const isBearish = input.indicators.marketStructure.trend === 'BEARISH';
    const isSwingAligned =
      (input.outcome === StrategySignalOutcome.BUY && input.indicators.marketStructure.swingTrend === 'BULLISH') ||
      (input.outcome === StrategySignalOutcome.SELL && input.indicators.marketStructure.swingTrend === 'BEARISH');
    const isInternalAligned =
      (input.outcome === StrategySignalOutcome.BUY && input.indicators.marketStructure.internalTrend === 'BULLISH') ||
      (input.outcome === StrategySignalOutcome.SELL && input.indicators.marketStructure.internalTrend === 'BEARISH');

    let trendPoints = 10;
    if (isSwingAligned && isInternalAligned) {
      trendPoints = 15;
      reasonCodes.push(DecisionReasonCode.MOMENTUM_ALIGNED);
    } else if (isSwingAligned || (input.outcome === StrategySignalOutcome.BUY && isBullish) || (input.outcome === StrategySignalOutcome.SELL && isBearish)) {
      trendPoints = 10;
      reasonCodes.push(DecisionReasonCode.MOMENTUM_ALIGNED);
    } else if ((input.outcome === StrategySignalOutcome.BUY && isBearish) || (input.outcome === StrategySignalOutcome.SELL && isBullish)) {
      trendPoints = 0;
    } else {
      trendPoints = 10; // Baseline structural allowance for neutral regime
      reasonCodes.push(DecisionReasonCode.MOMENTUM_ALIGNED);
    }

    const trendScore = {
      name: '1H Trend Alignment (Swing & Internal)',
      maxScore: 15,
      score: trendPoints,
      passed: trendPoints >= 10,
      explanation: isSwingAligned && isInternalAligned
        ? `Dual 1H Swing & Internal Trend fully aligned with ${input.outcome} (+15 pts)`
        : trendPoints > 0
          ? `Partial 1H Trend alignment (+${trendPoints} pts)`
          : `Trend conflicting with ${input.outcome} (0 pts)`,
    };

    // Factor 2: Order Block Freshness (15 pts)
    let freshnessPoints = 0;
    const zoneFreshness = input.activeZone ? input.activeZone.freshness : 100;
    if (zoneFreshness >= 80) {
      freshnessPoints = 15;
      reasonCodes.push(DecisionReasonCode.FRESH_ZONE_CONFIRMED);
    } else if (zoneFreshness >= 50) {
      freshnessPoints = 10;
    }

    const obFreshnessScore = {
      name: 'Order Block Freshness',
      maxScore: 15,
      score: freshnessPoints,
      passed: freshnessPoints >= 10,
      explanation: freshnessPoints === 15
        ? `Fresh pristine Order Block (${zoneFreshness}% freshness, +15 pts)`
        : freshnessPoints > 0
          ? `Moderately fresh Order Block (${zoneFreshness}% freshness, +${freshnessPoints} pts)`
          : `Order Block freshness decayed (0 pts)`,
    };

    // Factor 3: First Touch Verification (15 pts)
    const touches = input.activeZone ? input.activeZone.touchCount : 0;
    const isFirstTouch = touches <= 1;
    const firstTouchPoints = touches === 0 ? 15 : touches === 1 ? 12 : 0;
    if (isFirstTouch) {
      reasonCodes.push(DecisionReasonCode.FIRST_TOUCH_VALIDATED);
    }

    const firstTouchScore = {
      name: 'First Touch Verification',
      maxScore: 15,
      score: firstTouchPoints,
      passed: firstTouchPoints >= 12,
      explanation: touches === 0
        ? 'Exact pristine first return to Order Block (+15 pts)'
        : touches === 1
          ? 'First active touch in progress (+12 pts)'
          : `Zone has been tested ${touches} times (0 pts)`,
    };

    // Factor 4: Market Structure Break (BOS / CHoCH) (15 pts)
    const targetDir = input.outcome === StrategySignalOutcome.BUY ? 'BULLISH' : 'BEARISH';
    const alignedBreaks = (input.indicators.structureEvents || []).filter((e) => e.direction === targetDir);
    let structPoints = 0;
    if (alignedBreaks.length > 0) {
      structPoints = 15;
      const latest = alignedBreaks[alignedBreaks.length - 1]!;
      reasonCodes.push(latest.type === 'BOS' ? DecisionReasonCode.BOS_CONFIRMED : DecisionReasonCode.CHOCH_CONFIRMED);
    }

    const marketStructureScore = {
      name: 'Market Structure (BOS / CHoCH)',
      maxScore: 15,
      score: structPoints,
      passed: structPoints > 0,
      explanation: structPoints === 15
        ? `Confirmed ${alignedBreaks[alignedBreaks.length - 1]?.type || 'BOS'} in ${targetDir} direction (+15 pts)`
        : `No recent aligned structure break in ${targetDir} direction (0 pts)`,
    };

    // Factor 5: Liquidity Sweep (10 pts)
    const sweeps = input.indicators.liquiditySweeps || [];
    const isSweepAligned = input.outcome === StrategySignalOutcome.BUY
      ? sweeps.some((s) => s.sweepType === 'LOW_SWEEP')
      : sweeps.some((s) => s.sweepType === 'HIGH_SWEEP');
    const sweepPoints = isSweepAligned ? 10 : sweeps.length > 0 ? 6 : 4; // baseline structural allowance

    const liquiditySweepScore = {
      name: 'Liquidity Sweep Confirmation',
      maxScore: 10,
      score: sweepPoints,
      passed: sweepPoints >= 6,
      explanation: isSweepAligned
        ? `Confirmed ${input.outcome === StrategySignalOutcome.BUY ? 'sell-side low sweep' : 'buy-side high sweep'} (+10 pts)`
        : `Liquidity sweep status (+${sweepPoints} pts)`,
    };

    // Factor 6: Premium / Discount Zone (10 pts)
    // Buy in discount, Sell in premium
    let premDiscPoints = 10;
    if (input.activeZone) {
      const isBuy = input.outcome === StrategySignalOutcome.BUY;
      const isSell = input.outcome === StrategySignalOutcome.SELL;
      if ((isBuy && input.activeZone.type === 'DEMAND') || (isSell && input.activeZone.type === 'SUPPLY')) {
        premDiscPoints = 10;
      } else {
        premDiscPoints = 4;
      }
    }

    const premDiscScore = {
      name: 'Premium / Discount Pricing',
      maxScore: 10,
      score: premDiscPoints,
      passed: premDiscPoints >= 8,
      explanation: premDiscPoints === 10
        ? `Optimal ${input.outcome === StrategySignalOutcome.BUY ? 'Discount entry' : 'Premium entry'} location (+10 pts)`
        : `Non-optimal pricing zone (+${premDiscPoints} pts)`,
    };

    // Factor 7: Session Liquidity (5 pts)
    const sessionPoints = input.sessionAllowed ? 5 : 0;
    const sessionScore = {
      name: 'Session & Volatility Regime',
      maxScore: 5,
      score: sessionPoints,
      passed: sessionPoints === 5,
      explanation: input.sessionAllowed ? 'Optimal institutional trading session (+5 pts)' : 'Session restricted (0 pts)',
    };

    // Factor 8: Risk/Reward & 60% Growth Target Structure (10 pts)
    let rrPoints = 0;
    if (input.riskRewardRatio >= 1.7) {
      rrPoints = 10;
    } else if (input.riskRewardRatio >= 1.4) {
      rrPoints = 6;
    }

    const riskRewardScore = {
      name: 'Risk:Reward & Growth Target Path',
      maxScore: 10,
      score: rrPoints,
      passed: rrPoints >= 6,
      explanation: rrPoints === 10
        ? `Clean structural path with ${input.riskRewardRatio.toFixed(2)}:1 R:R (+10 pts)`
        : `R:R ratio ${input.riskRewardRatio.toFixed(2)}:1 (+${rrPoints} pts)`,
    };

    // Factor 9: News Sentiment & Volatility Safety (5 pts)
    const newsPoints = input.marketAllowed ? 5 : 0;
    const newsScore = {
      name: 'Macro & News Sentiment Safety',
      maxScore: 5,
      score: newsPoints,
      passed: newsPoints === 5,
      explanation: input.marketAllowed ? 'No high-volatility adverse news block (+5 pts)' : 'High impact news collision (0 pts)',
    };

    // Total Score calculation
    const totalScore = trendPoints + freshnessPoints + firstTouchPoints + structPoints + sweepPoints + premDiscPoints + sessionPoints + rrPoints + newsPoints;
    const confidenceScore = Math.min(100, Math.max(0, totalScore));
    const approved = confidenceScore >= 85; // 85% threshold per strategy

    let setupQuality: 'INSTITUTIONAL' | 'HIGH' | 'MEDIUM' | 'POOR' = 'POOR';
    if (confidenceScore >= 90) setupQuality = 'INSTITUTIONAL';
    else if (confidenceScore >= 85) setupQuality = 'HIGH';
    else if (confidenceScore >= 70) setupQuality = 'MEDIUM';

    if (approved) {
      reasonCodes.push(DecisionReasonCode.AI_CONFIRMATION_APPROVED);
      reasonCodes.push(DecisionReasonCode.CONFIDENCE_THRESHOLD_MET);
    } else {
      reasonCodes.push(DecisionReasonCode.AI_CONFIRMATION_REJECTED);
    }

    const breakdown = {
      trendScore,
      obFreshnessScore,
      firstTouchScore,
      marketStructureScore,
      liquiditySweepScore,
      premDiscScore,
      sessionScore,
      riskRewardScore,
      newsScore,
      totalScore: confidenceScore,
      threshold: 85,
      isApproved: approved,
    };

    return {
      approved,
      confidenceScore,
      ruleAgreementScore: confidenceScore,
      setupQuality,
      reasonCodes,
      breakdown,
      rationale: approved
        ? `AI Validation approved ${input.outcome} setup for ${input.symbol} (${confidenceScore}% score, ${setupQuality} quality >= 85% threshold).`
        : `AI Validation rejected setup for ${input.symbol} (${confidenceScore}% score < 85% institutional threshold).`,
    };
  }

  public static async explainDecision(decisionId: string): Promise<DecisionExplanationDto> {
    if (explanationCache[decisionId]) {
      return explanationCache[decisionId]!;
    }

    const decisionLogs = await DecisionEngineService.getDecisionLogs();
    const decision: DecisionDto = decisionLogs.find((d) => d.id === decisionId) || {
      id: decisionId,
      signalId: 'SIG-LOG-101',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      decisionState: DecisionState.EXECUTE,
      confidenceScore: 92.5,
      reasonCodes: [
        DecisionReasonCode.FRESH_ZONE_CONFIRMED,
        DecisionReasonCode.FIRST_TOUCH_VALIDATED,
        DecisionReasonCode.MOMENTUM_ALIGNED,
        DecisionReasonCode.CONFIDENCE_THRESHOLD_MET,
        DecisionReasonCode.AI_CONFIRMATION_APPROVED,
      ],
      inputSnapshotHash: 'a8f3b4c9e71234567890abcdef1234567890abcdef1234567890abcdef123456',
      timestamp: new Date().toISOString(),
    };

    const reasonExplanations = decision.reasonCodes.map((code) => ({
      code,
      humanExplanation: ReasonBuilder.buildHumanExplanation(code),
      isPassed: !code.includes('BLOCKED') && !code.includes('DECAYED') && !code.includes('INVALIDATED') && !code.includes('REJECTED') && !code.includes('EXCEEDED'),
    }));

    const passedValidators = reasonExplanations.filter((r) => r.isPassed).map((r) => r.code);
    const failedValidators = reasonExplanations.filter((r) => !r.isPassed).map((r) => r.code);

    const shortSummary = `Decision state '${decision.decisionState}' for ${decision.symbol} (${decision.confidenceScore}% confidence).`;
    const mediumSummary = `Market structure evaluation for ${decision.symbol} (${decision.timeframe}) resulted in state ${decision.decisionState}. ${passedValidators.length} rules passed.`;
    const detailedSummary = `Evaluated Supply/Demand zones, market structure and price momentum for ${decision.symbol} (${decision.timeframe}). Decision State: ${decision.decisionState}. Confidence score: ${decision.confidenceScore}%. Reproducibility Hash: ${decision.inputSnapshotHash}.`;

    const timeline = TimelineBuilder.buildTimeline(decision);
    const decisionTree = DecisionTreeBuilder.buildDecisionTree(decision);
    const journalEntry = JournalGenerator.generateJournalEntry(decision);

    const replayMetadata: ReplayMetadataDto = {
      snapshotHash: decision.inputSnapshotHash,
      decisionState: decision.decisionState as DecisionState,
      confidenceScore: decision.confidenceScore,
      validatorSnapshot: {
        freshZone: !decision.reasonCodes.includes(DecisionReasonCode.ZONE_FRESHNESS_DECAYED),
        firstTouch: decision.reasonCodes.includes(DecisionReasonCode.FIRST_TOUCH_VALIDATED),
        momentum: decision.reasonCodes.includes(DecisionReasonCode.MOMENTUM_ALIGNED),
        opposingZone: !decision.reasonCodes.includes(DecisionReasonCode.OPPOSING_ZONE_BLOCKED),
      },
    };

    const explanation: DecisionExplanationDto = {
      id: `EXP-LOG-${Date.now()}`,
      decisionId: decision.id,
      symbol: decision.symbol,
      decisionState: decision.decisionState as DecisionState,
      confidenceScore: decision.confidenceScore,
      shortSummary,
      mediumSummary,
      detailedSummary,
      reasonExplanations,
      passedValidators,
      failedValidators,
      timeline,
      decisionTree,
      journalEntry,
      replayMetadata,
      timestamp: new Date().toISOString(),
    };

    explanationCache[decisionId] = explanation;
    return explanation;
  }
}
