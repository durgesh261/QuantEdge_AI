package com.quantedge.ai.dto;

import com.quantedge.ai.contract.FeatureContract;

import java.math.BigDecimal;

/**
 * Structured feature vector for AI inference.
 *
 * <h2>Feature Contract</h2>
 * This record holds <em>all</em> raw data needed by the AI pipeline. Of its
 * 25 constructor parameters, exactly {@value FeatureContract#FEATURE_COUNT} are
 * exposed to the ONNX model as a {@code float[24]} via {@link #toFloat32Array()}.
 *
 * <p>The mapping from record fields to model input indices is defined canonically
 * in {@link FeatureContract#FEATURE_NAMES}. The mandatory {@code FeatureParityTest}
 * verifies this mapping on every build.</p>
 *
 * <h2>Metadata-only fields (not in model input)</h2>
 * <ul>
 *   <li>{@code setupId} — audit/tracing identifier</li>
 *   <li>{@code symbol}  — instrument symbol for logging</li>
 *   <li>{@code regime15m}, {@code regime4h} — stored for audit; only {@code regime1h}
 *       contributes to the one-hot encoding at indices 18–21</li>
 * </ul>
 *
 * <h2>Fields that contribute to model input</h2>
 * <pre>
 *  Indices  Source                                   Encoding
 *  ───────  ────────────────────────────────────────  ──────────────────────────────────
 *   0 –  4  bosStrength … liquidityProximity          float value as-is
 *   5 – 12  trendStrength1h … momentum15m             float value as-is
 *  13 – 15  riskReward … entryPrecision               float value as-is
 *  16 – 17  accountUtilization, leverageRatio         float value as-is
 *  18 – 21  regime1h                                  4-class one-hot (BULLISH/BEARISH/RANGING/TRANSITIONAL)
 *      22   regimeAlignment                           boolean → 1.0f / 0.0f
 *      23   direction                                 LONG|BUY → 1.0f, else 0.0f
 * </pre>
 */
