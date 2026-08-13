#!/usr/bin/env node
// Validation Diagnostic Tool with Historical Backfill
// 1. Fetches 30-day 1H candles from Delta Exchange (with pagination)
// 2. Runs LuxAlgoSMCEngine on real data
// 3. Runs OrderBlockMergeEngine
// 4. Outputs JSON + console comparison for TradingView validation

import { IndicatorEngineService } from '../modules/indicator-engine/services/indicatorEngine.service.js';
import { CandleStoreService } from '../modules/market-data/services/candleStore.service.js';
import { OrderBlockMergeEngine } from '../modules/indicator-engine/engines/orderBlockMergeEngine.js';
import { HistoricalBackfillService } from '../modules/market-data/services/historicalBackfill.service.js';
import { DeltaRestClient } from '../modules/delta-exchange/services/DeltaRestClient.js';
import { prisma } from '../db.js';
import * as fs from 'fs';
import * as path from 'path';

const SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
const TIMEFRAME = '1H';
const CANDLE_COUNT = 720; // 30 days * 24 hours

interface ValidationOutput {
  metadata: {
    timestamp: string;
    symbols: string[];
    timeframe: string;
    candleCount: number;
  };
  results: SymbolResult[];
}

interface SymbolResult {
  symbol: string;
  timeframe: string;
  candleCount: number;
  dateRange: { from: string; to: string };
  orderBlocks: OrderBlockInfo[];
  mergedZones: MergedZoneInfo[];
  structureEvents: StructureEventInfo[];
  pivots: PivotInfo[];
  trends: { swing: string; internal: string };
  atr200: number;
}

interface OrderBlockInfo {
  id: string;
  type: 'BULLISH' | 'BEARISH';
  direction: 'DEMAND' | 'SUPPLY';
  upperPrice: number;
  lowerPrice: number;
  width: number;
  widthPercent: number;
  baseCandleIndex: number;
  breakCandleIndex: number;
  createdAt: string;
  isMitigated: boolean;
  isUsed: boolean;
  touchCount: number;
  source: string;
}

interface MergedZoneInfo {
  id: string;
  type: 'BULLISH' | 'BEARISH';
  direction: 'DEMAND' | 'SUPPLY';
  upperPrice: number;
  lowerPrice: number;
  width: number;
  widthPercent: number;
  sourceIds: string[];
  sourceCount: number;
  createdAt: string;
  isMerged: boolean;
}

interface StructureEventInfo {
  index: number;
  time: string;
  type: 'BOS' | 'CHOCH';
  direction: 'BULLISH' | 'BEARISH';
  brokenLevel: number;
  isInternal: boolean;
}

interface PivotInfo {
  index: number;
  time: string;
  price: number;
  type: 'HIGH' | 'LOW';
  length: number;
  isSwing: boolean;
  confirmedAtIndex: number;
}

async function ensureHistoricalData(): Promise<void> {
  console.log('\n[Validation] Ensuring 30-day historical data from Delta Exchange...');
  
  // Create a REST client with credentials from env
  const restClient = new DeltaRestClient({
    apiKey: process.env.DELTA_API_KEY || '',
    apiSecret: process.env.DELTA_API_SECRET || '',
  });
  
  // Load products first
  await restClient.loadProducts();
  
  // Run backfill
  await HistoricalBackfillService.backfillAll(restClient);
}

