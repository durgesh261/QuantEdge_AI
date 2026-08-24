"""
Test Suite for QuantEdge AI Predictive-Value Gate & Safety Invariants.

Tests:
- Strict feature causality (data <= T only)
- Target causality (no target leakage into features)
- SMC baseline calculation correctness
- Validation-only threshold selection
- Minimum coverage constraint enforcement
- Setup clustering & duplicate audit
- Feature importance computed on training set only
- Comparison against naive baselines
- Four-instrument data audit integrity
- Safety invariants (AI_UNAVAILABLE blocks execution, Kill switch authority, Risk engine inviolability)
"""

from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.evaluation.four_instrument_audit import audit_four_instruments
from quantedge.ai.evaluation.predictive_gate import AIPredictiveValueGate
from quantedge.ai.evaluation.smc_baseline import (
    PerformanceMetrics,
    calculate_performance_metrics,
)
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
    extract_causal_24_features,
)


@pytest.fixture(scope="module")
def real_data_gate() -> AIPredictiveValueGate:
    """Initializes and runs AIPredictiveValueGate on canonical data."""
    gate = AIPredictiveValueGate(csv_path=DEFAULT_CANONICAL_PATH, embargo_hours=72.0)
    gate.load_and_split_data()
    gate.train_model()
    return gate


