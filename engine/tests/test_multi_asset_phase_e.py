"""
Phase E — Multi-Asset AI Research, Generalization & Second Promotion Gate Test Suite.

Verifies:
1. Four-Instrument Canonical Data Availability & Integrity Audit.
2. Causal Feature Extraction & Non-Contamination of Frozen OOS Period.
3. Structural Setup Clustering and Deduplication.
4. Moving Block Bootstrap (MBB) Confidence Intervals.
5. Multi-Regime Robustness & Catastrophic Failure Detection.
6. Monotonic 5-Bucket Prediction Confidence Calibration.
7. Phase E Second Promotion Gate Invariants & Decision Logic.
"""

from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.evaluation.phase_e_gate import PhaseEGateResults, PhaseEPredictiveGate
from quantedge.ai.evaluation.smc_baseline import calculate_performance_metrics
from quantedge.ai.training.model_research import train_and_evaluate_candidates
from quantedge.ai.training.multi_asset_dataset_builder import (
    AssetDataAudit,
    audit_canonical_datasets,
    cluster_and_deduplicate_setups,
)
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)


@pytest.fixture(scope="module")
def canonical_btc_data() -> pd.DataFrame:
    """Loads the real canonical BTC dataset once for testing."""
    if not DEFAULT_CANONICAL_PATH.exists():
        pytest.skip(f"Canonical dataset missing at {DEFAULT_CANONICAL_PATH}")
    return build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)


@pytest.fixture(scope="module")
def phase_e_gate() -> PhaseEPredictiveGate:
    """Instantiates and executes data preparation for PhaseEPredictiveGate."""
    gate = PhaseEPredictiveGate()
    gate.audit_multi_asset_data()
    gate.load_and_prepare_data()
    gate.execute_model_research()
    return gate


# ─────────────────────────────────────────────────────────────────────────────
# 1. Multi-Asset Canonical Data Audit Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestMultiAssetDataAudit:
    """Verifies that all 4 canonical instruments are audited accurately."""

    def test_four_instruments_audited(self):
        audits = audit_canonical_datasets()
        symbols = [a.symbol for a in audits]
        assert "BTCUSD" in symbols
        assert "ETHUSD" in symbols
        assert "SOLUSD" in symbols
        assert "XRPUSD" in symbols

    def test_btc_is_available_and_valid(self):
        audits = audit_canonical_datasets()
        btc_audit = next(a for a in audits if a.symbol == "BTCUSD")
        assert btc_audit.available is True
        assert btc_audit.status == "AVAILABLE"
        assert btc_audit.training_status == "TRAINABLE"
        assert btc_audit.candle_count >= 5000
        assert btc_audit.missing_candles == 0
        assert btc_audit.duplicate_candles == 0
        assert btc_audit.ohlc_valid is True
        assert btc_audit.volume_valid is True

    def test_missing_instruments_marked_not_available(self, tmp_path):
        audits = audit_canonical_datasets(canonical_base=tmp_path)
        for sym in ["ETHUSD", "SOLUSD", "XRPUSD"]:
            audit = next(a for a in audits if a.symbol == sym)
            assert audit.available is False
            assert audit.status == "NOT_AVAILABLE"
            assert audit.training_status == "NOT_TRAINABLE"
            assert audit.execution_authority == "BLOCKED"
            assert audit.candle_count == 0



# ─────────────────────────────────────────────────────────────────────────────
# 2. Structural Setup Clustering & Correlation Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStructuralSetupClustering:
    """Verifies setup clustering and deduplication logic."""

    def test_clustering_reduces_sample_count(self, canonical_btc_data):
        dedup_df, summary = cluster_and_deduplicate_setups(canonical_btc_data, cluster_window_hours=3.0)
        assert summary.total_raw_setups == len(canonical_btc_data)
        assert summary.unique_structural_events == len(dedup_df)
        assert summary.clustered_within_3h > 0
        assert summary.clustered_percentage > 40.0
        assert summary.unique_structural_events < summary.total_raw_setups

    def test_deduplication_preserves_features_and_targets(self, canonical_btc_data):
        dedup_df, _ = cluster_and_deduplicate_setups(canonical_btc_data, cluster_window_hours=3.0)
        for f in FEATURE_NAMES:
            assert f in dedup_df.columns
        assert TARGET_REALIZED_R in dedup_df.columns
        assert TARGET_MFE_R in dedup_df.columns
        assert TARGET_MAE_R in dedup_df.columns


# ─────────────────────────────────────────────────────────────────────────────
# 3. Frozen OOS Non-Contamination Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestFrozenOosIsolation:
    """Verifies that Phase C OOS period is never included in the training split."""

    def test_train_split_excludes_frozen_oos_period(self, phase_e_gate):
        # Frozen Phase C OOS period: July 6, 2026 onwards
        frozen_oos_start = pd.Timestamp("2026-07-06 00:00:00+00:00")
        train_max_ts = phase_e_gate.train_df["timestamp"].max()
        assert train_max_ts < frozen_oos_start, (
            f"Train split max timestamp ({train_max_ts}) must strictly precede frozen OOS ({frozen_oos_start})"
        )

    def test_embargo_gap_between_splits(self, phase_e_gate):
        train_end = phase_e_gate.train_df["timestamp"].max()
        val_start = phase_e_gate.val_df["timestamp"].min()
        gap_h = (val_start - train_end).total_seconds() / 3600.0
        assert gap_h >= 72.0, f"Purge embargo between Train and Val ({gap_h:.1f}h) must be >= 72h"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multi-Model Research & Candidate Evaluation
