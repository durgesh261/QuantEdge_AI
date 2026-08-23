package com.quantedge.economic.dto;

import com.quantedge.economic.entity.EconomicEvent;

import java.time.Instant;

/**
 * Sanitized Economic Event DTO for macroeconomic calendar views.
 */
public record EconomicEventDto(
        String id,
        String eventName,
        String country,
        String currency,
        String category,
        String importance,
        Instant scheduledAt,
        String previousValue,
        String forecastValue,
        String actualValue,
        String status,
        String source,
        String sourceUrl,
        Instant expiresAt
) {
    public static EconomicEventDto fromEntity(EconomicEvent entity) {
        if (entity == null) return null;
        return new EconomicEventDto(
                entity.getId(),
                entity.getEventName(),
                entity.getCountry(),
                entity.getCurrency(),
                entity.getCategory(),
                entity.getImportance(),
                entity.getScheduledAt(),
                entity.getPreviousValue(),
                entity.getForecastValue(),
                entity.getActualValue(),
                entity.getStatus(),
                entity.getSource(),
                entity.getSourceUrl(),
                entity.getExpiresAt()
        );
    }
}
