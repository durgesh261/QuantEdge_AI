# QuantEdge AI — Phase E Multi-Asset AI Research & Second Promotion Gate Report

**Generated At**: 2026-08-24 17:55:06 UTC  
**Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**  
**Frozen Validation Threshold**: `pred_realized_r >= +0.00R`  

---

## 1. Executive Summary

> [!WARNING]
> **Authoritative Promotion Status**: **`AI_PROMOTION_STATUS = REJECTED`**
> The AI model was evaluated across multi-asset real data, structural clustering, candidate architectures, regime profiles, and out-of-sample tests.
> In accordance with safety invariants, live trade execution authority remains strictly protected and governed by the deterministic SMC engine.

### Promotion Gate Rejection Reasons:
- ❌ `OOS Expectancy (-0.3438R) does not exceed SMC baseline (-0.0435R).`
- ❌ `OOS Profit Factor (0.560) is inferior to SMC baseline (0.936).`
- ❌ `Moving Block Bootstrap 95% CI lower bound for incremental expectancy (-0.9236R) is not strictly positive.`

---

## 2. Multi-Asset Data Availability & Audit

| Symbol | Timeframe | Available | Candles | Historical Date Range | Missing / Dups | Status |
|---|---|---|---|---|---|---|
| **BTCUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 → 2026-08-21 | 0 / 0 | `AVAILABLE` |
| **ETHUSD** | 1h | ❌ NO | 0 | N/A | 0 / 0 | `NOT_AVAILABLE` |
| **SOLUSD** | 1h | ❌ NO | 0 | N/A | 0 / 0 | `NOT_AVAILABLE` |
| **XRPUSD** | 1h | ❌ NO | 0 | N/A | 0 / 0 | `NOT_AVAILABLE` |

> [!IMPORTANT]
> Multi-asset models across ETHUSD, SOLUSD, and XRPUSD remain uncertified until canonical historical datasets for these pairs are imported and audited.

---

## 3. Structural Setup Clustering & Correlation Audit

- **Total Raw Setups**: 334
- **Clustered within $\le 3$ Hours**: 194 (58.1%)
- **Unique Structural Events**: 140
- **Mean Cluster Size**: 2.39
- **Max Cluster Size**: 17

---

## 4. Multi-Model Candidate Comparison (Validation Split)

| Candidate Architecture | Val Realized R² | Val MAE | Val Expectancy | Val PF | Val Win Rate | Val Coverage | Fitness Score |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Ridge_Linear** | `+0.0582` | 1.3330 | `+0.6364R` | 2.400 | 54.5% | 53.7% | `+0.4664` |
| **Random_Forest_Base** | `+0.1007` | 1.2767 | `+1.2941R` | 6.500 | 76.5% | 41.5% | `+0.8337` |
| **Extra_Trees_Regularized** | `+0.0459` | 1.3679 | `+0.4400R` | 1.846 | 48.0% | 61.0% | `+0.3437` |
| **Hist_Gradient_Boosting** | `-0.0585` | 1.3080 | `+0.7368R` | 2.750 | 57.9% | 46.3% | `+0.5013` |

- **Best Hyperparameters (Random Forest)**: `{"max_depth": 8, "min_samples_leaf": 3, "max_features": 0.7, "n_estimators": 100}`

---

## 5. Out-of-Sample Benchmark: SMC vs SMC + AI

### Final Out-of-Sample Test Split (Untouched Evaluation)

| Metric | SMC Only | SMC + AI | Change / Impact |
|---|---:|---:|---:|
| **Total Setups** | 69 | 69 | — |
| **Executed / Eligible Setups** | 69 | 32 | `46.4% coverage` |
| **Win Rate** | 31.9% (22) | 21.9% (7) | `-10.0%` |
| **Loss Rate** | 68.1% (47) | 78.1% (25) | `+10.0%` |
| **Timeout Rate** | 0.0% (0) | 0.0% (0) | `+0.0%` |
| **Mean R** | -0.0435R | -0.3438R | `-0.3003R` |
| **Median R** | -1.0000R | -1.0000R | `+0.0000R` |
| **Total Realized R** | -3.00R | -11.00R | `-8.00R` |
| **Profit Factor** | 0.936 | 0.560 | `-0.376` |
| **Expectancy** | -0.0435R | -0.3438R | `-0.3003R` |
| **Max Drawdown** | 18.00R | 22.00R | `+4.00R` |
| **Mean MFE** | 1.066R | 0.778R | `-0.288R` |
| **Mean MAE** | 1.272R | 1.326R | `+0.054R` |
| **Avg Holding Time** | 0.0 bars | 0.0 bars | `+0.0 bars` |

