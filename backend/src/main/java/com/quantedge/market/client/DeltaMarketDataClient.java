package com.quantedge.market.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.ArrayList;
import java.util.List;

/**
 * Public Market Data REST Client for Delta Exchange India (DELTAIN).
 * Exclusively queries public market-data endpoints without requiring user credentials.
 */
@Component
public class DeltaMarketDataClient {

    private static final Logger log = LoggerFactory.getLogger(DeltaMarketDataClient.class);

    private final String baseUrl;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public DeltaMarketDataClient(
            @Value("${quantedge.delta.api-base-url:https://api.india.delta.exchange}") String baseUrl,
            ObjectMapper objectMapper) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.restTemplate = new RestTemplate();
        this.objectMapper = objectMapper;
    }

    /**
     * Fetches raw candles from Delta Exchange India public history endpoint.
     */
    public JsonNode fetchRawCandles(String symbol, String resolution, Long start, Long end) {
        StringBuilder url = new StringBuilder(baseUrl)
                .append("/v2/history/candles?symbol=").append(symbol)
                .append("&resolution=").append(resolution);

        if (start != null) url.append("&start=").append(start);
        if (end != null) url.append("&end=").append(end);

        try {
            ResponseEntity<String> response = restTemplate.getForEntity(url.toString(), String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                JsonNode root = objectMapper.readTree(response.getBody());
                if (root.path("success").asBoolean(true)) {
                    return root.path("result");
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch candles from Delta India ({}): {}", url, e.getMessage());
        }
        return null;
    }

    /**
     * Fetches raw ticker data for symbol from Delta Exchange India.
     */
    public JsonNode fetchRawTicker(String symbol) {
        String url = baseUrl + "/v2/tickers";
        try {
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                JsonNode root = objectMapper.readTree(response.getBody());
                JsonNode result = root.path("result");
                if (result.isArray()) {
                    for (JsonNode t : result) {
                        if (symbol.equalsIgnoreCase(t.path("symbol").asText())) {
                            return t;
                        }
                    }
                    if (result.size() > 0) return result.get(0);
                } else if (result.isObject()) {
                    return result;
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch ticker from Delta India: {}", e.getMessage());
        }
        return null;
    }

    /**
     * Fetches all active products from Delta Exchange India.
     */
    public JsonNode fetchRawProducts() {
        String url = baseUrl + "/v2/products";
        try {
            ResponseEntity<String> response = restTemplate.getForEntity(url, String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                JsonNode root = objectMapper.readTree(response.getBody());
                return root.path("result");
            }
        } catch (Exception e) {
            log.warn("Failed to fetch products from Delta India: {}", e.getMessage());
        }
        return null;
    }
}
