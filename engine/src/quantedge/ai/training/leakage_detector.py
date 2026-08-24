"""
Phase A & B — Leakage Detection & Purged Chronological Split Validator.

Enforces strict data-hygiene invariants before training:

1. PURGED & EMBARGOED CHRONOLOGICAL SPLITS (NO FORWARD-HORIZON LEAKAGE)
   Splits dataset chronologically into Train (60%), Validation (20%), and
   Final Out-Of-Sample Test (20%).
   Enforces a strict embargo/purge window (>= 72 hours) between splits:
       max(T_train) + 72h <= min(T_val)
       max(T_val)   + 72h <= min(T_test)
   Guarantees zero overlapping forward-replay horizons across boundaries.

2. NO FEATURE LEAKAGE
   Feature columns may not contain any target, metadata, or future-derived information.
   Checks: no column named "target_*" or "meta_*" in feature matrix X, no NaNs,
   and no feature with correlation >= 0.98 with any target column.

3. TEMPORAL STATIONARITY CHECK
   Detects any feature whose lag-1 autocorrelation is suspiciously high (> threshold),
   which could indicate look-ahead smoothing.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES

LEGACY_TARGET_COLUMNS = ["target_pattern_score", "target_signal_score", "target_confidence"]
REAL_TARGET_COLUMNS = ["target_realized_r", "target_mfe_r", "target_mae_r"]
ALL_KNOWN_TARGETS = list(set(LEGACY_TARGET_COLUMNS + REAL_TARGET_COLUMNS))


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
            lines.append("=== FAILURES ===")
            lines.extend(self.issues)
        if self.warnings:
            lines.append("=== WARNINGS ===")
            lines.extend(self.warnings)
        if not lines:
            lines.append("[OK] All data-hygiene and purge checks passed.")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Split Generation with Purge / Embargo
# ─────────────────────────────────────────────────────────────────────────────


def split_purged_chronological(
    df: pd.DataFrame,
    train_ratio: float = 0.60,
    val_ratio: float = 0.20,
    test_ratio: float = 0.20,
    embargo_hours: float = 72.0,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Splits a chronological DataFrame into Train, Validation, and Test sets
    with a mandatory embargo/purge period between consecutive sets.

    Calculates timeline cutoffs based on timestamp duration, then filters out
    any setups within the embargo window immediately preceding the next split.
    """
    if "timestamp" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' column.")

    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    min_ts = df_sorted["timestamp"].min()
    max_ts = df_sorted["timestamp"].max()
    total_duration = max_ts - min_ts

    train_end_target = min_ts + (total_duration * train_ratio)
    val_end_target = min_ts + (total_duration * (train_ratio + val_ratio))

    embargo_delta = timedelta(hours=embargo_hours)

    # 1. Train set: up to train_end_target - embargo_delta
    train_cutoff = train_end_target - embargo_delta
    train_df = df_sorted[df_sorted["timestamp"] <= train_cutoff].copy()

    # 2. Validation set: from train_end_target up to val_end_target - embargo_delta
    val_cutoff = val_end_target - embargo_delta
    val_df = df_sorted[(df_sorted["timestamp"] >= train_end_target) & (df_sorted["timestamp"] <= val_cutoff)].copy()

    # 3. Test set: from val_end_target onward
    test_df = df_sorted[df_sorted["timestamp"] >= val_end_target].copy()

    return train_df, val_df, test_df


# ─────────────────────────────────────────────────────────────────────────────
# Check 1: 3-Way Purged Chronological Split Integrity
# ─────────────────────────────────────────────────────────────────────────────


