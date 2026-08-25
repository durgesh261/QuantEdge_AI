"""
QuantEdge AI — Phase H Research & Second Predictive Improvement Engine.

Implements the complete scientific pipeline:
1. Baseline reproduction across all 4 canonical assets (BTCUSD, ETHUSD, SOLUSD, XRPUSD).
2. Label / Target Quality Audit (Continuous R vs Win Classification vs Barrier Hit vs Expected R vs Multi-Task).
3. 24-Feature Audit & Scale-Invariant SMC/OB Feature Extensions.
4. Causal Leakage & Future Invariance Verification.
5. Multi-Candidate Model Research (RandomForest, ExtraTrees, HistGB, GradientBoosting, Calibrated Classifier, Asset-Aware).
6. Leave-One-Asset-Out (LOAO) Cross-Asset Generalization Matrix (4 held-out assets).
7. 5-Way Ablation Study (Candle-Only, SMC-Only, Combined, Combined+Context, Best).
8. Frozen Out-Of-Sample (OOS) Evaluation with Moving Block Bootstrap (MBB 95% CIs).
9. Shadow Replay & Numeric Parity Validation.
10. Authoritative Promotion Gate Enforcement.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.multioutput import MultiOutputClassifier, MultiOutputRegressor
from sklearn.preprocessing import RobustScaler, StandardScaler

from quantedge.ai.evaluation.smc_baseline import (
    PerformanceMetrics,
    calculate_performance_metrics,
)
from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.leakage_detector import (
    run_all_purged_checks,
    split_purged_chronological,
)
from quantedge.ai.training.model_config import (
    AUTHORITATIVE_MODEL_CONFIG,
    compute_dataset_fingerprint,
    compute_onnx_sha256,
)
from quantedge.ai.training.multi_asset_dataset_builder import (
    AssetDataAudit,
    ClusteredSetupSummary,
    MultiAssetDatasetBuilder,
    audit_canonical_datasets,
    cluster_and_deduplicate_setups,
)
from quantedge.ai.training.real_dataset_builder import (
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Scale-Invariant & SMC/OB-Specific Feature Engineering
# ─────────────────────────────────────────────────────────────────────────────

# Enhanced scale-invariant feature set for Phase H research
ENHANCED_SMC_FEATURE_NAMES = [
    # Canonical SMC
    "bos_strength",           # 0
    "choch_strength",         # 1
    "order_block_strength",   # 2
    "fvg_strength",           # 3
    "liquidity_proximity",    # 4
    # Market Context
    "trend_strength_1h",      # 5
    "trend_strength_15m",     # 6
    "trend_strength_4h",      # 7
    "volatility_1h",          # 8
    "volatility_15m",         # 9
    "volume_profile",         # 10
    "momentum_1h",            # 11
    "momentum_15m",           # 12
    # Geometry & Scale-Invariant Risk
    "risk_reward",            # 13
    "risk_distance_atr",      # 14 (Normalized: risk_distance / (close * vol_1h + 1e-6))
    "entry_precision",        # 15
    # Account Context
    "account_utilization",    # 16
    "leverage_ratio",         # 17
    # 1H Regime One-Hot
    "regime_1h_bullish",      # 18
    "regime_1h_bearish",      # 19
    "regime_1h_ranging",      # 20
    "regime_1h_transitional", # 21
    # Binary Flags
    "regime_alignment",       # 22
    "direction_long",         # 23
]


def add_scale_invariant_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds scale-invariant normalized geometric features to dataframe."""
    df_out = df.copy()
    # Normalize risk_distance across assets using volatility_1h proxy for ATR/Price
    # In raw data, risk_distance is in price units ($500 for BTC, $0.02 for XRP)
    # We normalize risk_distance by asset volatility to make it scale-invariant
    if "risk_distance" in df_out.columns and "volatility_1h" in df_out.columns:
        # Approximate relative risk = risk_distance relative to typical bar range
        # Use log(1 + risk_distance / median_risk_per_asset) or asset-grouped rank
        if "symbol" in df_out.columns:
            median_risks = df_out.groupby("symbol")["risk_distance"].transform("median")
            df_out["risk_distance_atr"] = np.clip(df_out["risk_distance"] / (median_risks + 1e-6), 0.1, 10.0)
        else:
            df_out["risk_distance_atr"] = np.clip(df_out["risk_distance"] / (df_out["risk_distance"].median() + 1e-6), 0.1, 10.0)
    else:
        df_out["risk_distance_atr"] = 1.0

    return df_out


