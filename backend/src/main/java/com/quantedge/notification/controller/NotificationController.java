package com.quantedge.notification.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.notification.dto.NotificationEventDto;
import com.quantedge.notification.service.NotificationService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * REST API Controller for In-App Notifications.
 */
@RestController
@RequestMapping("/api/v1/notifications")
public class NotificationController {

    private final NotificationService notificationService;

    public NotificationController(NotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @GetMapping
    public ResponseEntity<List<NotificationEventDto>> getNotifications(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "unreadOnly", required = false, defaultValue = "false") boolean unreadOnly,
            @RequestParam(value = "limit", required = false, defaultValue = "50") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<NotificationEventDto> list = notificationService.getNotifications(effectiveUser, unreadOnly, limit);
        return ResponseEntity.ok(list);
    }

    @PostMapping("/{id}/read")
    public ResponseEntity<Map<String, Object>> markAsRead(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("id") String notificationId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        notificationService.markAsRead(effectiveUser, notificationId);
        return ResponseEntity.ok(Map.of("success", true, "id", notificationId));
    }

    @PostMapping("/read-all")
    public ResponseEntity<Map<String, Object>> markAllAsRead(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser
    ) {
        User effectiveUser = user != null ? user : requestUser;
        int count = notificationService.markAllAsRead(effectiveUser);
        return ResponseEntity.ok(Map.of("success", true, "markedCount", count));
    }
}
