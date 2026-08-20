# QuantEdge AI V2 — Phase 3E: LuxAlgo OB Differential Validation

## 1. Objective

Phase 3E is a diagnostic investigation into the differences between the Python SMC OB engine
and LuxAlgo Smart Money Concepts on TradingView for Delta Exchange India BTCUSD.P 1H.

**Goal**: Understand *why* discrepancies exist — not immediately fix the production algorithm.

**Status**: `DIAGNOSTIC / PENDING MANUAL TV BLUE OB REFERENCES`

---

## 2. Dataset & Engine Provenance

| Parameter | Value |
|-----------|-------|
| Exchange | Delta Exchange India |
| API Symbol | BTCUSD |
| TradingView Symbol | BTCUSD.P |
| Timeframe | 1H |
| Dataset Source | `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` |
| Dataset SHA-256 (row-based) | `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b` |
| Dataset Period | 2026-01-01T00:00 → 2026-08-20T00:00 UTC |
| Candle Count | 5,545 |
| ATR Period | 200 |
| ATR Multiplier | 2.0 |
| Internal Length | 5 |
| Swing Length | 50 |
| OB Filter | ATR |
| OB Mitigation Rule | High/Low |

> **Data Cutoff**: Python OBs computed only through **2026-08-20 00:00 UTC**.

---

## 3. Known TradingView Findings (From Manual Screenshots)

The following observations were made from visual inspection of TradingView Free
(Delta Exchange India BTCUSD.P 1H, LuxAlgo SMC) — **without** TradingView Bar Replay:

### Finding A — State Mismatch (Latest Bullish OB)

| Field | Python (Production) | TradingView Visual |
|-------|--------------------|--------------------|
| Direction | bullish | blue OB (bullish) |
| Upper | 64,328.0 | ~64,328 |
| Lower | 64,137.5 | ~64,138 |
| Created | 2026-08-19 06:00 UTC | visible |
| **State** | **touched** | **appears fresh/unretested** |

**Interpretation**: Python marks this OB as "touched" but LuxAlgo appears to show it as fresh.

### Finding B — Missing Blue OB (~69k region)

A blue (bullish) OB was visually observed near the ~69k price region in TradingView screenshots.
This OB is **not present** in the Python active OB list at dataset cutoff.

> ⚠️ Exact prices were not readable from screenshots. This is documented as an unconfirmed discrepancy.

### Finding C — FVG / OB Colour Distinction

| Colour | LuxAlgo Object |
|--------|---------------|
| **BLUE** | Order Block (OB) — subject of this comparison |
| **GREEN** | Fair Value Gap (FVG) — explicitly excluded from all matching |

No green FVG zones were confused with OBs in this investigation.

---

## 4. Current Production Lifecycle Behaviour

Source: [`engine/ob_snapshot_engine.py`](../engine/ob_snapshot_engine.py) `_apply_lifecycle()` (lines 289–401)

```
For each OB:
  state = FRESH
  For each candle C after formation_candle.timestamp:
    If direction == BULLISH:
      if C.low <= OB.upper AND C.high >= OB.lower:
        state = TOUCHED   ← overlaps zone
      if C.low < OB.lower:
        state = INVALIDATED
    If direction == BEARISH:
      if C.low <= OB.upper AND C.high >= OB.lower:
        state = TOUCHED   ← overlaps zone
      if C.high > OB.upper:
        state = INVALIDATED
```

**Key observation**: The lifecycle starts with candles **after** `formation_candle.timestamp`.
The **break candle** (the candle that confirmed the structural break, at `break_candle_index`) is
the **first candle processed**. If the break candle's price range overlaps the OB zone,
the OB is immediately marked `TOUCHED` — even though the break candle *caused* the OB to form,
not a genuine *retest*.

---

## 5. Diagnostic Lifecycle Behaviour

Source: [`engine/generate_phase3e_diagnostics.py`](../engine/generate_phase3e_diagnostics.py) `compute_diagnostic_lifecycle()`

The diagnostic lifecycle applies one additional rule:

> **The break candle (at `break_candle_index`) is NOT counted as a genuine retest.**
> If the break candle overlaps the OB zone, it is recorded as `TOUCHED_BY_BREAK_CANDLE`
> but does NOT advance the state from FRESH → TOUCHED.
> Only candles **after** the break candle count as genuine retests.

