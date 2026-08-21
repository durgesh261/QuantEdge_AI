# Phase 4.1 Signal Qualification

## Setup State Model

Phase 4.1 advances the Strategy Layer into a structured Signal Qualification pipeline that answers the core question:
> **"Is this Order Block currently a qualified trading setup?"** *(rather than "Place a trade")*

### Deterministic Setup States (`SetupState`)
- **`NO_SETUP`**: No active valid Order Block exists in the pool (or all OBs in the pool are invalidated/used).
- **`WATCHING_OB`**: Valid active OB(s) exist in the pool, but current closed candle price is outside their price zones.
- **`OB_ENGAGED`**: Current closed candle price is inside an active valid OB zone, but structural confirmation is incomplete or conflicting.
- **`QUALIFIED_LONG`**: Bullish OB + price inside OB + required bullish structure confirmation satisfied.
- **`QUALIFIED_SHORT`**: Bearish OB + price inside OB + required bearish structure confirmation satisfied.

---

## Active OB Selection

- The Strategy Engine evaluates **ALL** active, eligible Order Blocks in the system (`state in (OBState.FRESH, OBState.TOUCHED)` and `not is_invalidated()` and `not is_used()`).
- **No Exclusion Rules**: There is strictly no "newest OB only", "latest OB only", "30-day cutoff", or "100-candle expiration".
- **Multi-OB Deterministic Priority**: When multiple active OBs are simultaneously engaged by current price, candidate selection follows a strict deterministic priority:
  1. Direction matching confirmed structure trend.
  2. Higher confidence score.
  3. Narrower zone width.
  4. Formation index tiebreaker.

---

## Long-Lived OB

- Order Blocks never expire based on elapsed time or candle count.
- An untouched Bullish or Bearish OB created 6 months in the past (4,320+ hourly candles) remains in `OBState.FRESH` and qualifies as `QUALIFIED_LONG` / `QUALIFIED_SHORT` the moment price retraces into its zone with matching structural confirmation.

---

## Current Price Engagement

- Evaluated using `smc_engine.get_active_obs_at_price(candle.close)` and `ob.contains_price(candle.close)`.
- Price engagement ($bottom\_price \le candle.close \le top\_price$) transitions state to `OB_ENGAGED`.
- **Engagement $\ne$ Buy/Sell**: Price engagement triggers condition evaluation, but does not produce a qualified signal unless structural confirmation is verified.

---

## LONG Qualification

A Bullish setup transitions to `QUALIFIED_LONG` if and only if all authoritative conditions are satisfied:
1. An active Bullish Order Block exists (`type == "BULLISH"`, `state in (OBState.FRESH, OBState.TOUCHED)`).
2. The OB is not invalidated and has not been used.
3. Current **CLOSED** candle price is inside the OB boundary ($bottom\_price \le candle.close \le top\_price$).
4. Authoritative bullish structure confirmation exists:
   - `internal_trend == TrendDirection.BULLISH`, OR
   - `swing_trend == TrendDirection.BULLISH`, OR
   - Recent structure break was bullish (`recent_breaks[-1].direction == TrendDirection.BULLISH`).

**Decision Attributes**:
- `setup_state = SetupState.QUALIFIED_LONG`
- `direction = StrategyDirection.LONG`
- `setup_type = "BULLISH_OB_RETEST"`
- `entry = ob.calculate_entry_price()`
- `stop_loss = ob.calculate_stop_loss()`
- `setup_id = generate_setup_id(symbol, timeframe, ob, StrategyDirection.LONG)`
- `reasons = ["active bullish order block", "price entered bullish order block zone", "bullish structure confirmation present"]`

---

## SHORT Qualification

A Bearish setup transitions to `QUALIFIED_SHORT` if and only if all authoritative conditions are satisfied:
1. An active Bearish Order Block exists (`type == "BEARISH"`, `state in (OBState.FRESH, OBState.TOUCHED)`).
2. The OB is not invalidated and has not been used.
3. Current **CLOSED** candle price is inside the OB boundary ($bottom\_price \le candle.close \le top\_price$).
4. Authoritative bearish structure confirmation exists:
   - `internal_trend == TrendDirection.BEARISH`, OR
   - `swing_trend == TrendDirection.BEARISH`, OR
   - Recent structure break was bearish (`recent_breaks[-1].direction == TrendDirection.BEARISH`).

**Decision Attributes**:
- `setup_state = SetupState.QUALIFIED_SHORT`
- `direction = StrategyDirection.SHORT`
- `setup_type = "BEARISH_OB_RETEST"`
- `entry = ob.calculate_entry_price()`
- `stop_loss = ob.calculate_stop_loss()`
- `setup_id = generate_setup_id(symbol, timeframe, ob, StrategyDirection.SHORT)`
- `reasons = ["active bearish order block", "price entered bearish order block zone", "bearish structure confirmation present"]`

---

## Entry / Stop Loss

- **Entry**: Derived deterministically from `ob.calculate_entry_price()`.
- **Stop Loss**: Derived deterministically from `ob.calculate_stop_loss()` (opposite OB boundary).
- **Take Profit / Risk-Reward**: Explicitly kept as `take_profit = None` and `risk_reward = None` (reserved for Phase 4.2+ risk management).

