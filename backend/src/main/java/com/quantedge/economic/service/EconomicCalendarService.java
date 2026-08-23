package com.quantedge.economic.service;

import com.quantedge.common.exception.ResourceNotFoundException;
import com.quantedge.economic.dto.EconomicEventDto;
import com.quantedge.economic.entity.EconomicEvent;
import com.quantedge.economic.provider.EconomicCalendarProvider;
import com.quantedge.economic.repository.EconomicEventRepository;
import com.quantedge.notification.service.NotificationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;

/**
 * Service managing continuous synchronization of upcoming ~15-day macroeconomic events,
 * dynamic updates of actuals/forecasts, and 24-hour post-event retention stamping.
 */
@Service
public class EconomicCalendarService {

    private static final Logger log = LoggerFactory.getLogger(EconomicCalendarService.class);

    private final EconomicCalendarProvider calendarProvider;
    private final EconomicEventRepository eventRepository;
    private final NotificationService notificationService;

    public EconomicCalendarService(
            EconomicCalendarProvider calendarProvider,
            EconomicEventRepository eventRepository,
            NotificationService notificationService
    ) {
        this.calendarProvider = calendarProvider;
        this.eventRepository = eventRepository;
        this.notificationService = notificationService;
    }

    private volatile Instant lastAttemptedSync = null;
    private volatile Instant lastSuccessfulSync = null;
    private volatile Instant lastErrorTimestamp = null;
    private volatile String lastErrorMessage = null;
    private volatile long totalEventsSynchronized = 0;

    /**
     * Scheduled synchronization job (runs every 2 hours with 10s initial delay).
     */
    @Scheduled(initialDelay = 10000, fixedRateString = "${quantedge.economic.sync-rate-ms:7200000}")
    @Transactional
    public int syncEconomicCalendar() {
        log.info("ECONOMIC_PROVIDER_SYNC_STARTED: Synchronizing economic calendar from provider [{}]", calendarProvider.getProviderName());
        lastAttemptedSync = Instant.now();
        Instant now = Instant.now();
        Instant windowEnd = now.plus(15, ChronoUnit.DAYS); // 15-Day upcoming window
        int updatedCount = 0;

        try {
            List<EconomicEvent> fetched = calendarProvider.fetchUpcomingEvents(now.minus(24, ChronoUnit.HOURS), windowEnd);
            for (EconomicEvent event : fetched) {
                Optional<EconomicEvent> existingOpt = eventRepository.findByProviderEventId(event.getProviderEventId());
                if (existingOpt.isPresent()) {
                    EconomicEvent existing = existingOpt.get();
                    boolean hadNoActual = existing.getActualValue() == null;

                    existing.setEventName(event.getEventName());
                    existing.setCategory(event.getCategory());
                    existing.setImportance(event.getImportance());
                    existing.setPreviousValue(event.getPreviousValue());
                    existing.setForecastValue(event.getForecastValue());
                    existing.setActualValue(event.getActualValue());
                    existing.setStatus(event.getStatus());
                    existing.setScheduledAt(event.getScheduledAt());

                    // Strict 24-Hour Post-Event Retention based on authoritative event release/scheduled time
                    if (event.getExpiresAt() != null) {
                        existing.setExpiresAt(event.getExpiresAt());
                    } else if ("COMPLETED".equalsIgnoreCase(event.getStatus())) {
                        Instant releaseTime = event.getScheduledAt() != null ? event.getScheduledAt() : existing.getScheduledAt();
                        existing.setExpiresAt(releaseTime.plus(24, ChronoUnit.HOURS));
                    } else {
                        existing.setExpiresAt(event.getScheduledAt().plus(24, ChronoUnit.HOURS));
                    }

                    eventRepository.save(existing);
                    updatedCount++;
                    totalEventsSynchronized++;

                    // Dispatch notification when actual value is released for high-impact events
                    if (hadNoActual && event.getActualValue() != null &&
                            ("HIGH".equalsIgnoreCase(event.getImportance()) || "CRITICAL".equalsIgnoreCase(event.getImportance()))) {
                        notificationService.createNotification(
                                null,
                                "ECONOMIC_EVENT_ALERT",
                                "[" + event.getCountry() + " " + event.getCurrency() + "] " + event.getEventName() + " Released",
                                "Actual: " + event.getActualValue() + " (Forecast: " + event.getForecastValue() + ", Previous: " + event.getPreviousValue() + ")",
                                existing.getId(),
                                event.getImportance()
                        );
                    }
                } else {
                    if (event.getExpiresAt() == null) {
                        event.setExpiresAt(event.getScheduledAt().plus(24, ChronoUnit.HOURS));
                    }
                    EconomicEvent saved = eventRepository.save(event);
                    updatedCount++;
                    totalEventsSynchronized++;

                    // If critical upcoming within 24h, dispatch upcoming alert
                    if ("CRITICAL".equalsIgnoreCase(saved.getImportance()) &&
                            saved.getScheduledAt().isAfter(now) &&
                            saved.getScheduledAt().isBefore(now.plus(24, ChronoUnit.HOURS))) {
                        notificationService.createNotification(
                                null,
                                "ECONOMIC_EVENT_ALERT",
                                "Upcoming Critical Event: " + saved.getEventName() + " (" + saved.getCountry() + ")",
                                "Scheduled: " + saved.getScheduledAt() + " | Forecast: " + saved.getForecastValue(),
                                saved.getId(),
                                "CRITICAL"
                        );
                    }
                }
            }
            lastSuccessfulSync = Instant.now();
            lastErrorMessage = null;
            log.info("ECONOMIC_EVENTS_UPDATED: Completed economic calendar sync: {} events synchronized", updatedCount);
        } catch (Exception e) {
            lastErrorTimestamp = Instant.now();
            lastErrorMessage = e.getMessage();
            log.error("ECONOMIC_PROVIDER_SYNC_FAILED: Error during economic calendar sync: {}", e.getMessage(), e);
        }

        return updatedCount;
    }

