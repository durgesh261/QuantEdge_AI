# QuantEdge AI — Phase L Research Design & Pre-Registration Protocol

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  
**Phase Objective**: Confirmatory Out-of-Sample Statistical Power Validation of the Real OB-Centric AI Filter  

---

## 1. Pre-Registered Hypotheses & Model Specification

- **Primary Hypothesis ($H_1$)**: The OB-centric AI filter achieves positive incremental expectancy $\Delta E[R] > 0$ with 95% bootstrap confidence interval strictly above $0.0$R on genuinely unseen chronological OOS market data.
- **Null Hypothesis ($H_0$)**: Incremental expectancy is $\le 0.0$R ($\Delta E[R] \le 0$).
- **Pre-Registered Model**: `Ridge(alpha=1.0)` (Frozen from Phase K).
- **Pre-Registered Decision Threshold**: `+0.20R` (Frozen from Phase K validation).
- **Feature Contract**: `phase-j-ob-causal-v1` (29 scale-invariant causal features, strictly $T \le \text{decision\_bar}$).

## 2. Confirmatory Chronological Partitioning

- **Training Period**: `2024-06-10T14:00:00+00:00 -> 2025-06-30T08:00:00+00:00` (804 unique setups).
- **72h Embargo Window**: `2025-06-30T18:00Z -> 2025-07-03T20:00Z` (prevents cross-boundary trade contamination).
- **Statistically Powered OOS Period**: `2025-07-04T07:00:00+00:00 -> 2026-08-19T06:00:00+00:00` (863 unique setups, 13.5 months).

## 3. Strict Confirmatory Protocol
- Zero hyperparameter tuning on the OOS split.
- Zero threshold search on the OOS split.
- 10,000 resamples for Paired Moving Block Bootstrap.
- Production live execution remains hard-locked to `BLOCKED_BY_SYSTEM`.