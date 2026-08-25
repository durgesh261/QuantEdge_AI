"""
QuantEdge AI — Phase J Research Engine.

Implements the full scientific pipeline over the REAL OB trade universe:

    TRAIN ──► VALIDATION ──► FROZEN OOS
      │            │              │
      │            └─ threshold    └─ ONE evaluation per frozen configuration
      │               selection ONLY
      └─ model fitting

Frozen split boundaries (identical to Phases E–H provenance):
    train :        start .. 2026-06-03T18:00Z   (post warm-up)
    val   : 2026-06-06T20:00Z .. 2026-07-02T22:00Z   (72h embargo each side)
    OOS   : 2026-07-06T00:00Z .. 2026-08-21T14:00Z   (FROZEN — touched once)

Threshold selection (PRE-DECLARED RULE — fixed before any OOS evaluation):
    grid = {0.00, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60}
    eligible(t): validation coverage >= 15%
                 AND validation incremental expectancy > 0
                 AND accepted expectancy > rejected expectancy
    t*       = argmax_eligible(validation incremental expectancy)
    fallback : relax coverage floor to 10%; if still none -> 0.50R (frozen prod)

No OOS value may influence any selection. All randomness is seeded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.evaluation.phase_i_ob_replay import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    ExtendedMetrics,
    moving_block_bootstrap_groups,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (
    ABLATION_SETS,
    LABEL_HOLDING_BARS,
    LABEL_MAE_R,
    LABEL_MFE_R,
    LABEL_REALIZED_R,
    LABEL_TP_FIRST,
    OB_FEATURE_NAMES,
)

# ═════════════════════════════════════════════════════════════════════════════
# Frozen configuration
# ═════════════════════════════════════════════════════════════════════════════

TRAIN_END_UTC = "2026-06-03T18:00:00+00:00"
VAL_START_UTC = "2026-06-06T20:00:00+00:00"
VAL_END_UTC = "2026-07-02T22:00:00+00:00"
OOS_START_UTC = "2026-07-06T00:00:00+00:00"
OOS_END_UTC = "2026-08-21T14:00:00+00:00"
EMBARGO_HOURS = 72.0

THRESHOLD_GRID: Tuple[float, ...] = (0.00, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60)
COVERAGE_FLOOR_PCT = 15.0
COVERAGE_FLOOR_RELAXED_PCT = 10.0
DEFAULT_THRESHOLD = 0.50

RANDOM_SEED = 42


# ═════════════════════════════════════════════════════════════════════════════
# Splits
# ═════════════════════════════════════════════════════════════════════════════


def assign_frozen_splits(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assigns train/val/OOS by decision_time using the frozen boundaries.
    Rows inside either embargo gap are dropped entirely (assigned to no split).
    """
    df = df.copy()
    dt = pd.to_datetime(df["decision_time"], utc=True)
    t_train_end = pd.Timestamp(TRAIN_END_UTC)
    t_val_start = pd.Timestamp(VAL_START_UTC)
    t_val_end = pd.Timestamp(VAL_END_UTC)
    t_oos_start = pd.Timestamp(OOS_START_UTC)
    t_oos_end = pd.Timestamp(OOS_END_UTC)

    df["split"] = "train"
    df.loc[(dt >= t_val_start) & (dt <= t_val_end), "split"] = "val"
    df.loc[(dt >= t_oos_start) & (dt <= t_oos_end), "split"] = "oos"

    # Embargo zones: (train_end, val_start) and (val_end, oos_start); also anything
    # beyond oos_end belongs to no usable split for this experiment.
    keep = (
        (df["split"] == "train") & (dt <= t_train_end)
    ) | (df["split"] == "val") | (df["split"] == "oos")
    df = df[keep].reset_index(drop=True)

    # Guard: train must never exceed the train boundary.
    assert pd.to_datetime(df[df["split"] == "train"]["decision_time"], utc=True).max() <= t_train_end
    return df


