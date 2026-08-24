"""
Phase A — Leakage Detection & Temporal Split Validator.

Enforces three data-hygiene invariants before training:

1. NO TEMPORAL LEAKAGE
   The training split must end strictly before the validation split starts.
   There must be zero rows where the timestamp appears in both splits.

2. NO FEATURE LEAKAGE
   Feature columns may not contain any target or future-derived information.
   Checks: no column named "target_*" in X, no NaN in feature matrix,
   no column that is a deterministic function of another target column.

3. TEMPORAL CORRELATION CHECK
   Detects any feature whose correlation with its own future value (1-step lag)
   is suspiciously high (> threshold), which would indicate look-ahead bias.

Usage::

    from quantedge.ai.training.leakage_detector import (
        validate_temporal_split,
        check_feature_leakage,
        check_temporal_stationarity,
        run_all_checks,
    )

    report = run_all_checks(df, train_end_idx=40_000)
    if not report.passed:
        raise RuntimeError(f"Data hygiene failed:\\n{report.summary}")
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES

TARGET_COLUMNS = ["target_pattern_score", "target_signal_score", "target_confidence"]


@dataclass
class DataHygieneReport:
    """Aggregated result of all leakage and split validation checks."""
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.passed = False
        self.issues.append(f"[FAIL] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(f"[WARN] {message}")

    @property
    def summary(self) -> str:
        lines = []
        if self.issues:
            lines.append("═══ FAILURES ═══")
            lines.extend(self.issues)
        if self.warnings:
            lines.append("═══ WARNINGS ═══")
            lines.extend(self.warnings)
        if not lines:
            lines.append("[OK] All data-hygiene checks passed.")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Check 1: Temporal split integrity
# ─────────────────────────────────────────────────────────────────────────────

def validate_temporal_split(
    df: pd.DataFrame,
    train_end_idx: int,
    report: Optional[DataHygieneReport] = None,
    verbose: bool = True,
) -> DataHygieneReport:
    """
    Verifies that train and validation splits do not overlap temporally.

    Args:
        df: Full dataset with 'timestamp' column, sorted chronologically.
        train_end_idx: Last row index (exclusive) of the training set.
        report: Optional existing report to append findings to.
        verbose: If True, prints findings to stdout.

    Returns:
        DataHygieneReport with temporal split findings.
    """
    if report is None:
        report = DataHygieneReport()

    if "timestamp" not in df.columns:
        report.fail("Dataset has no 'timestamp' column — temporal split cannot be validated.")
        return report

    if len(df) < 10:
        report.fail(f"Dataset too small ({len(df)} rows) for meaningful temporal split.")
        return report

    if train_end_idx <= 0 or train_end_idx >= len(df):
        report.fail(
            f"train_end_idx={train_end_idx} out of bounds for dataset of {len(df)} rows. "
            f"Must be in range [1, {len(df) - 1}]."
        )
        return report

    train_df = df.iloc[:train_end_idx]
    val_df = df.iloc[train_end_idx:]

    # Verify temporal ordering
    if not df["timestamp"].is_monotonic_increasing:
        report.fail(
            "Dataset timestamps are not monotonically increasing. "
            "Sort by timestamp before splitting."
        )

    train_max_ts = train_df["timestamp"].max()
    val_min_ts = val_df["timestamp"].min()

    if train_max_ts >= val_min_ts:
        report.fail(
            f"Temporal leakage detected: last training timestamp ({train_max_ts}) "
            f">= first validation timestamp ({val_min_ts}). "
            "Ensure strict chronological split."
        )
    else:
        gap_minutes = (val_min_ts - train_max_ts).total_seconds() / 60
        report.stats["temporal_gap_minutes"] = round(gap_minutes, 1)
        if gap_minutes < 15:
            report.warn(
                f"Temporal gap between train and val splits is only {gap_minutes:.1f} min. "
                "Consider a larger gap (>= 1 day) to avoid autocorrelation contamination."
            )

    # Check for timestamp overlaps (should not happen with iloc splits)
    train_ts = set(train_df["timestamp"].astype(str))
    val_ts = set(val_df["timestamp"].astype(str))
    overlap = train_ts & val_ts
    if overlap:
        report.fail(
            f"Timestamp overlap: {len(overlap)} timestamps appear in both train and val. "
            f"Example: {list(overlap)[:3]}"
        )

    report.stats["n_train"] = len(train_df)
    report.stats["n_val"] = len(val_df)
    report.stats["train_start"] = str(train_df["timestamp"].min())
    report.stats["train_end"] = str(train_max_ts)
    report.stats["val_start"] = str(val_min_ts)
    report.stats["val_end"] = str(val_df["timestamp"].max())
    report.stats["train_pct"] = round(100.0 * len(train_df) / len(df), 1)

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Check 2: Feature leakage detection
# ─────────────────────────────────────────────────────────────────────────────

def check_feature_leakage(
    df: pd.DataFrame,
    report: Optional[DataHygieneReport] = None,
    verbose: bool = True,
) -> DataHygieneReport:
    """
    Detects feature columns that may encode target or future information.

    Checks:
    - Feature matrix X contains no column named 'target_*'.
    - Feature matrix X contains no NaN (would indicate missing market data).
    - No feature has correlation > 0.98 with any target column (indicates label bleed).
    - All feature columns from FEATURE_NAMES are present.

    Args:
        df: Full dataset including both features and target columns.
        report: Optional existing report to append findings to.
    """
    if report is None:
        report = DataHygieneReport()

    # Verify all 24 contract columns are present
    missing = [n for n in FEATURE_NAMES if n not in df.columns]
    if missing:
        report.fail(
            f"Missing {len(missing)} feature columns: {missing}. "
            "Dataset was not built with dataset_builder.build_training_dataset()."
        )
        return report

    X = df[FEATURE_NAMES]

    # No target columns in feature matrix
    leaked_target_cols = [c for c in X.columns if c.startswith("target_")]
    if leaked_target_cols:
        report.fail(
            f"Target columns found in feature matrix: {leaked_target_cols}. "
            "Remove target columns from X before training."
        )

    # No NaN in features
    nan_cols = X.columns[X.isnull().any()].tolist()
    if nan_cols:
        report.fail(
            f"NaN values found in feature columns: {nan_cols}. "
            "Impute or drop rows before training."
        )
    else:
        report.stats["feature_nan_count"] = 0

    # Correlation with targets — high correlation is suspicious
    target_cols_present = [c for c in TARGET_COLUMNS if c in df.columns]
    if target_cols_present:
        corr_threshold = 0.98
        for target in target_cols_present:
            for feat in FEATURE_NAMES:
                try:
                    corr = abs(df[feat].corr(df[target]))
                    if corr > corr_threshold:
                        report.fail(
                            f"Potential label leakage: feature '{feat}' has correlation "
                            f"{corr:.4f} with '{target}' (threshold={corr_threshold}). "
                            "Verify this feature is not derived from the target."
                        )
                except Exception:
                    pass  # Non-numeric or constant column

    # Feature value range sanity for normalised features
    NORMALISED_01 = FEATURE_NAMES[:5] + FEATURE_NAMES[5:10] + [
        "entry_precision", "account_utilization", "leverage_ratio",
        "regime_1h_bullish", "regime_1h_bearish", "regime_1h_ranging",
        "regime_1h_transitional", "regime_alignment", "direction_long",
    ]
    for feat in NORMALISED_01:
        if feat not in df.columns:
            continue
        try:
            col = df[feat]
            # Guard against duplicate column names returning a DataFrame
            if isinstance(col, pd.DataFrame):
                col = col.iloc[:, 0]
            fmin = float(col.min())
            fmax = float(col.max())
            if fmin < -0.01 or fmax > 1.01:
                report.warn(
                    f"Feature '{feat}' expected in [0,1] but range is [{fmin:.4f}, {fmax:.4f}]. "
                    "Check normalisation in dataset_builder."
                )
        except Exception:
            pass  # Non-numeric column or other edge case

    return report



# ─────────────────────────────────────────────────────────────────────────────
# Check 3: Temporal autocorrelation / stationarity
# ─────────────────────────────────────────────────────────────────────────────

def check_temporal_stationarity(
    df: pd.DataFrame,
    lag: int = 1,
    autocorr_warn_threshold: float = 0.90,
    report: Optional[DataHygieneReport] = None,
) -> DataHygieneReport:
    """
    Checks whether any feature exhibits suspiciously high lag-1 autocorrelation,
    which could indicate that future information is being encoded.

    Note: High autocorrelation per se is NOT leakage for slow-moving features
    (e.g., trend_strength). This check is conservative — it warns, not fails.

    Args:
        df: Dataset sorted chronologically.
        lag: Autocorrelation lag to check (default = 1).
        autocorr_warn_threshold: Warn if autocorr > this value.
        report: Optional existing report to append findings to.
    """
    if report is None:
        report = DataHygieneReport()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for feat in FEATURE_NAMES:
            if feat not in df.columns:
                continue
            series = df[feat].dropna()
            if len(series) < 50:
                continue
            try:
                ac = series.autocorr(lag=lag)
                if ac is not None and not np.isnan(ac) and abs(ac) > autocorr_warn_threshold:
                    report.warn(
                        f"Feature '{feat}' has lag-{lag} autocorrelation {ac:.3f} "
                        f"(threshold={autocorr_warn_threshold}). "
                        "Verify this is a slowly-moving market signal, not look-ahead."
                    )
            except Exception:
                pass

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Composite runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_checks(
    df: pd.DataFrame,
    train_end_idx: Optional[int] = None,
    autocorr_warn_threshold: float = 0.90,
    verbose: bool = True,
) -> DataHygieneReport:
    """
    Runs all three data-hygiene checks and returns a consolidated report.

    Args:
        df: Full labelled dataset (features + targets + timestamp).
        train_end_idx: Index at which to split train/val. Defaults to 80% of data.
        autocorr_warn_threshold: Threshold for autocorrelation warnings.
        verbose: If True, prints the summary to stdout.

    Returns:
        DataHygieneReport. Check report.passed before proceeding to training.
    """
    if train_end_idx is None:
        train_end_idx = int(len(df) * 0.80)

    report = DataHygieneReport()
    validate_temporal_split(df, train_end_idx, report)
    check_feature_leakage(df, report)
    check_temporal_stationarity(df, autocorr_warn_threshold=autocorr_warn_threshold, report=report)

    if verbose:
        print("\n" + "═" * 60)
        print("  QuantEdge AI — Data Hygiene Report")
        print("═" * 60)
        print(report.summary)
        if report.stats:
            print("\nStats:")
            for k, v in report.stats.items():
                print(f"  {k}: {v}")
        print("═" * 60 + "\n")

    return report
