# Phase J — OB-Centric AI Research on Real SMC/OB Trades

Generated (UTC): 2026-08-25T07:26:31.010535+00:00

## 1. Dataset definition

ONE ROW = ONE UNIQUE Order-Block trade opportunity from the **real application SMC engine**
(Phase I authoritative replay; one trade per OB / USED-state semantics).

- Total unique OB trades: **454** (split assignment excludes 5 embargo-gap rows)
- Per asset: BTCUSD=123, ETHUSD=103, SOLUSD=130, XRPUSD=98
- Per split: oos=99, train=291, val=59
- Labels = REAL forward-replayed outcomes (second-edge SL, PHASE_I_OB_60TP_35SL TP, SL-first intrabar, 72h horizon).
- Note: the application quantises TP prices to 0.01; for low-priced assets (XRPUSD) this shifts realised R
  slightly around the nominal 60/35 multiple — labels reflect the REAL app outcome, not the idealised one.
- No synthetic candles, no arbitrary candle rows, no future information.

**SMC baseline by split:**

| Split | n | WR | E[R] | PF | MDD |
|---|---:|---:|---:|---:|---:|
| train | 291 | 36.8% | -0.0194R | 0.969 | 29.72R |
| val | 59 | 33.9% | -0.0530R | 0.918 | 13.51R |
| oos | 99 | 37.4% | +0.0119R | 1.019 | 12.58R |

## 2. Feature contract

`phase-j-ob-causal-v1` — 29 causal OB-centric features:

| Group | Features |
|---|---|
| OB geometry | ob_width_pct, ob_width_atr, stop_distance_pct, stop_distance_atr, entry_depth_in_zone, mitigation_depth_pct, formation_body_ratio, formation_range_atr, displacement_atr, bars_since_formation, bars_since_break, pre_decision_retests, price_to_entry_atr |
| Market structure | is_bos, is_choch, origin_swing, trend_align_internal, trend_align_swing, premium_discount, dist_nearest_pivot_atr |
| Volatility regime | atr_pct, atr_percentile, realized_vol_20, vol_expansion |
| Momentum/participation | ret_5, ret_15, ret_50, volume_ratio |
| Direction | direction_long |

All scale-sensitive quantities are ATR-, percent- or ratio-normalised (scale-invariant across assets).
Leakage control: features use only bars/pivots/breaks with index <= decision bar (mutation-tested).

## 3. Label definition

- Primary: `label_realized_r` — continuous realised R of the REAL trade (regression; preserves asymmetric reward/risk).
- Auxiliary: `label_tp_first` (TP before SL), `label_mfe_r`, `label_mae_r`, `label_holding_bars`.
- A TP-first classifier variant was evaluated as the ranking-oriented candidate (`tp_first_classifier`).

## 4. Train/validation/OOS dates

- Train: start → **2026-06-03T18:00:00+00:00**
- Validation: **2026-06-06T20:00:00+00:00 → 2026-07-02T22:00:00+00:00**
- Frozen OOS: **2026-07-06T00:00:00+00:00 → 2026-08-21T14:00:00+00:00** (identical to Phases E–I)
- Embargo: ≥ 72h between consecutive splits (verified: 125h / 86h)

## 5. Model comparison

Threshold selected on VALIDATION only (pre-declared rule); OOS evaluated once per configuration.

| Model | Threshold | Source | Val cov | Val inc E[R] | OOS n | OOS cov | OOS E[R] AI | OOS inc E[R] | OOS PF AI | OOS MDD AI | Inc 95% CI |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ridge | 0.00 | rule | 78.0% | +0.2676R | 38 | 38.4% | +0.3017R | +0.2898R | 1.573 | 4.85R | [-0.0858, 0.5962] |
| random_forest | 0.00 | rule | 67.8% | +0.0931R | 34 | 34.3% | +0.0560R | +0.0441R | 1.091 | 7.56R | [-0.3743, 0.3655] |
| extra_trees | 0.00 | rule | 71.2% | +0.1246R | 38 | 38.4% | +0.2813R | +0.2694R | 1.534 | 6.56R | [-0.1164, 0.5839] |
| hist_gbdt | 0.50 | fallback_frozen_default | 33.9% | -0.2049R | 11 | 11.1% | +0.7828R | +0.7709R | 3.153 | 2.00R | [-0.0072, 1.4404] |
| tp_first_classifier | 0.10 | rule | 44.1% | +0.0259R | 15 | 15.2% | +0.2665R | +0.2546R | 1.500 | 3.00R | [-0.2812, 0.8558] |

