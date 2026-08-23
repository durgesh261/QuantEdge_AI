package com.quantedge.news.entity;

import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.time.Instant;

/**
 * Authoritative News Article entity with strict 7-day retention policy.
 */
@Entity
@Table(name = "news_articles", indexes = {
        @Index(name = "idx_news_published_at", columnList = "published_at"),
        @Index(name = "idx_news_expires_at", columnList = "expires_at"),
        @Index(name = "idx_news_category", columnList = "category"),
        @Index(name = "idx_news_importance", columnList = "importance"),
        @Index(name = "idx_news_fingerprint", columnList = "fingerprint", unique = true)
})
public class NewsArticle extends BaseEntity {

    @Column(name = "title", nullable = false, length = 500)
    private String title;

    @Column(name = "summary", columnDefinition = "TEXT")
    private String summary;

    @Column(name = "source", nullable = false, length = 100)
    private String source;

    @Column(name = "source_url", nullable = false, length = 1000)
    private String sourceUrl;

    @Column(name = "category", nullable = false, length = 50)
    private String category;

    @Column(name = "importance", nullable = false, length = 20)
    private String importance = "MEDIUM";

    @Column(name = "relevant_symbols", length = 255)
    private String relevantSymbols;

    @Column(name = "sentiment", length = 20)
    private String sentiment = "NEUTRAL";

    @Column(name = "fingerprint", nullable = false, unique = true, length = 64)
    private String fingerprint;

    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    public NewsArticle() {}

    public NewsArticle(
            String title,
            String summary,
            String source,
            String sourceUrl,
            String category,
            String importance,
            String relevantSymbols,
            String sentiment,
            String fingerprint,
            Instant publishedAt,
            Instant expiresAt
    ) {
        this.title = title;
        this.summary = summary;
        this.source = source;
        this.sourceUrl = sourceUrl;
        this.category = category;
        this.importance = importance;
        this.relevantSymbols = relevantSymbols;
        this.sentiment = sentiment;
        this.fingerprint = fingerprint;
        this.publishedAt = publishedAt;
        this.expiresAt = expiresAt;
    }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }

    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }

    public String getSourceUrl() { return sourceUrl; }
    public void setSourceUrl(String sourceUrl) { this.sourceUrl = sourceUrl; }

    public String getCategory() { return category; }
    public void setCategory(String category) { this.category = category; }

    public String getImportance() { return importance; }
    public void setImportance(String importance) { this.importance = importance; }

    public String getRelevantSymbols() { return relevantSymbols; }
    public void setRelevantSymbols(String relevantSymbols) { this.relevantSymbols = relevantSymbols; }

    public String getSentiment() { return sentiment; }
    public void setSentiment(String sentiment) { this.sentiment = sentiment; }

    public String getFingerprint() { return fingerprint; }
    public void setFingerprint(String fingerprint) { this.fingerprint = fingerprint; }

    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant publishedAt) { this.publishedAt = publishedAt; }

    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant expiresAt) { this.expiresAt = expiresAt; }
}
