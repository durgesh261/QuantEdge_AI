import { OrderBlockWidthEngine } from '../../modules/indicator-engine/engines/orderBlockWidthEngine.js';

function assert(condition: boolean, msg: string) {
  if (!condition) throw new Error("ASSERTION FAILED: " + msg);
}

console.log("Running OrderBlockWidthEngine tests...");

// Test 1: Bullish OB <= 0.6% width (edge entry)
// Upper: 100, Lower: 99.5. Width: 0.5%
let ob1 = OrderBlockWidthEngine.enrichOrderBlock(
  'ob1', 'BTC', '1H', 'BULLISH', 100, 99.5, 0, 0, false, false, 0, 'SMC', ''
);
assert(ob1.widthPercent === 0.5, "Width should be 0.5%");
assert(ob1.entryPrice === 100, "Bullish <=0.6% should enter at upper edge");
assert(ob1.stopLossPrice === 99.5, "Bullish SL should be lower edge");

// Test 2: Bullish OB > 0.6% width (25% deep entry)
// Upper: 100, Lower: 99. Width: 1%
let ob2 = OrderBlockWidthEngine.enrichOrderBlock(
  'ob2', 'BTC', '1H', 'BULLISH', 100, 99, 0, 0, false, false, 0, 'SMC', ''
);
assert(ob2.widthPercent === 1.0, "Width should be 1.0%");
assert(ob2.entryPrice === 99.75, "Bullish >0.6% should enter 25% deep (100 - 0.25)");

// Test 3: Bearish OB <= 0.6% width (edge entry)
// Upper: 100.5, Lower: 100. Width: 0.5% (approx)
let ob3 = OrderBlockWidthEngine.enrichOrderBlock(
  'ob3', 'BTC', '1H', 'BEARISH', 100.5, 100, 0, 0, false, false, 0, 'SMC', ''
);
assert(ob3.entryPrice === 100, "Bearish <=0.6% should enter at lower edge");
assert(ob3.stopLossPrice === 100.5, "Bearish SL should be upper edge");

// Test 4: Bearish OB > 0.6% width (25% deep entry)
// Upper: 101, Lower: 100. Width: 1% (approx)
let ob4 = OrderBlockWidthEngine.enrichOrderBlock(
  'ob4', 'BTC', '1H', 'BEARISH', 101, 100, 0, 0, false, false, 0, 'SMC', ''
);
assert(ob4.entryPrice === 100.25, "Bearish >0.6% should enter 25% deep (100 + 0.25)");

console.log("All tests passed!");
