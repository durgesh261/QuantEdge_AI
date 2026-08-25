# Phase T — Multi-Year Expanding Walk-Forward AI Evaluation Report (2024–2026)

**Generated (UTC):** `2026-08-25T15:30:08.463618+00:00`  
**Seed Period:** `2024-06-01 to 2024-12-31 (429 mature training samples)`  
**OOS Evaluation Scope:** `2025-01-01 to 2026-08-21 (20 continuous months)` ($N=1239$ Setups across 20 Months)  
**Model Inspected:** `Ridge(alpha=1.0)` @ `+0.20R` on 29 Scale-Invariant Causal Features  
**Final Verdict:** **`STRONG EVIDENCE`**  

---

## 1. Executive Summary & Audit Conclusion

### Classification: **`STRONG EVIDENCE`**

Statistically significant edge confirmed across 20-month expanding walk-forward replay (95% MBB CI: [+0.0778R, +0.3153R], P(delta > 0) = 100.0%, Monthly consistency: 80.0%, Random Percentile: 99.9th).

> [!IMPORTANT]
> **Governance Invariants:**
> - `live_execution_authorized = false`
> - `AI_PROMOTION_STATUS = REJECTED`
> - `execution_status = BLOCKED_BY_SYSTEM`
> - Deterministic SMC engine remains the sole production authority.

---

## 2. Macro Out-of-Sample Performance Summary (20 Months: Jan 2025 – Aug 2026)

| Metric | SMC Baseline | AI Filtered (Ridge @ +0.20R) | Incremental Delta (Δ) |
|---|---:|---:|---:|
| **Evaluated Setups ($N$)** | `1239` | `288` | `-951` |
| **Coverage %** | `100.00%` | `23.24%` | — |
| **Expectancy (R)** | `+0.0076R` | **`+0.2081R`** | **`+0.2005R`** |
| **Win Rate %** | `37.29%` | **`44.44%`** | `+7.15%` |
| **Profit Factor** | `1.01` | **`1.38`** | `+0.36` |
| **Total Realized R** | `+9.39R` | **`+59.92R`** | `+50.53R` |
| **Max Drawdown (R)** | `36.17R` | **`10.71R`** | **`-25.46R`** |

---

## 3. Annual Performance Comparison (2025 vs 2026)

| Period | Total OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **`2025`** | 774 | 188 | 24.3% | `+0.0211R` | `+0.1533R` | **`+0.1322R`** | 42.5% | 1.27 | 10.71R |
| **`2026`** | 465 | 100 | 21.5% | `-0.0150R` | `+0.3111R` | **`+0.3261R`** | 48.0% | 1.61 | 7.24R |

---

## 4. Dependence-Aware Moving Block Bootstrap (10,000 Resamples)

Moving Block Bootstrap with block size $b=36$ bars across 10,000 resamples:

| Population | Mean Expectancy (R) | 95% Two-Sided Confidence Interval | $P(\Delta > 0)$ |
|---|---:|:---:|---:|
| **SMC Baseline** | `+0.0076R` | `[-0.0609R, +0.0909R]` | — |
| **AI Filtered** | `+0.2081R` | `[+0.0646R, +0.3638R]` | — |
| **Incremental Delta (\Delta)** | **`+0.2005R`** | **`[+0.0778R, +0.3153R]`** | **`100.0%`** |

---

## 5. Coverage-Matched Random Benchmark (10,000 Resamples)

Benchmarked against **10,000 random selections of exactly $N=288$ trades** sampled from the 1,239 OOS universe:

| Strategy / Benchmark | Selected Trades ($N$) | Mean Expectancy (R) | 95% Empirical Interval | Percentile Rank in Random Distribution |
|---|---:|---:|:---:|:---:|
| **Random Subsets Benchmark** | `288` | `+0.0075R` | `[-0.1203R, +0.1390R]` | `50.0%` |
| **Phase T AI Filter (Ridge)** | `288` | **`+0.2081R`** | — | **`99.9th Percentile`** |

