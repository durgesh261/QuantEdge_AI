# QuantEdge AI — Phase L Shadow Replay & Governance Gate Report

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  
**Governance Decision**: `AI_PROMOTION_STATUS = REJECTED`  
**Live Execution State**: `BLOCKED_BY_SYSTEM` (`live_execution_authorized = false`)  

## 1. 10-Criterion Promotion Gate Evaluation

| Criterion | Description | Observed Metric | Gate Status |
|---|---|---|:---:|
| **C1_data_provenance** | Strict Requirement | `100% genuine Delta Exchange India 1H candles` | ✅ PASS |
| **C2_causal_no_leakage** | Strict Requirement | `Zero lookahead (features <= decision_bar)` | ✅ PASS |
| **C3_frozen_model_and_threshold** | Strict Requirement | `Ridge(alpha=1.0) @ 0.2R` | ✅ PASS |
| **C4_minimum_oos_coverage** | Strict Requirement | `16.92% (floor 15.0%)` | ✅ PASS |
| **C5_statistical_significance_ci_positive** | Strict Requirement | `10k MBB 95% CI: [-0.0024R, +0.4093R]` | ❌ FAIL |
| **C6_cross_asset_robustness** | Strict Requirement | `4/4 non-negative LOAO` | ✅ PASS |
| **C7_walk_forward_stability** | Strict Requirement | `3/3 non-negative folds` | ✅ PASS |
| **C8_accepted_vs_rejected_separation** | Strict Requirement | `Accept +0.2154R vs Reject -0.0192R (Δ=+0.2346R)` | ✅ PASS |
| **C9_risk_and_leverage_safety** | Strict Requirement | `0 liquidations before SL` | ✅ PASS |
| **C10_reproducibility** | Strict Requirement | `Bit-exact across seeded executions` | ✅ PASS |

### Final Verdict: **`AI_PROMOTION_STATUS = REJECTED`**
> REJECTED: Statistical Significance (C5) requires 95% CI lower bound > 0.0R.

## 2. Production Safety Locks
- Zero orders dispatched to Delta Exchange India API.
- Production authority remains 100% exclusively with the deterministic SMC strategy.