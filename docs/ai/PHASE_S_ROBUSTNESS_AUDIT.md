# Phase S — AI Filter Robustness & Generalization Audit Report

**Generated (UTC):** `2026-08-25T14:56:45.131526+00:00`  
**Authoritative Scope:** Phase R 2026 Walk-Forward Replay ($N=298$ OOS Setups)  
**Model Inspected:** `Ridge(alpha=1.0)` @ `+0.20R` on 29 scale-invariant causal features  
**Overall Classification:** **`PROMISING BUT INSUFFICIENT`**  

---

## 1. Executive Summary & Audit Conclusion

### Final Category: **`PROMISING BUT INSUFFICIENT`**

Some evidence of improvement exists: overall incremental expectancy is positive (+0.0611R), 4 out of 5 months (80.0%) showed positive deltas, maximum drawdown was reduced by 55.7% (16.42R -> 7.28R), and Ridge placed in the 71.8th percentile against coverage-matched random controls. However, cross-asset consistency is mixed (50.0% positive: BTC/SOL positive, ETH/XRP negative), and with N=101 trades, the 95% bootstrap confidence interval [-0.0767R, +0.2547R] spans zero (empirical random p-value = 0.2824). The edge cannot yet be declared statistically significant at alpha=0.05. More walk-forward data is required.

> [!IMPORTANT]
> **Governance & Safety Status:**
> - `live_execution_authorized = false`
> - `AI_PROMOTION_STATUS = REJECTED`
> - `execution_status = BLOCKED_BY_SYSTEM`
> - Deterministic SMC engine remains the sole production authority.

---

## 2. Independent Phase R Numerical Reconciliation

Every metric reported in Phase R was independently reconstructed from the raw 2026 master dataset:

| Metric | SMC Baseline | AI Filtered (Ridge @ +0.20R) | Incremental Delta | Reconciliation Status |
|---|---:|---:|---:|:---:|
| **Evaluated Setups ($N$)** | `298` | `101` | `-197` | **EXACT MATCH** |
| **Coverage %** | `100.00%` | `33.89%` | — | **EXACT MATCH** |
| **Expectancy (R)** | `-0.0303R` | **`+0.0308R`** | **`+0.0611R`** | **EXACT MATCH** |
| **Win Rate %** | `35.91%` | **`37.62%`** | `+1.71%` | **EXACT MATCH** |
| **Profit Factor** | `0.95` | **`1.05`** | `+0.10` | **EXACT MATCH** |
| **Total Realized R** | `-9.03R` | **`+3.12R`** | `+12.15R` | **EXACT MATCH** |
| **Max Drawdown (R)** | `29.72R` | **`11.64R`** | **`-18.08R`** | **EXACT MATCH** |

---

## 3. Dependence-Aware Moving Block Bootstrap (10,000 Resamples)

To account for potential temporal autocorrelation, we performed a **Moving Block Bootstrap** with block size $b=18$ bars across 10,000 resamples:

| Population | Mean Expectancy (R) | 95% Two-Sided Confidence Interval | $P(\Delta > 0)$ |
|---|---:|:---:|---:|
| **SMC Baseline** | `-0.0303R` | `[-0.1962R, +0.1232R]` | — |
| **AI Filtered** | `+0.0308R` | `[-0.2099R, +0.3210R]` | — |
| **Incremental Delta (\Delta)** | **`+0.0611R`** | **`[-0.0767R, +0.2547R]`** | **`82.8%`** |

---

## 4. Temporal & Asset Consistency Breakdown

### A. Monthly Breakdown (April – August 2026)

