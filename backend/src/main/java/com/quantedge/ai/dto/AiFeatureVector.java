package com.quantedge.ai.dto;

import java.math.BigDecimal;

/**
 * Structured feature vector for AI inference.
 * All features are computed deterministically from market data and SMC setups.
 */
public record AiFeatureVector(
        String setupId,
        String symbol,
        String direction,

        // SMC Structural Features (0.0 - 1.0)
        BigDecimal bosStrength,
        BigDecimal chochStrength,
        BigDecimal orderBlockStrength,
        BigDecimal fvgStrength,
        BigDecimal liquidityProximity,

        // Market Context Features
        BigDecimal trendStrength1h,
        BigDecimal trendStrength15m,
        BigDecimal trendStrength4h,
        BigDecimal volatility1h,
        BigDecimal volatility15m,
        BigDecimal volumeProfile,
        BigDecimal momentum1h,
        BigDecimal momentum15m,

        // Setup Geometry
        BigDecimal riskReward,
        BigDecimal riskDistance,
        BigDecimal entryPrecision,

        // Account/Risk Context
        BigDecimal accountUtilization,
        BigDecimal leverageRatio,

        // Multi-timeframe Regime
        String regime1h,
        String regime15m,
        String regime4h,
        boolean regimeAlignment
) {
    /**
     * Validates that all required features are present and within bounds.
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
     * Returns a feature summary for logging/audit.
     */
    public String toFeatureSummary() {
        return String.format(
                "{\"setupId\":\"%s\",\"symbol\":\"%s\",\"direction\":\"%s\",\"bosStrength\":%s,\"chochStrength\":%s,\"obStrength\":%s,\"fvgStrength\":%s,\"liqProximity\":%s,\"trend1h\":%s,\"trend15m\":%s,\"trend4h\":%s,\"vol1h\":%s,\"vol15m\":%s,\"volProfile\":%s,\"mom1h\":%s,\"mom15m\":%s,\"rr\":%s,\"riskDist\":%s,\"entryPrec\":%s,\"accUtil\":%s,\"levRatio\":%s,\"regime1h\":\"%s\",\"regime15m\":\"%s\",\"regime4h\":\"%s\",\"regimeAlign\":%b}",
                setupId, symbol, direction,
                bosStrength, chochStrength, orderBlockStrength, fvgStrength, liquidityProximity,
                trendStrength1h, trendStrength15m, trendStrength4h,
                volatility1h, volatility15m, volumeProfile, momentum1h, momentum15m,
                riskReward, riskDistance, entryPrecision,
                accountUtilization, leverageRatio,
                regime1h, regime15m, regime4h, regimeAlignment
        );
    }
}