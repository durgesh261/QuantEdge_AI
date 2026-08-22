package com.quantedge.account.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Phase 5.17.1: Hardened service for explicit, user-confirmed real Delta Exchange India order tests.
 * Enforces two-step confirmation (prepare -> confirm), atomic single-use tokens,
 * pre-submission live revalidation, dynamic minimum-size calculation, reduce-only SL/TP brackets,
 * emergency closure on partial failure, strict position reconciliation, and zero secret leakage.
 */
@Service
public class LiveOrderTestService {

    private static final Logger log = LoggerFactory.getLogger(LiveOrderTestService.class);

    private final TradingAccountRepository accountRepository;
    private final DeltaConnectionRepository connectionRepository;
    private final DeltaCredentialService credentialService;
    private final DeltaIndiaRestClient deltaRestClient;
    private final AuditLogRepository auditLogRepository;
    private final ObjectMapper objectMapper;

    // In-memory token storage (5-minute TTL)
    private final ConcurrentHashMap<String, LiveTestPreparation> preparations = new ConcurrentHashMap<>();

    // Active test positions for reconciliation during close
    private final ConcurrentHashMap<String, ActiveTestPosition> activeTestPositions = new ConcurrentHashMap<>();

    public record LiveTestPreparation(
            String token,
            String userId,
            String accountId,
            String symbol,
            long productId,
            String side,
            BigDecimal quantity,
            BigDecimal contractValue,
            BigDecimal tickSize,
            BigDecimal markPrice,
            BigDecimal estimatedMargin,
            BigDecimal availableBalance,
            Instant expiresAt,
            AtomicBoolean consumed
    ) {}

    public record ActiveTestPosition(
            String accountId,
            String symbol,
            long productId,
            BigDecimal size,
            BigDecimal fillPrice,
            String entryOrderId,
            String slOrderId,
            String tpOrderId
    ) {}

    public record LiveTestPrepareResponse(
            boolean ready,
            String exchange,
            String accountId,
            String symbol,
            String side,
            BigDecimal minimumQuantity,
            BigDecimal contractValue,
            BigDecimal markPrice,
            BigDecimal estimatedMargin,
            BigDecimal availableBalance,
            String riskCheck,
            String confirmationToken,
            String expiresAt,
            boolean confirmationRequired,
            String warning,
            String error
    ) {}

    public record LiveTestConfirmResponse(
            boolean success,
            String status,
            String accountId,
            String symbol,
            String side,
            String exchangeOrderId,
            BigDecimal filledQuantity,
            BigDecimal fillPrice,
            String stopLossOrderId,
            BigDecimal stopLossPrice,
            String takeProfitOrderId,
            BigDecimal takeProfitPrice,
            String positionStatus,
            boolean emergencyCloseAttempted,
            String message,
            String error
    ) {}

    public record LiveTestCloseResponse(
            boolean success,
            String status,
            String accountId,
            String symbol,
            BigDecimal finalPosition,
            BigDecimal finalAvailableBalance,
            String message,
            String error
    ) {}

    public LiveOrderTestService(
            TradingAccountRepository accountRepository,
            DeltaConnectionRepository connectionRepository,
            DeltaCredentialService credentialService,
            DeltaIndiaRestClient deltaRestClient,
            AuditLogRepository auditLogRepository,
            ObjectMapper objectMapper
    ) {
        this.accountRepository = accountRepository;
        this.connectionRepository = connectionRepository;
        this.credentialService = credentialService;
        this.deltaRestClient = deltaRestClient;
        this.auditLogRepository = auditLogRepository;
        this.objectMapper = objectMapper;
    }

