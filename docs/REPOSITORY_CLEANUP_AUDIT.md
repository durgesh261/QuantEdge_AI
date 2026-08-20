# Repository Cleanup & Canonical Data Source Audit

**Document Version:** 1.0  
**Date:** 2026-08-20  
**Target Milestone:** QuantEdge AI V2 — Pre-Phase 4 Cleanup & Canonical Data Migration  
**Canonical Market Target:** **Delta Exchange India — BTCUSD (TradingView: BTCUSD.P) — 1H**  

---

## 1. Executive Summary & Policy Statement

### 1.1 Canonical Data Source Policy
> **Project Rule:**  
> **Delta Exchange India BTCUSD (1H)** is the **ONLY canonical market-data source** for QuantEdge AI V2 validation and strategy development.  
> - **Exchange:** Delta Exchange India (`api.india.delta.exchange`)  
> - **TradingView Symbol:** `BTCUSD.P`  
> - **Delta API Symbol:** `BTCUSD`  
> - **Timeframe:** `1H`  
> - **Canonical Real-Data Period:** 2026-01-01 through 2026-08-20 (5,545 candles, 0 gaps, 0 invalid OHLC)  
> - **Deterministic SHA-256:** `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`  
> 
> Binance BTCUSDT/BTCUSD and Delta Global BTCUSDT data are **non-canonical proxies** used in earlier exploration phases. They must not be used for Phase 4 strategy development, backtesting, or execution workflows.

### 1.2 Frozen Core SMC Algorithm Integrity
The following core engine files remain strictly **FROZEN** with **ZERO DIFF**:
- `engine/src/quantedge/smc/structure.py`
- `engine/src/quantedge/smc/order_blocks.py`
- `engine/src/quantedge/smc/volatility.py`

---

## 2. Comprehensive File Inventory & Classification

Every candidate file/directory across the repository has been evaluated under four classifications:
- **`KEEP`**: Essential production code, canonical dataset, active tests, active documentation, or Phase 3D validation infrastructure.
- **`ARCHIVE`**: Historical validation evidence from Phase 3A/3B/3C moved to `docs/archive/` and explicitly marked *Historical / Non-canonical*.
- **`DELETE`**: Obsolete proxy data, scratch/diagnostic scripts, temporary test outputs, stale validation outputs.
- **`REVIEW`**: Items requiring specific verification before action.

---

### Category A: Market Datasets (`engine/data/historical/`)

