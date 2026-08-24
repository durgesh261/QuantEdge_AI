"""
QuantEdge AI Training Pipeline — Dataset Builder.

Generates a synthetic training dataset from the FeatureContract schema.
Each row is a 24-feature vector with a 3-output target (pattern_score,
signal_score, confidence) all normalised to [0, 1].

═══════════════════════════════════════════════════════════════════════════════
DESIGN PRINCIPLES
═══════════════════════════════════════════════════════════════════════════════
1. Feature-contract fidelity: every column is named by FEATURE_NAMES so the
   ordering is always derivable from the contract, not from column position.
2. Reproducibility: a fixed seed is accepted; set seed=None for stochastic runs.
3. Leakage prevention:
   - No future price information used in feature generation.
   - No look-ahead on targets (targets are constructed from feature geometry
     and regime, not from future close prices).
4. Temporal ordering: rows are assigned synthetic timestamps and sorted
   chronologically so the temporal split validator can enforce a clean cut.

Usage::

    from quantedge.ai.training.dataset_builder import build_training_dataset

    df = build_training_dataset(n_samples=10_000, seed=42)
    df.to_parquet("data/training_v1.parquet")
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import (
    FEATURE_COUNT,
    FEATURE_NAMES,
    REGIME_BEARISH_VARIANTS,
    REGIME_BULLISH_VARIANTS,
    REGIME_RANGING_VARIANTS,
    REGIME_TRANSITIONAL_VARIANTS,
    encode_direction,
    encode_regime_1h,
)

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_ALL_REGIMES = (
    list(REGIME_BULLISH_VARIANTS)
    + list(REGIME_BEARISH_VARIANTS)
    + list(REGIME_RANGING_VARIANTS)
    + list(REGIME_TRANSITIONAL_VARIANTS)
    + ["UNKNOWN"]
)

_DIRECTIONS = ["LONG", "SHORT"]


def _clip01(v: float) -> float:
    """Clips a float to [0, 1]."""
    return max(0.0, min(1.0, v))


def _generate_one_row(rng: random.Random, np_rng: np.random.Generator) -> dict:
    """Generates a single sample row with all 24 features and 3 targets."""

    # ── Group 1: SMC Structural Features ─────────────────────────────────────
    bos = _clip01(np_rng.beta(2, 3))          # Skewed toward moderate values
    choch = _clip01(np_rng.beta(2, 2))
    ob = _clip01(np_rng.beta(3, 2))           # Order blocks tend to be fresh
    fvg = _clip01(np_rng.beta(2, 3))
    liq = _clip01(np_rng.beta(2, 4))          # Close to liquidity is less common

    # ── Group 2: Market Context ───────────────────────────────────────────────
    trend_1h = _clip01(np_rng.beta(2, 2))
    trend_15m = _clip01(np_rng.beta(2, 2))
    trend_4h = _clip01(np_rng.beta(2, 2))
    vol_1h = _clip01(np_rng.beta(2, 5))       # Low volatility is more common
    vol_15m = _clip01(np_rng.beta(2, 5))
    vol_profile = float(np.clip(np_rng.lognormal(0.0, 0.3), 0.0, 2.0))
    mom_1h = float(np.clip(np_rng.normal(0.0, 0.05), -0.5, 0.5))
    mom_15m = float(np.clip(np_rng.normal(0.0, 0.03), -0.5, 0.5))

    # ── Group 3: Geometry ─────────────────────────────────────────────────────
    rr = float(np.clip(np_rng.gamma(3.0, 1.0), 0.5, 8.0))   # RR 1–8 typical
    risk_dist = float(np.clip(np_rng.lognormal(5.0, 0.5), 10.0, 2000.0))
    entry_prec = _clip01(np_rng.beta(5, 2))   # High precision setups more common

    # ── Group 4: Account Context ──────────────────────────────────────────────
    acc_util = _clip01(np_rng.beta(2, 6))     # Low utilisation skew
    lev_ratio = _clip01(np_rng.beta(2, 8))    # Low leverage skew

    # ── Group 5: Regime one-hot ───────────────────────────────────────────────
    regime = rng.choice(_ALL_REGIMES)
    regime_oh = encode_regime_1h(regime)

    # ── Group 6: Binary flags ─────────────────────────────────────────────────
    regime_align = rng.random() > 0.4          # 60% aligned
    direction = rng.choice(_DIRECTIONS)

    features = (
        [bos, choch, ob, fvg, liq]            # 0–4
        + [trend_1h, trend_15m, trend_4h,     # 5–12
           vol_1h, vol_15m, vol_profile, mom_1h, mom_15m]
        + [rr, risk_dist, entry_prec]          # 13–15
        + [acc_util, lev_ratio]                # 16–17
        + regime_oh                            # 18–21
        + [1.0 if regime_align else 0.0,       # 22
           encode_direction(direction)]        # 23
    )

    assert len(features) == FEATURE_COUNT, (
        f"Feature generation produced {len(features)} values, expected {FEATURE_COUNT}"
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Target generation (label engineering)
    # Targets are derived *only* from the features above — no future data.
    # pattern_score ∈ [0,1], signal_score ∈ [0,1], confidence ∈ [0,1]
    # ─────────────────────────────────────────────────────────────────────────

    # pattern_score: weighted combination of SMC quality and trend alignment
    smc_quality = (bos * 0.3 + choch * 0.25 + ob * 0.25 + fvg * 0.1 + liq * 0.1)
    trend_quality = (trend_1h * 0.4 + trend_15m * 0.3 + trend_4h * 0.3)
    raw_pattern = smc_quality * 0.6 + trend_quality * 0.4
    # Regime boost: bullish/bearish trend regimes increase pattern quality
    if regime_oh[0] == 1.0 or regime_oh[1] == 1.0:   # bullish or bearish
        raw_pattern = min(1.0, raw_pattern * 1.15)
    elif regime_oh[2] == 1.0:                          # ranging
        raw_pattern = raw_pattern * 0.85
    pattern_score = _clip01(raw_pattern + np_rng.normal(0.0, 0.03))

    # signal_score: pattern + RR + entry precision
    rr_norm = _clip01(rr / 6.0)                        # RR 6.0 → 1.0
    raw_signal = pattern_score * 0.5 + rr_norm * 0.3 + entry_prec * 0.2
    signal_score = _clip01(raw_signal + np_rng.normal(0.0, 0.03))

    # confidence: signal + regime alignment + volume + low volatility
    low_vol_bonus = _clip01(1.0 - vol_1h) * 0.1
    raw_conf = (
        signal_score * 0.4
        + (1.0 if regime_align else 0.0) * 0.2
        + vol_profile * 0.1
        + low_vol_bonus
        + trend_quality * 0.2
    )
    confidence = _clip01(raw_conf + np_rng.normal(0.0, 0.03))

    row = dict(zip(FEATURE_NAMES, features))
    row["target_pattern_score"] = round(pattern_score, 6)
    row["target_signal_score"] = round(signal_score, 6)
    row["target_confidence"] = round(confidence, 6)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_training_dataset(
    n_samples: int = 50_000,
    seed: Optional[int] = 42,
    start_date: datetime = datetime(2024, 1, 1, tzinfo=timezone.utc),
    candle_interval_minutes: int = 15,
) -> pd.DataFrame:
    """
    Builds a labelled training dataset with n_samples rows.

    Each row contains:
    - 24 feature columns named by FEATURE_NAMES (contract-compliant).
    - 3 target columns: target_pattern_score, target_signal_score, target_confidence.
    - A synthetic timestamp column (UTC, 15-min cadence by default).

    Args:
        n_samples: Number of training samples to generate.
        seed: RNG seed for reproducibility. Use None for stochastic.
        start_date: Synthetic start timestamp for temporal ordering.
        candle_interval_minutes: Spacing between synthetic candle timestamps.

    Returns:
        pd.DataFrame with shape (n_samples, 24 + 3 + 1).
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    rows = [_generate_one_row(rng, np_rng) for _ in range(n_samples)]
    df = pd.DataFrame(rows)

    # Assign synthetic timestamps (strictly monotonic, chronological)
    timestamps = [
        start_date + timedelta(minutes=i * candle_interval_minutes)
        for i in range(n_samples)
    ]
    df.insert(0, "timestamp", pd.to_datetime(timestamps, utc=True))

    # Validate column order matches contract
    feature_cols = list(df.columns[1:25])   # skip timestamp, stop at 25th col
    assert feature_cols == FEATURE_NAMES, (
        f"Column order mismatch!\n"
        f"Expected: {FEATURE_NAMES}\n"
        f"Got:      {feature_cols}"
    )

    return df


def describe_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Returns summary statistics for the feature and target columns."""
    cols = FEATURE_NAMES + ["target_pattern_score", "target_signal_score", "target_confidence"]
    return df[cols].describe().round(4)
