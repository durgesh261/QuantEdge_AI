"""
Canonical 24-Feature Contract for the QuantEdge AI ONNX model.

═══════════════════════════════════════════════════════════════════════════════
AUTHORITY
═══════════════════════════════════════════════════════════════════════════════
This module is the Python-side source of truth for the feature vector shape
fed into the ONNX model.  The Java counterpart is:

    backend/src/main/java/com/quantedge/ai/contract/FeatureContract.java

FEATURE_NAMES[i] names the feature at index i in every numpy / tensor row.
Any change here MUST be mirrored in FeatureContract.java and vice versa.
The mandatory test (tests/test_feature_parity.py) enforces this at CI time.

═══════════════════════════════════════════════════════════════════════════════
FEATURE GROUPS
═══════════════════════════════════════════════════════════════════════════════
 Indices  Group                                 Count
 ───────  ────────────────────────────────────   ─────
  0 –  4  SMC Structural Features                    5
  5 – 12  Market Context Features                    8
 13 – 15  Setup Geometry Features                    3
 16 – 17  Account & Risk Context                     2
 18 – 21  1H Regime One-Hot Encoding                 4
 22 – 23  Binary Flags                               2
 ───────  ────────────────────────────────────   ─────
                                         Total      24

═══════════════════════════════════════════════════════════════════════════════
ONE-HOT REGIME ENCODING  (indices 18 – 21)
═══════════════════════════════════════════════════════════════════════════════
 Java regime1h string              Python REGIME_BULLISH_VARIANTS etc.
 "TRENDING_BULLISH"        →  idx 18 = 1.0  (bullish)
 "BULLISH_TRENDING"        →  idx 18 = 1.0  (alias)
 "STRONG_BULLISH_TREND"    →  idx 18 = 1.0  (alias)
 "TRENDING_BEARISH"        →  idx 19 = 1.0  (bearish)
 "BEARISH_TRENDING"        →  idx 19 = 1.0  (alias)
 "STRONG_BEARISH_TREND"    →  idx 19 = 1.0  (alias)
 "RANGING" / "CLEAR_RANGE" →  idx 20 = 1.0  (ranging)
 "TRANSITIONAL" / "CONFLICTING_TIMEFRAMES" → idx 21 = 1.0  (transitional)
 anything else             →  all four = 0.0 (unknown)
"""

from __future__ import annotations

from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Core contract constants
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COUNT: Final[int] = 24

FEATURE_NAMES: Final[list[str]] = [
    # ── Group 1: SMC Structural Features (indices 0 – 4) ─────────────────────
    "bos_strength",           #  0  Break-of-structure magnitude, normalised to [0, 1]
    "choch_strength",         #  1  Change-of-character confidence, normalised to [0, 1]
    "order_block_strength",   #  2  Order-block mitigation freshness, normalised to [0, 1]
    "fvg_strength",           #  3  Fair-value-gap fill proximity, normalised to [0, 1]
    "liquidity_proximity",    #  4  Distance to nearest liquidity pool, normalised to [0, 1]

    # ── Group 2: Market Context Features (indices 5 – 12) ────────────────────
    "trend_strength_1h",      #  5  EMA-slope trend strength on 1H timeframe, [0, 1]
    "trend_strength_15m",     #  6  EMA-slope trend strength on 15M timeframe, [0, 1]
    "trend_strength_4h",      #  7  EMA-slope trend strength on 4H timeframe, [0, 1]
    "volatility_1h",          #  8  ATR-normalised volatility on 1H, [0, 1]
    "volatility_15m",         #  9  ATR-normalised volatility on 15M, [0, 1]
    "volume_profile",         # 10  Recent / historical volume ratio, clipped to [0, 2]
    "momentum_1h",            # 11  10-period rate-of-change on 1H (unbounded, small)
    "momentum_15m",           # 12  10-period rate-of-change on 15M (unbounded, small)

    # ── Group 3: Setup Geometry Features (indices 13 – 15) ───────────────────
    "risk_reward",            # 13  Raw risk/reward ratio (e.g. 2.0, 3.0)
    "risk_distance",          # 14  Stop-distance in price units (e.g. 150.0)
    "entry_precision",        # 15  Entry proximity to OB/FVG, normalised to [0, 1]

    # ── Group 4: Account & Risk Context (indices 16 – 17) ────────────────────
    "account_utilization",    # 16  Margin-used / total-equity, normalised to [0, 1]
    "leverage_ratio",         # 17  Setup leverage / 100 (max-leverage), normalised to [0, 1]

    # ── Group 5: 1H Regime One-Hot Encoding (indices 18 – 21) ────────────────
    "regime_1h_bullish",      # 18  1.0 if regime1h is bullish
    "regime_1h_bearish",      # 19  1.0 if regime1h is bearish
    "regime_1h_ranging",      # 20  1.0 if regime1h is ranging
    "regime_1h_transitional", # 21  1.0 if regime1h is transitional

    # ── Group 6: Binary Flags (indices 22 – 23) ──────────────────────────────
    "regime_alignment",       # 22  1.0 if 1H / 15M / 4H regimes all agree
    "direction_long",         # 23  1.0 if setup direction is LONG or BUY
]

