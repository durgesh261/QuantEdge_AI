package com.quantedge.notification;

import com.quantedge.auth.entity.User;
import com.quantedge.notification.dto.NotificationEventDto;
import com.quantedge.notification.entity.NotificationEvent;
import com.quantedge.notification.repository.NotificationEventRepository;
import com.quantedge.notification.service.NotificationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.access.AccessDeniedException;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 8: Notification Service & Tenant Isolation Tests")
class NotificationServiceTest {

    @Mock private NotificationEventRepository notificationRepository;

    private NotificationService notificationService;

    @BeforeEach
    void setUp() {
        notificationService = new NotificationService(notificationRepository);
    }

    private void setId(Object entity, String id) throws Exception {
        Field field = entity.getClass().getSuperclass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(entity, id);
    }

    @Test
    @DisplayName("Dispatches and deduplicates notifications by referenceId and type")
    void dispatchesAndDeduplicates() {
        when(notificationRepository.existsByReferenceIdAndType("REF_123", "NEWS_ALERT")).thenReturn(true);

        NotificationEventDto result = notificationService.createNotification(
                null, "NEWS_ALERT", "Test News", "Summary", "REF_123", "HIGH"
        );

        assertThat(result).isNull();
        verify(notificationRepository, never()).save(any());
    }

    @Test
    @DisplayName("Marks notification as read ensuring tenant ownership")
    void marksAsReadWithTenantCheck() throws Exception {
        User user1 = new User();
        setId(user1, "USER_1");

        User user2 = new User();
        setId(user2, "USER_2");

        NotificationEvent event = new NotificationEvent(user1, "SYSTEM_ALERT", "Alert", "Msg", "REF_1", "INFO");
        setId(event, "NOTIF_1");

        when(notificationRepository.findById("NOTIF_1")).thenReturn(Optional.of(event));

        // User 2 should NOT be allowed to modify User 1's notification (IDOR protection)
        assertThatThrownBy(() -> notificationService.markAsRead(user2, "NOTIF_1"))
                .isInstanceOf(AccessDeniedException.class);

        // User 1 successfully marks as read
        notificationService.markAsRead(user1, "NOTIF_1");
        assertThat(event.isRead()).isTrue();
        verify(notificationRepository).save(event);
    }
}
