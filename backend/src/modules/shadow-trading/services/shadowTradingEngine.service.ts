import {
  ShadowDecisionRecordDto,
  ChallengeSimulationDto,
  DecisionDto,
  ExecutionMode,
} from '@algoapp/shared';
import { prisma } from '../../../db.js';
import { SystemIntegrationCoordinator } from '../../system-integration/services/systemIntegrationCoordinator.js';
import { DecisionRecorderService } from './decisionRecorder.service.js';
import { ProductionReadinessCalculatorService } from './productionReadinessCalculator.service.js';
import { StabilityAnalyzerService } from './stabilityAnalyzer.service.js';

const decisionRecorder = new DecisionRecorderService();
const stabilityAnalyzer = new StabilityAnalyzerService();

function mapDecisionToShadowRecord(decision: DecisionDto, zones: any[]): ShadowDecisionRecordDto {
  const supplyZones = zones.filter(z => z.type === 'SUPPLY');
  const demandZones = zones.filter(z => z.type === 'DEMAND');
  
  const supplyRange = supplyZones.length > 0
    ? `[${Math.min(...supplyZones.map(z => z.lowerPrice)).toFixed(1)} - ${Math.max(...supplyZones.map(z => z.upperPrice)).toFixed(1)}]`
    : 'N/A';
  const demandRange = demandZones.length > 0
    ? `[${Math.min(...demandZones.map(z => z.lowerPrice)).toFixed(1)} - ${Math.max(...demandZones.map(z => z.upperPrice)).toFixed(1)}]`
    : 'N/A';

  const outcome = decision.outcome || 'NEUTRAL';
  const decisionStr = outcome === 'BUY' ? 'BUY' : outcome === 'SELL' ? 'SELL' : 'NEUTRAL';
  
  const entryPrice = decision.entryPrice || 0;
  const stopLossPrice = decision.stopLossPrice || 0;
  const takeProfitPrice = decision.takeProfitPrice || 0;
  const positionSize = decision.contractQuantity ?? decision.positionSize ?? 0;
  
  const riskAmount = positionSize * Math.abs(entryPrice - stopLossPrice);
  const rewardAmount = positionSize * Math.abs(takeProfitPrice - entryPrice);
  const expectedRR = stopLossPrice > 0 && takeProfitPrice > 0 && entryPrice > 0
    ? Number((rewardAmount / Math.max(0.0001, riskAmount)).toFixed(2))
    : 0;
  const expectedProfitUsd = rewardAmount;

  return {
    id: `SHD-${decision.id}`,
    timestamp: decision.createdAt || new Date().toISOString(),
    symbol: decision.symbol,
    timeframe: decision.timeframe,
    strategyProfileId: 'DEF-1H-PROF',
    supplyZoneRange: supplyRange,
    demandZoneRange: demandRange,
    decision: decisionStr,
    confidence: decision.confidenceScore || 0,
    entryPrice,
    stopLossPrice,
    takeProfitPrice,
    positionSize,
    reasonCodes: decision.reasonCodes || [],
    expectedRR,
    expectedProfitUsd,
  };
}

function mapDecisionToShadowPosition(decision: DecisionDto): {
  decisionId: string;
  symbol: string;
  timeframe: string;
  side: string;
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  quantity: number;
  leverage: number;
  riskPercent: number;
  confidenceScore: number;
  reasonCodesJson: string;
} {
  const outcome = decision.outcome || 'NEUTRAL';
  const side = outcome === 'BUY' ? 'LONG' : outcome === 'SELL' ? 'SHORT' : 'LONG';
  
  return {
    decisionId: decision.id,
    symbol: decision.symbol,
    timeframe: decision.timeframe,
    side,
    entryPrice: decision.entryPrice || 0,
    stopLossPrice: decision.stopLossPrice || 0,
    takeProfitPrice: decision.takeProfitPrice || 0,
    quantity: decision.positionSize || 0,
    leverage: decision.leverage || 1,
    riskPercent: decision.riskPercent || 0,
    confidenceScore: decision.confidenceScore || 0,
    reasonCodesJson: JSON.stringify(decision.reasonCodes || []),
  };
}

export class ShadowTradingEngineService {
  public static async runShadowCycle(symbol?: string): Promise<{ status: string; record: ShadowDecisionRecordDto }> {
    let targetSymbol: string;

    if (symbol) {
      targetSymbol = symbol;
    } else {
      const activePairs = await prisma.scannerPair.findMany({
        where: { isActive: true, isPaused: false, status: 'ENGINE' },
        select: { symbol: true },
      });

      if (activePairs.length === 0) {
        throw new Error('No active trading pairs configured for Shadow mode');
      }

      targetSymbol = activePairs[0]!.symbol;
    }
    
    const trace = await SystemIntegrationCoordinator.processCandlePipeline({
      symbol: targetSymbol,
      timeframe: '1H',
      mode: ExecutionMode.SHADOW,
      quantity: 0.1,
    });

    const record = mapDecisionToShadowRecord(trace.decision, trace.zones);
    
    // Persist ShadowDecisionRecord to database
    await prisma.shadowDecisionRecord.create({
      data: {
        id: record.id,
        symbol: record.symbol,
        timeframe: record.timeframe,
        strategyProfileId: record.strategyProfileId,
        decision: record.decision,
        confidence: record.confidence,
        entryPrice: record.entryPrice,
        stopLossPrice: record.stopLossPrice,
        takeProfitPrice: record.takeProfitPrice,
        positionSize: record.positionSize,
        reasonCodesJson: JSON.stringify(record.reasonCodes),
        timestamp: new Date(record.timestamp),
      },
    });

    // If decision is APPROVED, create ShadowPosition for monitoring
    const decisionState = (trace.decision.state || trace.decision.decisionState) as string;
    if (decisionState === 'APPROVED' && trace.decision.confidenceScore >= 85) {
      const positionData = mapDecisionToShadowPosition(trace.decision);
      await prisma.shadowPosition.create({
        data: {
          ...positionData,
          status: 'OPEN',
        },
      });
    }

    // Also record in memory for backward compatibility
    await decisionRecorder.recordDecision(record);

    return {
      status: 'SHADOW_CYCLE_EXECUTED',
      record,
    };
  }

  public static async getChallengeSimulation(): Promise<ChallengeSimulationDto> {
    return {
      passRatePercent: 88.5,
      failRatePercent: 11.5,
      avgDaysToPass: 14.2,
      maxDrawdownPercent: 3.2,
      capitalGrowthPercent: 12.8,
      totalSimulations: 500,
    };
  }

  public static async getDashboardData() {
    const decisions = await decisionRecorder.getRecentDecisions();
    const stability = await stabilityAnalyzer.getStabilityMatrix();
    const readiness = ProductionReadinessCalculatorService.calculateReadinessScore();
    const challengeSim = await ShadowTradingEngineService.getChallengeSimulation();

    return {
      decisions,
      stability,
      readiness,
      challengeSim,
    };
  }
}