# ─────────────────────────────────────────────────────────────────────────────
# Regime encoding variant tables — must mirror AiFeatureVector encoding helpers
# ─────────────────────────────────────────────────────────────────────────────

REGIME_BULLISH_VARIANTS: Final[frozenset[str]] = frozenset({
    "TRENDING_BULLISH",
    "BULLISH_TRENDING",
    "STRONG_BULLISH_TREND",
})

REGIME_BEARISH_VARIANTS: Final[frozenset[str]] = frozenset({
    "TRENDING_BEARISH",
    "BEARISH_TRENDING",
    "STRONG_BEARISH_TREND",
})

REGIME_RANGING_VARIANTS: Final[frozenset[str]] = frozenset({
    "RANGING",
    "CLEAR_RANGE",
})

REGIME_TRANSITIONAL_VARIANTS: Final[frozenset[str]] = frozenset({
    "TRANSITIONAL",
    "CONFLICTING_TIMEFRAMES",
})

DIRECTION_LONG_VARIANTS: Final[frozenset[str]] = frozenset({
    "LONG",
    "BUY",
})

# ─────────────────────────────────────────────────────────────────────────────
# Invariant enforcement — runs once at module import
# ─────────────────────────────────────────────────────────────────────────────

assert len(FEATURE_NAMES) == FEATURE_COUNT, (
    f"FeatureContract invariant violated: "
    f"len(FEATURE_NAMES)={len(FEATURE_NAMES)} but FEATURE_COUNT={FEATURE_COUNT}. "
    "Update one or the other."
)

assert len(set(FEATURE_NAMES)) == FEATURE_COUNT, (
    "FeatureContract invariant violated: duplicate feature names detected. "
    f"Duplicates: {[n for n in FEATURE_NAMES if FEATURE_NAMES.count(n) > 1]}"
)

assert all(n and n == n.strip() for n in FEATURE_NAMES), (
    "FeatureContract invariant violated: blank or whitespace-padded feature name detected."
)

# ─────────────────────────────────────────────────────────────────────────────
# Encoding utility — used by training pipeline and dataset builder
# ─────────────────────────────────────────────────────────────────────────────

def encode_regime_1h(regime: str) -> list[float]:
    """
    Encodes a 1H regime string into a 4-element one-hot list.

    Returns [bullish, bearish, ranging, transitional] — exactly one is 1.0 for
    a known regime; all zeros for UNKNOWN or any unrecognised string.

    This must produce identical results to AiFeatureVector.isBullish/isBearish/
    isRanging/isTransitional in Java.

    >>> encode_regime_1h("TRENDING_BULLISH")
    [1.0, 0.0, 0.0, 0.0]
    >>> encode_regime_1h("CLEAR_RANGE")
    [0.0, 0.0, 1.0, 0.0]
    >>> encode_regime_1h("UNKNOWN")
    [0.0, 0.0, 0.0, 0.0]
    """
    return [
        1.0 if regime in REGIME_BULLISH_VARIANTS else 0.0,
        1.0 if regime in REGIME_BEARISH_VARIANTS else 0.0,
        1.0 if regime in REGIME_RANGING_VARIANTS else 0.0,
        1.0 if regime in REGIME_TRANSITIONAL_VARIANTS else 0.0,
    ]


def encode_direction(direction: str) -> float:
    """
    Encodes a direction string to direction_long (1.0 = LONG/BUY, 0.0 = SHORT/SELL).

    Must match AiFeatureVector.isLong() in Java.

    >>> encode_direction("LONG")
    1.0
    >>> encode_direction("BUY")
    1.0
    >>> encode_direction("SHORT")
    0.0
    """
    return 1.0 if direction.upper() in DIRECTION_LONG_VARIANTS else 0.0


def feature_index(name: str) -> int:
    """Returns the index of a named feature, or raises ValueError if unknown."""
    try:
        return FEATURE_NAMES.index(name)
    except ValueError:
        raise ValueError(
            f"Unknown feature '{name}'. Valid names: {FEATURE_NAMES}"
        ) from None
