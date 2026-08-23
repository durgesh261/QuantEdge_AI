package com.quantedge.notification.dto;

import com.quantedge.notification.entity.NotificationEvent;

import java.time.Instant;

/**
 * Sanitized Notification Event DTO for frontend event stream.
 */
public record NotificationEventDto(
        String id,
        String type,
        String title,
        String message,
        String referenceId,
        String severity,
        boolean isRead,
        Instant createdAt
) {
    public static NotificationEventDto fromEntity(NotificationEvent entity) {
        if (entity == null) return null;
        return new NotificationEventDto(
                entity.getId(),
                entity.getType(),
                entity.getTitle(),
                entity.getMessage(),
                entity.getReferenceId(),
                entity.getSeverity(),
                entity.isRead(),
                entity.getCreatedAt()
        );
    }
}
