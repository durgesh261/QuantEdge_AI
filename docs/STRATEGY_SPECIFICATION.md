# Strategy Specification - QuantEdge AI V2

## Overview

This document defines the complete trading strategy for QuantEdge AI V2, implementing the 8-factor confidence model with hard filters and explicit OB lifecycle.

## Strategy Baseline

| Parameter | Value |
|-----------|-------|
| Timeframe | 1H |
| SMC Source | LuxAlgo Smart Money Concepts |
| Symbols | BTCUSD.P, ETHUSD.P, SOLUSD.P, XRPUSD.P |
| Confidence Threshold | 85 / 100 |
| Max Active Trades | 1 (portfolio-wide) |

## Order Block Lifecycle (Explicit State Machine)

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

## Entry Rules (ALL must pass)

### Hard Filters (Rejection = No Trade)

1. **Valid Order Block**
   - OB exists in MarketStructureState
   - OB.state != INVALIDATED
   - OB.state != USED

2. **Eligible State**
   - OB.state in (FRESH, TOUCHED)
   - NOT_ELIGIBLE if USED or INVALIDATED

3. **Market Regime Valid**
   - Swing trend != RANGING
   - Internal trend != RANGING
   - Swing trend = Internal trend (no conflict)
   - Conflicting (e.g., Swing=Bullish, Internal=Bearish) -> RANGING -> REJECT

4. **No Opposing Zone**
   - No opposing OB within 0.5% of midline
   - Threshold: 0.5% proximity
   - Hard rejection (not confidence penalty)

5. **Confidence >= 85**
   - 8-factor model total >= 85
   - Below 85 -> REJECT

6. **Risk Validation Passes**
   - Spring Boot independent verification
   - Position size > 0
   - Leverage <= 100x
   - Sufficient balance

### Confidence Scoring (8 Factors = 100 Points)

| Factor | Max Points | Description |
|--------|------------|-------------|
| 1. Trend Alignment | 15 | Swing + Internal align with OB direction |
| 2. OB State | 15 | FRESH=15, TOUCHED=10, USED/INVALIDATED=0 |
| 3. BOS / CHOCH | 15 | CHOCH=15, BOS=10 |
| 4. Liquidity Sweep | 10 | Aligned=10, Other=5, None=0 |
| 5. Premium/Discount | 10 | OB in correct zone=10, else=0 |
| 6. Session/Volatility | 5 | Favorable session=5, else=0 |
| 7. Risk/Reward | 10 | Achievable R:R >= 1.5=10, else scaled |
| 8. News/Macro Safety | 5 | No high-impact news=5, else=0 |
| **Total** | **100** | **Threshold: 85** |

**Removed from V1 (9-factor):**
- "OB Freshness" (15) + "First Touch" (15) -> consolidated into "OB State" (15)
- The two factors overlapped and were contradictory

## Risk Model

| Parameter | Value | Formula |
|-----------|-------|---------|
| Risk per Trade | 35% | riskAmount = balance * 0.35 |
| Target Reward | 60% | rewardAmount = balance * 0.60 |
| Max Leverage | 100x | Cap on calculated leverage |
| Account R:R | ~1.71 | 60/35 |

### Position Sizing

```
riskAmount = accountBalance * 0.35
positionSize = riskAmount / |entry - stopLoss|
leverage = min(100, positionSize * entryPrice / accountBalance)
```

### Stop Loss

| Direction | Stop Loss |
|-----------|-----------|
| Long (Bullish OB) | Lower OB boundary |
| Short (Bearish OB) | Upper OB boundary |

**No ATR offset** - legacy rule removed.

### Take Profit

```
rewardAmount = accountBalance * 0.60
positionSize = riskAmount / |entry - stopLoss|
priceMove = rewardAmount / positionSize

Long:  TP = entry + priceMove
Short: TP = entry - priceMove
```

## Dynamic Entry

Based on OB width:

```
widthPercent = ((topPrice - bottomPrice) / bottomPrice) * 100

If widthPercent <= 0.6%:
    Long:  entry = topPrice
    Short: entry = bottomPrice

If widthPercent > 0.6%:
    Long:  entry = topPrice - 0.25 * width
    Short: entry = bottomPrice + 0.25 * width
```

