# QuantEdge AI V2 — REAL MARKET VALIDATION REPORT

> [!NOTE]
> **HISTORICAL NON-CANONICAL VALIDATION — PHASE 3A (BINANCE 2024 PROXY DATA)**
> This document records Phase 3A automated replay testing using 2024 Binance proxy data.
> Per the project canonical data policy, **Delta Exchange India BTCUSD (1H)** is the sole
> canonical market-data source for QuantEdge AI V2. This report is preserved for
> historical reproducibility of the Phase 3A historical OB replay pipeline fix.

**Repository commit**: ed192a1 (pre-fix) → Phase 3A fix commit  
**Validation date**: 2026-08-20  
**Status**: PHASE 3A — OB PIPELINE FIXED (HISTORICAL RECORD)  
**Tests**: 159 passed, 1 skipped (133 original + 26 new OB regression tests)

---

## 1. Data Source

| Symbol | Timeframe | Source | Start | End | Candle Count |
|---|---|---|---|---|---|
| BTCUSD.P | 1H | Binance | 2024-01-01T00:00:00+00:00 | 2024-12-31T00:00:00+00:00 | 8761 |
| ETHUSD.P | 1H | Binance | 2024-01-01T00:00:00+00:00 | 2024-12-31T00:00:00+00:00 | 8761 |
| SOLUSD.P | 1H | Binance | 2024-01-01T00:00:00+00:00 | 2024-12-31T00:00:00+00:00 | 8761 |
| XRPUSD.P | 1H | Binance | 2024-01-01T00:00:00+00:00 | 2024-12-31T00:00:00+00:00 | 8761 |

- **Source**: Binance 1H perpetual contract historical data (**NOT Delta Exchange**)
- **Downloaded**: 2026-08-19
- **Schema**: `timestamp,open,high,low,close,volume` (ISO 8601 timestamps, UTC)

> ⚠️ **Data source caveat**: Data is from Binance. The strategy targets Delta Exchange
> perpetual contracts. Spread, funding, and price structure may differ.
> This limitation must remain explicit in all validation reports.

---

## 2. Dataset Hashes

| Symbol | SHA256 | File Path |
|---|---|---|
| BTCUSD.P | `6e3de37bef8969524f08e4eb1edbb359c375278470460bd9192b03f7e4239721` | `data/historical/BTCUSD.P/1h/2024.csv` |
| ETHUSD.P | `adf810cb2135f1eccfc733cfffd59699b999b0268be70475e335ca7993e56040` | `data/historical/ETHUSD.P/1h/2024.csv` |
| SOLUSD.P | `142ca44c9a76ac0c43e8833a51490e2dd22d7069fa309baaffed01db577abb01` | `data/historical/SOLUSD.P/1h/2024.csv` |
| XRPUSD.P | `ff1c73621c3ee5bb37c4a6062cfd236e2da6635eb2f3238da02f112c6d5e47f3` | `data/historical/XRPUSD.P/1h/2024.csv` |

---

## 3. Date Ranges

All symbols: **2024-01-01 00:00:00 UTC** through **2024-12-31 00:00:00 UTC**
- Full year of 1H candles (8761 = 365 × 24 + 1 for leap handling)
- No missing days observed
- Timestamps: ISO 8601, UTC

---

## 4. Data Quality

| Symbol | Candle Count | Duplicates | Gaps | Invalid OHLC | Non-positive Prices | Status |
|---|---|---|---|---|---|---|
| BTCUSD.P | 8761 | 0 | 0 | 0 | 0 | CLEAN |
| ETHUSD.P | 8761 | 0 | 0 | 0 | 0 | CLEAN |
| SOLUSD.P | 8761 | 0 | 0 | 0 | 0 | CLEAN |
| XRPUSD.P | 8761 | 0 | 0 | 0 | 0 | CLEAN |

---

## 5. Phase 3A OB Pipeline Fix

### 5.1 Root Cause: Why OB Count Was Zero

The previous report (commit `ed192a1`) documented OB count = 0 across all 4 symbols.
Investigation (`ob_diagnostic.py`) identified the cause entirely within `replay.py`.
The frozen SMC files were NOT involved.

**Evidence**:

```
Direct call with correct inputs (BTCUSD.P 8761 candles, ATR=200, mult=2.0):
  Internal breaks: 468
  Swing breaks:     53
  Internal OBs:    468   <- OB algorithm works correctly
  Swing OBs:        53   <- OB algorithm works correctly

Replay engine _process_order_blocks() (before fix):
  internal_breaks=[]     <- HARDCODED EMPTY LIST
  swing_breaks=[]        <- HARDCODED EMPTY LIST
  OBs generated: 0       <- CAUSE OF ZERO COUNT
```

### 5.2 Bugs Fixed (all in `replay.py`)

