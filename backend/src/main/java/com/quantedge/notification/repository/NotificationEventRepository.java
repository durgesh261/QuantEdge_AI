package com.quantedge.notification.repository;

import com.quantedge.notification.entity.NotificationEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface NotificationEventRepository extends JpaRepository<NotificationEvent, String> {

    @Query("SELECT n FROM NotificationEvent n WHERE (n.user.id = :userId OR n.user IS NULL) " +
           "AND (:unreadOnly = false OR n.isRead = false) " +
           "ORDER BY n.createdAt DESC")
    List<NotificationEvent> findForUser(@Param("userId") String userId, @Param("unreadOnly") boolean unreadOnly);

    boolean existsByReferenceIdAndType(String referenceId, String type);

    @Modifying
    @Query("UPDATE NotificationEvent n SET n.isRead = true WHERE n.user.id = :userId OR n.user IS NULL")
    int markAllAsReadForUser(@Param("userId") String userId);
}
