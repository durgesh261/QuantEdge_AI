package com.quantedge.trading.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.account.service.LiveAccountSyncService;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.risk.entity.RiskConfiguration;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class OrderExecutionService {

    private static final Logger log = LoggerFactory.getLogger(OrderExecutionService.class);

    private final OrderValidationGateway validationGateway;
    private final DeltaIndiaRestClient deltaRestClient;
    private final DeltaCredentialService credentialService;
    private final LiveAccountSyncService accountSyncService;
    private final TradingAccountRepository tradingAccountRepository;
    private final DeltaConnectionRepository deltaConnectionRepository;
    private final RiskConfigurationRepository riskConfigRepository;
    private final StrategySetupRepository strategySetupRepository;
    private final OrderRepository orderRepository;
    private final AuditLogRepository auditLogRepository;
    private final ObjectMapper objectMapper;

    // In-flight concurrency lock registry
    private final Set<String> inFlightSetups = ConcurrentHashMap.newKeySet();
    private final Set<String> inFlightClientOrderIds = ConcurrentHashMap.newKeySet();

    private static final List<String> ACTIVE_ORDER_STATUSES = List.of("PENDING", "OPEN", "PARTIALLY_FILLED", "SUBMITTED", "FILLED");

    public OrderExecutionService(
            OrderValidationGateway validationGateway,
            DeltaIndiaRestClient deltaRestClient,
            DeltaCredentialService credentialService,
            LiveAccountSyncService accountSyncService,
            TradingAccountRepository tradingAccountRepository,
            DeltaConnectionRepository deltaConnectionRepository,
            RiskConfigurationRepository riskConfigRepository,
            StrategySetupRepository strategySetupRepository,
            OrderRepository orderRepository,
            AuditLogRepository auditLogRepository,
            ObjectMapper objectMapper
    ) {
        this.validationGateway = validationGateway;
        this.deltaRestClient = deltaRestClient;
        this.credentialService = credentialService;
        this.accountSyncService = accountSyncService;
        this.tradingAccountRepository = tradingAccountRepository;
        this.deltaConnectionRepository = deltaConnectionRepository;
        this.riskConfigRepository = riskConfigRepository;
        this.strategySetupRepository = strategySetupRepository;
        this.orderRepository = orderRepository;
        this.auditLogRepository = auditLogRepository;
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

    public record ExecutionCommand(
            String userId,
            String accountId,
            String setupId,
            String clientOrderId,
            Boolean reduceOnly
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

    @Transactional
    public ExecutionResult executeAuthoritativeOrder(ExecutionCommand command) {
        String clientOrderId = command.clientOrderId() != null && !command.clientOrderId().isBlank()
                ? command.clientOrderId()
                : "QE-" + System.currentTimeMillis() + "-" + UUID.randomUUID().toString().substring(0, 8);

        // 1. Account Ownership & Existence Check
        Optional<TradingAccount> accountOpt = tradingAccountRepository.findById(command.accountId());
        if (accountOpt.isEmpty()) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "ACCOUNT_NOT_FOUND", "Trading account not found.");
        }
        TradingAccount account = accountOpt.get();
        if (command.userId() != null && account.getUser() != null && !account.getUser().getId().equals(command.userId())) {
            log.warn("Unauthorized execution attempt on account {} by user {}", command.accountId(), command.userId());
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "FORBIDDEN", "User is not authorized for this trading account.");
        }

        // 2. Account Active & Kill Switch & Algo Enable Checks
        if (Boolean.FALSE.equals(account.getIsActive())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "ACCOUNT_DISABLED", "Trading account is deactivated.");
        }
        if (Boolean.TRUE.equals(account.getKillSwitchActive())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "KILL_SWITCH_ACTIVE", "Emergency kill switch is active. Order placement blocked.");
        }
        if (Boolean.FALSE.equals(account.getAlgoEnabled())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "ALGO_DISABLED", "Algorithmic trading is disabled on this account.");
        }

        // 3. Load Authoritative Risk Configuration
        Optional<RiskConfiguration> riskOpt = riskConfigRepository.findByTradingAccountId(account.getId());
        if (riskOpt.isEmpty()) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "RISK_CONFIG_MISSING", "Authoritative risk configuration missing for account.");
        }
        RiskConfiguration riskConfig = riskOpt.get();

        // 4. Load Authoritative Strategy Setup
        Optional<StrategySetupRecord> setupOpt = strategySetupRepository.findBySetupId(command.setupId());
        if (setupOpt.isEmpty()) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), null, "SETUP_NOT_FOUND", "Strategy setup '" + command.setupId() + "' not found.");
        }
        StrategySetupRecord setup = setupOpt.get();

        if (!"TRADE_SETUP_READY".equalsIgnoreCase(setup.getSetupState())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DECISION_NOT_READY", "Strategy setup state is '" + setup.getSetupState() + "' (expected 'TRADE_SETUP_READY').");
        }
        if (setup.getExpiresAt() != null && setup.getExpiresAt().isBefore(Instant.now())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "SETUP_EXPIRED", "Strategy setup has expired.");
        }

        // Check TP/SL Geometry
        BigDecimal entry = setup.getEntryPrice();
        BigDecimal sl = setup.getStopLoss();
        BigDecimal tp = setup.getTakeProfit();
        String direction = setup.getDirection().toUpperCase();

        if ("LONG".equals(direction) || "BUY".equals(direction)) {
            if (!(tp.compareTo(entry) > 0 && entry.compareTo(sl) > 0)) {
                return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "INVALID_TP_SL_GEOMETRY", "Invalid LONG geometry: TP must be > Entry and Entry must be > SL.");
            }
        } else {
            if (!(sl.compareTo(entry) > 0 && entry.compareTo(tp) > 0)) {
                return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "INVALID_TP_SL_GEOMETRY", "Invalid SHORT geometry: SL must be > Entry and Entry must be > TP.");
            }
        }

        // 5. Server-Side Credential Retrieval & Decryption (In-Memory Only)
        Optional<DeltaConnection> connOpt = deltaConnectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");
        if (connOpt.isEmpty()) {
            connOpt = deltaConnectionRepository.findByTradingAccountId(account.getId());
        }
        if (connOpt.isEmpty() || connOpt.get().getEncryptedApiKey() == null || connOpt.get().getEncryptedApiSecret() == null) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DELTA_CREDENTIALS_MISSING", "No Delta Exchange live API credentials configured.");
        }
        DeltaConnection connection = connOpt.get();
        String apiKey = credentialService.decrypt(connection.getEncryptedApiKey());
        String apiSecret = credentialService.decrypt(connection.getEncryptedApiSecret());

        if (apiKey.isBlank() || apiSecret.isBlank()) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DELTA_CREDENTIALS_INVALID", "Failed to decrypt valid Delta API credentials.");
        }

        // 6. Live Exchange Synchronization & Authoritative Balance Check
        LiveAccountSyncService.SyncSummary sync = accountSyncService.syncLiveAccount(account.getId(), connection.getEncryptedApiKey(), connection.getEncryptedApiSecret());
        if (!sync.success()) {
            log.error("Live account synchronization failed for {}: {}", account.getId(), sync.error());
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "EXCHANGE_DISCONNECTED", "Live account state unavailable from Delta Exchange: " + sync.error());
        }

        // Update cached account state
        account.setTotalEquity(sync.totalEquity());
        account.setAvailableBalance(sync.availableBalance());
        account.setMarginUsed(sync.marginUsed());
        account.setLastSyncedAt(sync.syncedAt());
        tradingAccountRepository.save(account);

        // Check active positions limit
        if (sync.positionsCount() >= riskConfig.getMaxConcurrentTrades()) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "MAX_CONCURRENT_TRADES_EXCEEDED", "Concurrent open positions (" + sync.positionsCount() + ") reached maximum limit (" + riskConfig.getMaxConcurrentTrades() + ").");
        }

        // 7. Calculate Position Sizing & Margin Requirement
        BigDecimal riskDistance = setup.getRiskDistance() != null && setup.getRiskDistance().compareTo(BigDecimal.ZERO) > 0
                ? setup.getRiskDistance()
                : (entry.subtract(sl)).abs();

        BigDecimal riskAmount = sync.totalEquity().multiply(riskConfig.getRiskPerTradePercent()).divide(BigDecimal.valueOf(100), 8, RoundingMode.HALF_UP);
        BigDecimal calculatedQty = riskDistance.compareTo(BigDecimal.ZERO) > 0
                ? riskAmount.divide(riskDistance, 0, RoundingMode.FLOOR)
                : BigDecimal.ONE;
        if (calculatedQty.compareTo(BigDecimal.ONE) < 0) {
            calculatedQty = BigDecimal.ONE;
        }

        BigDecimal requiredMargin = entry.multiply(calculatedQty).divide(BigDecimal.valueOf(riskConfig.getMaxLeverage()), 8, RoundingMode.HALF_UP);
        if (requiredMargin.compareTo(sync.availableBalance()) > 0) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "INSUFFICIENT_AVAILABLE_MARGIN", "Required margin (" + requiredMargin + ") exceeds available balance (" + sync.availableBalance() + ").");
        }

        // 8. Persistent Database Idempotency & In-Flight Locking
        if (!inFlightSetups.add(command.setupId())) {
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DUPLICATE_SETUP_ID", "Strategy setup is already in-flight.");
        }
        if (!inFlightClientOrderIds.add(clientOrderId)) {
            inFlightSetups.remove(command.setupId());
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DUPLICATE_CLIENT_ORDER_ID", "client_order_id is already in-flight.");
        }

        if (orderRepository.existsByClientOrderId(clientOrderId)) {
            inFlightSetups.remove(command.setupId());
            inFlightClientOrderIds.remove(clientOrderId);
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DUPLICATE_CLIENT_ORDER_ID", "Order with client_order_id '" + clientOrderId + "' already exists in database.");
        }

        if (orderRepository.existsBySetupIdAndStatusIn(command.setupId(), ACTIVE_ORDER_STATUSES)) {
            inFlightSetups.remove(command.setupId());
            inFlightClientOrderIds.remove(clientOrderId);
            return ExecutionResult.rejected(clientOrderId, command.setupId(), setup.getSymbol(), "DUPLICATE_SETUP_ID", "Strategy setup '" + command.setupId() + "' has already been executed.");
        }

        // 9. Pre-Persist Order in Database (PENDING / SUBMITTING)
        Order orderRecord = new Order(
                account,
                command.setupId(),
                clientOrderId,
                setup.getSymbol(),
                direction.contains("BUY") || direction.contains("LONG") ? "BUY" : "SELL",
                "LIMIT",
                calculatedQty,
                entry
        );
        orderRecord.setStopPrice(sl);
        orderRecord.setLeverage(riskConfig.getMaxLeverage());
        orderRecord.setReduceOnly(Boolean.TRUE.equals(command.reduceOnly()));
        orderRecord.setStatus("PENDING");
        orderRecord.setPlacedAt(Instant.now());
        orderRecord = orderRepository.save(orderRecord);

        try {
            // 10. Build Payload and Dispatch to Delta Exchange India (POST /v2/orders)
            String deltaSymbol = setup.getSymbol().replace(".P", "").toUpperCase();
            int productId = getProductIdForSymbol(deltaSymbol);

            Map<String, Object> orderPayload = new HashMap<>();
            orderPayload.put("product_id", productId);
            orderPayload.put("product_symbol", deltaSymbol);
            orderPayload.put("size", calculatedQty.intValue());
            orderPayload.put("side", direction.contains("BUY") || direction.contains("LONG") ? "buy" : "sell");
            orderPayload.put("order_type", "limit_order");
            orderPayload.put("limit_price", entry.toPlainString());
            orderPayload.put("stop_loss_price", sl.toPlainString());
            orderPayload.put("take_profit_price", tp.toPlainString());
            orderPayload.put("client_order_id", clientOrderId);
            orderPayload.put("time_in_force", "gtc");
            orderPayload.put("reduce_only", Boolean.TRUE.equals(command.reduceOnly()));

            log.info("Submitting live order to Delta India: client_order_id={}, symbol={}, size={}, price={}, sl={}, tp={}",
                    clientOrderId, deltaSymbol, calculatedQty, entry, sl, tp);

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

                // Update Order and Setup in DB
                orderRecord.setDeltaOrderId(deltaOrderId);
                orderRecord.setStatus(finalState == ExecutionState.FILLED ? "FILLED" : "OPEN");
                orderRecord.setAverageFillPrice(avgFillPrice);
                orderRepository.save(orderRecord);

                setup.setSetupState("EXECUTED");
                strategySetupRepository.save(setup);

                recordAuditLog(account, "ORDER_SUBMITTED", "Order", orderRecord.getId(),
                        String.format("Submitted live order %s (Delta ID: %s, Symbol: %s, Qty: %s)", clientOrderId, deltaOrderId, deltaSymbol, calculatedQty));

                return ExecutionResult.success(
                        finalState,
                        deltaOrderId,
                        clientOrderId,
                        command.setupId(),
                        setup.getSymbol(),
                        direction,
                        calculatedQty,
                        entry,
                        avgFillPrice,
                        false,
                        null
                );

            } catch (Exception e) {
                // Timeout / Network Failure / Unknown Outcome -> Reconcile Immediately
                log.error("Submission error or timeout for {}: {}. Initiating immediate reconciliation.", clientOrderId, e.getMessage());
                return reconcileWithExchange(apiKey, apiSecret, deltaSymbol, clientOrderId, command.setupId(), setup, orderRecord, account, calculatedQty, entry, direction);
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
            String setupId,
            StrategySetupRecord setup,
            Order orderRecord,
            TradingAccount account,
            BigDecimal quantity,
            BigDecimal entryPrice,
            String direction
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
                        log.info("Order {} RECOVERED during reconciliation! Delta ID: {}", clientOrderId, deltaOrderId);

                        orderRecord.setDeltaOrderId(deltaOrderId);
                        orderRecord.setStatus("OPEN");
                        orderRepository.save(orderRecord);

                        setup.setSetupState("EXECUTED");
                        strategySetupRepository.save(setup);

                        recordAuditLog(account, "ORDER_RECONCILED", "Order", orderRecord.getId(),
                                "Order recovered via Delta reconciliation after timeout: " + deltaOrderId);

                        return ExecutionResult.success(
                                ExecutionState.SUBMITTED,
                                deltaOrderId,
                                clientOrderId,
                                setupId,
                                setup.getSymbol(),
                                direction,
                                quantity,
                                entryPrice,
                                null,
                                true,
                                "Order recovered via Delta Exchange reconciliation after network drop."
                        );
                    }
                }
            }

            log.warn("Order {} was NOT FOUND on Delta Exchange after network timeout.", clientOrderId);
            orderRecord.setStatus("CANCELLED");
            orderRecord.setErrorMessage("Submission timed out and order was not found on exchange.");
            orderRepository.save(orderRecord);

            return ExecutionResult.failed(
                    clientOrderId,
                    setupId,
                    setup.getSymbol(),
                    "SUBMISSION_TIMEOUT",
                    "Network timeout during submission. Verified order was not placed on Delta Exchange.",
                    true,
                    "Reconciliation confirmed order never reached exchange."
            );

        } catch (Exception recErr) {
            log.error("Reconciliation query failed for {}: {}", clientOrderId, recErr.getMessage());
            orderRecord.setStatus("CANCELLED");
            orderRecord.setErrorMessage("Reconciliation failed: " + recErr.getMessage());
            orderRepository.save(orderRecord);

            return ExecutionResult.failed(
                    clientOrderId,
                    setupId,
                    setup.getSymbol(),
                    "RECONCILIATION_FAILED",
                    "Order outcome unknown and reconciliation query failed. Check Delta Exchange terminal.",
                    false,
                    recErr.getMessage()
            );
        }
    }

    private int getProductIdForSymbol(String symbol) {
        return switch (symbol.toUpperCase()) {
            case "ETHUSD", "ETHUSD.P" -> 1399;
            case "SOLUSD", "SOLUSD.P" -> 3074;
            case "XRPUSD", "XRPUSD.P" -> 2197;
            default -> 27; // BTCUSD
        };
    }

    private void recordAuditLog(TradingAccount account, String action, String resourceType, String resourceId, String details) {
        try {
            AuditLog logEntry = new AuditLog(account.getUser(), account, action, resourceType, resourceId, details);
            auditLogRepository.save(logEntry);
        } catch (Exception e) {
            log.warn("Failed to write audit log: {}", e.getMessage());
        }
    }
}
