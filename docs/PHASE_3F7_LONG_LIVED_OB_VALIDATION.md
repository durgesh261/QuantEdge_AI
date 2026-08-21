# Phase 3.7 Long-Lived Order Block Validation

## OB Lifetime Contract

In QuantEdge AI, an Order Block (OB) **never expires merely because it is old**.

### Core Invariants
1. **Age is NOT an Invalidation Condition**: There is strictly no time-based expiration, decay, bar count cutoff, or calendar-based removal of Order Blocks.
2. **Untouched OB Longevity**: An OB created months in the past that has never experienced a price touch remains in the `FRESH` state and remains 100% eligible for entry evaluation when price eventually returns to its zone.
3. **Determinism**: Order Block validity is governed solely by structural formation, break confirmation, and causal price interactions (touch / invalidation) according to the Phase 3E.2 lifecycle state machine:
   - `FRESH`: Never touched, eligible for entry.
   - `TOUCHED`: First return/touch occurred, eligible for single entry decision.
   - `USED`: Trade executed from this OB, no further entries.
   - `INVALIDATED`: Price closed through the invalidation boundary, dead.

---

## Six-Month Untouched OB

Deterministic simulation spanning 4,320+ hourly candles (~180 days / 6 months) was executed:

- **Scenario**: A Bullish OB was formed at $T_0$ with price zone `[49000.0, 50000.0]`.
- **Prolonged Trend**: Price traded continuously above the OB (between $55,000 and $80,000) for 6 months without entering the zone.
- **State After 6 Months**: The OB remained strictly in `OBState.FRESH` with `touch_count == 0` and `is_eligible_for_entry() == True`.
- **Subsequent Retest**: When price finally retraced into `[49000.0, 50000.0]` at candle index 4,321, the OB transitioned cleanly to `OBState.TOUCHED` on that exact candle.

---

## Formation / Break / Retest

The Phase 3E.2 causal lifecycle contract was verified:

- **Formation Candle**: Excluded from touch detection (`break_index < candle_idx`).
- **Break Candle**: Excluded from touch detection (`break_index < candle_idx`), preventing false self-touches during breakout bars.
- **First Genuine Retest**: A subsequent candle ($candle\_idx > break\_index$) overlapping `[bottom_price, top_price]` transitions `FRESH -> TOUCHED` and increments `touch_count`.

---

## Invalidation

- **Bullish Invalidation**: Price closing strictly below `bottom_price` transitions the OB to `OBState.INVALIDATED` and removes it from `_active_obs`.
- **Bearish Invalidation**: Price closing strictly above `top_price` transitions the OB to `OBState.INVALIDATED` and removes it from `_active_obs`.
- **No Revival**: An invalidated OB is permanently dead; future price returns to the zone months later cannot revive the OB or restore active status.

---

## Current Price Inside OB

A read-only helper concept was implemented to inspect whether a given price level engages a valid OB zone without triggering trade signals:

- **Helper Function**: `is_price_inside_ob(price, order_block) -> bool`
- **Method**: `OrderBlock.contains_price(price) -> bool`
- **Engine Query**: `IncrementalSMCEngine.get_active_obs_at_price(price) -> List[OrderBlock]`
- **Rule**: Price is inside when `order_block.bottom_price <= price <= order_block.top_price`.
- **Phase 4 Isolation**: This check is purely structural/state-based and does NOT generate buy/sell signals, orders, or entries.

---

## Multiple Active OBs

- **Coexistence**: Multiple active OBs across different price zones coexist in the active pool simultaneously.
- **No Overwrite**: The creation of newer OBs does not displace, overwrite, or prune older active OBs.
- **Overlapping Zones**: If current price enters a region where multiple active OB zones overlap, `get_active_obs_at_price()` returns all matching active OBs for strategy prioritization in Phase 4.

---

## Incremental vs Replay

- Processing candles one-by-one via `IncrementalSMCEngine.process_new_candles()` yields identical OB identities, price boundaries, total counts, active counts, and lifecycle states compared to a full batch initialization via `initialize_from_canonical()`.

---

## Future-Data Invariance

- The state of an Order Block at timestamp $T$ is completely unaffected by whether candles at $T+1 \dots T+k$ are subsequently processed.
- Future candles cannot retroactively invalidate or touch an OB prior to their occurrence timestamp.

---

## Timezone

### Canonical Architecture
- **Internal Storage & Calculations**: STRICTLY in **UTC**. All CSVs, metadata, database keys, candle models, and SMC algorithms operate on UTC timestamps.
- **User-Facing Display**: Standardized on **`Asia/Kolkata` (UTC+05:30)** using Python standard library `zoneinfo.ZoneInfo("Asia/Kolkata")`.
- **Deterministic Conversion Utilities** ([`quantedge/utils/timezone.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/utils/timezone.py)):
  - `to_utc(dt_or_ts) -> datetime`
  - `to_ist(dt_or_ts) -> datetime`
  - `format_ist(dt_or_ts, fmt="%Y-%m-%d %H:%M:%S %Z") -> str`
  - `from_ist_to_utc(dt) -> datetime`
- **Verification Example**: `2026-08-21 14:00:00 UTC` converts to `2026-08-21 19:30:00 Asia/Kolkata` (`+05:30`).

---

## Tests

New dedicated test suite: [`engine/tests/test_phase3f7_long_lived_ob.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase3f7_long_lived_ob.py) (24/24 passed).

```text
======================= 448 passed, 1 skipped in 19.51s =======================
```

- **Total Tests**: 449
- **Passed**: 448
- **Skipped**: 1 (`test_ob_pipeline_regression.py` marked for TV reference sync)
- **Failed**: 0 (100% pass rate)

---

## Historical Data Integrity

The 2026 canonical dataset and historical baseline remain 100% intact:
- **Canonical Path**: `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`
- **Historical Slice (2026-01-01 to 2026-08-20)**: Exactly 5,545 rows.
- **Historical Slice SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`.
- **Gap Count**: 0.

---

## Frozen SMC

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# (Output: EMPTY — ZERO DIFF)
```
- [`engine/src/quantedge/smc/structure.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/structure.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/order_blocks.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/order_blocks.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/volatility.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/volatility.py) — **ZERO DIFF**

---

## Repository Cleanliness

- All scratch files were kept in the external artifact workspace.
- `git status --short` contains only clean production, test, and documentation additions.

---

## Files Changed
1. [`engine/src/quantedge/smc/models.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/models.py): Added `contains_price()` method and `is_price_inside_ob()` helper.
2. [`engine/src/quantedge/market_data/incremental_engine.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/market_data/incremental_engine.py): Added `get_active_obs_at_price()` and `is_price_in_active_ob()`.
3. [`engine/src/quantedge/utils/timezone.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/utils/timezone.py): Standardized UTC and `Asia/Kolkata` conversion utilities.
4. [`engine/src/quantedge/utils/__init__.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/utils/__init__.py): Exported timezone utilities.
5. [`engine/tests/test_phase3f7_long_lived_ob.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase3f7_long_lived_ob.py): 24 comprehensive regression tests for OB longevity, retest, and timezone handling.
6. [`docs/PHASE_3F7_LONG_LIVED_OB_VALIDATION.md`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/docs/PHASE_3F7_LONG_LIVED_OB_VALIDATION.md): Complete Phase 3.7 documentation.

---

## Final Verdict

# `LONG_LIVED_OB_VALIDATED`