> [!TIP]
> **Empirical P-Value:** $P(\text{Random Expectancy} \ge +0.2081\text{R}) = 0.0014$.

---

## 6. Month-by-Month Consistency Table (20 Windows)

| Window | Test Month | Candidate Train | Mature Train | Test Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF |
|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `WF_2025_01` | **`2025-01`** | 431 | 429 | 59 | 19 | 32.2% | `+0.0083R` | `+0.0001R` | **`-0.0082R`** | 36.8% | 1.00 |
| `WF_2025_02` | **`2025-02`** | 490 | 489 | 50 | 16 | 32.0% | `-0.0198R` | `+0.1903R` | **`+0.2101R`** | 43.8% | 1.34 |
| `WF_2025_03` | **`2025-03`** | 540 | 540 | 52 | 16 | 30.8% | `-0.0075R` | `+0.3468R` | **`+0.3543R`** | 50.0% | 1.69 |
| `WF_2025_04` | **`2025-04`** | 592 | 592 | 64 | 17 | 26.6% | `-0.0698R` | `-0.0423R` | **`+0.0275R`** | 35.3% | 0.94 |
| `WF_2025_05` | **`2025-05`** | 656 | 655 | 75 | 10 | 13.3% | `+0.0386R` | `+0.0851R` | **`+0.0465R`** | 40.0% | 1.14 |
| `WF_2025_06` | **`2025-06`** | 731 | 730 | 73 | 14 | 19.2% | `-0.1142R` | `-0.2790R` | **`-0.1648R`** | 28.6% | 0.61 |
| `WF_2025_07` | **`2025-07`** | 804 | 804 | 64 | 11 | 17.2% | `-0.1464R` | `-0.2472R` | **`-0.1008R`** | 27.3% | 0.66 |
| `WF_2025_08` | **`2025-08`** | 868 | 868 | 50 | 9 | 18.0% | `+0.3046R` | `+1.1039R` | **`+0.7993R`** | 77.8% | 5.97 |
| `WF_2025_09` | **`2025-09`** | 918 | 918 | 74 | 13 | 17.6% | `+0.1329R` | `+0.6762R` | **`+0.5433R`** | 61.5% | 2.76 |
| `WF_2025_10` | **`2025-10`** | 992 | 992 | 70 | 23 | 32.9% | `+0.2477R` | `+0.2016R` | **`-0.0461R`** | 43.5% | 1.36 |
| `WF_2025_11` | **`2025-11`** | 1062 | 1062 | 56 | 11 | 19.6% | `+0.1517R` | `+0.2145R` | **`+0.0628R`** | 45.5% | 1.39 |
| `WF_2025_12` | **`2025-12`** | 1118 | 1118 | 87 | 29 | 33.3% | `-0.1652R` | `+0.0342R` | **`+0.1994R`** | 37.9% | 1.05 |
| `WF_2026_01` | **`2026-01`** | 1205 | 1205 | 58 | 5 | 8.6% | `-0.0767R` | `+0.0866R` | **`+0.1633R`** | 40.0% | 1.14 |
| `WF_2026_02` | **`2026-02`** | 1263 | 1263 | 46 | 16 | 34.8% | `-0.1562R` | `+0.1625R` | **`+0.3187R`** | 43.8% | 1.29 |
| `WF_2026_03` | **`2026-03`** | 1309 | 1309 | 63 | 14 | 22.2% | `+0.2175R` | `+0.5804R` | **`+0.3629R`** | 57.1% | 2.35 |
| `WF_2026_04` | **`2026-04`** | 1372 | 1372 | 69 | 11 | 15.9% | `-0.2055R` | `+0.2301R` | **`+0.4356R`** | 45.5% | 1.42 |
| `WF_2026_05` | **`2026-05`** | 1441 | 1441 | 63 | 11 | 17.5% | `+0.0188R` | `+0.4500R` | **`+0.4312R`** | 54.5% | 1.99 |
| `WF_2026_06` | **`2026-06`** | 1504 | 1503 | 55 | 21 | 38.2% | `+0.1636R` | `+0.3494R` | **`+0.1858R`** | 47.6% | 1.73 |
| `WF_2026_07` | **`2026-07`** | 1559 | 1559 | 75 | 15 | 20.0% | `+0.0148R` | `+0.0843R` | **`+0.0695R`** | 40.0% | 1.14 |
| `WF_2026_08` | **`2026-08`** | 1634 | 1633 | 36 | 7 | 19.4% | `-0.1707R` | `+0.5529R` | **`+0.7236R`** | 57.1% | 2.29 |

