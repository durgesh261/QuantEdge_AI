# Strategy Specification - QuantEdge AI V2

## Overview

This document defines the complete trading strategy for QuantEdge AI V2, implementing the 9-factor confidence model with hard filters.

## Strategy Baseline

| Parameter | Value |
|-----------|-------|
| Timeframe | 1H |
| SMC Source | LuxAlgo Smart Money Concepts |
| Symbols | BTCUSD.P, ETHUSD.P, SOLUSD.P, XRPUSD.P |
| Confidence Threshold | 85 / 100 |
| Max Active Trades | 1 (portfolio-wide) |

## Entry Rules (ALL must pass)

### Hard Filters (Rejection = No Trade)

1. **Valid Order Block**
   - OB exists in MarketStructureState
   - OB.is_invalidated = false
   - OB.is_used = false

2. **First Touch Only**
   - OB.touch_count = 0 (fresh)
   - touch_count >= 1 -> REJECT

3. **OB Not Previously Used**
   - OB.is_used = false
   - Once trade opened from OB -> is_used = true -> no more entries

4. **Market Regime Valid**
   - Swing trend != RANGING
   - Internal trend != RANGING
   - Swing trend = Internal trend (no conflict)
   - Conflicting (e.g., Swing=Bullish, Internal=Bearish) -> RANGING -> REJECT

5. **No Opposing Zone**
   - No opposing OB within 0.5% of midline
   - Threshold: 0.5% proximity
   - Hard rejection (not confidence penalty)

6. **Confidence >= 85**
   - 9-factor model total >= 85
   - Below 85 -> REJECT

7. **Risk Validation Passes**
   - Spring Boot independent verification
   - Position size > 0
   - Leverage <= 100x
   - Sufficient balance

### Confidence Scoring (9 Factors = 100 Points)

| Factor | Max Points | Description |
|--------|------------|-------------|
| 1. Trend Alignment | 15 | Swing + Internal align with OB direction |
| 2. OB Freshness | 15 | touchCount=0 (15), =1 (10), >=2 (0) |
| 3. First Touch | 15 | touchCount=0 (15), else (0) |
| 4. BOS / CHOCH | 15 | CHOCH (15), BOS (10) |
| 5. Liquidity Sweep | 10 | Aligned (10), Other (5), None (0) |
| 6. Premium/Discount | 10 | OB in correct zone (10), else (0) |
| 7. Session/Volatility | 5 | Favorable session (5), else (0) |
| 8. Risk/Reward | 10 | Achievable R:R >= 1.5 (10), else scaled |
| 9. News/Macro Safety | 5 | No high-impact news (5), else (0) |
| **Total** | **100** | **Threshold: 85** |

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

## Unresolved Items (Documented)

| Item | Status | Notes |
|------|--------|-------|
| Freshness decay formula | UNRESOLVED | Need deterministic formula |
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
| Confidence Scoring | `strategy/confidence.py` |
| Risk Calculator | `strategy/risk.py` |
| Strategy Engine | `strategy/engine.py` |
| Backtesting | `backtesting/engine.py` |

## Testing Matrix

| Scenario | Expected Signal |
|----------|-----------------|
| Valid OB, first touch, confidence=90 | VALID |
| Invalid OB (invalidated) | INVALID_OB |
| touchCount=1 | NOT_FIRST_TOUCH |
| OB.is_used=true | OB_USED |
| Ranging market | RANGING_MARKET |
| Opposing zone nearby | OPPOSING_ZONE |
| Confidence=84 | LOW_CONFIDENCE |
| Confidence=85 | VALID |
| Confidence=95 | VALID |
| Multiple candidates | Highest confidence wins |
| Active trade exists | ONE_TRADE_ACTIVE |