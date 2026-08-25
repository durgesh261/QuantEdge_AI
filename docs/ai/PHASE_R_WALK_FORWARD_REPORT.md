# Phase R — Strict 2026 Walk-Forward AI Training & Evaluation Report

**Generated (UTC):** `2026-08-25T13:59:50.196838+00:00`  
**Framework:** Expanding-Window Walk-Forward ML Replay (5 Windows)  
**Model Architecture:** Scikit-Learn `Ridge(alpha=1.0)` @ `+0.20R`  
**Feature Contract:** `phase-j-ob-causal-v1` (29 Scale-Invariant Causal Features)  
**Authoritative Population:** `2026_smc_order_blocks_master.csv` (`465` qualified OBs)  
**Governance Status:** `AI_PROMOTION_STATUS = REJECTED` (Shadow/Research-Only Mode)  

---

## 1. Executive Summary & Headline Findings

Phase R introduces the first **strictly causal, expanding-window walk-forward evaluation** on the 2026 SMC Order Block universe. Unlike static historical splits, the AI in Phase R is retrained progressively at the start of each month using only historical OB setups whose forward trading outcomes have **fully matured** (`label_available_timestamp <= training_end_cutoff`). It then evaluates future SMC Order Blocks for the upcoming month without lookahead bias.

### Headline Walk-Forward Out-of-Sample Performance (Apr – Aug 2026)

| Metric | SMC Baseline | AI Filtered (Ridge @ +0.20R) | AI Rejected | Incremental Delta |
|---|---:|---:|---:|---:|
| **Evaluated Setups ($N$)** | `298` | `101` | `197` | `-197` |
| **Coverage %** | `100.00%` | `33.89%` | `66.11%` | — |
| **Expectancy (R)** | `-0.0303R` | **`+0.0308R`** | `-0.0617R` | **`+0.0611R`** |
| **Win Rate %** | `35.91%` | **`37.62%`** | `35.03%` | `+1.71%` |
| **Win Rate 95% CI** | `[30.7%, 41.5%]` | `[28.8%, 47.4%]` | `[28.7%, 41.9%]` | — |
| **Profit Factor** | `0.95` | **`1.05`** | `0.91` | `+0.10` |
| **Total Realized R** | `-9.03R` | **`+3.12R`** | `-12.15R` | — |
| **Max Drawdown (R)** | `29.72R` | **`11.64R`** | `21.28R` | `-18.08R` |
| **MBB 95% CI (Delta Exp)** | — | **`[-0.0742R, +0.2498R]`** | — | $P(\Delta > 0) = 82.6\%$ |

---

## 2. Walk-Forward Window Breakdown

Progressive monthly retraining progression across all 5 expanding windows:

| Window | Test Month | Matured Training OBs | Test OBs | AI Accepted | Acceptance Rate | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **WF_WINDOW_1** | `2026-04` | `167` | `69` | `26` | `37.7%` | `-0.2055R` | `-0.4860R` | `-0.2805R` | `0.40` |
| **WF_WINDOW_2** | `2026-05` | `236` | `63` | `11` | `17.5%` | `+0.0188R` | `+0.4488R` | `+0.4300R` | `1.99` |
| **WF_WINDOW_3** | `2026-06` | `298` | `55` | `31` | `56.4%` | `+0.1636R` | `+0.2643R` | `+0.1007R` | `1.51` |
| **WF_WINDOW_4** | `2026-07` | `354` | `75` | `24` | `32.0%` | `+0.0148R` | `+0.1449R` | `+0.1301R` | `1.25` |
| **WF_WINDOW_5** | `2026-08` | `428` | `36` | `9` | `25.0%` | `-0.1707R` | `-0.0953R` | `+0.0754R` | `0.86` |

---

## 3. Per-Asset Walk-Forward Breakdown

Walk-forward out-of-sample performance across each canonical trading instrument:

| Asset | 2026 Total OBs | OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTCUSD** | 127 | 79 | 22 | 27.9% | `+0.0241R` | `+0.3571R` | `+0.3330R` | 50.0% | 1.71 | 5.00R |
| **ETHUSD** | 104 | 74 | 27 | 36.5% | `-0.0335R` | `-0.1605R` | `-0.1270R` | 29.6% | 0.76 | 7.29R |
| **SOLUSD** | 135 | 82 | 28 | 34.1% | `+0.0521R` | `+0.2595R` | `+0.2074R` | 46.4% | 1.48 | 3.00R |
| **XRPUSD** | 99 | 63 | 24 | 38.1% | `-0.2021R` | `-0.3197R` | `-0.1176R` | 25.0% | 0.57 | 9.33R |

---

## 4. Prediction Calibration & Monotonicity

Binned prediction scores vs actual realized outcomes across all test predictions:

