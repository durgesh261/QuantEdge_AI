# Fixed 0.7% Price-Target TP ($10 Full Compounding) Research Report

**Generated (UTC):** `2026-08-26T09:44:21.442224+00:00`  
**Dataset Scope:** Canonical Multi-Year SMC Order Blocks (June 2024 - August 2026, 1,670 raw setups across BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Starting Capital:** `$10.00` (Continuous Single-Account Compounding)  
**Execution Semantics:** 25% Penetration Limit Entry + Global 1-Trade-at-a-Time Lock (Conservative Tie-Break)  
**Research Classification:** **`EMPIRICALLY TESTED - UNPROFITABLE DUE TO ASYMMETRIC TP/SL RATIOS`**  

---

## 1. Executive Summary & Core Results

This research experiment evaluated whether fixing the Take Profit target to a constant **0.7% market price movement** from entry (`entry * 1.007` for Long, `entry * 0.993` for Short) with a **35% maximum margin-loss / SL rule** produces a profitable strategy when applied to canonical SMC Order Blocks with continuous compounding from a `$10.00` starting base.

### Macro Performance Comparison:

| Metric | Fixed 0.7% TP Strategy (Unfiltered) | Phase T Production Baseline (AI Filtered) | Delta (\Delta) |
|---|---:|---:|---:|
| **Starting Capital** | `$10.00` | `$10,000.00` | - |
| **Total Candidate Setups** | `1,670` | `1,239` (OOS) | `+431` |
| **Unfilled Setups (25% limit)** | `171` (`10.2%`) | `0` (100% Proximal fill) | `+171` |
| **Executed Trades ($N$)** | `963` | `288` | `+675` |
| **Win Rate %** | **`26.38%`** | **`44.44%`** | **`-18.06%`** |
| **Gross Expectancy (R)** | **`-0.4458R`** | **`+0.2081R`** | **`-0.6539R`** |
| **Profit Factor** | **`0.39`** | **`1.38`** | **`-0.99`** |
| **Total Realized R** | **`-429.34R`** | **`+59.92R`** | **`-489.26R`** |
| **Ending Capital (35% Margin Risk Compounding)** | **`$0.0000`** (`-100.00%`) | - | - |
| **Ending Capital (10% Risk Compounding)** | **`$0.0000`** (`-100.00%`) | - | - |
| **Ending Capital (1.0% Risk Compounding)** | **`$0.1292`** (`-98.71%`) | **`$17,729.78`** (`+77.30%`) | - |
| **Max Losing Streak** | `21 consecutive losses` | `6 consecutive losses` | `+15` |

---

## 2. Theoretical TP/SL Ratio Disconnect Analysis

Because the Take Profit is fixed in **price space** (0.7%) while the Stop Loss comes from **Order Block geometry** (0.75 * OB width), the resulting Risk-to-Reward ratio varies wildly across market regimes:

| Planned TP/SL Category | Condition | Setup Count | Percentage | Implications |
|---|---|---:|---:|---|
| **Category A (TP < SL)** | Planned RR < 0.90R | `536` | **`47.3%`** | Risking 1.0R to make only 0.2R - 0.8R. Highly unfavorable asymmetry. |
| **Category B (TP ≈ SL)** | Planned RR in [0.90R, 1.10R] | `166` | **`14.6%`** | Symmetric 1:1 payoff. |
| **Category C (TP > SL)** | Planned RR > 1.10R | `432` | **`38.1%`** | Narrow OB setups with favorable RR. |

- **Mean Planned RR:** `1.12R`
- **Median Planned RR:** `0.93R`
- **Minimum Planned RR:** `0.18R` (Worst case: risking $1.00 to make $0.18)
- **Maximum Planned RR:** `7.72R`

---

## 3. Cross-Asset Performance Breakdown

| Asset | Executed Trades | Wins | Losses | Win Rate % | Expectancy (R) | Total Realized R | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`BTCUSD`** | 274 | 67 | 207 | `24.45%` | `-0.3856R` | `-105.65R` | `0.49` |
| **`ETHUSD`** | 228 | 60 | 168 | `26.32%` | `-0.4810R` | `-109.67R` | `0.35` |
| **`SOLUSD`** | 256 | 69 | 187 | `26.95%` | `-0.5075R` | `-129.92R` | `0.31` |
| **`XRPUSD`** | 205 | 58 | 147 | `28.29%` | `-0.4102R` | `-84.10R` | `0.43` |

---

## 4. Month-by-Month Compounding Summary

| Month | Trades | Wins | Losses | Win Rate % | Monthly Total R | Expectancy (R) | Starting Capital (35% Risk) | Ending Capital (35% Risk) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `2024-06` | 25 | 5 | 20 | `20.0%` | `-14.78R` | `-0.5910R` | `$10.00` | `$0.00` |
| `2024-07` | 42 | 9 | 33 | `21.43%` | `-24.11R` | `-0.5742R` | `$0.00` | `$0.00` |
| `2024-08` | 33 | 6 | 27 | `18.18%` | `-23.69R` | `-0.7180R` | `$0.00` | `$0.00` |
| `2024-09` | 45 | 14 | 31 | `31.11%` | `-19.06R` | `-0.4235R` | `$0.00` | `$0.00` |
| `2024-10` | 42 | 7 | 35 | `16.67%` | `-26.74R` | `-0.6367R` | `$0.00` | `$0.00` |
| `2024-11` | 34 | 7 | 27 | `20.59%` | `-23.11R` | `-0.6797R` | `$0.00` | `$0.00` |
| `2024-12` | 44 | 8 | 36 | `18.18%` | `-29.43R` | `-0.6688R` | `$0.00` | `$0.00` |
| `2025-01` | 37 | 7 | 30 | `18.92%` | `-22.01R` | `-0.5948R` | `$0.00` | `$0.00` |
| `2025-02` | 26 | 6 | 20 | `23.08%` | `-16.50R` | `-0.6348R` | `$0.00` | `$0.00` |
| `2025-03` | 25 | 9 | 16 | `36.0%` | `-8.87R` | `-0.3548R` | `$0.00` | `$0.00` |
| `2025-04` | 35 | 13 | 22 | `37.14%` | `-11.38R` | `-0.3253R` | `$0.00` | `$0.00` |
| `2025-05` | 43 | 10 | 33 | `23.26%` | `-21.63R` | `-0.5031R` | `$0.00` | `$0.00` |
| `2025-06` | 37 | 6 | 31 | `16.22%` | `-24.67R` | `-0.6666R` | `$0.00` | `$0.00` |
| `2025-07` | 43 | 13 | 30 | `30.23%` | `-9.58R` | `-0.2228R` | `$0.00` | `$0.00` |
| `2025-08` | 37 | 13 | 24 | `35.14%` | `-9.25R` | `-0.2501R` | `$0.00` | `$0.00` |
| `2025-09` | 44 | 16 | 28 | `36.36%` | `-4.37R` | `-0.0994R` | `$0.00` | `$0.00` |
| `2025-10` | 35 | 9 | 26 | `25.71%` | `-17.27R` | `-0.4934R` | `$0.00` | `$0.00` |
| `2025-11` | 33 | 9 | 24 | `27.27%` | `-16.45R` | `-0.4984R` | `$0.00` | `$0.00` |
| `2025-12` | 49 | 11 | 38 | `22.45%` | `-25.34R` | `-0.5172R` | `$0.00` | `$0.00` |
| `2026-01` | 35 | 10 | 25 | `28.57%` | `-8.10R` | `-0.2315R` | `$0.00` | `$0.00` |
| `2026-02` | 25 | 4 | 21 | `16.0%` | `-17.35R` | `-0.6938R` | `$0.00` | `$0.00` |
| `2026-03` | 30 | 10 | 20 | `33.33%` | `-8.32R` | `-0.2775R` | `$0.00` | `$0.00` |
| `2026-04` | 39 | 10 | 29 | `25.64%` | `-17.80R` | `-0.4563R` | `$0.00` | `$0.00` |
| `2026-05` | 37 | 11 | 26 | `29.73%` | `-10.81R` | `-0.2921R` | `$0.00` | `$0.00` |
| `2026-06` | 23 | 7 | 16 | `30.43%` | `-8.58R` | `-0.3731R` | `$0.00` | `$0.00` |
| `2026-07` | 45 | 18 | 27 | `40.0%` | `-5.57R` | `-0.1238R` | `$0.00` | `$0.00` |
| `2026-08` | 20 | 6 | 14 | `30.0%` | `-4.55R` | `-0.2277R` | `$0.00` | `$0.00` |

---

## 5. Trade Ledger Sample (First 10 Executed Trades)

Below is an extract from [`docs/ai/fixed_0_7_tp_trades.csv`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/docs/ai/fixed_0_7_tp_trades.csv):

| # | Datetime | Asset | Dir | Entry | SL | TP | Planned RR | Outcome | Realized R | Starting $ | Net PnL $ (35% Risk) | Ending $ |
|---|---|---|:---:|---:|---:|---:|---:|:---:|---:|---:|---:|---:|
| 1 | `2024-06-10T14:00:00` | BTCUSD | SHORT | 69662.25 | 69813.0 | 69174.6142 | 3.2347R | **SL_HIT** | `-1.00R` | `$10.00` | `$-4.30` | `$5.70` |
| 2 | `2024-06-10T19:00:00` | BTCUSD | LONG | 69415.5 | 69186.0 | 69901.4085 | 2.1172R | **SL_HIT** | `-1.00R` | `$5.70` | `$-2.45` | `$3.25` |
| 3 | `2024-06-12T07:00:00` | ETHUSD | SHORT | 3530.6875 | 3547.0 | 3505.9727 | 1.5151R | **SL_HIT** | `-1.00R` | `$3.25` | `$-1.33` | `$1.92` |
| 4 | `2024-06-12T16:00:00` | BTCUSD | SHORT | 69769.75 | 70198.0 | 69281.3617 | 1.1404R | **TP_HIT** | `+1.14R` | `$1.92` | `$+0.68` | `$2.59` |
| 5 | `2024-06-13T19:00:00` | SOLUSD | LONG | 147.6325 | 145.645 | 148.6659 | 0.52R | **SL_HIT** | `-1.00R` | `$2.59` | `$-0.96` | `$1.63` |
| 6 | `2024-06-15T08:00:00` | XRPUSD | SHORT | 0.4794 | 0.4807 | 0.4761 | 2.6321R | **TIMEOUT_EXIT** | `+0.04R` | `$1.63` | `$-0.11` | `$1.53` |
| 7 | `2024-06-16T05:00:00` | BTCUSD | SHORT | 66300.625 | 66503.5 | 65836.5206 | 2.2876R | **SL_HIT** | `-1.00R` | `$1.53` | `$-0.66` | `$0.87` |
| 8 | `2024-06-17T01:00:00` | ETHUSD | LONG | 3604.4 | 3578.45 | 3629.6308 | 0.9723R | **SL_HIT** | `-1.00R` | `$0.87` | `$-0.34` | `$0.53` |
| 9 | `2024-06-17T06:00:00` | XRPUSD | LONG | 0.4864 | 0.4849 | 0.4898 | 2.2699R | **SL_HIT** | `-1.00R` | `$0.53` | `$-0.23` | `$0.30` |
| 10 | `2024-06-17T17:00:00` | BTCUSD | SHORT | 66670.875 | 66993.0 | 66204.1789 | 1.4488R | **SL_HIT** | `-1.00R` | `$0.30` | `$-0.12` | `$0.18` |

---

## 6. Scientific Attribution: Why Fixed 0.7% TP Fails

1. **Destruction of Risk/Reward Geometry:** In 47.3% of setups, the fixed 0.7% TP target is smaller than the SL distance (down to 0.18R). This means the trader risks 1.0R to gain a fraction of 1.0R. To break even on a 0.5R trade, a 67% win rate is required, yet raw SMC Order Blocks win only ~26% of the time with fixed 0.7% targets.
2. **Fixed Percentage Price Targets Ignore Volatility Regimes:** A 0.7% move on BTCUSD (~$600) behaves completely differently than 0.7% on SOLUSD or XRPUSD relative to average 1-hour ATR.
3. **Compound Decay:** Negative expectancy (-0.4458R) combined with high margin risk (35%) causes the initial $10 account to hit $0.00 within a few consecutive losses.
4. **Phase T Confirmation:** Phase T (+0.2081R, 1.38 PF) succeeds because its target scales proportionally to structural Order Block volatility and utilizes Ridge AI filtering.
