# Phase 3F.6 Data Integrity Audit

**Audit Date**: 2026-08-21  
**Repository**: QuantEdge AI (https://github.com/durgesh261/QuantEdge_AI.git)  
**Target File**: `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`  
**Current Test Status**: 411 passed, 1 skipped, 0 failed  
**Frozen SMC Diff**: **ZERO DIFF** (byte-for-byte identical to baseline)

---

## 1. Original Canonical Dataset

The canonical dataset established in commit `b8095dc` (Phase 3D/3E baseline) was:

- **Path**: `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`
- **Total Candles**: 5,545
- **First Timestamp**: `2026-01-01T00:00:00+00:00` (Unix `1767225600`)
- **Last Timestamp**: `2026-08-20T00:00:00+00:00` (Unix `1787184000`)
- **Row-based SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`
- **Gaps**: 0
- **Invalid OHLC Bars**: 0
- **Duplicates**: 0
- **Sorting**: Strictly ascending by timestamp

---

## 2. Current Canonical Dataset

Audit of the active `2026.csv` in the repository:

- **Total Rows**: 14,352
- **First Timestamp**: `2024-12-23T16:00:00+00:00` (Unix `1734969600`)
- **Last Timestamp**: `2026-08-21T13:00:00+00:00` (Unix `1787317200`)
- **Duplicates**: 0
- **Sorting Violations**: 0
- **Gap Count**: 1 gap detected
  - **Gap Interval**: `2024-12-23T16:00:00+00:00` -> `2024-12-31T15:00:00+00:00` (191.0 hours)
- **Breakdown of Rows**:
  - **Pre-2026 (2024/2025)**: 8,770 rows
  - **Original 2026 Baseline (2026-01-01 to 2026-08-20)**: 5,545 rows
  - **Live Post-Cutoff Candles (2026-08-20 01:00 to 2026-08-21 13:00)**: 37 rows

---

## 3. Git History

Git log inspection across `data/canonical/`:

```bash
git log --stat --oneline --all -- data/canonical/
```

| Commit | Date | Author | Action & Impact |
|---|---|---|---|
| `b8095dc` | Thu Aug 20 15:52:36 2026 | Durgesh | Initial canonical dataset created: 5,545 rows, SHA `2000fe264d7a...` |
| `24823a5` | Fri Aug 21 00:32:00 2026 | Durgesh | Metadata deduplication update |
| `4a8f2d8` | Fri Aug 21 02:44:00 2026 | Durgesh | Phase 3F.1 ingestion layer metadata update |
| `2ce6742` | Fri Aug 21 18:54:25 2026 | Durgesh | **Phase 3F.5 commit — added 8,806 rows to `2026.csv`** (expanded from 5,545 to 14,351 rows) |
| `d3610dd` | Fri Aug 21 19:46:24 2026 | Durgesh | Phase 3F.6 — appended 1 live closed candle (`2026-08-21T14:00:00`), total rows = 14,352 |

**Commit causing expansion**: `2ce6742bf9108749b5a64f483c049b02605b50a9` (Phase 3F.5).

---

## 4. Cause of Historical Expansion

### Mechanism Identified

1. **Inadvertent Test/Script Upsert**: During test fixture development for Phase 3F runtime fixes, a synthetic mock candle with timestamp `1734969600` (`2024-12-23T16:00:00`, price `50000.0`, high `50100.0`, low `49900.0`, close `50050.0`, volume `1.5`) was upserted into `CANONICAL_CSV` because default parameters in `DeltaWebSocketClient(csv_path=None)` point to `CANONICAL_CSV`.
2. **Reverse Pagination in `fetch_closed_candles()`**: In `ingestion.py`, `fetch_closed_candles(start_ts, end_ts)` starts at `cursor_end = min(end_ts, current_hour_start)` and paginates backwards using `cursor_end = oldest_ts - 1` until `cursor_end <= start_ts`.
3. **Unbounded Historical Fetch**: When incremental ingestion / REST backfill evaluated `start_ts = min(existing_candles)` (or used `start_ts = 1734969600`), the REST client fetched real Delta Exchange India BTCUSD 1H historical candles backwards across all of 2025 and late 2024 (8,769 real exchange candles).
4. **Missing Year Boundary Guard**: `upsert_closed_candles()` did not validate whether incoming candles belong to the `2026` calendar year partition corresponding to the filename `2026.csv`. It sorted and atomically persisted all 8,770 historical candles into `2026.csv`.

---

## 5. 2026 Historical Preservation

To verify whether the original historical data was modified or corrupted, we isolated the exact range `2026-01-01T00:00:00+00:00` through `2026-08-20T00:00:00+00:00` from the current CSV and compared it with the historical baseline:

- **Row Count**: Exactly **5,545 rows** (100% match)
- **First Timestamp**: `2026-01-01T00:00:00+00:00` (100% match)
- **Last Timestamp**: `2026-08-20T00:00:00+00:00` (100% match)
- **Row-based SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b` (**100% match, byte-for-byte identical**)
- **Ordering**: Strictly ascending, zero sorting violations
- **OHLCV Integrity**: Zero modified prices, zero invalid bars

> **Conclusion**: The original 5,545 historical candles were **100% preserved and untouched**. No historical 2026 price or volume data was corrupted or altered.

---

## 6. Gap Analysis

- **The 191-hour gap** (`2024-12-23T16:00:00` to `2024-12-31T15:00:00`) is **Classification C (Accidental Partial Backfill + Synthetic Artifact)**:
  - Row 0 (`2024-12-23T16:00:00`) is the synthetic test fixture (`O:50000, H:50100, L:49900, C:50050, V:1.5`).
  - Rows 1..8770 are real Delta Exchange India historical data starting from `2024-12-31T15:00:00` to `2025-12-31T23:00:00`.
  - The gap between `2024-12-23` and `2024-12-31` arose because the test fixture timestamp was 8 days prior to the earliest candle returned in that REST pagination batch.
- **2026 Data Continuity**: Within the intended 2026 dataset (2026-01-01 to present), there are **0 gaps** and **0 missing candles**.

---

## 7. Backfill & Upsert Analysis

### Code Review

1. **`ingestion.py` → `upsert_closed_candles()`**:
   - Correctly enforces OHLCV validity, timestamp deduplication, and atomic `.tmp` → `os.replace()`.
   - **Defect**: Lacks partition validation. It does not verify that candle timestamps fall within the year indicated by the target CSV filename (e.g., `2026.csv` should accept timestamps in `[2026-01-01 00:00:00, 2027-01-01 00:00:00)`).
2. **`delta_websocket.py` → `_backfill_gaps()`**:
   - Correctly computes `start_ts = self.last_closed_ts + 3600` and `end_ts = now_ts`.
   - During live running with initialized engine, it only requests forward missing candles.
3. **`ingestion.py` → `run_incremental_ingestion()`**:
   - Computes `start_ts = max(existing_candles.keys())` if candles exist, or `2026-01-01` if empty.
   - This forward boundary is safe.

---

## 8. Test Baseline Audit

Review of modifications made in Phase 3F.6:

| Test Assertion Modified | Previous State | Modified State | Audit Classification | Analysis |
|---|---|---|---|---|
| `test_delta_india_btcusd_data_quality` (`gap_count`) | `assert meta["gap_count"] == 0` | `assert meta["gap_count"] <= 1` | **Class C: Weakened Assertion** | Accommodated the unexpected 2024 pre-dataset gap. For a pure 2026 dataset, `gap_count == 0` must hold. |
| `test_delta_india_btcusd_data_quality` (`sha256`) | Hardcoded `2000fe264d7a...` | `assert meta["sha256"] == h_check.hexdigest()` | **Class A: Legitimate Dynamic Integrity Check** | For live operation, appending candles hourly changes the whole-file SHA; verifying `meta.sha256 == computed` is correct for file integrity. |
| `test_delta_india_btcusd_data_quality` (`historical preservation`) | Implicit in whole-file check | Missing explicit 2026-only slice check | **Class D: Masking Missing Historical Test** | An explicit test verifying the immutable 2026-01-01 to 2026-08-20 slice equals `2000fe264d7a...` is required. |
| `test_phase3d_snapshot_counts_unchanged` | `snap.all_count == 341` | `snap.all_count == 851` | **Class C: Weakened to match expanded CSV** | 851 was caused by feeding 14K candles (2024-2026) to the snapshot engine. Feeding only the 2026 baseline produces exactly 341. |
| `test_diag_has_341_records` | `assert len(diag_rows) == 341` | `assert len(diag_rows) == 851` | **Class C: Weakened to match expanded CSV** | Diagnostic calculations on 2026 baseline produce exactly 341 records. |

---

## 9. OB Baseline Audit (341 vs 851)

We executed the `OBSnapshotEngine` specifically on the isolated 2026 baseline slice (`2026-01-01T00:00:00` to `2026-08-20T00:00:00`):

| Metric | Full 14K Dataset (2024-2026) | 2026-Only Dataset (5,545 candles) | Original Baseline | Match? |
|---|---|---|---|---|
| **Candles Processed** | 14,315 | **5,545** | 5,545 | ✅ EXACT |
| **Total OBs Formed** | 851 | **341** | 341 | ✅ EXACT |
| **Active OBs (Lifecycle Corrected)** | 55 | **41** | 41 | ✅ EXACT |
| **S1 Snapshot (2026-02-09)** | 560 all / 32 active | **50 all / 18 active / 32 inv** | 50 all / 18 active / 32 inv | ✅ EXACT |
| **S4 Snapshot (2026-07-31)** | 824 all / 57 active | **314 all / 43 active / 271 inv** | 314 all / 43 active / 271 inv | ✅ EXACT |
| **S5 Snapshot (2026-08-19)** | 851 all / 58 active | **341 all / 44 active / 297 inv** | 341 all / 44 active / 297 inv | ✅ EXACT |

> **Conclusion**: The SMC algorithm and OB detection engine are **100% deterministic and intact**. The increase from 341 to 851 was solely caused by running the detector on 14,315 candles (including 2024 and 2025) instead of 5,545 candles.

---

## 10. Frozen SMC Verification

Verification of frozen SMC core production files:

```bash
git diff -- engine/src/quantedge/smc/structure.py \
            engine/src/quantedge/smc/order_blocks.py \
            engine/src/quantedge/smc/volatility.py
# Output: EMPTY (ZERO DIFF)
```

Also compared against the initial canonical commit `b8095dc`:
- `structure.py`: **ZERO DIFF**
- `order_blocks.py`: **ZERO DIFF**
- `volatility.py`: **ZERO DIFF**

The SMC algorithms remain byte-for-byte unchanged.

---

## 11. Multi-Year Architecture & Data Partition Contract

### Documented Contract

Per project architecture documentation:
- The path format `data/canonical/delta_exchange_india/BTCUSD/1h/<year>.csv` defines **yearly partition files**.
- `2026.csv` is explicitly intended to hold candles for the **2026 calendar year** (`2026-01-01T00:00:00+00:00` onward).
- Candles from 2024 belong in `2024.csv`.
- Candles from 2025 belong in `2025.csv`.
- Live closed candles during 2026 append to `2026.csv`.

---

## 12. Test Results

Full test suite execution:

```
collected 412 items
======================= 411 passed, 1 skipped in 40.02s =======================
```

- `test_confidence.py`: 22 passed
- `test_historical_replay.py`: 16 passed
- `test_ob_pipeline_regression.py`: 25 passed, 1 skipped
- `test_order_blocks.py`: 8 passed
- `test_order_blocks_luxalgo.py`: 16 passed
- `test_phase3d_ob_validation.py`: 14 passed
- `test_phase3e_ob_diagnostics.py`: 27 passed
- `test_phase3f1_ingestion.py`: 25 passed
- `test_phase3f2_incremental_equivalence.py`: 4 passed
- `test_phase3f3_websocket.py`: 18 passed
- `test_phase3f5_persistence.py`: 60 passed
- `test_phase3f6_continuous_live_validation.py`: 41 passed
- `test_phase3f_runtime_fix.py`: 63 passed
- `test_raw_vs_parsed.py`: 8 passed
- `test_strategy.py`: 12 passed
- `test_structure.py`: 13 passed
- `test_structure_luxalgo.py`: 31 passed
- `test_volatility.py`: 7 passed

---

## 13. Findings & Root Causes

1. **Original 2026 Data is 100% Uncorrupted**: The 5,545 baseline historical candles from 2026-01-01 to 2026-08-20 are byte-for-byte identical to the original canonical SHA `2000fe264d7a...`.
2. **Pre-2026 Data in `2026.csv` is Unintended**: 8,770 candles from 2024/2025 were added during Phase 3F.5 due to a missing year-partition validation guard during backfill/upsert.
3. **OB Baseline 341 is Proven**: Replaying the 2026 baseline reproduces exactly 341 OBs, 41 active OBs, and all Phase 3D snapshot counts.
4. **Live Pipeline Works Correctly**: Real WebSocket ingestion, atomic persistence, deduplication, restart recovery, and SMC event emission are fully operational.
5. **Year Partition Boundary Guard Missing**: `upsert_closed_candles()` needs year-boundary enforcement to prevent pre-2026 or cross-year candles from entering `2026.csv`.

---

## 14. Required Changes (Plan for Remediation)

When authorized to make production data and test corrections:

1. **Restore Pure `2026.csv`**:
   - Prune pre-2026 rows (`< 2026-01-01T00:00:00+00:00`) from `2026.csv`.
   - Retain the original 5,545 baseline candles + live closed candles from 2026-08-20 onward.
   - If 2024/2025 data is desired for multi-year historical backtesting, store them in `2024.csv` and `2025.csv`.
2. **Add Year Partition Boundary to `upsert_closed_candles()`**:
   - Enforce that candles upserted to `YYYY.csv` have timestamps belonging to calendar year `YYYY`.
3. **Restore Strict Test Assertions**:
   - Restore `gap_count == 0` in `test_phase3d_ob_validation.py` (since 2026 data has 0 gaps).
   - Restore baseline snapshot tests (341 OBs, S1=50, S4=314, S5=341) on the 2026 canonical dataset.
   - Add explicit historical preservation test verifying SHA of the `[2026-01-01, 2026-08-20]` slice is always `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`.

---

## 15. Final Verdict

# `DATASET_INTEGRITY_REQUIRES_FIX`

**Reasoning**:
- The core algorithm and original 2026 historical data are 100% sound, verified, and uncorrupted.
- The expansion of `2026.csv` to include 2024/2025 data was an accidental ingestion artifact rather than an intended design change.
- Restoring `2026.csv` to pure 2026 data + adding year boundary guards will cleanly align production data with the canonical data contract without weakening test assertions.
