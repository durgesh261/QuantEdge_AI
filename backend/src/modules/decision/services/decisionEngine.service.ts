import crypto from 'crypto';
import {
  DecisionDto,
  DecisionState,
  DecisionReasonCode,
  StrategySignalOutcome,
  TradingTimeframe,
  IndicatorEngineOutput,
  BaseZone,
  ZoneDto,
} from '@algoapp/shared';

import { logger } from '../../../logger/index.js';
import { SessionFilterEngine } from '../filters/sessionFilterEngine.js';
import { MarketFilterEngine } from '../filters/marketFilterEngine.js';
import { SignalDeduplicationEngine } from '../deduplication/signalDeduplicationEngine.js';
import { TrendValidator } from '../validators/trendValidator.js';
import { ZoneValidator } from '../validators/zoneValidator.js';
import { MarketStructureValidator } from '../validators/marketStructureValidator.js';
import { LiquidityValidator } from '../validators/liquidityValidator.js';
import { RiskValidator } from '../validators/riskValidator.js';
import { PositionSizingEngine } from '../sizing/positionSizingEngine.js';
import { AIDecisionCenterService } from '../../ai-decision/services/aiDecisionCenter.service.js';
import { deltaSyncService } from '../../delta-exchange/index.js';

let decisionLogs: DecisionDto[] = [];

export interface EvaluateDecisionInput {
  symbol: string;
  timeframe: TradingTimeframe;
  currentPrice: number;
  indicators: IndicatorEngineOutput;
  activeZone?: BaseZone | ZoneDto | undefined;
  outcome?: StrategySignalOutcome | undefined;
  candleTimestamp?: string | undefined;
}

export class DecisionEngineService {
  public static async getDecisionLogs(): Promise<DecisionDto[]> {
    return decisionLogs;
  }

  public static clearDecisionLogs(): void {
    decisionLogs = [];
  }

