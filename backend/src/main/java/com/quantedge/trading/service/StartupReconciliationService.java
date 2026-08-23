package com.quantedge.trading.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Service for deterministic crash recovery and exchange reconciliation on application startup.
 *
 * <h3>Guarantees:</h3>
 * <ul>
 *   <li>READ-ONLY with respect to exchange order placement (NEVER submits/cancels orders on exchange).</li>
 *   <li>Recovers pending/unknown orders after crash/restart using deterministic client_order_id.</li>
 *   <li>Reconciles open positions and clears orphaned database locks.</li>
 * </ul>
 */
@Service
public class StartupReconciliationService {

    private static final Logger log = LoggerFactory.getLogger(StartupReconciliationService.class);

    private final OrderRepository orderRepository;
    private final PositionRepository positionRepository;
    private final ActiveTradeLockRepository lockRepository;
    private final TradingAccountRepository accountRepository;
    private final DeltaConnectionRepository deltaConnectionRepository;
    private final DeltaCredentialService credentialService;
    private final DeltaIndiaRestClient deltaRestClient;
    private final TradePersistenceService tradePersistenceService;
    private final ObjectMapper objectMapper;

    public static final List<String> UNRESOLVED_STATUSES = List.of(
            OrderStatus.CREATED.name(),
            OrderStatus.SUBMISSION_PENDING.name(),
            OrderStatus.SUBMITTED.name(),
            OrderStatus.OPEN.name(),
            OrderStatus.PARTIALLY_FILLED.name(),
            OrderStatus.UNKNOWN.name(),
            "PENDING"
    );

    public StartupReconciliationService(
            OrderRepository orderRepository,
            PositionRepository positionRepository,
            ActiveTradeLockRepository lockRepository,
            TradingAccountRepository accountRepository,
            DeltaConnectionRepository deltaConnectionRepository,
            DeltaCredentialService credentialService,
            DeltaIndiaRestClient deltaRestClient,
            TradePersistenceService tradePersistenceService,
            ObjectMapper objectMapper
    ) {
        this.orderRepository = orderRepository;
        this.positionRepository = positionRepository;
        this.lockRepository = lockRepository;
        this.accountRepository = accountRepository;
        this.deltaConnectionRepository = deltaConnectionRepository;
        this.credentialService = credentialService;
        this.deltaRestClient = deltaRestClient;
        this.tradePersistenceService = tradePersistenceService;
        this.objectMapper = objectMapper;
    }

    public record ReconciliationReport(
            boolean success,
            int ordersReconciled,
            int ordersFailed,
            int positionsReconciled,
            int locksCleared,
            String message
    ) {}

    @EventListener(ApplicationReadyEvent.class)
    public void onApplicationReady() {
        log.info("QuantEdge AI Startup: Running authoritative exchange reconciliation...");
        try {
            ReconciliationReport report = runFullReconciliation();
            log.info("Startup reconciliation complete: {}", report);
        } catch (Exception e) {
            log.error("Startup reconciliation encountered an error: {}", e.getMessage(), e);
        }
    }

