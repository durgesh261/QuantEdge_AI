# QuantEdge AI — Phase F Multi-Asset AI Research & Second Promotion Gate Report

**Generated At**: 2026-08-24 18:24:49 UTC  
**Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**  
**Frozen Validation Threshold**: `pred_realized_r >= +0.50R`  

---

## 1. Executive Summary

> [!WARNING]
> **Authoritative Promotion Status**: **`AI_PROMOTION_STATUS = REJECTED`**
> The AI model was evaluated across 4 canonical real-market datasets (BTCUSD, ETHUSD, SOLUSD, XRPUSD), structural clustering, candidate architectures, leave-one-asset-out cross-asset generalization, regime profiles, and out-of-sample tests.
> In accordance with safety invariants, live trade execution authority remains strictly protected and governed by the deterministic SMC engine.

### Promotion Gate Rejection Reasons:
- ❌ `Pooled Moving Block Bootstrap 95% CI lower bound (-0.3239R) is not strictly positive.`
- ❌ `Cross-asset generalization failed: only 1/4 held-out assets demonstrated positive incremental expectancy.`

---

## 2. Multi-Asset Canonical Data Availability & Provenance Audit

| Symbol | Timeframe | Available | Total Candles | Date Range | Gaps / Dups | Usability Status | SHA-256 |
|---|---|---|---:|---|---:|---|---|
| **BTCUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 → 2026-08-21 | 0 / 0 | `AVAILABLE` | `9774e176db71b367...` |
| **ETHUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 → 2026-08-21 | 0 / 0 | `AVAILABLE` | `8644a3bf853915e4...` |
| **SOLUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 → 2026-08-21 | 0 / 0 | `AVAILABLE` | `c9ad8c1fc1a0d123...` |
| **XRPUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 → 2026-08-21 | 0 / 0 | `AVAILABLE` | `72d2be15477673ec...` |

### Setup Counts per Asset:
- **BTCUSD**: 334 qualified SMC trade setups
- **ETHUSD**: 356 qualified SMC trade setups
- **SOLUSD**: 430 qualified SMC trade setups
- **XRPUSD**: 381 qualified SMC trade setups

---

## 3. Structural Setup Clustering & Correlation Audit (Pooled)

- **Total Raw Setups**: 1501
- **Clustered within $\le 3$ Hours**: 560 (37.3%)
- **Unique Structural Events**: 941
- **Mean Cluster Size**: 1.6
- **Max Cluster Size**: 17

---

## 4. Multi-Model Candidate Comparison (Pooled Validation Split)

| Candidate Architecture | Val Realized R² | Val MAE | Val Expectancy | Val PF | Val Win Rate | Val Coverage | Fitness Score |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Ridge_Linear** | `-0.0839` | 1.2890 | `-0.1180R` | 0.831 | 29.7% | 62.2% | `-0.0931` |
| **Random_Forest_Base** | `-0.3588` | 1.4025 | `-0.3230R` | 0.580 | 22.3% | 55.8% | `-0.2413` |
| **Extra_Trees_Regularized** | `-0.1658` | 1.3430 | `-0.1269R` | 0.820 | 28.9% | 60.9% | `-0.0990` |
| **Hist_Gradient_Boosting** | `-0.2718` | 1.3435 | `-0.1577R` | 0.779 | 27.8% | 57.1% | `-0.1192` |

- **Best Hyperparameters (Random Forest)**: `{"max_depth": 4, "min_samples_leaf": 5, "max_features": 0.5, "n_estimators": 100}`

---

## 5. Pooled Out-of-Sample Benchmark: SMC vs SMC + AI

### Final Pooled Out-of-Sample Test Split (Untouched Evaluation)

| Metric | SMC Only | SMC + AI | Change / Impact |
|---|---:|---:|---:|
| **Total Setups** | 320 | 320 | — |
| **Executed / Eligible Setups** | 320 | 41 | `12.8% coverage` |
| **Win Rate** | 28.4% (91) | 34.1% (14) | `+5.7%` |
| **Loss Rate** | 71.6% (229) | 65.8% (27) | `-5.7%` |
| **Timeout Rate** | 0.0% (0) | 0.0% (0) | `+0.0%` |
| **Mean R** | -0.1532R | +0.0242R | `+0.1774R` |
| **Median R** | -1.0000R | -1.0000R | `+0.0000R` |
| **Total Realized R** | -49.04R | +0.99R | `+50.03R` |
| **Profit Factor** | 0.786 | 1.037 | `+0.251` |
| **Expectancy** | -0.1532R | +0.0242R | `+0.1774R` |
| **Max Drawdown** | 56.99R | 18.00R | `-38.99R` |
| **Mean MFE** | 1.159R | 1.136R | `-0.023R` |
| **Mean MAE** | 1.321R | 1.144R | `-0.177R` |
| **Avg Holding Time** | 0.0 bars | 0.0 bars | `+0.0 bars` |

