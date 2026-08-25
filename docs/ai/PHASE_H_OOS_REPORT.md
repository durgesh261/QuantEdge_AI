# QuantEdge AI — Phase H Frozen Out-of-Sample (OOS) Benchmark & Statistical Report

**Generated At**: 2026-08-25 UTC  
**Dataset Fingerprint (SHA-256)**: Canonical Multi-Asset Pooled (`data/canonical/delta_exchange_india/`)  
**Evaluation Scope**: 320 Frozen Chronological OOS Setups (Embargo: 72 Hours)  
**Governance Outcome**: `AI_PROMOTION_STATUS = REJECTED` (Shadow Mode Enforced)

---

## 1. Executive Summary

This report documents the frozen Out-of-Sample (OOS) benchmark for Phase H. The test split was held untouched throughout model research and threshold selection, and evaluated exactly once to guarantee zero test leakage.

---

## 2. Frozen OOS Performance Benchmark

| Metric | Deterministic SMC Baseline | Phase H Scale-Invariant AI | Incremental Delta | Governance Gate |
|---|---:|---:|---:|:---:|
| **Eligible Setups** | 320 | 320 | — | — |
| **Executed Trades** | 320 | 47 | `14.69% coverage` | ✅ $\ge 10.0\%$ PASS |
| **Win Rate** | 28.44% (91/320) | **40.43%** (19/47) | `+11.99%` | ✅ PASS |
| **Expectancy (Mean R)** | -0.1532R | **+0.2146R** | **`+0.3678R`** | ✅ $> 0.0$R PASS |
| **Profit Factor** | 0.786 | **1.360** | `+0.574` | ✅ $> 1.0$ PASS |
| **Max Drawdown** | 56.99R | **14.00R** | `-42.99R` | ✅ $< 125\%$ SMC PASS |
| **Mean MFE** | 1.159R | 1.182R | `+0.023R` | — |
| **Mean MAE** | 1.321R | 1.120R | `-0.201R` | — |
| **Total Realized R** | -49.04R | **+10.09R** | `+59.13R` | — |

---

## 3. Statistical Validation: Moving Block Bootstrap (MBB)

- **Resamples ($N$)**: 1,000
- **Block Length ($B$)**: 7 bars (preserving temporal autocorrelation)

| Metric | Point Estimate | 95% Confidence Interval (MBB) | Strict Gate Requirement | Gate Status |
|---|:---:|:---:|:---:|:---:|
| **SMC Baseline Mean R** | `-0.1532R` | `[-0.3012R, -0.0125R]` | — | — |
| **AI Filter Mean R** | `+0.2146R` | `[-0.2045R, +0.6850R]` | — | — |
| **Incremental Mean R** | **`+0.3678R`** | **`[-0.3434R, +1.1401R]`** | **Lower Bound $> 0.0$R** | ❌ **FAIL (Lower Bound $< 0$)** |

> [!WARNING]
> While the point estimate of incremental expectancy is positive ($+0.3678$R), the 95% bootstrap confidence interval spans negative territory ($[-0.3434\text{R}, +1.1401\text{R}]$).
> Under QuantEdge governance rules, statistical significance requires that the **entire 95% CI lower bound exceed $0.0$R**. Because this criterion is not satisfied, the model cannot be promoted.

---

## 4. Performance Breakdown by Market Regime

| Market Regime | Eligible Setups | Executed Setups | AI Win Rate | AI Expectancy | AI Profit Factor | Max Drawdown |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Bullish** | 108 | 18 | 44.44% (8/18) | `+0.3412R` | 1.621 | 4.00R |
| **Bearish** | 124 | 19 | 36.84% (7/19) | `+0.0895R` | 1.124 | 6.00R |
| **Ranging** | 22 | 3 | 33.33% (1/3) | `+0.0000R` | 1.000 | 2.00R |
| **Transitional** | 66 | 7 | 42.86% (3/7) | `+0.2857R` | 1.500 | 2.00R |

---

## 5. Promotion Gate Checklist

| # | Gate Criterion | Threshold Requirement | Observed Result | Status |
|---|---|---|---|:---:|
| 1 | **Incremental Expectancy** | $> +0.05$R vs SMC Baseline | `+0.3678R` | ✅ PASS |
| 2 | **Statistical Significance** | MBB 95% CI Lower Bound $> 0.0$R | `[-0.3434R, +1.1401R]` | ❌ **FAIL** |
| 3 | **Cross-Asset LOAO** | $\ge 50\%$ Non-Negative Assets | `4/4 (100%)` | ✅ PASS |
| 4 | **Maximum Drawdown** | $\le 125\%$ of SMC Baseline Drawdown | `14.00R (24.6% of SMC)` | ✅ PASS |
| 5 | **Minimum Setup Coverage** | $\ge 10.0\%$ of eligible setups | `14.69%` (47/320) | ✅ PASS |
| 6 | **Numeric Parity** | Max absolute error $< 10^{-3}$ | `0.000000` | ✅ PASS |
| 7 | **Inference Latency** | p95 latency $\le 5.0$ms | `0.041ms` | ✅ PASS |

### Final Decision: `AI_PROMOTION_STATUS = REJECTED`
All safety locks remain active: `live_execution_authorized = false`.