| Bug | Description | Fix |
|---|---|---|
| 1 (PRIMARY) | `internal_breaks=[]`, `swing_breaks=[]` hardcoded | Accumulate breaks in `_all_internal_breaks` / `_all_swing_breaks`; pass live list |
| 2 | `get_confirmed_pivots()` returns only final pair (2 pivots) | Maintain full `_all_internal_pivots_history` / `_all_swing_pivots_history` |
| 3 | OB processing every 100 candles, not per-break | Event-driven: OB processed immediately on each structure break |
| 4 | `run()` + 3 helpers defined twice (dead code) | Removed first (inactive) block |

### 5.3 Frozen SMC Files

The following files were **NOT MODIFIED** during the Phase 3A fix:

```
git diff -- engine/src/quantedge/smc/structure.py    → (empty)
git diff -- engine/src/quantedge/smc/order_blocks.py → (empty)
git diff -- engine/src/quantedge/smc/volatility.py   → (empty)
```

### 5.4 Same-Candle Causality

At break candle N, the OB processor uses:
- `parsed_candles[0 : N+1]` (includes break candle)
- pivot history snapshot as-of candle N (appended before breaks in the processing loop)
- OB source search range is `[pivot_index, break_index)` (excludes break candle)

No future candle data can influence any historical OB.

---

## 6. Replay Methodology

**Pipeline (candle-by-candle)**:

```
raw candle [0..N]
    ↓
volatility parser (ATR=200, mult=2.0)
    ↓
internal SMC detector (length=5)  +  swing SMC detector (length=50)
    ↓
if new pivot high/low:
    → append to _all_internal_pivots_history / _all_swing_pivots_history
if new structure break (BOS/CHOCH):
    → append to _all_internal_breaks / _all_swing_breaks
    → _process_ob_for_break(brk, structure_type, candle_index)
         ├── detect_order_blocks_streaming(
         │       parsed_candles[:N+1],
         │       breaks=[brk],        ← causal: only this break
         │       internal_pivots=_all_internal_pivots_history,
         │       swing_pivots=_all_swing_pivots_history
         │   )
         └── emit ORDER_BLOCK_CREATED event
```

**Configuration**: ATR period=200, ATR multiplier=2.0, internal length=5, swing length=50

---

## 7. Internal Structure Statistics

| Symbol | Internal Leg Changes | Internal Pivots | Internal BOS | Internal CHOCH |
|---|---|---|---|---|
| BTCUSD.P | 1311 | 1311 | 199 | 269 |
| ETHUSD.P | 1269 | 1269 | 215 | 267 |
| SOLUSD.P | 1197 | 1197 | 230 | 235 |
| XRPUSD.P | 1293 | 1293 | 212 | 242 |

*Unchanged from previous report — structure detection was always correct.*

---

## 8. Swing Structure Statistics

| Symbol | Swing Leg Changes | Swing Pivots | Swing BOS | Swing CHOCH |
|---|---|---|---|---|
| BTCUSD.P | 129 | 129 | 30 | 23 |
| ETHUSD.P | 142 | 142 | 27 | 32 |
| SOLUSD.P | 132 | 132 | 30 | 28 |
| XRPUSD.P | 151 | 151 | 19 | 35 |

*Unchanged from previous report — structure detection was always correct.*

---

## 9. Order Block Statistics (Phase 3A — FIXED)

| Symbol | Candles | Internal Breaks | Swing Breaks | Internal OBs | Swing OBs | Bullish | Bearish |
|---|---|---|---|---|---|---|---|
| BTCUSD.P | 8761 | 468 | 53 | 468 | 53 | 280 | 241 |
| ETHUSD.P | 8761 | 482 | 59 | 482 | 59 | 292 | 249 |
| SOLUSD.P | 8761 | 465 | 58 | 465 | 58 | 267 | 256 |
| XRPUSD.P | 8761 | 454 | 54 | 454 | 54 | 271 | 237 |

**Previous result (before fix)**: 0 OBs for all symbols.
**After fix**: Every structure break produces exactly one OB per LuxAlgo semantics.

### 9.1 First 5 OBs — BTCUSD.P (INTERNAL)

| # | Type | FmtIdx | Formation Timestamp | BrkIdx | Top | Bottom |
|---|---|---|---|---|---|---|
| 1 | BEARISH | 57 | 2024-01-03 09:00 | 59 | 45582.30 | 45207.50 |
| 2 | BULLISH | 82 | 2024-01-04 10:00 | 84 | 43123.20 | 42645.10 |
| 3 | BEARISH | 119 | 2024-01-05 23:00 | 126 | 44336.70 | 43964.20 |
| 4 | BULLISH | 146 | 2024-01-07 02:00 | 157 | 44088.00 | 43824.20 |
| 5 | BEARISH | 164 | 2024-01-07 20:00 | 168 | 44262.90 | 44100.00 |