    /**
     * Returns provider status and health metadata.
     */
    public java.util.Map<String, Object> getProviderStatus() {
        java.util.Map<String, Object> status = new java.util.LinkedHashMap<>();
        status.put("providerName", calendarProvider.getProviderName());
        status.put("enabled", true);
        status.put("lastAttemptedSync", lastAttemptedSync);
        status.put("lastSuccessfulSync", lastSuccessfulSync);
        status.put("lastErrorTimestamp", lastErrorTimestamp);
        status.put("lastErrorMessage", lastErrorMessage);
        status.put("totalEventsSynchronized", totalEventsSynchronized);
        return status;
    }

    /**
     * Retrieves upcoming economic events for the next 15 days.
     */
    @Transactional(readOnly = true)
    public List<EconomicEventDto> getUpcomingEvents(int limit) {
        Instant now = Instant.now();
        Instant windowEnd = now.plus(15, ChronoUnit.DAYS);
        List<EconomicEvent> list = eventRepository.findUpcomingEvents(now.minus(2, ChronoUnit.HOURS), windowEnd);
        return list.stream()
                .limit(limit > 0 ? limit : 50)
                .map(EconomicEventDto::fromEntity)
                .toList();
    }

    /**
     * Queries economic events with optional filters.
     */
    @Transactional(readOnly = true)
    public List<EconomicEventDto> getEvents(String country, String currency, String importance, Instant from, Instant to, int limit) {
        List<EconomicEvent> list = eventRepository.findWithFilters(
                country != null && !country.isBlank() ? country.trim().toUpperCase() : null,
                currency != null && !currency.isBlank() ? currency.trim().toUpperCase() : null,
                importance != null && !importance.isBlank() ? importance.trim().toUpperCase() : null,
                from,
                to
        );
        return list.stream()
                .limit(limit > 0 ? limit : 50)
                .map(EconomicEventDto::fromEntity)
                .toList();
    }

    /**
     * Fetches a single event by ID.
     */
    @Transactional(readOnly = true)
    public EconomicEventDto getEventById(String id) {
        EconomicEvent event = eventRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Economic event not found: " + id));
        return EconomicEventDto.fromEntity(event);
    }
}
