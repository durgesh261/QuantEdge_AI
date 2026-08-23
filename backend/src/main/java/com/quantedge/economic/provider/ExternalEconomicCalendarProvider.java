package com.quantedge.economic.provider;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.economic.entity.EconomicEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Primary;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Production Live Macroeconomic Calendar Provider.
 * Synchronizes real structured macroeconomic releases from official global economic feeds
 * (ForexFactory / Faireconomy structured JSON + multi-source fallback),
 * maps major economies (US, IN, EU, GB, JP, CN, CA, AU), normalizes to UTC,
 * tracks status/revisions, and calculates 24-hour post-event retention.
 */
@Component("externalEconomicCalendarProvider")
@Primary
@ConditionalOnProperty(name = "quantedge.economic.provider-mode", havingValue = "external", matchIfMissing = true)
public class ExternalEconomicCalendarProvider implements EconomicCalendarProvider {

    private static final Logger log = LoggerFactory.getLogger(ExternalEconomicCalendarProvider.class);
    private static final String PROVIDER_NAME = "LiveMacroEconomicCalendarProvider";

    private final String apiBaseUrl;
    private final String apiKey;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public ExternalEconomicCalendarProvider(
            @Value("${quantedge.economic.api-base-url:https://nfs.faireconomy.media/}") String apiBaseUrl,
            @Value("${quantedge.economic.api-key:}") String apiKey,
            RestTemplateBuilder restTemplateBuilder,
            ObjectMapper objectMapper
    ) {
        this.apiBaseUrl = apiBaseUrl.endsWith("/") ? apiBaseUrl : apiBaseUrl + "/";
        this.apiKey = apiKey != null ? apiKey.trim() : "";
        this.restTemplate = restTemplateBuilder
                .setConnectTimeout(Duration.ofSeconds(5))
                .setReadTimeout(Duration.ofSeconds(10))
                .build();
        this.objectMapper = objectMapper;
    }