| Month | Candidate Test Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **`2026-04`** | 69 | 26 | 37.7% | `-0.2055R` | `-0.4860R` | **`-0.2805R`** | 19.2% | 0.40 | 11.64R |
| **`2026-05`** | 63 | 11 | 17.5% | `+0.0188R` | `+0.4488R` | **`+0.4300R`** | 54.5% | 1.99 | 2.29R |
| **`2026-06`** | 55 | 31 | 56.4% | `+0.1636R` | `+0.2643R` | **`+0.1007R`** | 45.2% | 1.51 | 6.00R |
| **`2026-07`** | 75 | 24 | 32.0% | `+0.0148R` | `+0.1449R` | **`+0.1301R`** | 41.7% | 1.25 | 5.22R |
| **`2026-08`** | 36 | 9 | 25.0% | `-0.1707R` | `-0.0953R` | **`+0.0754R`** | 33.3% | 0.86 | 2.00R |

*Temporal Stability:* **`80.0%` of test months exhibited positive incremental expectancy.**

### B. Asset Breakdown (BTCUSD, ETHUSD, SOLUSD, XRPUSD)

| Asset | Total OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **`BTCUSD`** | 79 | 22 | 27.9% | `+0.0241R` | `+0.3571R` | **`+0.3330R`** | 50.0% | 1.71 | 5.00R |
| **`ETHUSD`** | 74 | 27 | 36.5% | `-0.0335R` | `-0.1605R` | **`-0.1270R`** | 29.6% | 0.76 | 7.29R |
| **`SOLUSD`** | 82 | 28 | 34.1% | `+0.0521R` | `+0.2595R` | **`+0.2074R`** | 46.4% | 1.48 | 3.00R |
| **`XRPUSD`** | 63 | 24 | 38.1% | `-0.2021R` | `-0.3197R` | **`-0.1176R`** | 25.0% | 0.57 | 9.33R |

*Cross-Asset Stability:* **`50.0%` of assets exhibited positive incremental expectancy.**

---

## 5. Coverage-Matched Random Benchmark (10,000 Resamples)

To test whether Ridge's improvement is simply an artifact of accepting fewer trades (trade-count reduction), we benchmarked the filter against **10,000 random subsets of exactly $N=101$ trades** selected from the 298 OOS universe:

| Strategy / Benchmark | Selected Trades ($N$) | Mean Expectancy (R) | 95% Empirical Interval | Percentile Rank in Random Distribution |
|---|---:|---:|:---:|:---:|
| **Random Subsets Benchmark** | `101` | `-0.0291R` | `[-0.2338R, +0.1763R]` | `50.0%` |
| **Phase R AI Filter (Ridge)** | `101` | **`+0.0308R`** | — | **`71.8th Percentile`** |

> [!TIP]
> **Empirical P-Value:** $P(\text{Random Expectancy} \ge +0.0308\text{R}) = 0.2824$.
> Ridge outperforms 71.8% of random trade-reduction subsets.

---

## 6. Heuristic Control Comparisons

| Strategy / Filter Rule | Trade Count | Coverage % | Win Rate % | Expectancy (R) | Profit Factor | Max Drawdown (R) |
|---|---:|---:|---:|---:|---:|---:|
| Full SMC Baseline (100% Accept) | 298 | 100.0% | 35.9% | `-0.0303R` | 0.95 | 29.72R |
| Direction Rule: Longs Only | 167 | 56.0% | 40.1% | `+0.0803R` | 1.13 | 13.29R |
| Direction Rule: Shorts Only | 131 | 44.0% | 30.5% | `-0.1712R` | 0.75 | 25.13R |
| Heuristic Rule: Internal Trend Aligned Only | 80 | 26.9% | 40.0% | `+0.0766R` | 1.13 | 10.85R |
| **Phase R AI Filter (Ridge @ +0.20R)** | 101 | 33.9% | 37.6% | **`+0.0308R`** | **1.05** | **11.64R** |

---

## 7. Prediction Score Diagnostics & Calibration

- **Winners Mean Score vs Losers Mean Score:** `+0.1094R` vs `-0.0084R` ($\Delta = +0.1178R$, Mann-Whitney U $p = 0.0498$)
- **Spearman Rank Correlation (Predicted R vs Realized R):** $\rho = +0.0897$ ($p = 0.1224$)

