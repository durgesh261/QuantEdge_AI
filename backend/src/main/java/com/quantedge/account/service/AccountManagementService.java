package com.quantedge.account.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.portfolio.entity.Position;
import com.quantedge.portfolio.repository.PositionRepository;
import com.quantedge.trading.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class AccountManagementService {

    private static final Logger log = LoggerFactory.getLogger(AccountManagementService.class);

    private final TradingAccountRepository accountRepository;
    private final DeltaConnectionRepository connectionRepository;
    private final PositionRepository positionRepository;
    private final OrderRepository orderRepository;
    private final AuditLogRepository auditLogRepository;
    private final DeltaCredentialService credentialService;
    private final LiveAccountSyncService syncService;

    public AccountManagementService(
            TradingAccountRepository accountRepository,
            DeltaConnectionRepository connectionRepository,
            PositionRepository positionRepository,
            OrderRepository orderRepository,
            AuditLogRepository auditLogRepository,
            DeltaCredentialService credentialService,
            LiveAccountSyncService syncService
    ) {
        this.accountRepository = accountRepository;
        this.connectionRepository = connectionRepository;
        this.positionRepository = positionRepository;
        this.orderRepository = orderRepository;
        this.auditLogRepository = auditLogRepository;
        this.credentialService = credentialService;
        this.syncService = syncService;
    }

    public record ConnectAccountRequest(
            String accountId,
            String name,
            String apiKey,
            String apiSecret
    ) {}

    public record ConnectAccountResponse(
            boolean success,
            String accountId,
            String name,
            String maskedApiKey,
            String connectionStatus,
            BigDecimal totalEquity,
            BigDecimal availableBalance,
            BigDecimal marginUsed,
            int positionsCount,
            int ordersCount,
            boolean algoEnabled,
            boolean killSwitchActive,
            Instant lastConnectedAt,
            String error
    ) {}

    public record AccountStatusResponse(
            String accountId,
            String name,
            boolean connected,
            String connectionStatus,
            String maskedApiKey,
            String environment,
            Instant lastConnectedAt,
            Instant lastSyncedAt,
            boolean algoEnabled,
            boolean killSwitchActive,
            String lastError
    ) {}

    public record AccountSummaryResponse(
            boolean success,
            String accountId,
            String name,
            String connectionStatus,
            String maskedApiKey,
            BigDecimal totalEquity,
            BigDecimal availableBalance,
            BigDecimal marginUsed,
            String baseCurrency,
            boolean algoEnabled,
            boolean killSwitchActive,
            Instant lastSyncedAt,
            List<LiveAccountSyncService.BalanceDetail> balances,
            List<LiveAccountSyncService.PositionDetail> positions,
            List<LiveAccountSyncService.OrderDetail> openOrders,
            String error
    ) {}

    private String maskApiKey(String apiKey) {
        if (apiKey == null || apiKey.length() <= 8) {
            return "********";
        }
        return apiKey.substring(0, 4) + "***" + apiKey.substring(apiKey.length() - 4);
    }

    @Transactional
    public ConnectAccountResponse connectAccount(User user, ConnectAccountRequest request) {
        if (user == null) {
            throw new IllegalArgumentException("Authenticated user is required to connect an account");
        }
        if (request.apiKey() == null || request.apiKey().trim().isEmpty() ||
            request.apiSecret() == null || request.apiSecret().trim().isEmpty()) {
            return new ConnectAccountResponse(
                    false, null, null, null, "ERROR",
                    BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, 0, 0,
                    false, true, null, "API Key and Secret cannot be blank"
            );
        }

        String rawApiKey = request.apiKey().trim();
        String rawApiSecret = request.apiSecret().trim();
        String maskedKey = maskApiKey(rawApiKey);

        // 1. Resolve or create TradingAccount
        TradingAccount account;
        if (request.accountId() != null && !request.accountId().trim().isEmpty()) {
            account = accountRepository.findById(request.accountId())
                    .orElseThrow(() -> new IllegalArgumentException("Trading account not found: " + request.accountId()));
            if (!account.getUser().getId().equals(user.getId())) {
                throw new SecurityException("Unauthorized access to trading account");
            }
        } else {
            List<TradingAccount> existingAccounts = accountRepository.findByUserId(user.getId());
            if (!existingAccounts.isEmpty()) {
                account = existingAccounts.get(0);
            } else {
                account = new TradingAccount(user, request.name() != null ? request.name() : "Delta Live Account", "LIVE", "USDT");
                // Fail-safe defaults are enforced by constructor; these are explicit safety assertions:
                if (Boolean.TRUE.equals(account.getAlgoEnabled())) {
                    throw new IllegalStateException("SAFETY VIOLATION: New account algoEnabled must default to false");
                }
                if (!Boolean.TRUE.equals(account.getKillSwitchActive())) {
                    throw new IllegalStateException("SAFETY VIOLATION: New account killSwitchActive must default to true");
                }
                account = accountRepository.save(account);
            }
        }

        if (request.name() != null && !request.name().trim().isEmpty()) {
            account.setName(request.name().trim());
        }

        // 2. Encrypt credentials
        String encryptedApiKey = credentialService.encrypt(rawApiKey);
        String encryptedApiSecret = credentialService.encrypt(rawApiSecret);

        // 3. Resolve or create DeltaConnection
        DeltaConnection connection = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE")
                .orElseGet(() -> new DeltaConnection(account, "LIVE", encryptedApiKey, encryptedApiSecret));

        connection.setEncryptedApiKey(encryptedApiKey);
        connection.setEncryptedApiSecret(encryptedApiSecret);
        connection.setConnectionStatus("DISCONNECTED");
        connection = connectionRepository.save(connection);

        // 4. Verify credentials via Read-Only Live Sync
        LiveAccountSyncService.SyncSummary sync = syncService.syncLiveAccount(account.getId(), encryptedApiKey, encryptedApiSecret);

        if (sync.success()) {
            connection.setConnectionStatus("CONNECTED");
            connection.setLastConnectedAt(sync.syncedAt());
            connection.setLastError(null);
            connectionRepository.save(connection);

            account.setTotalEquity(sync.totalEquity());
            account.setCurrentBalance(sync.totalEquity());
            account.setAvailableBalance(sync.availableBalance());
            account.setMarginUsed(sync.marginUsed());
            account.setIsActive(true);
            account.setLastSyncedAt(sync.syncedAt());
            accountRepository.save(account);

            // Update positions in DB
            syncPositionsToDatabase(account, sync.positions());

            auditLogRepository.save(new AuditLog(
                    account.getId(), user.getId(), "DELTA_ACCOUNT_CONNECTED",
                    "SUCCESS", "Successfully connected and verified Delta Exchange India account. MaskedKey=" + maskedKey,
                    null
            ));

            log.info("Delta account connected successfully for user {}: equity={}", user.getId(), sync.totalEquity());

            return new ConnectAccountResponse(
                    true,
                    account.getId(),
                    account.getName(),
                    maskedKey,
                    "CONNECTED",
                    sync.totalEquity(),
                    sync.availableBalance(),
                    sync.marginUsed(),
                    sync.positionsCount(),
                    sync.ordersCount(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    sync.syncedAt(),
                    null
            );
        } else {
            connection.setConnectionStatus("ERROR");
            connection.setLastError(sync.error());
            connectionRepository.save(connection);

            auditLogRepository.save(new AuditLog(
                    account.getId(), user.getId(), "DELTA_ACCOUNT_CONNECT_FAILED",
                    "FAILED", "Failed read-only verification with Delta Exchange India: " + sync.error(),
                    null
            ));

            log.warn("Delta account connection failed read-only verification for user {}: {}", user.getId(), sync.error());

            return new ConnectAccountResponse(
                    false,
                    account.getId(),
                    account.getName(),
                    maskedKey,
                    "ERROR",
                    BigDecimal.ZERO,
                    BigDecimal.ZERO,
                    BigDecimal.ZERO,
                    0,
                    0,
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    null,
                    "Failed to verify credentials with Delta Exchange India: " + sync.error()
            );
        }
    }

    @Transactional
    public AccountSummaryResponse verifyConnection(User user, String accountId) {
        TradingAccount account = getAuthorizedAccount(user, accountId);
        DeltaConnection connection = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE")
                .orElseThrow(() -> new IllegalStateException("No live Delta connection configured for this account."));

        LiveAccountSyncService.SyncSummary sync = syncService.syncLiveAccount(
                account.getId(), connection.getEncryptedApiKey(), connection.getEncryptedApiSecret()
        );

        String maskedKey = maskApiKey(credentialService.decrypt(connection.getEncryptedApiKey()));

        if (sync.success()) {
            connection.setConnectionStatus("CONNECTED");
            connection.setLastConnectedAt(sync.syncedAt());
            connection.setLastError(null);
            connectionRepository.save(connection);

            account.setTotalEquity(sync.totalEquity());
            account.setCurrentBalance(sync.totalEquity());
            account.setAvailableBalance(sync.availableBalance());
            account.setMarginUsed(sync.marginUsed());
            account.setLastSyncedAt(sync.syncedAt());
            accountRepository.save(account);

            syncPositionsToDatabase(account, sync.positions());

            return new AccountSummaryResponse(
                    true,
                    account.getId(),
                    account.getName(),
                    "CONNECTED",
                    maskedKey,
                    sync.totalEquity(),
                    sync.availableBalance(),
                    sync.marginUsed(),
                    account.getBaseCurrency(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    sync.syncedAt(),
                    sync.balances(),
                    sync.positions(),
                    sync.openOrders(),
                    null
            );
        } else {
            connection.setConnectionStatus("ERROR");
            connection.setLastError(sync.error());
            connectionRepository.save(connection);

            return new AccountSummaryResponse(
                    false,
                    account.getId(),
                    account.getName(),
                    "ERROR",
                    maskedKey,
                    account.getTotalEquity(),
                    account.getAvailableBalance(),
                    account.getMarginUsed(),
                    account.getBaseCurrency(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    account.getLastSyncedAt(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    "Synchronization failed: " + sync.error()
            );
        }
    }

    @Transactional(readOnly = true)
    public AccountStatusResponse getAccountStatus(User user, String accountId) {
        TradingAccount account = getAuthorizedAccount(user, accountId);
        Optional<DeltaConnection> connOpt = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");

        String maskedKey = null;
        String status = "DISCONNECTED";
        Instant lastConnected = null;
        String lastError = null;

        if (connOpt.isPresent()) {
            DeltaConnection conn = connOpt.get();
            status = conn.getConnectionStatus();
            lastConnected = conn.getLastConnectedAt();
            lastError = conn.getLastError();
            try {
                maskedKey = maskApiKey(credentialService.decrypt(conn.getEncryptedApiKey()));
            } catch (Exception ignored) {
                maskedKey = "********";
            }
        }

        return new AccountStatusResponse(
                account.getId(),
                account.getName(),
                "CONNECTED".equalsIgnoreCase(status),
                status,
                maskedKey,
                "LIVE",
                lastConnected,
                account.getLastSyncedAt(),
                account.getAlgoEnabled(),
                account.getKillSwitchActive(),
                lastError
        );
    }

    @Transactional(readOnly = true)
    public AccountSummaryResponse getAccountSummary(User user, String accountId) {
        TradingAccount account = getAuthorizedAccount(user, accountId);
        Optional<DeltaConnection> connOpt = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");

        if (connOpt.isEmpty() || !"CONNECTED".equalsIgnoreCase(connOpt.get().getConnectionStatus())) {
            String maskedKey = null;
            if (connOpt.isPresent()) {
                try {
                    maskedKey = maskApiKey(credentialService.decrypt(connOpt.get().getEncryptedApiKey()));
                } catch (Exception ignored) {
                    maskedKey = "********";
                }
            }
            return new AccountSummaryResponse(
                    true,
                    account.getId(),
                    account.getName(),
                    connOpt.map(DeltaConnection::getConnectionStatus).orElse("DISCONNECTED"),
                    maskedKey,
                    account.getTotalEquity(),
                    account.getAvailableBalance(),
                    account.getMarginUsed(),
                    account.getBaseCurrency(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    account.getLastSyncedAt(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    null
            );
        }

        // Active connection: execute fresh read-only synchronization
        DeltaConnection conn = connOpt.get();
        LiveAccountSyncService.SyncSummary sync = syncService.syncLiveAccount(
                account.getId(), conn.getEncryptedApiKey(), conn.getEncryptedApiSecret()
        );

        String maskedKey = maskApiKey(credentialService.decrypt(conn.getEncryptedApiKey()));

        if (sync.success()) {
            return new AccountSummaryResponse(
                    true,
                    account.getId(),
                    account.getName(),
                    "CONNECTED",
                    maskedKey,
                    sync.totalEquity(),
                    sync.availableBalance(),
                    sync.marginUsed(),
                    account.getBaseCurrency(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    sync.syncedAt(),
                    sync.balances(),
                    sync.positions(),
                    sync.openOrders(),
                    null
            );
        } else {
            return new AccountSummaryResponse(
                    false,
                    account.getId(),
                    account.getName(),
                    "ERROR",
                    maskedKey,
                    account.getTotalEquity(),
                    account.getAvailableBalance(),
                    account.getMarginUsed(),
                    account.getBaseCurrency(),
                    account.getAlgoEnabled(),
                    account.getKillSwitchActive(),
                    account.getLastSyncedAt(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    Collections.emptyList(),
                    sync.error()
            );
        }
    }

    @Transactional
    public AccountStatusResponse disconnectAccount(User user, String accountId) {
        TradingAccount account = getAuthorizedAccount(user, accountId);
        Optional<DeltaConnection> connOpt = connectionRepository.findByTradingAccountIdAndEnvironment(account.getId(), "LIVE");

        if (connOpt.isPresent()) {
            DeltaConnection conn = connOpt.get();
            conn.setConnectionStatus("DISCONNECTED");
            connectionRepository.save(conn);
        }

        account.setAlgoEnabled(false);
        account.setKillSwitchActive(true);
        accountRepository.save(account);

        auditLogRepository.save(new AuditLog(
                account.getId(), user.getId(), "DELTA_ACCOUNT_DISCONNECTED",
                "SUCCESS", "Delta Exchange India account disconnected.",
                null
        ));

        log.info("Delta account disconnected for user {}", user.getId());

        return getAccountStatus(user, account.getId());
    }

    private TradingAccount getAuthorizedAccount(User user, String accountId) {
        if (user == null) {
            throw new SecurityException("User authentication required");
        }
        if (accountId != null && !accountId.trim().isEmpty()) {
            TradingAccount account = accountRepository.findById(accountId)
                    .orElseThrow(() -> new IllegalArgumentException("Trading account not found: " + accountId));
            if (!account.getUser().getId().equals(user.getId())) {
                throw new SecurityException("Unauthorized access to trading account");
            }
            return account;
        }

        List<TradingAccount> accounts = accountRepository.findByUserId(user.getId());
        if (accounts.isEmpty()) {
            TradingAccount newAccount = new TradingAccount(user, "Delta Live Account", "LIVE", "USDT");
            // Fail-safe defaults are enforced by constructor; these are explicit safety assertions:
            if (Boolean.TRUE.equals(newAccount.getAlgoEnabled())) {
                throw new IllegalStateException("SAFETY VIOLATION: New account algoEnabled must default to false");
            }
            if (!Boolean.TRUE.equals(newAccount.getKillSwitchActive())) {
                throw new IllegalStateException("SAFETY VIOLATION: New account killSwitchActive must default to true");
            }
            return accountRepository.save(newAccount);
        }
        return accounts.get(0);
    }

    private void syncPositionsToDatabase(TradingAccount account, List<LiveAccountSyncService.PositionDetail> positions) {
        try {
            // Mark non-present open positions as closed
            List<Position> existing = positionRepository.findByTradingAccountIdAndStatus(account.getId(), "OPEN");
            for (Position p : existing) {
                boolean stillOpen = positions.stream().anyMatch(pos -> pos.symbol().equalsIgnoreCase(p.getSymbol()));
                if (!stillOpen) {
                    p.setStatus("CLOSED");
                    p.setClosedAt(Instant.now());
                    positionRepository.save(p);
                }
            }

            for (LiveAccountSyncService.PositionDetail pos : positions) {
                Optional<Position> match = positionRepository.findByTradingAccountIdAndSymbolAndStatus(
                        account.getId(), pos.symbol(), "OPEN"
                );
                Position entity = match.orElseGet(() -> new Position(
                        account, pos.symbol(), pos.side(), pos.size(), pos.entryPrice(), pos.leverage()
                ));
                entity.setQuantity(pos.size());
                entity.setSide(pos.side());
                entity.setEntryPrice(pos.entryPrice());
                entity.setCurrentPrice(pos.markPrice());
                entity.setUnrealizedPnl(pos.unrealizedPnl());
                entity.setRealizedPnl(pos.realizedPnl());
                entity.setLeverage(pos.leverage());
                entity.setMarginUsed(pos.margin());
                entity.setLiquidationPrice(pos.liquidationPrice());
                entity.setStatus("OPEN");
                positionRepository.save(entity);
            }
        } catch (Exception e) {
            log.error("Failed to sync positions to database for account {}: {}", account.getId(), e.getMessage());
        }
    }
}
