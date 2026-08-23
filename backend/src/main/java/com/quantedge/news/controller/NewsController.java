package com.quantedge.news.controller;

import com.quantedge.news.dto.NewsArticleDto;
import com.quantedge.news.service.NewsIngestionService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * REST API Controller for Financial & Crypto News.
 */
@RestController
@RequestMapping("/api/v1/news")
public class NewsController {

    private final NewsIngestionService newsService;

    public NewsController(NewsIngestionService newsService) {
        this.newsService = newsService;
    }

    @GetMapping
    public ResponseEntity<List<NewsArticleDto>> getNews(
            @RequestParam(value = "category", required = false) String category,
            @RequestParam(value = "importance", required = false) String importance,
            @RequestParam(value = "symbol", required = false) String symbol,
            @RequestParam(value = "limit", required = false, defaultValue = "50") Integer limit
    ) {
        List<NewsArticleDto> list = newsService.getNewsArticles(category, importance, symbol, limit);
        return ResponseEntity.ok(list);
    }

    @GetMapping("/{id}")
    public ResponseEntity<NewsArticleDto> getNewsById(@PathVariable("id") String id) {
        NewsArticleDto dto = newsService.getArticleById(id);
        return ResponseEntity.ok(dto);
    }

    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getNewsStatus() {
        return ResponseEntity.ok(newsService.getProviderStatus());
    }

    @PostMapping("/refresh")
    public ResponseEntity<Map<String, Object>> refreshNews() {
        int count = newsService.ingestNews();
        return ResponseEntity.ok(Map.of("success", true, "newArticlesCount", count));
    }
}