---

## Duplicate Protection

- Repeated submission of the same closed candle produces the exact same idempotent `StrategyDecision` and `setup_id` with 0 duplicate state creations.

---

## Causality

- Decisions at candle $T$ strictly consume candles $\le T$ and SMC states calculated at or before $T$.
- Future candles ($T+1 \dots T+k$) cannot retroactively change the historical signal computed at timestamp $T$.

---

## Incremental vs Replay

- Processing candles incrementally one-by-one matches a single full batch replay across all decision fields: `setup_state`, `direction`, `setup_id`, `entry`, `stop_loss`, and `reasons`.

---

## Read-Only SMC Guarantee

- The Strategy Engine is strictly observational.
- Evaluating a candle against SMC state causes **ZERO** side-effects:
  - Does NOT call `check_touch()` or `check_invalidation()`.
  - Does NOT modify `ob.state`, `ob.touch_count`, or `ob.invalidated_at`.
  - Does NOT modify pivots, breaks, or events.
  - Does NOT write to CSV files.

---

## UTC / Asia/Kolkata

- **Internal Storage**: `StrategyDecision.timestamp` is strictly timezone-aware **UTC** (`datetime.timezone.utc`).
- **User-Facing Presentation**: `StrategyDecision.timestamp_ist` formats the timestamp into **`Asia/Kolkata`** (`UTC+05:30`) via Python standard `zoneinfo.ZoneInfo("Asia/Kolkata")`.

---

## UI-Ready Output

The `StrategyDecision` model provides structured attributes designed for downstream UI/chart rendering:
- `ob_zone`: `(bottom_price, top_price)`
- `ob_formation_ts`: Timestamp when the OB was originally formed.
- `ob_age_days`: Precise age in fractional days.
- `setup_id`: Traceable identifier: `{symbol}_{timeframe}_OB{index}_{timestamp}_{direction}`.
- `timestamp_ist`: Formatted IST string (e.g. `2026-08-21 19:30:00 IST`).

---

## Tests

New dedicated test suite: [`engine/tests/test_phase4_1_signal_qualification.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_1_signal_qualification.py) (30/30 passed).

All 30 Scenarios Verified:
1. No OB $\to$ `NO_SETUP`
2. Valid OB outside price $\to$ `WATCHING_OB`
3. Bullish OB inside price unconfirmed $\to$ `OB_ENGAGED`
4. Bearish OB inside price unconfirmed $\to$ `OB_ENGAGED`
5. Bullish OB + bullish confirmation $\to$ `QUALIFIED_LONG`
6. Bearish OB + bearish confirmation $\to$ `QUALIFIED_SHORT`
7. Bullish OB + bearish trend $\to$ `OB_ENGAGED` (not long)
8. Bearish OB + bullish trend $\to$ `OB_ENGAGED` (not short)
9. Invalidated OB $\to$ `NO_SETUP`
10. Used OB $\to$ `NO_SETUP`
11. 6-month untouched OB $\to$ `QUALIFIED_LONG`
12. Old OB vs newer OB coexistence
13. Multiple overlapping OBs engagement
14. Deterministic OB selection priority
15. Forming candle rejection
16. Exact boundary testing (inside vs outside)
17. Duplicate candle idempotency
18. Duplicate evaluation idempotency
19. Future-data invariance
20. Incremental $\equiv$ Full replay equivalence
21. Zero OB mutation guarantee
22. Zero structure mutation guarantee
23. Zero CSV modification guarantee
24. Deterministic `setup_id`
25. Deterministic `reasons` list
26. UTC internal timestamp retention
27. IST presentation display formatting
28. No order execution methods
29. No Binance dependencies
30. Frozen SMC files unchanged

---

## Phase 3 Regression

Full test suite execution across repository:
```text
======================= 502 passed, 1 skipped in 21.33s =======================
```
- **Total Tests**: 503
- **Passed**: 502 (100% pass rate)
- **Skipped**: 1 (pre-existing TV sync skip)
- **Failed**: 0

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

- All temporary scratch files were isolated outside the repository.
- Working tree clean.

---

## Files Changed
1. [`engine/src/quantedge/strategy/models.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/models.py): Added `SetupState`, `generate_setup_id`, and UI properties on `StrategyDecision`.
2. [`engine/src/quantedge/strategy/engine.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/engine.py): Implemented Phase 4.1 signal qualification rules and multi-OB deterministic selection.
3. [`engine/src/quantedge/strategy/__init__.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/__init__.py): Exported `SetupState` and `generate_setup_id`.
4. [`engine/tests/test_phase4_1_signal_qualification.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_1_signal_qualification.py): 30 unit and regression tests for Phase 4.1.
5. [`docs/PHASE_4_1_SIGNAL_QUALIFICATION.md`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/docs/PHASE_4_1_SIGNAL_QUALIFICATION.md): Complete Phase 4.1 documentation report.

---

## Final Verdict

# `SIGNAL_QUALIFICATION_READY`
