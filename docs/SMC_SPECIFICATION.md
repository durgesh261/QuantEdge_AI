# SMC Specification - LuxAlgo Reference Implementation

## Overview

This document specifies the exact Smart Money Concepts (SMC) implementation based on the **LuxAlgo Smart Money Concepts [LuxAlgo]** Pine Script reference.

## Canonical Defaults

| Parameter | Value | Description |
|-----------|-------|-------------|
| Internal Structure Length | 5 | Left/right bars for internal pivots |
| Swing Structure Length | 50 | Left/right bars for swing pivots |
| ATR Period | 200 | Volatility measure period |
| ATR Multiplier | 2.0 | High volatility threshold |

## Volatility Parsing (CRITICAL)

**Reference**: LuxAlgo uses ATR(200) as volatility measure.

```
High Volatility Condition: high - low >= 2 × ATR(200)

If HIGH VOLATILITY:
    parsedHigh = low
    parsedLow = high
    (Inverted - candle range treated as noise)

If NORMAL:
    parsedHigh = high
    parsedLow = low
    (Standard interpretation)
```

**Implementation**: `quantedge.smc.volatility.parse_candles_with_volatility()`

## Structure Detection

### Two Independent Structures

1. **Internal Structure** (length=5)
   - Fast, responsive pivots
   - Short-term trend detection

2. **Swing Structure** (length=50)
   - Slow, major pivots
   - Long-term trend detection

**NEVER merge** internal and swing into one structure series.

### LuxAlgo Stateful Structure Detection

The reference implementation uses a **stateful leg-based model**:

1. **Pivot Formation**: Track unconfirmed highs/lows during formation
2. **Pivot Confirmation**: Require `length` bars on each side (left/right)
3. **Leg Formation**: Confirmed opposite pivots form legs (High→Low = down leg, Low→High = up leg)
3. **Trend State**: Current trend derived from last confirmed leg
4. **Structure Break**: BOS/CHOCH detected when price breaks last confirmed pivot

Key distinction from naive implementations:
- Pivot formation time ≠ confirmation time ≠ break time
- Must wait for right-bars confirmation before pivot is "real"
- Legs are only formed from confirmed pivots
- Structure breaks only checked against confirmed pivots

### Pivot Detection

Pivot High at index i (after confirmation):
```
parsedHigh[i] > parsedHigh[i-left...i-1] AND parsedHigh[i] > parsedHigh[i+1...i+right]
```

Pivot Low at index i (after confirmation):
```
parsedLow[i] < parsedLow[i-left...i-1] AND parsedLow[i] < parsedLow[i+1...i+right]
```

### Trend Determination

- **Bullish**: Higher highs & higher lows (pivot sequence: Low → High → Low → High...)
- **Bearish**: Lower highs & lower lows (pivot sequence: High → Low → High → Low...)
- **Ranging**: Conflicting or unclear structure

### BOS / CHOCH Logic

### Definitions

- **BOS (Break of Structure)**: Price breaks in direction of current trend
- **CHOCH (Change of Character)**: Price breaks against current trend (reversal signal)

### Rules

| Current Trend | Break Direction | Result |
|---------------|-----------------|--------|
| Bearish | Up (breaks pivot high) | CHOCH |
| Bearish | Down (breaks pivot low) | BOS |
| Bullish | Down (breaks pivot low) | CHOCH |
| Bullish | Up (breaks pivot high) | BOS |
| Ranging | Either | BOS (no prior trend) |

**Implementation**: `quantedge.smc.structure.StructureDetector` (stateful streaming)

## Order Block Detection (CRITICAL)

### LuxAlgo OB Logic (NOT "last opposite candle")

**Common Mistake**: "Last bearish candle before bullish move" = WRONG

**Correct LuxAlgo Process**:

1. Detect structure (internal/swing pivots) with confirmation
2. Detect structural break (BOS/CHOCH)
3. Determine bias from break direction
4. Search parsed range using LuxAlgo slice semantics:
   - `parsedLows.slice(pivot_index, break_index)` for bullish
   - `parsedHighs.slice(pivot_index, break_index)` for bearish
5. Select extreme candle:
   - **Bullish break** → Minimum **parsedLow** in range
   - **Bearish break** → Maximum **parsedHigh** in range
6. Create OB from that extreme candle's full range (high to low)

### LuxAlgo Slice Semantics (CRITICAL)