def validate_purged_chronological_split(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    embargo_hours: float = 72.0,
    report: Optional[DataHygieneReport] = None,
    verbose: bool = True,
) -> DataHygieneReport:
    """
    Verifies that Train, Val, and Test splits obey strict chronological non-overlap
    and maintain at least `embargo_hours` between them.
    """
    if report is None:
        report = DataHygieneReport()

    for name, split in [("Train", train_df), ("Val", val_df), ("Test", test_df)]:
        if split.empty:
            report.fail(f"{name} split is empty.")
            return report
        if "timestamp" not in split.columns:
            report.fail(f"{name} split missing 'timestamp' column.")
            return report
        if not split["timestamp"].is_monotonic_increasing:
            report.fail(f"{name} split timestamps are not monotonically increasing.")

    train_max = train_df["timestamp"].max()
    val_min = val_df["timestamp"].min()
    val_max = val_df["timestamp"].max()
    test_min = test_df["timestamp"].min()

    # 1. Train -> Val embargo
    train_val_gap_hours = (val_min - train_max).total_seconds() / 3600.0
    if train_max >= val_min:
        report.fail(f"Temporal overlap: Train max ({train_max}) >= Val min ({val_min})")
    elif train_val_gap_hours < embargo_hours - 0.01:
        report.fail(
            f"Train->Val purge embargo violated: gap is {train_val_gap_hours:.1f}h "
            f"(required >= {embargo_hours:.1f}h)"
        )
    else:
        report.stats["train_val_embargo_gap_hours"] = round(train_val_gap_hours, 1)

    # 2. Val -> Test embargo
    val_test_gap_hours = (test_min - val_max).total_seconds() / 3600.0
    if val_max >= test_min:
        report.fail(f"Temporal overlap: Val max ({val_max}) >= Test min ({test_min})")
    elif val_test_gap_hours < embargo_hours - 0.01:
        report.fail(
            f"Val->Test purge embargo violated: gap is {val_test_gap_hours:.1f}h "
            f"(required >= {embargo_hours:.1f}h)"
        )
    else:
        report.stats["val_test_embargo_gap_hours"] = round(val_test_gap_hours, 1)

    # 3. Disjoint timestamp set verification
    t_train_set = set(train_df["timestamp"].astype(str))
    t_val_set = set(val_df["timestamp"].astype(str))
    t_test_set = set(test_df["timestamp"].astype(str))

    if t_train_set & t_val_set:
        report.fail(f"Overlapping timestamps between Train and Val: {len(t_train_set & t_val_set)}")
    if t_val_set & t_test_set:
        report.fail(f"Overlapping timestamps between Val and Test: {len(t_val_set & t_test_set)}")
    if t_train_set & t_test_set:
        report.fail(f"Overlapping timestamps between Train and Test: {len(t_train_set & t_test_set)}")

    report.stats["n_train"] = len(train_df)
    report.stats["n_val"] = len(val_df)
    report.stats["n_test"] = len(test_df)
    report.stats["train_range"] = f"{train_df['timestamp'].min()} to {train_max}"
    report.stats["val_range"] = f"{val_min} to {val_max}"
    report.stats["test_range"] = f"{test_min} to {test_df['timestamp'].max()}"

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Check 2: 2-Way Temporal Split (Backward Compatibility)
# ─────────────────────────────────────────────────────────────────────────────


def validate_temporal_split(
    df: pd.DataFrame,
    train_end_idx: int,
    report: Optional[DataHygieneReport] = None,
    verbose: bool = True,
) -> DataHygieneReport:
    """Verifies that 2-way train and validation splits do not overlap temporally."""
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

    if not df["timestamp"].is_monotonic_increasing:
        report.fail("Dataset timestamps are not monotonically increasing.")

    train_max_ts = train_df["timestamp"].max()
    val_min_ts = val_df["timestamp"].min()

    if train_max_ts >= val_min_ts:
        report.fail(
            f"Temporal leakage detected: last training timestamp ({train_max_ts}) "
            f">= first validation timestamp ({val_min_ts})."
        )
    else:
        gap_minutes = (val_min_ts - train_max_ts).total_seconds() / 60.0
        report.stats["temporal_gap_minutes"] = round(gap_minutes, 1)

    train_ts = set(train_df["timestamp"].astype(str))
    val_ts = set(val_df["timestamp"].astype(str))
    overlap = train_ts & val_ts
    if overlap:
        report.fail(f"Timestamp overlap: {len(overlap)} timestamps appear in both train and val.")

    report.stats["n_train"] = len(train_df)
    report.stats["n_val"] = len(val_df)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Check 3: Feature Leakage Detection
# ─────────────────────────────────────────────────────────────────────────────


