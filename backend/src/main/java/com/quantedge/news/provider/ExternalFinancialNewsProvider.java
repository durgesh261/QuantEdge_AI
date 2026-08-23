package com.quantedge.news.provider;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.news.entity.NewsArticle;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Primary;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Production Live Financial & Crypto News Provider.
 * Queries live external news REST endpoints, parses articles, performs categorization,
 * sentiment detection, SHA-256 deduplication, and 7-day retention calculation.
 */
@Component("externalFinancialNewsProvider")
@Primary
@ConditionalOnProperty(name = "quantedge.news.provider-mode", havingValue = "external", matchIfMissing = true)
public class ExternalFinancialNewsProvider implements NewsProvider {

    private static final Logger log = LoggerFactory.getLogger(ExternalFinancialNewsProvider.class);
    private static final String PROVIDER_NAME = "LiveFinancialNewsProvider";

    private final String apiBaseUrl;
    private final String apiKey;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public ExternalFinancialNewsProvider(
            @Value("${quantedge.news.api-base-url:https://min-api.cryptocompare.com/data/v2/news/?lang=EN}") String apiBaseUrl,
            @Value("${quantedge.news.api-key:}") String apiKey,
            RestTemplateBuilder restTemplateBuilder,
            ObjectMapper objectMapper
    ) {
        this.apiBaseUrl = apiBaseUrl;
        this.apiKey = apiKey != null ? apiKey.trim() : "";
        this.restTemplate = restTemplateBuilder
                .setConnectTimeout(Duration.ofSeconds(5))
                .setReadTimeout(Duration.ofSeconds(10))
                .build();
        this.objectMapper = objectMapper;
    }

