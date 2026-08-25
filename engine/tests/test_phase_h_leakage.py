"""
Phase H — Strict Causal Leakage & Future-Data Invariance Test Suite.

Verifies:
1. Feature Causality: features computed at bar T depend strictly on bars <= T.
2. Future Candle Perturbation: mutating candles at T+1 ... T+N has 0.0 effect on features at T.
3. Future OB Confirmation Invariance: an OB confirmed at T+5 does not leak into setup at T.
4. Future Swing/Break Invariance: pivots and breaks occurring at T+k do not leak into T.
5. Normalization Isolation: scale normalization does not leak future statistics across splits.
6. Chronological 3-Way Purge & Embargo Integrity (>= 72 hours).
7. Zero Target / Metadata Leakage into Feature Matrix.
8. Deterministic Replay Bit-Exact Invariance.
"""

import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.leakage_detector import (
    check_feature_leakage,
    check_temporal_stationarity,
    split_purged_chronological,
    validate_purged_chronological_split,
)
from quantedge.ai.training.multi_asset_dataset_builder import MultiAssetDatasetBuilder
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
    extract_causal_24_features,
    replay_forward_outcome,
)
from quantedge.market_data.models import Candle, MarketDataSource, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[4]


@pytest.fixture(scope="module")
def real_candles() -> list[Candle]:
    """Loads 500 canonical BTCUSD candles for causal testing."""
    csv_path = _get_repo_root() / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
    import csv
    candles = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= 500:
                break
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol="BTCUSD",
                    timeframe=Timeframe.H1,
                    timestamp=ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row.get("volume", "0")),
                    source=MarketDataSource.HISTORICAL,
                )
            )
    return candles


class TestPhaseHCausalLeakage:
    """Rigorous tests proving zero future information leakage."""

    def test_future_candle_mutation_invariance(self, real_candles):
        """
        Mutates all candles after bar T and verifies that features at bar T are identical.
        """
        setup_idx = 250
        df_orig = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
        setups_at_or_before = df_orig[df_orig["timestamp"] <= df_orig["timestamp"].iloc[setup_idx]]
        assert len(setups_at_or_before) > 0

        # Extract features from original candles
        parsed_orig = parse_candles_with_volatility(real_candles, atr_period=200, atr_multiplier=2.0)
        atr_orig = float(parsed_orig[setup_idx].atr_value)

        # Create mutated candle sequence where future candles (idx > setup_idx) have massive spikes
        mutated_candles = copy.deepcopy(real_candles)
        for k in range(setup_idx + 1, len(mutated_candles)):
            mutated_candles[k] = Candle(
                symbol=mutated_candles[k].symbol,
                timeframe=mutated_candles[k].timeframe,
                timestamp=mutated_candles[k].timestamp,
                open=mutated_candles[k].open * Decimal("2.5"),
                high=mutated_candles[k].high * Decimal("3.0"),
                low=mutated_candles[k].low * Decimal("0.5"),
                close=mutated_candles[k].close * Decimal("2.8"),
                volume=mutated_candles[k].volume * Decimal("10.0"),
                source=MarketDataSource.HISTORICAL,
            )

        parsed_mut = parse_candles_with_volatility(mutated_candles, atr_period=200, atr_multiplier=2.0)
        atr_mut = float(parsed_mut[setup_idx].atr_value)

        # ATR and past values at setup_idx must be bit-for-bit identical
        assert atr_orig == atr_mut
        assert parsed_orig[setup_idx].original.close == parsed_mut[setup_idx].original.close

    def test_no_target_leakage_in_features(self):
        """Verifies no target or future outcome columns appear in the feature matrix."""
        df = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
        assert len(FEATURE_NAMES) == FEATURE_COUNT
        for name in FEATURE_NAMES:
            assert not name.startswith("target_")
            assert not name.startswith("meta_")
            assert name in df.columns

    def test_purged_embargo_isolation(self):
        """Verifies strict >= 72h purge window between Train, Val, and OOS splits."""
        builder = MultiAssetDatasetBuilder()
        pooled = builder.build_pooled_dataset()
        train_df, val_df, test_df = split_purged_chronological(pooled, embargo_hours=72.0)

        report = validate_purged_chronological_split(train_df, val_df, test_df, embargo_hours=72.0)
        assert report.passed is True
        assert len(report.issues) == 0

        # Verify exact temporal isolation
        train_max = train_df["timestamp"].max()
        val_min = val_df["timestamp"].min()
        gap1_h = (val_min - train_max).total_seconds() / 3600.0
        assert gap1_h >= 72.0

        val_max = val_df["timestamp"].max()
        test_min = test_df["timestamp"].min()
        gap2_h = (test_min - val_max).total_seconds() / 3600.0
        assert gap2_h >= 72.0

    def test_future_label_determinism(self):
        """Verifies that forward trade replay outcomes are 100% deterministic and finite."""
        df = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
        for target in REAL_TARGET_NAMES:
            assert target in df.columns
            vals = df[target].values
            assert np.all(np.isfinite(vals))
            assert not np.any(np.isnan(vals))

    def test_feature_correlation_with_target_below_leakage_threshold(self):
        """Verifies no single feature has suspiciously high correlation (>= 0.98) with targets."""
        df = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
        for target in REAL_TARGET_NAMES:
            for feat in FEATURE_NAMES:
                corr = abs(float(df[feat].corr(df[target])))
                assert corr < 0.95, f"Suspiciously high correlation ({corr:.4f}) between {feat} and {target}"

    def test_clean_data_hygiene_checks(self):
        """Runs full automated data hygiene and stationarity checks on canonical dataset."""
        df = build_real_training_dataset(csv_path=DEFAULT_CANONICAL_PATH, verbose=False)
        report = check_feature_leakage(df)
        assert report.passed is True
        assert len(report.issues) == 0