def check_feature_leakage(
    df: pd.DataFrame,
    report: Optional[DataHygieneReport] = None,
    verbose: bool = True,
) -> DataHygieneReport:
    """
    Detects feature columns that may encode target, metadata, or future information.
    """
    if report is None:
        report = DataHygieneReport()

    missing = [n for n in FEATURE_NAMES if n not in df.columns]
    if missing:
        report.fail(f"Missing {len(missing)} feature columns: {missing}.")
        return report

    X = df[FEATURE_NAMES]

    # No target or metadata columns in feature matrix
    leaked_cols = [c for c in X.columns if c.startswith("target_") or c.startswith("meta_")]
    if leaked_cols:
        report.fail(f"Target/meta columns found in feature matrix: {leaked_cols}.")

    # No NaN in features
    nan_cols = X.columns[X.isnull().any()].tolist()
    if nan_cols:
        report.fail(f"NaN values found in feature columns: {nan_cols}.")
    else:
        report.stats["feature_nan_count"] = 0

    # Correlation with all known target columns
    target_cols_present = [c for c in ALL_KNOWN_TARGETS if c in df.columns]
    if target_cols_present:
        corr_threshold = 0.98
        for target in target_cols_present:
            for feat in FEATURE_NAMES:
                try:
                    corr = abs(float(df[feat].corr(df[target])))
                    if corr > corr_threshold:
                        report.fail(
                            f"Potential label leakage: feature '{feat}' has correlation "
                            f"{corr:.4f} with '{target}' (threshold={corr_threshold})."
                        )
                except Exception:
                    pass

    # Range sanity for normalised features
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
            if isinstance(col, pd.DataFrame):
                col = col.iloc[:, 0]
            fmin = float(col.min())
            fmax = float(col.max())
            if fmin < -0.01 or fmax > 1.01:
                report.warn(
                    f"Feature '{feat}' expected in [0,1] but range is [{fmin:.4f}, {fmax:.4f}]."
                )
        except Exception:
            pass

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Check 4: Temporal Autocorrelation / Stationarity
# ─────────────────────────────────────────────────────────────────────────────


def check_temporal_stationarity(
    df: pd.DataFrame,
    lag: int = 1,
    autocorr_warn_threshold: float = 0.90,
    report: Optional[DataHygieneReport] = None,
) -> DataHygieneReport:
    """Checks for suspiciously high lag-1 autocorrelation."""
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
                        f"(threshold={autocorr_warn_threshold})."
                    )
            except Exception:
                pass

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Composite Runners
# ─────────────────────────────────────────────────────────────────────────────


def run_all_checks(
    df: pd.DataFrame,
    train_end_idx: Optional[int] = None,
    autocorr_warn_threshold: float = 0.90,
    verbose: bool = True,
) -> DataHygieneReport:
    """2-way split composite runner (legacy compatibility)."""
    if train_end_idx is None:
        train_end_idx = int(len(df) * 0.80)

    report = DataHygieneReport()
    validate_temporal_split(df, train_end_idx, report, verbose=verbose)
    check_feature_leakage(df, report, verbose=verbose)
    check_temporal_stationarity(df, autocorr_warn_threshold=autocorr_warn_threshold, report=report)

    if verbose:
        print("\n" + "=" * 60)
        print("  QuantEdge AI — Data Hygiene Report")
        print("=" * 60)
        print(report.summary)
        if report.stats:
            print("\nStats:")
            for k, v in report.stats.items():
                print(f"  {k}: {v}")
        print("=" * 60 + "\n")

    return report


def run_all_purged_checks(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    embargo_hours: float = 72.0,
    autocorr_warn_threshold: float = 0.90,
    verbose: bool = True,
) -> DataHygieneReport:
    """3-way purged chronological split composite runner."""
    report = DataHygieneReport()
    validate_purged_chronological_split(train_df, val_df, test_df, embargo_hours=embargo_hours, report=report, verbose=verbose)
    full_df = pd.concat([train_df, val_df, test_df], axis=0).sort_values("timestamp").reset_index(drop=True)
    check_feature_leakage(full_df, report, verbose=verbose)
    check_temporal_stationarity(full_df, autocorr_warn_threshold=autocorr_warn_threshold, report=report)

    if verbose:
        print("\n" + "=" * 60)
        print("  QuantEdge AI — Purged Chronological Data Hygiene Report")
        print("=" * 60)
        print(report.summary)
        if report.stats:
            print("\nStats:")
            for k, v in report.stats.items():
                print(f"  {k}: {v}")
        print("=" * 60 + "\n")

    return report
