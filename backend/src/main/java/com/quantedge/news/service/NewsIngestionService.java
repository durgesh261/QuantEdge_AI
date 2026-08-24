package com.quantedge.news.service;

import com.quantedge.common.exception.ResourceNotFoundException;
import com.quantedge.news.dto.NewsArticleDto;
import com.quantedge.news.entity.NewsArticle;
import com.quantedge.news.provider.NewsProvider;
import com.quantedge.news.repository.NewsArticleRepository;
import com.quantedge.notification.service.NotificationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.temporal.ChronoUnit;
import java.util.List;

/**
 * Service managing financial news ingestion, deduplication, 7-day retention stamping,
 * and high-importance notification dispatch.
 */
@Service
public class NewsIngestionService {

    private static final Logger log = LoggerFactory.getLogger(NewsIngestionService.class);

    private final NewsProvider newsProvider;
    private final NewsArticleRepository newsRepository;
    private final NotificationService notificationService;

    public NewsIngestionService(
            NewsProvider newsProvider,
            NewsArticleRepository newsRepository,
            NotificationService notificationService
    ) {
        this.newsProvider = newsProvider;
        this.newsRepository = newsRepository;
        this.notificationService = notificationService;
    }

    private volatile java.time.Instant lastAttemptedSync = null;
    private volatile java.time.Instant lastSuccessfulSync = null;
    private volatile java.time.Instant lastErrorTimestamp = null;
    private volatile String lastErrorMessage = null;
    private volatile long totalArticlesIngested = 0;

    /**
     * Scheduled news ingestion job (runs every 30 minutes with 5s initial delay).
     */
    @Scheduled(initialDelay = 5000, fixedRateString = "${quantedge.news.ingest-rate-ms:1800000}")
    @Transactional
    public int ingestNews() {
        log.info("NEWS_PROVIDER_SYNC_STARTED: Ingesting news from provider [{}]", newsProvider.getProviderName());
        lastAttemptedSync = java.time.Instant.now();
        int newArticlesCount = 0;

        try {
            List<NewsArticle> fetched = newsProvider.fetchLatestNews();
            for (NewsArticle article : fetched) {
                if (newsRepository.existsByFingerprint(article.getFingerprint())) {
                    log.debug("NEWS_DUPLICATE_SKIPPED: Skipping duplicate article: {}", article.getTitle());
                    continue;
                }

                // Strictly enforce 7-Day Retention: expires_at = published_at + 7 days
                if (article.getExpiresAt() == null) {
                    article.setExpiresAt(article.getPublishedAt().plus(7, ChronoUnit.DAYS));
                }

                NewsArticle saved = newsRepository.save(article);
                newArticlesCount++;
                totalArticlesIngested++;

                // Dispatch notification for HIGH or CRITICAL news events
                if ("HIGH".equalsIgnoreCase(saved.getImportance()) || "CRITICAL".equalsIgnoreCase(saved.getImportance())) {
                    notificationService.createNotification(
                            null, // Global broadcast to all traders
                            "NEWS_ALERT",
                            "[" + saved.getCategory() + "] " + saved.getTitle(),
                            saved.getSummary(),
                            saved.getId(),
                            saved.getImportance()
                    );
                }
            }
            lastSuccessfulSync = java.time.Instant.now();
            lastErrorMessage = null;
            log.info("NEWS_ARTICLES_INGESTED: Completed news ingestion: {} new articles stored", newArticlesCount);
        } catch (Exception e) {
            lastErrorTimestamp = java.time.Instant.now();
            lastErrorMessage = e.getMessage();
            log.error("NEWS_PROVIDER_SYNC_FAILED: Error during financial news ingestion: {}", e.getMessage(), e);
        }

        return newArticlesCount;
    }

    /**
     * Returns provider status and health metadata.
     */
    public java.util.Map<String, Object> getProviderStatus() {
        java.util.Map<String, Object> status = new java.util.LinkedHashMap<>();
        status.put("providerName", newsProvider.getProviderName());
        status.put("enabled", true);
        status.put("lastAttemptedSync", lastAttemptedSync);
        status.put("lastSuccessfulSync", lastSuccessfulSync);
        status.put("lastErrorTimestamp", lastErrorTimestamp);
        status.put("lastErrorMessage", lastErrorMessage);
        status.put("totalArticlesIngested", totalArticlesIngested);
        return status;
    }

    /**
     * Queries news articles with optional category and importance filters.
     */
    @Transactional(readOnly = true)
    public List<NewsArticleDto> getNewsArticles(String category, String importance, String symbol, int limit) {
        List<NewsArticle> articles = newsRepository.findWithFilters(
                category != null && !category.isBlank() ? category.trim().toUpperCase() : null,
                importance != null && !importance.isBlank() ? importance.trim().toUpperCase() : null,
                java.time.Instant.now()
        );

        return articles.stream()
                .filter(a -> symbol == null || symbol.isBlank() || (a.getRelevantSymbols() != null && a.getRelevantSymbols().toUpperCase().contains(symbol.trim().toUpperCase())))
                .limit(limit > 0 ? limit : 50)
                .map(NewsArticleDto::fromEntity)
                .toList();
    }

    /**
     * Fetches a single article by ID.
     */
    @Transactional(readOnly = true)
    public NewsArticleDto getArticleById(String id) {
        NewsArticle article = newsRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("News article not found: " + id));
        return NewsArticleDto.fromEntity(article);
    }
}