All other rules remain identical to production:
- Bullish invalidation: candle.low < OB.lower_price
- Bearish invalidation: candle.high > OB.upper_price
- Touch: price enters zone (post-break candles only in diagnostic mode)

**This is a diagnostic interpretation requiring TradingView manual confirmation.**

---

## 6. Quantified Diagnostic Findings

| Metric | Value |
|--------|-------|
| Total OBs analyzed | 341 |
| OBs where break candle overlaps zone | **99 (29%)** |
| State discrepancies (production vs diagnostic) | **74** |
| Latest OB (2026-08-19 06:00) production state | touched |
| Latest OB (2026-08-19 06:00) diagnostic state | touched |
| Latest OB break candle overlaps zone | False |

> **Note on latest OB**: The specific OB at 2026-08-19 06:00 UTC (upper=64,328, lower=64,138)
> is marked `touched` in **both** production and diagnostic modes. This means a genuine
> post-break retest *did* occur for this OB — the state is not caused by break-candle overlap.
> The TradingView visual discrepancy may instead reflect a **display rendering** difference
> (LuxAlgo may not update OB state in real-time on TradingView Free charts when scrolling).

---

## 7. Root Cause Hypothesis Summary

Three possible causes for production vs LuxAlgo discrepancies:

### 7A. Break-Candle Touch (Confirmed in 99 cases)

**Cause**: Production lifecycle marks OB as TOUCHED immediately if the break candle overlaps the zone.  
**Affected**: 99 out of 341 OBs (29%)  
**Diagnostic fix**: Exclude break candle from touch detection  
**Next step**: Confirm with TradingView manual observation

### 7B. Missing OBs in Python (~69k example)

**Possible causes** (in priority order):
1. LuxAlgo uses a different OB selection candle in the search range
2. ATR volatility parsing inverts candle (high-volatility candle) — OB selected differently
3. Different structure break identification (pivot index selection)
4. LuxAlgo may create OBs from a different structural level (internal vs swing crossover)

**Investigation method**: Fill in `validation/phase3e/tv_ob_manual_reference_template.json`
and run `investigate_missing_ob()`.

### 7C. State Display Difference

**Cause**: TradingView Free may not update OB state in real-time when bar replay is unavailable.  
**Implication**: An OB that Python marks as "touched" may appear "fresh" on TradingView Free
if the retest occurred on a candle not visible in the current chart view.

---

## 8. Blue OB vs Green FVG Distinction

```
BLUE zone  = Order Block (OB)   — compared in this validation
GREEN zone = Fair Value Gap (FVG) — EXCLUDED from all comparison

Never match green FVG zones against Python OBs.
In tv_ob_manual_reference_template.json, set is_fvg=true for any green zone.
```

---

## 9. Differential Matching Methodology

Source: `match_tv_ob_to_python()` in `generate_phase3e_diagnostics.py`

| Match Result | Condition |
|-------------|-----------|
| `EXACT_MATCH` | Direction + price within ±0.5 USD + timestamp match |
| `PRICE_MATCH_TIME_MISMATCH` | Price match but timestamp differs |
| `TIME_MATCH_PRICE_MISMATCH` | Timestamp match but price differs |
| `DIRECTION_MISMATCH` | Direction (bullish/bearish) differs |
| `STRUCTURE_MISMATCH` | Direction/price match but internal vs swing differs |
| `STATE_MISMATCH` | Price/direction match but OB state (fresh/touched) differs |
| `MISSING_IN_PYTHON` | TV blue OB has no match in Python output |
| `EXTRA_IN_PYTHON` | Python OB has no corresponding TV blue OB |
| `AMBIGUOUS_MATCH` | Multiple Python OBs match within 0.5% tolerance |
| `EXCLUDED_FVG` | `is_fvg=true` — green zone excluded |

**Price tolerance**: ±0.5 USD (Delta Exchange India minimum tick)  
**Loose tolerance** (fallback): ±0.5% of price

---

## 10. Generated Files

