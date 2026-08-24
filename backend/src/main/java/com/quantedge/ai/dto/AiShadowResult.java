package com.quantedge.ai.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Arrays;

/**
 * Structured container for Phase G AI Shadow Inference.
 *
 * <p>Invariant:
 * In shadow mode (and whenever model promotion is REJECTED),
 * {@code executionAuthorized} MUST strictly be {@code false}.
 * An accepted prediction (predictedRealizedR >= threshold) NEVER confers
 * live execution authority.</p>
 */
public record AiShadowResult(
        String symbol,
        Instant candleTimestamp,
        Instant setupTimestamp,
        String setupDirection,
        String obIdentifier,
        String modelName,
        String modelVersion,
        String modelArtifactHash,
        String featureContractVersion,
        float[] featureVector,
        BigDecimal predictedRealizedR,
        BigDecimal predictedMfeR,
        BigDecimal predictedMaeR,
        BigDecimal threshold,
        boolean predictionAccepted,
        String governanceStatus,
        boolean executionAuthorized
) {
    public AiShadowResult {
        if (featureVector != null) {
            featureVector = Arrays.copyOf(featureVector, featureVector.length);
        }
        if (executionAuthorized && !"PROMOTED".equalsIgnoreCase(governanceStatus)) {
            throw new IllegalStateException("Execution cannot be authorized when governanceStatus is " + governanceStatus);
        }
    }

    @Override
    public float[] featureVector() {
        return featureVector != null ? Arrays.copyOf(featureVector, featureVector.length) : null;
    }
}