  /**
   * Evaluates a trade candidate through the deterministic decision pipeline.
   * REJECTS legacy string signature — only accepts structured input.
   */
  public static async evaluateDecision(
    input: EvaluateDecisionInput
  ): Promise<DecisionDto> {
    if (!input || typeof input !== 'object') {
      throw new Error('DecisionEngine.evaluateDecision requires structured EvaluateDecisionInput. Legacy string signature removed.');
    }

    const {
      symbol,
      timeframe,
      currentPrice,
      indicators,
      activeZone,
      candleTimestamp = new Date().toISOString(),
    } = input;

    // Get REAL account data from Delta Exchange
    const balances = deltaSyncService.getBalances();
    const usdtBalance = balances.find((b) => b.asset_symbol === 'USDT' || b.asset_symbol === 'USD');
    const accountBalance = usdtBalance ? parseFloat(usdtBalance.balance || '0') : 0;
    const availableMargin = usdtBalance ? parseFloat(usdtBalance.available_balance || '0') : 0;
    
    const positions = deltaSyncService.getPositions();
    const hasOpenPosition = positions.length > 0;
    const openPositionCount = positions.length;

    const reasonCodes: DecisionReasonCode[] = [];

    // 1. Session Filter
    const sessionResult = SessionFilterEngine.evaluateSession(candleTimestamp);
    if (!sessionResult.allowed && sessionResult.reasonCode) {
      reasonCodes.push(sessionResult.reasonCode);
    }

    // 2. Market Regime & Volatility Filter (allowRanging=false blocks choppy/ranging markets)
    const marketResult = MarketFilterEngine.evaluateMarket(indicators, false);
    if (!marketResult.allowed && marketResult.reasonCode) {
      reasonCodes.push(marketResult.reasonCode);
    }

    // Determine Candidate Outcome
    let outcome = input.outcome;
    if (!outcome || outcome === StrategySignalOutcome.WAIT) {
      if (activeZone) {
        outcome =
          activeZone.type === 'DEMAND'
            ? StrategySignalOutcome.BUY
            : StrategySignalOutcome.SELL;
      } else {
        outcome =
          indicators.marketStructure.trend === 'BULLISH'
            ? StrategySignalOutcome.BUY
            : StrategySignalOutcome.SELL;
      }
    }

    // 3. Trend Alignment Validator
    const trendResult = TrendValidator.validate(outcome, indicators.marketStructure);
    if (trendResult.passed && trendResult.reasonCode) {
      reasonCodes.push(trendResult.reasonCode);
    }

    // 4. Zone Validator
    const zoneResult = ZoneValidator.validate(activeZone);
    if (zoneResult.reasonCode && !reasonCodes.includes(zoneResult.reasonCode)) {
      reasonCodes.push(zoneResult.reasonCode);
    }

    // 4a. Used-OB guard — reject if this OB has already generated a trade
    if (activeZone && (activeZone as any).id) {
      const { OrderBlockWidthEngine } = await import('../../indicator-engine/engines/orderBlockWidthEngine.js');
      if (OrderBlockWidthEngine.isUsed((activeZone as any).id)) {
        const usedDecision: DecisionDto = {
          id: `DEC-${Date.now()}`,
          symbol, timeframe,
          state: DecisionState.SKIP,
          outcome,
          entryPrice: currentPrice, stopLossPrice: currentPrice, takeProfitPrice: currentPrice,
          positionSize: 0, leverage: 0, riskPercent: 0, confidenceScore: 0,
          reasonCodes: ['OB_ALREADY_USED' as any, ...reasonCodes],
          inputSnapshotHash: '',
          createdAt: new Date().toISOString(),
        };
        decisionLogs.unshift(usedDecision);
        return usedDecision;
      }
    }

    // 4b. First-touch tracking — add reason code so downstream knows this is a fresh entry
    if (activeZone && (activeZone.touchCount === 0 || (activeZone as any).status === 'FIRST_TOUCH' || (activeZone as any).status === 'NEW')) {
      reasonCodes.push('FIRST_TOUCH_ENTRY' as any);
    }

    // 5. Market Structure Validator (BOS / CHoCH)
    const structResult = MarketStructureValidator.validate(outcome, indicators.structureEvents || []);
    if (structResult.passed && structResult.reasonCode && !reasonCodes.includes(structResult.reasonCode)) {
      reasonCodes.push(structResult.reasonCode);
    }

    // ── News Filter Check (Strategy §21) ──
    const { NewsFilterEngine } = await import('../../news/services/NewsFilterEngine.js');
    await NewsFilterEngine.fetchLatestEvents(); // Refresh if stale
    
    if (NewsFilterEngine.isBlocking()) {
      reasonCodes.push('NEWS_FILTER_BLOCKING' as any);
      
      const blockedDecision: DecisionDto = {
        id: `DEC-${Date.now()}`,
        symbol,
        timeframe,
        state: DecisionState.SKIP,
        outcome,
        entryPrice: currentPrice,
        stopLossPrice: currentPrice,
        takeProfitPrice: currentPrice,
        positionSize: 0,
        leverage: 0,
        riskPercent: 0,
        confidenceScore: 0,
        reasonCodes,
        inputSnapshotHash: '',
        createdAt: new Date().toISOString(),
      };
      
      decisionLogs.unshift(blockedDecision);
      return blockedDecision;
    }

    // 6. Liquidity Sweeps (FVGs disabled per strategy §7)
    const liqResult = LiquidityValidator.validate(
      outcome,
      indicators.liquiditySweeps || [],
      [] // FVGs completely ignored
    );
    for (const code of liqResult.reasonCodes) {
      if (!reasonCodes.includes(code)) reasonCodes.push(code);
    }

    // Calculate Entry, SL per Strategy §11 (Order Block Width Rule)
    let entryPrice = currentPrice;
    let stopLossPrice = currentPrice;
    // Removed slDistance

    if (activeZone) {
      const rawWidth = Math.max(0.0001, activeZone.upperPrice - activeZone.lowerPrice);
      // Width % = ((Upper - Lower) / Upper) × 100
      const widthPercent = (rawWidth / Math.max(0.0001, activeZone.upperPrice)) * 100;
      
      if (outcome === StrategySignalOutcome.BUY) {
        // Bullish OB: enter at upper edge if narrow, or 25% inside if wide
        entryPrice = widthPercent <= 0.6 ? activeZone.upperPrice : activeZone.upperPrice - 0.25 * rawWidth;
        stopLossPrice = activeZone.lowerPrice;
      } else {
        // Bearish OB: enter at lower edge if narrow, or 25% inside if wide
        entryPrice = widthPercent <= 0.6 ? activeZone.lowerPrice : activeZone.lowerPrice + 0.25 * rawWidth;
        stopLossPrice = activeZone.upperPrice;
      }
      // _slDistance removed
    } else {
      // No zone — reject. Strategy §9: Trade ONLY Order Blocks.
      const noZoneDecision: DecisionDto = {
        id: `DEC-${Date.now()}`,
        symbol,
        timeframe,
        state: DecisionState.SKIP,
        outcome,
        entryPrice: currentPrice,
        stopLossPrice: currentPrice,
        takeProfitPrice: currentPrice,
        positionSize: 0,
        leverage: 0,
        riskPercent: 0,
        confidenceScore: 0,
        reasonCodes: ['NO_ACTIVE_ZONE' as any, ...reasonCodes],
        inputSnapshotHash: '',
        createdAt: new Date().toISOString(),
      };
      decisionLogs.unshift(noZoneDecision);
      return noZoneDecision;
    }

    entryPrice = Number(entryPrice.toFixed(4));
    stopLossPrice = Number(stopLossPrice.toFixed(4));

    // 7. Position Sizing — 35% risk, max 100x leverage
    const sizingResult = PositionSizingEngine.calculatePositionSize({
      symbol,
      accountBalance,
      entryPrice,
      stopLossPrice,
      takeProfitPrice: entryPrice, // Temporary, will be ignored
      riskPercent: 35.0,
      maxLeverageCap: 100,
    } as any);
    reasonCodes.push(DecisionReasonCode.POSITION_SIZE_CALCULATED);

    // Calculate TP based on 60% account profit target (Strategy §19)
    let takeProfitPrice = sizingResult.takeProfitPrice || currentPrice;

    // 8. Risk Validator — Strategy §16-18: 35% risk, 100% balance, max 100x leverage
    const riskResult = RiskValidator.validate({
      entryPrice,
      stopLossPrice,
      takeProfitPrice,
      accountBalance,
      availableMargin,
      estimatedMarginRequired: accountBalance, // 100% margin utilization
      openPositionCount,
      maxOpenPositions: 1, // Strict 1 trade max (Strategy §15)
    });
    for (const code of riskResult.reasonCodes) {
      if (!reasonCodes.includes(code)) reasonCodes.push(code);
    }

    // 9. AI Confirmation — minimum 85% confidence (Strategy §20)
    const aiResult = AIDecisionCenterService.confirmDecision({
      symbol,
      timeframe,
      outcome,
      activeZone,
      indicators,
      riskRewardRatio: riskResult.riskRewardRatio,
      sessionAllowed: sessionResult.allowed,
      marketAllowed: marketResult.allowed,
    });
    for (const code of aiResult.reasonCodes) {
      if (!reasonCodes.includes(code)) reasonCodes.push(code);
    }

    // 10. Anti-Duplication and One-Trade-Max Check (Strategy §12, §15)
    const dedupResult = SignalDeduplicationEngine.checkDuplication({
      symbol,
      timeframe,
      candleTimestamp,
      zoneId: activeZone?.id,
      hasOpenPosition,
    });
    if (!dedupResult.allowed && dedupResult.reasonCode && !reasonCodes.includes(dedupResult.reasonCode)) {
      reasonCodes.push(dedupResult.reasonCode);
    }

    // Build Reproducibility Hash
    const inputSnapshotPayload = JSON.stringify({
      symbol,
      timeframe,
      currentPrice,
      outcome,
      sessionAllowed: sessionResult.allowed,
      marketRegime: marketResult.marketRegime,
      zoneId: activeZone?.id,
      riskRewardRatio: riskResult.riskRewardRatio,
      confidenceScore: aiResult.confidenceScore,
      candleTimestamp,
    });
    const inputSnapshotHash = crypto.createHash('sha256').update(inputSnapshotPayload).digest('hex');

    // Final Decision State
    let decisionState = DecisionState.WAIT;

    if (
      !sessionResult.allowed ||
      !marketResult.allowed ||
      !dedupResult.allowed ||
      !riskResult.passed ||
      !zoneResult.passed
    ) {
      decisionState = DecisionState.SKIP;
    } else if (aiResult.approved && aiResult.confidenceScore >= 85) {
      decisionState = 'APPROVED' as any;
    } else {
      decisionState = 'REJECTED' as any;
    }

    const decision: DecisionDto = {
      id: `DEC-${Date.now()}`,
      symbol,
      timeframe,
      state: decisionState,
      outcome,
      entryPrice,
      stopLossPrice,
      takeProfitPrice,
      positionSize: sizingResult.positionSize,
      leverage: sizingResult.leverage,
      riskPercent: 35.0,
      confidenceScore: aiResult.confidenceScore,
      reasonCodes,
      inputSnapshotHash,
      createdAt: new Date().toISOString(),
    };

    const isExecuted = (decisionState as string) === 'APPROVED';
    const widthPct = activeZone
      ? Number((((activeZone.upperPrice - activeZone.lowerPrice) / Math.max(0.0001, activeZone.upperPrice)) * 100).toFixed(3))
      : 0;

    logger.info(`[STRATEGY]
Symbol: ${symbol}
Timeframe: 1H
OB: ${activeZone?.id || 'NONE'}
Type: ${activeZone?.type || 'UNKNOWN'}
Live Price: ${currentPrice}
OB Range: ${activeZone ? `${activeZone.upperPrice}–${activeZone.lowerPrice}` : 'N/A'}
Width: ${widthPct}%
Touch: FIRST_TOUCH
Market: ${marketResult.marketRegime}
News: ${reasonCodes.includes('NEWS_FILTER_BLOCKING' as any) ? 'BLOCKED' : 'ALLOWED'}
Confidence: ${aiResult.confidenceScore}
Entry: ${entryPrice}
SL: ${stopLossPrice}
TP: ${takeProfitPrice}
Leverage: ${sizingResult.leverage}x
Account: $${accountBalance}
Risk: 35%
Decision: ${isExecuted ? 'EXECUTE' : 'REJECT'}
Reason: ${isExecuted ? 'STRATEGY_RULES_PASSED' : reasonCodes.join(', ')}`);

    decisionLogs.unshift(decision);
    if (decisionLogs.length > 1000) decisionLogs.pop();
    
    return decision;
  }
}
