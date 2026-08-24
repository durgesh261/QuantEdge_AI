# QuantEdge AI — Phase F Multi-Asset Data Quality & Provenance Report

**Generated At**: 2026-08-24 18:25:00 UTC  
**Exchange**: Delta Exchange India (`api.india.delta.exchange` / `cdn.india.deltaex.org`)  
**Cadence**: 1-Hour (`1h`)  
**Data Policy**: 100% Genuine Canonical Historical Market Data. Zero Synthetic Interpolation. Zero Cross-Asset Copying.

---

## 1. Multi-Asset Canonical Inventory

| Asset | Timeframe | Available | Total Rows | First Timestamp | Last Timestamp | Missing Gaps | Duplicates | File Size (Bytes) | Cryptographic SHA-256 | Validation Status |
|---|---|---|---:|---|---|---:|---:|---:|---|---|
| **BTCUSD** | 1h | ✅ YES | 5,583 | 2026-01-01T00:00:00Z | 2026-08-21T14:00:00Z | 0 | 0 | 380,166 | `9774e176db71b367aec26728546a53484b56cbf96f0331209fa324d7b6e5c27b` | `VALIDATED_CLEAN` |
| **ETHUSD** | 1h | ✅ YES | 5,583 | 2026-01-01T00:00:00Z | 2026-08-21T14:00:00Z | 0 | 0 | 342,402 | `8644a3bf853915e412f4881d76d52432b96d643e0d0562c4989cec3f3b5b90ab` | `VALIDATED_CLEAN` |
| **SOLUSD** | 1h | ✅ YES | 5,583 | 2026-01-01T00:00:00Z | 2026-08-21T14:00:00Z | 0 | 0 | 324,426 | `c9ad8c1fc1a0d123d29abb947618840ce13433582f2dc92e50e78b0428cbb86a` | `VALIDATED_CLEAN` |
| **XRPUSD** | 1h | ✅ YES | 5,583 | 2026-01-01T00:00:00Z | 2026-08-21T14:00:00Z | 0 | 0 | 326,799 | `72d2be15477673ec412a41b79da774cbbf63b94440b5bee19c8fd0f3e93bf862` | `VALIDATED_CLEAN` |

---

## 2. Invariant & Integrity Verification

1. **Schema Integrity**: Every dataset strictly adheres to the 6 canonical columns:
   - `timestamp` (ISO 8601 UTC)
   - `open` (Float, strictly $> 0$)
   - `high` (Float, strictly $\ge \max(\text{open}, \text{close})$)
   - `low` (Float, strictly $\le \min(\text{open}, \text{close})$)
   - `close` (Float, strictly $> 0$)
   - `volume` (Float, strictly $\ge 0$)

2. **Temporal Monotonicity**: All timestamps are strictly monotonic increasing without duplicate indices or time inversions.

3. **No Synthetic Pollution**:
   - Zero missing candle forward-fills or linear interpolations.
   - Zero borrowed/mirrored candles from BTCUSD.
   - Every row directly originated from Delta Exchange India public historical endpoint (`/v2/history/candles`).

4. **Cryptographic Manifest**:
   - Stored permanently at [`data/canonical/delta_exchange_india/manifest.json`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/data/canonical/delta_exchange_india/manifest.json).
