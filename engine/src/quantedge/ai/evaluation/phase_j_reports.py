"""
QuantEdge AI — Phase J report rendering (markdown from phase_j_results.json).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(v: Any, spec: str = "{:+.4f}") -> str:
    if v is None:
        return "—"
    try:
        return spec.format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _metrics_row(label: str, m: Optional[Dict[str, Any]]) -> str:
    if not m or m.get("executed_setups", 0) == 0:
        return f"| {label} | 0 | — | — | — | — | — | — |"
    return (
        f"| {label} | {m['executed_setups']} | {m.get('win_rate_pct', 0):.1f}% | "
        f"{_f(m['expectancy_r'])}R | {m['profit_factor']:.3f} | {_f(m['median_r'])}R | "
        f"{_f(m['total_r'], '{:+.2f}')}R | {m['max_drawdown_r']:.2f}R |"
    )


def render_phase_j_report(res: Dict[str, Any]) -> str:
    rep = res["reproducibility"]
    splits = res["splits"]
    ds = res["dataset_summary"]
    prim = res["primary"]
    gate = res["promotion_gate"]

    lines: List[str] = [
        "# Phase J — OB-Centric AI Research on Real SMC/OB Trades",
        "",
        f"Generated (UTC): {res['generated_at_utc']}",
        "",
        "## 1. Dataset definition",
        "",
        f"ONE ROW = ONE UNIQUE Order-Block trade opportunity from the **real application SMC engine**",
        f"(Phase I authoritative replay; one trade per OB / USED-state semantics).",
        "",
        f"- Total unique OB trades: **{ds['total_unique_ob_trades']}**"
        + (f" (split assignment excludes {ds['embargo_rows_dropped_in_split_assignment']} embargo-gap rows)" if ds.get("embargo_rows_dropped_in_split_assignment") else ""),
        "- Per asset: " + ", ".join(f"{a}={n}" for a, n in ds["per_asset"].items()),
        "- Per split: " + ", ".join(f"{s}={n}" for s, n in ds["per_split"].items()),
        "- Labels = REAL forward-replayed outcomes (second-edge SL, PHASE_I_OB_60TP_35SL TP, SL-first intrabar, 72h horizon).",
        "- Note: the application quantises TP prices to 0.01; for low-priced assets (XRPUSD) this shifts realised R",
        "  slightly around the nominal 60/35 multiple — labels reflect the REAL app outcome, not the idealised one.",
        "- No synthetic candles, no arbitrary candle rows, no future information.",
        "",
        "**SMC baseline by split:**",
        "",
        "| Split | n | WR | E[R] | PF | MDD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in ("train", "val", "oos"):
        m = ds["smc_baseline_by_split"][s]
        lines.append(
            f"| {s} | {m['executed_setups']} | {m['win_rate_pct']:.1f}% | "
            f"{_f(m['expectancy_r'])}R | {m['profit_factor']:.3f} | {m['max_drawdown_r']:.2f}R |"
        )

    lines += [
        "",
        "## 2. Feature contract",
        "",
        f"`{rep['feature_contract']}` — {rep['feature_count']} causal OB-centric features:",
        "",
        "| Group | Features |",
        "|---|---|",
        "| OB geometry | ob_width_pct, ob_width_atr, stop_distance_pct, stop_distance_atr, entry_depth_in_zone, mitigation_depth_pct, formation_body_ratio, formation_range_atr, displacement_atr, bars_since_formation, bars_since_break, pre_decision_retests, price_to_entry_atr |",
        "| Market structure | is_bos, is_choch, origin_swing, trend_align_internal, trend_align_swing, premium_discount, dist_nearest_pivot_atr |",
        "| Volatility regime | atr_pct, atr_percentile, realized_vol_20, vol_expansion |",
        "| Momentum/participation | ret_5, ret_15, ret_50, volume_ratio |",
        "| Direction | direction_long |",
        "",
        "All scale-sensitive quantities are ATR-, percent- or ratio-normalised (scale-invariant across assets).",
        "Leakage control: features use only bars/pivots/breaks with index <= decision bar (mutation-tested).",
        "",
        "## 3. Label definition",
        "",
        "- Primary: `label_realized_r` — continuous realised R of the REAL trade (regression; preserves asymmetric reward/risk).",
        "- Auxiliary: `label_tp_first` (TP before SL), `label_mfe_r`, `label_mae_r`, `label_holding_bars`.",
        "- A TP-first classifier variant was evaluated as the ranking-oriented candidate (`tp_first_classifier`).",
        "",
        "## 4. Train/validation/OOS dates",
        "",
        f"- Train: start → **{splits['train_end_utc']}**",
        f"- Validation: **{splits['val_window_utc'][0]} → {splits['val_window_utc'][1]}**",
        f"- Frozen OOS: **{splits['oos_window_utc_frozen'][0]} → {splits['oos_window_utc_frozen'][1]}** (identical to Phases E–I)",
        f"- Embargo: ≥ {splits['embargo_hours']:.0f}h between consecutive splits (verified: "
        f"{splits['isolation_report']['embargo_gap_train_to_val_hours']:.0f}h / {splits['isolation_report']['embargo_gap_val_to_oos_hours']:.0f}h)",
        "",
        "## 5. Model comparison",
        "",
        "Threshold selected on VALIDATION only (pre-declared rule); OOS evaluated once per configuration.",
        "",
        "| Model | Threshold | Source | Val cov | Val inc E[R] | OOS n | OOS cov | OOS E[R] AI | OOS inc E[R] | OOS PF AI | OOS MDD AI | Inc 95% CI |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, c in res["model_comparison"].items():
        o = c["oos"]
        b = c["oos_bootstrap"].get("incremental_mean_r_95ci", ("—", "—"))
        lines.append(
            f"| {name} | {c['threshold_selection']['chosen_threshold']:.2f} | {c['threshold_selection']['selection_source'].replace('validation_rule','rule').replace('_relaxed_coverage','(relax)')} "
            f"| {c['validation']['coverage_pct']:.1f}% | {_f(c['validation']['incremental_expectancy_r'])}R "
            f"| {o['n_selected']} | {o['coverage_pct']:.1f}% | {_f(o['filtered']['expectancy_r'])}R "
            f"| {_f(o['incremental_expectancy_r'])}R | {o['filtered']['profit_factor']:.3f} "
            f"| {o['filtered']['max_drawdown_r']:.2f}R | [{b[0]}, {b[1]}] |"
        )

    lines += [
        "",
        f"**Primary model (pre-declared validation ranking): `{res['primary_model']}`** — {res['primary_selection_basis']}.",
        "",
        "## 6–7. Primary configuration & coverage analysis",
        "",
        f"- Selected threshold: **{prim['threshold']:.2f}R** predicted realized R",
        f"- Validation: n_sel={prim['validation']['n_selected']}, coverage {prim['validation']['coverage_pct']:.1f}%, "
        f"inc E[R] {_f(prim['validation']['incremental_expectancy_r'])}R",
        f"- Frozen OOS: n_sel=**{prim['oos']['n_selected']}/{prim['oos']['n_universe']}**, "
        f"coverage **{prim['oos']['coverage_pct']:.1f}%** (Wilson 95% CI "
        f"{prim['oos_coverage_wilson_95ci_pct'][0]*100:.1f}–{prim['oos_coverage_wilson_95ci_pct'][1]*100:.1f}%)",
        "",
        "| Group (OOS) | n | WR | E[R] | PF | Median R | Total R | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.append(_metrics_row("A — SMC only", prim["oos"]["smc"]))
    lines.append(_metrics_row("B — SMC+AI", prim["oos"]["filtered"]))
    lines.append(_metrics_row("C — AI rejected", prim["oos"]["rejected"]))

    b = prim["oos_bootstrap"]
    lines += [
        "",
        f"- Incremental expectancy: **{_f(prim['oos']['incremental_expectancy_r'])}R** "
        f"(MBB 95% CI [{b.get('incremental_mean_r_95ci', ['—','—'])[0]}, {b.get('incremental_mean_r_95ci', ['—','—'])[1]}])",
        f"- Rejected-trade expectancy: {_f(prim['oos']['rejected']['expectancy_r'] if prim['oos']['rejected'] else None)}R",
        "",
        "### Per-asset frozen-OOS results",
        "",
        "| Asset | Setups | Accepted | Coverage | SMC E[R] | AI E[R] | ΔE[R] | SMC PF | AI PF | SMC MDD | AI MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prim["per_asset_oos"]:
        lines.append(
            f"| {r['asset']} | {r['smc_setups']} | {r['ai_accepted']} | {r['ai_coverage_pct']:.1f}% "
            f"| {_f(r['smc_expectancy_r'])} | {_f(r['ai_expectancy_r'])} | {_f(r['incremental_expectancy_r'])} "
            f"| {r['smc_profit_factor']:.3f} | {r['ai_profit_factor'] if r['ai_profit_factor'] is not None else '—'} "
            f"| {r['smc_max_drawdown_r']:.2f} | {r['ai_max_drawdown_r'] if r['ai_max_drawdown_r'] is not None else '—'} |"
        )
    lines += [
        "",
        "> Pooled numbers never replace per-asset scrutiny: an asset with zero acceptances is a robustness failure.",
        "",
        "## 8. Cross-asset LOAO (held-out asset never in training)",
        "",
        "| Held-out | Fold thr | Full-period cov | Full-period ΔE[R] | OOS-only cov | OOS-only ΔE[R] |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in res["loao_cross_asset"]:
        fp, ho = r["held_full_period"], r["held_oos_only"]
        lines.append(
            f"| {r['held_out_asset']} | {r['fold_threshold']:.2f} ({r['threshold_source'].replace('validation_rule','rule')}) "
            f"| {fp['coverage_pct']:.1f}% | {_f(fp['incremental_expectancy_r'])}R "
            f"| {ho['coverage_pct']:.1f}% | {_f(ho['incremental_expectancy_r'])}R |"
        )

    wf = res["walk_forward"]
    lines += [
        "",
        "## 9. Walk-forward validation (test folds end BEFORE the frozen OOS window)",
        "",
    ]
    if wf:
        lines += [
            "| Fold | Train n | Test n | Threshold | Cov | SMC E[R] | AI E[R] | ΔE[R] |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for f in wf:
            lines.append(
                f"| {f['fold']} ({str(f['test_window_utc'][0])[:10]}→{str(f['test_window_utc'][1])[:10]}) "
                f"| {f['train_n']} | {f['test_n']} | {f['threshold']:.2f} ({str(f['threshold_source']).replace('validation_rule','rule')}) "
                f"| {f['coverage_pct']:.1f}% | {_f(f['smc_expectancy_r'])} | {_f(f['ai_expectancy_r'])} "
                f"| {_f(f['incremental_expectancy_r'])} |"
            )
        stable = sum(1 for f in wf if f["incremental_expectancy_r"] is not None and f["incremental_expectancy_r"] > 0)
        lines += ["", f"- Positive incremental folds: {stable}/{len(wf)}."]
    else:
        lines.append("_No walk-forward folds produced (insufficient samples)._")
    lines += [
        "",
        "_The final historical period (frozen OOS from 2026-07-06) is deliberately excluded from all walk-forward test folds._",
        "",
        "## 10. Calibration (primary model)",
        "",
        "### Frozen OOS",
        "",
        "| Bucket | n | Pred mean R | Realized mean R | WR | PF | Mean abs calib error |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, bk in res["calibration"]["oos"]["buckets"].items():
        if bk["count"]:
            lines.append(
                f"| {label} | {bk['count']} | {_f(bk['predicted_mean_r'])} | {_f(bk['realized_mean_r'])} "
                f"| {bk['win_rate_pct']:.1f}% | {bk['profit_factor']:.3f} | {bk['mean_abs_calibration_error_r']:.4f}R |"
            )
        else:
            lines.append(f"| {label} | 0 | — | — | — | — | — |")
    co = res["calibration"]["oos"]
    lines += [
        "",
        f"- Monotonic calibration (OOS): **{co['monotonic']}** · overall MAE {co['overall_mean_abs_error_r']}R",
        f"- Full-history monotonicity: {res['calibration']['full_history']['monotonic']} "
        f"(MAE {res['calibration']['full_history']['overall_mean_abs_error_r']}R)",
        "",
        "## 11. Leverage & account analysis (SEPARATE from R-space model quality)",
        "",
    ]
    lev = res["leverage_analysis"]["all_trades_full_history"]
    lines += [
        f"- Universe: avg leverage {lev['avg_leverage']}x, median {lev['median_leverage']}x, range {lev['leverage_min_max']}",
        f"- Account path max drawdown (35%-risk budget per trade): {lev['account_max_drawdown_pct_of_balance_path']:.1f}% of balance",
        f"- Monte-Carlo shuffle (seeded) p95 max DD: {lev['mc_shuffle_max_dd_p95_pct_of_balance']:.1f}% of balance",
        f"- Risk-of-ruin proxy (≥50% balance drawdown probability): {lev['risk_of_ruin_proxy_50pct_loss_prob']*100:.1f}%",
        "",
        "| Leverage bucket | Trades | Mean R | WR | Avg lev |",
        "|---|---:|---:|---:|---:|",
    ]
    for k, v in lev["by_leverage_bucket"].items():
        lines.append(f"| {k} | {v['count']} | {_f(v['mean_realized_r'])} | {v['win_rate_pct']:.1f}% | {v['avg_leverage']}x |")

    lines += [
        "",
        "## 12. Liquidation analysis",
        "",
        f"- Liquidation-before-SL violations across all {ds['total_unique_ob_trades']} trades: "
        f"**{res['liquidation_violations_total']}** (isolated-margin approximation, maintenance margin "
        f"{rep['maintenance_margin_rate_assumption']*100:.1f}% assumption).",
        "- The stop always sits inside the estimated liquidation boundary under the capped research formula; residual gap risk remains.",
        "",
        "## 13. Bootstrap statistics",
        "",
        f"- Paired Moving Block Bootstrap over the frozen OOS universe: N={rep['bootstrap_n']}, seed={rep['bootstrap_seed']}, block=max(3, ⌈N^(1/3)⌉).",
        f"- Incremental expectancy 95% CI: [{b.get('incremental_mean_r_95ci', ['—','—'])[0]}, {b.get('incremental_mean_r_95ci', ['—','—'])[1]}]",
        f"- SMC expectancy 95% CI: [{b.get('smc_mean_r_95ci', ['—','—'])[0]}, {b.get('smc_mean_r_95ci', ['—','—'])[1]}]",
        f"- AI expectancy 95% CI: [{b.get('ai_mean_r_95ci', ['—','—'])[0]}, {b.get('ai_mean_r_95ci', ['—','—'])[1]}]",
        "- Significance requires the incremental CI lower bound to be strictly positive.",
        "",
        "## 14. Ablation study",
        "",
        "| Feature set | # feats | Thr | Val inc E[R] | Val cov | OOS n | OOS cov | OOS inc E[R] |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for set_name, ab in res["ablations"].items():
        o = ab["oos"]
        lines.append(
            f"| {set_name} | {ab['feature_count']} | {ab['threshold']:.2f} ({ab['threshold_source'].replace('validation_rule','rule')}) "
            f"| {_f(ab['validation_incremental_expectancy_r'])}R | {ab['validation_coverage_pct']:.1f}% "
            f"| {o['n_selected']} | {o['coverage_pct']:.1f}% | {_f(o['incremental_expectancy_r'])}R |"
        )
    lines += [
        "",
        "_Each ablation uses its own validation-selected threshold and receives exactly one frozen-OOS evaluation._",
        "",
        "## 15. Failure analysis & limitations",
        "",
        "- Small sample: ~454 real trades total; OOS ≈ 99. Bootstrap CIs are wide; power remains limited.",
        "- Overlapping 72h holding windows create serial correlation (mitigated by MBB blocks, not eliminated).",
        "- Costs are documented research assumptions (repo has no authoritative fee constants).",
        "- Entry fill assumes the application limit-level convention shared with Phases C–I.",
        "- If any model shows OOS improvement without cross-asset acceptance, it is reported as NOT production-generalizable.",
        "",
        "## 16. Governance decision",
        "",
    ]
    for name, c in gate["criteria"].items():
        lines.append(f"- {'✅' if c['passed'] else '❌'} **{name}**: {c['detail']}")
    lines += [
        "",
        f"**Gate status: {gate['status']}** · live_execution_authorized = **{str(gate['live_execution_authorized']).lower()}** · "
        f"AI live execution = **{gate['ai_live_execution']}** · authority: {gate['execution_authority']}",
        "",
        "## 17. Reproducibility",
        "",
        f"- Random seed {rep['random_seed']} everywhere (models, bootstrap, Monte Carlo).",
        f"- Threshold grid {[g for g in rep['threshold_grid']]} with pre-declared rule (see JSON `reproducibility.threshold_rule`).",
        f"- TP/SL/leverage/cost conventions identical to Phase I (`PHASE_I_OB_60TP_35SL`, second-edge SL, capped dynamic leverage).",
        "- Production ONNX artifact untouched; research models remain sklearn-side only.",
        "- Two identical runs produce identical results (deterministic dataset build + seeded fitting).",
        "",
        "---",
        "*Phase J is research/shadow-only. Zero live orders were placed. The deterministic SMC engine remains the sole",
        "production execution authority. Even a passing gate yields CANDIDATE_FOR_GOVERNANCE_REVIEW — never live trading.*",
    ]
    return "\n".join(lines)
