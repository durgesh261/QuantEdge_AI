package com.quantedge.news;

import com.quantedge.news.dto.NewsArticleDto;
import com.quantedge.news.entity.NewsArticle;
import com.quantedge.news.provider.FinancialNewsProvider;
import com.quantedge.news.provider.NewsProvider;
import com.quantedge.news.repository.NewsArticleRepository;
import com.quantedge.news.service.NewsIngestionService;
import com.quantedge.news.service.NewsRetentionCleanupService;
import com.quantedge.notification.service.NotificationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 8: News Ingestion, Deduplication & 7-Day Retention Tests")
class NewsIngestionAndRetentionTest {

    @Mock private NewsProvider newsProvider;
    @Mock private NewsArticleRepository newsRepository;
    @Mock private NotificationService notificationService;

    private NewsIngestionService newsIngestionService;
    private NewsRetentionCleanupService cleanupService;

    @BeforeEach
    void setUp() {
        newsIngestionService = new NewsIngestionService(newsProvider, newsRepository, notificationService);
        cleanupService = new NewsRetentionCleanupService(newsRepository);
    }

    @Test
    @DisplayName("Deduplicates articles using SHA-256 fingerprint")
    void deduplicatesArticles() {
        Instant now = Instant.now();
        String fp = FinancialNewsProvider.computeFingerprint("Bitcoin Surges", "CoinDesk", "http://example.com/1");

        NewsArticle article = new NewsArticle(
                "Bitcoin Surges", "Summary text", "CoinDesk", "http://example.com/1",
                "CRYPTO", "HIGH", "BTC", "BULLISH", fp, now, now.plus(7, ChronoUnit.DAYS)
        );

        when(newsProvider.fetchLatestNews()).thenReturn(List.of(article));
        when(newsRepository.existsByFingerprint(fp)).thenReturn(true); // Already exists!

        int ingested = newsIngestionService.ingestNews();

        assertThat(ingested).isEqualTo(0);
        verify(newsRepository, never()).save(any());
    }

    @Test
    @DisplayName("Stores new articles with strict 7-day retention and dispatches high-importance notifications")
    void ingestsAndStamps7DayRetention() {
        Instant now = Instant.now();
        String fp = FinancialNewsProvider.computeFingerprint("Fed Rate Cut Decision", "Bloomberg", "http://example.com/2");

        NewsArticle article = new NewsArticle(
                "Fed Rate Cut Decision", "FOMC cuts rates by 25bps", "Bloomberg", "http://example.com/2",
                "CENTRAL_BANKS", "CRITICAL", "USD", "BULLISH", fp, now, null
        );

        when(newsProvider.fetchLatestNews()).thenReturn(List.of(article));
        when(newsRepository.existsByFingerprint(fp)).thenReturn(false);
        when(newsRepository.save(any(NewsArticle.class))).thenAnswer(inv -> inv.getArgument(0));

        int ingested = newsIngestionService.ingestNews();

        assertThat(ingested).isEqualTo(1);

        ArgumentCaptor<NewsArticle> captor = ArgumentCaptor.forClass(NewsArticle.class);
        verify(newsRepository).save(captor.capture());
        NewsArticle saved = captor.getValue();

        // Verify strictly 7-day retention: expires_at = published_at + 7 days
        assertThat(saved.getExpiresAt()).isNotNull();
        long daysDiff = ChronoUnit.DAYS.between(saved.getPublishedAt(), saved.getExpiresAt());
        assertThat(daysDiff).isEqualTo(7);

        // Verify notification dispatch for CRITICAL importance
        verify(notificationService).createNotification(
                isNull(),
                eq("NEWS_ALERT"),
                contains("Fed Rate Cut Decision"),
                contains("FOMC cuts rates"),
                any(),
                eq("CRITICAL")
        );
    }

    @Test
    @DisplayName("Cleans up expired news articles past 7-day retention")
    void cleansUpExpiredNews() {
        when(newsRepository.deleteExpiredArticles(any(Instant.class))).thenReturn(5);

        int deleted = cleanupService.cleanupExpiredNews();

        assertThat(deleted).isEqualTo(5);
        verify(newsRepository).deleteExpiredArticles(any(Instant.class));
    }

    @Test
    @DisplayName("Status: Exposes provider health and sync metadata without credentials")
    void exposesProviderStatus() {
        when(newsProvider.getProviderName()).thenReturn("LiveFinancialNewsProvider");

        java.util.Map<String, Object> status = newsIngestionService.getProviderStatus();

        assertThat(status).isNotNull();
        assertThat(status.get("providerName")).isEqualTo("LiveFinancialNewsProvider");
        assertThat(status.get("enabled")).isEqualTo(true);
        assertThat(status).doesNotContainKey("apiKey");
        assertThat(status).doesNotContainKey("apiSecret");
    }
}