def verify_split_isolation(df: pd.DataFrame) -> Dict[str, Any]:
    """Asserts chronological separation + embargo between consecutive splits."""
    report: Dict[str, Any] = {}
    bounds = {}
    for split in ("train", "val", "oos"):
        sub = df[df["split"] == split]
        assert len(sub) > 0, f"empty split {split}"
        tmin, tmax = str(sub["decision_time"].min()), str(sub["decision_time"].max())
        bounds[split] = (tmin, tmax)
        report[f"{split}_window"] = {"start": tmin, "end": tmax, "n": int(len(sub))}

    def _t(s: str) -> pd.Timestamp:
        return pd.Timestamp(s)

    gap_train_val = (_t(bounds["val"][0]) - _t(bounds["train"][1])).total_seconds() / 3600.0
    gap_val_oos = (_t(bounds["oos"][0]) - _t(bounds["val"][1])).total_seconds() / 3600.0
    report["embargo_gap_train_to_val_hours"] = gap_train_val
    report["embargo_gap_val_to_oos_hours"] = gap_val_oos
    assert gap_train_val >= EMBARGO_HOURS, "train/validation embargo violated"
    assert gap_val_oos >= EMBARGO_HOURS, "validation/OOS embargo violated"
    assert bounds["oos"][0] == OOS_START_UTC or bounds["oos"][0] >= OOS_START_UTC
    return report


# ═════════════════════════════════════════════════════════════════════════════
# Metrics helpers (reuse smc_baseline on constructed frames)
# ═════════════════════════════════════════════════════════════════════════════


def _metrics_frame(sub: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "exit_reason": sub["exit_reason"].astype(str).values,
            LABEL_REALIZED_R: sub[LABEL_REALIZED_R].astype(float).values,
            LABEL_MFE_R: sub[LABEL_MFE_R].astype(float).values,
            LABEL_MAE_R: sub[LABEL_MAE_R].astype(float).values,
            "holding_bars": sub[LABEL_HOLDING_BARS].astype(float).values,
        }
    )


def group_metrics(
    universe: pd.DataFrame,
    mask: np.ndarray,
    r_col: str = LABEL_REALIZED_R,
) -> Dict[str, Any]:
    """SMC baseline (universe) vs filtered subset metrics + increments."""
    from quantedge.ai.evaluation.smc_baseline import calculate_performance_metrics

    n_total = len(universe)
    sel = universe[np.asarray(mask, dtype=bool)]
    m_all = calculate_performance_metrics(
        _metrics_frame(universe), r_col=r_col, mfe_col=LABEL_MFE_R, mae_col=LABEL_MAE_R,
        total_eligible_setups=n_total,
    )
    m_sel = (
        calculate_performance_metrics(
            _metrics_frame(sel), r_col=r_col, mfe_col=LABEL_MFE_R, mae_col=LABEL_MAE_R,
            total_eligible_setups=n_total,
        )
        if len(sel)
        else calculate_performance_metrics(
            _metrics_frame(universe.iloc[0:0]), r_col=r_col, mfe_col=LABEL_MFE_R,
            mae_col=LABEL_MAE_R, total_eligible_setups=n_total,
        )
    )
    rej_mask = ~np.asarray(mask, dtype=bool)
    rej = universe[rej_mask]
    m_rej = (
        calculate_performance_metrics(
            _metrics_frame(rej), r_col=r_col, mfe_col=LABEL_MFE_R, mae_col=LABEL_MAE_R,
            total_eligible_setups=n_total,
        )
        if len(rej)
        else None
    )

    out: Dict[str, Any] = {
        "smc": m_all.to_dict(),
        "filtered": m_sel.to_dict(),
        "rejected": m_rej.to_dict() if m_rej is not None else None,
        "coverage_pct": round(len(sel) / n_total * 100.0, 2) if n_total else 0.0,
        "incremental_expectancy_r": round(m_sel.expectancy_r - m_all.expectancy_r, 4),
        "incremental_total_r": round(m_sel.total_r - m_all.total_r, 2),
        "n_universe": n_total,
        "n_selected": int(len(sel)),
    }
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Pre-declared threshold selection (VALIDATION ONLY)
# ═════════════════════════════════════════════════════════════════════════════