### 9.2 First 3 OBs — BTCUSD.P (SWING)

| # | Type | FmtIdx | Formation Timestamp | BrkIdx | Top | Bottom |
|---|---|---|---|---|---|---|
| 1 | BULLISH | 170 | 2024-01-08 02:00 | 179 | 43793.70 | 43158.10 |
| 2 | BEARISH | 252 | 2024-01-11 12:00 | 286 | 47468.80 | 46939.20 |
| 3 | BEARISH | 381 | 2024-01-16 21:00 | 426 | 43589.00 | 43181.60 |

> **Note**: formation_index < break_index for all OBs — LuxAlgo slice `[pivot, break)` semantics confirmed.

---

## 10. Regression Tests Added (Phase 3A)

New file: `tests/test_ob_pipeline_regression.py`

| Class | Tests | Coverage |
|---|---|---|
| TestBreakAccumulation | 5 | Breaks recorded, count matches stats, type validation, no duplicates |
| TestPivotHistory | 6 | History populated, exceeds 2 entries, count matches stats, PivotPoint type, chronological order, no future pivots |
| TestOBGeneration | 9 | OBs generated when breaks exist, count in list, source range excludes break candle, within parsed slice, valid types, top > bottom, no duplicate OBs, events in stream, required event fields |
| TestOBCausality | 2 | Future candles do not change historical OBs, break candle excluded |
| TestOBDeterminism | 2 | Two runs identical OBs, identical stats |
| TestPivotHistoryCausality | 1 | Pivots appended before breaks |
| TestRawVsParsedInOBPipeline | 2 | Structure uses raw OHLC, OB formation within pivot-break range |
| **Total** | **26 passed, 1 skipped** | (swing skip: synthetic fixture too uniform for swing_length=50) |

---

## 11. Determinism

**Result**: ✅ PASS

Two repeated runs on identical dataset produce identical OB counts, OB boundaries,
and event sequences.

---

## 12. Future-Data Invariance

**Result**: ✅ PASS

Adding candles after index N does not change any OB with `break_index < N`.
Verified by `TestOBCausality::test_future_candles_do_not_change_ob_output`.

---

## 13. Duplicate OB Protection

**Result**: ✅ PASS

Each break produces at most one OB (deduplication key: `(structure_type, break_index)`).
No duplicate OBs observed across any of the 4 symbols.

---

## 14. Raw vs Parsed Separation

**Result**: ✅ VERIFIED

- Structure (pivot, BOS, CHOCH): uses **raw** OHLC
- OB extreme selection: uses **parsed** OHLC (ATR-adjusted)
- Verified by `TestRawVsParsedInOBPipeline`

---

## 15. Manual TradingView Validation

**Status**: ⚠️ NOT PERFORMED

Real-market comparison with TradingView/LuxAlgo indicators has not been performed.

> **Do NOT claim `HISTORICALLY VALIDATED` status until this step is complete.**

**Required comparison**:
- LuxAlgo settings: Internal=ON length=5, Swing=ON length=50, OB filter=ATR, mitigation=High/Low
- 5 representative periods per symbol: bullish trend, bearish trend, structure transition, range, high-vol
- Compare: BOS/CHOCH locations, pivot indices, OB source candles, OB boundaries

---

## 16. Known Discrepancies and Limitations

| Issue | Severity | Status |
|---|---|---|
| Dataset from Binance (not Delta Exchange) | Medium | Explicit caveat maintained |
| No TradingView/LuxAlgo manual comparison | High | Required before HISTORICALLY VALIDATED |
| atr_period documentation error (was "14" in previous report, should be "200") | Low | Corrected — code always uses 200 |

---

## 17. Previous Known Issue — RESOLVED

Previous report (OB section, line 145):
> "OB count = 0 across all symbols. This is a known issue under investigation..."

**Resolution**: Root cause was `internal_breaks=[]`, `swing_breaks=[]` hardcoded in
`replay.py::_process_order_blocks()`. Fixed in Phase 3A.

The note also mentioned "atr_period=14" — this was a documentation copy-paste error.
The running code always used `atr_period=200`. Code correct; documentation corrected.

---

## 18. Certification

```
Tests (original 133):         159 PASSED, 1 SKIPPED (133 original + 26 new)
Real-market datasets:         LOADED & REPLAYED (4/4 symbols)
OB statistics:                BTCUSD 521, ETHUSD 541, SOLUSD 523, XRPUSD 508
Determinism:                  PASS
Future-data invariance:       PASS
Duplicate OB protection:      PASS
Raw-vs-parsed separation:     VERIFIED
Frozen SMC files changed:     NO (structure.py, order_blocks.py, volatility.py unchanged)
TradingView comparison:       NOT PERFORMED

Overall status:               PHASE 3A — OB PIPELINE FIXED
                              TRADINGVIEW VALIDATION PENDING
```