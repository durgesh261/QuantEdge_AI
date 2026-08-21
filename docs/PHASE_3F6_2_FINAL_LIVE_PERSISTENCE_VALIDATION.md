# Phase 3F.6.2 Final Live Persistence Validation

## Baseline

Prior to beginning live validation, the initial canonical state and engine configuration were recorded and verified:

- **Canonical CSV Path**: `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`
- **Initial Row Count**: 5,582
- **First Timestamp**: `2026-01-01T00:00:00+00:00`
- **Last Timestamp**: `2026-08-21T13:00:00+00:00`
- **Full File SHA-256**: `c16d4896db74f702a555a8d697bcba13a45742f2fdb4343591658aef525ffbb1`
- **Metadata SHA-256**: `c16d4896db74f702a555a8d697bcba13a45742f2fdb4343591658aef525ffbb1`
- **Historical Baseline Rows**: 5,545 (`2026-01-01T00:00:00Z` to `2026-08-20T00:00:00Z`)
- **Historical Slice SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`
- **Gap Count**: 0 (zero gaps across entire 2026 dataset)
- **Invalid OHLC Count**: 0
- **Engine Baseline State**: 343 total OBs, initialized up to `1787317200` (`2026-08-21T13:00:00+00:00`)

---

## Real Delta WebSocket

A real-time connection was established to the verified Delta Exchange India WebSocket endpoint:

- **Endpoint**: `wss://socket.india.delta.exchange`
- **Subscription Channel**: `candlestick_1h`
- **Symbol**: `BTCUSD`
- **Channel Message Format Verified**:
  ```json
  {
    "type": "candlestick_1h",
    "symbol": "BTCUSD",
    "resolution": "1h",
    "open": 77681.0,
    "high": 77841.0,
    "low": 77650.0,
    "close": 77834.5,
    "volume": 32800.0,
    "candle_start_time": 1787324400000000,
    "timestamp": 1787325254000000
  }
  ```
- **Timestamp Conversion**: `candle_start_time // 1_000_000` -> `1787324400` (`2026-08-21T15:00:00+00:00`).

---

## Forming Candle

The real-time forming candle for the current hour (`2026-08-21T15:00:00+00:00`, timestamp `1787324400`) was evaluated against the boundary conditions:

- **`_is_candle_closed(1787324400)`**: Evaluated to `False` (`1787324400 < 1787324400` is `False`).
- **Persistence Prevention**: `DeltaWebSocketClient._handle_message()` logged `CANDLE_FORMING` and returned immediately.
- **CSV Invariance**: Row count remained strictly unchanged (5,582).
- **Engine Invariance**: `IncrementalSMCEngine` was not invoked.
- **Deduplication Set**: `1787324400` was not added to `processed_timestamps`.

---

## Real Closed Candle

The latest confirmed closed 1H candle (`2026-08-21T14:00:00+00:00`, timestamp `1787320800`), which fully closed at `15:00 UTC`, was received and validated:

- **Candle Start Time**: `2026-08-21T14:00:00+00:00` (`1787320800`)
- **Open**: `77258.5`
- **High**: `77458.0`
- **Low**: `76547.0`
- **Close**: `77396.0`
- **Volume**: `1660010.0`
- **Closed Condition**: `1787320800 < 1787324400` (`True`)
- **Execution Order Proven**:
  1. Parse message (`candle_start_time // 1_000_000`)
  2. Closed check (`_is_candle_closed` -> `True`)
  3. OHLCV validation (`validate_candle_ohlcv` -> passed)
  4. Year partition guard (`validate_candle_year` -> passed, year=2026)
  5. Deduplication check (`ts not in processed_timestamps` -> passed)
  6. Persistence (`upsert_closed_candles` -> atomic write success)
  7. Processed timestamps registration (`processed_timestamps.add(1787320800)`)
  8. Incremental SMC Engine (`engine.process_new_candles` -> processed 1)

---

## Persistence

Following receipt of the confirmed closed candle, the canonical storage was updated:

- **Row Count Progression**: `5582` -> `5583` (+1 row)
- **Last Timestamp Progression**: `2026-08-21T13:00:00+00:00` -> `2026-08-21T14:00:00+00:00`
- **New Full File SHA-256**: `0f11b0a482223afecbb378aac79f1509beb34a4de0eb653a12d29782acc9e3ac`
- **Metadata SHA-256**: `0f11b0a482223afecbb378aac79f1509beb34a4de0eb653a12d29782acc9e3ac` (100% matched)
- **Gap Count**: **0** (strictly contiguous)
- **Invalid OHLC**: **0**
- **Chronological Sort**: Strictly ascending by timestamp.

