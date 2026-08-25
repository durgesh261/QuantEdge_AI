# Phase I — AI Filter Analysis

Generated (UTC): 2026-08-25T06:25:30.595429+00:00

## Question

Does the AI remove poor-quality REAL OB setups? Group C (AI-rejected) outcomes are computed
even though those trades would NOT be executed under SMC+AI — enabling direct filter-value analysis.

## Filter value (OOS window, gross R)

- Accepted expectancy (Group B): **+0.3571R**
- Rejected expectancy (Group C): **-0.0027R**
- Filter lift (B − A): **+0.3452R**
- Rejection value (A − C): **+0.0146R**

> Interpretation: the filter is genuinely useful iff accepted setups materially outperform rejected
> setups AND overall expectancy improves without unacceptable coverage loss.

## Pooled full-history view (gross R)

- Accepted expectancy: +1.1138R
- Rejected expectancy: -0.0978R
- Filter lift: +1.1288R

## Per-asset filter behaviour (OOS window)

| Asset | SMC setups | AI accepted | AI rejected | AI coverage | SMC E[R] | AI E[R] | ΔE[R] | SMC PF | AI PF | SMC WR | AI WR | SMC MDD | AI MDD | Net R (SMC) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSD | 24 | 1 | 23 | 4.2% | +0.2440 | -1.0000 | -1.2440 | 1.451 | 0.000 | 45.8% | 0.0% | 6.00 | 0.00 | +5.86 |
| ETHUSD | 26 | 3 | 23 | 11.5% | -0.0605 | +0.8094 | +0.8699 | 0.908 | 3.428 | 34.6% | 66.7% | 4.29 | 1.00 | -1.57 |
| SOLUSD | 32 | 0 | 32 | 0.0% | +0.0174 | — | — | 1.028 | — | 37.5% | —% | 7.56 | — | +0.56 |
| XRPUSD | 17 | 0 | 17 | 0.0% | -0.2156 | — | — | 0.695 | — | 29.4% | —% | 6.89 | — | -3.66 |

## Score-bucket calibration (all setups)

Monotonic calibration: **True**

| Bucket | Count | Pred mean R | Realized mean R | WR | PF | Median R | MFE | MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| < 0R | 321 | -0.3532 | -0.2519 | 27.7 | 0.651 | -1.0000 | 1.097 | 1.378 |
| 0-0.25R | 66 | +0.1432 | +0.1809 | 43.9 | 1.323 | -1.0000 | 1.244 | 0.947 |
| 0.25-0.50R | 36 | +0.3718 | +0.7656 | 66.7 | 3.297 | +1.7128 | 1.575 | 0.842 |
| 0.50-1.00R | 31 | +0.7189 | +1.1138 | 77.4 | 6.709 | +1.7143 | 1.921 | 0.593 |
| >= 1.00R | 0 | — | — | — | — | — | — | — |

## Conclusion

The AI separates OB trade quality: rejected trades underperform accepted trades by more than the pre-declared 0.10R materiality margin.