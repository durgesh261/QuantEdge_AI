package com.quantedge.economic.entity;

import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.time.Instant;

/**
 * Authoritative Economic Event entity covering major macroeconomic indicators
 * with a continuous ~15-day upcoming window and 24-hour post-event retention policy.
 */
@Entity
@Table(name = "economic_events", indexes = {
        @Index(name = "idx_economic_scheduled_at", columnList = "scheduled_at"),
        @Index(name = "idx_economic_expires_at", columnList = "expires_at"),
        @Index(name = "idx_economic_country", columnList = "country"),
        @Index(name = "idx_economic_currency", columnList = "currency"),
        @Index(name = "idx_economic_importance", columnList = "importance"),
        @Index(name = "idx_economic_status", columnList = "status"),
        @Index(name = "idx_economic_provider_id", columnList = "provider_event_id", unique = true)
})
public class EconomicEvent extends BaseEntity {

    @Column(name = "event_name", nullable = false, length = 255)
    private String eventName;

    @Column(name = "country", nullable = false, length = 10)
    private String country;

    @Column(name = "currency", nullable = false, length = 10)
    private String currency;

    @Column(name = "category", nullable = false, length = 50)
    private String category;

    @Column(name = "importance", nullable = false, length = 20)
    private String importance = "MEDIUM";

    @Column(name = "scheduled_at", nullable = false)
    private Instant scheduledAt;

    @Column(name = "previous_value", length = 50)
    private String previousValue;

    @Column(name = "forecast_value", length = 50)
    private String forecastValue;

    @Column(name = "actual_value", length = 50)
    private String actualValue;

    @Column(name = "status", nullable = false, length = 30)
    private String status = "UPCOMING";

    @Column(name = "source", length = 100)
    private String source;

    @Column(name = "source_url", length = 1000)
    private String sourceUrl;

    @Column(name = "provider_event_id", unique = true, length = 100)
    private String providerEventId;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    public EconomicEvent() {}

    public EconomicEvent(
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
            String providerEventId,
            Instant expiresAt
    ) {
        this.eventName = eventName;
        this.country = country;
        this.currency = currency;
        this.category = category;
        this.importance = importance;
        this.scheduledAt = scheduledAt;
        this.previousValue = previousValue;
        this.forecastValue = forecastValue;
        this.actualValue = actualValue;
        this.status = status != null ? status : "UPCOMING";
        this.source = source;
        this.sourceUrl = sourceUrl;
        this.providerEventId = providerEventId;
        this.expiresAt = expiresAt;
    }

    public String getEventName() { return eventName; }
    public void setEventName(String eventName) { this.eventName = eventName; }

    public String getCountry() { return country; }
    public void setCountry(String country) { this.country = country; }

    public String getCurrency() { return currency; }
    public void setCurrency(String currency) { this.currency = currency; }

    public String getCategory() { return category; }
    public void setCategory(String category) { this.category = category; }

    public String getImportance() { return importance; }
    public void setImportance(String importance) { this.importance = importance; }

    public Instant getScheduledAt() { return scheduledAt; }
    public void setScheduledAt(Instant scheduledAt) { this.scheduledAt = scheduledAt; }

    public String getPreviousValue() { return previousValue; }
    public void setPreviousValue(String previousValue) { this.previousValue = previousValue; }

    public String getForecastValue() { return forecastValue; }
    public void setForecastValue(String forecastValue) { this.forecastValue = forecastValue; }

    public String getActualValue() { return actualValue; }
    public void setActualValue(String actualValue) { this.actualValue = actualValue; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }

    public String getSourceUrl() { return sourceUrl; }
    public void setSourceUrl(String sourceUrl) { this.sourceUrl = sourceUrl; }

    public String getProviderEventId() { return providerEventId; }
    public void setProviderEventId(String providerEventId) { this.providerEventId = providerEventId; }

    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant expiresAt) { this.expiresAt = expiresAt; }
}
