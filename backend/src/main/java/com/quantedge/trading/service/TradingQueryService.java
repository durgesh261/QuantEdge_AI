package com.quantedge.trading.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.market.service.InstrumentRegistry;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.dto.*;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.entity.TradeRecord;
import com.quantedge.trading.execution.OrderFill;
import com.quantedge.trading.execution.OrderFillRepository;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.position.Position;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.OrderRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

/**
 * Authoritative Query Service for Phase 7 Backend Trading API.
 *
 * <h3>Security & Tenant Isolation Guarantees:</h3>
 * <ul>
 *   <li>Every query enforces ownership: the authenticated user must own the requested trading account.</li>
 *   <li>IDOR attempts immediately throw {@link AccessDeniedException} resulting in HTTP 403 Forbidden.</li>
 *   <li>Zero exposure of passwords, API keys, encryption secrets, or internal engine tokens in DTO responses.</li>
 * </ul>
 */
@Service
@Transactional(readOnly = true)
public class TradingQueryService {

    private static final Logger log = LoggerFactory.getLogger(TradingQueryService.class);

    private final TradingAccountRepository accountRepository;
    private final OrderRepository orderRepository;
    private final PositionRepository positionRepository;
    private final OrderFillRepository orderFillRepository;
    private final TradeRecordRepository tradeRecordRepository;
    private final StrategySetupRepository strategySetupRepository;
    private final DeltaConnectionRepository deltaConnectionRepository;
    private final ActiveTradeLockRepository lockRepository;
    private final DeltaCredentialService credentialService;

