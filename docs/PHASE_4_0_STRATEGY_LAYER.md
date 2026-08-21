# Phase 4.0 Strategy Layer

## Existing SMC Interface

The Strategy Layer operates purely as an observational, read-only consumer of existing SMC (Smart Money Concepts) engine state.

### Consumed SMC Interfaces:
1. **`IncrementalSMCEngine`**:
   - `get_active_obs_at_price(price) -> List[OrderBlock]`: Queries active Order Blocks whose price range $[bottom, top]$ contains the closed candle price.
   - `get_active_obs() -> List[OrderBlock]`: Returns all active (`FRESH` or `TOUCHED`) Order Blocks.
   - `_internal_detector.get_current_trend() -> TrendDirection`: Retrieves current internal structure trend (`BULLISH`, `BEARISH`, or `RANGING`).
   - `_swing_detector.get_current_trend() -> TrendDirection`: Retrieves current swing structure trend (`BULLISH`, `BEARISH`, or `RANGING`).
   - `get_recent_breaks(lookback=10) -> List[StructureBreak]`: Retrieves recent BOS/CHOCH structure breaks.
2. **`OrderBlock`**:
   - `is_eligible_for_entry() -> bool`: Returns `True` if `state in (OBState.FRESH, OBState.TOUCHED)`.
   - `contains_price(price) -> bool`: Evaluates $bottom\_price \le price \le top\_price$.
   - `calculate_entry_price() -> Decimal`: Evaluates dynamic entry price per SMC spec.
   - `calculate_stop_loss() -> Decimal`: Evaluates invalidation stop boundary per SMC spec.
3. **No SMC Mutation**: Strategy evaluation does not create, modify, touch, or invalidate Order Blocks, does not mutate structure breaks, and does not alter canonical storage.

---

## StrategyDecision Contract

The Strategy Engine outputs a deterministic `StrategyDecision` data model:

```python
@dataclass
class StrategyDecision:
    timestamp: datetime                    # Timezone-aware UTC timestamp
    symbol: str                            # e.g., "BTCUSD.P"
    timeframe: str                         # e.g., "1h"
    direction: StrategyDirection = NONE    # NONE, LONG, or SHORT
    setup_type: Optional[str] = None       # e.g., "BULLISH_OB_RETEST" / "BEARISH_OB_RETEST"
    entry: Optional[Decimal] = None        # Explicit OB-derived entry price
    stop_loss: Optional[Decimal] = None    # Explicit OB-derived stop loss
    take_profit: Optional[Decimal] = None  # None (reserved for Phase 4.1+)
    risk_reward: Optional[Decimal] = None  # None (reserved for Phase 4.1+)
    confidence: Optional[float] = None     # Optional confidence score
    reasons: list[str] = field(...)        # Factual, deterministic rationale
    order_block: Optional[OrderBlock]      # The active OB engaged
    candle: Optional[object]               # The closed candle evaluated
```

### Display Helper:
- `decision.timestamp_ist`: Dynamically formats the UTC timestamp in `Asia/Kolkata` (`UTC+05:30`) for presentation and logs without altering internal UTC storage.

---

## LONG Setup

A `LONG` setup is triggered if and only if all of the following deterministic conditions are met:
1. **Valid Active Bullish OB**: An Order Block with `type == "BULLISH"` is present in the active pool (`state in (OBState.FRESH, OBState.TOUCHED)` and not invalidated / not used).
2. **Price Containment**: The closed candle price is inside the Bullish OB boundary ($bottom\_price \le candle.close \le top\_price$).
3. **Bullish Structure Confirmation**: Bullish structure confirmation is present via:
   - `internal_trend == TrendDirection.BULLISH`, OR
   - `swing_trend == TrendDirection.BULLISH`, OR
   - Recent structure break was bullish (`recent_breaks[-1].direction == TrendDirection.BULLISH`).
4. **No Invalidation**: The OB has not been invalidated.

**Output**:
- `direction = StrategyDirection.LONG`
- `setup_type = "BULLISH_OB_RETEST"`
- `entry = ob.calculate_entry_price()`
- `stop_loss = ob.calculate_stop_loss()`
- `reasons = ["valid bullish order block", "price inside bullish order block zone", "bullish structure confirmation"]`

---

## SHORT Setup

A `SHORT` setup is triggered if and only if all of the following deterministic conditions are met:
1. **Valid Active Bearish OB**: An Order Block with `type == "BEARISH"` is present in the active pool (`state in (OBState.FRESH, OBState.TOUCHED)` and not invalidated / not used).
2. **Price Containment**: The closed candle price is inside the Bearish OB boundary ($bottom\_price \le candle.close \le top\_price$).
3. **Bearish Structure Confirmation**: Bearish structure confirmation is present via:
   - `internal_trend == TrendDirection.BEARISH`, OR
   - `swing_trend == TrendDirection.BEARISH`, OR
   - Recent structure break was bearish (`recent_breaks[-1].direction == TrendDirection.BEARISH`).
4. **No Invalidation**: The OB has not been invalidated.

