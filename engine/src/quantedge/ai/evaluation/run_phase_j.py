"""
QuantEdge AI — Phase J Runner: OB-Centric AI Research on REAL SMC/OB Trades.

Pipeline (research/shadow only — ZERO live orders):
    1. Build the real-OB dataset (one row per unique OB trade opportunity).
    2. Assign frozen train/val/OOS splits (Phase H provenance boundaries).
    3. Model comparison (5 seeded candidates) with validation-only threshold rule.
    4. Frozen OOS evaluation ONCE per configuration + paired MBB CIs.
    5. Leave-One-Asset-Out cross-asset validation.
    6. Walk-forward validation (test folds strictly BEFORE the frozen OOS).
    7. Calibration buckets, leverage/account analysis, liquidation safety.
    8. Ablation study over feature groups.
    9. Phase J governance gate (REJECTED unless every criterion passes).

Outputs:
    docs/ai/phase_j_results.json
    docs/ai/PHASE_J_OB_AI_RESEARCH_REPORT.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from quantedge.ai.evaluation.phase_i_ob_replay import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    MAINTENANCE_MARGIN_RATE,
    compute_extended_metrics,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (
    ABLATION_SETS,
    FEATURE_DIM,
    LABEL_REALIZED_R,
    OB_FEATURE_NAMES,
    build_phase_j_dataset,
)
from quantedge.ai.evaluation.phase_j_research import (
    DEFAULT_THRESHOLD,
    EMBARGO_HOURS,
    OOS_END_UTC,
    OOS_START_UTC,
    RANDOM_SEED,
    THRESHOLD_GRID,
    TRAIN_END_UTC,
    VAL_END_UTC,
    VAL_START_UTC,
    assign_frozen_splits,
    calibration_buckets,
    evaluate_configuration,
    evaluate_phase_j_gate,
    group_metrics,
    leverage_analysis,
    paired_config_bootstrap,
    run_loao,
    run_walk_forward,
    verify_split_isolation,
    wilson_interval,
)

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
PRIMARY_MODEL = "random_forest"

COMPARISON_MODELS = ["ridge", "random_forest", "extra_trees", "hist_gbdt", "tp_first_classifier"]


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


def run_phase_j(verbose: bool = True) -> Dict[str, Any]:
    import time

    t0 = time.time()
    repo_root = _get_repo_root()
    canonical_base = repo_root / "data" / "canonical" / "delta_exchange_india"

    if verbose:
        print("=" * 78)
        print("PHASE J — OB-CENTRIC AI RESEARCH ON REAL SMC/OB TRADES (shadow only)")
        print("=" * 78)

    # ── 1. Dataset ────────────────────────────────────────────────────────────
    df_raw = build_phase_j_dataset(canonical_base, SYMBOLS, verbose=verbose)
    df = assign_frozen_splits(df_raw)
    isolation = verify_split_isolation(df)
    embargo_dropped = len(df_raw) - len(df)
    if verbose:
        for k, v in isolation.items():
            print(f"  {k}: {v}")

    # Liquidation violations across the whole universe (Phase I conventions)
    liq_violations = _count_liquidation_violations(df)

    # ── 2. Model comparison (validation-selected thresholds; single OOS eval) ─
    comparisons: Dict[str, Any] = {}

    for name in COMPARISON_MODELS:
        res = evaluate_configuration(df, name, list(OB_FEATURE_NAMES))
        comparisons[name] = {
            "threshold_selection": {
                "chosen_threshold": res.threshold_selection["chosen_threshold"],
                "selection_source": res.threshold_selection["selection_source"],
            },
            "validation": res.validation,
            "oos": res.oos,
            "oos_bootstrap": res.oos_bootstrap,
            "per_asset_oos": res.per_asset_oos,
        }
        if verbose:
            print(
                f"  {name}: thr={res.threshold_selection['chosen_threshold']:.2f} "
                f"({res.threshold_selection['selection_source']}) | "
                f"val_cov={res.validation['coverage_pct']:.1f}% val_inc={res.validation['incremental_expectancy_r']:+.4f}R | "
                f"oos_n={res.oos['n_selected']} oos_inc={res.oos['incremental_expectancy_r']:+.4f}R"
            )

    # Choose the primary research configuration by PRE-DECLARED ranking:
    # max validation incremental expectancy among models whose validation
    # threshold was rule-selected (never by OOS values).
    def _val_key(name: str) -> tuple:
        v = comparisons[name]["validation"]
        src = comparisons[name]["threshold_selection"]["selection_source"]
        eligible = src.startswith("validation_rule")
        return (1 if eligible else 0, v["incremental_expectancy_r"], v["filtered"]["expectancy_r"])

    ranked = sorted(COMPARISON_MODELS, key=_val_key, reverse=True)
    primary_model = ranked[0]
    if verbose:
        print(f"  PRIMARY MODEL (by pre-declared validation ranking): {primary_model}")

    # ── 3. Full evaluation of primary configuration ───────────────────────────
    primary = evaluate_configuration(df, primary_model, list(OB_FEATURE_NAMES))

    # Rebuild the OOS prediction mask for calibration/paired stats (deterministic refit)
    from quantedge.ai.evaluation.phase_j_research import fit_predict, model_candidates

    train_df = df[df["split"] == "train"]
    val_df = df[df["split"] == "val"]
    oos_df = df[df["split"] == "oos"]
    applied = fit_predict(
        primary_model, model_candidates()[primary_model], train_df,
        {"val": val_df, "oos": oos_df}, list(OB_FEATURE_NAMES),
    )
    thr_primary = primary.threshold_selection["chosen_threshold"]
    oos_mask = applied["oos"] >= thr_primary
    r_all = oos_df[LABEL_REALIZED_R].to_numpy(dtype=float)

    coverage_ci = wilson_interval(int(oos_mask.sum()), len(oos_mask))
    per_asset_oos = primary.per_asset_oos

    gate = evaluate_phase_j_gate(primary.oos, primary.oos_bootstrap, per_asset_oos, liq_violations)

    # ── 4. LOAO + walk-forward on the primary configuration ───────────────────
    loao = run_loao(df, primary_model, list(OB_FEATURE_NAMES))
    walk_forward = run_walk_forward(df, primary_model, list(OB_FEATURE_NAMES))

    # ── 5. Calibration (OOS predictions of the primary configuration) ────────
    calib_all = calibration_buckets(oos_df.assign(pred=applied["oos"]))
    calib_full = calibration_buckets(df.assign(pred=_predict_full(df, primary_model, OB_FEATURE_NAMES)))

    # ── 6. Leverage & account simulation (separate from R-space quality) ──────
    lev_all = leverage_analysis(df)
    lev_ai = leverage_analysis(oos_df[oos_mask]) if oos_mask.any() else None

    # ── 7. Ablation study (each with own val threshold; one OOS eval each) ────
    ablations: Dict[str, Any] = {}
    for set_name, cols in ABLATION_SETS.items():
        res = evaluate_configuration(df, primary_model, list(cols))
        ablations[set_name] = {
            "features": list(cols),
            "feature_count": len(cols),
            "threshold": res.threshold_selection["chosen_threshold"],
            "threshold_source": res.threshold_selection["selection_source"],
            "validation_incremental_expectancy_r": res.validation["incremental_expectancy_r"],
            "validation_coverage_pct": res.validation["coverage_pct"],
            "oos": res.oos,
            "per_asset_oos": res.per_asset_oos,
        }
        if verbose:
            print(
                f"  ablation {set_name}: val_inc={res.validation['incremental_expectancy_r']:+.4f}R "
                f"cov={res.validation['coverage_pct']:.1f}% | oos_inc={res.oos['incremental_expectancy_r']:+.4f}R "
                f"oos_n={res.oos['n_selected']}"
            )

    results = {
        "phase": "J",
        "experiment_name": "OB-Centric AI Research on Real SMC/OB Trades",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_question": (
            "Can an AI filter improve the actual SMC/OB trades generated by the application, "
            "using the application's real entry, second-edge SL, TP and risk/leverage rules, "
            "while maintaining sufficient coverage and statistically significant improvement?"
        ),
        "reproducibility": {
            "dataset_fingerprint_source": "canonical delta_exchange_india manifest",
            "feature_contract": "phase-j-ob-causal-v1",
            "feature_count": FEATURE_DIM,
            "feature_names": list(OB_FEATURE_NAMES),
            "labels": ["label_realized_r (primary)", "label_tp_first", "label_mfe_r", "label_mae_r"],
            "random_seed": RANDOM_SEED,
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "threshold_grid": [float(g) for g in THRESHOLD_GRID],
            "threshold_rule": (
                "argmax validation incremental expectancy subject to coverage>=15%, "
                "incremental>0 and accepted>rejected; relax floor to 10%; else 0.50R frozen default"
            ),
            "tp_config": "PHASE_I_OB_60TP_35SL (reward_multiple=60/35); production untouched",
            "sl_config": "second edge of the Order Block",
            "leverage_formula": "min(100, max(1, floor(35 / stop_distance_pct)))",
            "costs": {"taker_per_side": 0.0005, "slippage_per_side": 0.0001, "funding_per_hour": 0.0000125},
            "maintenance_margin_rate_assumption": MAINTENANCE_MARGIN_RATE,
        },
        "splits": {
            "train_end_utc": TRAIN_END_UTC,
            "val_window_utc": [VAL_START_UTC, VAL_END_UTC],
            "oos_window_utc_frozen": [OOS_START_UTC, OOS_END_UTC],
            "embargo_hours": EMBARGO_HOURS,
            "isolation_report": isolation,
        },
        "dataset_summary": {
            "total_unique_ob_trades": int(len(df_raw)),
            "embargo_rows_dropped_in_split_assignment": embargo_dropped,
            "per_asset": {a: int(n) for a, n in df_raw.groupby("asset").size().items()},
            "per_split": {k: int(v) for k, v in df.groupby("split").size().items()},
            "smc_baseline_by_split": {
                s: group_metrics(df[df["split"] == s], np.ones((df["split"] == s).sum(), dtype=bool))["smc"]
                for s in ("train", "val", "oos")
            },
        },
        "model_comparison": comparisons,
        "primary_model": primary_model,
        "primary_selection_basis": "max validation incremental expectancy among rule-eligible thresholds (OOS never used)",
        "primary": {
            "threshold": thr_primary,
            "validation": primary.validation,
            "oos": primary.oos,
            "oos_bootstrap": primary.oos_bootstrap,
            "oos_coverage_wilson_95ci_pct": list(coverage_ci),
            "per_asset_oos": per_asset_oos,
        },
        "promotion_gate": gate,
        "loao_cross_asset": loao,
        "walk_forward": walk_forward,
        "calibration": {"oos": calib_all, "full_history": calib_full},
        "leverage_analysis": {
            "all_trades_full_history": lev_all,
            "ai_accepted_oos": lev_ai,
        },
        "liquidation_violations_total": liq_violations,
        "ablations": ablations,
        "runtime_seconds": round(time.time() - t0, 2),
        "governance": {
            "phase_status": gate["status"],
            "live_execution_authorized": False,
            "deterministic_smc_is_production_authority": True,
            "production_onnx_untouched": True,
        },
    }
    if verbose:
        print(f"\n  Gate status: {gate['status']}")
        print(f"  Runtime: {results['runtime_seconds']}s")
    return results


def _predict_full(df: pd.DataFrame, model_name: str, feature_cols) -> np.ndarray:
    from quantedge.ai.evaluation.phase_j_research import fit_predict, model_candidates

    train_df = df[df["split"] == "train"]
    preds = fit_predict(model_name, model_candidates()[model_name], train_df, {"all": df}, list(feature_cols))
    return preds["all"]


def _count_liquidation_violations(df: pd.DataFrame) -> int:
    """Recomputes Phase I liquidation safety per trade row (capped formula)."""
    from quantedge.ai.evaluation.phase_i_ob_replay import estimate_liquidation

    violations = 0
    for _, row in df.iterrows():
        stop_frac = float(row["stop_distance_percent"]) / 100.0
        liq = estimate_liquidation(float(row["entry_price"]), stop_frac, int(row["leverage"]), str(row["direction"]))
        if liq["liquidation_before_sl"]:
            violations += 1
    return violations


def write_results(results: Dict[str, Any], repo_root: Path | None = None) -> None:
    from quantedge.ai.evaluation.phase_j_reports import render_phase_j_report

    root = repo_root or _get_repo_root()
    docs_dir = root / "docs" / "ai"
    docs_dir.mkdir(parents=True, exist_ok=True)
    payload = _json_safe(results)
    (docs_dir / "phase_j_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (docs_dir / "PHASE_J_OB_AI_RESEARCH_REPORT.md").write_text(render_phase_j_report(payload), encoding="utf-8")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def main() -> None:
    results = run_phase_j(verbose=True)
    write_results(results)
    print("\nReports written to docs/ai/")


if __name__ == "__main__":
    main()
