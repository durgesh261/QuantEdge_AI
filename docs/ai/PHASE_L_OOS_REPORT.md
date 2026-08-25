# QuantEdge AI — Phase L Primary Confirmatory OOS Performance Report

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  
**Evaluation Window**: 2025-07-04T07:00:00+00:00 -> 2026-08-19T06:00:00+00:00  
**Pre-Registered Model**: `Ridge` @ Threshold `+0.20R`  

## 1. Pooled OOS Performance Summary

| Strategy / Cohort | Setups (n) | Coverage | Win Rate (95% CI) | Expectancy (Mean R) | Profit Factor | Max Drawdown | Total Realized R |
|---|---:|---:|:---:|:---:|---:|---:|---:|
| **SMC Baseline (All Trades)** | 863 | 100.0% | 37.78% [34.6%, 41.06%] | `+0.0205R` | 1.033 | 36.17R | `+17.69R` |
| **SMC + AI Filter (Accepted)** | 146 | 16.92% | 44.52% [36.7%, 52.62%] | **`+0.2154R`** | **1.393** | **7.29R** | **`+31.44R`** |
| **AI Filtered Out (Rejected)** | 717 | 83.08% | 36.4% [32.96%, 39.99%] | `-0.0192R` | 0.97 | 36.9R | `-13.76R` |

**Incremental Expectancy (ΔE[R])**: **`+0.1949R`**  
**10,000-Resample Paired MBB 95% CI**: `[-0.0024R, +0.4093R]`  

## 2. Per-Asset Performance Breakdown (Confirmatory OOS)

| Instrument | Total Setups | Accepted Trades | AI Coverage | SMC Expectancy | AI Expectancy | Incremental Delta (ΔE[R]) | SMC PF | AI PF | AI Win Rate | SMC MDD | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTCUSD** | 224 | 24 | 10.71% | `+0.0519R` | `+0.3571R` | **`+0.3052R`** | 1.085 | 1.714 | 50.0% | 13.95R | 4.0R |
| **ETHUSD** | 197 | 37 | 18.78% | `+0.0579R` | `+0.2728R` | **`+0.2149R`** | 1.096 | 1.53 | 45.95% | 18.41R | 4.57R |
| **SOLUSD** | 242 | 50 | 20.66% | `+0.0296R` | `+0.3573R` | **`+0.3277R`** | 1.048 | 1.715 | 50.0% | 9.29R | 3.85R |
| **XRPUSD** | 200 | 35 | 17.5% | `-0.0624R` | `-0.1453R` | **`-0.0829R`** | 0.904 | 0.788 | 31.43% | 15.06R | 8.92R |