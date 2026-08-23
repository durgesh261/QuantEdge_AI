package com.quantedge.notification.entity;

import com.quantedge.auth.entity.User;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.time.Instant;

/**
 * In-app Notification Event entity for critical market news, economic calendar alerts, and system events.
 */
@Entity
@Table(name = "notification_events", indexes = {
        @Index(name = "idx_notifications_user_id", columnList = "user_id"),
        @Index(name = "idx_notifications_type", columnList = "type"),
        @Index(name = "idx_notifications_created_at", columnList = "created_at"),
        @Index(name = "idx_notifications_is_read", columnList = "is_read")
})
public class NotificationEvent extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User user;

    @Column(name = "type", nullable = false, length = 50)
    private String type;

    @Column(name = "title", nullable = false, length = 255)
    private String title;

    @Column(name = "message", nullable = false, columnDefinition = "TEXT")
    private String message;

    @Column(name = "reference_id", length = 100)
    private String referenceId;

    @Column(name = "severity", nullable = false, length = 20)
    private String severity = "INFO";

    @Column(name = "is_read", nullable = false)
    private boolean isRead = false;

    public NotificationEvent() {}

    public NotificationEvent(
            User user,
            String type,
            String title,
            String message,
            String referenceId,
            String severity
    ) {
        this.user = user;
        this.type = type;
        this.title = title;
        this.message = message;
        this.referenceId = referenceId;
        this.severity = severity != null ? severity : "INFO";
        this.isRead = false;
    }

    public User getUser() { return user; }
    public void setUser(User user) { this.user = user; }

    public String getType() { return type; }
    public void setType(String type) { this.type = type; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public String getReferenceId() { return referenceId; }
    public void setReferenceId(String referenceId) { this.referenceId = referenceId; }

    public String getSeverity() { return severity; }
    public void setSeverity(String severity) { this.severity = severity; }

    public boolean isRead() { return isRead; }
    public void setRead(boolean read) { isRead = read; }
}
