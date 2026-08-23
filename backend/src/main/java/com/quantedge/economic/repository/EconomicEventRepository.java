package com.quantedge.economic.repository;

import com.quantedge.economic.entity.EconomicEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public interface EconomicEventRepository extends JpaRepository<EconomicEvent, String> {

    Optional<EconomicEvent> findByProviderEventId(String providerEventId);

    @Query("SELECT e FROM EconomicEvent e WHERE e.scheduledAt BETWEEN :from AND :to ORDER BY e.scheduledAt ASC")
    List<EconomicEvent> findUpcomingEvents(@Param("from") Instant from, @Param("to") Instant to);

    @Query("SELECT e FROM EconomicEvent e WHERE (:country IS NULL OR e.country = :country) " +
           "AND (:currency IS NULL OR e.currency = :currency) " +
           "AND (:importance IS NULL OR e.importance = :importance) " +
           "AND (:from IS NULL OR e.scheduledAt >= :from) " +
           "AND (:to IS NULL OR e.scheduledAt <= :to) " +
           "ORDER BY e.scheduledAt ASC")
    List<EconomicEvent> findWithFilters(
            @Param("country") String country,
            @Param("currency") String currency,
            @Param("importance") String importance,
            @Param("from") Instant from,
            @Param("to") Instant to
    );

    @Modifying
    @Query("DELETE FROM EconomicEvent e WHERE e.expiresAt <= :now")
    int deleteExpiredEvents(@Param("now") Instant now);
}