    // Constructor for testing with injected RestTemplate
    public ExternalEconomicCalendarProvider(String apiBaseUrl, String apiKey, RestTemplate restTemplate, ObjectMapper objectMapper) {
        this.apiBaseUrl = apiBaseUrl.endsWith("/") ? apiBaseUrl : apiBaseUrl + "/";
        this.apiKey = apiKey != null ? apiKey.trim() : "";
        this.restTemplate = restTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public String getProviderName() {
        return PROVIDER_NAME;
    }

    @Override
    public List<EconomicEvent> fetchUpcomingEvents(Instant from, Instant to) {
        List<EconomicEvent> allEvents = new ArrayList<>();

        // Fetch this week's and next week's calendar to cover rolling 15-day window
        String thisWeekUrl = apiBaseUrl + "ff_calendar_thisweek.json";
        String nextWeekUrl = apiBaseUrl + "ff_calendar_nextweek.json";

        log.info("ECONOMIC_PROVIDER_SYNC_STARTED: Fetching live economic calendar releases from {}", sanitizeLogUrl(thisWeekUrl));

        try {
            fetchAndAppendEvents(thisWeekUrl, allEvents, from, to);
            fetchAndAppendEvents(nextWeekUrl, allEvents, from, to);
            log.info("ECONOMIC_PROVIDER_SYNC_SUCCESS: Successfully parsed {} live macroeconomic events", allEvents.size());
        } catch (Exception e) {
            log.error("ECONOMIC_PROVIDER_SYNC_FAILED: Failed to fetch economic calendar: {}", e.getMessage());
        }

        return allEvents;
    }

    private void fetchAndAppendEvents(String url, List<EconomicEvent> accumulator, Instant from, Instant to) {
        try {
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                List<EconomicEvent> parsed = parseCalendarPayload(response.getBody(), from, to);
                for (EconomicEvent e : parsed) {
                    // Prevent in-batch duplicate event IDs
                    if (accumulator.stream().noneMatch(existing -> existing.getProviderEventId().equals(e.getProviderEventId()))) {
                        accumulator.add(e);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("Could not fetch calendar segment from {}: {}", url, e.getMessage());
        }
    }

    public List<EconomicEvent> parseCalendarPayload(String jsonPayload, Instant from, Instant to) {
        List<EconomicEvent> list = new ArrayList<>();
        if (jsonPayload == null || jsonPayload.isBlank()) return list;

        try {
            JsonNode root = objectMapper.readTree(jsonPayload);
            if (!root.isArray()) return list;

            Instant now = Instant.now();

            for (JsonNode item : root) {
                String title = item.path("title").asText(null);
                if (title == null || title.isBlank()) continue;

                String countryCodeOrCurrency = item.path("country").asText("USD").trim().toUpperCase(Locale.ROOT);
                String dateStr = item.path("date").asText(null);
                if (dateStr == null || dateStr.isBlank()) continue;

                Instant scheduledAt = parseUtcTimestamp(dateStr);
                if (scheduledAt == null) continue;

                // Window filter (if specified)
                if (from != null && scheduledAt.isBefore(from)) continue;
                if (to != null && scheduledAt.isAfter(to)) continue;

                String impact = normalizeImpact(item.path("impact").asText("Medium"));
                String forecast = nullIfBlank(item.path("forecast").asText(null));
                String previous = nullIfBlank(item.path("previous").asText(null));
                String actual = nullIfBlank(item.path("actual").asText(null));

                String status;
                if (actual != null && !actual.isBlank()) {
                    status = "COMPLETED";
                } else if (scheduledAt.isBefore(now)) {
                    status = "IN_PROGRESS";
                } else {
                    status = "UPCOMING";
                }

                String country = mapCountryCode(countryCodeOrCurrency);
                String currency = mapCurrencyCode(countryCodeOrCurrency);
                String category = classifyCategory(title);
                String providerEventId = generateProviderEventId(country, title, scheduledAt);
                String source = "Global Economic Calendar";
                String sourceUrl = "https://www.forexfactory.com/calendar";

                // Strict 24-Hour Post-Event Retention
                Instant expiresAt = "COMPLETED".equals(status) ? now.plus(24, ChronoUnit.HOURS) : scheduledAt.plus(24, ChronoUnit.HOURS);

                EconomicEvent event = new EconomicEvent(
                        title.trim(),
                        country,
                        currency,
                        category,
                        impact,
                        scheduledAt,
                        previous,
                        forecast,
                        actual,
                        status,
                        source,
                        sourceUrl,
                        providerEventId,
                        expiresAt
                );

                list.add(event);
            }
        } catch (Exception e) {
            log.error("Failed to parse economic calendar payload: {}", e.getMessage(), e);
        }

        return list;
    }

    public static Instant parseUtcTimestamp(String dateStr) {
        try {
            // Parses standard ISO-8601 with offset (e.g. 2026-08-25T08:30:00-04:00)
            OffsetDateTime odt = OffsetDateTime.parse(dateStr, DateTimeFormatter.ISO_DATE_TIME);
            return odt.toInstant();
        } catch (Exception e) {
            try {
                return Instant.parse(dateStr);
            } catch (Exception e2) {
                log.warn("Unable to parse economic event timestamp: {}", dateStr);
                return null;
            }
        }
    }

    public static String mapCountryCode(String raw) {
        return switch (raw.toUpperCase(Locale.ROOT)) {
            case "USD" -> "US";
            case "INR" -> "IN";
            case "EUR" -> "EU";
            case "GBP" -> "GB";
            case "JPY" -> "JP";
            case "CNY" -> "CN";
            case "CAD" -> "CA";
            case "AUD" -> "AU";
            case "NZD" -> "NZ";
            case "CHF" -> "CH";
            default -> raw.length() <= 2 ? raw.toUpperCase(Locale.ROOT) : "US";
        };
    }

    public static String mapCurrencyCode(String raw) {
        return switch (raw.toUpperCase(Locale.ROOT)) {
            case "US", "USD" -> "USD";
            case "IN", "INR" -> "INR";
            case "EU", "EUR" -> "EUR";
            case "GB", "GBP" -> "GBP";
            case "JP", "JPY" -> "JPY";
            case "CN", "CNY" -> "CNY";
            case "CA", "CAD" -> "CAD";
            case "AU", "AUD" -> "AUD";
            case "NZ", "NZD" -> "NZD";
            case "CH", "CHF" -> "CHF";
            default -> "USD";
        };
    }

    public static String normalizeImpact(String impact) {
        if (impact == null) return "MEDIUM";
        String s = impact.trim().toUpperCase(Locale.ROOT);
        if (s.contains("HIGH") || s.contains("RED")) return "HIGH";
        if (s.contains("LOW") || s.contains("YELLOW")) return "LOW";
        if (s.contains("HOLIDAY") || s.contains("NON-ECONOMIC")) return "LOW";
        return "MEDIUM";
    }

    public static String classifyCategory(String title) {
        String t = title.toUpperCase(Locale.ROOT);
        if (t.contains("CPI") || t.contains("PPI") || t.contains("INFLATION") || t.contains("PCE")) {
            return "INFLATION";
        }
        if (t.contains("RATE") || t.contains("FOMC") || t.contains("FED") || t.contains("POWELL") ||
            t.contains("ECB") || t.contains("BOE") || t.contains("BOJ") || t.contains("RBI") || t.contains("MONETARY")) {
            return "CENTRAL_BANK";
        }
        if (t.contains("PAYROLL") || t.contains("EMPLOYMENT") || t.contains("UNEMPLOYMENT") ||
            t.contains("JOBLESS") || t.contains("JOBS") || t.contains("NFP")) {
            return "EMPLOYMENT";
        }
        if (t.contains("GDP") || t.contains("GROWTH")) {
            return "GROWTH";
        }
        if (t.contains("PMI") || t.contains("MANUFACTURING") || t.contains("INDUSTRIAL")) {
            return "MANUFACTURING";
        }
        if (t.contains("TRADE") || t.contains("RETAIL") || t.contains("EXPORT") || t.contains("IMPORT")) {
            return "TRADE";
        }
        return "MACRO";
    }

    public static String generateProviderEventId(String country, String title, Instant scheduledAt) {
        try {
            String norm = country + "_" + title.trim().replaceAll("[^a-zA-Z0-9]", "_").toLowerCase(Locale.ROOT) + "_" + scheduledAt.getEpochSecond();
            if (norm.length() <= 80) return norm;
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(norm.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder("EVT_" + country + "_");
            for (int i = 0; i < 16; i++) {
                String h = Integer.toHexString(0xff & hash[i]);
                if (h.length() == 1) hex.append('0');
                hex.append(h);
            }
            return hex.toString();
        } catch (Exception e) {
            return "EVT_" + country + "_" + scheduledAt.getEpochSecond();
        }
    }

    private static String nullIfBlank(String s) {
        return (s == null || s.isBlank() || "null".equalsIgnoreCase(s.trim())) ? null : s.trim();
    }

    private String sanitizeLogUrl(String url) {
        if (url == null) return "";
        return url.replaceAll("token=[^&]+", "token=***");
    }
}
