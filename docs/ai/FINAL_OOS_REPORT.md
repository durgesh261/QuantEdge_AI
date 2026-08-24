# QuantEdge AI — Final Out-of-Sample (OOS) Test Report

**Date**: 2026-08-24  
**Status**: **`AI_PROMOTION_STATUS = REJECTED`**  

---

## 1. Frozen Gate Configuration

- **Dataset**: Canonical Delta Exchange India `BTCUSD/1h/2026.csv` (5,583 candles)
- **Chronological Splits**: Train (212 setups), Val (41 setups), OOS Test (69 setups)
- **Purge Embargo**: Train $\to$ Val: 174h, Val $\to$ Test: 150h
- **Frozen Threshold**: `pred_realized_r >= +0.00R` (selected strictly on validation)
- **Model**: Multi-Output Random Forest (100 trees, max depth 8, 24 features)

---

## 2. Out-Of-Sample Benchmark Results

| Metric | SMC Only | SMC + AI | Change / Impact |
|---|---:|---:|---:|
| **Total Setups** | 69 | 69 | — |
| **Executed / Eligible Setups** | 69 | 34 | `49.3% coverage` |
| **Win Rate** | 31.9% (22) | 29.4% (10) | `-2.5%` |
| **Loss Rate** | 68.1% (47) | 70.6% (24) | `+2.5%` |
| **Timeout Rate** | 0.0% (0) | 0.0% (0) | `+0.0%` |
| **Mean R** | -0.0435R | -0.1176R | `-0.0741R` |
| **Median R** | -1.0000R | -1.0000R | `+0.0000R` |
| **Total Realized R** | -3.00R | -4.00R | `-1.00R` |
| **Profit Factor** | 0.936 | 0.833 | `-0.103` |
| **Expectancy** | -0.0435R | -0.1176R | `-0.0741R` |
| **Max Drawdown** | 18.00R | 19.00R | `+1.00R` |
| **Mean MFE** | 1.066R | 0.859R | `-0.207R` |
| **Mean MAE** | 1.272R | 1.250R | `-0.022R` |
| **Avg Holding Time** | 0.0 bars | 0.0 bars | `+0.0 bars` |

---

## 3. Gate Decision Rationale

- `OOS Expectancy (-0.1176R) does not exceed SMC Baseline (-0.0435R).`
- `OOS Profit Factor (0.833) is inferior to SMC Baseline (0.936).`

---

## 4. Production Rule

> [!CAUTION]
> `AI_PROMOTION_STATUS = REJECTED` ensures that the deterministic SMC engine continues as the authoritative execution engine. Live trade execution remains strictly protected from unverified AI filtering.