async function validateSymbol(symbol: string): Promise<SymbolResult | null> {
  console.log(`\n[Validation] Processing ${symbol}...`);
  
  // Get candles from store (DB + live)
  const candles = await CandleStoreService.getCandles(symbol, TIMEFRAME, CANDLE_COUNT);
  
  if (candles.length < 10) {
    console.warn(`[Validation] Insufficient candles for ${symbol}: ${candles.length}`);
    return null;
  }

  console.log(`[Validation] Got ${candles.length} candles for ${symbol}`);
  console.log(`[Validation] Date range: ${candles[0]?.timestamp ?? 'N/A'} to ${candles[candles.length - 1]?.timestamp ?? 'N/A'}`);

  // Run indicator engine
  const indicatorResult = IndicatorEngineService.computeIndicators(candles, TIMEFRAME, symbol);
  
  // Run merge engine on all order blocks
  const allOBs = [...(indicatorResult.orderBlocks || [])];
  const demandOBs = allOBs.filter(ob => ob.type === 'BULLISH');
  const supplyOBs = allOBs.filter(ob => ob.type === 'BEARISH');
  const mergeResult = OrderBlockMergeEngine.merge(demandOBs, supplyOBs);
  const merged = mergeResult.merged;

  // Format order blocks
  const orderBlocks: OrderBlockInfo[] = allOBs.map(ob => ({
    id: ob.id,
    type: ob.type,
    direction: ob.type === 'BULLISH' ? 'DEMAND' : 'SUPPLY',
    upperPrice: ob.upperPrice,
    lowerPrice: ob.lowerPrice,
    width: Number((ob.upperPrice - ob.lowerPrice).toFixed(4)),
    widthPercent: ob.widthPercent,
    baseCandleIndex: ob.baseCandleIndex,
    breakCandleIndex: ob.breakCandleIndex,
    createdAt: ob.createdAt,
    isMitigated: ob.isMitigated,
    isUsed: ob.isUsed,
    touchCount: ob.touchCount,
    source: ob.source,
  }));

  // Format merged zones
  const mergedZones: MergedZoneInfo[] = merged.map(m => ({
    id: m.id,
    type: m.type,
    direction: m.type === 'BULLISH' ? 'DEMAND' : 'SUPPLY',
    upperPrice: m.upperPrice,
    lowerPrice: m.lowerPrice,
    width: Number((m.upperPrice - m.lowerPrice).toFixed(4)),
    widthPercent: m.widthPercent,
    sourceIds: (m as any).sourceIds || [m.id],
    sourceCount: ((m as any).sourceIds || [m.id]).length,
    createdAt: m.createdAt,
    isMerged: (m as any).isMerged || false,
  }));

  // Format structure events
  const structureEvents: StructureEventInfo[] = (indicatorResult.structureEvents || []).map(e => ({
    index: e.index,
    time: e.time,
    type: e.type,
    direction: e.direction,
    brokenLevel: e.brokenLevel,
    isInternal: e.isInternal,
  }));

  // Format pivots
  const pivots: PivotInfo[] = [...(indicatorResult.pivotsSwing || []), ...(indicatorResult.pivotsInternal || [])].map(p => ({
    index: p.index,
    time: p.time,
    price: p.price,
    type: p.type,
    length: p.length,
    isSwing: p.isSwing,
    confirmedAtIndex: p.confirmedAtIndex,
  }));

  return {
    symbol,
    timeframe: TIMEFRAME,
    candleCount: candles.length,
    dateRange: {
      from: candles[0]?.timestamp ?? '',
      to: candles[candles.length - 1]?.timestamp ?? '',
    },
    orderBlocks,
    mergedZones,
    structureEvents,
    pivots,
    trends: {
      swing: indicatorResult.marketStructure.swingTrend,
      internal: indicatorResult.marketStructure.internalTrend,
    },
    atr200: indicatorResult.atr200 ?? 0,
  };
}