**Output**:
- `direction = StrategyDirection.SHORT`
- `setup_type = "BEARISH_OB_RETEST"`
- `entry = ob.calculate_entry_price()`
- `stop_loss = ob.calculate_stop_loss()`
- `reasons = ["valid bearish order block", "price inside bearish order block zone", "bearish structure confirmation"]`

---

## NONE

When no setup conditions are met:
- `direction = StrategyDirection.NONE`
- `setup_type = None`
- `entry = None`, `stop_loss = None`, `take_profit = None`, `risk_reward = None`, `confidence = None`
- `reasons`: Factual descriptions (e.g. `"Price outside any active order block"`, `"Price inside bullish OB but no bullish structure confirmation"`, etc.).

---

## Long-Lived OB Handling

- In compliance with Phase 3.7 specifications, Order Blocks **never expire due to age**.
- A 6-month-old (4,320+ hourly candles) untouched OB in `OBState.FRESH` is evaluated with the exact same authority and validity as a recently created OB.
- The strategy does not employ latest-OB-only filters, calendar limits, or bar-age cutoffs.

---

## Current Price Inside OB

- Strategy evaluation uses `smc_engine.get_active_obs_at_price(candle.close)` and `ob.contains_price(candle.close)`.
- Engaging an OB zone is a prerequisite for condition evaluation, but does **NOT** alone constitute a trade signal. Confirmation of trend and structural validity is strictly enforced.

---

## Causality

- Every decision at candle $T$ uses strictly information from candles $\le T$ and SMC states computed from candles $\le T$.
- Future candles ($T+1 \dots T+k$) cannot retroactively alter or influence a decision at timestamp $T$.

---

## Incremental vs Replay

- Strategy evaluation across an incremental candle feed produces bit-for-bit identical `StrategyDecision` outcomes compared to evaluating the state after a full historical batch replay.

---

## Determinism

- Given the exact same closed candle and SMC engine state, `StrategyEngine` produces identical decisions, setup types, entry/SL levels, and reason lists with 0% variance.

---

## Timezone

- **Internal Representation**: All `StrategyDecision.timestamp` fields strictly maintain timezone-aware **UTC** (`datetime.timezone.utc`).
- **User-Facing Presentation**: `StrategyDecision.timestamp_ist` formats the timestamp into **`Asia/Kolkata`** (`UTC+05:30`) via `zoneinfo.ZoneInfo("Asia/Kolkata")`.

---

## Tests

New test suite: [`engine/tests/test_phase4_strategy.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_strategy.py) (24/24 passed).

Key scenarios verified:
1. No setup $\to$ `NONE`
2. Bullish OB outside price $\to$ `NONE`
3. Bearish OB outside price $\to$ `NONE`
4. Bullish OB + price inside + bullish confirmation $\to$ `LONG`
5. Bearish OB + price inside + bearish confirmation $\to$ `SHORT`
6. Invalidated OB $\to$ `NONE`
7. Old untouched bullish OB remains eligible
8. Old untouched bearish OB remains eligible
9. 6-month-old OB produces setup
10. Newest OB does not override older valid OB
11. Multiple valid OBs coexist
12. Current price inside OB detection
13. Forming candle cannot generate strategy signal
14. Duplicate candle idempotent handling
15. Future-data invariance
16. Incremental $\equiv$ Full replay equivalence
17. Strategy does not mutate SMC state
18. Strategy does not modify canonical CSV
19. UTC timestamp internal retention
20. `Asia/Kolkata` display conversion
21. No order execution methods
22. No private exchange APIs
23. No Binance dependencies
24. Frozen SMC files unchanged

---

## Phase 3 Regression

Full test suite execution across repository:
```text
======================= 472 passed, 1 skipped in 20.97s =======================
```
- **Total Tests**: 473
- **Passed**: 472
- **Skipped**: 1 (`test_ob_pipeline_regression.py` marked for TV reference sync)
- **Failed**: 0 (100% pass rate)

---

## Frozen SMC

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# Output: EMPTY (ZERO DIFF)
```
- [`engine/src/quantedge/smc/structure.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/structure.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/order_blocks.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/order_blocks.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/volatility.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/volatility.py) — **ZERO DIFF**

---

## Repository Cleanliness

- All temporary test scripts were isolated from the git repository.
- `git status --short` shows only clean production, test, and documentation additions.

---

## Files Changed
1. [`engine/src/quantedge/strategy/models.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/models.py): Added `StrategyDirection`, `SetupType`, and `StrategyDecision`.
2. [`engine/src/quantedge/strategy/engine.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/engine.py): Implemented `evaluate_candle` and `evaluate_state`.
3. [`engine/src/quantedge/strategy/__init__.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/__init__.py): Exported strategy public interface.
4. [`engine/src/quantedge/smc/analyzer.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/analyzer.py): Fixed typing import syntax error.
5. [`engine/tests/test_phase4_strategy.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_strategy.py): 24 regression tests for Phase 4.0 Strategy Layer.
6. [`docs/PHASE_4_0_STRATEGY_LAYER.md`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/docs/PHASE_4_0_STRATEGY_LAYER.md): Comprehensive Phase 4.0 documentation.

---

## Final Verdict

# `STRATEGY_LAYER_READY`