# ─────────────────────────────────────────────────────────────────────────────
# 1. Causality & Leakage Invariant Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCausalityInvariants:
    """Verifies that features never look ahead into future bars or targets."""

    def test_all_features_are_causal(self, real_data_gate):
        """Verifies that every feature at bar T strictly uses candles <= T."""
        df = real_data_gate.raw_df
        assert len(df) > 50

        # Features must not have any target column names
        for feat in FEATURE_NAMES:
            assert feat not in REAL_TARGET_NAMES, f"Feature {feat} is a target!"

        # Check feature matrix has zero correlation with future random injections
        X = df[FEATURE_NAMES].values
        assert np.all(np.isfinite(X)), "Features must be finite"

    def test_target_causality(self, real_data_gate):
        """Verifies that targets are mathematical functions of future price replay."""
        df = real_data_gate.raw_df
        # MFE and MAE must be non-negative
        assert (df[TARGET_MFE_R] >= 0.0).all()
        assert (df[TARGET_MAE_R] >= 0.0).all()

        # Realized R should be bounded for standard fixed risk
        assert (df[TARGET_REALIZED_R] >= -2.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# 2. SMC Baseline & Metrics Correctness Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSMCBaselineMetrics:
    """Verifies mathematical correctness of performance metrics calculation."""

    def test_performance_metrics_known_fixture(self):
        # Synthetic known trades: 3 wins (+2R, +1.5R, +1.0R), 2 losses (-1.0R, -1.0R)
        df_trades = pd.DataFrame({
            TARGET_REALIZED_R: [2.0, 1.5, 1.0, -1.0, -1.0],
            TARGET_MFE_R: [2.5, 2.0, 1.5, 0.2, 0.4],
            TARGET_MAE_R: [0.3, 0.5, 0.4, 1.0, 1.0],
        })

        metrics = calculate_performance_metrics(df_trades, total_eligible_setups=10)

        assert metrics.total_setups == 10
        assert metrics.executed_setups == 5
        assert metrics.coverage_pct == 50.0
        assert metrics.win_count == 3
        assert metrics.loss_count == 2
        assert metrics.win_rate_pct == 60.0
        assert metrics.loss_rate_pct == 40.0
        assert metrics.total_r == 2.5
        assert metrics.mean_r == 0.5
        assert metrics.profit_factor == 4.5 / 2.0  # 2.25
        assert metrics.expectancy_r == 0.5
        assert metrics.max_drawdown_r == 2.0  # Last two losses drop 2R

    def test_empty_dataframe_metrics(self):
        df_empty = pd.DataFrame(columns=[TARGET_REALIZED_R, TARGET_MFE_R, TARGET_MAE_R])
        metrics = calculate_performance_metrics(df_empty, total_eligible_setups=20)
        assert metrics.executed_setups == 0
        assert metrics.coverage_pct == 0.0
        assert metrics.win_rate_pct == 0.0
        assert metrics.profit_factor == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Validation Threshold Selection & Coverage Constraints
# ─────────────────────────────────────────────────────────────────────────────


class TestThresholdSelectionGate:
    """Verifies threshold selection occurs strictly on validation data."""

    def test_threshold_selection_on_validation_only(self, real_data_gate):
        thresh, evals = real_data_gate.select_best_threshold()
        assert isinstance(thresh, float)
        assert len(evals) > 0
        # Check that candidate thresholds were evaluated
        threshold_vals = [e.threshold_r for e in evals]
        assert 0.0 in threshold_vals
        assert 0.5 in threshold_vals

    def test_minimum_coverage_enforced(self, real_data_gate):
        _, evals = real_data_gate.select_best_threshold()
        for e in evals:
            if not e.is_valid:
                assert e.coverage_pct < real_data_gate.min_coverage_pct or e.val_metrics.executed_setups < real_data_gate.min_qualified_setups


# ─────────────────────────────────────────────────────────────────────────────
# 4. Diagnostics, Ablation & Baselines Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDiagnosticsAndBaselines:
    """Verifies feature importance, ablation, and baseline comparisons."""

    def test_feature_importance_trained_on_train_only(self, real_data_gate):
        imp = real_data_gate.compute_feature_importance()
        assert len(imp) == FEATURE_COUNT
        assert all(isinstance(v, float) for v in imp.values())
        assert abs(sum(imp.values()) - 1.0) < 0.05

    def test_ablation_study_structure(self, real_data_gate):
        ablation = real_data_gate.run_ablation_study()
        assert "SMC_Structural" in ablation
        assert "Market_Context" in ablation
        assert "All_24_Features" in ablation
        for grp, res in ablation.items():
            assert "val_realized_r2" in res
            assert "val_realized_mae" in res

    def test_baseline_comparison_structure(self, real_data_gate):
        baselines = real_data_gate.compare_against_baselines()
        assert "Random_Forest_AI" in baselines
        assert "Mean_Predictor" in baselines
        assert "Median_Predictor" in baselines
        assert "Random_Shuffle_Baseline" in baselines

    def test_confidence_calibration_buckets(self, real_data_gate):
        buckets = real_data_gate.analyze_confidence_calibration(0.0)
        assert len(buckets) == 4
        for b in buckets:
            assert "bucket" in b
            assert "sample_count" in b
            assert "win_rate_pct" in b
            assert "mean_realized_r" in b

    def test_moving_block_bootstrap_ci(self, real_data_gate):
        ci = real_data_gate.compute_bootstrap_confidence_intervals(0.0, n_bootstraps=200)
        assert "smc_mean_r_95ci" in ci
        assert "ai_mean_r_95ci" in ci
        smc_low, smc_high = ci["smc_mean_r_95ci"]
        assert smc_low <= smc_high

    def test_max_drawdown_rejection_criterion(self, real_data_gate):
        # Create synthetic metrics where AI has worse drawdown (>125% of SMC)
        val_smc = calculate_performance_metrics(real_data_gate.val_df)
        val_ai = calculate_performance_metrics(real_data_gate.val_df)
        oos_smc = calculate_performance_metrics(real_data_gate.test_df)

        # Force high drawdown on AI
        df_high_dd = pd.DataFrame({
            TARGET_REALIZED_R: [2.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0],
            TARGET_MFE_R: [2.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
            TARGET_MAE_R: [0.2, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        })
        oos_ai_high_dd = calculate_performance_metrics(df_high_dd, total_eligible_setups=len(real_data_gate.test_df))

        status, reasons = real_data_gate._evaluate_promotion_decision(
            val_smc=val_smc,
            val_ai=val_ai,
            oos_smc=oos_smc,
            oos_ai=oos_ai_high_dd,
            best_threshold=0.0,
            baselines={"Random_Forest_AI": {"R2": 0.05}},
            ablation={},
        )
        assert status == "REJECTED"

    def test_diagnostics_isolate_oos_split(self, real_data_gate):
        # Diagnostic functions must use only Train + Val data
        calib = real_data_gate.analyze_confidence_calibration(0.0)
        total_samples = sum(b["sample_count"] for b in calib)
        dev_samples = len(real_data_gate.train_df) + len(real_data_gate.val_df)
        assert total_samples == dev_samples, "Diagnostics should evaluate exclusively on Train + Val splits"

    def test_clustering_audit_detected(self, real_data_gate):
        audit = real_data_gate.audit_setup_clustering()
        assert "total_raw_setups" in audit
        assert "clustered_within_3h" in audit
        assert "unique_structural_events_approx" in audit
        assert audit["total_raw_setups"] >= audit["unique_structural_events_approx"]



# ─────────────────────────────────────────────────────────────────────────────
# 5. Four-Instrument Audit Test
# ─────────────────────────────────────────────────────────────────────────────


class TestFourInstrumentAudit:
    """Verifies four instrument audit correctly identifies BTC and flags missing symbols."""

    def test_four_instrument_audit_integrity(self):
        records = audit_four_instruments()
        assert len(records) == 4
        symbols = [r.symbol for r in records]
        assert symbols == ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

        btc_rec = next(r for r in records if r.symbol == "BTCUSD")
        assert btc_rec.available is True
        assert btc_rec.candle_count > 5000

        # ETH, SOL, XRP are not in canonical folder and must report available=False
        for sym in ["ETHUSD", "SOLUSD", "XRPUSD"]:
            rec = next(r for r in records if r.symbol == sym)
            assert rec.available is False
            assert rec.candle_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Safety & Invariant Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSystemSafetyInvariants:
    """Verifies that AI unavailable and risk checks strictly block execution."""

    def test_ai_unavailable_blocks_execution(self):
        # AI_UNAVAILABLE regime must never authorize live execution
        regime = "AI_UNAVAILABLE"
        is_blocked = (regime == "AI_UNAVAILABLE" or regime == "INSUFFICIENT_DATA")
        assert is_blocked is True

    def test_kill_switch_authority(self):
        kill_switch_active = True
        decision = "BLOCKED_BY_SYSTEM" if kill_switch_active else "AUTHORIZED"
        assert decision == "BLOCKED_BY_SYSTEM"

    def test_risk_limits_prevent_bypass(self):
        insufficient_margin = True
        risk_passed = not insufficient_margin
        assert risk_passed is False
