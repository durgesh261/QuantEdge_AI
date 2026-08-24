"""
Phase A: Feature Contract Parity Tests — Python side.

MANDATORY — never skip, never disable with pytest.mark.skip or @pytest.mark.xfail.

Verifies that engine/src/quantedge/ai/feature_contract.py is internally consistent
and structurally identical to the Java FeatureContract.java canonical 24-feature list.

These tests are the Python counterpart to the Java FeatureParityTest.java.
"""

import pytest
from quantedge.ai.feature_contract import (
    FEATURE_COUNT,
    FEATURE_NAMES,
    REGIME_BEARISH_VARIANTS,
    REGIME_BULLISH_VARIANTS,
    REGIME_RANGING_VARIANTS,
    REGIME_TRANSITIONAL_VARIANTS,
    encode_direction,
    encode_regime_1h,
    feature_index,
)

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Structural invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestContractInvariants:
    """Structural invariants that mirror FeatureContract.java static initialiser."""

    def test_feature_count_is_24(self):
        """FEATURE_COUNT must be exactly 24 — matches ONNX model input shape [1, 24]."""
        assert FEATURE_COUNT == 24, (
            f"FEATURE_COUNT={FEATURE_COUNT} but expected 24. "
            "Update feature_contract.py AND FeatureContract.java together."
        )

    def test_feature_names_length_equals_feature_count(self):
        """len(FEATURE_NAMES) must equal FEATURE_COUNT."""
        assert len(FEATURE_NAMES) == FEATURE_COUNT, (
            f"len(FEATURE_NAMES)={len(FEATURE_NAMES)} != FEATURE_COUNT={FEATURE_COUNT}"
        )

    def test_feature_names_are_unique(self):
        """No duplicate feature names."""
        dupes = [n for n in FEATURE_NAMES if FEATURE_NAMES.count(n) > 1]
        assert len(dupes) == 0, f"Duplicate feature names: {dupes}"

    def test_feature_names_are_non_empty(self):
        """All feature names must be non-empty and non-whitespace."""
        for i, name in enumerate(FEATURE_NAMES):
            assert name and name.strip(), (
                f"Feature at index {i} is blank or None: {name!r}"
            )

    def test_feature_names_are_snake_case(self):
        """All feature names must match lower_snake_case convention."""
        import re
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for name in FEATURE_NAMES:
            assert pattern.match(name), (
                f"Feature name '{name}' is not lower_snake_case. "
                "Check FeatureContract.java for the canonical form."
            )

    def test_feature_names_match_java_canonical_list(self):
        """
        Bit-exact comparison against the canonical 24-feature list from FeatureContract.java.

        If this test fails, update feature_contract.py OR FeatureContract.java
        — both must always be identical.
        """
        JAVA_CANONICAL = [
            # Group 1: SMC (0–4)
            "bos_strength",
            "choch_strength",
            "order_block_strength",
            "fvg_strength",
            "liquidity_proximity",
            # Group 2: Market Context (5–12)
            "trend_strength_1h",
            "trend_strength_15m",
            "trend_strength_4h",
            "volatility_1h",
            "volatility_15m",
            "volume_profile",
            "momentum_1h",
            "momentum_15m",
            # Group 3: Geometry (13–15)
            "risk_reward",
            "risk_distance",
            "entry_precision",
            # Group 4: Account (16–17)
            "account_utilization",
            "leverage_ratio",
            # Group 5: Regime one-hot (18–21)
            "regime_1h_bullish",
            "regime_1h_bearish",
            "regime_1h_ranging",
            "regime_1h_transitional",
            # Group 6: Binary flags (22–23)
            "regime_alignment",
            "direction_long",
        ]
        assert FEATURE_NAMES == JAVA_CANONICAL, (
            "FEATURE_NAMES does not match the Java FeatureContract.FEATURE_NAMES.\n"
            f"Python: {FEATURE_NAMES}\n"
            f"Java:   {JAVA_CANONICAL}\n"
            "Both files must be updated together."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Group offset spot-checks
# ─────────────────────────────────────────────────────────────────────────────


class TestGroupOffsets:
    """Verify named features are at the documented group indices."""

    def test_smc_group_boundaries(self):
        assert FEATURE_NAMES[0] == "bos_strength"
        assert FEATURE_NAMES[4] == "liquidity_proximity"

    def test_market_context_group_boundaries(self):
        assert FEATURE_NAMES[5] == "trend_strength_1h"
        assert FEATURE_NAMES[12] == "momentum_15m"

    def test_geometry_group_boundaries(self):
        assert FEATURE_NAMES[13] == "risk_reward"
        assert FEATURE_NAMES[15] == "entry_precision"

    def test_account_group_boundaries(self):
        assert FEATURE_NAMES[16] == "account_utilization"
        assert FEATURE_NAMES[17] == "leverage_ratio"

    def test_regime_onehot_group_boundaries(self):
        assert FEATURE_NAMES[18] == "regime_1h_bullish"
        assert FEATURE_NAMES[19] == "regime_1h_bearish"
        assert FEATURE_NAMES[20] == "regime_1h_ranging"
        assert FEATURE_NAMES[21] == "regime_1h_transitional"

    def test_binary_flag_group_boundaries(self):
        assert FEATURE_NAMES[22] == "regime_alignment"
        assert FEATURE_NAMES[23] == "direction_long"

    def test_feature_index_lookup(self):
        """feature_index() returns correct positions."""
        assert feature_index("bos_strength") == 0
        assert feature_index("regime_1h_bullish") == 18
        assert feature_index("direction_long") == 23

    def test_feature_index_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unknown feature"):
            feature_index("nonexistent_feature_xyz")


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: encode_regime_1h — one-hot encoding
# ─────────────────────────────────────────────────────────────────────────────


class TestEncodeRegime1h:
    """Tests for the 4-element one-hot regime encoder."""

    def test_output_is_four_floats(self):
        result = encode_regime_1h("TRENDING_BULLISH")
        assert len(result) == 4
        assert all(isinstance(v, float) for v in result)

    def test_trending_bullish(self):
        assert encode_regime_1h("TRENDING_BULLISH") == [1.0, 0.0, 0.0, 0.0]

    def test_bullish_trending_alias(self):
        assert encode_regime_1h("BULLISH_TRENDING") == [1.0, 0.0, 0.0, 0.0]

    def test_strong_bullish_alias(self):
        assert encode_regime_1h("STRONG_BULLISH_TREND") == [1.0, 0.0, 0.0, 0.0]

    def test_trending_bearish(self):
        assert encode_regime_1h("TRENDING_BEARISH") == [0.0, 1.0, 0.0, 0.0]

    def test_bearish_trending_alias(self):
        assert encode_regime_1h("BEARISH_TRENDING") == [0.0, 1.0, 0.0, 0.0]

    def test_strong_bearish_alias(self):
        assert encode_regime_1h("STRONG_BEARISH_TREND") == [0.0, 1.0, 0.0, 0.0]

    def test_ranging(self):
        assert encode_regime_1h("RANGING") == [0.0, 0.0, 1.0, 0.0]

    def test_clear_range_alias(self):
        assert encode_regime_1h("CLEAR_RANGE") == [0.0, 0.0, 1.0, 0.0]

    def test_transitional(self):
        assert encode_regime_1h("TRANSITIONAL") == [0.0, 0.0, 0.0, 1.0]

    def test_conflicting_timeframes_alias(self):
        assert encode_regime_1h("CONFLICTING_TIMEFRAMES") == [0.0, 0.0, 0.0, 1.0]

    def test_unknown_produces_all_zeros(self):
        assert encode_regime_1h("UNKNOWN") == [0.0, 0.0, 0.0, 0.0]

    def test_empty_string_produces_all_zeros(self):
        assert encode_regime_1h("") == [0.0, 0.0, 0.0, 0.0]

    def test_exactly_one_bit_set_for_known_regimes(self):
        """Mutual exclusivity: exactly one slot must be 1.0 for known regimes."""
        known = [
            "TRENDING_BULLISH", "BULLISH_TRENDING", "STRONG_BULLISH_TREND",
            "TRENDING_BEARISH", "BEARISH_TRENDING", "STRONG_BEARISH_TREND",
            "RANGING", "CLEAR_RANGE",
            "TRANSITIONAL", "CONFLICTING_TIMEFRAMES",
        ]
        for regime in known:
            enc = encode_regime_1h(regime)
            assert sum(enc) == 1.0, (
                f"Expected exactly one 1.0 for regime '{regime}', got {enc}"
            )

    def test_all_zeros_for_unknown_regimes(self):
        """Unknown regimes must produce all-zero encoding."""
        for regime in ("UNKNOWN", "NONE", "", "INVALID_REGIME", "choppy_range"):
            enc = encode_regime_1h(regime)
            assert enc == [0.0, 0.0, 0.0, 0.0], (
                f"Expected [0,0,0,0] for unknown regime '{regime}', got {enc}"
            )

    def test_encoding_positions_match_feature_names_indices(self):
        """
        Encoding output index must match FEATURE_NAMES group 5 positions.
        This is the critical Java-Python alignment check.
        """
        enc = encode_regime_1h("TRENDING_BULLISH")
        # enc[0] = bullish → FEATURE_NAMES[18]
        # enc[1] = bearish → FEATURE_NAMES[19]
        # enc[2] = ranging → FEATURE_NAMES[20]
        # enc[3] = transitional → FEATURE_NAMES[21]
        assert FEATURE_NAMES[18] == "regime_1h_bullish"
        assert FEATURE_NAMES[19] == "regime_1h_bearish"
        assert FEATURE_NAMES[20] == "regime_1h_ranging"
        assert FEATURE_NAMES[21] == "regime_1h_transitional"
        assert enc[0] == 1.0  # TRENDING_BULLISH → bullish slot active


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: encode_direction — binary flag
# ─────────────────────────────────────────────────────────────────────────────


class TestEncodeDirection:
    """Tests for the direction_long binary encoder."""

    def test_long_is_1(self):
        assert encode_direction("LONG") == 1.0

    def test_buy_is_1(self):
        assert encode_direction("BUY") == 1.0

    def test_long_case_insensitive(self):
        assert encode_direction("long") == 1.0
        assert encode_direction("Long") == 1.0

    def test_buy_case_insensitive(self):
        assert encode_direction("buy") == 1.0

    def test_short_is_0(self):
        assert encode_direction("SHORT") == 0.0

    def test_sell_is_0(self):
        assert encode_direction("SELL") == 0.0

    def test_unknown_is_0(self):
        assert encode_direction("NONE") == 0.0
        assert encode_direction("") == 0.0

    def test_return_type_is_float(self):
        assert isinstance(encode_direction("LONG"), float)
        assert isinstance(encode_direction("SHORT"), float)

    def test_direction_long_index_is_23(self):
        """direction_long must be at FEATURE_NAMES index 23."""
        assert feature_index("direction_long") == 23
        assert FEATURE_NAMES[23] == "direction_long"


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Full golden vector — verifies complete 24-element assembly
# ─────────────────────────────────────────────────────────────────────────────


class TestGoldenFeatureVector:
    """
    Assembles a full 24-element feature vector from a known synthetic input
    and verifies every index against the documented FEATURE_NAMES.

    These golden values must match the Java FeatureParityTest.goldenLongBullishAligned()
    fixture exactly — same floats, same indices.
    """

    # Canonical golden numeric values (match Java FeatureParityTest buildDefaultVector)
    GOLDEN_SMC = [0.60, 0.80, 0.70, 0.55, 0.40]
    GOLDEN_MARKET = [0.75, 0.65, 0.70, 0.30, 0.25, 1.20, 0.05, 0.02]
    GOLDEN_GEOMETRY = [3.00, 150.00, 0.85]
    GOLDEN_ACCOUNT = [0.20, 0.10]

    def _build_golden_vector(
        self,
        direction: str = "LONG",
        regime_1h: str = "TRENDING_BULLISH",
        regime_alignment: bool = True,
    ) -> list[float]:
        regime_onehot = encode_regime_1h(regime_1h)
        alignment_float = 1.0 if regime_alignment else 0.0
        direction_float = encode_direction(direction)

        return (
            self.GOLDEN_SMC
            + self.GOLDEN_MARKET
            + self.GOLDEN_GEOMETRY
            + self.GOLDEN_ACCOUNT
            + regime_onehot
            + [alignment_float, direction_float]
        )

    def test_golden_vector_has_24_elements(self):
        vec = self._build_golden_vector()
        assert len(vec) == FEATURE_COUNT == 24

    def test_golden_long_bullish_aligned_smc_group(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[0] == pytest.approx(0.60, abs=1e-6)   # bos_strength
        assert vec[1] == pytest.approx(0.80, abs=1e-6)   # choch_strength
        assert vec[2] == pytest.approx(0.70, abs=1e-6)   # order_block_strength
        assert vec[3] == pytest.approx(0.55, abs=1e-6)   # fvg_strength
        assert vec[4] == pytest.approx(0.40, abs=1e-6)   # liquidity_proximity

    def test_golden_long_bullish_aligned_market_context_group(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[5]  == pytest.approx(0.75, abs=1e-6)   # trend_strength_1h
        assert vec[6]  == pytest.approx(0.65, abs=1e-6)   # trend_strength_15m
        assert vec[7]  == pytest.approx(0.70, abs=1e-6)   # trend_strength_4h
        assert vec[8]  == pytest.approx(0.30, abs=1e-6)   # volatility_1h
        assert vec[9]  == pytest.approx(0.25, abs=1e-6)   # volatility_15m
        assert vec[10] == pytest.approx(1.20, abs=1e-5)   # volume_profile
        assert vec[11] == pytest.approx(0.05, abs=1e-6)   # momentum_1h
        assert vec[12] == pytest.approx(0.02, abs=1e-6)   # momentum_15m

    def test_golden_long_bullish_aligned_geometry_group(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[13] == pytest.approx(3.00, abs=1e-5)    # risk_reward
        assert vec[14] == pytest.approx(150.0, abs=1e-3)   # risk_distance
        assert vec[15] == pytest.approx(0.85, abs=1e-6)    # entry_precision

    def test_golden_long_bullish_aligned_account_group(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[16] == pytest.approx(0.20, abs=1e-6)   # account_utilization
        assert vec[17] == pytest.approx(0.10, abs=1e-6)   # leverage_ratio

    def test_golden_long_bullish_aligned_regime_group(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[18] == 1.0   # regime_1h_bullish   ← active
        assert vec[19] == 0.0   # regime_1h_bearish
        assert vec[20] == 0.0   # regime_1h_ranging
        assert vec[21] == 0.0   # regime_1h_transitional

    def test_golden_long_bullish_aligned_binary_flags(self):
        vec = self._build_golden_vector("LONG", "TRENDING_BULLISH", True)
        assert vec[22] == 1.0   # regime_alignment=True
        assert vec[23] == 1.0   # direction_long=LONG

    def test_golden_short_bearish_unaligned(self):
        vec = self._build_golden_vector("SHORT", "TRENDING_BEARISH", False)
        assert len(vec) == 24
        assert vec[18] == 0.0   # bullish
        assert vec[19] == 1.0   # bearish ← active
        assert vec[20] == 0.0
        assert vec[21] == 0.0
        assert vec[22] == 0.0   # alignment=False
        assert vec[23] == 0.0   # direction=SHORT

    def test_golden_ranging_regime(self):
        vec = self._build_golden_vector("LONG", "RANGING", True)
        assert vec[18] == 0.0
        assert vec[19] == 0.0
        assert vec[20] == 1.0   # ranging ← active
        assert vec[21] == 0.0

    def test_golden_transitional_regime(self):
        vec = self._build_golden_vector("LONG", "TRANSITIONAL", False)
        assert vec[18] == 0.0
        assert vec[19] == 0.0
        assert vec[20] == 0.0
        assert vec[21] == 1.0   # transitional ← active

    def test_golden_unknown_regime_all_zeros(self):
        vec = self._build_golden_vector("LONG", "UNKNOWN", False)
        assert vec[18] == 0.0
        assert vec[19] == 0.0
        assert vec[20] == 0.0
        assert vec[21] == 0.0

    def test_all_elements_are_finite_floats(self):
        """No NaN, no inf in the golden vector."""
        import math
        vec = self._build_golden_vector()
        for i, v in enumerate(vec):
            assert math.isfinite(v), (
                f"Non-finite value at index {i} ({FEATURE_NAMES[i]}): {v}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Regime variant set completeness
# ─────────────────────────────────────────────────────────────────────────────


class TestRegimeVariantSets:
    """Verify all variant sets are non-empty and internally consistent."""

    def test_bullish_variants_non_empty(self):
        assert len(REGIME_BULLISH_VARIANTS) >= 3

    def test_bearish_variants_non_empty(self):
        assert len(REGIME_BEARISH_VARIANTS) >= 3

    def test_ranging_variants_non_empty(self):
        assert len(REGIME_RANGING_VARIANTS) >= 2

    def test_transitional_variants_non_empty(self):
        assert len(REGIME_TRANSITIONAL_VARIANTS) >= 2

    def test_variant_sets_are_mutually_exclusive(self):
        """No regime string should appear in more than one variant set."""
        all_sets = [
            REGIME_BULLISH_VARIANTS,
            REGIME_BEARISH_VARIANTS,
            REGIME_RANGING_VARIANTS,
            REGIME_TRANSITIONAL_VARIANTS,
        ]
        combined = []
        for s in all_sets:
            combined.extend(s)
        dupes = [v for v in combined if combined.count(v) > 1]
        assert len(dupes) == 0, (
            f"Regime strings appear in multiple variant sets: {dupes}"
        )