    public TradingQueryService(
            TradingAccountRepository accountRepository,
            OrderRepository orderRepository,
            PositionRepository positionRepository,
            OrderFillRepository orderFillRepository,
            TradeRecordRepository tradeRecordRepository,
            StrategySetupRepository strategySetupRepository,
            DeltaConnectionRepository deltaConnectionRepository,
            ActiveTradeLockRepository lockRepository,
            DeltaCredentialService credentialService
    ) {
        this.accountRepository = accountRepository;
        this.orderRepository = orderRepository;
        this.positionRepository = positionRepository;
        this.orderFillRepository = orderFillRepository;
        this.tradeRecordRepository = tradeRecordRepository;
        this.strategySetupRepository = strategySetupRepository;
        this.deltaConnectionRepository = deltaConnectionRepository;
        this.lockRepository = lockRepository;
        this.credentialService = credentialService;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Tenant Verification Helper
    // ─────────────────────────────────────────────────────────────────────────

    public TradingAccount resolveAndVerifyAccount(User user, String accountId) {
        if (user == null || user.getId() == null) {
            throw new AccessDeniedException("Authentication required to access trading state.");
        }

        if (accountId != null && !accountId.isBlank()) {
            TradingAccount account = accountRepository.findById(accountId)
                    .orElseThrow(() -> new IllegalArgumentException("Trading account not found: " + accountId));

            if (account.getUser() == null || !user.getId().equals(account.getUser().getId())) {
                log.warn("IDOR attempt detected: User {} tried to access Account {} owned by {}",
                        user.getId(), accountId, account.getUser() != null ? account.getUser().getId() : "null");
                throw new AccessDeniedException("Access denied: You do not own trading account " + accountId);
            }
            return account;
        }

        // Default to user's first active account or any account
        List<TradingAccount> accounts = accountRepository.findByUserId(user.getId());
        if (accounts.isEmpty()) {
            log.warn("No trading account configured for user: {}", user.getId());
            throw new IllegalArgumentException("No trading account is configured for this account.");
        }

        return accounts.stream()
                .filter(a -> Boolean.TRUE.equals(a.getIsActive()))
                .findFirst()
                .orElse(accounts.getFirst());
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1. Trading System Status API
    // ─────────────────────────────────────────────────────────────────────────

    public TradingSystemStatusDto getTradingSystemStatus(User user, String accountId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        Optional<DeltaConnection> connOpt = deltaConnectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");
        if (connOpt.isEmpty()) {
            connOpt = deltaConnectionRepository.findByTradingAccountId(account.getId());
        }

        boolean connected = false;
        String connectionStatus = "DISCONNECTED";
        String environment = "TESTNET";
        String maskedApiKey = null;
        java.time.Instant lastConnectedAt = null;

        if (connOpt.isPresent()) {
            DeltaConnection conn = connOpt.get();
            connected = "CONNECTED".equalsIgnoreCase(conn.getConnectionStatus());
            connectionStatus = conn.getConnectionStatus();
            environment = conn.getEnvironment();
            lastConnectedAt = conn.getLastConnectedAt();
            if (conn.getEncryptedApiKey() != null) {
                try {
                    String decryptedKey = credentialService.decrypt(conn.getEncryptedApiKey());
                    if (decryptedKey != null && decryptedKey.length() >= 6) {
                        maskedApiKey = decryptedKey.substring(0, 3) + "***" + decryptedKey.substring(decryptedKey.length() - 3);
                    } else if (decryptedKey != null && !decryptedKey.isBlank()) {
                        maskedApiKey = "***";
                    }
                } catch (Exception e) {
                    maskedApiKey = "***";
                }
            }
        }

        Optional<ActiveTradeLock> activeLock = lockRepository.findActiveLockByAccountId(account.getId());
        int openPositionsCount = positionRepository.findAllOpenByAccountId(account.getId()).size();
        int openOrdersCount = orderRepository.countByTradingAccountIdAndStatusIn(
                account.getId(),
                List.of(OrderStatus.OPEN.name(), OrderStatus.SUBMITTED.name(), OrderStatus.PARTIALLY_FILLED.name())
        );

        return new TradingSystemStatusDto(
                account.getId(),
                account.getName(),
                account.getBaseCurrency(),
                connected,
                connectionStatus,
                environment,
                maskedApiKey,
                Boolean.TRUE.equals(account.getAlgoEnabled()),
                Boolean.TRUE.equals(account.getKillSwitchActive()),
                activeLock.isPresent(),
                activeLock.map(ActiveTradeLock::getSetupId).orElse(null),
                activeLock.map(ActiveTradeLock::getSymbol).orElse(null),
                activeLock.map(ActiveTradeLock::getLifecycleState).orElse(null),
                activeLock.map(ActiveTradeLock::getAcquiredAt).orElse(null),
                openPositionsCount,
                openOrdersCount,
                account.getTotalEquity() != null ? account.getTotalEquity() : BigDecimal.ZERO,
                account.getAvailableBalance() != null ? account.getAvailableBalance() : BigDecimal.ZERO,
                account.getCurrentBalance() != null ? account.getCurrentBalance() : BigDecimal.ZERO,
                account.getMarginUsed() != null ? account.getMarginUsed() : BigDecimal.ZERO,
                account.getLastSyncedAt(),
                lastConnectedAt
        );
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2. Orders API
    // ─────────────────────────────────────────────────────────────────────────

    public List<OrderDto> getOrders(User user, String accountId, String symbol, String status, Integer limit) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        List<Order> orders;

        if (symbol != null && !symbol.isBlank() && status != null && !status.isBlank()) {
            orders = orderRepository.findByTradingAccountIdAndSymbolAndStatusOrderByPlacedAtDesc(
                    account.getId(), symbol.trim().toUpperCase(), status.trim().toUpperCase());
        } else if (symbol != null && !symbol.isBlank()) {
            orders = orderRepository.findByTradingAccountIdAndSymbolOrderByPlacedAtDesc(
                    account.getId(), symbol.trim().toUpperCase());
        } else if (status != null && !status.isBlank()) {
            orders = orderRepository.findByTradingAccountIdAndStatusOrderByPlacedAtDesc(
                    account.getId(), status.trim().toUpperCase());
        } else {
            orders = orderRepository.findByTradingAccountIdOrderByPlacedAtDesc(account.getId());
        }

        int max = (limit != null && limit > 0) ? limit : 100;
        return orders.stream().limit(max).map(OrderDto::fromEntity).toList();
    }

    public OrderDto getOrderById(User user, String accountId, String orderIdOrClientOrderId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        Optional<Order> orderOpt = orderRepository.findByIdAndTradingAccountId(orderIdOrClientOrderId, account.getId());
        if (orderOpt.isEmpty()) {
            orderOpt = orderRepository.findByClientOrderIdAndTradingAccountId(orderIdOrClientOrderId, account.getId());
        }

        Order order = orderOpt.orElseThrow(() ->
                new IllegalArgumentException("Order not found with ID/clientOrderId: " + orderIdOrClientOrderId));
        return OrderDto.fromEntity(order);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3. Positions API
    // ─────────────────────────────────────────────────────────────────────────

    public List<PositionDto> getPositions(User user, String accountId, String status) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        List<Position> positions;

        if (status != null && !status.isBlank() && !"ALL".equalsIgnoreCase(status)) {
            positions = positionRepository.findByTradingAccountIdAndStatusOrderByOpenedAtDesc(
                    account.getId(), status.trim().toUpperCase());
        } else {
            positions = positionRepository.findAllByAccountIdOrderByOpenedAtDesc(account.getId());
        }

        return positions.stream().map(PositionDto::fromEntity).toList();
    }

    public PositionDto getPositionById(User user, String accountId, String positionId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        Position position = positionRepository.findByIdAndTradingAccountId(positionId, account.getId())
                .orElseThrow(() -> new IllegalArgumentException("Position not found with ID: " + positionId));

        return PositionDto.fromEntity(position);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4. Fills API
    // ─────────────────────────────────────────────────────────────────────────

    public List<OrderFillDto> getFills(User user, String accountId, String orderId, String symbol, Integer limit) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        List<OrderFill> fills;

        if (orderId != null && !orderId.isBlank()) {
            fills = orderFillRepository.findByOrderIdOrderByFilledAtAsc(orderId);
            // Verify tenant ownership of fills
            fills = fills.stream()
                    .filter(f -> f.getTradingAccount() != null && account.getId().equals(f.getTradingAccount().getId()))
                    .toList();
        } else if (symbol != null && !symbol.isBlank()) {
            fills = orderFillRepository.findByTradingAccountIdAndSymbolOrderByFilledAtDesc(
                    account.getId(), symbol.trim().toUpperCase());
        } else {
            fills = orderFillRepository.findByTradingAccountIdOrderByFilledAtDesc(account.getId());
        }

        int max = (limit != null && limit > 0) ? limit : 100;
        return fills.stream().limit(max).map(OrderFillDto::fromEntity).toList();
    }

    public OrderFillDto getFillById(User user, String accountId, String fillId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        OrderFill fill = orderFillRepository.findByIdAndTradingAccountId(fillId, account.getId())
                .orElseThrow(() -> new IllegalArgumentException("Fill not found with ID: " + fillId));

        return OrderFillDto.fromEntity(fill);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 5. Trade History API
    // ─────────────────────────────────────────────────────────────────────────

    public List<TradeHistoryDto> getTradeHistory(User user, String accountId, Integer limit) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        List<TradeRecord> trades = tradeRecordRepository.findByAccountIdOrderByOpenedAtDesc(account.getId());

        int max = (limit != null && limit > 0) ? limit : 100;
        return trades.stream().limit(max).map(TradeHistoryDto::fromEntity).toList();
    }

    public TradeHistoryDto getTradeRecordById(User user, String accountId, String tradeRecordId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        TradeRecord trade = tradeRecordRepository.findByIdAndTradingAccountId(tradeRecordId, account.getId())
                .orElseThrow(() -> new IllegalArgumentException("Trade record not found with ID: " + tradeRecordId));

        return TradeHistoryDto.fromEntity(trade);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6. Strategy Setups / Signals API
    // ─────────────────────────────────────────────────────────────────────────

    public List<SignalSetupDto> getSignals(User user, String accountId, String state, String symbol, Integer limit) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        List<StrategySetupRecord> setups;

        boolean hasState = state != null && !state.isBlank() && !"ALL".equalsIgnoreCase(state.trim());
        boolean hasSymbol = symbol != null && !symbol.isBlank() && !"ALL".equalsIgnoreCase(symbol.trim());

        String cleanSymbol = hasSymbol ? InstrumentRegistry.normalize(symbol) : null;
        String cleanState = hasState ? state.trim().toUpperCase() : null;

        if (cleanState != null && cleanSymbol != null) {
            setups = strategySetupRepository.findByTradingAccountIdAndSetupStateAndSymbolOrderByCreatedAtDesc(
                    account.getId(), cleanState, cleanSymbol);
        } else if (cleanState != null) {
            setups = strategySetupRepository.findByTradingAccountIdAndSetupStateOrderByCreatedAtDesc(
                    account.getId(), cleanState);
        } else if (cleanSymbol != null) {
            setups = strategySetupRepository.findByTradingAccountIdAndSymbolOrderByCreatedAtDesc(
                    account.getId(), cleanSymbol);
        } else {
            setups = strategySetupRepository.findByTradingAccountIdOrderByCreatedAtDesc(account.getId());
        }

        int max = (limit != null && limit > 0) ? limit : 100;
        return setups.stream().limit(max).map(SignalSetupDto::fromEntity).toList();
    }

    public SignalSetupDto getSignalBySetupId(User user, String accountId, String setupId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        StrategySetupRecord setup = strategySetupRepository.findBySetupIdAndTradingAccountId(setupId, account.getId())
                .orElseThrow(() -> new IllegalArgumentException("Signal setup not found: " + setupId));

        return SignalSetupDto.fromEntity(setup);
    }
}
