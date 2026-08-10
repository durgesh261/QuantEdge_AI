import { logger } from '../../../logger/index.js';

export interface OrderBlock {
  id: string;
  symbol: string;
  type: 'DEMAND' | 'SUPPLY';
  priceLow: number;
  priceHigh: number;
  strength: number;        // 0-100
  touches: number;         // How many times price retested
  freshness: number;       // 0-100% (100% = untested)
  volume: number;
  createdAt: string;
  timeframe: string;
  isActive: boolean;
  aiScore: number;         // 9-Factor AI Gate score
  factors: {
    volumeImbalance: boolean;
    fairValueGap: boolean;
    breakerBlock: boolean;
    mitigation: boolean;
    institutionalCandle: boolean;
    liquiditySweep: boolean;
    premiumDiscount: boolean;
    orderFlowDelta: boolean;
    timeAlignment: boolean;
  };
}

export class OrderBlockService {
  private static orderBlocks: Map<string, OrderBlock[]> = new Map();

  /**
   * Detect order blocks from OHLCV + Volume data
   * This integrates with your existing scanner pipeline
   */
  static detectBlocks(symbol: string, candles: any[]): OrderBlock[] {
    const blocks: OrderBlock[] = [];
    
    if (!candles || candles.length < 20) return blocks;

    // Find swing lows for Demand zones
    for (let i = 3; i < candles.length - 3; i++) {
      const current = candles[i];
      const prev = candles[i - 1];
      const next = candles[i + 1];

      // Demand Block: Bullish engulfing / strong impulse after consolidation
      if (this.isDemandBlock(prev, current, next)) {
        const block = this.createBlock(symbol, current, prev, candles.slice(0, i), 'DEMAND');
        if (block.aiScore >= 85) blocks.push(block); // 9-Factor Gate
      }

      // Supply Block: Bearish engulfing / strong down impulse
      if (this.isSupplyBlock(prev, current, next)) {
        const block = this.createBlock(symbol, current, prev, candles.slice(0, i), 'SUPPLY');
        if (block.aiScore >= 85) blocks.push(block);
      }
    }

    // Keep only last 10 active blocks per symbol
    const active = blocks.filter(b => b.isActive).slice(0, 10);
    this.orderBlocks.set(symbol, active);
    return active;
  }

  private static isDemandBlock(prev: any, current: any, next: any): boolean {
    // Strong bullish candle after bearish consolidation
    const isBullish = current.close > current.open;
    const bodySize = Math.abs(current.close - current.open);
    const prevBody = Math.abs(prev.close - prev.open);
    const isStrong = bodySize > prevBody * 1.5 && (current.close - current.low) > bodySize * 0.3;
    return isBullish && isStrong && next.close > current.close;
  }

  private static isSupplyBlock(prev: any, current: any, next: any): boolean {
    // Strong bearish candle after bullish consolidation
    const isBearish = current.close < current.open;
    const bodySize = Math.abs(current.close - current.open);
    const prevBody = Math.abs(prev.close - prev.open);
    const isStrong = bodySize > prevBody * 1.5 && (current.high - current.close) > bodySize * 0.3;
    return isBearish && isStrong && next.close < current.close;
  }

