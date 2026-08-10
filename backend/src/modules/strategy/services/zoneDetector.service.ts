// backend/src/modules/strategy/services/zoneDetector.service.ts
import { CandleDto, TradingTimeframe, ZoneDto } from '@algoapp/shared';
import { IndicatorEngineService } from '../../indicator-engine/services/indicatorEngine.service.js';

export class ZoneDetectorService {
  public static async detectZones(
    symbol: string = 'BTCUSD.P',
    timeframe: TradingTimeframe = '1H',
    candles?: CandleDto[]
  ): Promise<ZoneDto[]> {
    if (!candles || candles.length === 0) return [];
    const output = IndicatorEngineService.computeIndicators(candles, timeframe, symbol);
    const raw = [...output.supplyZones, ...output.demandZones];
    return raw.map((z) => ({
      ...z,
      strength: (z as any).mergedStrength ?? 90.0,
    })) as ZoneDto[];
  }

  public static async getZones(
    symbol: string = 'BTCUSD.P',
    timeframe: TradingTimeframe = '1H',
    candles?: CandleDto[]
  ): Promise<ZoneDto[]> {
    return this.detectZones(symbol, timeframe, candles);
  }
}