| `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` | 5,545 Delta Exchange India 1H candles (2026) | **`KEEP`** | **SOLE CANONICAL DATASET** (SHA-256: `2000fe264d7...`). |
| `data/canonical/delta_exchange_india/BTCUSD/1h/2026_metadata.json` | Canonical metadata + quality report | **`KEEP`** | Required metadata for canonical dataset. |
| `engine/data/historical/BTCUSD.P/1h/2026_delta_india.csv` | Duplicate mirror dataset | **`DELETE`** | Redundant mirror removed to enforce single source of truth. |
| `engine/data/historical/BTCUSD.P/1h/2026_delta_india_metadata.json` | Duplicate mirror metadata | **`DELETE`** | Redundant mirror metadata removed. |
| `engine/data/historical/BTCUSD.P/1h/2024.csv` | 8,784 Binance 1H candles (2024 proxy) | **`DELETE`** | Obsolete 2024 Binance proxy. Replaced by canonical Delta India dataset. |
| `engine/data/historical/BTCUSD.P/1h/2024_metadata.json` | 2024 Binance proxy metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/BTCUSD.P/1h/2026.csv` | 5,544 Binance 1H candles (2026 proxy) | **`DELETE`** | Obsolete 2026 Binance proxy. |
| `engine/data/historical/BTCUSD.P/1h/2026_metadata.json` | 2026 Binance proxy metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/BTCUSDT.P/1h/2026_delta.csv` | 5,545 Delta Global BTCUSDT candles | **`DELETE`** | Delta Global non-India proxy from Phase 3C. Superseded by Delta India BTCUSD. |
| `engine/data/historical/BTCUSDT.P/1h/2026_delta_metadata.json` | Phase 3C metadata | **`DELETE`** | Obsolete proxy metadata. |
| `engine/data/historical/ETHUSD.P/1h/2024.csv` | 8,784 Binance ETH candles (2024) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/ETHUSD.P/1h/2024_metadata.json` | Binance ETH metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/ETHUSD.P/1h/2026.csv` | 5,544 Binance ETH candles (2026) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/ETHUSD.P/1h/2026_metadata.json` | Binance ETH metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/SOLUSD.P/1h/2024.csv` | 8,784 Binance SOL candles (2024) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/SOLUSD.P/1h/2024_metadata.json` | Binance SOL metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/SOLUSD.P/1h/2026.csv` | 5,544 Binance SOL candles (2026) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/SOLUSD.P/1h/2026_metadata.json` | Binance SOL metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/XRPUSD.P/1h/2024.csv` | 8,784 Binance XRP candles (2024) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/XRPUSD.P/1h/2024_metadata.json` | Binance XRP metadata | **`DELETE`** | Obsolete metadata. |
| `engine/data/historical/XRPUSD.P/1h/2026.csv` | 5,544 Binance XRP candles (2026) | **`DELETE`** | Obsolete proxy data. |
| `engine/data/historical/XRPUSD.P/1h/2026_metadata.json` | Binance XRP metadata | **`DELETE`** | Obsolete metadata. |

---

### Category B: Scripts & Tooling (`engine/`)

| Path | Description | Classification | Rationale |
|---|---|---|---|
| `engine/download_delta_india_btcusd.py` | Official Delta Exchange India downloader | **`KEEP`** | Canonical dataset downloader (uses `api.india.delta.exchange`). |
| `engine/ob_snapshot_engine.py` | Causal OB snapshot engine + matching | **`KEEP`** | Core Phase 3D validation engine with future-data invariance. |
| `engine/generate_3d_snapshots.py` | Phase 3D snapshot generator | **`KEEP`** | Generates Python active OB inventories and TV reference templates. |
| `engine/download_2026.py` | Binance 2026 proxy downloader | **`DELETE`** | Obsolete Binance CCXT downloader. |
| `engine/download_delta_btcusdt.py` | Delta Global BTCUSDT downloader | **`DELETE`** | Phase 3C one-off script. Superseded by Delta India downloader. |
| `engine/generate_3b_manifest.py` | Phase 3B Binance proxy manifest generator | **`DELETE`** | Completed Phase 3B artifact generator. |
| `engine/generate_3c_events.py` | Phase 3C Delta BTCUSDT event generator | **`DELETE`** | Completed Phase 3C artifact generator. |
| `engine/generate_comparison_manifest.py` | Old Phase 3A comparison generator | **`DELETE`** | Obsolete Phase 3A script. |
| `engine/ob_diagnostic.py` | Phase 3A OB replay diagnostic script | **`DELETE`** | One-off diagnostic script used to fix historical replay bug. |
| `engine/run_validation.py` | Phase 3A 2024 Binance replay script | **`DELETE`** | Obsolete validation runner for 2024 Binance data. |
| `engine/scripts/download_historical_data.py` | Old Binance CCXT downloader | **`DELETE`** | Hardcoded to Binance futures; obsolete. |
| `engine/check_bytes.py` | Debug helper | **`DELETE`** | Temporary scratch file. |
| `engine/check_docstring.py` | Debug helper | **`DELETE`** | Temporary scratch file. |
| `engine/check_sig.py` | Debug helper | **`DELETE`** | Temporary scratch file. |
| `engine/debug_structure.py` | Debug helper | **`DELETE`** | Temporary scratch file. |
| `engine/design_fixtures.py` | Debug helper | **`DELETE`** | Temporary scratch file. |
| `engine/fix_docstring.py` | One-off fix script | **`DELETE`** | Temporary scratch file. |
| `engine/fix_indent.py` | One-off fix script | **`DELETE`** | Temporary scratch file. |
| `engine/fix_indent2.py` | One-off fix script | **`DELETE`** | Temporary scratch file. |
| `engine/fix_test.py` | One-off fix script | **`DELETE`** | Temporary scratch file. |
| `engine/test_parse.py` | One-off test script | **`DELETE`** | Temporary scratch file. |
| `engine/pytest_output.txt` | Stale pytest log | **`DELETE`** | Temporary scratch file. |
| `engine/det_test/` | Diagnostic test folder | **`DELETE`** | Temporary directory. |
| `engine/fi_test/` | Diagnostic test folder | **`DELETE`** | Temporary directory. |

---

### Category C: Validation Outputs & Artifacts (`validation/` and `engine/`)

| Path | Description | Classification | Rationale |
|---|---|---|---|
| `validation/tradingview_ob_reference/` | Phase 3D active OB reference templates, snapshots, manifest | **`KEEP`** | **Active Phase 3D validation infrastructure**. Required for user TV reference input. |
| `validation/manual/` | Phase 3A Binance 2024 manual validation window CSVs | **`ARCHIVE`** | Historical evidence of Phase 3A manual inspection. Archive to `docs/archive/phase3a_manual/`. |
| `validation/phase3b/` | Phase 3B Binance 2026 proxy comparison window CSVs | **`ARCHIVE`** | Historical evidence of Phase 3B proxy validation. Archive to `docs/archive/phase3b/`. |
| `validation/phase3c/` | Phase 3C Delta Global BTCUSDT window CSVs + summary | **`ARCHIVE`** | Historical evidence of Phase 3C investigation. Archive to `docs/archive/phase3c/`. |
| `engine/tv_val/` | Phase 3B TV validation JSONL/JSON outputs | **`DELETE`** | Obsolete proxy validation output (tracked in git). |
| `engine/validation_output*/` | Phase 3A text logs for 4 pairs | **`DELETE`** | Obsolete generated logs. |

---

### Category D: Tests (`engine/tests/`)

| Path | Description | Classification | Rationale |
|---|---|---|---|
| `engine/tests/test_phase3d_ob_validation.py` | Phase 3D OB snapshot, lifecycle, invariance tests | **`KEEP`** | 14 tests verifying canonical Delta India data and OB snapshot engine. |
| `engine/tests/test_confidence.py` | SMC confidence score tests | **`KEEP`** | Core strategy component tests (22 tests). |
| `engine/tests/test_historical_replay.py` | Causality & determinism tests | **`KEEP`** | Historical replay determinism tests (16 tests). |
| `engine/tests/test_ob_pipeline_regression.py` | Regression tests for OB pipeline fix | **`KEEP`** | Regression tests (25 tests passed, 1 skipped). |
| `engine/tests/test_order_blocks.py` | OB formation tests | **`KEEP`** | Core OB unit tests (8 tests). |
| `engine/tests/test_order_blocks_luxalgo.py` | LuxAlgo OB parity tests | **`KEEP`** | LuxAlgo OB parity tests (16 tests). |
| `engine/tests/test_raw_vs_parsed.py` | Raw vs parsed candle tests | **`KEEP`** | Volatility parser tests (8 tests). |
| `engine/tests/test_strategy.py` | Strategy engine tests | **`KEEP`** | Strategy rules tests (12 tests). |
| `engine/tests/test_structure.py` | SMC structure detector tests | **`KEEP`** | Structure tests (13 tests). |
| `engine/tests/test_structure_luxalgo.py` | LuxAlgo structure parity tests | **`KEEP`** | LuxAlgo structure tests (31 tests). |
| `engine/tests/test_volatility.py` | ATR & volatility parsing tests | **`KEEP`** | Volatility parser tests (7 tests). |
| `engine/tests/conftest.py` | Pytest fixtures | **`KEEP`** | Test configuration and shared fixtures. |
| `engine/tests/fixtures/luxalgo/__init__.py` | LuxAlgo synthetic fixtures | **`KEEP`** | Synthetic test fixtures required for deterministic unit tests. |

---

### Category E: Documentation (`docs/`)

| Path | Description | Classification | Rationale |
|---|---|---|---|
| `docs/PHASE_3D_OB_EXACT_VALIDATION.md` | Active Phase 3D OB validation report | **`KEEP`** | Primary validation document for Delta India BTCUSD. |
| `docs/PHASE_3C_EXACT_DELTA_VALIDATION.md` | Phase 3C Delta Global BTCUSDT report | **`ARCHIVE`** | Move to `docs/archive/` labeled historical/non-canonical. |
| `docs/PHASE_3B_TRADINGVIEW_VALIDATION.md` | Phase 3B Binance proxy report | **`ARCHIVE`** | Move to `docs/archive/` labeled historical/non-canonical. |
| `docs/HISTORICAL_VALIDATION.md` | Phase 3A automated validation report | **`KEEP / UPDATE`** | Update header to clarify canonical status and historical context. |
| `docs/REAL_MARKET_VALIDATION.md` | Phase 3A real market validation report | **`KEEP / UPDATE`** | Update header to clarify canonical status and historical context. |
| `docs/SMC_SPECIFICATION.md` | LuxAlgo SMC technical specification | **`KEEP`** | Essential reference specification. |
| `docs/STRATEGY_SPECIFICATION.md` | QuantEdge strategy specification | **`KEEP`** | Essential strategy reference. |
| `docs/RISK_SPECIFICATION.md` | Risk management specification | **`KEEP`** | Essential risk model reference. |
| `docs/ARCHITECTURE.md` | System architecture document | **`KEEP`** | Architecture overview. |
| `docs/API_SPECIFICATION.md` | Backend API specification | **`KEEP`** | Backend API contract. |
| `docs/DATABASE_SPECIFICATION.md` | PostgreSQL database schema spec | **`KEEP`** | Database schema reference. |
| `docs/SECURITY.md` | Security and authentication spec | **`KEEP`** | Security guidelines. |

---

### Category F: Core Source Code, Backend, Frontend, Infra

| Path | Description | Classification | Rationale |
|---|---|---|---|
| `engine/src/quantedge/**` | Python engine production code | **`KEEP`** | Core system logic. SMC files strictly frozen. |
| `backend/**` | Java / Spring Boot backend | **`KEEP`** | Complete backend implementation. |
| `frontend/**` | React / TypeScript frontend | **`KEEP`** | Complete frontend UI. |
| `docker/**` & `docker-compose.yml` | Container definitions | **`KEEP`** | Deployment infrastructure. |
| `README.md`, `.env.example`, `pyproject.toml` | Root configuration & readme | **`KEEP / UPDATE`** | Ensure canonical data policy is clearly stated in README. |
| `.gitignore` | Git ignore rules | **`KEEP / UPDATE`** | Ensure proper ignoring of cache/temp files while tracking canonical structure. |

---

## 3. Execution Plan

1. **Archive Historical Reports & Manifests**:
   - Create `docs/archive/` directory.
   - Move `docs/PHASE_3B_TRADINGVIEW_VALIDATION.md` → `docs/archive/` (add historical disclaimer).
   - Move `docs/PHASE_3C_EXACT_DELTA_VALIDATION.md` → `docs/archive/` (add historical disclaimer).
   - Move `validation/manual/`, `validation/phase3b/`, `validation/phase3c/` → `docs/archive/validation_history/`.

2. **Delete Obsolete Files & Directories**:
   - Delete obsolete proxy data: `engine/data/historical/BTCUSDT.P`, `ETHUSD.P`, `SOLUSD.P`, `XRPUSD.P`, and `2024.csv`/`2026.csv` in `BTCUSD.P`.
   - Delete scratch/debug scripts: `check_bytes.py`, `check_docstring.py`, `check_sig.py`, `debug_structure.py`, `design_fixtures.py`, `fix_*.py`, `test_parse.py`, `pytest_output.txt`.
   - Delete obsolete proxy tools: `download_2026.py`, `download_delta_btcusdt.py`, `generate_3b_manifest.py`, `generate_3c_events.py`, `generate_comparison_manifest.py`, `ob_diagnostic.py`, `run_validation.py`, `engine/scripts/download_historical_data.py`.
   - Delete generated outputs: `engine/tv_val/`, `engine/validation_output*`, `engine/det_test/`, `engine/fi_test/`.

3. **Establish Single Canonical Dataset Structure**:
   - Store the sole canonical dataset at `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` (and metadata `2026_metadata.json`).
   - Remove redundant duplicate mirror `engine/data/historical/BTCUSD.P/1h/2026_delta_india.csv`.
   - Maintain complete metadata with SHA-256 `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`.


4. **Update Documentation**:
   - Update `README.md`, `engine/README.md`, `docs/HISTORICAL_VALIDATION.md`, and `docs/REAL_MARKET_VALIDATION.md` to state Delta Exchange India as the sole canonical source.

5. **Update `.gitignore`**:
   - Clean up `.gitignore` entries for removed scratch scripts and ensure proper ignore patterns.

6. **Verify Test Suite & Frozen Files**:
   - Run `python -m pytest -q` → confirm 173 passed, 1 skipped, 0 failed.
   - Run `git diff -- engine/src/quantedge/smc/` → confirm ZERO DIFF.
