package com.quantedge.ai.dto;

import com.quantedge.ai.entity.AiSignalEnrichment;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized DTO representing AI intelligence metadata for a deterministic SMC setup.
 */
public record AiEnrichmentDto(
        String id,
        String setupId,
        String accountId,
        String symbol,
        String direction,
        String intelligenceVersion,
        BigDecimal patternScore,
        BigDecimal signalScore,
        BigDecimal confidence,
        String marketRegime,
        String marketContext,
        String modelMetadata,
        String featureSummary,
        Instant generatedAt
) {
    public static AiEnrichmentDto fromEntity(AiSignalEnrichment entity) {
        if (entity == null) return null;
        return new AiEnrichmentDto(
                entity.getId(),
                entity.getSetupId(),
                entity.getTradingAccount() != null ? entity.getTradingAccount().getId() : null,
                entity.getSymbol(),
                entity.getDirection(),
                entity.getIntelligenceVersion(),
                entity.getPatternScore(),
                entity.getSignalScore(),
                entity.getConfidence(),
                entity.getMarketRegime(),
                entity.getMarketContext(),
                entity.getModelMetadata(),
                entity.getFeatureSummary(),
                entity.getGeneratedAt()
        );
    }
}