*Temporal Stability:* **`80.0%` of test months exhibited positive incremental expectancy (16/20 months).**

---

## 7. Cross-Asset Breakdown (BTCUSD, ETHUSD, SOLUSD, XRPUSD)

| Asset | Total OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **`BTCUSD`** | 324 | 45 | 13.9% | `+0.0204R` | `+0.3270R` | **`+0.3066R`** | 48.9% | 1.64 | 4.29R |
| **`ETHUSD`** | 289 | 68 | 23.5% | `-0.0065R` | `+0.0917R` | **`+0.0982R`** | 39.7% | 1.16 | 8.14R |
| **`SOLUSD`** | 344 | 103 | 29.9% | `+0.0084R` | `+0.3176R` | **`+0.3092R`** | 48.5% | 1.62 | 10.57R |
| **`XRPUSD`** | 282 | 72 | 25.5% | `+0.0063R` | `+0.0869R` | **`+0.0806R`** | 40.3% | 1.15 | 9.98R |

*Cross-Asset Consistency:* **`100.0%` of assets exhibited positive incremental expectancy (4/4 assets).**

---

## 8. Score Diagnostics & Calibration

- **Winners Mean Score vs Losers Mean Score:** `+0.0376R` vs `-0.0579R` ($\Delta = +0.0955R$, Mann-Whitney U $p = 0.0000$)
- **Spearman Rank Correlation (Predicted R vs Realized R):** $\rho = +0.1256$ ($p = 0.0000$)

### Score Quintile Calibration:

| Score Quintile | Sample Count | Score Range (R) | Mean Predicted R | Mean Realized R | Win Rate % | Profit Factor |
|---|---:|:---:|---:|---:|---:|---:|
| **`Q1_Lowest`** | 248 | `[-1.19R, -0.28R]` | `-0.4902R` | `-0.3396R` | 24.6% | 0.55 |
| **`Q2_Low`** | 248 | `[-0.28R, -0.06R]` | `-0.1626R` | `+0.0379R` | 38.3% | 1.06 |
| **`Q3_Mid`** | 247 | `[-0.06R, +0.08R]` | `+0.0051R` | `-0.0314R` | 36.0% | 0.95 |
| **`Q4_High`** | 248 | `[+0.08R, +0.23R]` | `+0.1486R` | `+0.1557R` | 42.7% | 1.27 |
| **`Q5_Highest`** | 248 | `[+0.23R, +3.38R]` | `+0.3877R` | `+0.2152R` | 44.8% | 1.39 |

---

## 9. Economic Translation (1.0% Fixed-Fractional Risk on $10,000 Base)

Simulating a conservative 1.0% risk per trade on an initial balance of $10,000.00 across the 20-month OOS period:

| Strategy | Initial Equity | Terminal Equity | Net Return % | Max Dollar Drawdown % |
|---|---:|---:|---:|---:|
| **SMC Baseline** | `$10,000.00` | `$9,883.83` | `-1.16%` | `32.33%` |
| **Phase T AI Filter** | `$10,000.00` | **`$17,729.78`** | **`+77.30%`** | **`10.32%`** |

---

## 10. Governance Recommendation

Maintain AI_PROMOTION_STATUS = REJECTED and live_execution_authorized = false. AI intelligence operates strictly in shadow/research mode.
