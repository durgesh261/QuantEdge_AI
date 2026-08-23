package com.quantedge.news.dto;

import com.quantedge.news.entity.NewsArticle;

import java.time.Instant;

/**
 * Sanitized News Article DTO for frontend consumption.
 */
public record NewsArticleDto(
        String id,
        String title,
        String summary,
        String source,
        String sourceUrl,
        String category,
        String importance,
        String relevantSymbols,
        String sentiment,
        Instant publishedAt,
        Instant expiresAt
) {
    public static NewsArticleDto fromEntity(NewsArticle entity) {
        if (entity == null) return null;
        return new NewsArticleDto(
                entity.getId(),
                entity.getTitle(),
                entity.getSummary(),
                entity.getSource(),
                entity.getSourceUrl(),
                entity.getCategory(),
                entity.getImportance(),
                entity.getRelevantSymbols(),
                entity.getSentiment(),
                entity.getPublishedAt(),
                entity.getExpiresAt()
        );
    }
}