---

## Engine Processing

The confirmed closed candle was processed by the live `IncrementalSMCEngine`:

- **Before `last_processed_ts`**: `1787317200` (`2026-08-21T13:00:00+00:00`)
- **After `last_processed_ts`**: `1787320800` (`2026-08-21T14:00:00+00:00`)
- **Processed Count**: 1
- **Total OBs**: 343
- **Active OBs**: 343 (with updated touch and invalidation lifecycle state)

---

## Duplicate Protection

The identical closed candle message (`1787320800`) was submitted a second time to test idempotency:

- **Event Logged**: `CANDLE_DUPLICATE | candle_ts=1787320800`
- **CSV Row Count**: Remained strictly 5,583 (0 inserts, 0 updates)
- **Engine Invocations**: 0
- **Duplicate Protection**: Verified 100% idempotent.

---

## Persistence Failure

Transactional safety was verified under simulated disk write failure (`upsert_closed_candles` raising `OSError`):

- **Engine Invocations on Failure**: 0 (processing blocked before reaching engine)
- **`processed_timestamps` on Failure**: Timestamp not registered
- **CSV State on Failure**: Unmodified
- **Retry Mechanism**: After restoring persistence, the candle was resubmitted and successfully persisted and processed.

---

## Year Boundary

Hard year-partition boundaries were verified against historical test fixtures:

- **2025 Synthetic Candle (`2025-06-01T00:00:00Z`)**: Rejected with `ValueError: Year partition guard: timestamp ... does not belong to 2026.csv`.
- **2024 Synthetic Candle (`2024-12-23T16:00:00Z`)**: Rejected with `ValueError: Year partition guard: timestamp ... does not belong to 2026.csv`.
- **Storage & Engine**: Neither candle was persisted or passed to the SMC engine.

---

## Restart Recovery

The system's cold-restart capability was validated:

- **Procedure**: The live engine was halted, and a brand new `IncrementalSMCEngine` instance was instantiated from `2026.csv`.
- **Initialization `last_processed_ts`**: `1787320800` (`2026-08-21T14:00:00+00:00`)
- **Total OBs on Restart**: 343 (exact match)
- **Duplicate OBs**: 0
- **Reprocessing Required**: None; initialized cleanly from persisted state.

---

## REST Backfill

Forward-only backfill boundary enforcement was validated:

- **Query Range**: When `last_closed_ts = 1787320800` (`14:00 UTC`), `_backfill_gaps` queried `start_ts = 1787324400` (`15:00 UTC`).
- **Boundary Clamping**: Backfill never queries prior to `MIN_CANONICAL_YEAR_START_TS` (`2026-01-01T00:00:00Z`).
- **Year Partition**: All fetched candles must strictly belong to 2026.

---

## Historical Integrity

The original historical baseline dataset was re-audited and verified byte-for-byte:

- **Historical Slice**: `2026-01-01T00:00:00+00:00` through `2026-08-20T00:00:00+00:00`
- **Historical Rows**: **5,545** (exact)
- **Historical Row-Based SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`
- **Status**: 100% intact and immutable.

---

## Frozen SMC

Verification that core SMC files were not modified:

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# (Output: EMPTY — ZERO DIFF)
```

- `engine/src/quantedge/smc/structure.py` — **ZERO DIFF**
- `engine/src/quantedge/smc/order_blocks.py` — **ZERO DIFF**
- `engine/src/quantedge/smc/volatility.py` — **ZERO DIFF**

---

## Test Results

Full repository automated test execution:

```text
======================= 424 passed, 1 skipped in 20.66s =======================
```

- **Total Test Count**: 425
- **Passed**: 424 (100% passing)
- **Skipped**: 1 (`test_ob_pipeline_regression.py` marked for TV reference sync)
- **Failed**: 0

---

## Repository Cleanliness

- All scratch test scripts were maintained outside the repository (`C:\Users\durge\.gemini\antigravity-ide\brain\a51a2593-1d3b-4cc8-9827-d0fd541e9bc9\scratch/`).
- `git status --short` shows only the canonical market dataset and metadata updates.
- No temporary CSV, JSON, or debug files remain in the repository.

---

## Final Verdict

# `LIVE_PERSISTENCE_VALIDATED`