def select_threshold_on_validation(
    val_df: pd.DataFrame,
    pred_col: str = "pred",
    grid: Sequence[float] = THRESHOLD_GRID,
    coverage_floor: float = COVERAGE_FLOOR_PCT,
) -> Dict[str, Any]:
    """
    Implements the pre-declared selection rule. NEVER sees OOS data.
    """
    candidates: List[Dict[str, Any]] = []
    for t in grid:
        mask = val_df[pred_col].to_numpy(dtype=float) >= t
        gm = group_metrics(val_df, mask)
        accepted_exp = gm["filtered"]["expectancy_r"]
        rejected_exp = gm["rejected"]["expectancy_r"] if gm["rejected"] else None
        rec = {
            "threshold": float(t),
            "coverage_pct": gm["coverage_pct"],
            "incremental_expectancy_r": gm["incremental_expectancy_r"],
            "accepted_expectancy_r": accepted_exp,
            "rejected_expectancy_r": rejected_exp,
            "pf": gm["filtered"]["profit_factor"],
            "n_selected": gm["n_selected"],
            "eligible": bool(
                gm["coverage_pct"] >= coverage_floor
                and gm["incremental_expectancy_r"] > 0
                and rejected_exp is not None
                and accepted_exp > rejected_exp
            ),
        }
        candidates.append(rec)

    eligible = [c for c in candidates if c["eligible"]]
    relaxed = False
    if not eligible:
        relaxed = True
        for c in candidates:
            mask = val_df[pred_col].to_numpy(dtype=float) >= c["threshold"]
            gm = group_metrics(val_df, mask)
            c["eligible"] = bool(
                gm["coverage_pct"] >= COVERAGE_FLOOR_RELAXED_PCT
                and gm["incremental_expectancy_r"] > 0
                and gm["rejected"] is not None
                and gm["filtered"]["expectancy_r"] > gm["rejected"]["expectancy_r"]
            )
        eligible = [c for c in candidates if c["eligible"]]

    if eligible:
        best = max(eligible, key=lambda c: (c["incremental_expectancy_r"], -c["coverage_pct"]))
        source = "validation_rule" + ("_relaxed_coverage" if relaxed else "")
        chosen = float(best["threshold"])
    else:
        best = next(c for c in candidates if c["threshold"] == DEFAULT_THRESHOLD)
        source = "fallback_frozen_default"
        chosen = DEFAULT_THRESHOLD

    return {
        "chosen_threshold": chosen,
        "selection_source": source,
        "grid_evaluated": [float(g) for g in grid],
        "candidates": candidates,
        "selected_summary": best,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Model candidates (all seeded, deterministic)
# ═════════════════════════════════════════════════════════════════════════════


def model_candidates() -> "Dict[str, Tuple[Any, str]]":
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "ridge": (Ridge(alpha=1.0, random_state=RANDOM_SEED), "regression"),
        "random_forest": (
            RandomForestRegressor(
                n_estimators=200, max_depth=4, min_samples_leaf=5,
                max_features=0.5, random_state=RANDOM_SEED, n_jobs=1,
            ),
            "regression",
        ),
        "extra_trees": (
            ExtraTreesRegressor(
                n_estimators=200, max_depth=4, min_samples_leaf=5,
                max_features=0.5, random_state=RANDOM_SEED, n_jobs=1,
            ),
            "regression",
        ),
        "hist_gbdt": (
            HistGradientBoostingRegressor(
                max_iter=150, max_depth=3, min_samples_leaf=10,
                learning_rate=0.05, l2_regularization=1.0, random_state=RANDOM_SEED,
            ),
            "regression",
        ),
        "tp_first_classifier": (
            Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("clf", LogisticRegression(C=0.5, max_iter=5000, random_state=RANDOM_SEED)),
                ]
            ),
            "classification_tp_first",
        ),
    }


def fit_predict(
    name: str,
    spec: Tuple[Any, str],
    train_df: pd.DataFrame,
    apply_dfs: Dict[str, pd.DataFrame],
    feature_cols: Sequence[str],
) -> Dict[str, np.ndarray]:
    model, kind = spec
    X_train = train_df[list(feature_cols)].to_numpy(dtype=float)
    y_train = (
        train_df[LABEL_TP_FIRST].to_numpy(dtype=float)
        if kind == "classification_tp_first"
        else train_df[LABEL_REALIZED_R].to_numpy(dtype=float)
    )
    model.fit(X_train, y_train)
    preds: Dict[str, np.ndarray] = {}
    for key, frame in apply_dfs.items():
        X = frame[list(feature_cols)].to_numpy(dtype=float)
        preds[key] = np.asarray(model.predict(X), dtype=float)
    return preds