    @Transactional
    public ReconciliationReport runFullReconciliation() {
        List<TradingAccount> accounts = accountRepository.findAll();
        int ordersReconciled = 0;
        int ordersFailed = 0;
        int positionsReconciled = 0;
        int locksCleared = 0;

        for (TradingAccount account : accounts) {
            Optional<DeltaConnection> connOpt = deltaConnectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");
            if (connOpt.isEmpty()) {
                connOpt = deltaConnectionRepository.findByTradingAccountId(account.getId());
            }
            if (connOpt.isEmpty() || connOpt.get().getEncryptedApiKey() == null || connOpt.get().getEncryptedApiSecret() == null) {
                continue;
            }

            String apiKey;
            String apiSecret;
            try {
                apiKey = credentialService.decrypt(connOpt.get().getEncryptedApiKey());
                apiSecret = credentialService.decrypt(connOpt.get().getEncryptedApiSecret());
                if (apiKey.isBlank() || apiSecret.isBlank()) continue;
            } catch (Exception e) {
                log.warn("Could not decrypt credentials for account {}: {}", account.getId(), e.getMessage());
                continue;
            }

            // 1. Reconcile active / unresolved orders
            List<Order> unresolvedOrders = orderRepository.findByTradingAccountIdAndStatusIn(account.getId(), UNRESOLVED_STATUSES);
            if (!unresolvedOrders.isEmpty()) {
                try {
                    ResponseEntity<String> openOrdersResp = deltaRestClient.executeRequest(
                            apiKey, apiSecret, HttpMethod.GET, "/v2/orders", "state=open", null
                    );
                    JsonNode root = objectMapper.readTree(openOrdersResp.getBody());
                    JsonNode exchangeOrders = root.path("result");

                    for (Order order : unresolvedOrders) {
                        boolean found = false;
                        if (exchangeOrders.isArray()) {
                            for (JsonNode exOrder : exchangeOrders) {
                                String clientOrderId = exOrder.path("client_order_id").asText("");
                                String deltaId = exOrder.path("id").asText("");
                                if (order.getClientOrderId().equals(clientOrderId) || (order.getDeltaOrderId() != null && order.getDeltaOrderId().equals(deltaId))) {
                                    found = true;
                                    order.setDeltaOrderId(deltaId);
                                    order.transitionStatus(OrderStatus.OPEN);
                                    order.setReconciliationState("RECONCILED");
                                    orderRepository.saveAndFlush(order);
                                    ordersReconciled++;
                                    log.info("Reconciled order {} to OPEN", order.getClientOrderId());
                                    break;
                                }
                            }
                        }

                        if (!found) {
                            if (OrderStatus.SUBMISSION_PENDING.name().equalsIgnoreCase(order.getStatus())
                                    || OrderStatus.CREATED.name().equalsIgnoreCase(order.getStatus())
                                    || "PENDING".equalsIgnoreCase(order.getStatus())
                                    || OrderStatus.UNKNOWN.name().equalsIgnoreCase(order.getStatus())) {
                                order.transitionStatus(OrderStatus.FAILED);
                                order.setErrorMessage("Reconciliation confirmed order never placed on exchange before restart.");
                                order.setReconciliationState("RECONCILED");
                                orderRepository.saveAndFlush(order);
                                ordersFailed++;
                                log.info("Reconciled unplaced order {} to FAILED", order.getClientOrderId());
                            }
                        }
                    }
                } catch (Exception e) {
                    log.error("Failed to reconcile orders for account {}: {}", account.getId(), e.getMessage());
                }
            }

            // 2. Reconcile positions
            try {
                ResponseEntity<String> posResp = deltaRestClient.executeRequest(
                        apiKey, apiSecret, HttpMethod.GET, "/v2/positions/margined", null, null
                );
                JsonNode posRoot = objectMapper.readTree(posResp.getBody());
                JsonNode posList = posRoot.path("result");
                List<TradePersistenceService.PositionReconciliationData> exchangePositions = new ArrayList<>();

                if (posList.isArray()) {
                    for (JsonNode p : posList) {
                        BigDecimal size = new BigDecimal(p.path("size").asText("0"));
                        if (size.compareTo(BigDecimal.ZERO) != 0) {
                            String sym = p.path("product_symbol").asText(p.path("symbol").asText(""));
                            String side = size.compareTo(BigDecimal.ZERO) > 0 ? "LONG" : "SHORT";
                            BigDecimal entryPrice = new BigDecimal(p.path("entry_price").asText("0"));
                            BigDecimal markPrice = new BigDecimal(p.path("mark_price").asText("0"));
                            BigDecimal upnl = new BigDecimal(p.path("unrealized_pnl").asText("0"));
                            BigDecimal rpnl = new BigDecimal(p.path("realized_pnl").asText("0"));
                            int lev = p.path("leverage").asInt(1);
                            BigDecimal margin = new BigDecimal(p.path("margin").asText("0"));
                            BigDecimal liqPrice = p.hasNonNull("liquidation_price") ? new BigDecimal(p.path("liquidation_price").asText()) : null;

                            exchangePositions.add(new TradePersistenceService.PositionReconciliationData(
                                    sym, side, size.abs(), entryPrice, markPrice, upnl, rpnl, lev, margin, liqPrice
                            ));
                        }
                    }
                }

                tradePersistenceService.reconcilePositions(account.getId(), exchangePositions);
                positionsReconciled += exchangePositions.size();

                // 3. Clear orphaned active trade locks if no matching position exists
                Optional<ActiveTradeLock> activeLock = lockRepository.findActiveLockByAccountId(account.getId());
                if (activeLock.isPresent()) {
                    boolean hasOpenPos = exchangePositions.stream().anyMatch(ep -> ep.symbol().equalsIgnoreCase(activeLock.get().getSymbol()));
                    if (!hasOpenPos) {
                        log.warn("Clearing orphaned active lock for setup {} on account {}", activeLock.get().getSetupId(), account.getId());
                        tradePersistenceService.forceReleaseLock(account.getId(), "RECONCILIATION_NOT_FOUND_ON_EXCHANGE");
                        locksCleared++;
                    }
                }

            } catch (Exception e) {
                log.error("Failed to reconcile positions for account {}: {}", account.getId(), e.getMessage());
            }
        }

        return new ReconciliationReport(true, ordersReconciled, ordersFailed, positionsReconciled, locksCleared, "Reconciliation successful.");
    }
}