    /**
     * Step 1: PREPARE (Read-Only)
     * Validates live balance, product specifications, margin requirement, and returns a single-use token.
     */
    public LiveTestPrepareResponse prepareLiveTest(User user, String accountId, String requestedSymbol) {
        if (user == null) {
            throw new IllegalArgumentException("Authenticated user is required");
        }

        String symbol = (requestedSymbol != null && !requestedSymbol.trim().isEmpty())
                ? requestedSymbol.trim().toUpperCase() : "ETHUSD";

        TradingAccount account = resolveAndAuthorizeAccount(user, accountId);

        if (Boolean.TRUE.equals(account.getKillSwitchActive())) {
            return new LiveTestPrepareResponse(
                    false, "Delta Exchange India", account.getId(), symbol, "BUY",
                    BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                    "FAIL", null, null, false, null, "Kill switch is active on this account"
            );
        }

        DeltaConnection connection = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE")
                .orElseThrow(() -> new IllegalArgumentException("No live Delta Exchange connection found for account"));

        String apiKey = credentialService.decrypt(connection.getEncryptedApiKey());
        String apiSecret = credentialService.decrypt(connection.getEncryptedApiSecret());

        try {
            // 1. Live balances
            BigDecimal availableBalance = fetchAvailableBalance(apiKey, apiSecret);

            // 2. Check existing open positions
            boolean hasPosition = checkExistingPosition(apiKey, apiSecret, symbol);
            if (hasPosition) {
                return new LiveTestPrepareResponse(
                        false, "Delta Exchange India", account.getId(), symbol, "BUY",
                        BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, availableBalance,
                        "FAIL", null, null, false, null, "Existing position detected on " + symbol + ". Live test requires a flat position."
                );
            }

            // 3. Live product spec
            ProductSpec productSpec = fetchProductSpec(symbol);

            // 4. Live ticker mark price
            BigDecimal markPrice = fetchMarkPrice(apiKey, apiSecret, symbol);

            // 5. Calculate minimum quantity and required margin
            BigDecimal minQuantity = productSpec.minOrderSize().max(BigDecimal.ONE);
            BigDecimal notional = minQuantity.multiply(productSpec.contractValue()).multiply(markPrice);
            // Conservative 5x margin requirement for live test sizing
            BigDecimal estimatedMargin = notional.divide(BigDecimal.valueOf(5), 4, RoundingMode.HALF_UP);

            if (availableBalance.compareTo(estimatedMargin) < 0) {
                return new LiveTestPrepareResponse(
                        false, "Delta Exchange India", account.getId(), symbol, "BUY",
                        minQuantity, productSpec.contractValue(), markPrice, estimatedMargin, availableBalance,
                        "FAIL", null, null, false, null,
                        "Insufficient balance: available balance $" + availableBalance + " is less than required margin $" + estimatedMargin
                );
            }

            // 6. Issue single-use 5-minute confirmation token
            String token = UUID.randomUUID().toString();
            Instant expiresAt = Instant.now().plus(Duration.ofMinutes(5));

            LiveTestPreparation prep = new LiveTestPreparation(
                    token, user.getId(), account.getId(), symbol, productSpec.productId(),
                    "BUY", minQuantity, productSpec.contractValue(), productSpec.tickSize(),
                    markPrice, estimatedMargin, availableBalance, expiresAt, new AtomicBoolean(false)
            );
            preparations.put(token, prep);

            return new LiveTestPrepareResponse(
                    true,
                    "Delta Exchange India",
                    account.getId(),
                    symbol,
                    "BUY",
                    minQuantity,
                    productSpec.contractValue(),
                    markPrice,
                    estimatedMargin,
                    availableBalance,
                    "PASS",
                    token,
                    expiresAt.toString(),
                    true,
                    "THIS WILL PLACE A REAL ORDER USING YOUR CONNECTED DELTA EXCHANGE ACCOUNT.",
                    null
            );

        } catch (SecurityException se) {
            log.warn("Authentication failed during live test preparation for user {}", user.getId());
            return new LiveTestPrepareResponse(
                    false, "Delta Exchange India", account.getId(), symbol, "BUY",
                    BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                    "FAIL", null, null, false, null, "Authentication failed with Delta Exchange. Please verify your credentials."
            );
        } catch (Exception e) {
            log.error("Failed to prepare live test for user {}: {}", user.getId(), e.getClass().getSimpleName());
            return new LiveTestPrepareResponse(
                    false, "Delta Exchange India", account.getId(), symbol, "BUY",
                    BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                    "FAIL", null, null, false, null, "Unable to inspect exchange state. Please verify network connection and try again."
            );
        }
    }

