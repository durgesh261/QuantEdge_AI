package com.quantedge.news.provider;

import com.quantedge.news.entity.NewsArticle;

import java.util.List;

/**
 * Pluggable interface for financial & crypto news providers.
 */
public interface NewsProvider {

    /**
     * Provider identification name.
     */
    String getProviderName();

    /**
     * Fetches latest financial and crypto news articles.
     */
    List<NewsArticle> fetchLatestNews();
}
