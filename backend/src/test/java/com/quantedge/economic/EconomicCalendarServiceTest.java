package com.quantedge.economic;

import com.quantedge.economic.entity.EconomicEvent;
import com.quantedge.economic.provider.EconomicCalendarProvider;
import com.quantedge.economic.repository.EconomicEventRepository;
import com.quantedge.economic.service.EconomicCalendarService;
import com.quantedge.economic.service.EconomicRetentionCleanupService;
import com.quantedge.notification.service.NotificationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 8: Economic Calendar Synchronization & 24h Retention Tests")
class EconomicCalendarServiceTest {

    @Mock private EconomicCalendarProvider calendarProvider;
    @Mock private EconomicEventRepository eventRepository;
    @Mock private NotificationService notificationService;

    private EconomicCalendarService calendarService;
    private EconomicRetentionCleanupService cleanupService;

    @BeforeEach
    void setUp() {
        calendarService = new EconomicCalendarService(calendarProvider, eventRepository, notificationService);
        cleanupService = new EconomicRetentionCleanupService(eventRepository);
    }

    @Test
    @DisplayName("Synchronizes new upcoming 15-day macroeconomic event")
    void synchronizesNewEvent() {
        Instant now = Instant.now();
        Instant scheduledAt = now.plus(48, ChronoUnit.HOURS);

        EconomicEvent event = new EconomicEvent(
                "US Core CPI (MoM)", "US", "USD", "INFLATION", "CRITICAL",
                scheduledAt, "0.3%", "0.2%", null, "UPCOMING",
                "US BLS", "https://bls.gov", "US_CPI_TEST", null
        );

        when(calendarProvider.fetchUpcomingEvents(any(), any())).thenReturn(List.of(event));
        when(eventRepository.findByProviderEventId("US_CPI_TEST")).thenReturn(Optional.empty());
        when(eventRepository.save(any(EconomicEvent.class))).thenAnswer(inv -> inv.getArgument(0));

        int count = calendarService.syncEconomicCalendar();

        assertThat(count).isEqualTo(1);

        ArgumentCaptor<EconomicEvent> captor = ArgumentCaptor.forClass(EconomicEvent.class);
        verify(eventRepository).save(captor.capture());
        EconomicEvent saved = captor.getValue();

        // Stamped with 24-hour post-event retention
        assertThat(saved.getExpiresAt()).isNotNull();
        long hoursDiff = ChronoUnit.HOURS.between(saved.getScheduledAt(), saved.getExpiresAt());
        assertThat(hoursDiff).isEqualTo(24);
    }

    @Test
    @DisplayName("Updates existing event with actual value release and triggers notification")
    void updatesActualValueAndNotifies() {
        Instant now = Instant.now();
        Instant scheduledAt = now.minus(1, ChronoUnit.HOURS);

        EconomicEvent existing = new EconomicEvent(
                "US Core CPI (MoM)", "US", "USD", "INFLATION", "CRITICAL",
                scheduledAt, "0.3%", "0.2%", null, "UPCOMING",
                "US BLS", "https://bls.gov", "US_CPI_TEST", scheduledAt.plus(24, ChronoUnit.HOURS)
        );

        EconomicEvent update = new EconomicEvent(
                "US Core CPI (MoM)", "US", "USD", "INFLATION", "CRITICAL",
                scheduledAt, "0.3%", "0.2%", "0.4%", "COMPLETED",
                "US BLS", "https://bls.gov", "US_CPI_TEST", scheduledAt.plus(24, ChronoUnit.HOURS)
        );

        when(calendarProvider.fetchUpcomingEvents(any(), any())).thenReturn(List.of(update));
        when(eventRepository.findByProviderEventId("US_CPI_TEST")).thenReturn(Optional.of(existing));

        int count = calendarService.syncEconomicCalendar();

        assertThat(count).isEqualTo(1);
        assertThat(existing.getActualValue()).isEqualTo("0.4%");
        assertThat(existing.getStatus()).isEqualTo("COMPLETED");

        verify(eventRepository).save(existing);
        verify(notificationService).createNotification(
                isNull(),
                eq("ECONOMIC_EVENT_ALERT"),
                contains("US Core CPI"),
                contains("Actual: 0.4%"),
                any(),
                eq("CRITICAL")
        );
    }

    @Test
    @DisplayName("Cleans up expired economic events past 24-hour post-event retention")
    void cleansUpExpiredEvents() {
        when(eventRepository.deleteExpiredEvents(any(Instant.class))).thenReturn(3);

        int deleted = cleanupService.cleanupExpiredEvents();

        assertThat(deleted).isEqualTo(3);
        verify(eventRepository).deleteExpiredEvents(any(Instant.class));
    }
}