---

## 6. Statistical Significance & Moving Block Bootstrap (Pooled OOS)

- **Bootstrap Method**: Moving Block Bootstrap ($B=7$, $N=1000$ resamples)
- **SMC Mean R 95% CI**: `[-0.3972R, +0.0930R]`
- **AI Mean R 95% CI**: `[-0.4881R, +0.8291R]`
- **Incremental Expectancy ($E_{AI} - E_{SMC}$) 95% CI**: `[-0.3239R, +0.9932R]`

---

## 7. Leave-One-Asset-Out (LOAO) Cross-Asset Generalization Matrix

| Held-Out Test Asset | Training Assets | Train Setups | Test Setups | SMC Expectancy | AI Expectancy | Incremental R | AI Win Rate | AI PF | MBB 95% CI (Inc R) | Generalization Status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| **BTCUSD** | ETHUSD+SOLUSD+XRPUSD | 1167 | 334 | `+0.1356R` | `-1.0000R` | `-1.1356R` | 0.0% | 0.000 | `[-1.16R, -1.16R]` | `GENERALIZED_NEGATIVE` |
| **ETHUSD** | BTCUSD+SOLUSD+XRPUSD | 1145 | 356 | `-0.0145R` | `+0.0133R` | `+0.0278R` | 34.8% | 1.021 | `[-0.37R, +0.36R]` | `GENERALIZED_NEUTRAL` |
| **SOLUSD** | BTCUSD+ETHUSD+XRPUSD | 1071 | 430 | `+0.2543R` | `+0.0000R` | `-0.2543R` | 0.0% | 0.000 | `[-0.26R, -0.26R]` | `GENERALIZED_NEGATIVE` |
| **XRPUSD** | BTCUSD+ETHUSD+SOLUSD | 1120 | 381 | `-0.4109R` | `-0.6765R` | `-0.2656R` | 10.0% | 0.248 | `[-0.60R, +0.37R]` | `GENERALIZED_NEGATIVE` |

---

## 8. Multi-Asset Market Regime Robustness & Failure Analysis

| Market Regime | SMC Trades | SMC Expectancy | AI Trades | AI Expectancy | Incremental R | AI Win Rate | AI MDD | Failure Risk |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **Bullish Trend** | 375 | `+0.0857R` | 104 | `+1.2590R` | `+1.1733R` | 75.0% | 10.05R | ✅ OK |
| **Bearish Trend** | 414 | `-0.0138R` | 125 | `+1.2520R` | `+1.2658R` | 77.6% | 7.00R | ✅ OK |
| **Ranging Market** | 62 | `+0.2097R` | 17 | `+2.0000R` | `+1.7903R` | 100.0% | 0.00R | ✅ OK |
| **Transitional** | 294 | `-0.0131R` | 78 | `+0.9585R` | `+0.9716R` | 65.4% | 13.00R | ✅ OK |
| **High Volatility** | 573 | `-0.0933R` | 141 | `+1.2195R` | `+1.3128R` | 75.2% | 25.05R | ✅ OK |
| **Low Volatility** | 572 | `+0.1557R` | 183 | `+1.2254R` | `+1.0697R` | 74.9% | 8.00R | ✅ OK |

---

## 9. 5-Bucket Prediction Confidence Calibration (Dev Split)

| Predicted R Bucket | Samples | Predicted Mean R | Realized Mean R | Realized Win Rate | Median Realized R |
|---|---:|---:|---:|---:|---:|
| **< 0.0R (Bearish/Avoid)** | 598 | `-0.4007R` | `-0.6265R` | 12.5% | `-1.0000R` |
| **0.0R – 0.25R (Low)** | 104 | `+0.1285R` | `-0.0755R` | 30.8% | `-1.0000R` |
| **0.25R – 0.50R (Moderate)** | 119 | `+0.3841R` | `+0.1836R` | 40.3% | `-1.0000R` |
| **0.50R – 1.00R (High)** | 196 | `+0.7461R` | `+0.9719R` | 67.3% | `+1.9998R` |
| **>= 1.00R (Very High)** | 128 | `+1.1798R` | `+1.6071R` | 86.7% | `+1.9998R` |

---

## 10. ONNX Inference Latency Benchmarks

- **p50 Latency**: `0.034 ms`
- **p95 Latency**: `0.041 ms` (Target $\le 5.0$ ms: ✅ PASS)
- **p99 Latency**: `0.059 ms`
- **Mean Latency**: `0.034 ms`

---

## 11. Production Promotion Decision & Governance Rule

**Decision**: **`AI_PROMOTION_STATUS = REJECTED`**

> [!CAUTION]
> The AI model remains **STRICTLY DENIED LIVE EXECUTION AUTHORITY**.
> Deterministic SMC engine continues as the sole authoritative execution engine.