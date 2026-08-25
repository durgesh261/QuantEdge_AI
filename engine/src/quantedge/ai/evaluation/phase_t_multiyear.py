"""
QuantEdge AI — Phase T: Multi-Year Expanding Walk-Forward AI Evaluation Engine (2024–2026).

Implements the authoritative 20-month expanding-window walk-forward evaluation protocol
across 1,239 out-of-sample setups seeded with the mature June–December 2024 population.

Invariants:
- Models: Frozen Ridge(alpha=1.0, random_state=42).
- Features: Exact 29 scale-invariant causal features.
- Primary Threshold: Frozen +0.20R.
- Strict Mature-Label Isolation: label_available_timestamp <= training_cutoff.
- Moving Block Bootstrap (10,000 resamples, b=36).
- Coverage-Matched Random Benchmark (10,000 resamples).
- Annual, Monthly, and Cross-Asset Consistency Audits.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import Ridge

from quantedge.ai.evaluation.phase_j_ob_dataset import FEATURE_DIM, OB_FEATURE_NAMES
from quantedge.ai.evaluation.phase_l_research import (
    FROZEN_ALPHA,
    FROZEN_MODEL_NAME,
    FROZEN_THRESHOLD,
    RANDOM_SEED,
    SYMBOLS,
    compute_phase_l_metrics,
    wilson_score_interval,
)
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root

# ═════════════════════════════════════════════════════════════════════════════
# 20-Month Expanding Walk-Forward Windows Definition (Jan 2025 – Aug 2026)
# ═════════════════════════════════════════════════════════════════════════════

MULTI_YEAR_WALK_FORWARD_WINDOWS: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    # Window ID, Train Start, Train End, Test Start, Test End, Test Month
    ("WF_2025_01", "2024-06-01T00:00:00+00:00", "2024-12-31T23:59:59+00:00", "2025-01-01T00:00:00+00:00", "2025-01-31T23:59:59+00:00", "2025-01"),
    ("WF_2025_02", "2024-06-01T00:00:00+00:00", "2025-01-31T23:59:59+00:00", "2025-02-01T00:00:00+00:00", "2025-02-28T23:59:59+00:00", "2025-02"),
    ("WF_2025_03", "2024-06-01T00:00:00+00:00", "2025-02-28T23:59:59+00:00", "2025-03-01T00:00:00+00:00", "2025-03-31T23:59:59+00:00", "2025-03"),
    ("WF_2025_04", "2024-06-01T00:00:00+00:00", "2025-03-31T23:59:59+00:00", "2025-04-01T00:00:00+00:00", "2025-04-30T23:59:59+00:00", "2025-04"),
    ("WF_2025_05", "2024-06-01T00:00:00+00:00", "2025-04-30T23:59:59+00:00", "2025-05-01T00:00:00+00:00", "2025-05-31T23:59:59+00:00", "2025-05"),
    ("WF_2025_06", "2024-06-01T00:00:00+00:00", "2025-05-31T23:59:59+00:00", "2025-06-01T00:00:00+00:00", "2025-06-30T23:59:59+00:00", "2025-06"),
    ("WF_2025_07", "2024-06-01T00:00:00+00:00", "2025-06-30T23:59:59+00:00", "2025-07-01T00:00:00+00:00", "2025-07-31T23:59:59+00:00", "2025-07"),
    ("WF_2025_08", "2024-06-01T00:00:00+00:00", "2025-07-31T23:59:59+00:00", "2025-08-01T00:00:00+00:00", "2025-08-31T23:59:59+00:00", "2025-08"),
    ("WF_2025_09", "2024-06-01T00:00:00+00:00", "2025-08-31T23:59:59+00:00", "2025-09-01T00:00:00+00:00", "2025-09-30T23:59:59+00:00", "2025-09"),
    ("WF_2025_10", "2024-06-01T00:00:00+00:00", "2025-09-30T23:59:59+00:00", "2025-10-01T00:00:00+00:00", "2025-10-31T23:59:59+00:00", "2025-10"),
    ("WF_2025_11", "2024-06-01T00:00:00+00:00", "2025-10-31T23:59:59+00:00", "2025-11-01T00:00:00+00:00", "2025-11-30T23:59:59+00:00", "2025-11"),
    ("WF_2025_12", "2024-06-01T00:00:00+00:00", "2025-11-30T23:59:59+00:00", "2025-12-01T00:00:00+00:00", "2025-12-31T23:59:59+00:00", "2025-12"),
    ("WF_2026_01", "2024-06-01T00:00:00+00:00", "2025-12-31T23:59:59+00:00", "2026-01-01T00:00:00+00:00", "2026-01-31T23:59:59+00:00", "2026-01"),
    ("WF_2026_02", "2024-06-01T00:00:00+00:00", "2026-01-31T23:59:59+00:00", "2026-02-01T00:00:00+00:00", "2026-02-28T23:59:59+00:00", "2026-02"),
    ("WF_2026_03", "2024-06-01T00:00:00+00:00", "2026-02-28T23:59:59+00:00", "2026-03-01T00:00:00+00:00", "2026-03-31T23:59:59+00:00", "2026-03"),
    ("WF_2026_04", "2024-06-01T00:00:00+00:00", "2026-03-31T23:59:59+00:00", "2026-04-01T00:00:00+00:00", "2026-04-30T23:59:59+00:00", "2026-04"),
    ("WF_2026_05", "2024-06-01T00:00:00+00:00", "2026-04-30T23:59:59+00:00", "2026-05-01T00:00:00+00:00", "2026-05-31T23:59:59+00:00", "2026-05"),
    ("WF_2026_06", "2024-06-01T00:00:00+00:00", "2026-05-31T23:59:59+00:00", "2026-06-01T00:00:00+00:00", "2026-06-30T23:59:59+00:00", "2026-06"),
    ("WF_2026_07", "2024-06-01T00:00:00+00:00", "2026-06-30T23:59:59+00:00", "2026-07-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00", "2026-07"),
    ("WF_2026_08", "2024-06-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00", "2026-08-01T00:00:00+00:00", "2026-08-21T23:59:59+00:00", "2026-08"),
)

DESCRIPTIVE_THRESHOLD_GRID = (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)
BOOTSTRAP_N_PHASE_T = 10000
RANDOM_BENCHMARK_N_PHASE_T = 10000


@dataclass(frozen=True)
class PhaseTPredictionRecord:
    """Individual OOS setup evaluation record in Phase T."""
    ob_id: str
    asset: str
    direction: str
    decision_timestamp: str
    window_id: str
    test_month: str
    realized_r: float
    mfe_r: float
    mae_r: float
    holding_bars: int
    exit_reason: str
    prediction: float
    ai_decision: str  # ACCEPT or REJECT


class PhaseTMultiYearWalkForwardPipeline:
    """
    Executes the 20-Month Expanding Walk-Forward Evaluation across 2024–2026.
    """

    def __init__(self, master_df: pd.DataFrame):
        self.master_df = master_df.copy()
        if len(self.master_df) != 1670:
            raise ValueError(f"Expected 1670 OBs in multi-year master dataset, got {len(self.master_df)}")

        self.feature_cols = [f"feat_{name}" for name in OB_FEATURE_NAMES]
        self._compute_label_maturity()

    def _compute_label_maturity(self) -> None:
        """Computes exact maturity timestamp when trade outcome was knowable."""
        self.master_df["dec_dt"] = pd.to_datetime(self.master_df["decision_timestamp"], utc=True)
        self.master_df["label_mat_dt"] = self.master_df["dec_dt"] + pd.to_timedelta(self.master_df["holding_bars"], unit="h")

    def run_multiyear_evaluation(self) -> Tuple[List[PhaseTPredictionRecord], Dict[str, Any]]:
        """Runs the complete 20-month expanding walk-forward replay and statistical audit."""
        all_test_records: List[PhaseTPredictionRecord] = []
        window_summaries: List[Dict[str, Any]] = []

        for wid, tr_s, tr_e, te_s, te_e, test_month in MULTI_YEAR_WALK_FORWARD_WINDOWS:
            t_tr_s = pd.Timestamp(tr_s)
            t_tr_e = pd.Timestamp(tr_e)
            t_te_s = pd.Timestamp(te_s)
            t_te_e = pd.Timestamp(te_e)

            # Training set: decision <= train_end AND label mature <= train_end
            train_mask = (self.master_df["dec_dt"] >= t_tr_s) & (self.master_df["dec_dt"] <= t_tr_e) & (self.master_df["label_mat_dt"] <= t_tr_e)
            train_df = self.master_df[train_mask]

            # Candidate training OBs (including immature)
            cand_train_mask = (self.master_df["dec_dt"] >= t_tr_s) & (self.master_df["dec_dt"] <= t_tr_e)
            cand_train_count = int(cand_train_mask.sum())
            immature_count = cand_train_count - len(train_df)

            # Test set: decision in [test_start, test_end]
            test_mask = (self.master_df["dec_dt"] >= t_te_s) & (self.master_df["dec_dt"] <= t_te_e)
            test_df = self.master_df[test_mask].copy()

            if len(train_df) == 0 or len(test_df) == 0:
                raise RuntimeError(f"Window {wid} ({test_month}): empty train ({len(train_df)}) or test ({len(test_df)}) dataset!")

            # Fit Ridge(alpha=1.0) on mature training data
            X_train = train_df[self.feature_cols].values
            y_train = train_df["realized_r"].values

            model = Ridge(alpha=FROZEN_ALPHA, random_state=RANDOM_SEED)
            model.fit(X_train, y_train)

            # Predict on test data
            X_test = test_df[self.feature_cols].values
            preds = model.predict(X_test)
            test_df["prediction"] = preds
            test_df["ai_decision"] = np.where(preds >= FROZEN_THRESHOLD, "ACCEPT", "REJECT")

            # Metrics for this window
            smc_metrics = compute_phase_l_metrics(
                test_df.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}),
                len(test_df),
            )
            ai_acc_df = test_df[test_df["ai_decision"] == "ACCEPT"]
            ai_metrics = compute_phase_l_metrics(
                ai_acc_df.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}),
                len(test_df),
            )

            delta_exp = round(ai_metrics["expectancy_r"] - smc_metrics["expectancy_r"], 4) if ai_metrics["n"] > 0 else 0.0

            window_summaries.append({
                "window_id": wid,
                "test_month": test_month,
                "train_range": f"{tr_s} to {tr_e}",
                "test_range": f"{te_s} to {te_e}",
                "candidate_train_rows": cand_train_count,
                "mature_train_rows": len(train_df),
                "immature_excluded": immature_count,
                "test_rows": len(test_df),
                "accepted_count": ai_metrics["n"],
                "acceptance_rate_pct": ai_metrics["coverage_pct"],
                "smc_baseline": smc_metrics,
                "ai_filtered": ai_metrics,
                "incremental_expectancy_r": delta_exp,
            })

            for _, row in test_df.iterrows():
                all_test_records.append(
                    PhaseTPredictionRecord(
                        ob_id=row["ob_id"],
                        asset=row["asset"],
                        direction=row["direction"],
                        decision_timestamp=row["decision_timestamp"],
                        window_id=wid,
                        test_month=test_month,
                        realized_r=float(row["realized_r"]),
                        mfe_r=float(row["mfe_r"]),
                        mae_r=float(row["mae_r"]),
                        holding_bars=int(row["holding_bars"]),
                        exit_reason=str(row["exit_reason"]),
                        prediction=round(float(row["prediction"]), 6),
                        ai_decision=str(row["ai_decision"]),
                    )
                )

        # Aggregate DataFrame of all 1,239 OOS setups
        full_test_df = pd.DataFrame([asdict(r) for r in all_test_records])
        agg_smc = compute_phase_l_metrics(
            full_test_df.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}),
            len(full_test_df),
        )
        full_ai_df = full_test_df[full_test_df["ai_decision"] == "ACCEPT"]
        agg_ai = compute_phase_l_metrics(
            full_ai_df.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}),
            len(full_test_df),
        )
        agg_delta = round(agg_ai["expectancy_r"] - agg_smc["expectancy_r"], 4)

        # 1. Moving Block Bootstrap (b=36)
        bootstrap_res = self._run_moving_block_bootstrap(full_test_df)

        # 2. Coverage-Matched Random Benchmark
        random_bmark = self._run_random_benchmark(full_test_df, target_n=agg_ai["n"])

        # 3. Monthly Consistency
        monthly_aud = self._run_monthly_consistency(window_summaries)

        # 4. Asset Consistency
        asset_aud = self._run_asset_consistency(full_test_df)

        # 5. Annual Breakdown (2025 vs 2026)
        annual_aud = self._run_annual_consistency(full_test_df)

        # 6. Score Diagnostics & Rank Correlation
        score_diag = self._run_score_diagnostics(full_test_df)

        # 7. Descriptive Threshold Sensitivity
        thresh_diag = self._run_threshold_sensitivity(full_test_df)

        # 8. Heuristic Controls
        heuristic_ctrls = self._run_heuristic_controls(full_test_df)

        # 9. Conservative 1% Fixed-Fractional Economic Growth
        economic_res = self._run_economic_analysis(full_test_df)

        # 10. Evidence Classification Decision
        evidence_decision = self._classify_evidence(
            bootstrap_res,
            monthly_aud,
            asset_aud,
            random_bmark,
            score_diag,
            agg_delta,
        )

        results_payload: Dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "framework": "Phase T: Multi-Year Expanding Walk-Forward Evaluation (2024-2026)",
            "seed_period": "2024-06-01 to 2024-12-31 (429 mature training samples)",
            "oos_test_period": "2025-01-01 to 2026-08-21 (20 continuous months)",
            "aggregate_oos_performance": {
                "total_oos_setups": len(full_test_df),
                "ai_accepted_count": agg_ai["n"],
                "ai_rejected_count": len(full_test_df) - agg_ai["n"],
                "smc_baseline": agg_smc,
                "ai_filtered": agg_ai,
                "incremental_expectancy_r": agg_delta,
            },
            "moving_block_bootstrap": bootstrap_res,
            "random_coverage_benchmark": random_bmark,
            "monthly_consistency": monthly_aud,
            "asset_consistency": asset_aud,
            "annual_consistency": annual_aud,
            "score_diagnostics": score_diag,
            "threshold_sensitivity": thresh_diag,
            "heuristic_controls": heuristic_ctrls,
            "economic_analysis": economic_res,
            "evidence_classification": evidence_decision,
            "window_results": window_summaries,
        }

        return all_test_records, results_payload

    # ── Statistical Routines ─────────────────────────────────────────────────

    def _run_moving_block_bootstrap(self, test_df: pd.DataFrame, n_boot: int = BOOTSTRAP_N_PHASE_T) -> Dict[str, Any]:
        """Dependence-aware moving block bootstrap preserving serial autocorrelation."""
        r_all = test_df["realized_r"].values
        is_acc = (test_df["ai_decision"] == "ACCEPT").values
        r_ai = np.where(is_acc, r_all, np.nan)

        n = len(r_all)
        block_size = max(4, int(math.ceil(math.sqrt(n))))  # block size ~36 for N=1239
        n_blocks = int(math.ceil(n / block_size))
        rng = np.random.default_rng(RANDOM_SEED)

        boot_smc_exps = []
        boot_ai_exps = []
        boot_inc_exps = []

        for _ in range(n_boot):
            start_indices = rng.integers(0, max(1, n - block_size + 1), size=n_blocks)
            boot_idx = np.concatenate([np.arange(s, min(s + block_size, n)) for s in start_indices])[:n]

            smc_mean = float(np.mean(r_all[boot_idx]))
            ai_vals = r_ai[boot_idx]
            ai_valid = ai_vals[~np.isnan(ai_vals)]

            if len(ai_valid) == 0:
                continue

            ai_mean = float(np.mean(ai_valid))
            boot_smc_exps.append(smc_mean)
            boot_ai_exps.append(ai_mean)
            boot_inc_exps.append(ai_mean - smc_mean)

        arr_smc = np.array(boot_smc_exps)
        arr_ai = np.array(boot_ai_exps)
        arr_inc = np.array(boot_inc_exps)

        return {
            "n_resamples": n_boot,
            "block_size": block_size,
            "smc_expectancy_95ci": [round(float(np.percentile(arr_smc, 2.5)), 4), round(float(np.percentile(arr_smc, 97.5)), 4)],
            "ai_expectancy_95ci": [round(float(np.percentile(arr_ai, 2.5)), 4), round(float(np.percentile(arr_ai, 97.5)), 4)],
            "incremental_expectancy_95ci": [round(float(np.percentile(arr_inc, 2.5)), 4), round(float(np.percentile(arr_inc, 97.5)), 4)],
            "p_value_incremental_greater_than_zero": round(float(np.mean(arr_inc > 0.0)), 4),
            "p_value_incremental_less_equal_zero": round(float(np.mean(arr_inc <= 0.0)), 4),
        }

    def _run_random_benchmark(self, test_df: pd.DataFrame, target_n: int, n_resamples: int = RANDOM_BENCHMARK_N_PHASE_T) -> Dict[str, Any]:
        """Coverage-matched random trade selection benchmark."""
        r_all = test_df["realized_r"].values
        n_total = len(r_all)
        ridge_exp = float(test_df[test_df["ai_decision"] == "ACCEPT"]["realized_r"].mean())

        rng = np.random.default_rng(RANDOM_SEED)
        random_exps = []

        for _ in range(n_resamples):
            sample_idx = rng.choice(n_total, size=target_n, replace=False)
            random_exps.append(float(np.mean(r_all[sample_idx])))

        arr_rand = np.array(random_exps)
        mean_rand = float(np.mean(arr_rand))
        std_rand = float(np.std(arr_rand))
        lo_95 = float(np.percentile(arr_rand, 2.5))
        hi_95 = float(np.percentile(arr_rand, 97.5))

        pct_rank = float(np.mean(arr_rand <= ridge_exp) * 100.0)
        p_val = float(np.mean(arr_rand >= ridge_exp))

        return {
            "n_resamples": n_resamples,
            "target_trade_count": target_n,
            "ridge_realized_expectancy": round(ridge_exp, 4),
            "random_benchmark_mean_exp": round(mean_rand, 4),
            "random_benchmark_std_exp": round(std_rand, 4),
            "random_benchmark_95ci": [round(lo_95, 4), round(hi_95, 4)],
            "ridge_percentile_rank_in_random_distribution": round(pct_rank, 1),
            "empirical_p_value_ridge_vs_random": round(p_val, 4),
        }

    def _run_monthly_consistency(self, window_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Examines performance consistency across all 20 individual test months."""
        pos_months = sum(1 for w in window_summaries if w["incremental_expectancy_r"] > 0)
        tot_months = len(window_summaries)
        return {
            "total_test_months": tot_months,
            "positive_incremental_months": pos_months,
            "consistency_fraction_pct": round(pos_months / tot_months * 100.0, 1),
            "monthly_summaries": window_summaries,
        }

    def _run_asset_consistency(self, test_df: pd.DataFrame) -> Dict[str, Any]:
        """Examines performance consistency across the 4 canonical trading assets."""
        asset_res = {}
        pos_assets = 0
        for sym in SYMBOLS:
            sub_all = test_df[test_df["asset"] == sym]
            sub_ai = test_df[(test_df["asset"] == sym) & (test_df["ai_decision"] == "ACCEPT")]

            smc_m = compute_phase_l_metrics(sub_all.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), len(sub_all))
            ai_m = compute_phase_l_metrics(sub_ai.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), len(sub_all))
            d_exp = round(ai_m["expectancy_r"] - smc_m["expectancy_r"], 4) if ai_m["n"] > 0 else 0.0

            if d_exp > 0:
                pos_assets += 1

            asset_res[sym] = {
                "total_oos_setups": smc_m["n"],
                "ai_accepted_count": ai_m["n"],
                "coverage_pct": ai_m["coverage_pct"],
                "smc_expectancy_r": smc_m["expectancy_r"],
                "ai_expectancy_r": ai_m["expectancy_r"],
                "incremental_expectancy_r": d_exp,
                "ai_win_rate_pct": ai_m["win_rate_pct"],
                "ai_profit_factor": ai_m["profit_factor"],
                "ai_max_drawdown_r": ai_m["max_drawdown_r"],
            }

        return {
            "total_assets": len(SYMBOLS),
            "positive_incremental_assets": pos_assets,
            "consistency_fraction_pct": round(pos_assets / len(SYMBOLS) * 100.0, 1),
            "asset_breakdown": asset_res,
        }

    def _run_annual_consistency(self, test_df: pd.DataFrame) -> Dict[str, Any]:
        """Examines performance consistency across 2025 (Full Year) vs 2026 (YTD)."""
        test_df["dt"] = pd.to_datetime(test_df["decision_timestamp"], utc=True)
        test_df["year"] = test_df["dt"].dt.year

        annual_res = {}
        for y in [2025, 2026]:
            sub_all = test_df[test_df["year"] == y]
            sub_ai = test_df[(test_df["year"] == y) & (test_df["ai_decision"] == "ACCEPT")]

            smc_m = compute_phase_l_metrics(sub_all.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), len(sub_all))
            ai_m = compute_phase_l_metrics(sub_ai.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), len(sub_all))
            d_exp = round(ai_m["expectancy_r"] - smc_m["expectancy_r"], 4) if ai_m["n"] > 0 else 0.0

            annual_res[str(y)] = {
                "total_oos_setups": smc_m["n"],
                "ai_accepted_count": ai_m["n"],
                "coverage_pct": ai_m["coverage_pct"],
                "smc_expectancy_r": smc_m["expectancy_r"],
                "ai_expectancy_r": ai_m["expectancy_r"],
                "incremental_expectancy_r": d_exp,
                "ai_win_rate_pct": ai_m["win_rate_pct"],
                "ai_profit_factor": ai_m["profit_factor"],
                "ai_max_drawdown_r": ai_m["max_drawdown_r"],
            }

        return annual_res

    def _run_score_diagnostics(self, test_df: pd.DataFrame) -> Dict[str, Any]:
        """Examines score separation between winners and losers and Spearman correlation."""
        preds = test_df["prediction"].values
        realized = test_df["realized_r"].values

        win_scores = preds[realized > 0]
        lose_scores = preds[realized <= 0]

        spearman_corr, spearman_pval = stats.spearmanr(preds, realized)
        mwu_stat, mwu_pval = stats.mannwhitneyu(win_scores, lose_scores, alternative="greater")

        test_df_copy = test_df.copy()
        test_df_copy["quintile"] = pd.qcut(test_df_copy["prediction"], q=5, labels=["Q1_Lowest", "Q2_Low", "Q3_Mid", "Q4_High", "Q5_Highest"])
        quintiles_summary = []
        for q_label, grp in test_df_copy.groupby("quintile", observed=False):
            quintiles_summary.append({
                "quintile": str(q_label),
                "count": len(grp),
                "min_score": round(float(grp["prediction"].min()), 4),
                "max_score": round(float(grp["prediction"].max()), 4),
                "mean_prediction": round(float(grp["prediction"].mean()), 4),
                "mean_realized_r": round(float(grp["realized_r"].mean()), 4),
                "win_rate_pct": round(float((grp["realized_r"] > 0).mean() * 100.0), 1),
                "profit_factor": round(float(grp[grp["realized_r"] > 0]["realized_r"].sum() / max(1e-6, abs(grp[grp["realized_r"] <= 0]["realized_r"].sum()))), 2),
            })

        return {
            "winners_mean_score": round(float(np.mean(win_scores)), 4),
            "losers_mean_score": round(float(np.mean(lose_scores)), 4),
            "score_separation_delta": round(float(np.mean(win_scores) - np.mean(lose_scores)), 4),
            "mann_whitney_u_p_value": round(float(mwu_pval), 4),
            "spearman_rank_correlation": {
                "rho": round(float(spearman_corr), 4),
                "p_value": round(float(spearman_pval), 4),
            },
            "quintile_calibration": quintiles_summary,
        }

    def _run_threshold_sensitivity(self, test_df: pd.DataFrame) -> Dict[str, Any]:
        """Post-hoc descriptive sensitivity across threshold grid."""
        rows = []
        smc_exp = float(test_df["realized_r"].mean())
        n_tot = len(test_df)

        for th in DESCRIPTIVE_THRESHOLD_GRID:
            sub = test_df[test_df["prediction"] >= th]
            m = compute_phase_l_metrics(sub.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), n_tot)
            inc = round(m["expectancy_r"] - smc_exp, 4) if m["n"] > 0 else 0.0

            rows.append({
                "threshold_r": th,
                "is_primary_frozen": bool(th == FROZEN_THRESHOLD),
                "n_accepted": m["n"],
                "coverage_pct": m["coverage_pct"],
                "win_rate_pct": m["win_rate_pct"],
                "ai_expectancy_r": m["expectancy_r"],
                "incremental_expectancy_r": inc,
                "profit_factor": m["profit_factor"],
                "max_drawdown_r": m["max_drawdown_r"],
            })

        return {
            "disclaimer": "POST-HOC DESCRIPTIVE SENSITIVITY — NOT VALID FOR MODEL SELECTION",
            "threshold_grid": rows,
        }

    def _run_heuristic_controls(self, test_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Evaluates non-learning heuristic controls."""
        n_tot = len(test_df)
        controls = []

        m_smc = compute_phase_l_metrics(test_df.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), n_tot)
        controls.append({
            "control_name": "Full SMC Baseline (100% Accept)",
            "n_trades": m_smc["n"],
            "coverage_pct": m_smc["coverage_pct"],
            "expectancy_r": m_smc["expectancy_r"],
            "win_rate_pct": m_smc["win_rate_pct"],
            "profit_factor": m_smc["profit_factor"],
            "total_r": m_smc["total_r"],
            "max_drawdown_r": m_smc["max_drawdown_r"],
        })

        sub_long = test_df[test_df["direction"] == "LONG"]
        m_long = compute_phase_l_metrics(sub_long.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), n_tot)
        controls.append({
            "control_name": "Direction Rule: Longs Only",
            "n_trades": m_long["n"],
            "coverage_pct": m_long["coverage_pct"],
            "expectancy_r": m_long["expectancy_r"],
            "win_rate_pct": m_long["win_rate_pct"],
            "profit_factor": m_long["profit_factor"],
            "total_r": m_long["total_r"],
            "max_drawdown_r": m_long["max_drawdown_r"],
        })

        sub_short = test_df[test_df["direction"] == "SHORT"]
        m_short = compute_phase_l_metrics(sub_short.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), n_tot)
        controls.append({
            "control_name": "Direction Rule: Shorts Only",
            "n_trades": m_short["n"],
            "coverage_pct": m_short["coverage_pct"],
            "expectancy_r": m_short["expectancy_r"],
            "win_rate_pct": m_short["win_rate_pct"],
            "profit_factor": m_short["profit_factor"],
            "total_r": m_short["total_r"],
            "max_drawdown_r": m_short["max_drawdown_r"],
        })

        sub_ridge = test_df[test_df["ai_decision"] == "ACCEPT"]
        m_ridge = compute_phase_l_metrics(sub_ridge.rename(columns={"realized_r": "label_realized_r", "mfe_r": "label_mfe_r", "mae_r": "label_mae_r"}), n_tot)
        controls.append({
            "control_name": "Phase T AI Filter (Ridge @ +0.20R)",
            "n_trades": m_ridge["n"],
            "coverage_pct": m_ridge["coverage_pct"],
            "expectancy_r": m_ridge["expectancy_r"],
            "win_rate_pct": m_ridge["win_rate_pct"],
            "profit_factor": m_ridge["profit_factor"],
            "total_r": m_ridge["total_r"],
            "max_drawdown_r": m_ridge["max_drawdown_r"],
        })

        return controls

    def _run_economic_analysis(self, test_df: pd.DataFrame, initial_balance: float = 10000.0, risk_pct: float = 0.01) -> Dict[str, Any]:
        """Translates R-multiples into conservative 1% fixed-fractional equity growth."""
        df_sort = test_df.sort_values("decision_timestamp").reset_index(drop=True)

        smc_equity = [initial_balance]
        ai_equity = [initial_balance]

        for _, row in df_sort.iterrows():
            r_val = float(row["realized_r"])
            is_acc = row["ai_decision"] == "ACCEPT"

            curr_smc = smc_equity[-1]
            smc_gain = curr_smc * risk_pct * r_val
            smc_equity.append(curr_smc + smc_gain)

            curr_ai = ai_equity[-1]
            if is_acc:
                ai_gain = curr_ai * risk_pct * r_val
                ai_equity.append(curr_ai + ai_gain)
            else:
                ai_equity.append(curr_ai)

        smc_arr = np.array(smc_equity)
        ai_arr = np.array(ai_equity)

        smc_peak = np.maximum.accumulate(smc_arr)
        smc_dd_pct = float(np.max((smc_peak - smc_arr) / smc_peak * 100.0))

        ai_peak = np.maximum.accumulate(ai_arr)
        ai_dd_pct = float(np.max((ai_peak - ai_arr) / ai_peak * 100.0))

        return {
            "initial_balance_usd": initial_balance,
            "risk_per_trade_fraction": risk_pct,
            "smc_terminal_balance_usd": round(float(smc_arr[-1]), 2),
            "smc_net_return_pct": round(float((smc_arr[-1] - initial_balance) / initial_balance * 100.0), 2),
            "smc_max_drawdown_pct": round(smc_dd_pct, 2),
            "ai_terminal_balance_usd": round(float(ai_arr[-1]), 2),
            "ai_net_return_pct": round(float((ai_arr[-1] - initial_balance) / initial_balance * 100.0), 2),
            "ai_max_drawdown_pct": round(ai_dd_pct, 2),
        }

    def _classify_evidence(
        self,
        bootstrap: Dict[str, Any],
        monthly: Dict[str, Any],
        asset: Dict[str, Any],
        random_bmark: Dict[str, Any],
        score_diag: Dict[str, Any],
        inc_exp: float,
    ) -> Dict[str, Any]:
        """Classifies Phase T findings into pre-registered categories."""
        ci_lo = bootstrap["incremental_expectancy_95ci"][0]
        m_frac = monthly["consistency_fraction_pct"]
        a_frac = asset["consistency_fraction_pct"]
        pct_rank = random_bmark["ridge_percentile_rank_in_random_distribution"]

        if ci_lo > 0.0 and m_frac >= 70.0 and a_frac >= 75.0 and pct_rank >= 95.0:
            category = "STRONG EVIDENCE"
            summary = (
                f"Statistically significant edge confirmed across 20-month expanding walk-forward replay "
                f"(95% MBB CI: [{bootstrap['incremental_expectancy_95ci'][0]:+.4f}R, {bootstrap['incremental_expectancy_95ci'][1]:+.4f}R], "
                f"P(delta > 0) = {bootstrap['p_value_incremental_greater_than_zero']*100:.1f}%, "
                f"Monthly consistency: {m_frac:.1f}%, Random Percentile: {pct_rank:.1f}th)."
            )
        elif inc_exp > 0.0 and (m_frac >= 55.0 or a_frac >= 50.0) and pct_rank >= 60.0:
            category = "PROMISING BUT INSUFFICIENT"
            summary = (
                f"Incremental expectancy is positive ({inc_exp:+.4f}R) and outperforms random selection ({pct_rank:.1f}th percentile), "
                f"with positive deltas in {m_frac:.1f}% of test months and {a_frac:.1f}% of assets. "
                f"However, the 95% bootstrap CI [{bootstrap['incremental_expectancy_95ci'][0]:+.4f}R, {bootstrap['incremental_expectancy_95ci'][1]:+.4f}R] "
                f"spans zero (empirical p = {random_bmark['empirical_p_value_ridge_vs_random']:.4f})."
            )
        elif pct_rank < 30.0 or inc_exp < -0.05:
            category = "NEGATIVE EVIDENCE"
            summary = "AI filter underperforms random selection and degrades the SMC baseline."
        else:
            category = "NO RELIABLE EVIDENCE"
            summary = "Observed performance is indistinguishable from random noise."

        return {
            "classification": category,
            "summary": summary,
            "governance_recommendation": (
                "Maintain AI_PROMOTION_STATUS = REJECTED and live_execution_authorized = false. "
                "AI intelligence operates strictly in shadow/research mode."
            ),
        }


def write_phase_t_artifacts(
    records: List[PhaseTPredictionRecord],
    results: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Path]:
    """Serializes Phase T outputs."""
    root = repo_root or _find_repo_root()
    docs_dir = root / "docs" / "ai"
    docs_dir.mkdir(parents=True, exist_ok=True)

    csv_path = docs_dir / "phase_t_multiyear_predictions.csv"
    json_path = docs_dir / "phase_t_multiyear_results.json"
    rep_path = docs_dir / "PHASE_T_MULTI_YEAR_REPORT.md"

    # 1. Predictions CSV
    pred_dicts = [asdict(r) for r in records]
    if pred_dicts:
        fieldnames = list(pred_dicts[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(pred_dicts)

    # 2. Results JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # 3. Comprehensive Markdown Report
    _write_phase_t_report(rep_path, results)

    return {
        "predictions_csv": csv_path,
        "results_json": json_path,
        "multiyear_report": rep_path,
    }


def _write_phase_t_report(report_path: Path, results: Dict[str, Any]) -> None:
    """Generates PHASE_T_MULTI_YEAR_REPORT.md."""
    rep = results["aggregate_oos_performance"]
    smc = rep["smc_baseline"]
    ai = rep["ai_filtered"]
    boot = results["moving_block_bootstrap"]
    m_aud = results["monthly_consistency"]
    a_aud = results["asset_consistency"]
    ann = results["annual_consistency"]
    r_bm = results["random_coverage_benchmark"]
    econ = results["economic_analysis"]
    score = results["score_diagnostics"]
    cls = results["evidence_classification"]

    lines: List[str] = []
    lines.append("# Phase T — Multi-Year Expanding Walk-Forward AI Evaluation Report (2024–2026)\n\n")
    lines.append(f"**Generated (UTC):** `{datetime.now(timezone.utc).isoformat()}`  \n")
    lines.append(f"**Seed Period:** `{results['seed_period']}`  \n")
    lines.append(f"**OOS Evaluation Scope:** `{results['oos_test_period']}` ($N={rep['total_oos_setups']}$ Setups across 20 Months)  \n")
    lines.append(f"**Model Inspected:** `Ridge(alpha=1.0)` @ `+0.20R` on 29 Scale-Invariant Causal Features  \n")
    lines.append(f"**Final Verdict:** **`{cls['classification']}`**  \n\n---\n\n")

    lines.append("## 1. Executive Summary & Audit Conclusion\n\n")
    lines.append(f"### Classification: **`{cls['classification']}`**\n\n")
    lines.append(f"{cls['summary']}\n\n")
    lines.append(
        "> [!IMPORTANT]\n"
        "> **Governance Invariants:**\n"
        "> - `live_execution_authorized = false`\n"
        "> - `AI_PROMOTION_STATUS = REJECTED`\n"
        "> - `execution_status = BLOCKED_BY_SYSTEM`\n"
        "> - Deterministic SMC engine remains the sole production authority.\n\n---\n\n"
    )

    lines.append("## 2. Macro Out-of-Sample Performance Summary (20 Months: Jan 2025 – Aug 2026)\n\n")
    lines.append("| Metric | SMC Baseline | AI Filtered (Ridge @ +0.20R) | Incremental Delta (Δ) |\n")
    lines.append("|---|---:|---:|---:|\n")
    lines.append(f"| **Evaluated Setups ($N$)** | `{smc['n']}` | `{ai['n']}` | `{ai['n'] - smc['n']}` |\n")
    lines.append(f"| **Coverage %** | `100.00%` | `{ai['coverage_pct']:.2f}%` | — |\n")
    lines.append(f"| **Expectancy (R)** | `{smc['expectancy_r']:+.4f}R` | **`{ai['expectancy_r']:+.4f}R`** | **`{rep['incremental_expectancy_r']:+.4f}R`** |\n")
    lines.append(f"| **Win Rate %** | `{smc['win_rate_pct']:.2f}%` | **`{ai['win_rate_pct']:.2f}%`** | `{ai['win_rate_pct'] - smc['win_rate_pct']:+.2f}%` |\n")
    lines.append(f"| **Profit Factor** | `{smc['profit_factor']:.2f}` | **`{ai['profit_factor']:.2f}`** | `{ai['profit_factor'] - smc['profit_factor']:+.2f}` |\n")
    lines.append(f"| **Total Realized R** | `{smc['total_r']:+.2f}R` | **`{ai['total_r']:+.2f}R`** | `{ai['total_r'] - smc['total_r']:+.2f}R` |\n")
    lines.append(f"| **Max Drawdown (R)** | `{smc['max_drawdown_r']:.2f}R` | **`{ai['max_drawdown_r']:.2f}R`** | **`{ai['max_drawdown_r'] - smc['max_drawdown_r']:+.2f}R`** |\n\n---\n\n")

    lines.append("## 3. Annual Performance Comparison (2025 vs 2026)\n\n")
    lines.append("| Period | Total OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for yr, ydata in ann.items():
        lines.append(
            f"| **`{yr}`** | {ydata['total_oos_setups']} | {ydata['ai_accepted_count']} | {ydata['coverage_pct']:.1f}% | "
            f"`{ydata['smc_expectancy_r']:+.4f}R` | `{ydata['ai_expectancy_r']:+.4f}R` | **`{ydata['incremental_expectancy_r']:+.4f}R`** | "
            f"{ydata['ai_win_rate_pct']:.1f}% | {ydata['ai_profit_factor']:.2f} | {ydata['ai_max_drawdown_r']:.2f}R |\n"
        )
    lines.append("\n---\n\n")

    lines.append("## 4. Dependence-Aware Moving Block Bootstrap (10,000 Resamples)\n\n")
    lines.append(
        f"Moving Block Bootstrap with block size $b={boot['block_size']}$ bars across 10,000 resamples:\n\n"
    )
    lines.append("| Population | Mean Expectancy (R) | 95% Two-Sided Confidence Interval | $P(\\Delta > 0)$ |\n")
    lines.append("|---|---:|:---:|---:|\n")
    lines.append(f"| **SMC Baseline** | `{smc['expectancy_r']:+.4f}R` | `[{boot['smc_expectancy_95ci'][0]:+.4f}R, {boot['smc_expectancy_95ci'][1]:+.4f}R]` | — |\n")
    lines.append(f"| **AI Filtered** | `{ai['expectancy_r']:+.4f}R` | `[{boot['ai_expectancy_95ci'][0]:+.4f}R, {boot['ai_expectancy_95ci'][1]:+.4f}R]` | — |\n")
    lines.append(f"| **Incremental Delta (\\Delta)** | **`{rep['incremental_expectancy_r']:+.4f}R`** | **`[{boot['incremental_expectancy_95ci'][0]:+.4f}R, {boot['incremental_expectancy_95ci'][1]:+.4f}R]`** | **`{boot['p_value_incremental_greater_than_zero']*100:.1f}%`** |\n\n---\n\n")

    lines.append("## 5. Coverage-Matched Random Benchmark (10,000 Resamples)\n\n")
    lines.append(
        f"Benchmarked against **10,000 random selections of exactly $N={r_bm['target_trade_count']}$ trades** sampled from the 1,239 OOS universe:\n\n"
    )
    lines.append("| Strategy / Benchmark | Selected Trades ($N$) | Mean Expectancy (R) | 95% Empirical Interval | Percentile Rank in Random Distribution |\n")
    lines.append("|---|---:|---:|:---:|:---:|\n")
    lines.append(f"| **Random Subsets Benchmark** | `{r_bm['target_trade_count']}` | `{r_bm['random_benchmark_mean_exp']:+.4f}R` | `[{r_bm['random_benchmark_95ci'][0]:+.4f}R, {r_bm['random_benchmark_95ci'][1]:+.4f}R]` | `50.0%` |\n")
    lines.append(f"| **Phase T AI Filter (Ridge)** | `{r_bm['target_trade_count']}` | **`{r_bm['ridge_realized_expectancy']:+.4f}R`** | — | **`{r_bm['ridge_percentile_rank_in_random_distribution']:.1f}th Percentile`** |\n\n")
    lines.append(f"> [!TIP]\n> **Empirical P-Value:** $P(\\text{{Random Expectancy}} \\ge {r_bm['ridge_realized_expectancy']:+.4f}\\text{{R}}) = {r_bm['empirical_p_value_ridge_vs_random']:.4f}$.\n\n---\n\n")

    lines.append("## 6. Month-by-Month Consistency Table (20 Windows)\n\n")
    lines.append("| Window | Test Month | Candidate Train | Mature Train | Test Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF |\n")
    lines.append("|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for w in results["window_results"]:
        lines.append(
            f"| `{w['window_id']}` | **`{w['test_month']}`** | {w['candidate_train_rows']} | {w['mature_train_rows']} | "
            f"{w['test_rows']} | {w['accepted_count']} | {w['acceptance_rate_pct']:.1f}% | "
            f"`{w['smc_baseline']['expectancy_r']:+.4f}R` | `{w['ai_filtered']['expectancy_r']:+.4f}R` | **`{w['incremental_expectancy_r']:+.4f}R`** | "
            f"{w['ai_filtered']['win_rate_pct']:.1f}% | {w['ai_filtered']['profit_factor']:.2f} |\n"
        )
    lines.append(f"\n*Temporal Stability:* **`{m_aud['consistency_fraction_pct']:.1f}%` of test months exhibited positive incremental expectancy ({m_aud['positive_incremental_months']}/{m_aud['total_test_months']} months).**\n\n---\n\n")

    lines.append("## 7. Cross-Asset Breakdown (BTCUSD, ETHUSD, SOLUSD, XRPUSD)\n\n")
    lines.append("| Asset | Total OOS Setups | AI Accepted | Coverage % | SMC Exp (R) | AI Exp (R) | Delta Exp (R) | AI Win Rate | AI PF | AI MDD |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for sym, a in a_aud["asset_breakdown"].items():
        lines.append(
            f"| **`{sym}`** | {a['total_oos_setups']} | {a['ai_accepted_count']} | {a['coverage_pct']:.1f}% | "
            f"`{a['smc_expectancy_r']:+.4f}R` | `{a['ai_expectancy_r']:+.4f}R` | **`{a['incremental_expectancy_r']:+.4f}R`** | {a['ai_win_rate_pct']:.1f}% | {a['ai_profit_factor']:.2f} | {a['ai_max_drawdown_r']:.2f}R |\n"
        )
    lines.append(f"\n*Cross-Asset Consistency:* **`{a_aud['consistency_fraction_pct']:.1f}%` of assets exhibited positive incremental expectancy ({a_aud['positive_incremental_assets']}/{a_aud['total_assets']} assets).**\n\n---\n\n")

    lines.append("## 8. Score Diagnostics & Calibration\n\n")
    lines.append(
        f"- **Winners Mean Score vs Losers Mean Score:** `{score['winners_mean_score']:+.4f}R` vs `{score['losers_mean_score']:+.4f}R` "
        f"($\\Delta = {score['score_separation_delta']:+.4f}R$, Mann-Whitney U $p = {score['mann_whitney_u_p_value']:.4f}$)\n"
        f"- **Spearman Rank Correlation (Predicted R vs Realized R):** $\\rho = {score['spearman_rank_correlation']['rho']:+.4f}$ ($p = {score['spearman_rank_correlation']['p_value']:.4f}$)\n\n"
    )
    lines.append("### Score Quintile Calibration:\n\n")
    lines.append("| Score Quintile | Sample Count | Score Range (R) | Mean Predicted R | Mean Realized R | Win Rate % | Profit Factor |\n")
    lines.append("|---|---:|:---:|---:|---:|---:|---:|\n")
    for q in score["quintile_calibration"]:
        lines.append(
            f"| **`{q['quintile']}`** | {q['count']} | `[{q['min_score']:+.2f}R, {q['max_score']:+.2f}R]` | "
            f"`{q['mean_prediction']:+.4f}R` | `{q['mean_realized_r']:+.4f}R` | {q['win_rate_pct']:.1f}% | {q['profit_factor']:.2f} |\n"
        )
    lines.append("\n---\n\n")

    lines.append("## 9. Economic Translation (1.0% Fixed-Fractional Risk on $10,000 Base)\n\n")
    lines.append(
        f"Simulating a conservative 1.0% risk per trade on an initial balance of ${econ['initial_balance_usd']:,.2f} across the 20-month OOS period:\n\n"
    )
    lines.append("| Strategy | Initial Equity | Terminal Equity | Net Return % | Max Dollar Drawdown % |\n")
    lines.append("|---|---:|---:|---:|---:|\n")
    lines.append(f"| **SMC Baseline** | `${econ['initial_balance_usd']:,.2f}` | `${econ['smc_terminal_balance_usd']:,.2f}` | `{econ['smc_net_return_pct']:+.2f}%` | `{econ['smc_max_drawdown_pct']:.2f}%` |\n")
    lines.append(f"| **Phase T AI Filter** | `${econ['initial_balance_usd']:,.2f}` | **`${econ['ai_terminal_balance_usd']:,.2f}`** | **`{econ['ai_net_return_pct']:+.2f}%`** | **`{econ['ai_max_drawdown_pct']:.2f}%`** |\n\n---\n\n")

    lines.append("## 10. Governance Recommendation\n\n")
    lines.append(f"{cls['governance_recommendation']}\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
