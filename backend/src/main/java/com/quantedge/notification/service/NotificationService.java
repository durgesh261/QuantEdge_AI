package com.quantedge.notification.service;

import com.quantedge.auth.entity.User;
import com.quantedge.common.exception.ResourceNotFoundException;
import com.quantedge.notification.dto.NotificationEventDto;
import com.quantedge.notification.entity.NotificationEvent;
import com.quantedge.notification.repository.NotificationEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Service managing user notifications and system broadcast alerts.
 */
@Service
public class NotificationService {

    private static final Logger log = LoggerFactory.getLogger(NotificationService.class);

    private final NotificationEventRepository notificationRepository;

    public NotificationService(NotificationEventRepository notificationRepository) {
        this.notificationRepository = notificationRepository;
    }

    /**
     * Dispatches a notification event with automatic deduplication.
     */
    @Transactional
    public NotificationEventDto createNotification(User user, String type, String title, String message, String referenceId, String severity) {
        if (referenceId != null && notificationRepository.existsByReferenceIdAndType(referenceId, type)) {
            log.debug("Skipping duplicate notification for refId: {}, type: {}", referenceId, type);
            return null;
        }

        NotificationEvent event = new NotificationEvent(user, type, title, message, referenceId, severity);
        NotificationEvent saved = notificationRepository.save(event);
        log.info("Dispatched notification [{}]: {}", type, title);
        return NotificationEventDto.fromEntity(saved);
    }

    /**
     * Retrieves notifications for the authenticated user.
     */
    @Transactional(readOnly = true)
    public List<NotificationEventDto> getNotifications(User user, boolean unreadOnly, int limit) {
        String userId = user != null ? user.getId() : null;
        List<NotificationEvent> list = notificationRepository.findForUser(userId, unreadOnly);
        return list.stream()
                .limit(limit > 0 ? limit : 50)
                .map(NotificationEventDto::fromEntity)
                .toList();
    }

    /**
     * Marks a specific notification as read ensuring user ownership.
     */
    @Transactional
    public void markAsRead(User user, String notificationId) {
        NotificationEvent event = notificationRepository.findById(notificationId)
                .orElseThrow(() -> new ResourceNotFoundException("Notification not found: " + notificationId));

        if (event.getUser() != null && (user == null || !event.getUser().getId().equals(user.getId()))) {
            throw new AccessDeniedException("Access denied: You do not own this notification");
        }

        event.setRead(true);
        notificationRepository.save(event);
    }

    /**
     * Marks all notifications as read for the user.
     */
    @Transactional
    public int markAllAsRead(User user) {
        String userId = user != null ? user.getId() : null;
        return notificationRepository.markAllAsReadForUser(userId);
    }
}