## Candidate Selection (Multiple Symbols)

When multiple symbols qualify simultaneously:

1. Evaluate ALL candidates
2. Apply hard filters (discard invalid)
3. Discard confidence < 85
4. Rank remaining by confidence (descending)
5. Select highest confidence
6. Execute ONLY ONE trade

```
winner = max(eligibleCandidates, key=confidenceScore)
```

## One Active Trade Rule

- **Maximum 1 active trade** across entire portfolio
- If BTC active -> ETH/SOL/XRP blocked
- Scanner continues monitoring
- New entries blocked until current trade closes

## Trade Lifecycle

```
SCAN -> CANDIDATE -> VALIDATE -> EXECUTE -> MONITOR -> EXIT
  v        v           v          v         v       v
All     Score      Risk       Submit   Track   TP/SL/
Symbols  85+       Check      Order    P&L     Manual
```

## Exit Rules

1. **Take Profit Hit** -> Close position (target achieved)
2. **Stop Loss Hit** -> Close position (risk managed)
3. **Manual Close** -> User/API initiated
4. **End of Data** -> Close at last price (backtest only)

## Legacy Rules (DO NOT USE)

| Legacy Rule | Status | Replacement |
|-------------|--------|-------------|
| 75% confidence threshold | REMOVED | 85% threshold |
| TP1 = 1.5R, TP2 = 2.8R | REMOVED | Single TP at 60% account growth |
| Min RR = 2.5 | REMOVED | Account R:R ~1.71 |
| Risk 1.5%, Lev 10x | REMOVED | Risk 35%, Lev 100x |
| OB = last opposite candle | REMOVED | LuxAlgo extreme selection |
| SL = OB +/- 0.25 ATR | REMOVED | SL = OB boundary |
| PAT / Merged Zones | DEFERRED | Separate layer after SMC validation |
| 9-factor confidence (75% threshold) | REMOVED | 8-factor confidence (85% threshold) |
| OB Freshness + First Touch (separate) | REMOVED | Consolidated into OB State |

## Unresolved Items (Documented)

| Item | Status | Notes |
|------|--------|-------|
| Freshness decay formula | UNRESOLVED | Need deterministic formula (replaced by OB State) |
| Structure lookback period | UNRESOLVED | Recent BOS/CHOCH window |
| Session definitions | UNRESOLVED | Allowed/restricted hours |
| News/macro source | UNRESOLVED | Data source & blocking logic |
| Fees/funding/slippage | UNRESOLVED | Gross vs net targets |
| Leverage rounding | UNRESOLVED | Deterministic behavior |
| PAT reintegration | UNRESOLVED | After pure SMC validation |

## Implementation Files

| Component | File |
|-----------|------|
| Strategy Models | `strategy/models.py` |
| Confidence Scoring (8-factor) | `strategy/confidence.py` |
| Risk Calculator | `strategy/risk.py` |
| Strategy Engine | `strategy/engine.py` |
| Backtesting | `backtesting/engine.py` |
| OB Lifecycle (OBState) | `smc/models.py` |

## Testing Matrix

| Scenario | Expected Signal |
|----------|-----------------|
| Valid OB (FRESH), confidence=90 | VALID |
| Valid OB (TOUCHED), confidence=88 | VALID |
| Invalid OB (INVALIDATED) | INVALID_OB |
| OB.state = USED | OB_USED |
| OB.state = INVALIDATED | INVALID_OB |
| OB.state = FRESH/TOUCHED but not eligible | NOT_ELIGIBLE |
| Ranging market | RANGING_MARKET |
| Opposing zone nearby | OPPOSING_ZONE |
| Confidence=84 | LOW_CONFIDENCE |
| Confidence=85 | VALID |
| Confidence=95 | VALID |
| Multiple candidates | Highest confidence wins |
| Active trade exists | ONE_TRADE_ACTIVE |
| OB FRESH -> TOUCHED transition | touch_count=1, state=TOUCHED |
| OB TOUCHED -> USED on execution | state=USED |
| OB INVALIDATED by close | state=INVALIDATED |