| Prediction Score Bin | Sample Count | Mean Predicted R | Mean Realized R | Win Rate % | Profit Factor |
|---|---:|---:|---:|---:|---:|
| **`< -0.10R`** | 120 | `-0.3892R` | `-0.2077R` | 30.0% | 0.70 |
| **`[-0.10R, 0.00R)`** | 30 | `-0.0324R` | `-0.2973R` | 26.7% | 0.59 |
| **`[0.00R, +0.10R)`** | 22 | `+0.0452R` | `+0.1301R` | 40.9% | 1.22 |
| **`[+0.10R, +0.20R)`** | 25 | `+0.1534R` | `+0.7531R` | 64.0% | 3.09 |
| **`[+0.20R, +0.30R)`** | 30 | `+0.2547R` | `+0.0913R` | 40.0% | 1.15 |
| **`>= +0.30R`** | 71 | `+0.6380R` | `+0.0053R` | 36.6% | 1.01 |

---

## 5. Secondary Threshold Sensitivity

Sensitivity analysis evaluating different static thresholds across the walk-forward prediction stream:

| Threshold (R) | Accepted Setups | Coverage % | Win Rate % | AI Expectancy (R) | Delta Expectancy (R) | Profit Factor | Max Drawdown (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`-0.25R`** | 222 | 74.5% | 38.3% | `+0.0449R` | `+0.0752R` | 1.07 | 19.46R |
| **`+0.00R`** | 148 | 49.7% | 42.6% | `+0.1676R` | `+0.1979R` | 1.29 | 16.92R |
| **`+0.10R`** | 126 | 42.3% | 42.9% | `+0.1742R` | `+0.2045R` | 1.31 | 16.64R |
| **`+0.20R`** **(Primary Frozen)** | 101 | 33.9% | 37.6% | `+0.0308R` | `+0.0611R` | 1.05 | 11.64R |
| **`+0.25R`** | 90 | 30.2% | 38.9% | `+0.0664R` | `+0.0967R` | 1.11 | 11.64R |
| **`+0.30R`** | 71 | 23.8% | 36.6% | `+0.0053R` | `+0.0356R` | 1.01 | 13.19R |
| **`+0.40R`** | 51 | 17.1% | 43.1% | `+0.1949R` | `+0.2252R` | 1.35 | 8.00R |
| **`+0.50R`** | 37 | 12.4% | 37.8% | `+0.0513R` | `+0.0816R` | 1.09 | 7.00R |
| **`+0.60R`** | 29 | 9.7% | 31.0% | `-0.1266R` | `-0.0963R` | 0.81 | 5.00R |

---

## 6. Strict Anti-Leakage & Data Causality Audit

The Phase R implementation adheres to the following causal guarantees:
1. **Mature-Label Constraint:** Every OB setup $i$ entering window $k$ training satisfies `label_available_timestamp <= training_end_cutoff(k)`. Unresolved trades or trades that exited after the training month boundary are strictly excluded from that month's training.
2. **Zero Post-Decision Feature Leakage:** Model inputs consist exclusively of the 29 scale-invariant causal features. Forward outcomes (`realized_r`, `mfe_r`, `mae_r`, `first_touch_*`, `invalidation_*`) are excluded from feature vectors.
3. **Window Boundary Isolation:** Zero test-set setups enter the model's training window. The model used for month $M$ is frozen as of the final second of month $M-1$.
4. **Exact Universe Accounting:** All 465 Order Blocks from `2026_smc_order_blocks_master.csv` are accounted for: 167 in the initial seed period (Jan–Mar), and 298 across the 5 walk-forward test periods (Apr–Aug).

---

## 7. Comparative Assessment: Phase L vs Phase R

| Dimension | Phase L (Confirmatory Split) | Phase R (Expanding Walk-Forward) |
|---|---|---|
| **Evaluation Protocol** | Single static split (Train: Jun 2024–Jun 2025, OOS: Jul 2025–Aug 2026) | 5-Window Expanding Walk-Forward (Monthly Retraining in 2026) |
| **Training Scope** | 12-month historical lookback across 2024–2025 | Progressively expanding 2026 history (3 months $\rightarrow$ 7 months) |
| **Label Maturity Handling** | 72h fixed chronological embargo | Explicit per-trade `label_available_timestamp` barrier |
| **Model & Threshold** | `Ridge(alpha=1.0)` @ `+0.20R` | `Ridge(alpha=1.0)` @ `+0.20R` |
| **Real-World Fidelity** | Approximates static deployment | Replicates continuously operating monthly retraining cycle |

---

## 8. Governance Invariants & Production Safety

- `live_execution_authorized = false`
- `AI_PROMOTION_STATUS = REJECTED`
- `execution_status = BLOCKED_BY_SYSTEM`
- Deterministic SMC engine remains sole production execution authority.