  private static createBlock(
    symbol: string, 
    candle: any, 
    prevCandle: any, 
    history: any[], 
    type: 'DEMAND' | 'SUPPLY'
  ): OrderBlock {
    const priceLow = type === 'DEMAND' ? Math.min(prevCandle.low, candle.open) : Math.min(candle.close, prevCandle.close);
    const priceHigh = type === 'DEMAND' ? Math.max(candle.close, prevCandle.high) : Math.max(candle.open, prevCandle.high);
    
    // Calculate touches (how many times price came back to this zone)
    const touches = history.filter(c => c.low <= priceHigh && c.high >= priceLow).length;
    
    // Freshness: 100% if never touched, decreases with each touch
    const freshness = Math.max(0, 100 - touches * 15);

    // 9-Factor AI Scoring
    const factors = {
      volumeImbalance: candle.volume > prevCandle.volume * 1.3,
      fairValueGap: Math.abs(candle.close - prevCandle.open) > Math.abs(prevCandle.close - prevCandle.open) * 0.5,
      breakerBlock: touches > 0,
      mitigation: touches > 0 && history[history.length - 1]?.close > priceLow,
      institutionalCandle: candle.volume > this.avgVolume(history.slice(-20)) * 2,
      liquiditySweep: type === 'DEMAND' ? candle.low < prevCandle.low : candle.high > prevCandle.high,
      premiumDiscount: type === 'DEMAND' ? priceLow < this.poc(history) : priceHigh > this.poc(history),
      orderFlowDelta: (candle.close > candle.open) === (type === 'DEMAND'),
      timeAlignment: new Date(candle.time).getMinutes() % 15 === 0, // Aligned to 15m
    };

    const aiScore = Object.values(factors).filter(Boolean).length * 11 + 1; // ~1-100 scale

    return {
      id: `ob-${symbol}-${candle.time}-${type}`,
      symbol,
      type,
      priceLow,
      priceHigh,
      strength: Math.min(100, aiScore + touches * 2),
      touches,
      freshness,
      volume: candle.volume,
      createdAt: new Date(candle.time).toISOString(),
      timeframe: '1H',
      isActive: freshness > 20,
      aiScore,
      factors,
    };
  }

  private static avgVolume(candles: any[]): number {
    if (!candles.length) return 1;
    return candles.reduce((sum, c) => sum + (c.volume || 0), 0) / candles.length;
  }

  private static poc(candles: any[]): number {
    // Point of Control approximation (most traded price)
    if (!candles.length) return 0;
    return candles.reduce((sum, c) => sum + ((c.high + c.low) / 2), 0) / candles.length;
  }

  static setBlocks(symbol: string, blocks: OrderBlock[]): void {
    this.orderBlocks.set(symbol, blocks);
  }

  static syncFromIndicators(symbol: string, rawOBs: any[], zoneScores: any = {}): OrderBlock[] {
    const formatted: OrderBlock[] = (rawOBs || []).map((ob: any, idx: number) => {
      const isDemand = ob.type === 'DEMAND' || ob.type === 'BULLISH' || ob.type === 'DEMAND_ZONE';
      const low = ob.lowerPrice ?? ob.priceLow ?? ob.low ?? ob.bottom ?? 0;
      const high = ob.upperPrice ?? ob.priceHigh ?? ob.high ?? ob.top ?? 0;
      const score = zoneScores[`ZONE-SUP-${ob.id}`]?.totalScore
                 ?? zoneScores[`ZONE-DEM-${ob.id}`]?.totalScore
                 ?? ob.aiScore
                 ?? ob.score
                 ?? 78;

      return {
        id: ob.id || `ob-${symbol}-${idx}`,
        symbol,
        type: isDemand ? 'DEMAND' : 'SUPPLY',
        priceLow: Number(low),
        priceHigh: Number(high),
        strength: ob.strength ?? 80,
        touches: ob.touches ?? 0,
        freshness: ob.freshness ?? 100,
        volume: ob.volume ?? 0,
        createdAt: ob.createdAt || new Date().toISOString(),
        timeframe: ob.timeframe || '1H',
        isActive: true,
        aiScore: score,
        factors: ob.factors || {
          volumeImbalance: true,
          fairValueGap: true,
          breakerBlock: false,
          mitigation: false,
          institutionalCandle: true,
          liquiditySweep: true,
          premiumDiscount: true,
          orderFlowDelta: true,
          timeAlignment: true,
        },
      };
    });

    this.orderBlocks.set(symbol, formatted);
    return formatted;
  }

  static getBlocksForSymbol(symbol: string): OrderBlock[] {
    return this.orderBlocks.get(symbol) || [];
  }

  static getAllBlocks(): OrderBlock[] {
    return Array.from(this.orderBlocks.values()).flat();
  }

  /**
   * Called by your scanner pipeline every tick/candle close
   */
  static async updateFromScanner(symbol: string, candleData: any[]) {
    logger.info(`[OrderBlockService] Scanning ${symbol} — ${candleData.length} candles`);
    const blocks = this.detectBlocks(symbol, candleData);
    logger.info(`[OrderBlockService] Found ${blocks.length} blocks for ${symbol}`);
    return blocks;
  }
}