# ═════════════════════════════════════════════════════════════════════════════
# Full evaluation of one configuration (model × feature-set)
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class ConfigResult:
    config_name: str
    model_name: str
    feature_set: str
    threshold_selection: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    oos: Dict[str, Any] = field(default_factory=dict)
    oos_bootstrap: Dict[str, Any] = field(default_factory=dict)
    per_asset_oos: List[Dict[str, Any]] = field(default_factory=list)


def evaluate_configuration(
    df: pd.DataFrame,
    model_name: str,
    feature_cols: Sequence[str],
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> ConfigResult:
    """
    TRAIN → fit; VALIDATION → threshold; OOS → single evaluation. Nothing more.
    """
    specs = model_candidates()
    train = df[df["split"] == "train"]
    val = df[df["split"] == "val"]
    oos = df[df["split"] == "oos"]

    # Single deterministic fit; predictions applied to val AND oos.
    applied = fit_predict(model_name, specs[model_name], train, {"val": val, "oos": oos}, feature_cols)
    preds_val = applied["val"]
    thr_sel = select_threshold_on_validation(val.assign(pred=preds_val))
    thr = thr_sel["chosen_threshold"]

    val_gm = group_metrics(val, preds_val >= thr)

    pred_oos = applied["oos"]
    oos_mask = pred_oos >= thr
    oos_gm = group_metrics(oos, oos_mask)

    r_all = oos[LABEL_REALIZED_R].to_numpy(dtype=float)
    boot = (
        moving_block_bootstrap_groups(r_all, oos_mask, n_boot=n_boot, seed=seed)
        if len(r_all) > 0
        else {}
    )

    per_asset = []
    pos = {idx: p for idx, p in zip(oos.index, pred_oos)}
    for asset in sorted(oos["asset"].unique()):
        a_df = oos[oos["asset"] == asset]
        a_mask = np.array([pos[i] >= thr for i in a_df.index], dtype=bool)
        agm = group_metrics(a_df, a_mask)
        per_asset.append(
            {
                "asset": asset,
                "smc_setups": agm["n_universe"],
                "ai_accepted": agm["n_selected"],
                "ai_rejected": agm["n_universe"] - agm["n_selected"],
                "ai_coverage_pct": agm["coverage_pct"],
                "smc_expectancy_r": agm["smc"]["expectancy_r"],
                "ai_expectancy_r": agm["filtered"]["expectancy_r"] if agm["n_selected"] else None,
                "incremental_expectancy_r": agm["incremental_expectancy_r"] if agm["n_selected"] else None,
                "smc_profit_factor": agm["smc"]["profit_factor"],
                "ai_profit_factor": agm["filtered"]["profit_factor"] if agm["n_selected"] else None,
                "smc_win_rate_pct": agm["smc"]["win_rate_pct"],
                "ai_win_rate_pct": agm["filtered"]["win_rate_pct"] if agm["n_selected"] else None,
                "smc_max_drawdown_r": agm["smc"]["max_drawdown_r"],
                "ai_max_drawdown_r": agm["filtered"]["max_drawdown_r"] if agm["n_selected"] else None,
                "rejected_expectancy_r": agm["rejected"]["expectancy_r"] if agm["rejected"] else None,
            }
        )

    return ConfigResult(
        config_name=f"{model_name}|{'custom'}",
        model_name=model_name,
        feature_set="custom",
        threshold_selection=thr_sel,
        validation=val_gm,
        oos=oos_gm,
        oos_bootstrap=boot,
        per_asset_oos=per_asset,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Leave-One-Asset-Out cross-asset validation
# ═════════════════════════════════════════════════════════════════════════════


def run_loao(
    df: pd.DataFrame,
    model_name: str = "random_forest",
    feature_cols: Sequence[str] = tuple(OB_FEATURE_NAMES),
) -> List[Dict[str, Any]]:
    """
    Per fold: train on 3 assets (train+their val), select threshold on those
    assets' VALIDATION slices only, evaluate the held-out asset across its FULL
    post-warmup timeline AND on its OOS-only slice.
    """
    specs = model_candidates()
    results = []
    assets = sorted(df["asset"].unique())
    for held in assets:
        tr_assets = [a for a in assets if a != held]
        fold_train_pool = df[(df["asset"].isin(tr_assets)) & (df["split"].isin(["train", "val"]))]
        # temporal inner split for threshold: use val-period rows of training assets
        fold_inner_val = df[(df["asset"].isin(tr_assets)) & (df["split"] == "val")]
        fold_inner_train = df[(df["asset"].isin(tr_assets)) & (df["split"] == "train")]
        held_all = df[df["asset"] == held]

        preds_inner_val = fit_predict(
            model_name, specs[model_name], fold_inner_train,
            {"v": fold_inner_val, "h": held_all}, feature_cols,
        )
        thr_sel = select_threshold_on_validation(fold_inner_val.assign(pred=preds_inner_val["v"]))
        thr = thr_sel["chosen_threshold"]

        preds_held = preds_inner_val["h"]
        held_mask = preds_held >= thr
        gm_full = group_metrics(held_all, held_mask)

        held_oos = held_all[held_all["split"] == "oos"]
        pos = {idx: p for idx, p in zip(held_all.index, preds_held)}
        oos_mask = np.array([pos[i] >= thr for i in held_oos.index], dtype=bool)
        gm_oos = group_metrics(held_oos, oos_mask) if len(held_oos) else None

        results.append(
            {
                "held_out_asset": held,
                "training_assets": tr_assets,
                "fold_threshold": thr,
                "threshold_source": thr_sel["selection_source"],
                "held_full_period": {
                    "n": gm_full["n_universe"],
                    "coverage_pct": gm_full["coverage_pct"],
                    "smc_expectancy_r": gm_full["smc"]["expectancy_r"],
                    "ai_expectancy_r": gm_full["filtered"]["expectancy_r"] if gm_full["n_selected"] else None,
                    "incremental_expectancy_r": gm_full["incremental_expectancy_r"] if gm_full["n_selected"] else None,
                    "smc_pf": gm_full["smc"]["profit_factor"],
                    "ai_pf": gm_full["filtered"]["profit_factor"] if gm_full["n_selected"] else None,
                    "ai_mdd_r": gm_full["filtered"]["max_drawdown_r"] if gm_full["n_selected"] else None,
                },
                "held_oos_only": {
                    "n": gm_oos["n_universe"] if gm_oos else 0,
                    "coverage_pct": gm_oos["coverage_pct"] if gm_oos else 0.0,
                    "smc_expectancy_r": gm_oos["smc"]["expectancy_r"] if gm_oos else None,
                    "ai_expectancy_r": gm_oos["filtered"]["expectancy_r"] if gm_oos and gm_oos["n_selected"] else None,
                    "incremental_expectancy_r": gm_oos["incremental_expectancy_r"] if gm_oos and gm_oos["n_selected"] else None,
                },
            }
        )
    return results


# ═════════════════════════════════════════════════════════════════════════════
# Walk-forward validation (test months strictly BEFORE the frozen OOS window)
# ═════════════════════════════════════════════════════════════════════════════

WALK_FORWARD_FOLDS: Tuple[Tuple[str, str, str], ...] = (
    # (train_end, val_month_start..end, test month) — expanding window
    ("2026-03-31T23:59:59+00:00", ("2026-04-01T00:00:00+00:00", "2026-04-24T23:59:59+00:00"), ("2026-04-27T00:00:00+00:00", "2026-05-31T23:59:59+00:00")),
    ("2026-04-30T23:59:59+00:00", ("2026-05-01T00:00:00+00:00", "2026-05-24T23:59:59+00:00"), ("2026-05-27T00:00:00+00:00", "2026-06-30T23:59:59+00:00")),
)


def run_walk_forward(
    df: pd.DataFrame,
    model_name: str = "random_forest",
    feature_cols: Sequence[str] = tuple(OB_FEATURE_NAMES),
) -> List[Dict[str, Any]]:
    """
    Expanding-window walk-forward. The final historical period (frozen OOS,
    starting 2026-07-06) is deliberately NEVER used as a walk-forward test fold.
    """
    specs = model_candidates()
    wf = df[pd.to_datetime(df["decision_time"], utc=True) < pd.Timestamp(OOS_START_UTC)]
    folds_out = []
    for k, (tr_end, val_rng, test_rng) in enumerate(WALK_FORWARD_FOLDS):
        train_f = wf[pd.to_datetime(wf["decision_time"], utc=True) <= pd.Timestamp(tr_end)]
        val_f = wf[
            (pd.to_datetime(wf["decision_time"], utc=True) >= pd.Timestamp(val_rng[0]))
            & (pd.to_datetime(wf["decision_time"], utc=True) <= pd.Timestamp(val_rng[1]))
        ]
        test_f = wf[
            (pd.to_datetime(wf["decision_time"], utc=True) >= pd.Timestamp(test_rng[0]))
            & (pd.to_datetime(wf["decision_time"], utc=True) <= pd.Timestamp(test_rng[1]))
        ]
        if len(train_f) < 20 or len(test_f) < 5:
            continue
        preds_val = fit_predict(model_name, specs[model_name], train_f, {"v": val_f}, feature_cols)["v"] if len(val_f) >= 5 else np.array([])
        if len(preds_val) >= 5:
            thr_sel = select_threshold_on_validation(val_f.assign(pred=preds_val))
            thr = thr_sel["chosen_threshold"]
        else:
            thr, thr_sel = DEFAULT_THRESHOLD, {"selection_source": "fallback_small_val"}
        preds_test = fit_predict(model_name, specs[model_name], train_f, {"t": test_f}, feature_cols)["t"]
        gm = group_metrics(test_f, preds_test >= thr)
        folds_out.append(
            {
                "fold": k + 1,
                "train_n": len(train_f),
                "val_n": len(val_f),
                "test_window_utc": list(test_rng),
                "test_n": gm["n_universe"],
                "threshold": thr,
                "threshold_source": thr_sel.get("selection_source"),
                "coverage_pct": gm["coverage_pct"],
                "smc_expectancy_r": gm["smc"]["expectancy_r"],
                "ai_expectancy_r": gm["filtered"]["expectancy_r"] if gm["n_selected"] else None,
                "incremental_expectancy_r": gm["incremental_expectancy_r"] if gm["n_selected"] else None,
                "ai_pf": gm["filtered"]["profit_factor"] if gm["n_selected"] else None,
            }
        )
    return folds_out


# ═════════════════════════════════════════════════════════════════════════════
# Calibration
# ═════════════════════════════════════════════════════════════════════════════


def calibration_buckets(df: pd.DataFrame, pred_col: str = "pred") -> Dict[str, Any]:
    edges = [
        ("< 0R", lambda p: p < 0.0),
        ("0-0.25R", lambda p: (p >= 0.0) & (p < 0.25)),
        ("0.25-0.50R", lambda p: (p >= 0.25) & (p < 0.50)),
        ("0.50-1.00R", lambda p: (p >= 0.50) & (p < 1.00)),
        (">= 1.00R", lambda p: p >= 1.00),
    ]
    p = df[pred_col].to_numpy(dtype=float)
    r = df[LABEL_REALIZED_R].to_numpy(dtype=float)
    buckets: Dict[str, Any] = {}
    realized_seq: List[Tuple[str, float, int]] = []
    for label, fn in edges:
        m = np.asarray(fn(p), dtype=bool)
        n = int(m.sum())
        if n:
            rr = r[m]
            gp = float(rr[rr > 0].sum())
            gl = float(abs(rr[rr < 0].sum()))
            pf = round(gp / gl, 3) if gl > 1e-9 else (999.0 if gp > 0 else 0.0)
            buckets[label] = {
                "count": n,
                "predicted_mean_r": round(float(p[m].mean()), 4),
                "realized_mean_r": round(float(rr.mean()), 4),
                "win_rate_pct": round(float((rr > 0).mean()) * 100.0, 2),
                "profit_factor": pf,
                "median_r": round(float(np.median(rr)), 4),
                "mean_abs_calibration_error_r": round(float(np.abs(p[m] - rr).mean()), 4),
            }
            realized_seq.append((label, buckets[label]["realized_mean_r"], n))
        else:
            buckets[label] = {"count": 0}
    populated = [(l, v, n) for l, v, n in realized_seq if n >= 5]
    monotonic = all(populated[i][1] <= populated[i + 1][1] + 1e-9 for i in range(len(populated) - 1)) if len(populated) >= 2 else None
    overall_mae = round(float(np.abs(p - r).mean()), 4) if len(p) else None
    return {
        "buckets": buckets,
        "monotonic": monotonic,
        "overall_mean_abs_error_r": overall_mae,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Leverage / account simulation (separate from R-space model quality)
# ═════════════════════════════════════════════════════════════════════════════


def leverage_analysis(subset: pd.DataFrame, mc_shuffles: int = 5000, seed: int = RANDOM_SEED) -> Dict[str, Any]:
    lev = subset["leverage"].to_numpy(dtype=float)
    r = subset[LABEL_REALIZED_R].to_numpy(dtype=float)
    # Account P&L per trade as fraction of balance: R * 35% risk budget
    acct_ret = r * 0.35
    cum = np.cumsum(acct_ret)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    mdd_acct = float(dd.max()) if len(dd) else 0.0

    rng = np.random.default_rng(seed)
    mc_max_dd = np.empty(mc_shuffles)
    for i in range(mc_shuffles):
        s = acct_ret[rng.permutation(len(acct_ret))]
        c = np.cumsum(s)
        pk = np.maximum.accumulate(c)
        mc_max_dd[i] = (pk - c).max()
    p95 = float(np.percentile(mc_max_dd, 95))

    buckets: Dict[str, Any] = {}
    for lo, hi in [(1, 19), (20, 39), (40, 69), (70, 100)]:
        m = (lev >= lo) & (lev <= hi)
        if m.any():
            buckets[f"{lo}-{hi}x"] = {
                "count": int(m.sum()),
                "mean_realized_r": round(float(r[m].mean()), 4),
                "win_rate_pct": round(float((r[m] > 0).mean()) * 100.0, 2),
                "avg_leverage": round(float(lev[m].mean()), 1),
            }

    return {
        "avg_leverage": round(float(lev.mean()), 2) if len(lev) else 0.0,
        "median_leverage": round(float(np.median(lev)), 2) if len(lev) else 0.0,
        "leverage_min_max": [int(lev.min()), int(lev.max())] if len(lev) else [0, 0],
        "account_return_per_trade_pct_of_balance_avg": round(float(acct_ret.mean()) * 100.0, 4) if len(acct_ret) else 0.0,
        "account_max_drawdown_pct_of_balance_path": round(mdd_acct * 100.0, 2),
        "mc_shuffle_max_dd_p95_pct_of_balance": round(p95 * 100.0, 2),
        "risk_of_ruin_proxy_50pct_loss_prob": round(float((mc_max_dd >= 0.50).mean()), 4),
        "by_leverage_bucket": buckets,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Wilson coverage interval
# ═════════════════════════════════════════════════════════════════════════════


def wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(centre - half, 4), round(centre + half, 4))


# ═════════════════════════════════════════════════════════════════════════════
# Governance gate (Phase J — same criteria family as Phase I)
# ═════════════════════════════════════════════════════════════════════════════


def evaluate_phase_j_gate(
    oos_result: Dict[str, Any],
    bootstrap: Dict[str, Any],
    per_asset: List[Dict[str, Any]],
    liquidation_violations: int,
    min_coverage: float = 15.0,
) -> Dict[str, Any]:
    smc = oos_result["smc"]
    ai = oos_result["filtered"]
    criteria: Dict[str, Any] = {}

    inc = oos_result["incremental_expectancy_r"]
    criteria["C1_oos_incremental_expectancy_positive"] = {
        "passed": inc is not None and inc > 0,
        "detail": f"SMC {smc['expectancy_r']:+.4f}R vs AI {ai['expectancy_r']:+.4f}R (inc {inc if inc is not None else '—'})",
    }
    criteria["C2_oos_profit_factor_improvement"] = {
        "passed": ai["executed_setups"] > 0 and ai["profit_factor"] > smc["profit_factor"],
        "detail": f"SMC PF {smc['profit_factor']:.3f} vs AI PF {ai['profit_factor']:.3f}",
    }
    criteria["C3_oos_drawdown_improvement"] = {
        "passed": ai["executed_setups"] > 0 and ai["max_drawdown_r"] < smc["max_drawdown_r"],
        "detail": f"SMC MDD {smc['max_drawdown_r']:.2f}R vs AI MDD {ai['max_drawdown_r']:.2f}R",
    }
    cov = oos_result["coverage_pct"]
    criteria["C4_minimum_ai_coverage"] = {
        "passed": cov >= min_coverage,
        "detail": f"AI coverage {cov:.2f}% of OOS SMC setups (floor {min_coverage}%)",
    }
    ci_low = bootstrap.get("incremental_mean_r_95ci", (-99.0, 0.0))[0] if bootstrap else -99.0
    criteria["C5_bootstrap_ci_lower_bound_positive"] = {
        "passed": ci_low > 0,
        "detail": f"Incremental expectancy MBB 95% CI lower bound {ci_low:+.4f}R",
    }
    nonneg = sum(1 for a in per_asset if a["incremental_expectancy_r"] is not None and a["incremental_expectancy_r"] >= 0)
    no_acc = [a["asset"] for a in per_asset if not a["ai_accepted"]]
    worst_vals = [a["incremental_expectancy_r"] for a in per_asset if a["incremental_expectancy_r"] is not None]
    worst = min(worst_vals) if worst_vals else -99.0
    criteria["C6_cross_asset_robustness"] = {
        "passed": nonneg >= 3 and worst > -0.50 and not no_acc,
        "detail": f"{nonneg}/{len(per_asset)} assets non-negative incremental; worst {worst:+.4f}R"
                  + (f"; no acceptances on {no_acc}" if no_acc else ""),
    }
    acc_exp = ai["expectancy_r"] if ai["executed_setups"] else 0.0
    rej_exp = oos_result["rejected"]["expectancy_r"] if oos_result.get("rejected") else 0.0
    criteria["C7_rejected_trades_materially_worse"] = {
        "passed": rej_exp < acc_exp - 0.10,
        "detail": f"Accepted {acc_exp:+.4f}R vs rejected {rej_exp:+.4f}R (gap needed >= 0.10R)",
    }
    criteria["C8_no_unacceptable_liquidation_risk"] = {
        "passed": liquidation_violations == 0,
        "detail": f"{liquidation_violations} trades with estimated liquidation before SL",
    }

    all_pass = all(c["passed"] for c in criteria.values())
    return {
        "criteria": criteria,
        "all_pass": all_pass,
        "status": "CANDIDATE_FOR_GOVERNANCE_REVIEW" if all_pass else "REJECTED",
        "live_execution_authorized": False,
        "execution_authority": "DETERMINISTIC_SMC",
        "ai_live_execution": "BLOCKED_BY_SYSTEM",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Paired comparison bootstrap for two configurations (model A vs B)
# ═════════════════════════════════════════════════════════════════════════════


def paired_config_bootstrap(
    r_all: np.ndarray,
    mask_a: np.ndarray,
    mask_b: np.ndarray,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """MBB difference of filtered means between two masks over the same universe."""
    n = len(r_all)
    bs = max(3, int(math.ceil(n ** (1.0 / 3.0))))
    num_blocks = int(math.ceil(n / bs))
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max(1, n - bs + 1), size=num_blocks)
        idx = np.mod(np.concatenate([np.arange(s, s + bs) for s in starts])[:n], n)
        rs = r_all[idx]
        ma, mb = mask_a[idx], mask_b[idx]
        mean_a = float(rs[ma].mean()) if ma.any() else 0.0
        mean_b = float(rs[mb].mean()) if mb.any() else 0.0
        diffs[b] = mean_b - mean_a
    return {
        "diff_b_minus_a_95ci": (
            round(float(np.percentile(diffs, 2.5)), 4),
            round(float(np.percentile(diffs, 97.5)), 4),
        ),
        "mbb_block_size": bs,
        "n_bootstraps": n_boot,
        "seed": seed,
    }