**Primary model (pre-declared validation ranking): `ridge`** — max validation incremental expectancy among rule-eligible thresholds (OOS never used).

## 6–7. Primary configuration & coverage analysis

- Selected threshold: **0.00R** predicted realized R
- Validation: n_sel=46, coverage 78.0%, inc E[R] +0.2676R
- Frozen OOS: n_sel=**38/99**, coverage **38.4%** (Wilson 95% CI 29.4–48.2%)

| Group (OOS) | n | WR | E[R] | PF | Median R | Total R | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| A — SMC only | 99 | 37.4% | +0.0119R | 1.019 | -1.0000R | +1.18R | 12.58R |
| B — SMC+AI | 38 | 47.4% | +0.3017R | 1.573 | -1.0000R | +11.47R | 4.85R |
| C — AI rejected | 61 | 31.1% | -0.1687R | 0.755 | -1.0000R | -10.29R | 11.73R |

- Incremental expectancy: **+0.2898R** (MBB 95% CI [-0.0858, 0.5962])
- Rejected-trade expectancy: -0.1687R

### Per-asset frozen-OOS results

| Asset | Setups | Accepted | Coverage | SMC E[R] | AI E[R] | ΔE[R] | SMC PF | AI PF | SMC MDD | AI MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSD | 24 | 6 | 25.0% | +0.2440 | +0.8095 | +0.5655 | 1.451 | 3.429 | 6.00 | 2.0 |
| ETHUSD | 26 | 11 | 42.3% | -0.0605 | -0.2598 | -0.1993 | 0.908 | 0.643 | 4.29 | 4.0 |
| SOLUSD | 32 | 18 | 56.2% | +0.0174 | +0.5079 | +0.4905 | 1.028 | 2.143 | 7.56 | 4.0 |
| XRPUSD | 17 | 3 | 17.6% | -0.2156 | +0.1080 | +0.3236 | 0.695 | 1.162 | 6.89 | 2.0 |

> Pooled numbers never replace per-asset scrutiny: an asset with zero acceptances is a robustness failure.

## 8. Cross-asset LOAO (held-out asset never in training)

| Held-out | Fold thr | Full-period cov | Full-period ΔE[R] | OOS-only cov | OOS-only ΔE[R] |
|---|---:|---:|---:|---:|---:|
| BTCUSD | 0.00 (rule) | 46.7% | +0.3050R | 20.8% | +0.3846R |
| ETHUSD | 0.10 (rule) | 39.8% | +0.0997R | 38.5% | -0.1253R |
| SOLUSD | 0.10 (rule) | 51.6% | +0.1782R | 46.9% | +0.4309R |
| XRPUSD | 0.50 (rule) | 15.6% | +0.1702R | 5.9% | -0.7844R |

## 9. Walk-forward validation (test folds end BEFORE the frozen OOS window)

| Fold | Train n | Test n | Threshold | Cov | SMC E[R] | AI E[R] | ΔE[R] |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 (2026-04-27→2026-05-31) | 157 | 72 | 0.50 (rule) | 16.7% | -0.1086 | -0.3510 | -0.2424 |
| 2 (2026-05-27→2026-06-30) | 226 | 63 | 0.00 (rule) | 66.7% | +0.1345 | +0.3860 | +0.2515 |

- Positive incremental folds: 1/2.

_The final historical period (frozen OOS from 2026-07-06) is deliberately excluded from all walk-forward test folds._

## 10. Calibration (primary model)

### Frozen OOS

| Bucket | n | Pred mean R | Realized mean R | WR | PF | Mean abs calib error |
|---|---:|---:|---:|---:|---:|---:|
| < 0R | 61 | -0.3520 | -0.1687 | 31.1% | 0.755 | 1.0509R |
| 0-0.25R | 17 | +0.1098 | +0.3120 | 47.1% | 1.589 | 1.3801R |
| 0.25-0.50R | 13 | +0.3614 | +0.2544 | 46.1% | 1.473 | 1.3337R |
| 0.50-1.00R | 8 | +0.7251 | +0.3568 | 50.0% | 1.714 | 1.4282R |
| >= 1.00R | 0 | — | — | — | — | — |

- Monotonic calibration (OOS): **False** · overall MAE 1.175R
- Full-history monotonicity: False (MAE 1.142R)

## 11. Leverage & account analysis (SEPARATE from R-space model quality)

- Universe: avg leverage 54.55x, median 53.0x, range [13, 100]
- Account path max drawdown (35%-risk budget per trade): 1040.3% of balance
- Monte-Carlo shuffle (seeded) p95 max DD: 1675.1% of balance
- Risk-of-ruin proxy (≥50% balance drawdown probability): 100.0%

