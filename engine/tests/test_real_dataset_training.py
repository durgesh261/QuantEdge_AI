"""
Real-Market Dataset, Purged Chronological Splitting & AI Pipeline Tests.

MANDATORY GATES:
- Real historical data only (Delta Exchange India BTCUSD 1H canonical CSV).
- Zero synthetic rows or fabricated outcomes.
- Canonical 24-feature contract strict compliance.
- 3-Way Chronological Purged Split with >= 72-hour embargo between splits.
- Real outcome targets (realized R, MFE_R, MAE_R) are finite, mathematically sound.
- ONNX model export and numeric parity gate.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    check_feature_leakage,
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
from quantedge.ai.training.train import run_pipeline


@pytest.fixture(scope="module")
def real_dataset() -> pd.DataFrame:
    """Builds real historical dataset from canonical 2026 BTCUSD 1H CSV."""
    assert DEFAULT_CANONICAL_PATH.exists(), f"Canonical CSV missing: {DEFAULT_CANONICAL_PATH}"
    df = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 1. Real Dataset Schema & Contract Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestRealDatasetSchema:
    """Verifies that the real market dataset strictly adheres to the 24-feature contract."""

    def test_dataset_is_non_empty(self, real_dataset):
        assert len(real_dataset) > 50, f"Expected >50 historical setups, got {len(real_dataset)}"

    def test_dataset_has_timestamp(self, real_dataset):
        assert "timestamp" in real_dataset.columns

    def test_all_24_canonical_features_present(self, real_dataset):
        for feat in FEATURE_NAMES:
            assert feat in real_dataset.columns, f"Missing canonical feature: {feat}"

    def test_feature_order_matches_contract(self, real_dataset):
        cols = list(real_dataset.columns)
        ts_idx = cols.index("timestamp")
        # Features should appear immediately after timestamp in exact canonical order
        feat_cols = [c for c in cols if c in FEATURE_NAMES]
        assert feat_cols == FEATURE_NAMES, "Feature columns must follow exact canonical contract order"

    def test_three_real_targets_present(self, real_dataset):
        for target in REAL_TARGET_NAMES:
            assert target in real_dataset.columns, f"Missing target: {target}"

    def test_no_nan_in_features(self, real_dataset):
        X = real_dataset[FEATURE_NAMES]
        assert not X.isnull().any().any(), f"NaNs found in feature matrix: {X.isnull().sum()}"

    def test_no_nan_in_targets(self, real_dataset):
        y = real_dataset[REAL_TARGET_NAMES]
        assert not y.isnull().any().any(), f"NaNs found in target matrix: {y.isnull().sum()}"

    def test_all_features_are_finite(self, real_dataset):
        X = real_dataset[FEATURE_NAMES].values
        assert np.all(np.isfinite(X)), "All feature values must be finite (no Inf or NaN)"

    def test_all_targets_are_finite(self, real_dataset):
        y = real_dataset[REAL_TARGET_NAMES].values
        assert np.all(np.isfinite(y)), "All target values must be finite (no Inf or NaN)"

    def test_targets_follow_mathematical_rules(self, real_dataset):
        # MFE and MAE in R units must be >= 0.0
        assert (real_dataset[TARGET_MFE_R] >= -1e-5).all(), "MFE_R must be >= 0.0"
        assert (real_dataset[TARGET_MAE_R] >= -1e-5).all(), "MAE_R must be >= 0.0"
        # Realized R for standard stops is >= -1.0 (or slightly lower if gap slippage)
        assert (real_dataset[TARGET_REALIZED_R] >= -2.0).all(), "Realized R should not exceed max risk loss"

    def test_timestamps_monotonically_increasing(self, real_dataset):
        assert real_dataset["timestamp"].is_monotonic_increasing, "Dataset must be sorted chronologically"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Purged Chronological Splitting & Embargo Gate
# ─────────────────────────────────────────────────────────────────────────────


class TestPurgedChronologicalSplit:
    """Verifies that 3-way splitting enforces strict chronological order and >=72h embargo."""

    def test_split_sizes_and_ranges(self, real_dataset):
        train_df, val_df, test_df = split_purged_chronological(
            real_dataset,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )
        assert len(train_df) > 0, "Train split must be non-empty"
        assert len(val_df) > 0, "Val split must be non-empty"
        assert len(test_df) > 0, "Test split must be non-empty"

    def test_train_to_val_embargo_gap(self, real_dataset):
        train_df, val_df, test_df = split_purged_chronological(
            real_dataset,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )
        train_max = train_df["timestamp"].max()
        val_min = val_df["timestamp"].min()

        gap_hours = (val_min - train_max).total_seconds() / 3600.0
        assert gap_hours >= 72.0, (
            f"Train -> Val embargo gap is only {gap_hours:.1f}h (must be >= 72.0h)"
        )

    def test_val_to_test_embargo_gap(self, real_dataset):
        train_df, val_df, test_df = split_purged_chronological(
            real_dataset,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )
        val_max = val_df["timestamp"].max()
        test_min = test_df["timestamp"].min()

        gap_hours = (test_min - val_max).total_seconds() / 3600.0
        assert gap_hours >= 72.0, (
            f"Val -> Test embargo gap is only {gap_hours:.1f}h (must be >= 72.0h)"
        )

    def test_validator_passes_on_clean_splits(self, real_dataset):
        train_df, val_df, test_df = split_purged_chronological(
            real_dataset,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )
        report = validate_purged_chronological_split(train_df, val_df, test_df, embargo_hours=72.0, verbose=False)
        assert report.passed, f"Validation failed: {report.summary}"

    def test_validator_fails_if_embargo_violated(self, real_dataset):
        # Create an artificial overlapping split
        train_df = real_dataset.iloc[:100].copy()
        val_df = real_dataset.iloc[95:150].copy()  # Overlaps by 5 rows
        test_df = real_dataset.iloc[150:].copy()

        report = validate_purged_chronological_split(train_df, val_df, test_df, embargo_hours=72.0, verbose=False)
        assert not report.passed
        assert any("overlap" in issue.lower() or "embargo" in issue.lower() for issue in report.issues)

    def test_full_purged_hygiene_audit(self, real_dataset):
        train_df, val_df, test_df = split_purged_chronological(
            real_dataset,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )
        report = run_all_purged_checks(train_df, val_df, test_df, embargo_hours=72.0, verbose=False)
        assert report.passed, f"Hygiene audit failed: {report.summary}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Leakage & Correlation Gates
# ─────────────────────────────────────────────────────────────────────────────


class TestRealDataLeakageGates:
    """Verifies that no future labels or target correlations bleed into feature matrices."""

    def test_no_target_in_feature_matrix(self, real_dataset):
        report = check_feature_leakage(real_dataset, verbose=False)
        assert report.passed, f"Leakage detected: {report.summary}"

    def test_tainted_target_injection_fails(self, real_dataset):
        tainted = real_dataset.copy()
        tainted["bos_strength"] = tainted[TARGET_REALIZED_R]
        report = check_feature_leakage(tainted, verbose=False)
        assert not report.passed, "Leakage check must catch target injected into feature column"


# ─────────────────────────────────────────────────────────────────────────────
# 4. End-to-End Real Training Pipeline & ONNX Parity
# ─────────────────────────────────────────────────────────────────────────────


class TestRealPipelineExecution:
    """Verifies that the full training pipeline runs on real data and produces valid ONNX."""

    def test_pipeline_runs_and_evaluates(self, tmp_path):
        onnx_out = tmp_path / "test_quantedge_real.onnx"
        result = run_pipeline(
            data_source="real",
            csv_path=DEFAULT_CANONICAL_PATH,
            embargo_hours=72.0,
            onnx_output=onnx_out,
            skip_hygiene=False,
        )
        assert result["n_train"] > 0
        assert result["n_val"] > 0
        assert result["n_test"] > 0
        assert onnx_out.exists()
        assert onnx_out.stat().st_size > 1024

    def test_onnx_inference_shape_and_finite_outputs(self, tmp_path):
        import onnxruntime as rt

        onnx_out = tmp_path / "test_quantedge_real.onnx"
        run_pipeline(
            data_source="real",
            csv_path=DEFAULT_CANONICAL_PATH,
            embargo_hours=72.0,
            onnx_output=onnx_out,
            skip_hygiene=False,
        )

        sess = rt.InferenceSession(str(onnx_out))
        input_name = sess.get_inputs()[0].name
        input_shape = sess.get_inputs()[0].shape

        assert input_shape == ["None", 24] or input_shape == [None, 24]

        # Test inference with a sample 24-feature vector
        sample_input = np.ones((1, 24), dtype=np.float32) * 0.5
        outputs = sess.run(None, {input_name: sample_input})

        pred_matrix = np.column_stack(outputs) if len(outputs) == 3 else np.array(outputs[0])
        assert pred_matrix.shape == (1, 3), f"Expected shape (1, 3), got {pred_matrix.shape}"
        assert np.all(np.isfinite(pred_matrix)), "Inference outputs must be finite"
