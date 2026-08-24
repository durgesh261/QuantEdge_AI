package com.quantedge.ai.service;

/**
 * Canonical 24-feature contract for ONNX AI inference.
 * 
 * This specification is shared identically between:
 * - Python training feature extractor
 * - Java ONNX inference service (OnnxModelInferenceService)
 * - FeatureParityTest numerical validation
 * 
 * Feature indices are 0-based, matching OnnxModelInferenceService.featuresToNormalizedArray().
 * All features are BigDecimal in AiFeatureVector, converted to float[24] for ONNX input.
 * 
 * Numerical parity tolerance between Python and Java: 1e-5 (maximum acceptable absolute difference)
 * for already-normalized features; for raw feature values, tolerance is determined by the
 * feature's natural scale (e.g., BigDecimal scale/precision).
 */
public final class FeatureContract {

    // Feature indices matching OnnxModelInferenceService.featuresToNormalizedArray()
    public static final int BOS_STRENGTH = 0;
    public static final int CHOCH_STRENGTH = 1;
    public static final int ORDER_BLOCK_STRENGTH = 2;
    public static final int FVG_STRENGTH = 3;
    public static final int LIQUIDITY_PROXIMITY = 4;
    public static final int TREND_STRENGTH_1H = 5;
    public static final int TREND_STRENGTH_15M = 6;
    public static final int TREND_STRENGTH_4H = 7;
    public static final int VOLATILITY_1H = 8;
    public static final int VOLATILITY_15M = 9;
    public static final int VOLUME_PROFILE = 10;
    public static final int MOMENTUM_1H = 11;
    public static final int MOMENTUM_15M = 12;
    public static final int RISK_REWARD = 13;
    public static final int RISK_DISTANCE = 14;
    public static final int ENTRY_PRECISION = 15;
    public static final int ACCOUNT_UTILIZATION = 16;
    public static final int LEVERAGE_RATIO = 17;
    // Regime 1h one-hot encoded: indices 18-21
    public static final int REGIME_1H_BULLISH = 18;
    public static final int REGIME_1H_BEARISH = 19;
    public static final int REGIME_1H_RANGING = 20;
    public static final int REGIME_1H_TRANSITIONAL = 21;
    public static final int REGIME_ALIGNMENT = 22;
    public static final int DIRECTION = 23;

    // Total feature count
    public static final int INPUT_FEATURE_COUNT = 24;

    // Human-readable feature names (index -> name)
    public static final String[] FEATURE_NAMES = new String[]{
            "bosStrength",
            "chochStrength",
            "orderBlockStrength",
            "fvgStrength",
            "liquidityProximity",
            "trendStrength1h",
            "trendStrength15m",
            "trendStrength4h",
            "volatility1h",
            "volatility15m",
            "volumeProfile",
            "momentum1h",
            "momentum15m",
            "riskReward",
            "riskDistance",
            "entryPrecision",
            "accountUtilization",
            "leverageRatio",
            "regime1h_bullish",
            "regime1h_bearish",
            "regime1h_ranging",
            "regime1h_transitional",
            "regimeAlignment",
            "direction"
    };

