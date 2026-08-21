package com.quantedge.account.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.service.DeltaCredentialService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

@Service
public class LiveAccountSyncService {

    private static final Logger log = LoggerFactory.getLogger(LiveAccountSyncService.class);

    private final DeltaIndiaRestClient deltaRestClient;
    private final DeltaCredentialService credentialService;
    private final ObjectMapper objectMapper;

    public LiveAccountSyncService(
            DeltaIndiaRestClient deltaRestClient,
            DeltaCredentialService credentialService,
            ObjectMapper objectMapper
    ) {
        this.deltaRestClient = deltaRestClient;
        this.credentialService = credentialService;
        this.objectMapper = objectMapper;
    }

    public record SyncSummary(
            boolean success,
            Instant syncedAt,
            String accountId,
            BigDecimal totalEquity,
            BigDecimal availableBalance,
            BigDecimal marginUsed,
            int positionsCount,
            int ordersCount,
            List<String> discrepancies,
            String error
    ) {}

    public SyncSummary syncLiveAccount(String accountId, String encryptedApiKey, String encryptedApiSecret) {
        Instant syncTime = Instant.now();
        List<String> discrepancies = new ArrayList<>();

        try {
            String apiKey = credentialService.decrypt(encryptedApiKey);
            String apiSecret = credentialService.decrypt(encryptedApiSecret);

            if (apiKey.isEmpty() || apiSecret.isEmpty()) {
                throw new IllegalArgumentException("API Key or Secret cannot be empty for live account sync");
            }

            // 1. Fetch wallet balances
            ResponseEntity<String> balanceResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/wallet/balances", null, null
            );
            JsonNode balanceRoot = objectMapper.readTree(balanceResp.getBody());
            JsonNode balances = balanceRoot.path("result");

            BigDecimal totalEquity = BigDecimal.ZERO;
            BigDecimal availableBalance = BigDecimal.ZERO;
            BigDecimal marginUsed = BigDecimal.ZERO;

            if (balances.isArray()) {
                for (JsonNode b : balances) {
                    String symbol = b.path("asset_symbol").asText("");
                    if ("USDT".equalsIgnoreCase(symbol)) {
                        totalEquity = new BigDecimal(b.path("balance").asText("0"));
                        availableBalance = new BigDecimal(b.path("available_balance").asText("0"));
                        BigDecimal posMargin = new BigDecimal(b.path("position_margin").asText("0"));
                        BigDecimal ordMargin = new BigDecimal(b.path("order_margin").asText("0"));
                        marginUsed = posMargin.add(ordMargin);
                    }
                }
            }

            // 2. Fetch margined positions
            ResponseEntity<String> posResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/positions/margined", null, null
            );
            JsonNode posRoot = objectMapper.readTree(posResp.getBody());
            JsonNode positions = posRoot.path("result");
            int openPositionsCount = 0;
            if (positions.isArray()) {
                for (JsonNode p : positions) {
                    BigDecimal size = new BigDecimal(p.path("size").asText("0"));
                    if (size.compareTo(BigDecimal.ZERO) != 0) {
                        openPositionsCount++;
                    }
                }
            }

            // 3. Fetch open orders
            ResponseEntity<String> ordersResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/orders", "state=open", null
            );
            JsonNode ordersRoot = objectMapper.readTree(ordersResp.getBody());
            JsonNode orders = ordersRoot.path("result");
            int openOrdersCount = orders.isArray() ? orders.size() : 0;

            log.info("Successfully synchronized live account {}: equity={}, positions={}, orders={}",
                    accountId, totalEquity, openPositionsCount, openOrdersCount);

            return new SyncSummary(
                    true,
                    syncTime,
                    accountId,
                    totalEquity,
                    availableBalance,
                    marginUsed,
                    openPositionsCount,
                    openOrdersCount,
                    discrepancies,
                    null
            );

        } catch (Exception e) {
            log.error("Failed to synchronize live account {}: {}", accountId, e.getMessage());
            return new SyncSummary(
                    false,
                    syncTime,
                    accountId,
                    BigDecimal.ZERO,
                    BigDecimal.ZERO,
                    BigDecimal.ZERO,
                    0,
                    0,
                    discrepancies,
                    e.getMessage()
            );
        }
    }
}
