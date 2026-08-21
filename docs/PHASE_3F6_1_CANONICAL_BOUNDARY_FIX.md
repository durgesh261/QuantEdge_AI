# Phase 3F.6.1 — Canonical Dataset Boundary Fix & Invariant Hardening Report

## Status: COMPLETE
## Final Verdict: `CANONICAL_BOUNDARY_FIXED`

---

## 1. Executive Summary & Root Cause Confirmation

During the Phase 3F.6 live continuous run, an unexpected dataset expansion was detected where rows from 2024 and 2025 were added to `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`.

### Forensic Findings
1. **Accidental Test Mutation**: A synthetic test fixture (`2024-12-23T16:00:00Z`) was upserted into `CANONICAL_CSV` because `DeltaWebSocketClient` defaulted to `persist=True` with default `csv_path=CANONICAL_CSV`.
2. **Reverse Pagination Expansion**: When `DeltaWebSocketClient._backfill_gaps` or `fetch_closed_candles` triggered, the Delta Exchange REST API reverse-paginated from the 2026 cutoff back to the 2024 synthetic candle, retrieving 8,770 historical candles across 2024 and 2025.
3. **Historical Data Integrity**: The original 5,545 candles from `2026-01-01T00:00:00Z` to `2026-08-20T00:00:00Z` remained 100% byte-for-byte and row-for-row uncorrupted inside the file.

---

## 2. Canonical Dataset Repair & Verification

`2026.csv` has been repaired to contain only valid 2026 data.

### Dataset Profile
| Metric | Value |
| :--- | :--- |
| **File Path** | `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` |
| **Total Rows** | 5,582 |
| **First Timestamp** | `2026-01-01T00:00:00+00:00` |
| **Last Timestamp** | `2026-08-21T13:00:00+00:00` (live verified closed candle) |
| **Gap Count** | **0** (strictly zero gaps across entire 2026 range) |
| **Invalid OHLC Count** | **0** |
| **Full File SHA-256** | `c16d4896db74f702a555a8d697bcba13a45742f2fdb4343591658aef525ffbb1` |

### Historical Baseline Invariance
| Metric | Historical Baseline Slice (`2026-01-01` to `2026-08-20`) |
| :--- | :--- |
| **Row Count** | **5,545** (exact) |
| **Row-Based SHA-256** | `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b` (verified immutable) |
| **Total Order Blocks** | **341** |
| **Active Order Blocks** | **41** (corrected lifecycle) |
| **Invalidated Order Blocks** | **300** |

---

## 3. Architecture Hardening & Year Partition Guard

To permanently prevent pre-2026 or cross-year data from entering `2026.csv`, four independent security layers were implemented:

### Layer 1: Year Partition Guard (`validate_candle_year`)
- `engine/src/quantedge/market_data/ingestion.py` implements `validate_candle_year(candle, csv_path=None, target_year=None)`.
- If `csv_path` is named `YYYY.csv` (e.g. `2026.csv`), any candle with `timestamp.year != YYYY` is rejected with `ValueError`.
- `upsert_closed_candles()` enforces `validate_candle_year()` on every candle before acquisition or atomic disk write.

### Layer 2: Forward-Only REST Pagination & Clamping
- `MIN_CANONICAL_YEAR_START_TS = 1767225600` (`2026-01-01 00:00:00 UTC`).
- `fetch_closed_candles(start_ts, end_ts)` clamps `effective_start = max(start_ts, MIN_CANONICAL_YEAR_START_TS)`.
- Reverse pagination stops immediately when the oldest batch reaches `MIN_CANONICAL_YEAR_START_TS`.
- All candles outside the target calendar year are filtered out.

### Layer 3: WebSocket Client Invariant Enforcement
- `DeltaWebSocketClient._handle_message()` runs `validate_candle_year()` before persistence and incremental engine processing.
- `DeltaWebSocketClient._backfill_gaps()` filters backfilled candles through `validate_candle_year()`.

### Layer 4: Test Isolation & Production CSV Safety
- `DeltaExchangeIngestionService` uses `self.csv_path` and `self.meta_path` for clean test isolation with `tmp_path`.
- All unit tests instantiating `DeltaWebSocketClient` explicitly set `persist=False` or provide isolated temporary directory paths.

---

## 4. Test Verification Summary

### Comprehensive Test Results
- **Total Tests**: 425
- **Passed**: 424
- **Skipped**: 1 (`test_ob_pipeline_regression.py` marked for TV reference sync)
- **Failed**: 0
- **Pass Rate**: **100%**

### Key Test Suites Validated
1. `test_phase3f61_canonical_boundary.py` (12/12 PASSED)
   - 2024/2025 candles rejected by `2026.csv`
   - 2026 candles accepted
   - Forward-only backfill starting at `max(existing) + 1h`
   - REST backfill bounded by `MIN_CANONICAL_YEAR_START_TS`
   - Test isolation: fixtures cannot mutate `CANONICAL_CSV`
   - Deduplication idempotency and in-place revision semantics
   - Immutable historical slice SHA check
2. `test_phase3d_ob_validation.py` (15/15 PASSED)
   - Restored `gap_count == 0`
   - Restored snapshot counts (S1: 50 OBs, S4: 314 OBs, S5: 341 OBs)
   - Verified `test_historical_baseline_slice_sha`
3. `test_phase3e_ob_diagnostics.py` (27/27 PASSED)
   - Restored total OBs = 341, active OBs = 41
   - Restored 341 diagnostic records in `test_diag_has_341_records`
   - Restored `test_diff_json_total_obs == 341`
4. `test_phase3f_runtime_fix.py` (63/63 PASSED)
5. `test_phase3f3_websocket.py` (18/18 PASSED)
6. `test_phase3f5_persistence.py` (60/60 PASSED)
7. `test_phase3f6_continuous_live_validation.py` (41/41 PASSED)

---

## 5. Frozen SMC Files Zero Diff Verification

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# (Output is EMPTY — ZERO DIFF)
```

- `engine/src/quantedge/smc/structure.py` — **UNMODIFIED** (ZERO DIFF)
- `engine/src/quantedge/smc/order_blocks.py` — **UNMODIFIED** (ZERO DIFF)
- `engine/src/quantedge/smc/volatility.py` — **UNMODIFIED** (ZERO DIFF)