function printConsoleSummary(results: SymbolResult[]): void {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('VALIDATION SUMMARY — CONSOLE OUTPUT');
  console.log('═══════════════════════════════════════════════════════════════\n');

  for (const r of results) {
    console.log(`┌─ ${r.symbol} (${r.timeframe}) ─────────────────────────────────────`);
    console.log(`│ Candles: ${r.candleCount} | Range: ${r.dateRange.from.split('T')[0]} to ${r.dateRange.to.split('T')[0]}`);
    console.log(`│ ATR(200): ${r.atr200.toFixed(2)} | Swing Trend: ${r.trends.swing} | Internal Trend: ${r.trends.internal}`);
    console.log(`│ Structure Events: ${r.structureEvents.length} | Pivots: ${r.pivots.length}`);
    console.log(`│ Order Blocks: ${r.orderBlocks.length} | Merged Zones: ${r.mergedZones.length}`);
    console.log(`│`);
    
    // Print order blocks table
    if (r.orderBlocks.length > 0) {
      console.log(`│ ORDER BLOCKS:`);
      for (const ob of r.orderBlocks) {
        const status = ob.isMitigated ? 'MITIGATED' : (ob.isUsed ? 'USED' : 'ACTIVE');
        console.log(`│   ${ob.direction} | ${ob.upperPrice.toFixed(2)}-${ob.lowerPrice.toFixed(2)} | ${ob.widthPercent.toFixed(3)}% | base:${ob.baseCandleIndex} break:${ob.breakCandleIndex} | ${status} | ${ob.createdAt.split('T')[0]}`);
      }
    } else {
      console.log(`│ ORDER BLOCKS: (none)`);
    }
    
    // Print merged zones
    if (r.mergedZones.length > 0) {
      console.log(`│ MERGED ZONES:`);
      for (const mz of r.mergedZones) {
        const srcInfo = mz.isMerged ? `merged from ${mz.sourceCount} OBs` : 'single';
        console.log(`│   ${mz.direction} | ${mz.upperPrice.toFixed(2)}-${mz.lowerPrice.toFixed(2)} | ${mz.widthPercent.toFixed(3)}% | ${srcInfo} | ${mz.createdAt.split('T')[0]}`);
      }
    }
    console.log(`└────────────────────────────────────────────────────────────\n`);
  }

  // Side-by-side comparison helper
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('TRADINGVIEW COMPARISON CHECKLIST');
  console.log('═══════════════════════════════════════════════════════════════\n');
  console.log('For EACH symbol, compare QuantEdge output above with TradingView:');
  console.log('  ✓ Order Block COUNT (should match exactly)');
  console.log('  ✓ Direction: DEMAND (BULLISH) vs SUPPLY (BEARISH)');
  console.log('  ✓ Upper Price (±0.01% tolerance)');
  console.log('  ✓ Lower Price (±0.01% tolerance)');
  console.log('  ✓ Creation Time (same candle)');
  console.log('  ✓ Active/Mitigated/Used status');
  console.log('  ✓ Merged zones: combined price range covers all source OBs\n');
}

async function main(): Promise<void> {
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('QuantEdge AI — Indicator Validation Diagnostic Tool');
  console.log('═══════════════════════════════════════════════════════════════\n');
  console.log(`Symbols: ${SYMBOLS.join(', ')}`);
  console.log(`Timeframe: ${TIMEFRAME}`);
  console.log(`Target Candle Count: ${CANDLE_COUNT} (30 days * 24h)\n`);

  // Step 1: Ensure historical data exists
  await ensureHistoricalData();

  // Step 2: Validate each symbol
  const results: SymbolResult[] = [];

  for (const symbol of SYMBOLS) {
    const result = await validateSymbol(symbol);
    if (result) {
      results.push(result);
    }
  }

  if (results.length === 0) {
    console.error('\n[Validation] No results generated — check Delta connection and data');
    process.exit(1);
  }

  // Print console summary
  printConsoleSummary(results);

  // Write JSON output
  const output: ValidationOutput = {
    metadata: {
      timestamp: new Date().toISOString(),
      symbols: SYMBOLS,
      timeframe: TIMEFRAME,
      candleCount: CANDLE_COUNT,
    },
    results,
  };

  const outputDir = path.resolve(process.cwd(), 'validation-output');
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const outputFile = path.join(outputDir, `validation-${Date.now()}.json`);
  fs.writeFileSync(outputFile, JSON.stringify(output, null, 2));
  
  console.log(`\n[Validation] Full JSON output written to: ${outputFile}`);
  console.log('\n[Validation] Done. Compare with TradingView indicator output.\n');
  
  await prisma.$disconnect();
  process.exit(0);
}

main().catch((err) => {
  console.error('[Validation] Fatal error:', err);
  process.exit(1);
});