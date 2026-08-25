# QuantEdge AI — Phase K Pre-Registered Model Comparison

**Generated At (UTC)**: 2026-08-25T07:55:11.422856+00:00  
**Selection Standard**: Evaluated and selected strictly on Train $\to$ Validation; Frozen OOS evaluated once per candidate.

## 1. Candidate Model Benchmark Table

| Model Architecture | Selected Thr | Thr Source | Val Cov | Val Inc E[R] | Val PF | OOS n | OOS Cov | OOS E[R] | OOS Inc E[R] | OOS PF | OOS Inc 95% CI |
|---|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **ridge** | `0.2` | `rule_primary` | 17.51% | `+0.3258R` | 1.57 | 37 | 22.56% | `+0.2845R` | `+0.2812R` | 1.553 | `[-0.1700, +0.6782]` |
| **elastic_net** | `0.5` | `fallback_default` | 0.0% | `+0.0301R` | 0.0 | 1 | 0.61% | `+1.7143R` | `+1.7110R` | 99.0 | `[-0.1303, +1.8961]` |
| **random_forest** | `0.2` | `rule_primary` | 15.49% | `+0.2223R` | 1.354 | 26 | 15.85% | `+0.2634R` | `+0.2601R` | 1.489 | `[-0.3330, +0.7112]` |
| **extra_trees** | `0.0` | `rule_primary` | 49.83% | `+0.2120R` | 1.328 | 98 | 59.76% | `+0.1848R` | `+0.1815R` | 1.329 | `[-0.0081, +0.3548]` |
| **hist_gbdt** | `0.3` | `rule_primary` | 16.84% | `+0.2381R` | 1.385 | 27 | 16.46% | `+0.0048R` | `+0.0015R` | 1.008 | `[-0.5425, +0.4627]` |

**Primary Model Selected**: `ridge`
> Highest validation incremental expectancy (+0.3258R) among pre-declared models.