package com.quantedge.trading.service;

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
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class OrderExecutionService {

    private static final Logger log = LoggerFactory.getLogger(OrderExecutionService.class);

    private final OrderValidationGateway validationGateway;
    private final DeltaIndiaRestClient deltaRestClient;
    private final DeltaCredentialService credentialService;
    private final ObjectMapper objectMapper;

    // In-flight concurrency lock registry
    private final Set<String> inFlightSetups = ConcurrentHashMap.newKeySet();
    private final Set<String> inFlightClientOrderIds = ConcurrentHashMap.newKeySet();

    public OrderExecutionService(
            OrderValidationGateway validationGateway,
            DeltaIndiaRestClient deltaRestClient,
            DeltaCredentialService credentialService,
            ObjectMapper objectMapper
    ) {
        this.validationGateway = validationGateway;
        this.deltaRestClient = deltaRestClient;
        this.credentialService = credentialService;
        this.objectMapper = objectMapper;
    }

    public enum ExecutionState {
        VALIDATED,
        SUBMITTING,
        SUBMITTED,
        RECONCILIATION_REQUIRED,
        REJECTED,
        FAILED,
        PARTIALLY_FILLED,
        FILLED,
        CANCELLED
    }

    public record ExecutionRequest(
            String accountId,
            String setupId,
            String symbol,
            String direction,
            String orderType,
            BigDecimal quantity,
            BigDecimal entryPrice,
            BigDecimal stopLoss,
            BigDecimal takeProfit,
            Integer leverage,
            String clientOrderId,
            boolean reduceOnly
    ) {}

    public record ExecutionResult(
            boolean success,
            ExecutionState state,
            String orderId,
            String clientOrderId,
            String setupId,
            String symbol,
            String direction,
            BigDecimal quantity,
            BigDecimal price,
            BigDecimal averageFillPrice,
            String rejectionCode,
            String errorMessage,
            boolean reconciled,
            String reconciliationDetail,
            Instant completedAt
    ) {
        public static ExecutionResult rejected(String clientOrderId, String setupId, String symbol, String code, String message) {
            return new ExecutionResult(false, ExecutionState.REJECTED, null, clientOrderId, setupId, symbol, null, null, null, null, code, message, false, null, Instant.now());
        }

        public static ExecutionResult failed(String clientOrderId, String setupId, String symbol, String code, String message, boolean reconciled, String reconciliationDetail) {
            return new ExecutionResult(false, ExecutionState.FAILED, null, clientOrderId, setupId, symbol, null, null, null, null, code, message, reconciled, reconciliationDetail, Instant.now());
        }

        public static ExecutionResult success(ExecutionState state, String orderId, String clientOrderId, String setupId, String symbol, String direction, BigDecimal quantity, BigDecimal price, BigDecimal avgFillPrice, boolean reconciled, String reconciliationDetail) {
            return new ExecutionResult(true, state, orderId, clientOrderId, setupId, symbol, direction, quantity, price, avgFillPrice, null, null, reconciled, reconciliationDetail, Instant.now());
        }
    }

    public ExecutionResult executeOrder(
            ExecutionRequest request,
            OrderValidationGateway.ValidationContext validationContext,
            String encryptedApiKey,
            String encryptedApiSecret
    ) {
        String clientOrderId = request.clientOrderId() != null && !request.clientOrderId().isBlank()
                ? request.clientOrderId()
                : "QE-" + System.currentTimeMillis() + "-" + UUID.randomUUID().toString().substring(0, 8);

        // 1. Atomic In-Flight Locking
        if (request.setupId() != null && !request.setupId().isBlank()) {
            if (!inFlightSetups.add(request.setupId())) {
                log.warn("Blocked duplicate in-flight setup_id: {}", request.setupId());
                return ExecutionResult.rejected(clientOrderId, request.setupId(), request.symbol(), "DUPLICATE_SETUP_ID", "setup_id is already in-flight or submitted.");
            }
        }

        if (!inFlightClientOrderIds.add(clientOrderId)) {
            log.warn("Blocked duplicate in-flight client_order_id: {}", clientOrderId);
            if (request.setupId() != null) inFlightSetups.remove(request.setupId());
            return ExecutionResult.rejected(clientOrderId, request.setupId(), request.symbol(), "DUPLICATE_CLIENT_ORDER_ID", "client_order_id is already in-flight.");
        }

        try {
            // 2. Phase 5.3 Fail-Closed Validation
            OrderValidationGateway.ValidationRequest valReq = new OrderValidationGateway.ValidationRequest(
                    request.accountId(),
                    request.symbol(),
                    request.direction(),
                    request.orderType(),
                    request.quantity(),
                    request.entryPrice(),
                    request.stopLoss(),
                    request.takeProfit(),
                    request.leverage(),
                    clientOrderId,
                    request.setupId(),
                    request.reduceOnly()
            );

            OrderValidationGateway.ValidationResult valResult = validationGateway.validate(valReq, validationContext);
            if (!valResult.valid()) {
                log.warn("Order validation failed for {}: [{}] {}", clientOrderId, valResult.rejectionCode(), valResult.rejectionReason());
                return ExecutionResult.rejected(
                        clientOrderId,
                        request.setupId(),
                        request.symbol(),
                        valResult.rejectionCode() != null ? valResult.rejectionCode().name() : "VALIDATION_FAILED",
                        valResult.rejectionReason()
                );
            }

            // Decrypt credentials
            String apiKey = credentialService.decrypt(encryptedApiKey);
            String apiSecret = credentialService.decrypt(encryptedApiSecret);

            // 3. Build Delta Exchange India Order Payload
            String deltaSymbol = request.symbol().replace(".P", "").toUpperCase();
            OrderValidationGateway.ProductSpecification spec = validationContext.getProductSpecs().get(request.symbol().toUpperCase());
            int productId = spec != null ? spec.productId() : 27;

            Map<String, Object> orderPayload = new HashMap<>();
            orderPayload.put("product_id", productId);
            orderPayload.put("product_symbol", deltaSymbol);
            orderPayload.put("size", request.quantity().intValue());
            orderPayload.put("side", request.direction().equalsIgnoreCase("LONG") || request.direction().equalsIgnoreCase("BUY") ? "buy" : "sell");
            orderPayload.put("order_type", request.orderType().toLowerCase().contains("market") ? "market_order" : "limit_order");
            if (request.entryPrice() != null && !request.orderType().toLowerCase().contains("market")) {
                orderPayload.put("limit_price", request.entryPrice().toPlainString());
            }
            if (request.stopLoss() != null) {
                orderPayload.put("stop_loss_price", request.stopLoss().toPlainString());
            }
            if (request.takeProfit() != null) {
                orderPayload.put("take_profit_price", request.takeProfit().toPlainString());
            }
            orderPayload.put("client_order_id", clientOrderId);
            orderPayload.put("time_in_force", "gtc");
            orderPayload.put("reduce_only", request.reduceOnly());

            // 4. Submit Order to Delta Exchange India (POST /v2/orders)
            log.info("Submitting live order to Delta India: client_order_id={}, symbol={}, size={}",
                    clientOrderId, deltaSymbol, request.quantity());

            try {
                ResponseEntity<String> response = deltaRestClient.executeRequest(
                        apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, orderPayload
                );

                JsonNode root = objectMapper.readTree(response.getBody());
                JsonNode result = root.path("result");
                String deltaOrderId = result.path("id").asText(null);
                String state = result.path("state").asText("open");
                BigDecimal avgFillPrice = result.has("avg_fill_price") && !result.path("avg_fill_price").isNull()
                        ? new BigDecimal(result.path("avg_fill_price").asText("0")) : null;

                ExecutionState finalState = "filled".equalsIgnoreCase(state) ? ExecutionState.FILLED : ExecutionState.SUBMITTED;

                log.info("Order {} successfully submitted to Delta India: order_id={}, state={}",
                        clientOrderId, deltaOrderId, finalState);

                return ExecutionResult.success(
                        finalState,
                        deltaOrderId,
                        clientOrderId,
                        request.setupId(),
                        request.symbol(),
                        request.direction(),
                        request.quantity(),
                        request.entryPrice(),
                        avgFillPrice,
                        false,
                        null
                );

            } catch (SecurityException e) {
                log.error("Authentication failure during order submission for {}: {}", clientOrderId, e.getMessage());
                return ExecutionResult.failed(clientOrderId, request.setupId(), request.symbol(), "AUTH_ERROR", "Exchange authentication failed", false, null);

            } catch (Exception e) {
                // Timeout / Network Failure / Unknown Outcome -> Reconcile Immediately
                log.error("Submission error or timeout for {}: {}. Initiating reconciliation.", clientOrderId, e.getMessage());

                return reconcileWithExchange(apiKey, apiSecret, deltaSymbol, clientOrderId, request);
            }

        } finally {
            inFlightClientOrderIds.remove(clientOrderId);
        }
    }

    private ExecutionResult reconcileWithExchange(
            String apiKey,
            String apiSecret,
            String productSymbol,
            String clientOrderId,
            ExecutionRequest request
    ) {
        try {
            ResponseEntity<String> openOrdersResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.GET, "/v2/orders", "state=open", null
            );

            JsonNode root = objectMapper.readTree(openOrdersResp.getBody());
            JsonNode orders = root.path("result");

            if (orders.isArray()) {
                for (JsonNode o : orders) {
                    if (clientOrderId.equals(o.path("client_order_id").asText(""))) {
                        String deltaOrderId = o.path("id").asText(null);
                        String state = o.path("state").asText("open");
                        log.info("Order {} RECOVERED during reconciliation! Delta ID: {}, State: {}",
                                clientOrderId, deltaOrderId, state);

                        return ExecutionResult.success(
                                ExecutionState.SUBMITTED,
                                deltaOrderId,
                                clientOrderId,
                                request.setupId(),
                                request.symbol(),
                                request.direction(),
                                request.quantity(),
                                request.entryPrice(),
                                null,
                                true,
                                "Order recovered via Delta Exchange reconciliation after network drop."
                        );
                    }
                }
            }

            log.warn("Order {} was NOT FOUND on Delta Exchange after network timeout.", clientOrderId);
            return ExecutionResult.failed(
                    clientOrderId,
                    request.setupId(),
                    request.symbol(),
                    "SUBMISSION_TIMEOUT",
                    "Network timeout during submission. Verified order was not placed on Delta Exchange.",
                    true,
                    "Reconciliation confirmed order never reached exchange."
            );

        } catch (Exception recErr) {
            log.error("Reconciliation query failed for {}: {}", clientOrderId, recErr.getMessage());
            return ExecutionResult.failed(
                    clientOrderId,
                    request.setupId(),
                    request.symbol(),
                    "RECONCILIATION_FAILED",
                    "Order outcome unknown and reconciliation query failed. Manual check required.",
                    false,
                    recErr.getMessage()
            );
        }
    }
}
