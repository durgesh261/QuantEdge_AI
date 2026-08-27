# OB Width-Based TP/SL Strategy ($10 Full Compounding) Research Report

**Generated (UTC):** `2026-08-26T08:55:21.760628+00:00`  
**Dataset Scope:** Canonical Multi-Year SMC Order Blocks (June 2024 - August 2026, 1,670 raw setups across BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Starting Capital:** `$10.00` (Continuous Single-Account Compounding)  
**Execution Semantics:** Global 1-Trade-at-a-Time Portfolio Lock (Conservative Tie-Break)  
**Research Classification:** **`EMPIRICALLY TESTED - NEGATIVE NET EXPECTANCY WITHOUT AI FILTER`**  

---

## 1. Executive Summary & Core Results

This research experiment tested the hypothesized **OB Width-Based TP Rule** on the unfiltered QuantEdge SMC Order Block engine with continuous account compounding on a `$10.00` base:
- **Regime A (OB Width <= 0.6%):** 60% Target (60/35 ROE = 1.7143R).
- **Regime B (OB Width > 0.6%):** Exactly 1:1 Risk/Reward (1.0R TP = 1.0R SL).
- **Portfolio Constraint:** Strict Global 1-Trade Lock (only 1 active position across all 4 assets).

### Macro Performance Summary:

| Metric | OB Width TP Strategy (Unfiltered) | Phase T Production Baseline (AI Filtered) | Delta (\Delta) |
|---|---:|---:|---:|
| **Starting Capital** | `$10.00` | `$10,000.00` | - |
| **Executed Trades ($N$)** | `1085` | `288` | `+797` |
| **Win Rate %** | **`37.05%`** | **`44.44%`** | **`-7.39%`** |
| **Gross Expectancy (R)** | **`-0.2246R`** | **`+0.2081R`** | **`-0.4327R`** |
| **Profit Factor** | **`0.64`** | **`1.38`** | **`-0.74`** |
| **Total Realized R** | **`-243.74R`** | **`+59.92R`** | **`-303.66R`** |
| **Ending Capital (10% Risk Compounding)** | **`$0.0000`** (`-100.00%`) | - | - |
| **Ending Capital (1.0% Risk Compounding)** | **`$0.8237`** (`-91.76%`) | **`$17,729.78`** (`+77.30%`) | - |
| **Max Losing Streak** | `11 consecutive losses` | `6 consecutive losses` | `+5` |

> [!IMPORTANT]
> **Key Scientific Conclusion:**
> 1. When applied blindly to all SMC Order Blocks without AI quality filtration, the 1:1 TP rule for wide OBs (>0.6%) achieves only a **38.95% win rate**, which with a 1:1 payoff produces negative expectancy (**`-0.2212R`**).
> 2. Because expectancy is negative (-0.2246R overall), full account compounding rapidly draws the $10 account down rather than growing it.
> 3. The **Phase T AI Filter** remains essential because it rejects the ~75% of low-conviction setups that cause negative drift.

---

## 2. TP Regime Breakdown (Narrow <= 0.6% vs Wide > 0.6%)

| Regime | Condition | Planned RR | Executed Trades | Win Rate % | Expectancy (R) | Profit Factor | Total R | Avg Holding Time |
|---|---|:---:|---:|---:|---:|---:|---:|---:|
| **`REGIME_A_LE_06`** | Width <= 0.6% | `1.7143R` (60/35) | `194` | `28.35%` | `-0.2404R` | `0.66` | `-46.64R` | `3.9h` |
| **`REGIME_B_GT_06`** | Width > 0.6% | `1.0000R` (1:1) | `891` | `38.95%` | `-0.2212R` | `0.64` | `-197.10R` | `6.3h` |

---

## 3. Cross-Asset Breakdown

| Asset | Total Trades | Wins | Losses | Win Rate % | Expectancy (R) | Total R | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`BTCUSD`** | 309 | 110 | 199 | `35.6%` | `-0.2164R` | `-66.86R` | `0.66` |
| **`ETHUSD`** | 263 | 102 | 161 | `38.78%` | `-0.2053R` | `-54.00R` | `0.66` |
| **`SOLUSD`** | 293 | 113 | 180 | `38.57%` | `-0.2165R` | `-63.43R` | `0.65` |
| **`XRPUSD`** | 220 | 77 | 143 | `35.0%` | `-0.2702R` | `-59.45R` | `0.58` |

---

## 4. Month-by-Month Compounding Summary

| Month | Trades | Wins | Losses | Win Rate % | Monthly Total R | Expectancy (R) | Starting Capital (10% Risk) | Ending Capital (10% Risk) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `2024-06` | 29 | 8 | 21 | `27.59%` | `-11.43R` | `-0.3942R` | `$10.00` | `$2.73` |
| `2024-07` | 49 | 18 | 31 | `36.73%` | `-12.29R` | `-0.2507R` | `$2.73` | `$0.62` |
| `2024-08` | 38 | 10 | 28 | `26.32%` | `-18.10R` | `-0.4763R` | `$0.62` | `$0.08` |
| `2024-09` | 47 | 16 | 31 | `34.04%` | `-15.29R` | `-0.3254R` | `$0.08` | `$0.01` |
| `2024-10` | 48 | 13 | 35 | `27.08%` | `-20.18R` | `-0.4204R` | `$0.01` | `$0.00` |
| `2024-11` | 43 | 17 | 26 | `39.53%` | `-9.00R` | `-0.2093R` | `$0.00` | `$0.00` |
| `2024-12` | 49 | 13 | 36 | `26.53%` | `-22.29R` | `-0.4548R` | `$0.00` | `$0.00` |
| `2025-01` | 44 | 17 | 27 | `38.64%` | `-8.57R` | `-0.1948R` | `$0.00` | `$0.00` |
| `2025-02` | 30 | 11 | 19 | `36.67%` | `-8.00R` | `-0.2667R` | `$0.00` | `$0.00` |
| `2025-03` | 28 | 12 | 16 | `42.86%` | `-3.29R` | `-0.1173R` | `$0.00` | `$0.00` |
| `2025-04` | 39 | 18 | 21 | `46.15%` | `-2.29R` | `-0.0586R` | `$0.00` | `$0.00` |
| `2025-05` | 50 | 18 | 32 | `36.0%` | `-13.29R` | `-0.2657R` | `$0.00` | `$0.00` |
| `2025-06` | 40 | 13 | 27 | `32.5%` | `-11.17R` | `-0.2791R` | `$0.00` | `$0.00` |
| `2025-07` | 46 | 17 | 29 | `36.96%` | `-9.14R` | `-0.1988R` | `$0.00` | `$0.00` |
| `2025-08` | 41 | 18 | 23 | `43.9%` | `-2.86R` | `-0.0697R` | `$0.00` | `$0.00` |
| `2025-09` | 49 | 23 | 26 | `46.94%` | `+1.29R` | `+0.0262R` | `$0.00` | `$0.00` |
| `2025-10` | 42 | 16 | 26 | `38.1%` | `-9.29R` | `-0.2211R` | `$0.00` | `$0.00` |
| `2025-11` | 37 | 13 | 24 | `35.14%` | `-10.29R` | `-0.2780R` | `$0.00` | `$0.00` |
| `2025-12` | 53 | 18 | 35 | `33.96%` | `-14.86R` | `-0.2803R` | `$0.00` | `$0.00` |
| `2026-01` | 36 | 14 | 22 | `38.89%` | `-5.14R` | `-0.1429R` | `$0.00` | `$0.00` |
| `2026-02` | 28 | 8 | 20 | `28.57%` | `-12.00R` | `-0.4286R` | `$0.00` | `$0.00` |
| `2026-03` | 35 | 15 | 20 | `42.86%` | `-2.86R` | `-0.0816R` | `$0.00` | `$0.00` |
| `2026-04` | 40 | 12 | 28 | `30.0%` | `-15.29R` | `-0.3821R` | `$0.00` | `$0.00` |
| `2026-05` | 45 | 19 | 26 | `42.22%` | `-4.14R` | `-0.0921R` | `$0.00` | `$0.00` |
| `2026-06` | 33 | 18 | 15 | `54.55%` | `+4.43R` | `+0.1342R` | `$0.00` | `$0.00` |
| `2026-07` | 45 | 20 | 25 | `44.44%` | `-3.57R` | `-0.0794R` | `$0.00` | `$0.00` |
| `2026-08` | 21 | 7 | 14 | `33.33%` | `-4.86R` | `-0.2313R` | `$0.00` | `$0.00` |

---

## 5. Trade Ledger Sample (First 10 Executed Trades)

Below is an extract from the generated compounding ledger:

| # | Timestamp | Asset | Dir | OB Width % | Regime | Entry | SL | TP | Outcome | Realized R | Starting $ | PnL $ (10% Risk) | Ending $ |
|---|---|---|:---:|---:|:---:|---:|---:|---:|:---:|---:|---:|---:|---:|
| 1 | `2024-06-10T14:00:00` | BTCUSD | SHORT | 0.2884% | `REGIME_A_LE_06` | 69612.0 | 69813.0 | 69267.4286 | **SL_HIT** | `-1.00R` | `$10.00` | `$-1.00` | `$9.00` |
| 2 | `2024-06-10T15:00:00` | ETHUSD | SHORT | 0.7578% | `REGIME_B_GT_06` | 3706.6125 | 3727.65 | 3685.575 | **TP_HIT** | `+1.00R` | `$9.00` | `$+0.90` | `$9.90` |
| 3 | `2024-06-10T19:00:00` | BTCUSD | LONG | 0.4404% | `REGIME_A_LE_06` | 69492.0 | 69186.0 | 70016.5714 | **SL_HIT** | `-1.00R` | `$9.90` | `$-0.99` | `$8.91` |
| 4 | `2024-06-12T07:00:00` | ETHUSD | SHORT | 0.6165% | `REGIME_B_GT_06` | 3530.6875 | 3547.0 | 3514.375 | **SL_HIT** | `-1.00R` | `$8.91` | `$-0.89` | `$8.02` |
| 5 | `2024-06-12T16:00:00` | BTCUSD | SHORT | 0.8199% | `REGIME_B_GT_06` | 69769.75 | 70198.0 | 69341.5 | **TP_HIT** | `+1.00R` | `$8.02` | `$+0.80` | `$8.82` |
| 6 | `2024-06-13T19:00:00` | SOLUSD | LONG | 1.7928% | `REGIME_B_GT_06` | 147.6325 | 145.645 | 149.62 | **SL_HIT** | `-1.00R` | `$8.82` | `$-0.88` | `$7.94` |
| 7 | `2024-06-15T08:00:00` | XRPUSD | SHORT | 0.3546% | `REGIME_A_LE_06` | 0.479 | 0.4807 | 0.4761 | **TIMEOUT_EXIT** | `-0.15R` | `$7.94` | `$-0.12` | `$7.82` |
| 8 | `2024-06-16T05:00:00` | BTCUSD | SHORT | 0.4081% | `REGIME_A_LE_06` | 66233.0 | 66503.5 | 65769.2857 | **SL_HIT** | `-1.00R` | `$7.82` | `$-0.78` | `$7.04` |
| 9 | `2024-06-17T01:00:00` | ETHUSD | LONG | 0.9579% | `REGIME_B_GT_06` | 3604.4 | 3578.45 | 3630.35 | **SL_HIT** | `-1.00R` | `$7.04` | `$-0.70` | `$6.34` |
| 10 | `2024-06-17T06:00:00` | XRPUSD | LONG | 0.4114% | `REGIME_A_LE_06` | 0.4869 | 0.4849 | 0.4903 | **SL_HIT** | `-1.00R` | `$6.34` | `$-0.63` | `$5.70` |

---

## 6. Scientific Analysis & Recommendations

1. **The 1:1 Wide OB Rule Fails Without Filtering:** A 1:1 TP requires a win rate > 50% to break even. In raw SMC, Order Blocks have a natural base win rate of only ~37-39%. Lowering TP to 1:1 from 1.714R increases win rate only marginally (36.4% -> 38.9%) while cutting the payoff from +1.714R to +1.0R, resulting in a worse overall profit factor (0.64).
2. **Account Compounding Requires Positive Drift:** When expectancy is negative (-0.2246R), compounding amplifies losses and bankrupts the initial $10 account.
3. **Phase T Protection:** Phase T remains the sole verified profitable baseline (+0.2081R, 1.38 PF, +77.30% growth) because its Ridge regression model successfully filters out low-conviction setups.