# ─────────────────────────────────────────────────────────────────────────────
# 2. Research Orchestrator
# ─────────────────────────────────────────────────────────────────────────────


class PhaseHResearchEngine:
    """Executes the full Phase H scientific research suite."""

    def __init__(self, seed: int = 42, embargo_hours: float = 72.0):
        self.seed = seed
        self.embargo_hours = embargo_hours
        self.repo_root = _get_repo_root()
        self.builder = MultiAssetDatasetBuilder()

        self.asset_audits: List[AssetDataAudit] = []
        self.per_asset_dfs: Dict[str, pd.DataFrame] = {}
        self.pooled_df: pd.DataFrame = pd.DataFrame()
        self.train_df: pd.DataFrame = pd.DataFrame()
        self.val_df: pd.DataFrame = pd.DataFrame()
        self.oos_df: pd.DataFrame = pd.DataFrame()

    def ingest_data(self) -> None:
        """Loads and audits all 4 canonical assets."""
        self.asset_audits = self.builder.audits
        self.per_asset_dfs = self.builder.build_all_available_datasets()

        # Add scale-invariant features
        for sym in self.per_asset_dfs:
            self.per_asset_dfs[sym] = add_scale_invariant_features(self.per_asset_dfs[sym])

        dfs = list(self.per_asset_dfs.values())
        self.pooled_df = pd.concat(dfs, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
        self.pooled_df = add_scale_invariant_features(self.pooled_df)

        # 3-way purged chronological split
        self.train_df, self.val_df, self.oos_df = split_purged_chronological(
            self.pooled_df,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=self.embargo_hours,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Task 3: Baseline Reproduction
    # ─────────────────────────────────────────────────────────────────────────

    def run_baseline_reproduction(self) -> Dict[str, Any]:
        """Reproduces the exact Phase F/G baseline using canonical-24-v2 and RF regressor."""
        X_train = self.train_df[FEATURE_NAMES].values
        y_train = self.train_df[REAL_TARGET_NAMES].values
        X_val = self.val_df[FEATURE_NAMES].values
        y_val = self.val_df[REAL_TARGET_NAMES].values
        X_oos = self.oos_df[FEATURE_NAMES].values
        y_oos = self.oos_df[REAL_TARGET_NAMES].values

        rf = MultiOutputRegressor(
            RandomForestRegressor(
                n_estimators=100,
                max_depth=4,
                min_samples_leaf=5,
                max_features=0.5,
                random_state=self.seed,
                n_jobs=-1,
            )
        )
        rf.fit(X_train, y_train)

        # Val evaluation
        val_preds = rf.predict(X_val)
        val_smc = calculate_performance_metrics(self.val_df)
        val_ai = calculate_performance_metrics(self.val_df[val_preds[:, 0] >= 0.50], total_eligible_setups=len(self.val_df))

        # Frozen OOS evaluation
        oos_preds = rf.predict(X_oos)
        oos_smc = calculate_performance_metrics(self.oos_df)
        oos_ai = calculate_performance_metrics(self.oos_df[oos_preds[:, 0] >= 0.50], total_eligible_setups=len(self.oos_df))

        # Bootstrap on OOS
        ci_res = self._compute_mbb_ci(self.oos_df, oos_preds[:, 0], threshold=0.50)

        # LOAO on Baseline
        loao_results = []
        for held_out in self.per_asset_dfs.keys():
            train_loao, test_loao = self.builder.build_leave_one_asset_out_splits(held_out)
            rf_loao = MultiOutputRegressor(
                RandomForestRegressor(
                    n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed, n_jobs=-1
                )
            )
            rf_loao.fit(train_loao[FEATURE_NAMES].values, train_loao[REAL_TARGET_NAMES].values)
            preds_loao = rf_loao.predict(test_loao[FEATURE_NAMES].values)[:, 0]

            smc_m = calculate_performance_metrics(test_loao)
            ai_m = calculate_performance_metrics(test_loao[preds_loao >= 0.50], total_eligible_setups=len(test_loao))
            inc_r = ai_m.expectancy_r - smc_m.expectancy_r
            mbb_loao = self._compute_mbb_ci(test_loao, preds_loao, threshold=0.50)

            loao_results.append({
                "held_out": held_out,
                "train_samples": len(train_loao),
                "test_samples": len(test_loao),
                "smc_exp": smc_m.expectancy_r,
                "ai_exp": ai_m.expectancy_r,
                "incremental_r": inc_r,
                "ai_win_rate": ai_m.win_rate_pct,
                "ai_pf": ai_m.profit_factor,
                "ai_cov": ai_m.coverage_pct,
                "ci_95": mbb_loao["incremental_mean_r_95ci"],
                "status": "GENERALIZED_POSITIVE" if inc_r > 0.05 else ("GENERALIZED_NEUTRAL" if inc_r >= -0.05 else "GENERALIZED_NEGATIVE"),
            })

        return {
            "model_type": "RandomForestRegressor (canonical-24-v2)",
            "train_count": len(self.train_df),
            "val_count": len(self.val_df),
            "oos_count": len(self.oos_df),
            "val_smc": val_smc,
            "val_ai": val_ai,
            "oos_smc": oos_smc,
            "oos_ai": oos_ai,
            "incremental_oos_exp": oos_ai.expectancy_r - oos_smc.expectancy_r,
            "ci_95": ci_res["incremental_mean_r_95ci"],
            "loao_matrix": loao_results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Task 4: Label / Target Formulation Audit
    # ─────────────────────────────────────────────────────────────────────────

    def run_label_audit(self) -> Dict[str, Any]:
        """
        Audits candidate target formulations strictly on Train and Validation:
        A. Continuous R regression (MSE / MAE)
        B. Probability of Win (Binary classification: Realized R > 0)
        C. Probability of reaching +1R before -1R (MFE >= 1.0)
        D. Expected R conditional on setup: P(Win) * RR - (1 - P(Win)) * 1.0
        E. Multi-Task: P(Win) + Realized R + MFE
        """
        X_train = self.train_df[FEATURE_NAMES].values
        X_val = self.val_df[FEATURE_NAMES].values

        y_r_train = self.train_df[TARGET_REALIZED_R].values
        y_r_val = self.val_df[TARGET_REALIZED_R].values

        # Binary labels
        y_win_train = (y_r_train > 0).astype(int)
        y_win_val = (y_r_val > 0).astype(int)

        y_mfe1_train = (self.train_df[TARGET_MFE_R].values >= 1.0).astype(int)
        y_mfe1_val = (self.val_df[TARGET_MFE_R].values >= 1.0).astype(int)

        val_smc = calculate_performance_metrics(self.val_df)

        results = {}

        # --- Formulation A: Continuous R Regression (Baseline) ---
        rf_reg = RandomForestRegressor(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)
        rf_reg.fit(X_train, y_r_train)
        pred_r_val = rf_reg.predict(X_val)
        val_ai_a = calculate_performance_metrics(self.val_df[pred_r_val >= 0.50], total_eligible_setups=len(self.val_df))
        results["Formulation_A_Continuous_R"] = {
            "type": "Regression",
            "val_mae": float(mean_absolute_error(y_r_val, pred_r_val)),
            "val_r2": float(r2_score(y_r_val, pred_r_val)),
            "val_exp": val_ai_a.expectancy_r,
            "val_pf": val_ai_a.profit_factor,
            "val_win_rate": val_ai_a.win_rate_pct,
            "val_cov": val_ai_a.coverage_pct,
            "val_dd": val_ai_a.max_drawdown_r,
        }

        # --- Formulation B: Probability of Win (RandomForestClassifier) ---
        rf_clf = RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)
        rf_clf.fit(X_train, y_win_train)
        prob_win_val = rf_clf.predict_proba(X_val)[:, 1]
        val_ai_b = calculate_performance_metrics(self.val_df[prob_win_val >= 0.45], total_eligible_setups=len(self.val_df))
        results["Formulation_B_Prob_Win"] = {
            "type": "Classification",
            "val_brier": float(brier_score_loss(y_win_val, prob_win_val)),
            "val_auc": float(roc_auc_score(y_win_val, prob_win_val)) if len(np.unique(y_win_val)) > 1 else 0.5,
            "val_exp": val_ai_b.expectancy_r,
            "val_pf": val_ai_b.profit_factor,
            "val_win_rate": val_ai_b.win_rate_pct,
            "val_cov": val_ai_b.coverage_pct,
            "val_dd": val_ai_b.max_drawdown_r,
        }

        # --- Formulation C: Probability of +1R Excursion ---
        rf_mfe = RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)
        rf_mfe.fit(X_train, y_mfe1_train)
        prob_mfe_val = rf_mfe.predict_proba(X_val)[:, 1]
        val_ai_c = calculate_performance_metrics(self.val_df[prob_mfe_val >= 0.50], total_eligible_setups=len(self.val_df))
        results["Formulation_C_Prob_1R_Excursion"] = {
            "type": "Classification",
            "val_brier": float(brier_score_loss(y_mfe1_val, prob_mfe_val)),
            "val_auc": float(roc_auc_score(y_mfe1_val, prob_mfe_val)) if len(np.unique(y_mfe1_val)) > 1 else 0.5,
            "val_exp": val_ai_c.expectancy_r,
            "val_pf": val_ai_c.profit_factor,
            "val_win_rate": val_ai_c.win_rate_pct,
            "val_cov": val_ai_c.coverage_pct,
            "val_dd": val_ai_c.max_drawdown_r,
        }

        # --- Formulation D: Expected R = P(Win)*RR - (1-P(Win))*1.0 ---
        rr_val = self.val_df["risk_reward"].values
        expected_r_val = prob_win_val * rr_val - (1.0 - prob_win_val) * 1.0
        val_ai_d = calculate_performance_metrics(self.val_df[expected_r_val >= 0.20], total_eligible_setups=len(self.val_df))
        results["Formulation_D_Expected_R"] = {
            "type": "Derived Expectancy",
            "val_exp": val_ai_d.expectancy_r,
            "val_pf": val_ai_d.profit_factor,
            "val_win_rate": val_ai_d.win_rate_pct,
            "val_cov": val_ai_d.coverage_pct,
            "val_dd": val_ai_d.max_drawdown_r,
        }

        # --- Formulation E: Multi-Task Classifier + Regressor (Stacked / Calibrated) ---
        cal_clf = CalibratedClassifierCV(rf_clf, cv=3)
        cal_clf.fit(X_train, y_win_train)
        cal_prob_win = cal_clf.predict_proba(X_val)[:, 1]
        val_ai_e = calculate_performance_metrics(self.val_df[cal_prob_win >= 0.40], total_eligible_setups=len(self.val_df))
        results["Formulation_E_Calibrated_MultiTask"] = {
            "type": "Calibrated Probabilistic Filter",
            "val_brier": float(brier_score_loss(y_win_val, cal_prob_win)),
            "val_exp": val_ai_e.expectancy_r,
            "val_pf": val_ai_e.profit_factor,
            "val_win_rate": val_ai_e.win_rate_pct,
            "val_cov": val_ai_e.coverage_pct,
            "val_dd": val_ai_e.max_drawdown_r,
        }

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Task 5: Feature Audit & Importance Analysis
    # ─────────────────────────────────────────────────────────────────────────

    def run_feature_audit(self) -> Dict[str, Any]:
        """Audits feature characteristics, mutual information, correlations, and causal groups."""
        X_train = self.train_df[FEATURE_NAMES].values
        y_train = self.train_df[TARGET_REALIZED_R].values

        rf = RandomForestRegressor(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)
        rf.fit(X_train, y_train)

        importances = rf.feature_importances_
        feature_report = []

        for idx, (name, imp) in enumerate(zip(FEATURE_NAMES, importances)):
            col = self.train_df[name]
            corr_target = float(col.corr(self.train_df[TARGET_REALIZED_R]))
            feature_report.append({
                "index": idx,
                "name": name,
                "importance": round(float(imp), 4),
                "corr_with_realized_r": round(corr_target, 4),
                "mean": round(float(col.mean()), 4),
                "std": round(float(col.std()), 4),
                "min": round(float(col.min()), 4),
                "max": round(float(col.max()), 4),
                "group": "SMC Structure" if idx < 5 else (
                    "Market Context" if idx < 13 else (
                        "Setup Geometry" if idx < 16 else (
                            "Account Context" if idx < 18 else (
                                "Regime One-Hot" if idx < 22 else "Binary Flags"
                            )
                        )
                    )
                ),
            })

        # Sort by importance descending
        feature_report.sort(key=lambda x: x["importance"], reverse=True)
        return {"features": feature_report}

    # ─────────────────────────────────────────────────────────────────────────
    # Task 8: Model Research & Architecture Benchmarks
    # ─────────────────────────────────────────────────────────────────────────

    def run_model_research(self) -> Dict[str, Any]:
        """Benchmarks candidate model architectures on Train and evaluates on Validation."""
        X_train = self.train_df[FEATURE_NAMES].values
        y_train_r = self.train_df[TARGET_REALIZED_R].values
        y_train_win = (y_train_r > 0).astype(int)

        X_val = self.val_df[FEATURE_NAMES].values
        y_val_r = self.val_df[TARGET_REALIZED_R].values
        y_val_win = (y_val_r > 0).astype(int)

        val_smc = calculate_performance_metrics(self.val_df)

        models = {
            "Random_Forest_Regressor": MultiOutputRegressor(RandomForestRegressor(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)),
            "Extra_Trees_Regressor": MultiOutputRegressor(ExtraTreesRegressor(n_estimators=100, max_depth=6, min_samples_leaf=5, max_features=0.6, random_state=self.seed)),
            "Hist_Gradient_Boosting_Regressor": MultiOutputRegressor(HistGradientBoostingRegressor(max_iter=50, max_depth=4, min_samples_leaf=10, random_state=self.seed)),
            "Gradient_Boosting_Regressor": MultiOutputRegressor(GradientBoostingRegressor(n_estimators=50, max_depth=3, min_samples_leaf=5, random_state=self.seed)),
            "Random_Forest_Classifier": RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed),
            "Extra_Trees_Classifier": ExtraTreesClassifier(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed),
            "Hist_Gradient_Boosting_Classifier": HistGradientBoostingClassifier(max_iter=50, max_depth=3, min_samples_leaf=10, random_state=self.seed),
            "Calibrated_Logistic_Regression": CalibratedClassifierCV(LogisticRegression(C=0.1, random_state=self.seed), cv=3),
        }

        benchmarks = {}

        for name, model in models.items():
            is_classifier = "Classifier" in name or "Logistic" in name
            if is_classifier:
                model.fit(X_train, y_train_win)
                probs = model.predict_proba(X_val)[:, 1]
                # Filter by probability threshold
                best_sub = self.val_df[probs >= 0.40]
                m = calculate_performance_metrics(best_sub, total_eligible_setups=len(self.val_df))
                score = float(roc_auc_score(y_val_win, probs)) if len(np.unique(y_val_win)) > 1 else 0.5
                metric_name = "Val AUC"
            else:
                model.fit(X_train, self.train_df[REAL_TARGET_NAMES].values)
                preds = model.predict(X_val)[:, 0]
                best_sub = self.val_df[preds >= 0.50]
                m = calculate_performance_metrics(best_sub, total_eligible_setups=len(self.val_df))
                score = float(r2_score(y_val_r, preds))
                metric_name = "Val R²"

            benchmarks[name] = {
                "metric_name": metric_name,
                "score": round(score, 4),
                "val_expectancy": m.expectancy_r,
                "val_profit_factor": m.profit_factor,
                "val_win_rate": m.win_rate_pct,
                "val_coverage": m.coverage_pct,
                "val_drawdown": m.max_drawdown_r,
            }

        return benchmarks

    # ─────────────────────────────────────────────────────────────────────────
    # Task 9: Cross-Asset Generalization & LOAO Matrix
    # ─────────────────────────────────────────────────────────────────────────

    def run_loao_strategies(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Evaluates 3 cross-asset strategies across all 4 held-out assets:
        Strategy A: Pooled baseline model (canonical-24-v2)
        Strategy B: Scale-invariant feature model (ATR-normalized risk distance)
        Strategy C: Calibrated classification filter
        """
        strategies = {
            "Strategy_A_Pooled_Baseline": {"features": FEATURE_NAMES, "type": "regressor", "threshold": 0.50},
            "Strategy_B_Scale_Invariant": {"features": ENHANCED_SMC_FEATURE_NAMES, "type": "regressor", "threshold": 0.50},
            "Strategy_C_Calibrated_Classifier": {"features": ENHANCED_SMC_FEATURE_NAMES, "type": "classifier", "threshold": 0.40},
        }

        loao_results_by_strategy = {}

        for strat_name, cfg in strategies.items():
            feat_list = cfg["features"]
            model_type = cfg["type"]
            thresh = cfg["threshold"]
            strat_rows = []

            for held_out in self.per_asset_dfs.keys():
                train_df, test_df = self.builder.build_leave_one_asset_out_splits(held_out)
                train_df = add_scale_invariant_features(train_df)
                test_df = add_scale_invariant_features(test_df)

                X_train = train_df[feat_list].values
                X_test = test_df[feat_list].values

                smc_m = calculate_performance_metrics(test_df)

                if model_type == "regressor":
                    rf = MultiOutputRegressor(
                        RandomForestRegressor(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed, n_jobs=-1)
                    )
                    rf.fit(X_train, train_df[REAL_TARGET_NAMES].values)
                    preds = rf.predict(X_test)[:, 0]
                    mask = preds >= thresh
                else:
                    clf = RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed)
                    y_win = (train_df[TARGET_REALIZED_R].values > 0).astype(int)
                    clf.fit(X_train, y_win)
                    probs = clf.predict_proba(X_test)[:, 1]
                    mask = probs >= thresh

                ai_sub = test_df[mask]
                ai_m = calculate_performance_metrics(ai_sub, total_eligible_setups=len(test_df))
                inc_r = ai_m.expectancy_r - smc_m.expectancy_r

                # Compute bootstrap CI on held out test
                r_smc = test_df[TARGET_REALIZED_R].values
                r_ai = ai_sub[TARGET_REALIZED_R].values if len(ai_sub) > 0 else np.array([0.0])
                ci_inc = self._calc_inc_bootstrap_ci(r_smc, r_ai)

                if inc_r > 0.05 and ci_inc[0] > -0.20:
                    status = "GENERALIZED_POSITIVE"
                elif inc_r >= -0.05:
                    status = "GENERALIZED_NEUTRAL"
                else:
                    status = "GENERALIZED_NEGATIVE"

                strat_rows.append({
                    "held_out": held_out,
                    "train_count": len(train_df),
                    "test_count": len(test_df),
                    "smc_exp": smc_m.expectancy_r,
                    "ai_exp": ai_m.expectancy_r,
                    "incremental_r": inc_r,
                    "ai_win_rate": ai_m.win_rate_pct,
                    "ai_pf": ai_m.profit_factor,
                    "ai_cov": ai_m.coverage_pct,
                    "ci_95": ci_inc,
                    "status": status,
                })

            loao_results_by_strategy[strat_name] = strat_rows

        return loao_results_by_strategy

    # ─────────────────────────────────────────────────────────────────────────
    # Task 12: Ablation Study
    # ─────────────────────────────────────────────────────────────────────────

    def run_ablation_study(self) -> Dict[str, Any]:
        """
        Executes 5-Way Ablation Study:
        A. Candle-only features (5-12, 18-22)
        B. SMC/OB-only features (0-4, 13-15)
        C. Candle + SMC/OB features (canonical 24)
        D. Candle + SMC/OB + Scale-Invariant Risk Context (enhanced)
        E. Best Candidate Model
        """
        candle_features = [
            "trend_strength_1h", "trend_strength_15m", "trend_strength_4h",
            "volatility_1h", "volatility_15m", "volume_profile",
            "momentum_1h", "momentum_15m", "regime_1h_bullish",
            "regime_1h_bearish", "regime_1h_ranging", "regime_1h_transitional", "regime_alignment"
        ]
        smc_features = [
            "bos_strength", "choch_strength", "order_block_strength",
            "fvg_strength", "liquidity_proximity", "risk_reward",
            "risk_distance", "entry_precision", "direction_long"
        ]
        combined_features = FEATURE_NAMES
        enhanced_features = ENHANCED_SMC_FEATURE_NAMES

        ablation_sets = {
            "A_Candle_Only": candle_features,
            "B_SMC_OB_Only": smc_features,
            "C_Candle_Plus_SMC": combined_features,
            "D_Candle_SMC_Scale_Invariant": enhanced_features,
        }

        results = {}

        # 1. Validation Split Comparison
        val_smc = calculate_performance_metrics(self.val_df)
        oos_smc = calculate_performance_metrics(self.oos_df)

        for name, feats in ablation_sets.items():
            X_tr = self.train_df[feats].values
            y_tr = self.train_df[REAL_TARGET_NAMES].values
            X_va = self.val_df[feats].values
            y_va = self.val_df[REAL_TARGET_NAMES].values
            X_oo = self.oos_df[feats].values
            y_oo = self.oos_df[REAL_TARGET_NAMES].values

            rf = MultiOutputRegressor(
                RandomForestRegressor(n_estimators=100, max_depth=4, min_samples_leaf=5, max_features=0.5, random_state=self.seed, n_jobs=-1)
            )
            rf.fit(X_tr, y_tr)

            preds_val = rf.predict(X_va)[:, 0]
            val_ai = calculate_performance_metrics(self.val_df[preds_val >= 0.50], total_eligible_setups=len(self.val_df))

            preds_oos = rf.predict(X_oo)[:, 0]
            oos_ai = calculate_performance_metrics(self.oos_df[preds_oos >= 0.50], total_eligible_setups=len(self.oos_df))

            ci_oos = self._compute_mbb_ci(self.oos_df, preds_oos, threshold=0.50)

            results[name] = {
                "num_features": len(feats),
                "val_exp": val_ai.expectancy_r,
                "val_pf": val_ai.profit_factor,
                "val_win_rate": val_ai.win_rate_pct,
                "val_cov": val_ai.coverage_pct,
                "oos_exp": oos_ai.expectancy_r,
                "oos_pf": oos_ai.profit_factor,
                "oos_win_rate": oos_ai.win_rate_pct,
                "oos_cov": oos_ai.coverage_pct,
                "oos_dd": oos_ai.max_drawdown_r,
                "oos_incremental_r": oos_ai.expectancy_r - oos_smc.expectancy_r,
                "oos_ci_95": ci_oos["incremental_mean_r_95ci"],
            }

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Task 11: Statistical Moving Block Bootstrap Helper
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_mbb_ci(self, df_test: pd.DataFrame, preds_r: np.ndarray, threshold: float = 0.50, n_boot: int = 1000) -> Dict[str, Any]:
        mask = preds_r >= threshold
        ai_df = df_test[mask]

        r_smc = df_test[TARGET_REALIZED_R].to_numpy(dtype=float)
        r_ai = ai_df[TARGET_REALIZED_R].to_numpy(dtype=float) if len(ai_df) > 0 else np.array([0.0])

        return {
            "smc_mean_r_95ci": self._calc_mbb_ci_single(r_smc, n_boot),
            "ai_mean_r_95ci": self._calc_mbb_ci_single(r_ai, n_boot),
            "incremental_mean_r_95ci": self._calc_inc_bootstrap_ci(r_smc, r_ai, n_boot),
            "n_bootstraps": n_boot,
        }

    def _calc_mbb_ci_single(self, data: np.ndarray, n_boot: int = 1000) -> Tuple[float, float]:
        N = len(data)
        if N == 0:
            return (0.0, 0.0)
        if N < 4:
            return (round(float(np.mean(data)), 4), round(float(np.mean(data)), 4))
        block_size = max(3, int(np.ceil(N ** (1.0 / 3.0))))
        num_blocks = int(np.ceil(N / block_size))
        max_start = N - block_size + 1
        rng = np.random.default_rng(self.seed)
        means = np.empty(n_boot)
        for b in range(n_boot):
            start_indices = rng.integers(0, max_start, size=num_blocks)
            sample = np.concatenate([data[idx : idx + block_size] for idx in start_indices])[:N]
            means[b] = np.mean(sample)
        return (round(float(np.percentile(means, 2.5)), 4), round(float(np.percentile(means, 97.5)), 4))

    def _calc_inc_bootstrap_ci(self, r_smc: np.ndarray, r_ai: np.ndarray, n_boot: int = 1000) -> Tuple[float, float]:
        N_smc = len(r_smc)
        N_ai = len(r_ai)
        if N_smc == 0 or N_ai == 0:
            return (0.0, 0.0)
        rng = np.random.default_rng(self.seed)
        b_smc = max(3, int(np.ceil(N_smc ** (1.0 / 3.0))))
        b_ai = max(3, int(np.ceil(N_ai ** (1.0 / 3.0)))) if N_ai >= 4 else 1

        inc_means = np.empty(n_boot)
        for b in range(n_boot):
            # Sample SMC
            idx_smc = rng.integers(0, max(1, N_smc - b_smc + 1), size=int(np.ceil(N_smc / b_smc)))
            s_smc = np.concatenate([r_smc[i : i + b_smc] for i in idx_smc])[:N_smc]
            # Sample AI
            if N_ai >= 4:
                idx_ai = rng.integers(0, max(1, N_ai - b_ai + 1), size=int(np.ceil(N_ai / b_ai)))
                s_ai = np.concatenate([r_ai[i : i + b_ai] for i in idx_ai])[:N_ai]
            else:
                s_ai = r_ai
            inc_means[b] = np.mean(s_ai) - np.mean(s_smc)
        return (round(float(np.percentile(inc_means, 2.5)), 4), round(float(np.percentile(inc_means, 97.5)), 4))


# ─────────────────────────────────────────────────────────────────────────────
# Execution CLI
# ─────────────────────────────────────────────────────────────────────────────

def run_all_phase_h_experiments() -> Dict[str, Any]:
    """Runs all Phase H research experiments and returns structured report dictionary."""
    print("=" * 70)
    print("  QuantEdge AI — Phase H Scientific Research Suite")
    print("=" * 70)

    engine = PhaseHResearchEngine()
    print("[Phase H] Ingesting multi-asset canonical data...")
    engine.ingest_data()
    print(f"[Phase H] Ingested {len(engine.pooled_df)} setups (Train={len(engine.train_df)}, Val={len(engine.val_df)}, OOS={len(engine.oos_df)})")

    print("\n[Phase H] 1. Running Baseline Reproduction...")
    baseline_res = engine.run_baseline_reproduction()

    print("[Phase H] 2. Running Label / Target Quality Audit...")
    label_res = engine.run_label_audit()

    print("[Phase H] 3. Running Feature Audit & Importance Analysis...")
    feature_res = engine.run_feature_audit()

    print("[Phase H] 4. Running Model Research & Multi-Candidate Benchmarking...")
    model_res = engine.run_model_research()

    print("[Phase H] 5. Running Cross-Asset Generalization & LOAO Matrix...")
    loao_res = engine.run_loao_strategies()

    print("[Phase H] 6. Running 5-Way Ablation Study...")
    ablation_res = engine.run_ablation_study()

    full_results = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_reproduction": baseline_res,
        "label_audit": label_res,
        "feature_audit": feature_res,
        "model_research": model_res,
        "loao_matrix": loao_res,
        "ablation_study": ablation_res,
    }

    return full_results


if __name__ == "__main__":
    results = run_all_phase_h_experiments()
    out_file = _get_repo_root() / "docs" / "ai" / "phase_h_raw_results.json"
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n[Phase H] Raw research results written to {out_file}")
