"""
Phase A: Dataset Audit, Leakage Detection & Temporal Split Validation Tests.

MANDATORY — never skip. These tests are a CI gate before training.

Tests verify:
- Dataset columns match FeatureContract schema exactly.
- All 24 features are present, correctly named, and in contract order.
- No NaN in feature matrix.
- Temporal split is clean (no overlap, correct chronological order).
- No feature has suspiciously high correlation with targets (label bleed).
- Training pipeline produces a valid output dict with required keys.
"""

import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.dataset_builder import build_training_dataset, describe_dataset
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    check_feature_leakage,
    check_temporal_stationarity,
    run_all_checks,
    validate_temporal_split,
)

TARGET_COLUMNS = ["target_pattern_score", "target_signal_score", "target_confidence"]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def small_dataset() -> pd.DataFrame:
    """500-row dataset for fast unit tests."""
    return build_training_dataset(n_samples=500, seed=99)


@pytest.fixture(scope="module")
def medium_dataset() -> pd.DataFrame:
    """2000-row dataset for leakage and temporal tests."""
    return build_training_dataset(n_samples=2_000, seed=42)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Dataset schema validation
# ─────────────────────────────────────────────────────────────────────────────


class TestDatasetSchema:
    """Verifies the dataset produced by build_training_dataset() matches FeatureContract."""

    def test_dataset_has_correct_number_of_rows(self, small_dataset):
        assert len(small_dataset) == 500

    def test_dataset_has_timestamp_column(self, small_dataset):
        assert "timestamp" in small_dataset.columns, (
            "Dataset must have a 'timestamp' column for temporal split validation."
        )

    def test_dataset_has_all_24_feature_columns(self, small_dataset):
        for feat in FEATURE_NAMES:
            assert feat in small_dataset.columns, (
                f"Feature '{feat}' is missing from the dataset. "
                "Check dataset_builder.build_training_dataset()."
            )

    def test_dataset_feature_columns_in_contract_order(self, small_dataset):
        """Feature columns must appear in the same order as FEATURE_NAMES."""
        # Get only the feature columns (skip timestamp and targets)
        feature_cols = [c for c in small_dataset.columns if c in FEATURE_NAMES]
        assert feature_cols == FEATURE_NAMES, (
            f"Column order mismatch.\n"
            f"Expected: {FEATURE_NAMES}\n"
            f"Got:      {feature_cols}"
        )

    def test_dataset_has_three_target_columns(self, small_dataset):
        for target in TARGET_COLUMNS:
            assert target in small_dataset.columns, (
                f"Target column '{target}' missing from dataset."
            )

    def test_dataset_total_columns(self, small_dataset):
        """Expect: 1 timestamp + 24 features + 3 targets = 28 columns."""
        expected_cols = 1 + FEATURE_COUNT + len(TARGET_COLUMNS)
        assert len(small_dataset.columns) == expected_cols, (
            f"Expected {expected_cols} columns, got {len(small_dataset.columns)}. "
            f"Columns: {list(small_dataset.columns)}"
        )

    def test_no_nan_in_features(self, small_dataset):
        """Feature matrix must be fully populated — no NaN allowed."""
        X = small_dataset[FEATURE_NAMES]
        nan_cols = X.columns[X.isnull().any()].tolist()
        assert not nan_cols, (
            f"NaN values found in feature columns: {nan_cols}. "
            "Fix dataset_builder to ensure all features are always populated."
        )

    def test_no_nan_in_targets(self, small_dataset):
        nan_cols = small_dataset[TARGET_COLUMNS].columns[
            small_dataset[TARGET_COLUMNS].isnull().any()
        ].tolist()
        assert not nan_cols, f"NaN in target columns: {nan_cols}"

    def test_all_feature_values_are_finite(self, small_dataset):
        X = small_dataset[FEATURE_NAMES]
        for feat in FEATURE_NAMES:
            assert np.isfinite(X[feat].values).all(), (
                f"Non-finite values (inf/NaN) found in feature '{feat}'."
            )

    def test_target_values_in_01_range(self, small_dataset):
        """Targets are normalised scores in [0, 1]."""
        for target in TARGET_COLUMNS:
            vals = small_dataset[target]
            assert vals.min() >= 0.0, (
                f"{target} has value below 0.0: {vals.min()}"
            )
            assert vals.max() <= 1.0, (
                f"{target} has value above 1.0: {vals.max()}"
            )

    def test_timestamp_is_monotonically_increasing(self, small_dataset):
        assert small_dataset["timestamp"].is_monotonic_increasing, (
            "Dataset timestamps must be in ascending chronological order."
        )

    def test_timestamps_are_unique(self, small_dataset):
        n_unique = small_dataset["timestamp"].nunique()
        assert n_unique == len(small_dataset), (
            f"Expected {len(small_dataset)} unique timestamps, got {n_unique}. "
            "Duplicate timestamps indicate a bug in dataset_builder."
        )

    def test_binary_flag_columns_are_01(self, small_dataset):
        """regime_1h_* and direction_long and regime_alignment must be 0.0 or 1.0."""
        binary_cols = [
            "regime_1h_bullish", "regime_1h_bearish",
            "regime_1h_ranging", "regime_1h_transitional",
            "regime_alignment", "direction_long",
        ]
        for col in binary_cols:
            unique_vals = set(small_dataset[col].unique())
            assert unique_vals <= {0.0, 1.0}, (
                f"Binary column '{col}' has unexpected values: {unique_vals}. "
                "Must only contain 0.0 and 1.0."
            )

    def test_regime_onehot_mutual_exclusivity(self, small_dataset):
        """At most one of the four regime one-hot columns may be 1.0 per row."""
        regime_cols = [
            "regime_1h_bullish", "regime_1h_bearish",
            "regime_1h_ranging", "regime_1h_transitional",
        ]
        row_sums = small_dataset[regime_cols].sum(axis=1)
        assert (row_sums <= 1.0).all(), (
            "Regime one-hot encoding is not mutually exclusive. "
            f"Rows where sum > 1: {small_dataset[row_sums > 1.0].index.tolist()[:5]}"
        )

    def test_describe_dataset_returns_dataframe(self, small_dataset):
        stats = describe_dataset(small_dataset)
        assert isinstance(stats, pd.DataFrame)
        assert len(stats) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Temporal split validation