| File | Description |
|------|-------------|
| `validation/phase3e/ob_lifecycle_trace.csv` | Candle-by-candle trace for 84 selected OBs (9,464 rows) |
| `validation/phase3e/ob_creation_diagnostics.csv` | Production vs diagnostic state for all 341 OBs |
| `validation/phase3e/tv_ob_manual_reference_template.json` | Template to enter TradingView BLUE OB observations |
| `validation/phase3e/differential_results.json` | Full diagnostic summary with statistics |
| `validation/phase3e/README.md` | Directory readme |
| `engine/generate_phase3e_diagnostics.py` | Deterministic diagnostic generator |
| `engine/tests/test_phase3e_ob_diagnostics.py` | 28 regression tests |

---

## 11. Test Results

```
228 passed, 1 skipped
+28 new Phase 3E tests (all pass)
```

Phase 3E tests cover:
1. Formation candle cannot cause incorrect TOUCHED state
2. Break-candle overlap is recorded but NOT treated as genuine retest
3. Genuine post-break retest IS detected as TOUCHED
4. Bullish lower-bound violation → INVALIDATED
5. Bearish upper-bound violation → INVALIDATED
6. Diagnostic lifecycle is deterministic
7. Blue OB references can be matched (exact price)
8. Green FVG zones are NEVER treated as OBs
9. Missing Python OB is correctly classified
10. Price mismatch is correctly classified
11. State mismatch is correctly classified
12. Phase 3D baseline (341 OBs, 36 active) unchanged
13. Dataset row-based SHA-256 unchanged
14. Frozen SMC files exist

---

## 12. Frozen SMC File Verification

```
git diff -- engine/src/quantedge/smc/structure.py
git diff -- engine/src/quantedge/smc/order_blocks.py
git diff -- engine/src/quantedge/smc/volatility.py
```

**Result**: ZERO DIFF — all three production SMC files remain unchanged.

---

## 13. Known Limitations

1. **TradingView Free**: No bar replay. Cannot verify historical OB states precisely.
2. **Screenshot-only evidence**: Missing OB at ~69k not confirmed with exact prices.
3. **State comparison**: Only currently active OBs are visible on TradingView Free.
4. **Display rendering**: TradingView may not show all active OBs if chart is not at the latest bar.

---

## 14. Exact Next Steps Required

### Manual Input Needed from TradingView

1. Open TradingView → Delta Exchange India → BTCUSD.P → 1H
2. Load LuxAlgo SMC with: Swing=50, Internal=5, Filter=ATR, Mitigation=High/Low
3. Hover over BLUE OB boxes near the following price zones and record exact prices:
   - ~64,328 / ~64,138 (is it shown as fresh or touched?)
   - ~69,000 region (blue OB visible in screenshots — what are exact upper/lower?)
   - Any other blue OBs currently visible on the chart
4. Fill in `validation/phase3e/tv_ob_manual_reference_template.json`
5. Run `engine/generate_phase3e_diagnostics.py` again to generate differential results

### After Reference Data is Provided

- Run `compare_snapshot_to_reference()` for matched/mismatched OBs
- If break-candle exclusion from TOUCHED matches LuxAlgo → propose targeted fix to `_apply_lifecycle`
- If missing OBs remain after excluding break candle → investigate `_create_order_block_from_break` search range
- Only then consider production SMC modification (requires separate approval)

---

## PHASE 3E VALIDATION STATUS

| Item | Status |
|------|--------|
| Python OB inventory | ✅ COMPLETE |
| Diagnostic lifecycle layer | ✅ COMPLETE |
| Break-candle overlap analysis | ✅ COMPLETE (99/341 OBs affected) |
| State discrepancy quantification | ✅ COMPLETE (74 discrepancies) |
| FVG / OB distinction | ✅ DOCUMENTED |
| Differential matcher | ✅ COMPLETE |
| Missing OB investigation tool | ✅ COMPLETE |
| TradingView exact validation | ❌ NOT CLAIMED |
| Production SMC modification | 🔒 NOT PERFORMED |
| Phase 4 strategy development | 🔒 NOT STARTED |

> **Phase 3E Status**: `DIAGNOSTIC / PENDING MANUAL TV BLUE OB REFERENCES`

---

*Generated by `engine/generate_phase3e_diagnostics.py`*
*Dataset SHA-256 (row-based): `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`*