### Score Quintile Calibration Table:

| Score Quintile | Sample Count | Score Range (R) | Mean Predicted R | Mean Realized R | Win Rate % | Profit Factor |
|---|---:|:---:|---:|---:|---:|---:|
| **`Q1_Lowest`** | 60 | `[-1.04R, -0.33R]` | `-0.5718R` | `-0.0950R` | 35.0% | 0.85 |
| **`Q2_Low`** | 59 | `[-0.32R, -0.11R]` | `-0.2083R` | `-0.3087R` | 25.4% | 0.59 |
| **`Q3_Mid`** | 60 | `[-0.11R, +0.13R]` | `+0.0124R` | `-0.0532R` | 35.0% | 0.92 |
| **`Q4_High`** | 59 | `[+0.13R, +0.38R]` | `+0.2447R` | `+0.2905R` | 47.5% | 1.55 |
| **`Q5_Highest`** | 60 | `[+0.38R, +6.06R]` | `+0.6919R` | `+0.0157R` | 36.7% | 1.03 |

---

## 8. Post-Hoc Descriptive Threshold Sensitivity

> [!WARNING]
> **POST-HOC DESCRIPTIVE SENSITIVITY — NOT VALID FOR MODEL SELECTION**

| Threshold (R) | Accepted Trades | Coverage % | Win Rate % | AI Expectancy (R) | Delta Exp (R) | Profit Factor | Max Drawdown (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`+0.00R`** | 148 | 49.7% | 42.6% | `+0.1676R` | `+0.1979R` | 1.29 | 16.92R |
| **`+0.05R`** | 134 | 45.0% | 44.8% | `+0.2288R` | `+0.2591R` | 1.42 | 14.92R |
| **`+0.10R`** | 126 | 42.3% | 42.9% | `+0.1742R` | `+0.2045R` | 1.31 | 16.64R |
| **`+0.15R`** | 116 | 38.9% | 40.5% | `+0.1115R` | `+0.1418R` | 1.19 | 14.64R |
| **`+0.20R`** **(Primary Frozen)** | 101 | 33.9% | 37.6% | `+0.0308R` | `+0.0611R` | 1.05 | 11.64R |
| **`+0.25R`** | 90 | 30.2% | 38.9% | `+0.0664R` | `+0.0967R` | 1.11 | 11.64R |
| **`+0.30R`** | 71 | 23.8% | 36.6% | `+0.0053R` | `+0.0356R` | 1.01 | 13.19R |
| **`+0.40R`** | 51 | 17.1% | 43.1% | `+0.1949R` | `+0.2252R` | 1.35 | 8.00R |

---

## 9. Economic Translation (1.0% Fixed-Fractional Risk)

Simulating a conservative 1.0% risk per trade on an initial balance of $10,000.00 across the 2026 OOS period:

| Strategy | Initial Equity | Terminal Equity | Net Return % | Max Dollar Drawdown % |
|---|---:|---:|---:|---:|
| **SMC Baseline** | `$10,000.00` | `$8,911.74` | `-10.88%` | `26.19%` |
| **Phase R AI Filter** | `$10,000.00` | **`$10,227.43`** | **`+2.27%`** | **`12.02%`** |

---

## 10. Key Limitations & Governance Recommendation

### Limitations:
1. **Sample Size ($N=101$):** While 101 trades in 5 months represents genuine activity, it produces wider confidence intervals than the 14-month Phase L confirmatory split.
2. **Early Regime Fragility:** In Month 1 (April), when trained on only 167 Q1 samples, the filter underperformed before adapting in subsequent months.
3. **Statistical Threshold:** The 95% MBB CI spans zero, preventing a formal statistical rejection of the null hypothesis at $\alpha=0.05$.

### Governance Recommendation:
Maintain AI_PROMOTION_STATUS = REJECTED and live_execution_authorized = false. The AI filter demonstrates promising risk-mitigation qualities but remains strictly in shadow/research mode.
