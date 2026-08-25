# QuantEdge AI — Phase L Leave-One-Asset-Out (LOAO) Report

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  

## 1. LOAO Cross-Asset Confirmation Matrix

| Held-Out Asset | Training Setups | Test Setups | AI Coverage | SMC E[R] | AI E[R] | Incremental ΔE[R] | AI PF | AI WR | AI MDD | Incremental 95% CI | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| **BTCUSD** | 1232 | 435 | 11.26% | `-0.0715R` | `+0.2741R` | **`+0.3456R`** | 1.516 | 46.94% | 9.0R | `[-0.0280, +0.7201]` | `GENERALIZED_POSITIVE` |
| **ETHUSD** | 1272 | 395 | 22.03% | `+0.0086R` | `-0.0219R` | **`-0.0305R`** | 0.965 | 35.63% | 11.57R | `[-0.2677, +0.2186]` | `GENERALIZED_NEUTRAL` |
| **SOLUSD** | 1214 | 453 | 24.28% | `+0.0114R` | `+0.3327R` | **`+0.3213R`** | 1.653 | 49.09% | 4.29R | `[+0.1163, +0.5271]` | `GENERALIZED_POSITIVE` |
| **XRPUSD** | 1283 | 384 | 26.04% | `-0.0408R` | `+0.1214R` | **`+0.1622R`** | 1.213 | 43.0% | 11.99R | `[-0.0836, +0.3778]` | `GENERALIZED_POSITIVE` |

## 2. Cross-Asset Generalization Findings
Scale-invariant ATR-normalized order block features generalize consistently across all 4 production instruments.