Pine Script `array.slice(from, to)` behavior:
- `from` (inclusive) = pivot index that was broken
- `to` (exclusive) = break candle index
- Range includes pivot candle, excludes break candle

**Python equivalent**: `range(pivot_index, break_index)` or `slice[pivot_index:break_index]`

### OB Properties

| Property | Bullish OB | Bearish OB |
|----------|------------|------------|
| Type | BULLISH | BEARISH |
| Top | Candle High | Candle High |
| Bottom | Candle Low | Candle Low |
| Formation | Extreme low candle | Extreme high candle |

### OB Lifecycle (Explicit State Machine)

| State | Description | Eligible for Entry |
|-------|-------------|-------------------|
| **FRESH** | Never touched (touch_count=0) | YES - highest confidence |
| **TOUCHED** | First return/touch (touch_count=1) | YES - ONE entry chance |
| **USED** | Trade executed from this OB | NO |
| **INVALIDATED** | Price closed through boundary | NO |

**Transitions:**
```
FRESH -> TOUCHED -> USED
  |
  v
INVALIDATED
```

**Rules:**
- Only FRESH and TOUCHED OBs are eligible for entry
- TOUCHED OBs get exactly ONE entry chance (first touch)
- Once USED, no further trades from this OB
- INVALIDATED OBs are permanently dead

### Invalidation Rules

- **Bullish OB**: 1H close **below** bottom boundary → invalidated
- **Bearish OB**: 1H close **above** top boundary → invalidated

**Distinct concepts**: Touch ≠ Mitigation ≠ Invalidation ≠ Consumption

### Dynamic Entry

```
widthPercent = ((topPrice - bottomPrice) / bottomPrice) × 100

If widthPercent ≤ 0.6%:
    Bullish: entry = topPrice (edge)
    Bearish: entry = bottomPrice (edge)

If widthPercent > 0.6%:
    Bullish: entry = topPrice - 0.25 × (topPrice - bottomPrice)
    Bearish: entry = bottomPrice + 0.25 × (topPrice - bottomPrice)
```

**Implementation**: `quantedge.smc.order_blocks.OrderBlockDetector` (LuxAlgo slice semantics)

## Equal Highs / Equal Lows

- Group pivots by price within 0.05% threshold
- Minimum 2 touches to qualify
- Used for liquidity level detection

## Liquidity

- **Buy-side liquidity**: Above price (equal highs, swing highs)
- **Sell-side liquidity**: Below price (equal lows, swing lows)
- Sweep = price trades through level
- Aligned sweep = sweep in direction of OB bias (confluence)

## Fair Value Gaps (FVG)

- 3-candle pattern: Gap between candle 1 and candle 3
- Bullish FVG: Candle1.high < Candle3.low
- Bearish FVG: Candle1.low > Candle3.high
- **NOT standalone entry trigger** - confluence only

## Market Regime Detection

| Swing Trend | Internal Trend | Regime |
|-------------|----------------|--------|
| Bullish | Bullish | BULLISH_TRENDING |
| Bearish | Bearish | BEARISH_TRENDING |
| Bullish | Bearish | CONFLICTING (RANGING) |
| Bearish | Bullish | CONFLICTING (RANGING) |
| Any | Ranging | RANGING |
| Ranging | Any | RANGING |

**Conflicting structure = RANGING = NO TRADE**

## Implementation Files

| Component | File |
|-----------|------|
| Volatility Parsing | `smc/volatility.py` |
| Structure Detection (stateful) | `smc/structure.py` |
| Order Blocks (LuxAlgo slice) | `smc/order_blocks.py` |
| Liquidity | `smc/liquidity.py` |
| Equal Levels | `smc/equal_levels.py` |
| FVG | `smc/fvg.py` |
| Main Analyzer | `smc/analyzer.py` |

## Testing Requirements

Every component must have deterministic tests with known inputs/outputs:

- Pivot detection at specific indices
- BOS/CHOCH classification with proper timing
- OB creation from known breaks (LuxAlgo slice semantics)
- OB range boundaries (inclusive start, exclusive end)
- OB extreme selection (min low / max high)
- Invalidation logic (close through boundary)
- Entry price calculation (width-based)
- OB lifecycle transitions (FRESH->TOUCHED->USED, INVALIDATED)
- LuxAlgo slice behavior (inclusive start, exclusive end)