package com.quantedge.news.repository;

import com.quantedge.news.entity.NewsArticle;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public interface NewsArticleRepository extends JpaRepository<NewsArticle, String> {

    boolean existsByFingerprint(String fingerprint);

    Optional<NewsArticle> findByFingerprint(String fingerprint);

    List<NewsArticle> findAllByOrderByPublishedAtDesc();

    @Query("SELECT n FROM NewsArticle n WHERE (:category IS NULL OR n.category = :category) " +
           "AND (:importance IS NULL OR n.importance = :importance) " +
           "AND (n.expiresAt IS NULL OR n.expiresAt > :now) " +
           "ORDER BY n.publishedAt DESC")
    List<NewsArticle> findWithFilters(
            @Param("category") String category,
            @Param("importance") String importance,
            @Param("now") Instant now
    );

    @Modifying
    @Query("DELETE FROM NewsArticle n WHERE n.expiresAt <= :now")
    int deleteExpiredArticles(@Param("now") Instant now);
}
