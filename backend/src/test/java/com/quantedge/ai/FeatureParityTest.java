package com.quantedge.ai;

import com.quantedge.ai.contract.FeatureContract;
import com.quantedge.ai.dto.AiFeatureVector;
import org.assertj.core.data.Offset;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Mandatory Feature Contract Parity Tests.
 *
 * <h2>Purpose</h2>
 * This test class enforces the structural correctness of the 24-feature AI model
 * interface. It must <strong>never</strong> be skipped, disabled, or removed.
 *
 * <h2>What it verifies</h2>
 * <ol>
 *   <li>{@link FeatureContract#FEATURE_COUNT} == 24 (constant value is correct)</li>
 *   <li>{@link FeatureContract#FEATURE_NAMES} has exactly 24 unique, non-blank entries</li>
 *   <li>{@link AiFeatureVector#toFloat32Array()} produces exactly 24 floats</li>
 *   <li>The golden encoding: known input values → expected float values at each named index</li>
 *   <li>All regime and direction variants encode correctly</li>
 * </ol>
 *
 * <h2>Cross-language parity (Phase C)</h2>
 * The Java contract verified here must be manually or automatically compared with
 * {@code engine/feature_contract.py} before the ONNX model is trained. The Python
 * golden parity test (Phase C) will verify that running identical inputs through
 * Python ONNX Runtime and Java ONNX Runtime produces identical outputs.
 */
@DisplayName("Phase A: Feature Contract Parity — MANDATORY, never disable")
class FeatureParityTest {

    // ─────────────────────────────────────────────────────────────────────────
    // Section 1: FeatureContract structural invariants
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("1. FeatureContract structural invariants")
    class ContractInvariants {

        @Test
        @DisplayName("FEATURE_COUNT constant is exactly 24")
        void featureCountIs24() {
            assertThat(FeatureContract.FEATURE_COUNT)
                    .as("FeatureContract.FEATURE_COUNT must be exactly 24 to match ONNX model input shape [1, 24]")
                    .isEqualTo(24);
        }

        @Test
        @DisplayName("FEATURE_NAMES array has exactly FEATURE_COUNT entries")
        void featureNamesArrayHasFeatureCountEntries() {
            assertThat(FeatureContract.FEATURE_NAMES)
                    .as("FeatureContract.FEATURE_NAMES.length must equal FEATURE_COUNT=%d", FeatureContract.FEATURE_COUNT)
                    .hasSize(FeatureContract.FEATURE_COUNT);
        }

        @Test
        @DisplayName("All FEATURE_NAMES are non-null and non-blank")
        void featureNamesAreNonBlank() {
            for (int i = 0; i < FeatureContract.FEATURE_NAMES.length; i++) {
                String name = FeatureContract.FEATURE_NAMES[i];
                assertThat(name)
                        .as("Feature at index %d must not be null or blank. Check FeatureContract.FEATURE_NAMES[%d].", i, i)
                        .isNotNull()
                        .isNotBlank();
            }
        }

        @Test
        @DisplayName("All FEATURE_NAMES are unique — no duplicate feature names")
        void featureNamesAreUnique() {
            String[] names = FeatureContract.FEATURE_NAMES;
            Set<String> unique = new HashSet<>(Arrays.asList(names));
            assertThat(unique)
                    .as("Duplicate names detected in FeatureContract.FEATURE_NAMES. Found %d unique names but expected %d.",
                            unique.size(), names.length)
                    .hasSize(names.length);
        }

        @Test
        @DisplayName("FEATURE_NAMES are in snake_case format (enforces naming convention)")
        void featureNamesAreSnakeCase() {
            for (String name : FeatureContract.FEATURE_NAMES) {
                assertThat(name)
                        .as("Feature name '%s' must be lower_snake_case", name)
                        .matches("[a-z][a-z0-9_]*");
            }
        }

        @Test
        @DisplayName("FEATURE_NAMES group offsets match documented indices (spot-check)")
        void featureNamesGroupOffsets() {
            String[] n = FeatureContract.FEATURE_NAMES;
            // Group 1: SMC (0–4)
            assertThat(n[0]).isEqualTo("bos_strength");
            assertThat(n[4]).isEqualTo("liquidity_proximity");
            // Group 2: Market Context (5–12)
            assertThat(n[5]).isEqualTo("trend_strength_1h");
            assertThat(n[12]).isEqualTo("momentum_15m");
            // Group 3: Geometry (13–15)
            assertThat(n[13]).isEqualTo("risk_reward");
            assertThat(n[15]).isEqualTo("entry_precision");
            // Group 4: Account (16–17)
            assertThat(n[16]).isEqualTo("account_utilization");
            assertThat(n[17]).isEqualTo("leverage_ratio");
            // Group 5: Regime one-hot (18–21)
            assertThat(n[18]).isEqualTo("regime_1h_bullish");
            assertThat(n[19]).isEqualTo("regime_1h_bearish");
            assertThat(n[20]).isEqualTo("regime_1h_ranging");
            assertThat(n[21]).isEqualTo("regime_1h_transitional");
            // Group 6: Binary flags (22–23)
            assertThat(n[22]).isEqualTo("regime_alignment");
            assertThat(n[23]).isEqualTo("direction_long");
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Section 2: AiFeatureVector.toFloat32Array() structural correctness
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("2. AiFeatureVector.toFloat32Array() structural correctness")
    class Encoding_StructuralCorrectness {

        @Test
        @DisplayName("toFloat32Array() produces exactly FeatureContract.FEATURE_COUNT floats")
        void producesExactlyFeatureCountFloats() {
            float[] arr = longBullish().toFloat32Array();
            assertThat(arr)
                    .as("toFloat32Array() must return float[%d]", FeatureContract.FEATURE_COUNT)
                    .hasSize(FeatureContract.FEATURE_COUNT);
        }

        @Test
        @DisplayName("toFloat32Array() length matches FEATURE_NAMES length")
        void arrayLengthMatchesFeatureNamesLength() {
            float[] arr = longBullish().toFloat32Array();
            assertThat(arr.length).isEqualTo(FeatureContract.FEATURE_NAMES.length);
        }

        @Test
        @DisplayName("toFloat32Array() throws IllegalStateException for invalid vector (null BigDecimal)")
        void throwsForInvalidVector() {
            AiFeatureVector invalid = buildVector("LONG", "TRENDING_BULLISH", true,
                    null, // null bosStrength → isValid() returns false
                    bd("0.80"), bd("0.70"), bd("0.55"), bd("0.40"),
                    bd("0.75"), bd("0.65"), bd("0.70"), bd("0.30"), bd("0.25"),
                    bd("1.20"), bd("0.05"), bd("0.02"),
                    bd("3.00"), bd("150.00"), bd("0.85"),
                    bd("0.20"), bd("0.10"));
            assertThatThrownBy(invalid::toFloat32Array)
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("invalid AiFeatureVector");
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Section 3: Golden encoding tests — numeric fields (indices 0–17)
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("3. Golden encoding — numeric fields (indices 0–17)")
    class GoldenEncoding_NumericFields {

        private static final Offset<Double> TOLERANCE = Offset.offset(1e-6);

        @Test
        @DisplayName("Group 1: SMC structural features encode at correct indices")
        void smcStructuralFeaturesEncodeCorrectly() {
            float[] arr = longBullish().toFloat32Array();
            assertThat((double) arr[0]).as("idx 0: bos_strength").isCloseTo(0.60, TOLERANCE);
            assertThat((double) arr[1]).as("idx 1: choch_strength").isCloseTo(0.80, TOLERANCE);
            assertThat((double) arr[2]).as("idx 2: order_block_strength").isCloseTo(0.70, TOLERANCE);
            assertThat((double) arr[3]).as("idx 3: fvg_strength").isCloseTo(0.55, TOLERANCE);
            assertThat((double) arr[4]).as("idx 4: liquidity_proximity").isCloseTo(0.40, TOLERANCE);
        }

        @Test
        @DisplayName("Group 2: Market context features encode at correct indices")
        void marketContextFeaturesEncodeCorrectly() {
            float[] arr = longBullish().toFloat32Array();
            assertThat((double) arr[5]).as("idx 5: trend_strength_1h").isCloseTo(0.75, TOLERANCE);
            assertThat((double) arr[6]).as("idx 6: trend_strength_15m").isCloseTo(0.65, TOLERANCE);
            assertThat((double) arr[7]).as("idx 7: trend_strength_4h").isCloseTo(0.70, TOLERANCE);
            assertThat((double) arr[8]).as("idx 8: volatility_1h").isCloseTo(0.30, TOLERANCE);
            assertThat((double) arr[9]).as("idx 9: volatility_15m").isCloseTo(0.25, TOLERANCE);
            assertThat((double) arr[10]).as("idx 10: volume_profile").isCloseTo(1.20, TOLERANCE);
            assertThat((double) arr[11]).as("idx 11: momentum_1h").isCloseTo(0.05, TOLERANCE);
            assertThat((double) arr[12]).as("idx 12: momentum_15m").isCloseTo(0.02, TOLERANCE);
        }

        @Test
        @DisplayName("Group 3: Setup geometry features encode at correct indices")
        void setupGeometryFeaturesEncodeCorrectly() {
            float[] arr = longBullish().toFloat32Array();
            assertThat((double) arr[13]).as("idx 13: risk_reward").isCloseTo(3.00, TOLERANCE);
            assertThat((double) arr[14]).as("idx 14: risk_distance").isCloseTo(150.0, Offset.offset(1e-3));
            assertThat((double) arr[15]).as("idx 15: entry_precision").isCloseTo(0.85, TOLERANCE);
        }

        @Test
        @DisplayName("Group 4: Account & risk context features encode at correct indices")
        void accountContextFeaturesEncodeCorrectly() {
            float[] arr = longBullish().toFloat32Array();
            assertThat((double) arr[16]).as("idx 16: account_utilization").isCloseTo(0.20, TOLERANCE);
            assertThat((double) arr[17]).as("idx 17: leverage_ratio").isCloseTo(0.10, TOLERANCE);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Section 4: Regime one-hot encoding (indices 18–21)
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("4. Regime one-hot encoding (indices 18–21)")
    class RegimeOneHotEncoding {

        @Test
        @DisplayName("TRENDING_BULLISH → regime_1h_bullish=1.0, others=0.0")
        void trendingBullish() {
            float[] arr = buildDefaultVector("LONG", "TRENDING_BULLISH", true).toFloat32Array();
            assertThat(arr[18]).as("regime_1h_bullish").isEqualTo(1.0f);
            assertThat(arr[19]).as("regime_1h_bearish").isEqualTo(0.0f);
            assertThat(arr[20]).as("regime_1h_ranging").isEqualTo(0.0f);
            assertThat(arr[21]).as("regime_1h_transitional").isEqualTo(0.0f);
        }

        @Test
        @DisplayName("TRENDING_BEARISH → regime_1h_bearish=1.0, others=0.0")
        void trendingBearish() {
            float[] arr = buildDefaultVector("SHORT", "TRENDING_BEARISH", false).toFloat32Array();
            assertThat(arr[18]).as("regime_1h_bullish").isEqualTo(0.0f);
            assertThat(arr[19]).as("regime_1h_bearish").isEqualTo(1.0f);
            assertThat(arr[20]).as("regime_1h_ranging").isEqualTo(0.0f);
            assertThat(arr[21]).as("regime_1h_transitional").isEqualTo(0.0f);
        }

        @Test
        @DisplayName("RANGING → regime_1h_ranging=1.0, others=0.0")
        void ranging() {
            float[] arr = buildDefaultVector("LONG", "RANGING", true).toFloat32Array();
            assertThat(arr[18]).isEqualTo(0.0f);
            assertThat(arr[19]).isEqualTo(0.0f);
            assertThat(arr[20]).as("regime_1h_ranging").isEqualTo(1.0f);
            assertThat(arr[21]).isEqualTo(0.0f);
        }

        @Test
        @DisplayName("TRANSITIONAL → regime_1h_transitional=1.0, others=0.0")
        void transitional() {
            float[] arr = buildDefaultVector("LONG", "TRANSITIONAL", false).toFloat32Array();
            assertThat(arr[18]).isEqualTo(0.0f);
            assertThat(arr[19]).isEqualTo(0.0f);
            assertThat(arr[20]).isEqualTo(0.0f);
            assertThat(arr[21]).as("regime_1h_transitional").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("UNKNOWN → all regime one-hot slots are 0.0 (no active category)")
        void unknown() {
            float[] arr = buildDefaultVector("LONG", "UNKNOWN", false).toFloat32Array();
            assertThat(arr[18]).isEqualTo(0.0f);
            assertThat(arr[19]).isEqualTo(0.0f);
            assertThat(arr[20]).isEqualTo(0.0f);
            assertThat(arr[21]).isEqualTo(0.0f);
        }

        @Test
        @DisplayName("Alias BULLISH_TRENDING also encodes as bullish (variant support)")
        void aliasBullishTrending() {
            float[] arr = buildDefaultVector("LONG", "BULLISH_TRENDING", true).toFloat32Array();
            assertThat(arr[18]).as("BULLISH_TRENDING alias → regime_1h_bullish=1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("Alias BEARISH_TRENDING also encodes as bearish (variant support)")
        void aliasBearishTrending() {
            float[] arr = buildDefaultVector("SHORT", "BEARISH_TRENDING", false).toFloat32Array();
            assertThat(arr[19]).as("BEARISH_TRENDING alias → regime_1h_bearish=1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("Alias CLEAR_RANGE also encodes as ranging (variant support)")
        void aliasClearRange() {
            float[] arr = buildDefaultVector("LONG", "CLEAR_RANGE", false).toFloat32Array();
            assertThat(arr[20]).as("CLEAR_RANGE alias → regime_1h_ranging=1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("Alias CONFLICTING_TIMEFRAMES also encodes as transitional (variant support)")
        void aliasConflictingTimeframes() {
            float[] arr = buildDefaultVector("LONG", "CONFLICTING_TIMEFRAMES", false).toFloat32Array();
            assertThat(arr[21]).as("CONFLICTING_TIMEFRAMES alias → regime_1h_transitional=1.0").isEqualTo(1.0f);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Section 5: Binary flag encoding (indices 22–23)
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("5. Binary flag encoding (indices 22–23)")
    class BinaryFlagEncoding {

        @Test
        @DisplayName("regimeAlignment=true → index 22 = 1.0")
        void regimeAlignmentTrue() {
            float[] arr = buildDefaultVector("LONG", "TRENDING_BULLISH", true).toFloat32Array();
            assertThat(arr[22]).as("idx 22: regime_alignment=true → 1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("regimeAlignment=false → index 22 = 0.0")
        void regimeAlignmentFalse() {
            float[] arr = buildDefaultVector("LONG", "TRENDING_BULLISH", false).toFloat32Array();
            assertThat(arr[22]).as("idx 22: regime_alignment=false → 0.0").isEqualTo(0.0f);
        }

        @Test
        @DisplayName("direction=LONG → index 23 = 1.0 (direction_long)")
        void directionLong() {
            float[] arr = buildDefaultVector("LONG", "TRENDING_BULLISH", true).toFloat32Array();
            assertThat(arr[23]).as("idx 23: direction=LONG → direction_long=1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("direction=BUY → index 23 = 1.0 (alias support)")
        void directionBuy() {
            float[] arr = buildDefaultVector("BUY", "TRENDING_BULLISH", true).toFloat32Array();
            assertThat(arr[23]).as("idx 23: direction=BUY → direction_long=1.0").isEqualTo(1.0f);
        }

        @Test
        @DisplayName("direction=SHORT → index 23 = 0.0")
        void directionShort() {
            float[] arr = buildDefaultVector("SHORT", "TRENDING_BEARISH", false).toFloat32Array();
            assertThat(arr[23]).as("idx 23: direction=SHORT → direction_long=0.0").isEqualTo(0.0f);
        }

        @Test
        @DisplayName("direction=SELL → index 23 = 0.0")
        void directionSell() {
            float[] arr = buildDefaultVector("SELL", "TRENDING_BEARISH", false).toFloat32Array();
            assertThat(arr[23]).as("idx 23: direction=SELL → direction_long=0.0").isEqualTo(0.0f);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Section 6: Full golden vector test — all 24 values, bit-exact
    // ─────────────────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("6. Full golden vector — all 24 values verified")
    class FullGoldenVector {

        @Test
        @DisplayName("LONG / TRENDING_BULLISH / regimeAlignment=true — full 24-element golden array")
        void goldenLongBullishAligned() {
            float[] arr = longBullish().toFloat32Array();

            // Verify index-by-index against canonical names
            assertThat(arr).hasSize(24);

            // idx 0–4: SMC
            assertThat(arr[0]).isEqualTo(0.60f, Offset.offset(1e-6f));   // bos_strength
            assertThat(arr[1]).isEqualTo(0.80f, Offset.offset(1e-6f));   // choch_strength
            assertThat(arr[2]).isEqualTo(0.70f, Offset.offset(1e-6f));   // order_block_strength
            assertThat(arr[3]).isEqualTo(0.55f, Offset.offset(1e-6f));   // fvg_strength
            assertThat(arr[4]).isEqualTo(0.40f, Offset.offset(1e-6f));   // liquidity_proximity
            // idx 5–12: Market Context
            assertThat(arr[5]).isEqualTo(0.75f, Offset.offset(1e-6f));   // trend_strength_1h
            assertThat(arr[6]).isEqualTo(0.65f, Offset.offset(1e-6f));   // trend_strength_15m
            assertThat(arr[7]).isEqualTo(0.70f, Offset.offset(1e-6f));   // trend_strength_4h
            assertThat(arr[8]).isEqualTo(0.30f, Offset.offset(1e-6f));   // volatility_1h
            assertThat(arr[9]).isEqualTo(0.25f, Offset.offset(1e-6f));   // volatility_15m
            assertThat(arr[10]).isEqualTo(1.20f, Offset.offset(1e-5f));  // volume_profile
            assertThat(arr[11]).isEqualTo(0.05f, Offset.offset(1e-6f));  // momentum_1h
            assertThat(arr[12]).isEqualTo(0.02f, Offset.offset(1e-6f));  // momentum_15m
            // idx 13–15: Geometry
            assertThat(arr[13]).isEqualTo(3.00f, Offset.offset(1e-5f));  // risk_reward
            assertThat(arr[14]).isEqualTo(150.0f, Offset.offset(1e-3f)); // risk_distance
            assertThat(arr[15]).isEqualTo(0.85f, Offset.offset(1e-6f));  // entry_precision
            // idx 16–17: Account
            assertThat(arr[16]).isEqualTo(0.20f, Offset.offset(1e-6f));  // account_utilization
            assertThat(arr[17]).isEqualTo(0.10f, Offset.offset(1e-6f));  // leverage_ratio
            // idx 18–21: Regime one-hot (TRENDING_BULLISH)
            assertThat(arr[18]).isEqualTo(1.0f);                          // regime_1h_bullish
            assertThat(arr[19]).isEqualTo(0.0f);                          // regime_1h_bearish
            assertThat(arr[20]).isEqualTo(0.0f);                          // regime_1h_ranging
            assertThat(arr[21]).isEqualTo(0.0f);                          // regime_1h_transitional
            // idx 22–23: Binary flags
            assertThat(arr[22]).isEqualTo(1.0f);                          // regime_alignment
            assertThat(arr[23]).isEqualTo(1.0f);                          // direction_long
        }

        @Test
        @DisplayName("SHORT / TRENDING_BEARISH / regimeAlignment=false — full 24-element golden array")
        void goldenShortBearishUnaligned() {
            float[] arr = buildDefaultVector("SHORT", "TRENDING_BEARISH", false).toFloat32Array();
            assertThat(arr).hasSize(24);
            assertThat(arr[18]).isEqualTo(0.0f);  // bullish
            assertThat(arr[19]).isEqualTo(1.0f);  // bearish ← active
            assertThat(arr[20]).isEqualTo(0.0f);
            assertThat(arr[21]).isEqualTo(0.0f);
            assertThat(arr[22]).isEqualTo(0.0f);  // alignment=false
            assertThat(arr[23]).isEqualTo(0.0f);  // direction=SHORT
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helper builders
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Canonical golden LONG / TRENDING_BULLISH / aligned fixture.
     * All numeric values are chosen to be exact float32 representations
     * to avoid rounding noise in assertions.
     */
    private AiFeatureVector longBullish() {
        return buildDefaultVector("LONG", "TRENDING_BULLISH", true);
    }

    /**
     * Builds a fully-specified AiFeatureVector using the canonical golden numeric values.
     * Only direction, regime1h, and regimeAlignment vary per test scenario.
     */
    private AiFeatureVector buildDefaultVector(String direction, String regime1h, boolean regimeAlignment) {
        return buildVector(direction, regime1h, regimeAlignment,
                bd("0.60"), bd("0.80"), bd("0.70"), bd("0.55"), bd("0.40"),  // SMC
                bd("0.75"), bd("0.65"), bd("0.70"), bd("0.30"), bd("0.25"),  // Market (trend + vol)
                bd("1.20"), bd("0.05"), bd("0.02"),                           // Market (vol profile + momentum)
                bd("3.00"), bd("150.00"), bd("0.85"),                         // Geometry
                bd("0.20"), bd("0.10")                                        // Account
        );
    }

    private AiFeatureVector buildVector(
            String direction, String regime1h, boolean regimeAlignment,
            BigDecimal bosStrength, BigDecimal chochStrength, BigDecimal orderBlockStrength,
            BigDecimal fvgStrength, BigDecimal liquidityProximity,
            BigDecimal trendStrength1h, BigDecimal trendStrength15m, BigDecimal trendStrength4h,
            BigDecimal volatility1h, BigDecimal volatility15m,
            BigDecimal volumeProfile, BigDecimal momentum1h, BigDecimal momentum15m,
            BigDecimal riskReward, BigDecimal riskDistance, BigDecimal entryPrecision,
            BigDecimal accountUtilization, BigDecimal leverageRatio
    ) {
        return new AiFeatureVector(
                "test-setup-parity-001",   // setupId (metadata)
                "BTCUSD",                  // symbol  (metadata)
                direction,

                // SMC
                bosStrength, chochStrength, orderBlockStrength, fvgStrength, liquidityProximity,
                // Market Context
                trendStrength1h, trendStrength15m, trendStrength4h,
                volatility1h, volatility15m, volumeProfile, momentum1h, momentum15m,
                // Geometry
                riskReward, riskDistance, entryPrecision,
                // Account
                accountUtilization, leverageRatio,
                // Regime
                regime1h,
                "RANGING",            // regime15m — metadata only, not a model input
                "TRENDING_BULLISH",   // regime4h  — metadata only, not a model input
                regimeAlignment
        );
    }

    private static BigDecimal bd(String val) {
        return new BigDecimal(val);
    }
}