# ─────────────────────────────────────────────────────────────────────────────


class TestTemporalSplit:
    """Verifies temporal split integrity for train/val separation."""

    def test_clean_split_passes(self, medium_dataset):
        train_end = int(len(medium_dataset) * 0.80)
        report = validate_temporal_split(medium_dataset, train_end, verbose=False)
        assert report.passed, (
            f"Clean temporal split should pass but got failures:\n{report.summary}"
        )

    def test_train_max_ts_strictly_before_val_min_ts(self, medium_dataset):
        train_end = int(len(medium_dataset) * 0.80)
        train_df = medium_dataset.iloc[:train_end]
        val_df = medium_dataset.iloc[train_end:]
        assert train_df["timestamp"].max() < val_df["timestamp"].min(), (
            "Train split's last timestamp must be strictly before val split's first timestamp."
        )

    def test_split_sizes_sum_to_total(self, medium_dataset):
        train_end = int(len(medium_dataset) * 0.80)
        report = validate_temporal_split(medium_dataset, train_end, verbose=False)
        total = report.stats.get("n_train", 0) + report.stats.get("n_val", 0)
        assert total == len(medium_dataset)

    def test_zero_train_end_idx_fails(self, medium_dataset):
        report = validate_temporal_split(medium_dataset, 0, verbose=False)
        assert not report.passed

    def test_out_of_bounds_train_end_idx_fails(self, medium_dataset):
        report = validate_temporal_split(medium_dataset, len(medium_dataset) + 100, verbose=False)
        assert not report.passed

    def test_report_stats_populated(self, medium_dataset):
        train_end = 1_600
        report = validate_temporal_split(medium_dataset, train_end, verbose=False)
        assert "n_train" in report.stats
        assert "n_val" in report.stats
        assert report.stats["n_train"] == 1_600
        assert report.stats["n_val"] == 400

    def test_shuffled_dataset_fails_monotonic_check(self, medium_dataset):
        """Shuffled (non-chronological) data should be detected."""
        shuffled = medium_dataset.sample(frac=1, random_state=77).reset_index(drop=True)
        report = validate_temporal_split(shuffled, 1_600, verbose=False)
        # Shuffled timestamps will fail monotonic check
        assert not report.passed


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Feature leakage detection
# ─────────────────────────────────────────────────────────────────────────────


