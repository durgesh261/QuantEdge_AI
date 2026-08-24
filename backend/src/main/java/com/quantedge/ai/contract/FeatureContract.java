package com.quantedge.ai.contract;

/**
 * Canonical 24-feature contract for the QuantEdge AI ONNX model interface.
 *
 * <h2>Authority</h2>
 * This class is the single source of truth for the feature vector shape
 * that is fed into the ONNX model. {@code FEATURE_NAMES[i]} names the
 * feature at position {@code i} in the {@code float[]} array produced by
 * {@code AiFeatureVector.toFloat32Array()}.
 *
 * <h2>Cross-language parity</h2>
 * The Python training pipeline ({@code engine/feature_contract.py}) must
 * maintain an <strong>identical</strong> {@code FEATURE_NAMES} list and
 * {@code FEATURE_COUNT} value. The mandatory {@code FeatureParityTest}
 * enforces the Java side of this contract on every build.
 *
 * <h2>Invariant enforcement</h2>
 * A static initialiser asserts that {@code FEATURE_NAMES.length == FEATURE_COUNT}.
 * Any discrepancy raises {@link ExceptionInInitializerError} at class load time,
 * making the failure impossible to miss.
 *
 * <h2>Feature groups</h2>
 * <pre>
 *  Indices  Group                            Count
 *  ───────  ────────────────────────────────  ─────
 *   0 –  4  SMC Structural Features              5
 *   5 – 12  Market Context Features              8
 *  13 – 15  Setup Geometry Features              3
 *  16 – 17  Account &amp; Risk Context              2
 *  18 – 21  1H Regime One-Hot Encoding           4
 *  22 – 23  Binary Flags                         2
 *  ───────  ────────────────────────────────  ─────
 *                                         Total  24
 * </pre>
 */
public final class FeatureContract {

    private FeatureContract() {}

    // ─────────────────────────────────────────────────────────────────────────
    // Core constants — edit FEATURE_NAMES and FEATURE_COUNT together.
    // A mismatch causes ExceptionInInitializerError on startup.
    // ─────────────────────────────────────────────────────────────────────────

    /** Number of numeric inputs the ONNX model expects. */
    public static final int FEATURE_COUNT = 24;

    /**
     * Ordered feature names. {@code FEATURE_NAMES[i]} is the name of the
     * feature at index {@code i} in {@code AiFeatureVector.toFloat32Array()}.
     *
     * <p><strong>Must match {@code engine/feature_contract.py FEATURE_NAMES} exactly.</strong></p>
     */
    public static final String[] FEATURE_NAMES = {

        // ── Group 1: SMC Structural Features (indices 0 – 4) ─────────────────
        "bos_strength",           //  0  Break-of-structure magnitude, normalised to [0, 1]
        "choch_strength",         //  1  Change-of-character confidence, normalised to [0, 1]
        "order_block_strength",   //  2  Order-block mitigation freshness, normalised to [0, 1]
        "fvg_strength",           //  3  Fair-value-gap fill proximity, normalised to [0, 1]
        "liquidity_proximity",    //  4  Distance to nearest liquidity pool, normalised to [0, 1]

        // ── Group 2: Market Context Features (indices 5 – 12) ────────────────
        "trend_strength_1h",      //  5  EMA-slope trend strength on 1H timeframe, [0, 1]
        "trend_strength_15m",     //  6  EMA-slope trend strength on 15M timeframe, [0, 1]
        "trend_strength_4h",      //  7  EMA-slope trend strength on 4H timeframe, [0, 1]
        "volatility_1h",          //  8  ATR-normalised volatility on 1H, [0, 1]
        "volatility_15m",         //  9  ATR-normalised volatility on 15M, [0, 1]
        "volume_profile",         // 10  Recent / historical volume ratio, clipped to [0, 2]
        "momentum_1h",            // 11  10-period rate-of-change on 1H (unbounded, small)
        "momentum_15m",           // 12  10-period rate-of-change on 15M (unbounded, small)

        // ── Group 3: Setup Geometry Features (indices 13 – 15) ───────────────
        "risk_reward",            // 13  Raw risk/reward ratio (e.g. 2.0, 3.0)
        "risk_distance",          // 14  Stop-distance in price units (e.g. 150.0)
        "entry_precision",        // 15  Entry proximity to OB/FVG, normalised to [0, 1]

        // ── Group 4: Account & Risk Context (indices 16 – 17) ────────────────
        "account_utilization",    // 16  Margin-used / total-equity, normalised to [0, 1]
        "leverage_ratio",         // 17  Setup leverage / 100 (max-leverage), normalised to [0, 1]

        // ── Group 5: 1H Regime One-Hot Encoding (indices 18 – 21) ────────────
        //    Source field: AiFeatureVector.regime1h()
        //    Exactly one of the four is 1.0; all four are 0.0 for UNKNOWN.
        "regime_1h_bullish",      // 18  1.0 if regime1h == "TRENDING_BULLISH"
        "regime_1h_bearish",      // 19  1.0 if regime1h == "TRENDING_BEARISH"
        "regime_1h_ranging",      // 20  1.0 if regime1h == "RANGING"
        "regime_1h_transitional", // 21  1.0 if regime1h == "TRANSITIONAL"

        // ── Group 6: Binary Flags (indices 22 – 23) ──────────────────────────
        "regime_alignment",       // 22  1.0 if 1H / 15M / 4H regimes are all identical
        "direction_long",         // 23  1.0 if setup direction is "LONG" or "BUY"; else 0.0
    };

    // ─────────────────────────────────────────────────────────────────────────
    // Invariant enforcement — runs once at class load.
    // ─────────────────────────────────────────────────────────────────────────

    static {
        if (FEATURE_NAMES.length != FEATURE_COUNT) {
            throw new ExceptionInInitializerError(
                    "FeatureContract invariant violated: FEATURE_NAMES.length=" +
                    FEATURE_NAMES.length + " but FEATURE_COUNT=" + FEATURE_COUNT +
                    ". Update one or the other to restore parity."
            );
        }
        // Check for duplicates
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (String name : FEATURE_NAMES) {
            if (name == null || name.isBlank()) {
                throw new ExceptionInInitializerError(
                        "FeatureContract invariant violated: null or blank feature name detected.");
            }
            if (!seen.add(name)) {
                throw new ExceptionInInitializerError(
                        "FeatureContract invariant violated: duplicate feature name '" + name + "'.");
            }
        }
    }
}
