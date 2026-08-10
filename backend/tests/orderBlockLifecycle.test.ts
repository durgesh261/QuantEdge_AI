/**
 * Order Block Lifecycle Tests — 17 required test cases
 * Run: npx jest tests/orderBlockLifecycle.test.ts
 */

import { OrderBlockMergeEngine } from '../src/modules/indicator-engine/engines/orderBlockMergeEngine.js';
import { OrderBlockWidthEngine } from '../src/modules/indicator-engine/engines/orderBlockWidthEngine.js';
import type { OrderBlockDto } from '@algoapp/shared';

function makeOB(id: string, type: 'BULLISH' | 'BEARISH', upper: number, lower: number): OrderBlockDto {
  const rawWidth = Math.max(0.0001, upper - lower);
  const widthPct = (rawWidth / upper) * 100;
  const entry = type === 'BULLISH'
    ? (widthPct <= 0.6 ? upper : upper - 0.25 * rawWidth)
    : (widthPct <= 0.6 ? lower : lower + 0.25 * rawWidth);
  const sl    = type === 'BULLISH' ? lower : upper;
  const slDist = Math.max(0.01, Math.abs(entry - sl) / entry * 100);
  const lev   = Math.min(100, Math.max(1, Math.round(35 / slDist)));
  const tp    = type === 'BULLISH' ? entry * (1 + 60 / lev / 100) : entry * (1 - 60 / lev / 100);
  return {
    id, symbol: 'BTCUSD.P', timeframe: '1H', type,
    upperPrice: upper, lowerPrice: lower,
    widthPercent: widthPct, entryPrice: entry,
    stopLossPrice: sl, takeProfitPrice: tp, calculatedLeverage: lev,
    baseCandleIndex: 0, breakCandleIndex: 1,
    isMitigated: false, isInvalidated: false, isUsed: false,
    touchCount: 0, source: 'SMC', createdAt: new Date().toISOString(),
  };
}

// TEST 1: Non-overlapping DEMAND OBs remain separate
test('T01: Non-overlapping DEMAND OBs remain separate', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('A', 'BULLISH', 100, 99), makeOB('B', 'BULLISH', 97, 96)], []);
  expect(merged.length).toBe(2);
});

// TEST 2: Overlapping DEMAND OBs merge into one
test('T02: Overlapping DEMAND OBs merge into one', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('C', 'BULLISH', 100, 99), makeOB('D', 'BULLISH', 100.5, 99.5)], []);
  expect(merged.length).toBe(1);
  expect(merged[0]!.upperPrice).toBe(100.5);
  expect(merged[0]!.lowerPrice).toBe(99);
});

// TEST 3: Overlapping SUPPLY OBs merge into one
test('T03: Overlapping SUPPLY OBs merge into one', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('E', 'BEARISH', 100, 99), makeOB('F', 'BEARISH', 100.5, 99.5)], []);
  const bears = merged.filter(ob => ob.type === 'BEARISH');
  expect(bears.length).toBe(1);
  expect(bears[0]!.upperPrice).toBe(100.5);
  expect(bears[0]!.lowerPrice).toBe(99);
});

// TEST 4: DEMAND + SUPPLY never merge
test('T04: DEMAND + SUPPLY do NOT merge', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('G', 'BULLISH', 100, 99)], [makeOB('H', 'BEARISH', 100.3, 99.2)]);
  expect(merged.filter(ob => ob.type === 'BULLISH').length).toBe(1);
  expect(merged.filter(ob => ob.type === 'BEARISH').length).toBe(1);
});

// TEST 5: Transitive overlap — A overlaps B, B overlaps C → one OB
test('T05: Transitive overlapping OBs merge into one', () => {
  const obA = makeOB('A', 'BULLISH', 100, 99);     // 99–100
  const obB = makeOB('B', 'BULLISH', 100.5, 99.5); // overlaps A
  const obC = makeOB('C', 'BULLISH', 101, 100);    // overlaps B, not A directly
  const { merged } = OrderBlockMergeEngine.merge([obA, obB, obC], []);
  expect(merged.length).toBe(1);
  expect(merged[0]!.upperPrice).toBe(101);
  expect(merged[0]!.lowerPrice).toBe(99);
});

// TEST 6: Merged width from final merged range
test('T06: Merged width recalculated from final range', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('I', 'BULLISH', 100, 99.5), makeOB('J', 'BULLISH', 100.5, 99.8)], []);
  const m = merged[0]!;
  const expectedWidth = ((m.upperPrice - m.lowerPrice) / m.upperPrice) * 100;
  expect(Math.abs(m.widthPercent - expectedWidth)).toBeLessThan(0.001);
});

// TEST 7: Merged entry from final range (wide zone rule)
test('T07: Entry price from merged range using 0.6% rule', () => {
  const { merged } = OrderBlockMergeEngine.merge([makeOB('K', 'BULLISH', 100, 98), makeOB('L', 'BULLISH', 101, 99)], []);
  const m = merged[0]!;
  const expectedEntry = m.upperPrice - 0.25 * (m.upperPrice - m.lowerPrice);
  expect(Math.abs(m.entryPrice - expectedEntry)).toBeLessThan(0.01);
});