public record AiFeatureVector(
        // ── Metadata (not model inputs) ──────────────────────────────────────
        String setupId,
        String symbol,
        String direction,           // also encoded at index 23

        // ── Group 1: SMC Structural Features — model indices 0–4 ─────────────
        BigDecimal bosStrength,
        BigDecimal chochStrength,
        BigDecimal orderBlockStrength,
        BigDecimal fvgStrength,
        BigDecimal liquidityProximity,

        // ── Group 2: Market Context Features — model indices 5–12 ────────────
        BigDecimal trendStrength1h,
        BigDecimal trendStrength15m,
        BigDecimal trendStrength4h,
        BigDecimal volatility1h,
        BigDecimal volatility15m,
        BigDecimal volumeProfile,
        BigDecimal momentum1h,
        BigDecimal momentum15m,

        // ── Group 3: Setup Geometry — model indices 13–15 ────────────────────
        BigDecimal riskReward,
        BigDecimal riskDistance,
        BigDecimal entryPrecision,

        // ── Group 4: Account / Risk Context — model indices 16–17 ────────────
        BigDecimal accountUtilization,
        BigDecimal leverageRatio,

        // ── Group 5: Multi-timeframe Regime ──────────────────────────────────
        //    regime1h  → one-hot at model indices 18–21
        //    regime15m, regime4h → metadata only (not model inputs)
        String regime1h,
        String regime15m,
        String regime4h,

        // ── Group 6: Binary flags — model indices 22–23 ──────────────────────
        boolean regimeAlignment     // encoded at index 22; direction encoded at index 23
) {

    // ─────────────────────────────────────────────────────────────────────────
    // Model Input Encoding
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Encodes this feature vector into the {@code float[]{@value FeatureContract#FEATURE_COUNT}}
     * array expected by the ONNX model.
     *
     * <p>Index {@code i} corresponds to {@link FeatureContract#FEATURE_NAMES}{@code [i]}.
     * This method is the authoritative encoder — no other class should re-implement
     * this mapping.</p>
     *
     * @return a {@code float[24]} array ready for ONNX Runtime tensor creation.
     * @throws IllegalStateException if the record is not {@link #isValid()}.
     */
    public float[] toFloat32Array() {
        if (!isValid()) {
            throw new IllegalStateException(
                    "Cannot encode an invalid AiFeatureVector to float[]. Call isValid() first. setupId=" + setupId);
        }

        float[] arr = new float[FeatureContract.FEATURE_COUNT];
        int idx = 0;

        // ── Group 1: SMC Structural Features (indices 0–4) ───────────────────
        arr[idx++] = bosStrength.floatValue();           // 0  bos_strength
        arr[idx++] = chochStrength.floatValue();         // 1  choch_strength
        arr[idx++] = orderBlockStrength.floatValue();    // 2  order_block_strength
        arr[idx++] = fvgStrength.floatValue();           // 3  fvg_strength
        arr[idx++] = liquidityProximity.floatValue();    // 4  liquidity_proximity

        // ── Group 2: Market Context Features (indices 5–12) ──────────────────
        arr[idx++] = trendStrength1h.floatValue();       // 5  trend_strength_1h
        arr[idx++] = trendStrength15m.floatValue();      // 6  trend_strength_15m
        arr[idx++] = trendStrength4h.floatValue();       // 7  trend_strength_4h
        arr[idx++] = volatility1h.floatValue();          // 8  volatility_1h
        arr[idx++] = volatility15m.floatValue();         // 9  volatility_15m
        arr[idx++] = volumeProfile.floatValue();         // 10 volume_profile
        arr[idx++] = momentum1h.floatValue();            // 11 momentum_1h
        arr[idx++] = momentum15m.floatValue();           // 12 momentum_15m

        // ── Group 3: Setup Geometry (indices 13–15) ──────────────────────────
        arr[idx++] = riskReward.floatValue();            // 13 risk_reward
        arr[idx++] = riskDistance.floatValue();          // 14 risk_distance
        arr[idx++] = entryPrecision.floatValue();        // 15 entry_precision

        // ── Group 4: Account & Risk Context (indices 16–17) ──────────────────
        arr[idx++] = accountUtilization.floatValue();    // 16 account_utilization
        arr[idx++] = leverageRatio.floatValue();         // 17 leverage_ratio

        // ── Group 5: 1H Regime One-Hot Encoding (indices 18–21) ──────────────
        //    Exactly one slot is 1.0 for a known regime; all 0.0 for UNKNOWN.
        arr[idx++] = isBullish(regime1h)      ? 1.0f : 0.0f;  // 18 regime_1h_bullish
        arr[idx++] = isBearish(regime1h)      ? 1.0f : 0.0f;  // 19 regime_1h_bearish
        arr[idx++] = isRanging(regime1h)      ? 1.0f : 0.0f;  // 20 regime_1h_ranging
        arr[idx++] = isTransitional(regime1h) ? 1.0f : 0.0f;  // 21 regime_1h_transitional

        // ── Group 6: Binary Flags (indices 22–23) ────────────────────────────
        arr[idx++] = regimeAlignment ? 1.0f : 0.0f;            // 22 regime_alignment
        arr[idx]   = isLong(direction) ? 1.0f : 0.0f;          // 23 direction_long

        // Compile-time safety: if idx+1 != FEATURE_COUNT, update the encoding above.
        assert idx + 1 == FeatureContract.FEATURE_COUNT
                : "toFloat32Array() produced " + (idx + 1) + " values, expected " + FeatureContract.FEATURE_COUNT;

        return arr;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Private encoding helpers — each helper is tested individually
    // in FeatureParityTest to ensure regime string variants are handled.
    // ─────────────────────────────────────────────────────────────────────────

    private static boolean isBullish(String regime) {
        return "TRENDING_BULLISH".equals(regime)
                || "STRONG_BULLISH_TREND".equals(regime)
                || "BULLISH_TRENDING".equals(regime);
    }

    private static boolean isBearish(String regime) {
        return "TRENDING_BEARISH".equals(regime)
                || "STRONG_BEARISH_TREND".equals(regime)
                || "BEARISH_TRENDING".equals(regime);
    }

    private static boolean isRanging(String regime) {
        return "RANGING".equals(regime)
                || "CLEAR_RANGE".equals(regime);
    }

    private static boolean isTransitional(String regime) {
        return "TRANSITIONAL".equals(regime)
                || "CONFLICTING_TIMEFRAMES".equals(regime);
    }

    private static boolean isLong(String dir) {
        return "LONG".equalsIgnoreCase(dir) || "BUY".equalsIgnoreCase(dir);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Validation & Audit
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Validates that all required fields are present and non-null.
     * Does not validate numerical bounds — those are enforced during extraction.
     */
    public boolean isValid() {
        return setupId != null && !setupId.isBlank()
                && symbol != null && !symbol.isBlank()
                && direction != null && !direction.isBlank()
                && bosStrength != null
                && chochStrength != null
                && orderBlockStrength != null
                && fvgStrength != null
                && liquidityProximity != null
                && trendStrength1h != null
                && trendStrength15m != null
                && trendStrength4h != null
                && volatility1h != null
                && volatility15m != null
                && volumeProfile != null
                && momentum1h != null
                && momentum15m != null
                && riskReward != null
                && riskDistance != null
                && entryPrecision != null
                && accountUtilization != null
                && leverageRatio != null
                && regime1h != null
                && regime15m != null
                && regime4h != null;
    }

    /**
     * Returns a JSON-like feature summary for logging and audit trails.
     * Does not include {@code setupId} / {@code symbol} to avoid redundancy in audit records.
     */
    public String toFeatureSummary() {
        return String.format(
                "{\"setupId\":\"%s\",\"symbol\":\"%s\",\"direction\":\"%s\","
                + "\"bosStrength\":%s,\"chochStrength\":%s,\"obStrength\":%s,"
                + "\"fvgStrength\":%s,\"liqProximity\":%s,"
                + "\"trend1h\":%s,\"trend15m\":%s,\"trend4h\":%s,"
                + "\"vol1h\":%s,\"vol15m\":%s,\"volProfile\":%s,"
                + "\"mom1h\":%s,\"mom15m\":%s,"
                + "\"rr\":%s,\"riskDist\":%s,\"entryPrec\":%s,"
                + "\"accUtil\":%s,\"levRatio\":%s,"
                + "\"regime1h\":\"%s\",\"regime15m\":\"%s\",\"regime4h\":\"%s\","
                + "\"regimeAlign\":%b}",
                setupId, symbol, direction,
                bosStrength, chochStrength, orderBlockStrength,
                fvgStrength, liquidityProximity,
                trendStrength1h, trendStrength15m, trendStrength4h,
                volatility1h, volatility15m, volumeProfile,
                momentum1h, momentum15m,
                riskReward, riskDistance, entryPrecision,
                accountUtilization, leverageRatio,
                regime1h, regime15m, regime4h, regimeAlignment
        );
    }
}