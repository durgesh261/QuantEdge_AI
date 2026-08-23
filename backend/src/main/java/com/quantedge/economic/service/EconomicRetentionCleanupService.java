package com.quantedge.economic.service;

import com.quantedge.economic.repository.EconomicEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

/**
 * Scheduled cleanup service enforcing the 24-hour post-event retention policy.
 * Permanently purges completed and expired macroeconomic events.
 */
@Service
public class EconomicRetentionCleanupService {

    private static final Logger log = LoggerFactory.getLogger(EconomicRetentionCleanupService.class);

    private final EconomicEventRepository eventRepository;

    public EconomicRetentionCleanupService(EconomicEventRepository eventRepository) {
        this.eventRepository = eventRepository;
    }

    /**
     * Hourly scheduled job purging events past their 24-hour post-event expiration date.
     */
    @Scheduled(cron = "${quantedge.cleanup.economic-retention-cron:0 0 * * * *}")
    @Transactional
    public int cleanupExpiredEvents() {
        Instant now = Instant.now();
        int deleted = eventRepository.deleteExpiredEvents(now);
        if (deleted > 0) {
            log.info("Economic Retention Cleanup: Purged {} expired events past 24h retention window", deleted);
        }
        return deleted;
    }
}