| Leverage bucket | Trades | Mean R | WR | Avg lev |
|---|---:|---:|---:|---:|
| 1-19x | 6 | +0.5504 | 66.7% | 16.5x |
| 20-39x | 123 | +0.1296 | 41.5% | 32.0x |
| 40-69x | 219 | -0.0334 | 35.6% | 54.6x |
| 70-100x | 101 | -0.1933 | 30.7% | 84.2x |

## 12. Liquidation analysis

- Liquidation-before-SL violations across all 454 trades: **0** (isolated-margin approximation, maintenance margin 0.5% assumption).
- The stop always sits inside the estimated liquidation boundary under the capped research formula; residual gap risk remains.

## 13. Bootstrap statistics

- Paired Moving Block Bootstrap over the frozen OOS universe: N=2000, seed=42, block=max(3, ⌈N^(1/3)⌉).
- Incremental expectancy 95% CI: [-0.0858, 0.5962]
- SMC expectancy 95% CI: [-0.2571, 0.2541]
- AI expectancy 95% CI: [-0.1856, 0.6543]
- Significance requires the incremental CI lower bound to be strictly positive.

## 14. Ablation study

| Feature set | # feats | Thr | Val inc E[R] | Val cov | OOS n | OOS cov | OOS inc E[R] |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_candle_only | 10 | 0.00 (rule) | +0.0697R | 66.1% | 32 | 32.3% | +0.1897R |
| B_ob_geometry_only | 14 | 0.25 (rule) | +0.3184R | 33.9% | 12 | 12.1% | -0.1074R |
| C_structure_only | 8 | 0.10 (rule) | +0.5506R | 35.6% | 39 | 39.4% | +0.0974R |
| D_geometry_plus_structure | 21 | 0.40 (rule) | +0.3668R | 18.6% | 9 | 9.1% | +0.1946R |
| E_full_causal_ob | 29 | 0.00 (rule) | +0.2676R | 78.0% | 38 | 38.4% | +0.2898R |

_Each ablation uses its own validation-selected threshold and receives exactly one frozen-OOS evaluation._

## 15. Failure analysis & limitations

- Small sample: ~454 real trades total; OOS ≈ 99. Bootstrap CIs are wide; power remains limited.
- Overlapping 72h holding windows create serial correlation (mitigated by MBB blocks, not eliminated).
- Costs are documented research assumptions (repo has no authoritative fee constants).
- Entry fill assumes the application limit-level convention shared with Phases C–I.
- If any model shows OOS improvement without cross-asset acceptance, it is reported as NOT production-generalizable.

## 16. Governance decision

- ✅ **C1_oos_incremental_expectancy_positive**: SMC +0.0119R vs AI +0.3017R (inc 0.2898)
- ✅ **C2_oos_profit_factor_improvement**: SMC PF 1.019 vs AI PF 1.573
- ✅ **C3_oos_drawdown_improvement**: SMC MDD 12.58R vs AI MDD 4.85R
- ✅ **C4_minimum_ai_coverage**: AI coverage 38.38% of OOS SMC setups (floor 15.0%)
- ❌ **C5_bootstrap_ci_lower_bound_positive**: Incremental expectancy MBB 95% CI lower bound -0.0858R
- ✅ **C6_cross_asset_robustness**: 3/4 assets non-negative incremental; worst -0.1993R
- ✅ **C7_rejected_trades_materially_worse**: Accepted +0.3017R vs rejected -0.1687R (gap needed >= 0.10R)
- ✅ **C8_no_unacceptable_liquidation_risk**: 0 trades with estimated liquidation before SL

**Gate status: REJECTED** · live_execution_authorized = **false** · AI live execution = **BLOCKED_BY_SYSTEM** · authority: DETERMINISTIC_SMC

## 17. Reproducibility

- Random seed 42 everywhere (models, bootstrap, Monte Carlo).
- Threshold grid [0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6] with pre-declared rule (see JSON `reproducibility.threshold_rule`).
- TP/SL/leverage/cost conventions identical to Phase I (`PHASE_I_OB_60TP_35SL`, second-edge SL, capped dynamic leverage).
- Production ONNX artifact untouched; research models remain sklearn-side only.
- Two identical runs produce identical results (deterministic dataset build + seeded fitting).

---
*Phase J is research/shadow-only. Zero live orders were placed. The deterministic SMC engine remains the sole
production execution authority. Even a passing gate yields CANDIDATE_FOR_GOVERNANCE_REVIEW — never live trading.*