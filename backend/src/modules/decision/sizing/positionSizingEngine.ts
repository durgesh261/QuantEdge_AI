import { PositionSizingResultDto } from '@algoapp/shared';
import { DynamicRiskLeverageService } from '../../live-trading/services/DynamicRiskLeverageService.js';

export interface PositionSizingInput {
  symbol: string;
  accountBalance: number;
  entryPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  riskPercent?: number | undefined; // e.g. 1.0 = 1%
  maxLeverageCap?: number | undefined; // e.g. 10 or 25
}

export class PositionSizingEngine {
  /**
   * Deterministically calculates exact institutional position size and margin requirements.
   */
  public static calculatePositionSize(input: PositionSizingInput): PositionSizingResultDto {
    const direction = input.takeProfitPrice > input.entryPrice ? 'BUY' : 'SELL';
    
    const riskResult = DynamicRiskLeverageService.calculateRiskAndLeverage({
      accountBalance: input.accountBalance,
      entryPrice: input.entryPrice,
      stopLossPrice: input.stopLossPrice,
      direction,
      riskPercent: input.riskPercent,
    });

    return {
      positionSize: riskResult.notionalValue,
      contractQuantity: riskResult.positionSize,
      notionalValue: riskResult.notionalValue,
      riskAmount: riskResult.riskAmount,
      marginRequired: riskResult.marginRequired,
      leverage: riskResult.leverage,
      entryPrice: riskResult.entryPrice,
      stopLossPrice: riskResult.stopLossPrice,
      takeProfitPrice: riskResult.takeProfitPrice,
    };
  }
}