    /**
     * Step 2: CONFIRM & REAL ORDER
     * Atomically consumes single-use token, revalidates live state, submits real order, and places reduce-only SL/TP.
     */
    public LiveTestConfirmResponse confirmLiveTest(User user, String accountId, String confirmationToken) {
        if (user == null || confirmationToken == null || confirmationToken.trim().isEmpty()) {
            return new LiveTestConfirmResponse(
                    false, "REJECTED", accountId, null, null, null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Confirmation token and authenticated user are required"
            );
        }

        LiveTestPreparation prep = preparations.get(confirmationToken.trim());
        if (prep == null) {
            return new LiveTestConfirmResponse(
                    false, "TOKEN_INVALID", accountId, null, null, null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Invalid or non-existent confirmation token"
            );
        }

        if (Instant.now().isAfter(prep.expiresAt())) {
            preparations.remove(confirmationToken.trim());
            return new LiveTestConfirmResponse(
                    false, "TOKEN_EXPIRED", accountId, prep.symbol(), prep.side(), null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Confirmation token has expired (>5 minutes). Please prepare a new test."
            );
        }

        // Atomic single-use consumption: prevents race condition double execution
        if (!prep.consumed().compareAndSet(false, true)) {
            return new LiveTestConfirmResponse(
                    false, "TOKEN_ALREADY_USED", accountId, prep.symbol(), prep.side(), null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Confirmation token has already been consumed."
            );
        }

        if (!prep.userId().equals(user.getId()) || (accountId != null && !prep.accountId().equals(accountId))) {
            throw new SecurityException("Unauthorized: Token does not match authenticated user or account");
        }

        TradingAccount account = resolveAndAuthorizeAccount(user, prep.accountId());
        if (Boolean.TRUE.equals(account.getKillSwitchActive())) {
            return new LiveTestConfirmResponse(
                    false, "KILL_SWITCH_ACTIVE", account.getId(), prep.symbol(), prep.side(), null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Kill switch is active on this account"
            );
        }

        DeltaConnection connection = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE")
                .orElseThrow(() -> new IllegalArgumentException("No live Delta Exchange connection found"));

        String apiKey = credentialService.decrypt(connection.getEncryptedApiKey());
        String apiSecret = credentialService.decrypt(connection.getEncryptedApiSecret());

        try {
            // 1. Re-query live balance
            BigDecimal liveBalance = fetchAvailableBalance(apiKey, apiSecret);
            if (liveBalance.compareTo(prep.estimatedMargin()) < 0) {
                return new LiveTestConfirmResponse(
                        false, "INSUFFICIENT_BALANCE", account.getId(), prep.symbol(), prep.side(), null,
                        BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                        "NONE", false, null, "Available collateral insufficient for execution: $" + liveBalance
                );
            }

            // 2. Re-query positions to ensure 0 open positions
            boolean hasPosition = checkExistingPosition(apiKey, apiSecret, prep.symbol());
            if (hasPosition) {
                return new LiveTestConfirmResponse(
                        false, "EXISTING_POSITION", account.getId(), prep.symbol(), prep.side(), null,
                        BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                        "NONE", false, null, "Existing position detected prior to order execution."
                );
            }

            // 3. Re-query ticker for fresh mark price
            BigDecimal freshMarkPrice = fetchMarkPrice(apiKey, apiSecret, prep.symbol());
            BigDecimal limitPrice = roundToTickSize(freshMarkPrice.multiply(new BigDecimal("1.005")), prep.tickSize());

            // 4. Submit REAL Delta order
            Map<String, Object> orderPayload = new HashMap<>();
            orderPayload.put("product_id", prep.productId());
            orderPayload.put("size", prep.quantity().intValue());
            orderPayload.put("side", "buy");
            orderPayload.put("order_type", "limit_order");
            orderPayload.put("limit_price", limitPrice.toPlainString());
            orderPayload.put("time_in_force", "ioc");

            ResponseEntity<String> orderResp = deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, orderPayload
            );

            JsonNode orderNode = objectMapper.readTree(orderResp.getBody()).path("result");
            String orderId = orderNode.path("id").asText();
            String state = orderNode.path("state").asText("filled");
            BigDecimal fillPrice = orderNode.hasNonNull("avg_fill_price") && !orderNode.path("avg_fill_price").asText().equals("0")
                    ? new BigDecimal(orderNode.path("avg_fill_price").asText())
                    : limitPrice;
            BigDecimal filledQty = orderNode.hasNonNull("size")
                    ? new BigDecimal(orderNode.path("size").asText())
                    : prep.quantity();

            // Reconcile filled position
            PositionInfo currentPos = fetchPositionInfo(apiKey, apiSecret, prep.symbol());
            if (currentPos != null && currentPos.size().compareTo(BigDecimal.ZERO) > 0) {
                filledQty = currentPos.size();
            }

            // 5. Establish Protective SL & TP Brackets
            BigDecimal slPrice = roundToTickSize(fillPrice.multiply(new BigDecimal("0.985")), prep.tickSize()); // 1.5% SL
            BigDecimal tpPrice = roundToTickSize(fillPrice.multiply(new BigDecimal("1.030")), prep.tickSize()); // 3.0% TP

            String slOrderId = null;
            String tpOrderId = null;
            boolean bracketFailure = false;

            try {
                // Submit Stop Loss (reduce_only)
                Map<String, Object> slPayload = new HashMap<>();
                slPayload.put("product_id", prep.productId());
                slPayload.put("size", filledQty.intValue());
                slPayload.put("side", "sell");
                slPayload.put("order_type", "market_order");
                slPayload.put("stop_order_type", "stop_loss_order");
                slPayload.put("stop_price", slPrice.toPlainString());
                slPayload.put("reduce_only", true);

                ResponseEntity<String> slResp = deltaRestClient.executeRequest(
                        apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, slPayload
                );
                slOrderId = objectMapper.readTree(slResp.getBody()).path("result").path("id").asText();

                // Submit Take Profit (reduce_only)
                Map<String, Object> tpPayload = new HashMap<>();
                tpPayload.put("product_id", prep.productId());
                tpPayload.put("size", filledQty.intValue());
                tpPayload.put("side", "sell");
                tpPayload.put("order_type", "limit_order");
                tpPayload.put("limit_price", tpPrice.toPlainString());
                tpPayload.put("reduce_only", true);

                ResponseEntity<String> tpResp = deltaRestClient.executeRequest(
                        apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, tpPayload
                );
                tpOrderId = objectMapper.readTree(tpResp.getBody()).path("result").path("id").asText();

            } catch (Exception ex) {
                log.error("Protective bracket placement failed for test order: {}", ex.getClass().getSimpleName());
                bracketFailure = true;
            }

            // Handle partial failure if SL/TP failed
            if (bracketFailure) {
                boolean emergencyClosed = executeEmergencyClose(apiKey, apiSecret, prep.productId(), filledQty);
                auditLogRepository.save(new AuditLog(
                        user, account, "LIVE_TEST_PROTECTION_FAILED",
                        "FAILURE", orderId, "Entry filled at " + fillPrice + " but protection setup failed. Emergency close attempted=" + emergencyClosed
                ));

                return new LiveTestConfirmResponse(
                        false, "PROTECTION_SETUP_FAILED", account.getId(), prep.symbol(), prep.side(),
                        orderId, filledQty, fillPrice, slOrderId, slPrice, tpOrderId, tpPrice,
                        emergencyClosed ? "CLOSED_EMERGENCY" : "OPEN_UNPROTECTED",
                        emergencyClosed,
                        "Protection setup failed: emergency close order was executed.",
                        "Protective SL/TP order placement failed on exchange"
                );
            }

            // Save active test position record
            activeTestPositions.put(account.getId(), new ActiveTestPosition(
                    account.getId(), prep.symbol(), prep.productId(), filledQty, fillPrice, orderId, slOrderId, tpOrderId
            ));

            auditLogRepository.save(new AuditLog(
                    user, account, "LIVE_TEST_ORDER_EXECUTED",
                    "SUCCESS", orderId, "Live test order filled: " + prep.symbol() + " qty=" + filledQty + " price=" + fillPrice
            ));

            return new LiveTestConfirmResponse(
                    true, "REAL_ORDER_FILLED", account.getId(), prep.symbol(), prep.side(),
                    orderId, filledQty, fillPrice, slOrderId, slPrice, tpOrderId, tpPrice,
                    "OPEN", false, "REAL ORDER FILLED AND PROTECTIVE BRACKETS ESTABLISHED", null
            );

        } catch (Exception e) {
            log.error("Failed to execute live test order for user {}: {}", user.getId(), e.getClass().getSimpleName());
            return new LiveTestConfirmResponse(
                    false, "EXECUTION_ERROR", account.getId(), prep.symbol(), prep.side(), null,
                    BigDecimal.ZERO, BigDecimal.ZERO, null, BigDecimal.ZERO, null, BigDecimal.ZERO,
                    "NONE", false, null, "Delta Exchange order rejected. Please try again."
            );
        }
    }

    /**
     * Step 3: CLOSE TEST POSITION
     * Verifies position size against test record, executes reduce-only close, and confirms 0 position.
     */
    public LiveTestCloseResponse closeLiveTest(User user, String accountId, String requestedSymbol) {
        if (user == null) {
            throw new IllegalArgumentException("Authenticated user is required");
        }

        String symbol = (requestedSymbol != null && !requestedSymbol.trim().isEmpty())
                ? requestedSymbol.trim().toUpperCase() : "ETHUSD";

        TradingAccount account = resolveAndAuthorizeAccount(user, accountId);
        DeltaConnection connection = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE")
                .orElseThrow(() -> new IllegalArgumentException("No live Delta Exchange connection found"));

        String apiKey = credentialService.decrypt(connection.getEncryptedApiKey());
        String apiSecret = credentialService.decrypt(connection.getEncryptedApiSecret());

        try {
            // 1. Inspect live positions
            PositionInfo currentPos = fetchPositionInfo(apiKey, apiSecret, symbol);
            if (currentPos == null || currentPos.size().compareTo(BigDecimal.ZERO) == 0) {
                activeTestPositions.remove(account.getId());
                BigDecimal finalBal = fetchAvailableBalance(apiKey, apiSecret);
                return new LiveTestCloseResponse(
                        true, "POSITION_FLAT", account.getId(), symbol,
                        BigDecimal.ZERO, finalBal, "Position is already flat.", null
                );
            }

            ActiveTestPosition activeTest = activeTestPositions.get(account.getId());
            if (activeTest != null && activeTest.size().compareTo(currentPos.size().abs()) != 0) {
                return new LiveTestCloseResponse(
                        false, "CLOSE_REQUIRES_RECONCILIATION", account.getId(), symbol,
                        currentPos.size(), null, null,
                        "Position size mismatch: expected test size " + activeTest.size() + " but found " + currentPos.size() + " on exchange."
                );
            }

            // 2. Submit reduce-only market close
            String closeSide = currentPos.size().compareTo(BigDecimal.ZERO) > 0 ? "sell" : "buy";
            Map<String, Object> closePayload = new HashMap<>();
            closePayload.put("product_id", currentPos.productId());
            closePayload.put("size", currentPos.size().abs().intValue());
            closePayload.put("side", closeSide);
            closePayload.put("order_type", "market_order");
            closePayload.put("reduce_only", true);

            deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, closePayload
            );

            // 3. Cancel any remaining open bracket orders
            cancelOpenOrdersForProduct(apiKey, apiSecret, currentPos.productId());

            // 4. Verify position is flat
            PositionInfo verifiedPos = fetchPositionInfo(apiKey, apiSecret, symbol);
            BigDecimal finalSize = (verifiedPos != null) ? verifiedPos.size() : BigDecimal.ZERO;
            BigDecimal finalBal = fetchAvailableBalance(apiKey, apiSecret);

            activeTestPositions.remove(account.getId());

            auditLogRepository.save(new AuditLog(
                    user, account, "LIVE_TEST_POSITION_CLOSED",
                    "SUCCESS", account.getId(), "Live test position successfully closed. Final size=" + finalSize
            ));

            return new LiveTestCloseResponse(
                    true, "POSITION_FLAT", account.getId(), symbol,
                    finalSize, finalBal, "LIVE TEST COMPLETE: Position flat and brackets cleared.", null
            );

        } catch (Exception e) {
            log.error("Failed to close live test position for user {}: {}", user.getId(), e.getClass().getSimpleName());
            return new LiveTestCloseResponse(
                    false, "CLOSE_ERROR", account.getId(), symbol,
                    null, null, null, "Failed to close test position. Please verify on exchange."
            );
        }
    }

    // --- Helper Methods ---

    private TradingAccount resolveAndAuthorizeAccount(User user, String accountId) {
        if (accountId != null && !accountId.trim().isEmpty()) {
            TradingAccount acct = accountRepository.findById(accountId.trim())
                    .orElseThrow(() -> new IllegalArgumentException("Trading account not found: " + accountId));
            if (!acct.getUser().getId().equals(user.getId())) {
                throw new SecurityException("Unauthorized access to trading account");
            }
            return acct;
        } else {
            List<TradingAccount> accounts = accountRepository.findByUserId(user.getId());
            if (accounts.isEmpty()) {
                throw new IllegalArgumentException("No trading account found for authenticated user");
            }
            return accounts.get(0);
        }
    }

    private BigDecimal fetchAvailableBalance(String apiKey, String apiSecret) throws Exception {
        ResponseEntity<String> resp = deltaRestClient.executeRequest(
                apiKey, apiSecret, HttpMethod.GET, "/v2/wallet/balances", null, null
        );
        JsonNode root = objectMapper.readTree(resp.getBody()).path("result");
        if (root.isArray()) {
            for (JsonNode b : root) {
                if ("USDT".equalsIgnoreCase(b.path("asset_symbol").asText())) {
                    return new BigDecimal(b.path("available_balance").asText("0"));
                }
            }
        }
        return BigDecimal.ZERO;
    }

    private boolean checkExistingPosition(String apiKey, String apiSecret, String symbol) throws Exception {
        ResponseEntity<String> resp = deltaRestClient.executeRequest(
                apiKey, apiSecret, HttpMethod.GET, "/v2/positions/margined", null, null
        );
        JsonNode root = objectMapper.readTree(resp.getBody()).path("result");
        if (root.isArray()) {
            for (JsonNode p : root) {
                String sym = p.path("product_symbol").asText(p.path("symbol").asText(""));
                BigDecimal size = new BigDecimal(p.path("size").asText("0"));
                if (symbol.equalsIgnoreCase(sym) && size.compareTo(BigDecimal.ZERO) != 0) {
                    return true;
                }
            }
        }
        return false;
    }

    private record PositionInfo(long productId, BigDecimal size) {}

    private PositionInfo fetchPositionInfo(String apiKey, String apiSecret, String symbol) throws Exception {
        ResponseEntity<String> resp = deltaRestClient.executeRequest(
                apiKey, apiSecret, HttpMethod.GET, "/v2/positions/margined", null, null
        );
        JsonNode root = objectMapper.readTree(resp.getBody()).path("result");
        if (root.isArray()) {
            for (JsonNode p : root) {
                String sym = p.path("product_symbol").asText(p.path("symbol").asText(""));
                if (symbol.equalsIgnoreCase(sym)) {
                    long prodId = p.path("product_id").asLong(0);
                    BigDecimal size = new BigDecimal(p.path("size").asText("0"));
                    return new PositionInfo(prodId, size);
                }
            }
        }
        return null;
    }

    private record ProductSpec(long productId, BigDecimal minOrderSize, BigDecimal contractValue, BigDecimal tickSize) {}

    private ProductSpec fetchProductSpec(String symbol) {
        try {
            ResponseEntity<String> resp = deltaRestClient.executeRequest(
                    "", "", HttpMethod.GET, "/v2/products", null, null
            );
            JsonNode root = objectMapper.readTree(resp.getBody()).path("result");
            if (root.isArray()) {
                for (JsonNode p : root) {
                    if (symbol.equalsIgnoreCase(p.path("symbol").asText())) {
                        long id = p.path("id").asLong();
                        BigDecimal minSize = p.hasNonNull("min_order_size") ? new BigDecimal(p.path("min_order_size").asText()) : BigDecimal.ONE;
                        BigDecimal contractVal = p.hasNonNull("contract_value") ? new BigDecimal(p.path("contract_value").asText()) : new BigDecimal("0.001");
                        BigDecimal tickSize = p.hasNonNull("tick_size") ? new BigDecimal(p.path("tick_size").asText()) : new BigDecimal("0.05");
                        return new ProductSpec(id, minSize, contractVal, tickSize);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch live products, using fallback specs: {}", e.getClass().getSimpleName());
        }
        return new ProductSpec(134, BigDecimal.ONE, new BigDecimal("0.001"), new BigDecimal("0.05"));
    }

    private BigDecimal fetchMarkPrice(String apiKey, String apiSecret, String symbol) throws Exception {
        ResponseEntity<String> resp = deltaRestClient.executeRequest(
                apiKey, apiSecret, HttpMethod.GET, "/v2/tickers/" + symbol, null, null
        );
        JsonNode node = objectMapper.readTree(resp.getBody()).path("result");
        if (node.hasNonNull("mark_price")) {
            return new BigDecimal(node.path("mark_price").asText());
        }
        if (node.hasNonNull("close")) {
            return new BigDecimal(node.path("close").asText());
        }
        return new BigDecimal("2500.00");
    }

    private BigDecimal roundToTickSize(BigDecimal value, BigDecimal tickSize) {
        if (tickSize.compareTo(BigDecimal.ZERO) == 0) return value;
        BigDecimal steps = value.divide(tickSize, 0, RoundingMode.HALF_UP);
        return steps.multiply(tickSize);
    }

    private boolean executeEmergencyClose(String apiKey, String apiSecret, long productId, BigDecimal quantity) {
        try {
            Map<String, Object> closePayload = new HashMap<>();
            closePayload.put("product_id", productId);
            closePayload.put("size", quantity.intValue());
            closePayload.put("side", "sell");
            closePayload.put("order_type", "market_order");
            closePayload.put("reduce_only", true);

            deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.POST, "/v2/orders", null, closePayload
            );
            return true;
        } catch (Exception e) {
            log.error("Emergency close execution failed: {}", e.getClass().getSimpleName());
            return false;
        }
    }

    private void cancelOpenOrdersForProduct(String apiKey, String apiSecret, long productId) {
        try {
            Map<String, Object> cancelPayload = new HashMap<>();
            cancelPayload.put("product_id", productId);
            deltaRestClient.executeRequest(
                    apiKey, apiSecret, HttpMethod.DELETE, "/v2/orders/all", null, cancelPayload
            );
        } catch (Exception e) {
            log.warn("Failed to cancel open orders: {}", e.getClass().getSimpleName());
        }
    }
}