---

## 6. Statistical Significance & Moving Block Bootstrap (MBB)

- **Bootstrap Method**: Moving Block Bootstrap ($B=5$, $N=1000$ resamples)
- **SMC Mean R 95% CI**: `[-0.4783R, +0.5652R]`
- **AI Mean R 95% CI**: `[-0.9062R, +0.4062R]`
- **Incremental Expectancy ($E_{AI} - E_{SMC}$) 95% CI**: `[-0.9236R, +0.3889R]`

---

## 7. Market Regime Robustness & Failure Analysis

| Market Regime | SMC Trades | SMC Expectancy | AI Trades | AI Expectancy | Incremental R | AI Win Rate | AI MDD | Failure Risk |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **Bullish Trend** | 65 | `+0.4308R` | 33 | `+1.8182R` | `+1.3874R` | 93.9% | 1.00R | ✅ OK |
| **Bearish Trend** | 87 | `+0.1379R` | 34 | `+1.6471R` | `+1.5092R` | 88.2% | 2.00R | ✅ OK |
| **Ranging Market** | 59 | `+0.2712R` | 24 | `+1.8750R` | `+1.6038R` | 95.8% | 1.00R | ✅ OK |
| **Transitional** | 42 | `+0.1022R` | 15 | `+1.6861R` | `+1.5839R` | 93.3% | 1.00R | ✅ OK |
| **High Volatility** | 127 | `+0.2858R` | 57 | `+1.7069R` | `+1.4211R` | 91.2% | 3.00R | ✅ OK |
| **Low Volatility** | 126 | `+0.1905R` | 49 | `+1.8163R` | `+1.6258R` | 93.9% | 2.00R | ✅ OK |

---

## 8. Cross-Asset Generalization Matrix

| Asset | Status | SMC Expectancy | AI Expectancy | Incremental R | Profit Factor | Max Drawdown | Coverage |
|---|---|---:|---:|---:|---:|---:|---:|
| **BTCUSD** | `AVAILABLE` | -0.0435R | -0.3438R | -0.3003R | 0.560 | 22.00R | 46.4% |
| **ETHUSD** | `NOT_AVAILABLE` | N/A | N/A | N/A | N/A | N/A | 0.0% |
| **SOLUSD** | `NOT_AVAILABLE` | N/A | N/A | N/A | N/A | N/A | 0.0% |
| **XRPUSD** | `NOT_AVAILABLE` | N/A | N/A | N/A | N/A | N/A | 0.0% |

---

## 9. 5-Bucket Prediction Confidence Calibration

| Predicted R Bucket | Samples | Predicted Mean R | Realized Mean R | Realized Win Rate | Median Realized R |
|---|---:|---:|---:|---:|---:|
| **< 0.0R (Bearish/Avoid)** | 147 | `-0.5739R` | `-0.8571R` | 4.8% | `-1.0000R` |
| **0.0R – 0.25R (Low)** | 11 | `+0.0858R` | `+0.3636R` | 45.5% | `-1.0000R` |
| **0.25R – 0.50R (Moderate)** | 8 | `+0.3851R` | `+1.6250R` | 87.5% | `+2.0000R` |
| **0.50R – 1.00R (High)** | 20 | `+0.7815R` | `+1.7646R` | 95.0% | `+2.0000R` |
| **>= 1.00R (Very High)** | 67 | `+1.6252R` | `+2.0000R` | 100.0% | `+2.0000R` |

---

## 10. ONNX Inference Latency Benchmarks

- **p50 Latency**: `89.886 ms`
- **p95 Latency**: `143.09 ms` (Target $\le 5.0$ ms: ❌ FAIL)
- **p99 Latency**: `197.034 ms`
- **Mean Latency**: `97.097 ms`

---

## 11. Production Promotion Decision & Rule

**Decision**: **`AI_PROMOTION_STATUS = REJECTED`**

> [!CAUTION]
> The AI model remains **STRICTLY DENIED LIVE EXECUTION AUTHORITY**.
> Deterministic SMC engine continues as the sole authoritative execution engine.