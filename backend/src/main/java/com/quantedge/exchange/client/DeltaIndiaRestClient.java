package com.quantedge.exchange.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;
import org.springframework.web.client.RestTemplate;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.Map;

@Slf4j
@Component
public class DeltaIndiaRestClient {

    private final String baseUrl;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public DeltaIndiaRestClient(
            @Value("${quantedge.delta.api-base-url:https://api.india.delta.exchange}") String baseUrl,
            ObjectMapper objectMapper) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.restTemplate = new RestTemplate();
        this.objectMapper = objectMapper;
    }

    public static String generateSignature(String apiSecret, String method, String path, String query, String body, long timestamp) {
        try {
            String queryPart = (query != null && !query.isEmpty()) ? (query.startsWith("?") ? query : "?" + query) : "";
            String message = method.toUpperCase() + timestamp + path + queryPart + (body != null ? body : "");
            Mac mac = Mac.getInstance("HmacSHA256");
            SecretKeySpec secretKeySpec = new SecretKeySpec(apiSecret.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
            mac.init(secretKeySpec);
            byte[] hash = mac.doFinal(message.getBytes(StandardCharsets.UTF_8));
            StringBuilder hexString = new StringBuilder();
            for (byte b : hash) {
                String hex = Integer.toHexString(0xff & b);
                if (hex.length() == 1) hexString.append('0');
                hexString.append(hex);
            }
            return hexString.toString();
        } catch (Exception e) {
            throw new RuntimeException("Failed to generate HMAC-SHA256 signature", e);
        }
    }

    public ResponseEntity<String> executeRequest(String apiKey, String apiSecret, HttpMethod method, String path, String query, Object body) {
        long timestamp = Instant.now().getEpochSecond();
        String bodyStr = "";
        if (body != null) {
            try {
                bodyStr = objectMapper.writeValueAsString(body);
            } catch (Exception e) {
                throw new IllegalArgumentException("Failed to serialize request body", e);
            }
        }

        String signature = generateSignature(apiSecret, method.name(), path, query, bodyStr, timestamp);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setAccept(Collections.singletonList(MediaType.APPLICATION_JSON));
        headers.set("api-key", apiKey);
        headers.set("timestamp", String.valueOf(timestamp));
        headers.set("signature", signature);
        headers.set("User-Agent", "QuantEdge-AI/2.0");

        String fullUrl = baseUrl + path + (query != null && !query.isEmpty() ? (query.startsWith("?") ? query : "?" + query) : "");

        HttpEntity<String> entity = new HttpEntity<>(bodyStr.isEmpty() ? null : bodyStr, headers);

        try {
            return restTemplate.exchange(fullUrl, method, entity, String.class);
        } catch (HttpClientErrorException.Unauthorized e) {
            log.error("Delta Exchange authentication failed for endpoint: {}", path);
            throw new SecurityException("Delta Exchange authentication failed: invalid API key or signature");
        } catch (HttpClientErrorException.TooManyRequests e) {
            log.warn("Delta Exchange rate limit exceeded for endpoint: {}", path);
            throw new RuntimeException("Delta Exchange rate limit exceeded");
        } catch (HttpClientErrorException | HttpServerErrorException e) {
            log.error("Delta Exchange HTTP error on endpoint {}: {}", path, e.getStatusCode());
            throw new RuntimeException("Delta Exchange HTTP error: " + e.getStatusCode(), e);
        } catch (Exception e) {
            log.error("Delta Exchange connection error on endpoint {}: {}", path, e.getMessage());
            throw new RuntimeException("Delta Exchange connection error: " + e.getMessage(), e);
        }
    }
}
