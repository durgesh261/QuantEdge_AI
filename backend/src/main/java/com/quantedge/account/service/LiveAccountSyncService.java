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
            ObjectMapper objectMapper) {
        this.deltaRestClient = deltaRestClient;
        this.credentialService = credentialService;
        this.objectMapper = objectMapper;
    }

    public record BalanceDetail(
            String asset,
            BigDecimal balance,
            BigDecimal availableBalance
    ) {}

    public record PositionDetail(
            String symbol,
            String side,
            BigDecimal size,
            BigDecimal entryPrice,
            BigDecimal markPrice,
            BigDecimal unrealizedPnl,
            BigDecimal realizedPnl,
            Integer leverage,
            BigDecimal margin,
            BigDecimal liquidationPrice
    ) {}

    public record OrderDetail(
            String orderId,
            String clientOrderId,
            String symbol,
            String side,
            String orderType,
            String state,
            BigDecimal price,
            BigDecimal size,
            BigDecimal unfilledSize
    ) {}

    public record SyncSummary(
            boolean success,
            Instant syncedAt,
            String accountId,
            BigDecimal totalEquity,
            BigDecimal availableBalance,
            BigDecimal marginUsed,
            int positionsCount,
            int ordersCount,
            List<BalanceDetail> balances,
            List<PositionDetail> positions,
            List<OrderDetail> openOrders,
            List<String> discrepancies,
            String error) {
    }

    public SyncSummary syncLiveAccount(String accountId, String encryptedApiKey, String encryptedApiSecret) {
        Instant syncTime = Instant.now();
        List<String> discrepancies = new ArrayList<>();
        List<BalanceDetail> balanceList = new ArrayList<>();
        List<PositionDetail> positionList = new ArrayList<>();
        List<OrderDetail> orderList = new ArrayList<>();

        try {
            String apiKey = credentialService.decrypt(encryptedApiKey);
            String apiSecret = credentialService.decrypt(encryptedApiSecret);

            if (apiKey.isEmpty() || apiSecret.isEmpty()) {
                throw new IllegalArgumentException("API Key or Secret cannot be empty for live account sync");
            }

            // 1. Fetch wallet balances
            ResponseEntity<String> balanceResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/wallet/balances", null, null);
            JsonNode balanceRoot = objectMapper.readTree(balanceResp.getBody());
            JsonNode balances = balanceRoot.path("result");

            BigDecimal totalEquity = BigDecimal.ZERO;
            BigDecimal availableBalance = BigDecimal.ZERO;
            BigDecimal marginUsed = BigDecimal.ZERO;

            if (balances.isArray()) {
                for (JsonNode b : balances) {
                    String symbol = b.path("asset_symbol").asText("");
                    BigDecimal bal = new BigDecimal(b.path("balance").asText("0"));
                    BigDecimal avail = new BigDecimal(b.path("available_balance").asText("0"));
                    balanceList.add(new BalanceDetail(symbol, bal, avail));

                    if ("USDT".equalsIgnoreCase(symbol)) {
                        totalEquity = bal;
                        availableBalance = avail;
                        BigDecimal posMargin = new BigDecimal(b.path("position_margin").asText("0"));
                        BigDecimal ordMargin = new BigDecimal(b.path("order_margin").asText("0"));
                        marginUsed = posMargin.add(ordMargin);
                    }
                }
            }

            // 2. Fetch margined positions
            ResponseEntity<String> posResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/positions/margined", null, null);
            JsonNode posRoot = objectMapper.readTree(posResp.getBody());
            JsonNode positions = posRoot.path("result");
            int openPositionsCount = 0;
            if (positions.isArray()) {
                for (JsonNode p : positions) {
                    BigDecimal size = new BigDecimal(p.path("size").asText("0"));
                    if (size.compareTo(BigDecimal.ZERO) != 0) {
                        openPositionsCount++;
                        String sym = p.path("product_symbol").asText(p.path("symbol").asText(""));
                        String side = size.compareTo(BigDecimal.ZERO) > 0 ? "LONG" : "SHORT";
                        BigDecimal entryPrice = new BigDecimal(p.path("entry_price").asText("0"));
                        BigDecimal markPrice = new BigDecimal(p.path("mark_price").asText("0"));
                        BigDecimal upnl = new BigDecimal(p.path("unrealized_pnl").asText("0"));
                        BigDecimal rpnl = new BigDecimal(p.path("realized_pnl").asText("0"));
                        int lev = p.path("leverage").asInt(1);
                        BigDecimal margin = new BigDecimal(p.path("margin").asText("0"));
                        BigDecimal liqPrice = p.hasNonNull("liquidation_price") ? new BigDecimal(p.path("liquidation_price").asText()) : null;

                        positionList.add(new PositionDetail(
                                sym, side, size.abs(), entryPrice, markPrice, upnl, rpnl, lev, margin, liqPrice
                        ));
                    }
                }
            }

            // 3. Fetch open orders
            ResponseEntity<String> ordersResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/orders", "state=open", null);
            JsonNode ordersRoot = objectMapper.readTree(ordersResp.getBody());
            JsonNode orders = ordersRoot.path("result");
            int openOrdersCount = 0;
            if (orders.isArray()) {
                openOrdersCount = orders.size();
                for (JsonNode o : orders) {
                    orderList.add(new OrderDetail(
                            o.path("id").asText(""),
                            o.path("client_order_id").asText(""),
                            o.path("product_symbol").asText(o.path("symbol").asText("")),
                            o.path("side").asText(""),
                            o.path("order_type").asText(""),
                            o.path("state").asText(""),
                            new BigDecimal(o.path("limit_price").asText("0")),
                            new BigDecimal(o.path("size").asText("0")),
                            new BigDecimal(o.path("unfilled_size").asText("0"))
                    ));
                }
            }

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
                    balanceList,
                    positionList,
                    orderList,
                    discrepancies,
                    null);

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
                    balanceList,
                    positionList,
                    orderList,
                    discrepancies,
                    e.getMessage());
        }
    }
}
