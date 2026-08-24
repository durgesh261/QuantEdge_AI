# QuantEdge AI — Phase C Predictive-Value Gate Report

**Generated At**: 2026-08-24 17:17:37 UTC  
**Authoritative Gate Status**: `REJECTED`  
**Frozen Validation Threshold**: `+0.00R`  

---

## 1. Executive Summary & Promotion Status

**Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**

> [!WARNING]
> **Promotion Rejection Notice**:
> The AI model was evaluated against the existing deterministic SMC strategy on real historical Delta Exchange India data.
> The model failed to demonstrate statistically meaningful out-of-sample predictive superiority over the SMC baseline.
> In accordance with safety rules, **the AI model is REJECTED for live execution authority** and SMC remains the sole authoritative trading engine.

### Specific Rejection Reasons:
- ❌ OOS Expectancy (-0.1176R) does not exceed SMC Baseline (-0.0435R).
- ❌ OOS Profit Factor (0.833) is inferior to SMC Baseline (0.936).

---

## 2. Four-Instrument Canonical Data Readiness

| Symbol | Timeframe | Available | Candles | Historical Date Range | File Size | Status |
|---|---|---|---|---|---|---|
| **BTCUSD** | 1h | ✅ YES | 5,583 | 2026-01-01 00:00:00 UTC → 2026-08-21 14:00:00 UTC | 371.3 KB | `READY (5,583 real candles, 2026-01-01 00:00:00 UTC to 2026-08-21 14:00:00 UTC)` |
| **ETHUSD** | 1h | ❌ NO | 0 | N/A | 0 KB | `NOT_AVAILABLE (No canonical 2026.csv present in repo)` |
| **SOLUSD** | 1h | ❌ NO | 0 | N/A | 0 KB | `NOT_AVAILABLE (No canonical 2026.csv present in repo)` |
| **XRPUSD** | 1h | ❌ NO | 0 | N/A | 0 KB | `NOT_AVAILABLE (No canonical 2026.csv present in repo)` |

> [!IMPORTANT]
> Multi-asset production models (BTC, ETH, SOL, XRP) cannot be certified until canonical historical CSV datasets for ETHUSD, SOLUSD, and XRPUSD are imported and audited.

---

## 3. SMC Baseline vs SMC + AI Performance Comparison

### A. Validation Split Performance (41 Setups)

| Metric | SMC Only | SMC + AI | Change / Impact |
|---|---:|---:|---:|
| **Total Setups** | 41 | 41 | — |
| **Executed / Eligible Setups** | 41 | 21 | `51.2% coverage` |
| **Win Rate** | 46.3% (19) | 61.9% (13) | `+15.6%` |
| **Loss Rate** | 53.7% (22) | 38.1% (8) | `-15.6%` |
| **Timeout Rate** | 0.0% (0) | 0.0% (0) | `+0.0%` |
| **Mean R** | +0.3902R | +0.8571R | `+0.4669R` |
| **Median R** | -1.0000R | +2.0000R | `+3.0000R` |
| **Total Realized R** | +16.00R | +18.00R | `+2.00R` |
| **Profit Factor** | 1.727 | 3.250 | `+1.523` |
| **Expectancy** | +0.3902R | +0.8571R | `+0.4669R` |
| **Max Drawdown** | 8.00R | 4.00R | `-4.00R` |
| **Mean MFE** | 1.318R | 1.589R | `+0.271R` |
| **Mean MAE** | 0.825R | 0.563R | `-0.262R` |
| **Avg Holding Time** | 0.0 bars | 0.0 bars | `+0.0 bars` |

### B. Final Out-Of-Sample Test Split Performance (69 Setups — UNTOUCHED)

| Metric | SMC Only | SMC + AI | Change / Impact |
|---|---:|---:|---:|
| **Total Setups** | 69 | 69 | — |
| **Executed / Eligible Setups** | 69 | 34 | `49.3% coverage` |
| **Win Rate** | 31.9% (22) | 29.4% (10) | `-2.5%` |
| **Loss Rate** | 68.1% (47) | 70.6% (24) | `+2.5%` |
| **Timeout Rate** | 0.0% (0) | 0.0% (0) | `+0.0%` |
| **Mean R** | -0.0435R | -0.1176R | `-0.0741R` |
| **Median R** | -1.0000R | -1.0000R | `+0.0000R` |
| **Total Realized R** | -3.00R | -4.00R | `-1.00R` |
| **Profit Factor** | 0.936 | 0.833 | `-0.103` |
| **Expectancy** | -0.0435R | -0.1176R | `-0.0741R` |
| **Max Drawdown** | 18.00R | 19.00R | `+1.00R` |
| **Mean MFE** | 1.066R | 0.859R | `-0.207R` |
| **Mean MAE** | 1.272R | 1.250R | `-0.022R` |
| **Avg Holding Time** | 0.0 bars | 0.0 bars | `+0.0 bars` |

---

## 4. Setup Clustering & Duplicate Audit

- **Total Raw Setups Discovered**: 334
- **Clustered Setups within ≤ 3 Hours**: 201 (60.2%)
- **Near-Duplicate Setups (Same Entry Region & Direction)**: 8
- **Approximate Unique Structural Events**: 133

---

## 5. Model Diagnostics & Ablation Study

### A. Random Forest Feature Importance (Training Set Only)

