package com.quantedge.news.service;

import com.quantedge.news.repository.NewsArticleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

/**
 * Scheduled cleanup service enforcing the 7-day news retention policy.
 * Permanently removes expired news records from the database.
 */
@Service
public class NewsRetentionCleanupService {

    private static final Logger log = LoggerFactory.getLogger(NewsRetentionCleanupService.class);

    private final NewsArticleRepository newsRepository;

    public NewsRetentionCleanupService(NewsArticleRepository newsRepository) {
        this.newsRepository = newsRepository;
    }

    /**
     * Hourly scheduled job purging news articles past their 7-day retention expiry date.
     */
    @Scheduled(cron = "${quantedge.cleanup.news-retention-cron:0 0 * * * *}")
    @Transactional
    public int cleanupExpiredNews() {
        Instant now = Instant.now();
        int deleted = newsRepository.deleteExpiredArticles(now);
        if (deleted > 0) {
            log.info("News Retention Cleanup: Purged {} expired articles older than 7 days", deleted);
        }
        return deleted;
    }
}