    // Constructor for testing with injected RestTemplate
    public ExternalFinancialNewsProvider(String apiBaseUrl, String apiKey, RestTemplate restTemplate, ObjectMapper objectMapper) {
        this.apiBaseUrl = apiBaseUrl;
        this.apiKey = apiKey != null ? apiKey.trim() : "";
        this.restTemplate = restTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public String getProviderName() {
        return PROVIDER_NAME;
    }

    /**
     * Computes deterministic SHA-256 fingerprint for deduplication.
     */
    public static String computeFingerprint(String title, String source, String sourceUrl) {
        try {
            String norm = (title != null ? title.trim().toLowerCase(Locale.ROOT) : "") + "|" +
                          (source != null ? source.trim().toLowerCase(Locale.ROOT) : "") + "|" +
                          (sourceUrl != null ? normalizeUrl(sourceUrl) : "");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(norm.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte b : hash) {
                String h = Integer.toHexString(0xff & b);
                if (h.length() == 1) hex.append('0');
                hex.append(h);
            }
            return hex.toString();
        } catch (Exception e) {
            return String.valueOf((title + source + sourceUrl).hashCode());
        }
    }

    /**
     * Strips tracking parameters from canonical source URLs.
     */
    public static String normalizeUrl(String url) {
        if (url == null || url.isBlank()) return "";
        try {
            URI uri = new URI(url.trim());
            String clean = uri.getScheme() + "://" + uri.getAuthority() + uri.getPath();
            return clean.toLowerCase(Locale.ROOT);
        } catch (Exception e) {
            return url.trim().toLowerCase(Locale.ROOT);
        }
    }

    @Override
    public List<NewsArticle> fetchLatestNews() {
        List<NewsArticle> articles = new ArrayList<>();
        String requestUrl = buildRequestUrl();

        log.info("NEWS_PROVIDER_SYNC_STARTED: Fetching real-time market news from {}", sanitizeLogUrl(requestUrl));

        try {
            ResponseEntity<String> response = restTemplate.getForEntity(requestUrl, String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                articles = parseNewsPayload(response.getBody());
                log.info("NEWS_PROVIDER_SYNC_SUCCESS: Successfully parsed {} live news articles", articles.size());
            } else {
                log.warn("NEWS_PROVIDER_SYNC_FAILED: Unexpected response status [{}] from news provider", response.getStatusCode());
            }
        } catch (Exception e) {
            log.error("NEWS_PROVIDER_SYNC_FAILED: Failed to fetch live news from provider: {}", e.getMessage());
        }

        return articles;
    }

    public List<NewsArticle> parseNewsPayload(String jsonPayload) {
        List<NewsArticle> list = new ArrayList<>();
        if (jsonPayload == null || jsonPayload.isBlank()) return list;

        try {
            JsonNode root = objectMapper.readTree(jsonPayload);
            JsonNode dataNode = root.path("Data");
            if (!dataNode.isArray()) {
                // If direct array
                if (root.isArray()) dataNode = root;
                else return list;
            }

            Instant now = Instant.now();

            for (JsonNode item : dataNode) {
                String title = item.path("title").asText(null);
                if (title == null || title.isBlank()) continue;

                String summary = item.path("body").asText("");
                if (summary.isBlank()) summary = item.path("description").asText("");

                String url = item.path("url").asText("");
                if (url.isBlank()) url = item.path("guid").asText("");

                String source = item.path("source_info").path("name").asText(null);
                if (source == null || source.isBlank()) source = item.path("source").asText("CryptoNews");

                long publishedOn = item.path("published_on").asLong(0);
                Instant publishedAt = publishedOn > 0 ? Instant.ofEpochSecond(publishedOn) : now;

                // Handle future timestamps safely
                if (publishedAt.isAfter(now)) {
                    publishedAt = now;
                }

                // Strict 7-Day Retention: expires_at = published_at + 7 days
                Instant expiresAt = publishedAt.plus(7, ChronoUnit.DAYS);

                String rawCategories = item.path("categories").asText("");
                String rawTags = item.path("tags").asText("");
                String category = classifyCategory(title, rawCategories);
                String sentiment = classifySentiment(title, summary);
                String importance = classifyImportance(title, category);
                String symbols = extractSymbols(title, rawCategories + "|" + rawTags);
                String fingerprint = computeFingerprint(title, source, url);

                NewsArticle article = new NewsArticle(
                        title.trim(),
                        summary.trim(),
                        source.trim(),
                        url.trim(),
                        category,
                        importance,
                        symbols,
                        sentiment,
                        fingerprint,
                        publishedAt,
                        expiresAt
                );

                list.add(article);
            }
        } catch (Exception e) {
            log.error("Failed to parse news payload: {}", e.getMessage(), e);
        }

        return list;
    }

    public static String classifyCategory(String title, String rawCategories) {
        String combined = (title + " " + rawCategories).toUpperCase(Locale.ROOT);

        if (combined.contains("FED") || combined.contains("FOMC") || combined.contains("CENTRAL BANK") ||
            combined.contains("POWELL") || combined.contains("RATE HIKE") || combined.contains("RATE CUT") ||
            combined.contains("ECB") || combined.contains("BOE") || combined.contains("BOJ") || combined.contains("RBI")) {
            return "CENTRAL_BANKS";
        }
        if (combined.contains("SEC") || combined.contains("REGULATION") || combined.contains("CFTC") ||
            combined.contains("LAWSUIT") || combined.contains("COMPLIANCE") || combined.contains("LEGAL") ||
            combined.contains("BAN") || combined.contains("POLICY") || combined.contains("SANCTION")) {
            return "REGULATION";
        }
        if (combined.contains("OIL") || combined.contains("GOLD") || combined.contains("SILVER") ||
            combined.contains("COMMODITY") || combined.contains("ENERGY") || combined.contains("CRUDE")) {
            return "COMMODITIES";
        }
        if (combined.contains("INFLATION") || combined.contains("CPI") || combined.contains("GDP") ||
            combined.contains("UNEMPLOYMENT") || combined.contains("MACRO") || combined.contains("ECONOMY") ||
            combined.contains("PAYROLL") || combined.contains("RECESSION") || combined.contains("PMI")) {
            return "ECONOMY";
        }
        if (combined.contains("STOCK") || combined.contains("EQUITY") || combined.contains("NASDAQ") ||
            combined.contains("S&P") || combined.contains("DERIVATIVES") || combined.contains("FUTURES") ||
            combined.contains("OPTIONS") || combined.contains("LIQUIDITY")) {
            return "MARKETS";
        }
        if (combined.contains("BANK") || combined.contains("FINANCIAL") || combined.contains("FINTECH") ||
            combined.contains("PAYMENT") || combined.contains("CREDIT") || combined.contains("TREASURY")) {
            return "FINANCE";
        }
        return "CRYPTO";
    }

    public static String classifySentiment(String title, String summary) {
        String text = (title + " " + summary).toLowerCase(Locale.ROOT);
        int bullishScore = 0;
        int bearishScore = 0;

        String[] bullWords = {"surge", "surges", "gain", "gains", "bullish", "high", "rally", "rallies", "breakout", "record", "soars", "approval", "support", "inflow", "inflows"};
        String[] bearWords = {"drop", "drops", "fall", "falls", "bearish", "crash", "plunge", "plunges", "slump", "decline", "declines", "hack", "scam", "ban", "loss", "losses", "outflow", "liquidation"};

        for (String w : bullWords) if (text.contains(w)) bullishScore++;
        for (String w : bearWords) if (text.contains(w)) bearishScore++;

        if (bullishScore > bearishScore) return "BULLISH";
        if (bearishScore > bullishScore) return "BEARISH";
        return "NEUTRAL";
    }

    public static String classifyImportance(String title, String category) {
        String t = title.toUpperCase(Locale.ROOT);
        if (t.contains("CRITICAL") || t.contains("BREAKING") || t.contains("WAR") || t.contains("EMERGENCY") ||
            t.contains("FOMC") || t.contains("INTEREST RATE DECISION") || t.contains("CPI RELEASE")) {
            return "CRITICAL";
        }
        if (t.contains("SURGE") || t.contains("CRASH") || t.contains("SEC") || t.contains("APPROVES") ||
            t.contains("ETF") || t.contains("RECORD HIGH") || "CENTRAL_BANKS".equals(category) || "REGULATION".equals(category)) {
            return "HIGH";
        }
        if (t.contains("UPDATE") || t.contains("ANALYSIS") || t.contains("REPORT")) {
            return "MEDIUM";
        }
        return "MEDIUM";
    }

    public static String extractSymbols(String title, String metadata) {
        String text = (title + " " + metadata).toUpperCase(Locale.ROOT);
        List<String> matched = new ArrayList<>();
        Set<String> targetSymbols = Set.of("BTC", "ETH", "SOL", "XRP", "USDT", "USD", "INR", "EUR", "GBP", "JPY");

        for (String sym : targetSymbols) {
            if (text.contains(sym)) {
                matched.add(sym);
            }
        }
        if (matched.isEmpty()) return "BTC,ETH";
        return String.join(",", matched);
    }

    private String buildRequestUrl() {
        if (apiKey != null && !apiKey.isBlank()) {
            return apiBaseUrl + (apiBaseUrl.contains("?") ? "&" : "?") + "api_key=" + apiKey;
        }
        return apiBaseUrl;
    }

    private String sanitizeLogUrl(String url) {
        if (url == null) return "";
        return url.replaceAll("api_key=[^&]+", "api_key=***");
    }
}
