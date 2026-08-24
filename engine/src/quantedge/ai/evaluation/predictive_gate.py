"""
QuantEdge AI Predictive-Value Gate & SMC vs AI Comparative Evaluation Engine.

Executes the Phase C rigorous validation process:
- Pipeline audit & feature/target causality checks
- Deterministic SMC strategy baseline calculation
- AI-filtered strategy evaluation with validation-only threshold tuning
- Comprehensive diagnostics for out-of-sample performance (distribution shift, regime breakdown,
  feature importance, ablation, baseline comparisons, confidence calibration, statistical bootstrap)
- Single untouched Out-Of-Sample (OOS) test gate
- Authoritative promotion decision (APPROVED, REJECTED, or INSUFFICIENT_DATA)
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    run_all_purged_checks,
    split_purged_chronological,
    validate_purged_chronological_split,
)
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)
from quantedge.ai.evaluation.four_instrument_audit import (
    audit_four_instruments,
    format_four_instrument_report,
)
from quantedge.ai.evaluation.smc_baseline import (
    PerformanceMetrics,
    calculate_performance_metrics,
    format_performance_table,
)


@dataclass(frozen=True)
class ThresholdEvaluation:
    threshold_r: float
    val_metrics: PerformanceMetrics
    coverage_pct: float
    is_valid: bool
    selection_score: float  # Expectancy * sqrt(coverage)


@dataclass(frozen=True)
class GateResults:
    status: str  # "APPROVED", "REJECTED", "INSUFFICIENT_DATA"
    reasons: List[str]
    frozen_threshold_r: float
    train_smc: PerformanceMetrics
    train_ai: PerformanceMetrics
    val_smc: PerformanceMetrics
    val_ai: PerformanceMetrics
    oos_smc: PerformanceMetrics
    oos_ai: PerformanceMetrics
    feature_importance: Dict[str, float]
    ablation_results: Dict[str, Dict[str, float]]
    baseline_comparisons: Dict[str, Dict[str, float]]
    regime_breakdown: List[Dict[str, Any]]
    monthly_breakdown: List[Dict[str, Any]]
    calibration_buckets: List[Dict[str, Any]]
    bootstrap_ci: Dict[str, Tuple[float, float]]
    clustering_audit: Dict[str, Any]


class AIPredictiveValueGate:
    """Orchestrates the entire AI predictive-value validation and promotion gate."""

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        embargo_hours: float = 72.0,
        min_coverage_pct: float = 10.0,
        min_qualified_setups: int = 5,
        random_seed: int = 42,
    ):
        self.csv_path = csv_path or DEFAULT_CANONICAL_PATH
        self.embargo_hours = embargo_hours
        self.min_coverage_pct = min_coverage_pct
        self.min_qualified_setups = min_qualified_setups
        self.seed = random_seed

        # Internal state
        self.raw_df: Optional[pd.DataFrame] = None
        self.train_df: Optional[pd.DataFrame] = None
        self.val_df: Optional[pd.DataFrame] = None
        self.test_df: Optional[pd.DataFrame] = None
        self.model: Optional[RandomForestRegressor] = None

    def load_and_split_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Loads real historical data and performs 3-way purged chronological split."""
        print("[Gate] Ingesting real historical market data...")
        self.raw_df = build_real_training_dataset(csv_path=self.csv_path, verbose=False)
        self.train_df, self.val_df, self.test_df = split_purged_chronological(
            self.raw_df,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=self.embargo_hours,
        )
        print(f"[Gate] Data split: Train={len(self.train_df)}, Val={len(self.val_df)}, Test={len(self.test_df)}")
        return self.train_df, self.val_df, self.test_df

    def audit_setup_clustering(self) -> Dict[str, Any]:
        """Audits duplicate or temporally clustered SMC setups."""
        df = self.raw_df
        if df is None:
            raise ValueError("Data not loaded")

        total_setups = len(df)
        df_sorted = df.sort_values("timestamp").reset_index(drop=True)

        # 1. Clustered setups within <= 3 bars
        time_diffs_h = (df_sorted["timestamp"].diff().dt.total_seconds() / 3600.0).fillna(999.0)
        clustered_count = int(np.sum(time_diffs_h <= 3.0))

        # 2. Duplicate setups with same direction and entry price within 0.05%
        near_duplicate_count = 0
        for i in range(1, len(df_sorted)):
            prev = df_sorted.iloc[i - 1]
            curr = df_sorted.iloc[i]
            if prev["direction_long"] == curr["direction_long"]:
                p_dist = abs(float(curr.get("entry_precision", 0)) - float(prev.get("entry_precision", 0)))
                t_diff = (curr["timestamp"] - prev["timestamp"]).total_seconds() / 3600.0
                if t_diff <= 24.0 and p_dist < 0.01:
                    near_duplicate_count += 1

        unique_structural_events = total_setups - clustered_count

        return {
            "total_raw_setups": total_setups,
            "clustered_within_3h": clustered_count,
            "clustered_pct": round(clustered_count / total_setups * 100.0, 1),
            "near_duplicate_setups": near_duplicate_count,
            "unique_structural_events_approx": unique_structural_events,
        }

    def train_model(self) -> RandomForestRegressor:
        """Trains multi-output Random Forest exclusively on the Train split."""
        X_train = self.train_df[FEATURE_NAMES].values
        y_train = self.train_df[REAL_TARGET_NAMES].values

        self.model = RandomForestRegressor(
            n_estimators=100,
            max_depth=8,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=self.seed,
            n_jobs=-1,
        )
        self.model.fit(X_train, y_train)
        return self.model

    def select_best_threshold(self) -> Tuple[float, List[ThresholdEvaluation]]:
        """
        Sweeps candidate thresholds strictly on the Validation set.
        Enforces minimum coverage constraint (>= 10% of setups and >= min_qualified_setups).
        Freezes the threshold BEFORE looking at Out-Of-Sample test data.
        """
        X_val = self.val_df[FEATURE_NAMES].values
        preds_val = self.model.predict(X_val)  # Shape (N, 3): [realized_r, mfe_r, mae_r]
        pred_r_val = preds_val[:, 0]

        candidate_thresholds = [-0.5, -0.2, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
        evaluations: List[ThresholdEvaluation] = []

        best_score = -999.0
        best_threshold = 0.0

        n_val_total = len(self.val_df)

        for thresh in candidate_thresholds:
            mask = pred_r_val >= thresh
            qualified_indices = np.where(mask)[0]
            n_qual = len(qualified_indices)
            cov_pct = (n_qual / n_val_total * 100.0) if n_val_total > 0 else 0.0

            if n_qual >= self.min_qualified_setups and cov_pct >= self.min_coverage_pct:
                sub_df = self.val_df.iloc[qualified_indices]
                perf = calculate_performance_metrics(sub_df, total_eligible_setups=n_val_total)
                # Selection score rewards positive expectancy while penalizing excessive trade collapse
                score = perf.expectancy_r * np.sqrt(cov_pct / 100.0)
                is_valid = True
                if score > best_score:
                    best_score = score
                    best_threshold = thresh
            else:
                perf = calculate_performance_metrics(pd.DataFrame(columns=self.val_df.columns), total_eligible_setups=n_val_total)
                score = -999.0
                is_valid = False

            evaluations.append(
                ThresholdEvaluation(
                    threshold_r=thresh,
                    val_metrics=perf,
                    coverage_pct=cov_pct,
                    is_valid=is_valid,
                    selection_score=round(score, 4),
                )
            )

        # Fallback: if no threshold passes, default to 0.0R
        if best_score == -999.0:
            best_threshold = 0.0

        return best_threshold, evaluations

    def evaluate_filter_on_split(self, df: pd.DataFrame, threshold_r: float) -> PerformanceMetrics:
        """Applies the frozen model and frozen threshold to a dataset split."""
        X = df[FEATURE_NAMES].values
        preds = self.model.predict(X)
        pred_r = preds[:, 0]

        mask = pred_r >= threshold_r
        qualified_indices = np.where(mask)[0]
        sub_df = df.iloc[qualified_indices]

        return calculate_performance_metrics(sub_df, total_eligible_setups=len(df))

    def analyze_distribution_shift(self) -> Dict[str, Any]:
        """Analyzes distribution shift of features and targets across Train, Val, and OOS."""
        metrics = {}
        for col in [TARGET_REALIZED_R, TARGET_MFE_R, TARGET_MAE_R, "volatility_1h", "momentum_1h", "risk_reward"]:
            if col in self.train_df.columns:
                train_m, train_s = float(self.train_df[col].mean()), float(self.train_df[col].std())
                val_m, val_s = float(self.val_df[col].mean()), float(self.val_df[col].std())
                test_m, test_s = float(self.test_df[col].mean()), float(self.test_df[col].std())

                metrics[col] = {
                    "train_mean_std": f"{train_m:.3f} ± {train_s:.3f}",
                    "val_mean_std": f"{val_m:.3f} ± {val_s:.3f}",
                    "test_mean_std": f"{test_m:.3f} ± {test_s:.3f}",
                    "val_shift_z": round((val_m - train_m) / (train_s + 1e-6), 2),
                    "test_shift_z": round((test_m - train_m) / (train_s + 1e-6), 2),
                }
        return metrics

    def compute_feature_importance(self) -> Dict[str, float]:
        """Calculates Random Forest feature importance using TRAINING DATA ONLY."""
        importances = self.model.feature_importances_
        res = {name: float(round(imp, 4)) for name, imp in zip(FEATURE_NAMES, importances)}
        # Sort descending
        return dict(sorted(res.items(), key=lambda item: item[1], reverse=True))

    def run_ablation_study(self) -> Dict[str, Dict[str, float]]:
        """Evaluates feature subsets on the Validation split (never touches OOS)."""
        feature_groups = {
            "SMC_Structural": FEATURE_NAMES[0:5],
            "Market_Context": FEATURE_NAMES[5:13],
            "Setup_Geometry": FEATURE_NAMES[13:16],
            "Account_Context": FEATURE_NAMES[16:18],
            "Regime_OneHot": FEATURE_NAMES[18:22],
            "All_24_Features": FEATURE_NAMES,
        }

        results = {}
        y_train = self.train_df[REAL_TARGET_NAMES].values
        y_val = self.val_df[REAL_TARGET_NAMES].values

        for group_name, feats in feature_groups.items():
            X_tr = self.train_df[feats].values
            X_v = self.val_df[feats].values

            rf = RandomForestRegressor(n_estimators=100, max_depth=8, random_state=self.seed, n_jobs=-1)
            rf.fit(X_tr, y_train)
            preds_v = rf.predict(X_v)

            r_realized_r2 = r2_score(y_val[:, 0], preds_v[:, 0])
            r_realized_mae = mean_absolute_error(y_val[:, 0], preds_v[:, 0])

            results[group_name] = {
                "num_features": len(feats),
                "val_realized_r2": round(float(r_realized_r2), 4),
                "val_realized_mae": round(float(r_realized_mae), 4),
            }

        return results

    def compare_against_baselines(self) -> Dict[str, Dict[str, float]]:
        """Compares the ML model against simple baseline predictors on Validation."""
        y_train_r = self.train_df[TARGET_REALIZED_R].values
        y_val_r = self.val_df[TARGET_REALIZED_R].values

        mean_val = float(np.mean(y_train_r))
        median_val = float(np.median(y_train_r))

        # Model predictions
        rf_preds_r = self.model.predict(self.val_df[FEATURE_NAMES].values)[:, 0]

        # Naive predictions
        mean_preds = np.full_like(y_val_r, mean_val)
        median_preds = np.full_like(y_val_r, median_val)
        np.random.seed(self.seed)
        random_preds = np.random.choice(y_train_r, size=len(y_val_r))

        return {
            "Random_Forest_AI": {
                "MAE": round(float(mean_absolute_error(y_val_r, rf_preds_r)), 4),
                "MSE": round(float(mean_squared_error(y_val_r, rf_preds_r)), 4),
                "R2": round(float(r2_score(y_val_r, rf_preds_r)), 4),
            },
            "Mean_Predictor": {
                "MAE": round(float(mean_absolute_error(y_val_r, mean_preds)), 4),
                "MSE": round(float(mean_squared_error(y_val_r, mean_preds)), 4),
                "R2": round(float(r2_score(y_val_r, mean_preds)), 4),
            },
            "Median_Predictor": {
                "MAE": round(float(mean_absolute_error(y_val_r, median_preds)), 4),
                "MSE": round(float(mean_squared_error(y_val_r, median_preds)), 4),
                "R2": round(float(r2_score(y_val_r, median_preds)), 4),
            },
            "Random_Shuffle_Baseline": {
                "MAE": round(float(mean_absolute_error(y_val_r, random_preds)), 4),
                "MSE": round(float(mean_squared_error(y_val_r, random_preds)), 4),
                "R2": round(float(r2_score(y_val_r, random_preds)), 4),
            },
        }

    def analyze_confidence_calibration(self, threshold_r: float) -> List[Dict[str, Any]]:
        """
        Analyzes calibration of model predictions strictly on Train + Validation splits.
        Keeps OOS dataset completely untouched for final gate evaluation.
        """
        df_dev = pd.concat([self.train_df, self.val_df], ignore_index=True)
        X_dev = df_dev[FEATURE_NAMES].values
        preds_r = self.model.predict(X_dev)[:, 0]
        actual_r = df_dev[TARGET_REALIZED_R].values

        buckets = [
            ("< 0.0R (Bearish/Avoid)", preds_r < 0.0),
            ("0.0R – 0.2R (Low)", (preds_r >= 0.0) & (preds_r < 0.2)),
            ("0.2R – 0.5R (Moderate)", (preds_r >= 0.2) & (preds_r < 0.5)),
            (">= 0.5R (High)", preds_r >= 0.5),
        ]

        calibration = []
        for name, mask in buckets:
            n = int(np.sum(mask))
            if n > 0:
                sub_r = actual_r[mask]
                win_rate = float(np.sum(sub_r > 0.0) / n * 100.0)
                mean_r = float(np.mean(sub_r))
                median_r = float(np.median(sub_r))
            else:
                win_rate, mean_r, median_r = 0.0, 0.0, 0.0

            calibration.append({
                "bucket": name,
                "sample_count": n,
                "win_rate_pct": round(win_rate, 1),
                "mean_realized_r": round(mean_r, 4),
                "median_realized_r": round(median_r, 4),
            })
        return calibration

    def analyze_regime_breakdown(self, threshold_r: float) -> List[Dict[str, Any]]:
        """
        Evaluates performance by market regime strictly on Train + Validation splits.
        Keeps OOS dataset untouched for final gate evaluation.
        """
        df_dev = pd.concat([self.train_df, self.val_df], ignore_index=True)
        X = df_dev[FEATURE_NAMES].values
        preds_r = self.model.predict(X)[:, 0]

        regimes = [
            ("Bullish Trend", df_dev["regime_1h_bullish"] == 1.0),
            ("Bearish Trend", df_dev["regime_1h_bearish"] == 1.0),
            ("Ranging Market", df_dev["regime_1h_ranging"] == 1.0),
            ("Transitional", df_dev["regime_1h_transitional"] == 1.0),
        ]

        rows = []
        for reg_name, mask in regimes:
            reg_df = df_dev[mask]
            n_smc = len(reg_df)
            if n_smc == 0:
                continue

            smc_perf = calculate_performance_metrics(reg_df)
            ai_mask = mask & (preds_r >= threshold_r)
            ai_df = df_dev[ai_mask]
            ai_perf = calculate_performance_metrics(ai_df, total_eligible_setups=n_smc)

            rows.append({
                "regime": reg_name,
                "smc_setups": smc_perf.executed_setups,
                "smc_win_rate_pct": smc_perf.win_rate_pct,
                "smc_mean_r": smc_perf.mean_r,
                "ai_setups": ai_perf.executed_setups,
                "ai_win_rate_pct": ai_perf.win_rate_pct,
                "ai_mean_r": ai_perf.mean_r,
                "coverage_pct": ai_perf.coverage_pct,
            })
        return rows

    def analyze_monthly_breakdown(self, threshold_r: float) -> List[Dict[str, Any]]:
        """
        Evaluates chronological performance by month strictly on Train + Validation splits.
        Keeps OOS dataset untouched for final gate evaluation.
        """
        df_dev = pd.concat([self.train_df, self.val_df], ignore_index=True).copy()
        X = df_dev[FEATURE_NAMES].values
        preds_r = self.model.predict(X)[:, 0]
        df_dev["ai_qualified"] = preds_r >= threshold_r
        df_dev["month"] = df_dev["timestamp"].dt.strftime("%Y-%m")

        months = sorted(df_dev["month"].unique())
        rows = []
        for m in months:
            m_df = df_dev[df_dev["month"] == m]
            smc_perf = calculate_performance_metrics(m_df)
            ai_df = m_df[m_df["ai_qualified"]]
            ai_perf = calculate_performance_metrics(ai_df, total_eligible_setups=len(m_df))

            rows.append({
                "month": m,
                "smc_trades": smc_perf.executed_setups,
                "smc_win_rate": smc_perf.win_rate_pct,
                "smc_mean_r": smc_perf.mean_r,
                "smc_total_r": smc_perf.total_r,
                "ai_trades": ai_perf.executed_setups,
                "ai_win_rate": ai_perf.win_rate_pct,
                "ai_mean_r": ai_perf.mean_r,
                "ai_total_r": ai_perf.total_r,
                "coverage_pct": ai_perf.coverage_pct,
            })
        return rows

    def compute_bootstrap_confidence_intervals(
        self, threshold_r: float, n_bootstraps: int = 1000
    ) -> Dict[str, Tuple[float, float]]:
        """
        Computes Moving Block Bootstrap (MBB) 95% confidence intervals on OOS test trades.
        Block size B = max(3, ceil(N^(1/3))) captures temporal autocorrelation in time-series trades.
        """
        X_test = self.test_df[FEATURE_NAMES].values
        preds_r = self.model.predict(X_test)[:, 0]
        mask = preds_r >= threshold_r
        ai_test_df = self.test_df[mask]

        r_smc = self.test_df[TARGET_REALIZED_R].to_numpy(dtype=float)
        r_ai = ai_test_df[TARGET_REALIZED_R].to_numpy(dtype=float) if len(ai_test_df) > 0 else np.array([0.0])

        def _moving_block_bootstrap(data: np.ndarray, n_boot: int, seed: int) -> np.ndarray:
            N = len(data)
            if N == 0:
                return np.zeros(n_boot)
            if N < 4:
                return np.full(n_boot, np.mean(data))
            block_size = max(3, int(np.ceil(N ** (1.0 / 3.0))))
            num_blocks = int(np.ceil(N / block_size))
            max_start = N - block_size + 1
            rng = np.random.default_rng(seed)
            means = np.empty(n_boot)
            for b in range(n_boot):
                start_indices = rng.integers(0, max_start, size=num_blocks)
                sample = np.concatenate([data[idx : idx + block_size] for idx in start_indices])[:N]
                means[b] = np.mean(sample)
            return means

        smc_means = _moving_block_bootstrap(r_smc, n_bootstraps, self.seed)
        ai_means = _moving_block_bootstrap(r_ai, n_bootstraps, self.seed)

        return {
            "smc_mean_r_95ci": (round(float(np.percentile(smc_means, 2.5)), 4), round(float(np.percentile(smc_means, 97.5)), 4)),
            "ai_mean_r_95ci": (round(float(np.percentile(ai_means, 2.5)), 4), round(float(np.percentile(ai_means, 97.5)), 4)),
        }

    def execute_gate(self) -> GateResults:
        """Executes the full predictive-value gate sequence."""
        # 1. Ingest and split data
        self.load_and_split_data()

        # 2. Audit setup clustering
        clustering = self.audit_setup_clustering()

        # 3. Train model on Train split
        self.train_model()

        # 4. Select and freeze threshold on Validation split
        best_threshold, threshold_evals = self.select_best_threshold()

        # 5. Evaluate SMC baseline and AI filter across all 3 splits
        train_smc = calculate_performance_metrics(self.train_df)
        train_ai = self.evaluate_filter_on_split(self.train_df, best_threshold)

        val_smc = calculate_performance_metrics(self.val_df)
        val_ai = self.evaluate_filter_on_split(self.val_df, best_threshold)

        # 6. Evaluate on Untouched Final Out-Of-Sample (OOS) Test Split
        oos_smc = calculate_performance_metrics(self.test_df)
        oos_ai = self.evaluate_filter_on_split(self.test_df, best_threshold)

        # 7. Diagnostics (strictly on Train + Val splits)
        importance = self.compute_feature_importance()
        ablation = self.run_ablation_study()
        baselines = self.compare_against_baselines()
        calibration = self.analyze_confidence_calibration(best_threshold)
        regimes = self.analyze_regime_breakdown(best_threshold)
        monthly = self.analyze_monthly_breakdown(best_threshold)
        bootstrap = self.compute_bootstrap_confidence_intervals(best_threshold)

        # 8. Promotion Decision Evaluation
        status, reasons = self._evaluate_promotion_decision(
            val_smc=val_smc,
            val_ai=val_ai,
            oos_smc=oos_smc,
            oos_ai=oos_ai,
            best_threshold=best_threshold,
            baselines=baselines,
            ablation=ablation,
        )

        return GateResults(
            status=status,
            reasons=reasons,
            frozen_threshold_r=best_threshold,
            train_smc=train_smc,
            train_ai=train_ai,
            val_smc=val_smc,
            val_ai=val_ai,
            oos_smc=oos_smc,
            oos_ai=oos_ai,
            feature_importance=importance,
            ablation_results=ablation,
            baseline_comparisons=baselines,
            regime_breakdown=regimes,
            monthly_breakdown=monthly,
            calibration_buckets=calibration,
            bootstrap_ci=bootstrap,
            clustering_audit=clustering,
        )

    def _evaluate_promotion_decision(
        self,
        val_smc: PerformanceMetrics,
        val_ai: PerformanceMetrics,
        oos_smc: PerformanceMetrics,
        oos_ai: PerformanceMetrics,
        best_threshold: float,
        baselines: Dict[str, Dict[str, float]],
        ablation: Dict[str, Dict[str, float]],
    ) -> Tuple[str, List[str]]:
        """
        Evaluates objective production promotion criteria:
        1. Out-of-sample Expectancy / Mean R improvement (E_ai > E_smc).
        2. Out-of-sample Profit Factor superiority (PF_ai >= PF_smc).
        3. Out-of-sample Max Drawdown protection (MDD_ai <= MDD_smc * 1.25).
        4. Minimum coverage maintained (>= 10% on OOS).
        5. Superiority over naive baselines on validation (R2 > 0).
        6. Sample size sufficiency (>= 50 OOS setups).
        """
        reasons = []
        is_approved = True

        # Criterion 1: OOS Expectancy Improvement
        if oos_ai.expectancy_r <= oos_smc.expectancy_r:
            is_approved = False
            reasons.append(
                f"OOS Expectancy ({oos_ai.expectancy_r:+.4f}R) does not exceed SMC Baseline ({oos_smc.expectancy_r:+.4f}R)."
            )

        # Criterion 2: OOS Win Rate or Profit Factor Degradation
        if oos_ai.profit_factor < oos_smc.profit_factor:
            is_approved = False
            reasons.append(
                f"OOS Profit Factor ({oos_ai.profit_factor:.3f}) is inferior to SMC Baseline ({oos_smc.profit_factor:.3f})."
            )

        # Criterion 3: OOS Max Drawdown Protection (MDD_ai <= MDD_smc * 1.25)
        if oos_ai.max_drawdown_r > (oos_smc.max_drawdown_r * 1.25):
            is_approved = False
            reasons.append(
                f"OOS Max Drawdown ({oos_ai.max_drawdown_r:.2f}R) exceeds 125% of SMC baseline ({oos_smc.max_drawdown_r:.2f}R)."
            )

        # Criterion 4: OOS Coverage Constraint
        if oos_ai.coverage_pct < self.min_coverage_pct:
            is_approved = False
            reasons.append(
                f"OOS Coverage ({oos_ai.coverage_pct:.1f}%) is below mandatory minimum ({self.min_coverage_pct}%)."
            )

        # Criterion 5: Baseline Superiority (R2 > 0 vs Mean baseline)
        rf_r2 = baselines.get("Random_Forest_AI", {}).get("R2", -1.0)
        if rf_r2 <= 0.0:
            is_approved = False
            reasons.append(
                f"Validation R2 ({rf_r2:.4f}) is non-positive, indicating model does not beat a naive Mean Predictor."
            )

        # Criterion 6: Sample Size Sufficiency
        if len(self.test_df) < 50:
            return "INSUFFICIENT_DATA", ["Out-of-sample sample size is below 50 historical setups."]

        if is_approved:
            return "APPROVED", ["All predictive, risk, and stability gates passed."]
        else:
            return "REJECTED", reasons