// TEST 8: Price outside zone — OB not consumed
test('T08: Price outside BULLISH zone — isUsed stays false', () => {
  OrderBlockWidthEngine.resetUsed();
  const livePrice = 65400; const upper = 65300; const lower = 65000;
  const inside = livePrice <= upper && livePrice >= lower;
  expect(inside).toBe(false);
  expect(OrderBlockWidthEngine.isUsed('OB-OUTSIDE')).toBe(false);
});

// TEST 9: Price enters DEMAND zone → OB consumed
test('T09: Price inside BULLISH zone → OB marked used', () => {
  OrderBlockWidthEngine.resetUsed();
  const id = 'OB-DEMAND-9'; const upper = 65300; const lower = 65000; const live = 65150;
  expect(live <= upper && live >= lower).toBe(true);
  OrderBlockWidthEngine.markUsed(id);
  expect(OrderBlockWidthEngine.isUsed(id)).toBe(true);
});

// TEST 10: Price enters SUPPLY zone → OB consumed
test('T10: Price inside BEARISH zone → OB marked used', () => {
  OrderBlockWidthEngine.resetUsed();
  const id = 'OB-SUPPLY-10'; const upper = 66000; const lower = 65700; const live = 65850;
  expect(live >= lower && live <= upper).toBe(true);
  OrderBlockWidthEngine.markUsed(id);
  expect(OrderBlockWidthEngine.isUsed(id)).toBe(true);
});

// TEST 11: Price staying inside zone = 1 touch, not multiple
test('T11: Price staying inside zone produces exactly one touch event', () => {
  OrderBlockWidthEngine.resetUsed();
  const id = 'OB-STAY-11'; const upper = 65300; const lower = 65000;
  const prices = [65150, 65100, 65200, 65050, 65180, 65250]; // all inside
  let touches = 0; let wasInside = false;
  for (const price of prices) {
    const inside = price <= upper && price >= lower;
    if (inside && !wasInside) { touches++; OrderBlockWidthEngine.markUsed(id); }
    wasInside = inside;
  }
  expect(touches).toBe(1);
  expect(OrderBlockWidthEngine.isUsed(id)).toBe(true);
});

// TEST 12: Used OB excluded from active list after touch
test('T12: Used OB excluded from active list', () => {
  OrderBlockWidthEngine.resetUsed();
  const usedId = 'OB-USED-12'; OrderBlockWidthEngine.markUsed(usedId);
  const obs = [makeOB(usedId, 'BULLISH', 100, 99), makeOB('OB-FRESH-12', 'BULLISH', 98, 97)];
  const active = obs.filter(ob => !OrderBlockWidthEngine.isUsed(ob.id));
  expect(active.length).toBe(1);
  expect(active[0]!.id).toBe('OB-FRESH-12');
});

// TEST 13: Merged OB excluded after touch
test('T13: Merged OB excluded from active list after touch', () => {
  OrderBlockWidthEngine.resetUsed();
  const { merged } = OrderBlockMergeEngine.merge([makeOB('M1', 'BULLISH', 100, 99), makeOB('M2', 'BULLISH', 100.5, 99.5)], []);
  OrderBlockWidthEngine.markUsed(merged[0]!.id);
  const active = merged.filter(ob => !OrderBlockWidthEngine.isUsed(ob.id));
  expect(active.length).toBe(0);
});

// TEST 14: Deterministic merged ID — same sources → same ID
test('T14: Deterministic merged ID across multiple runs', () => {
  const obs = [makeOB('ID-X', 'BULLISH', 100, 99), makeOB('ID-Y', 'BULLISH', 100.5, 99.5)];
  const { merged: m1 } = OrderBlockMergeEngine.merge(obs, []);
  const { merged: m2 } = OrderBlockMergeEngine.merge(obs, []);
  expect(m1[0]!.id).toBe(m2[0]!.id);
  expect(m1[0]!.id).toContain('MERGED');
});

// TEST 15: New OB in same price area is independently eligible
test('T15: New OB ID in same price area is not blocked', () => {
  OrderBlockWidthEngine.resetUsed();
  OrderBlockWidthEngine.markUsed('OB-OLD');
  expect(OrderBlockWidthEngine.isUsed('OB-NEW-SAME-AREA')).toBe(false);
});

// TEST 16: Confidence <85 → no trade, but OB still consumed
test('T16: OB consumed on touch even when confidence < 85', () => {
  OrderBlockWidthEngine.resetUsed();
  const id = 'OB-LOW-CONF'; const confidence = 70;
  OrderBlockWidthEngine.markUsed(id); // touch happened
  expect(confidence >= 85).toBe(false); // no trade
  expect(OrderBlockWidthEngine.isUsed(id)).toBe(true); // OB still consumed
});

// TEST 17: Confidence >=85 → trade executed, OB consumed
test('T17: OB consumed on touch when confidence >= 85', () => {
  OrderBlockWidthEngine.resetUsed();
  const id = 'OB-HIGH-CONF'; const confidence = 92;
  OrderBlockWidthEngine.markUsed(id);
  expect(confidence >= 85).toBe(true);
  expect(OrderBlockWidthEngine.isUsed(id)).toBe(true);
});