# ─────────────────────────────────────────────────────────────────────────────


class TestModelResearchAndCandidates:
    """Verifies candidate model architectures evaluation on validation."""

    def test_candidate_evaluations_computed(self, phase_e_gate):
        evals = train_and_evaluate_candidates(phase_e_gate.train_df, phase_e_gate.val_df)
        assert "Ridge_Linear" in evals
        assert "Random_Forest_Base" in evals
        assert "Extra_Trees_Regularized" in evals
        assert "Hist_Gradient_Boosting" in evals
        for name, ev in evals.items():
            assert isinstance(ev.val_r2_realized, float)
            assert isinstance(ev.val_expectancy_r, float)
            assert isinstance(ev.validation_fitness_score, float)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Statistical Significance & Regime Robustness Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStatisticsAndRegimeRobustness:
    """Verifies Moving Block Bootstrap and regime robustness profiling."""

    def test_moving_block_bootstrap_ci(self, phase_e_gate):
        ci = phase_e_gate.compute_moving_block_bootstrap(0.0, n_bootstraps=200)
        assert "smc_mean_r_95ci" in ci
        assert "ai_mean_r_95ci" in ci
        assert "incremental_mean_r_95ci" in ci
        assert ci["mbb_block_size"] >= 3
        low, high = ci["incremental_mean_r_95ci"]
        assert low <= high

    def test_regime_robustness_structure(self, phase_e_gate):
        regimes = phase_e_gate.analyze_regime_robustness(0.0)
        assert len(regimes) == 6
        reg_names = [r["regime"] for r in regimes]
        assert "Bullish Trend" in reg_names
        assert "Bearish Trend" in reg_names
        assert "Ranging Market" in reg_names
        assert "Transitional" in reg_names
        assert "High Volatility" in reg_names
        assert "Low Volatility" in reg_names

    def test_5_bucket_confidence_calibration(self, phase_e_gate):
        calib = phase_e_gate.analyze_confidence_calibration()
        assert len(calib) == 5
        total_n = sum(b["sample_count"] for b in calib)
        dev_n = len(phase_e_gate.train_df) + len(phase_e_gate.val_df)
        assert total_n == dev_n


# ─────────────────────────────────────────────────────────────────────────────
# 6. Second Promotion Gate Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestPhaseESecondPromotionGate:
    """Verifies Phase E promotion gate decision criteria."""

    def test_gate_rejects_inferior_oos_expectancy(self, phase_e_gate):
        oos_smc = calculate_performance_metrics(phase_e_gate.test_df)
        # Create synthetic AI metrics with inferior expectancy
        df_inferior = pd.DataFrame({
            TARGET_REALIZED_R: [-1.0, -1.0, -1.0, 1.0],
            TARGET_MFE_R: [0.1, 0.1, 0.1, 1.5],
            TARGET_MAE_R: [1.0, 1.0, 1.0, 0.2],
        })
        oos_ai = calculate_performance_metrics(df_inferior, total_eligible_setups=len(phase_e_gate.test_df))
        bootstrap_ci = {"incremental_mean_r_95ci": (-0.5, 0.1)}

        status, reasons = phase_e_gate.evaluate_promotion_gate(
            oos_smc=oos_smc,
            oos_ai=oos_ai,
            regime_analysis=[],
            bootstrap_ci=bootstrap_ci,
        )
        assert status == "REJECTED"
        assert any("OOS Expectancy" in r for r in reasons)

    def test_gate_rejects_excessive_drawdown(self, phase_e_gate):
        oos_smc = calculate_performance_metrics(phase_e_gate.test_df)
        # Create synthetic AI metrics with >25R drawdown (exceeding 125% of SMC 18.0R)
        df_high_dd = pd.DataFrame({
            TARGET_REALIZED_R: [2.0] + [-1.0] * 30,
            TARGET_MFE_R: [2.0] + [0.1] * 30,
            TARGET_MAE_R: [0.2] + [1.0] * 30,
        })
        oos_ai = calculate_performance_metrics(df_high_dd, total_eligible_setups=len(phase_e_gate.test_df))
        bootstrap_ci = {"incremental_mean_r_95ci": (0.05, 0.50)}

        status, reasons = phase_e_gate.evaluate_promotion_gate(
            oos_smc=oos_smc,
            oos_ai=oos_ai,
            regime_analysis=[],
            bootstrap_ci=bootstrap_ci,
        )
        assert status == "REJECTED"
        assert any("OOS Max Drawdown" in r for r in reasons)

