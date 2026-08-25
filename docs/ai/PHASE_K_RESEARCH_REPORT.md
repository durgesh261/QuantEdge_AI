# QuantEdge AI — Phase K Comprehensive Research Report

**Generated At (UTC)**: 2026-08-25T07:55:11.422856+00:00  
**Dataset Universe**: Expanded Canonical Historical Market Data (Delta Exchange India, 2024–2026)  
**Governance Decision**: `AI_PROMOTION_STATUS = REJECTED` (Production Execution Hard-Locked to `BLOCKED_BY_SYSTEM`)  

---

## 1. Executive Summary

Phase K evaluated whether the **Phase J OB-centric AI filter** (`phase-j-ob-causal-v1`) provides a genuine, statistically defensible edge when tested on a substantially larger historical dataset of real Order-Block trading setups.

- **Expanded Sample**: **1666 total unique OB setups** across 19,479 1H candles per asset (BTCUSD=433, ETHUSD=395, SOLUSD=454, XRPUSD=384).
- **Frozen OOS Universe**: **164 setups** (2026-06-05T04:00:00+00:00 -> 2026-08-19T06:00:00+00:00).
- **Primary Candidate**: `ridge` with validation-selected threshold.
- **SMC Baseline OOS**: Expectancy `+0.0033R`, Profit Factor `1.005`, Win Rate `36.59%`.
- **AI Filtered OOS**: Expectancy `+0.2845R`, Profit Factor `1.553`, Win Rate `45.95%`, Coverage `22.56%` (37/164).
- **Incremental Expectancy**: **`+0.2812R`** vs SMC baseline.
- **Paired MBB 95% CI**: `[-0.1700R, +0.6782R]`.

---

## 2. Frozen OOS Performance Summary

| Group | Setups (n) | Win Rate | Expectancy (Mean R) | Profit Factor | Max Drawdown | Total Realized R |
|---|---:|---:|---:|---:|---:|---:|
| **SMC Baseline (All Trades)** | 164 | 36.59% | `+0.0033R` | 1.005 | 20.08R | `+0.54R` |
| **SMC + AI Filter (Accepted)** | 37 | 45.95% | `+0.2845R` | 1.553 | 7.24R | `+10.53R` |
| **AI Rejected (Filtered Out)** | 127 | 33.86% | `-0.0787R` | 0.881 | 18.22R | `-9.99R` |

---

## 3. Governance Promotion Gate Checklist

| Criterion | Requirement | Observed Metric | Gate Status |
|---|---|---|:---:|
| **C1_oos_incremental_expectancy_positive** | Strict threshold | `+0.2812R` | ✅ PASS |
| **C2_oos_profit_factor_improvement** | Strict threshold | `1.553 vs 1.005` | ✅ PASS |
| **C3_oos_drawdown_improvement** | Strict threshold | `7.24R vs 20.08R` | ✅ PASS |
| **C4_minimum_ai_coverage** | Strict threshold | `22.56% (floor 15.0%)` | ✅ PASS |
| **C5_bootstrap_ci_lower_bound_positive** | Strict threshold | `CI: [-0.1700, +0.6782]` | ❌ FAIL |
| **C6_cross_asset_robustness** | Strict threshold | `4/4 non-negative` | ✅ PASS |
| **C7_rejected_trades_materially_worse** | Strict threshold | `Accept 0.2845R vs Reject -0.0787R` | ✅ PASS |
| **C8_no_unacceptable_liquidation_risk** | Strict threshold | `0 liquidations before SL` | ✅ PASS |

### Final Verdict: **`AI_PROMOTION_STATUS = REJECTED`**
> REJECTED by Statistical Significance (C5) or Approved only for shadow review.

---

## 4. Invariant Protection & Production Execution Lock
- `live_execution_authorized = false` hardcoded.
- Production authority remains exclusively with the deterministic SMC strategy.
- Zero Delta Exchange India REST API live order placement calls.