    // Feature descriptions for audit/logging
    public static final String[] FEATURE_DESCRIPTIONS = new String[]{
            "Strength of Break of Structure (0.0-1.0, higher = stronger BOS)",
            "Strength of Change of Character (0.0-1.0, higher = stronger CHoCH)",
            "Strength of Order Block (0.0-1.0, higher = stronger OB)",
            "Strength of Fair Value Gap (0.0-1.0, higher = stronger FVG)",
            "Proximity to nearest liquidity pool (0.0-1.0, higher = closer)",
            "Trend strength on 1-hour timeframe (0.0-1.0, higher = stronger trend)",
            "Trend strength on 15-minute timeframe (0.0-1.0, higher = stronger trend)",
            "Trend strength on 4-hour timeframe (0.0-1.0, higher = stronger trend)",
            "Volatility measure on 1-hour timeframe (0.0-1.0, higher = higher vol)",
            "Volatility measure on 15-minute timeframe (0.0-1.0, higher = higher vol)",
            "Volume profile distribution (0.0-1.0, higher = higher volume concentration)",
            "Momentum on 1-hour timeframe (0.0-1.0, higher = stronger momentum)",
            "Momentum on 15-minute timeframe (0.0-1.0, higher = stronger momentum)",
            "Risk/reward ratio of the setup (e.g., 3.0 means 1:3 risk:reward)",
            "Distance from entry to stop-loss (price units, smaller = tighter risk)",
            "Precision of entry price versus ideal entry (0.0-1.0, higher = more precise)",
            "Fraction of available account equity allocated to this position (0.0-1.0)",
            "Leverage ratio in effect for this setup (e.g., 10.0 = 10x leverage)",
            "BULLISH regime flag for 1h timeframe (one-hot, index 18)",
            "BEARISH regime flag for 1h timeframe (one-hot, index 19)",
            "RANGING regime flag for 1h timeframe (one-hot, index 20)",
            "TRANSITIONAL regime flag for 1h timeframe (one-hot, index 21)",
            "Regime alignment indicator (1 = aligned across timeframes, 0 = misaligned)",
            "Order direction encoding (1 = BUY/LONG, 0 = SELL/SHORT)"
    };

    // Valid range for each feature (min, max). Null means no range constraint.
    // All BigDecimal features are expected in [0.0, 1.0] except:
    // - riskReward: positive, no upper bound
    // - riskDistance: > 0
    // - entryPrecision: [0.0, 1.0]
    // - accountUtilization: [0.0, 1.0]
    // - leverageRatio: > 0
    public static final double[][] FEATURE_RANGES = new double[][]{
            {0.0, 1.0},         // 0: bosStrength
            {0.0, 1.0},         // 1: chochStrength
            {0.0, 1.0},         // 2: orderBlockStrength
            {0.0, 1.0},         // 3: fvgStrength
            {0.0, 1.0},         // 4: liquidityProximity
            {0.0, 1.0},         // 5: trendStrength1h
            {0.0, 1.0},         // 6: trendStrength15m
            {0.0, 1.0},         // 7: trendStrength4h
            {0.0, 1.0},         // 8: volatility1h
            {0.0, 1.0},         // 9: volatility15m
            {0.0, 1.0},         // 10: volumeProfile
            {0.0, 1.0},         // 11: momentum1h
            {0.0, 1.0},         // 12: momentum15m
            {0.0, Double.POSITIVE_INFINITY}, // 13: riskReward (positive, unbounded)
            {0.0, Double.POSITIVE_INFINITY}, // 14: riskDistance (>0)
            {0.0, 1.0},         // 15: entryPrecision
            {0.0, 1.0},         // 16: accountUtilization
            {0.0, Double.POSITIVE_INFINITY}, // 17: leverageRatio (>0)
            {0.0, 1.0},         // 18: regime1h_bullish (one-hot)
            {0.0, 1.0},         // 19: regime1h_bearish (one-hot)
            {0.0, 1.0},         // 20: regime1h_ranging (one-hot)
            {0.0, 1.0},         // 21: regime1h_transitional (one-hot)
            {0.0, 1.0},         // 22: regimeAlignment (boolean)
            {0.0, 1.0}          // 23: direction (one-hot: BUY/LONG=1, SELL/SHORT=0)
    };

    // Default tolerance for FeatureParityTest numerical comparison
    // (absolute difference between Python and Java float values)
    public static final double PARITY_TOLERANCE_ABSOLUTE = 1e-5;

    // Prohibited: manufacturing or fabricating historical data features.
    // All feature values MUST come from legitimate market data and SMC analysis.
    public static final String MANUFACTURING_PROHIBITED = "Feature values must originate from legitimate market data and SMC setup analysis; manufacturing or fabricating data is strictly prohibited.";

    // Private constructor - utility class
    private FeatureContract() {
    }
}