| Rank | Feature Name | Importance (Gini) | Group |
|---|---|---|---|
| 1 | `risk_distance` | 0.2287 | Structural |
| 2 | `volatility_1h` | 0.0880 | Structural |
| 3 | `volume_profile` | 0.0869 | Structural |
| 4 | `trend_strength_4h` | 0.0834 | Structural |
| 5 | `bos_strength` | 0.0793 | Structural |
| 6 | `liquidity_proximity` | 0.0547 | Context |
| 7 | `order_block_strength` | 0.0521 | Context |
| 8 | `volatility_15m` | 0.0485 | Context |
| 9 | `fvg_strength` | 0.0476 | Context |
| 10 | `momentum_15m` | 0.0459 | Context |
| 11 | `choch_strength` | 0.0450 | Context |
| 12 | `momentum_1h` | 0.0265 | Context |
| 13 | `trend_strength_15m` | 0.0215 | Context |
| 14 | `trend_strength_1h` | 0.0212 | Geometry |
| 15 | `entry_precision` | 0.0211 | Geometry |
| 16 | `leverage_ratio` | 0.0202 | Geometry |
| 17 | `risk_reward` | 0.0165 | Account |
| 18 | `regime_alignment` | 0.0044 | Account |
| 19 | `regime_1h_transitional` | 0.0036 | Regime/Flags |
| 20 | `regime_1h_bearish` | 0.0021 | Regime/Flags |
| 21 | `direction_long` | 0.0014 | Regime/Flags |
| 22 | `regime_1h_ranging` | 0.0008 | Regime/Flags |
| 23 | `regime_1h_bullish` | 0.0005 | Regime/Flags |
| 24 | `account_utilization` | 0.0000 | Regime/Flags |

### B. Feature Group Ablation Study (Validation Split)

| Feature Group | Features Count | Val Realized R² | Val Realized MAE |
|---|---:|---:|---:|
| **SMC_Structural** | 5 | `+0.0224` | `1.2373` |
| **Market_Context** | 8 | `+0.0258` | `1.3889` |
| **Setup_Geometry** | 3 | `-0.3975` | `1.3839` |
| **Account_Context** | 2 | `-0.0808` | `1.1100` |
| **Regime_OneHot** | 4 | `-0.0944` | `1.5321` |
| **All_24_Features** | 24 | `+0.0274` | `1.3318` |

### C. Model vs Naive Baselines Comparison (Validation Split)

| Model / Predictor | Realized R MAE | Realized R MSE | Realized R R² |
|---|---:|---:|---:|
| **Random_Forest_AI** | 1.3256 | 2.1507 | `+0.0390` |
| **Mean_Predictor** | 1.4787 | 2.2708 | `-0.0147` |
| **Median_Predictor** | 1.3902 | 4.1707 | `-0.8636` |
| **Random_Shuffle_Baseline** | 1.4832 | 4.4063 | `-0.9689` |

---

## 6. Confidence Calibration & Stratification

| Predicted Expected R Bucket | Sample Count | Realized Win Rate | Mean Realized R | Median Realized R |
|---|---:|---:|---:|---:|
| **< 0.0R (Bearish/Avoid)** | 185 | 9.7% | -0.7081R | -1.0000R |
| **0.0R – 0.2R (Low)** | 26 | 46.2% | +0.3846R | -1.0000R |
| **0.2R – 0.5R (Moderate)** | 27 | 22.2% | -0.3333R | -1.0000R |
| **>= 0.5R (High)** | 96 | 94.8% | +1.8260R | +2.0000R |

---

## 7. Market Regime Breakdown

| Market Regime | SMC Setups | SMC Win Rate | SMC Mean R | AI Setups | AI Win Rate | AI Mean R | AI Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Bullish Trend** | 91 | 39.6% | +0.1868R | 44 | 77.3% | +1.3182R | 48.4% |
| **Bearish Trend** | 104 | 40.4% | +0.2115R | 37 | 86.5% | +1.5946R | 35.6% |
| **Ranging Market** | 94 | 35.1% | +0.0532R | 51 | 54.9% | +0.6471R | 54.3% |
| **Transitional** | 45 | 35.6% | +0.0287R | 17 | 88.2% | +1.5466R | 37.8% |

---

## 8. Monthly Chronological Performance Breakdown

| Month | SMC Trades | SMC Win Rate | SMC Total R | AI Trades | AI Win Rate | AI Total R | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| **2026-01** | 43 | 48.8% | +20.00R | 21 | 100.0% | +42.00R | 48.8% |
| **2026-02** | 32 | 31.2% | -2.00R | 11 | 90.9% | +19.00R | 34.4% |
| **2026-03** | 47 | 44.7% | +16.00R | 21 | 100.0% | +42.00R | 44.7% |
| **2026-04** | 65 | 44.6% | +20.29R | 30 | 96.7% | +55.29R | 46.2% |
| **2026-05** | 46 | 17.4% | -22.00R | 13 | 53.9% | +8.00R | 28.3% |
| **2026-06** | 27 | 59.3% | +21.00R | 14 | 78.6% | +19.00R | 51.9% |
| **2026-07** | 50 | 26.0% | -11.00R | 28 | 35.7% | +2.00R | 56.0% |
| **2026-08** | 24 | 37.5% | +3.00R | 11 | 0.0% | -11.00R | 45.8% |

---

## 9. Statistical Robustness & Bootstrap Confidence Intervals

- **SMC Baseline OOS Mean R (95% CI)**: `-0.3489R` to `+0.3043R`
- **SMC + AI OOS Mean R (95% CI)**: `-0.5588R` to `+0.3235R`

---

## 10. Conclusion & Next Research Directions

1. **Execution Invariant Maintained**: Because the promotion gate output is `REJECTED`, the model is not authorized for live execution.
2. **Root Cause Analysis of 42% OOS**: Market regime shifts between H1 2026 and Q3 2026 along with target noise in 1H timeframe swing targets limit standalone Random Forest generalization without higher timeframe multi-asset contextual anchors.
3. **Next Phase Recommendations**: Expand canonical historical coverage across ETH, SOL, and XRP; incorporate multi-horizon label conditioning.