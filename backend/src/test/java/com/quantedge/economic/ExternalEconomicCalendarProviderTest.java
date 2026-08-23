package com.quantedge.economic;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.economic.entity.EconomicEvent;
import com.quantedge.economic.provider.ExternalEconomicCalendarProvider;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ExternalEconomicCalendarProviderTest {

    private RestTemplate restTemplate;
    private ObjectMapper objectMapper;
    private ExternalEconomicCalendarProvider provider;

    @BeforeEach
    void setUp() {
        restTemplate = mock(RestTemplate.class);
        objectMapper = new ObjectMapper();
        provider = new ExternalEconomicCalendarProvider("https://nfs.faireconomy.media/", "", restTemplate, objectMapper);
    }

    @Test
    @DisplayName("Provider: Successfully parses ForexFactory JSON macroeconomic calendar feed")
    void testParseEconomicCalendarSuccess() {
        String json = """
                [
                    {
                        "title": "Core CPI m/m",
                        "country": "USD",
                        "date": "2026-08-25T08:30:00-04:00",
                        "impact": "High",
                        "forecast": "0.2%",
                        "previous": "0.3%",
                        "actual": ""
                    },
                    {
                        "title": "ECB Main Refinancing Rate",
                        "country": "EUR",
                        "date": "2026-08-27T14:15:00+02:00",
                        "impact": "High",
                        "forecast": "4.00%",
                        "previous": "4.25%",
                        "actual": "4.00%"
                    },
                    {
                        "title": "RBI Repo Rate",
                        "country": "INR",
                        "date": "2026-08-28T10:00:00+05:30",
                        "impact": "High",
                        "forecast": "6.50%",
                        "previous": "6.50%",
                        "actual": ""
                    }
                ]
                """;

        when(restTemplate.getForEntity(anyString(), eq(String.class)))
                .thenReturn(new ResponseEntity<>(json, HttpStatus.OK));

        Instant from = Instant.now().minus(24, ChronoUnit.HOURS);
        Instant to = Instant.now().plus(15, ChronoUnit.DAYS);

        List<EconomicEvent> events = provider.fetchUpcomingEvents(from, to);

        assertThat(events).hasSize(3);

        EconomicEvent cpi = events.get(0);
        assertThat(cpi.getEventName()).isEqualTo("Core CPI m/m");
        assertThat(cpi.getCountry()).isEqualTo("US");
        assertThat(cpi.getCurrency()).isEqualTo("USD");
        assertThat(cpi.getCategory()).isEqualTo("INFLATION");
        assertThat(cpi.getImportance()).isEqualTo("HIGH");
        assertThat(cpi.getForecastValue()).isEqualTo("0.2%");
        assertThat(cpi.getPreviousValue()).isEqualTo("0.3%");
        assertThat(cpi.getActualValue()).isNull();
        assertThat(cpi.getStatus()).isEqualTo("UPCOMING");
        assertThat(cpi.getProviderEventId()).contains("US_core_cpi_m_m");

        EconomicEvent ecb = events.get(1);
        assertThat(ecb.getEventName()).isEqualTo("ECB Main Refinancing Rate");
        assertThat(ecb.getCountry()).isEqualTo("EU");
        assertThat(ecb.getCurrency()).isEqualTo("EUR");
        assertThat(ecb.getCategory()).isEqualTo("CENTRAL_BANK");
        assertThat(ecb.getStatus()).isEqualTo("COMPLETED");
        assertThat(ecb.getActualValue()).isEqualTo("4.00%");
        // Completed event retention uses authoritative release time (scheduledAt) + 24h
        assertThat(ecb.getExpiresAt()).isEqualTo(ecb.getScheduledAt().plus(24, ChronoUnit.HOURS));

        EconomicEvent rbi = events.get(2);
        assertThat(rbi.getEventName()).isEqualTo("RBI Repo Rate");
        assertThat(rbi.getCountry()).isEqualTo("IN");
        assertThat(rbi.getCurrency()).isEqualTo("INR");
    }

    @Test
    @DisplayName("Retention: Completed event with explicit released_at field uses released_at + 24h")
    void testCompletedEventWithExplicitReleasedAt() {
        String json = """
                [
                    {
                        "title": "US Non-Farm Payrolls",
                        "country": "USD",
                        "date": "2026-08-25T08:30:00-04:00",
                        "released_at": "2026-08-25T08:31:15-04:00",
                        "impact": "High",
                        "forecast": "165K",
                        "previous": "175K",
                        "actual": "180K"
                    }
                ]
                """;

        when(restTemplate.getForEntity(anyString(), eq(String.class)))
                .thenReturn(new ResponseEntity<>(json, HttpStatus.OK));

        List<EconomicEvent> events = provider.fetchUpcomingEvents(null, null);

        assertThat(events).hasSize(1);
        EconomicEvent nfp = events.get(0);
        assertThat(nfp.getStatus()).isEqualTo("COMPLETED");

        Instant expectedReleasedAt = Instant.parse("2026-08-25T12:31:15Z");
        assertThat(nfp.getExpiresAt()).isEqualTo(expectedReleasedAt.plus(24, ChronoUnit.HOURS));
    }

    @Test
    @DisplayName("Timezone: Parses ISO-8601 offset strings into accurate UTC Instants")
    void testTimezoneParsing() {
        String nyTime = "2026-08-25T08:30:00-04:00"; // 12:30:00 UTC
        Instant utc = ExternalEconomicCalendarProvider.parseUtcTimestamp(nyTime);

        assertThat(utc).isNotNull();
        assertThat(utc.toString()).isEqualTo("2026-08-25T12:30:00Z");
    }

    @Test
    @DisplayName("Deduplication: Generates deterministic providerEventId based on country, title, and timestamp")
    void testProviderEventIdGeneration() {
        Instant ts = Instant.parse("2026-08-25T12:30:00Z");
        String id1 = ExternalEconomicCalendarProvider.generateProviderEventId("US", "Core CPI m/m", ts);
        String id2 = ExternalEconomicCalendarProvider.generateProviderEventId("US", "Core CPI m/m", ts);

        assertThat(id1).isEqualTo(id2);
        assertThat(id1).startsWith("US_core_cpi_m_m_");
    }

    @Test
    @DisplayName("Resilience: Handles network failures gracefully by returning empty list")
    void testNetworkFailureHandling() {
        when(restTemplate.getForEntity(anyString(), eq(String.class)))
                .thenThrow(new RuntimeException("Connection refused by economic calendar provider"));

        List<EconomicEvent> events = provider.fetchUpcomingEvents(Instant.now(), Instant.now().plus(15, ChronoUnit.DAYS));

        assertThat(events).isNotNull().isEmpty();
    }
}