class TestLeakageDetection:
    """Verifies the leakage detector correctly identifies clean and tainted data."""

    def test_clean_dataset_passes_leakage_check(self, medium_dataset):
        report = check_feature_leakage(medium_dataset, verbose=False)
        assert report.passed, (
            f"Clean dataset should pass leakage check but got:\n{report.summary}"
        )

    def test_no_nan_in_clean_dataset(self, medium_dataset):
        report = check_feature_leakage(medium_dataset, verbose=False)
        assert report.stats.get("feature_nan_count", 0) == 0

    def test_dataset_with_target_in_feature_matrix_fails(self, medium_dataset):
        """Injecting a target value into a feature column must be detected by high correlation."""
        tainted = medium_dataset.copy()
        # Overwrite bos_strength with the target value — perfect correlation with the target
        tainted["bos_strength"] = tainted["target_pattern_score"]
        report = check_feature_leakage(tainted, verbose=False)
        # Should detect label leakage via the correlation check (corr > 0.98)
        assert not report.passed, (
            "Leakage detector should have caught bos_strength being overwritten with target. "
            f"Report: {report.summary}"
        )
        assert any("leakage" in issue.lower() for issue in report.issues), (
            f"Expected a 'leakage' failure message but got: {report.issues}"
        )


    def test_missing_feature_columns_fail(self):
        """Dataset missing required feature columns must fail."""
        bad_df = pd.DataFrame({"some_random_col": [1.0, 2.0], "target_pattern_score": [0.5, 0.6]})
        report = check_feature_leakage(bad_df, verbose=False)
        assert not report.passed

    def test_run_all_checks_on_clean_dataset(self, medium_dataset):
        """Composite run_all_checks must pass on a cleanly built dataset."""
        train_end = int(len(medium_dataset) * 0.80)
        report = run_all_checks(medium_dataset, train_end_idx=train_end, verbose=False)
        assert report.passed, (
            f"run_all_checks failed on a clean dataset:\n{report.summary}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Pipeline smoke test (no ONNX export — fast unit mode)
# ─────────────────────────────────────────────────────────────────────────────


class TestPipelineSmoke:
    """Runs the pipeline in skip_hygiene + minimal-sample mode for fast CI feedback."""

    def test_pipeline_runs_without_error(self, tmp_path):
        """Pipeline must complete without raising, producing expected output keys."""
        try:
            from quantedge.ai.training.train import run_pipeline
        except ImportError as e:
            pytest.skip(f"Training pipeline not importable (missing deps?): {e}")

        onnx_out = tmp_path / "test_model.onnx"
        try:
            result = run_pipeline(
                n_samples=300,
                seed=7,
                train_split=0.80,
                onnx_output=onnx_out,
                skip_hygiene=True,  # bypass for speed in unit test
            )
        except Exception as e:
            pytest.fail(f"Pipeline raised an unexpected error: {e}")

        assert "n_train" in result
        assert "n_val" in result
        assert "validation_metrics" in result
        assert "onnx_path" in result

    def test_pipeline_output_metrics_structure(self, tmp_path):
        """Validation metrics must have the correct target names and metric keys."""
        try:
            from quantedge.ai.training.train import run_pipeline, TARGET_NAMES
        except ImportError as e:
            pytest.skip(f"Pipeline not importable: {e}")

        onnx_out = tmp_path / "test_model2.onnx"
        try:
            result = run_pipeline(
                n_samples=300,
                seed=8,
                onnx_output=onnx_out,
                skip_hygiene=True,
            )
        except Exception as e:
            pytest.fail(f"Pipeline raised: {e}")

        metrics = result["validation_metrics"]
        for target in TARGET_NAMES:
            assert target in metrics, f"Missing metrics for target '{target}'"
            assert "MAE" in metrics[target]
            assert "R2" in metrics[target]

    def test_onnx_file_is_written(self, tmp_path):
        """ONNX output file must exist after a successful pipeline run."""
        try:
            from quantedge.ai.training.train import run_pipeline
        except ImportError as e:
            pytest.skip(f"Pipeline not importable: {e}")

        onnx_out = tmp_path / "test_model3.onnx"
        try:
            run_pipeline(
                n_samples=300,
                seed=9,
                onnx_output=onnx_out,
                skip_hygiene=True,
            )
        except Exception as e:
            pytest.fail(f"Pipeline raised: {e}")

        assert onnx_out.exists(), (
            f"ONNX file was not written to {onnx_out}."
        )
        assert onnx_out.stat().st_size > 1_000, (
            f"ONNX file is suspiciously small ({onnx_out.stat().st_size} bytes)."
        )
