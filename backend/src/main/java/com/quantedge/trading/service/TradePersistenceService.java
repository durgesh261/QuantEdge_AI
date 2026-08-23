package com.quantedge.trading.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.TradeRecord;
import com.quantedge.trading.position.Position;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * Authoritative service for all trade and position persistence operations.
 *
 * <h3>Atomic operation pairs (each wrapped in a single transaction):</h3>
 * <ul>
 *   <li>Trade OPEN: {acquire DB lock} + {persist TradeRecord}</li>
 *   <li>Position OPEN: {persist or update Position record with fill data}</li>
 *   <li>Trade CLOSE: {update TradeRecord P&amp;L} + {update account balance}
 *       + {close Position} + {release DB lock}</li>
 * </ul>
 *
 * <p>A partial database failure within any of these pairs will roll back
 * the entire transaction, leaving no orphaned lock or inconsistent balance.</p>
 *
 * <h3>One-trade-at-a-time enforcement:</h3>
 * <p>The {@code active_trade_locks} table has a partial unique index
 * {@code (trading_account_id WHERE released_at IS NULL)}. Any attempt to
 * acquire a second lock for the same account while one is already active
 * will throw {@link DataIntegrityViolationException}, which is caught and
 * translated into a {@link TradeLockException}.</p>
 */
@Service
public class TradePersistenceService {

    private static final Logger log = LoggerFactory.getLogger(TradePersistenceService.class);

    private final ActiveTradeLockRepository lockRepository;
    private final TradeRecordRepository tradeRecordRepository;
    private final TradingAccountRepository accountRepository;
    private final PositionRepository positionRepository;
    private final AuditLogRepository auditLogRepository;

    public TradePersistenceService(
            ActiveTradeLockRepository lockRepository,
            TradeRecordRepository tradeRecordRepository,
            TradingAccountRepository accountRepository,
            PositionRepository positionRepository,
            AuditLogRepository auditLogRepository
    ) {
        this.lockRepository = lockRepository;
        this.tradeRecordRepository = tradeRecordRepository;
        this.accountRepository = accountRepository;
        this.positionRepository = positionRepository;
        this.auditLogRepository = auditLogRepository;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // DTOs
    // ─────────────────────────────────────────────────────────────────────────

    public record TradeOpenRequest(
            String accountId,
            String setupId,
            String symbol,
            String direction,
            BigDecimal entryPrice,
            BigDecimal quantity,
            Integer leverage,
            BigDecimal preTradeBalance,
            BigDecimal orderBlockUpper,
            BigDecimal orderBlockLower,
            BigDecimal stopLossPrice,
            BigDecimal takeProfitPrice,
            Integer configurationVersion,
            BigDecimal maxLossPct,
            BigDecimal targetRoePct
    ) {}

    public record TradeOpenResult(
            boolean success,
            String tradeRecordId,
            String lockId,
            String error
    ) {}

    public record TradeCloseRequest(
            String accountId,
            String setupId,
            BigDecimal grossPnl,
            BigDecimal tradingFees,
            BigDecimal fundingCosts,
            BigDecimal otherCosts,
            BigDecimal exitPrice,
            String closeReason,
            /** authoritative balance from Delta REST API after close — may be null */
            BigDecimal authoritativeExchangeBalance,
            String exitOrderId
    ) {}

    public record TradeCloseResult(
            boolean success,
            BigDecimal netPnl,
            BigDecimal postTradeBalance,
            String error
    ) {}

    public record PositionOpenRequest(
            String accountId,
            String setupId,
            String entryOrderId,
            String symbol,
            String side,
            BigDecimal entryPrice,
            BigDecimal quantity,
            Integer leverage,
            BigDecimal stopLossPrice,
            BigDecimal takeProfitPrice
    ) {}

    public record PositionCloseRequest(
            String accountId,
            String symbol,
            String closeOrderId,
            BigDecimal realizedPnl,
            Instant closedAt
    ) {}

    public record PositionReconciliationData(
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

    public record AccountStateSnapshot(
            String accountId,
            boolean hasActiveTrade,
            String activeSetupId,
            String activeSymbol,
            String activeLockState,
            Instant lockAcquiredAt,
            BigDecimal currentBalance,
            BigDecimal latestPostTradeBalance,
            long totalClosedTrades,
            BigDecimal totalNetPnl,
            BigDecimal totalFeesPaid,
            boolean algoEnabled,
            boolean killSwitchActive
    ) {}

    // ─────────────────────────────────────────────────────────────────────────
    // Custom Exceptions
    // ─────────────────────────────────────────────────────────────────────────

    public static class TradeLockException extends RuntimeException {
        public TradeLockException(String msg) { super(msg); }
    }

    public static class TradeNotFoundException extends RuntimeException {
        public TradeNotFoundException(String msg) { super(msg); }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Core Trade Operations
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Atomically:
     * 1. Checks no active lock exists for the account (fast path).
     * 2. Inserts a new ActiveTradeLock row.
     * 3. Inserts a new TradeRecord row (OPEN state).
     *
     * Both writes are in one transaction. If the DB unique constraint fires
     * (duplicate lock), rolls back and returns failure — never a partial state.
     *
     * @throws TradeLockException if an active trade already exists for this account
     */
    @Transactional
    public TradeOpenResult openTrade(TradeOpenRequest req) {
        TradingAccount account = accountRepository.findById(req.accountId())
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + req.accountId()));

        // Fast safety re-checks
        if (Boolean.TRUE.equals(account.getKillSwitchActive())) {
            return new TradeOpenResult(false, null, null, "KILL_SWITCH_ACTIVE");
        }
        if (Boolean.FALSE.equals(account.getAlgoEnabled())) {
            return new TradeOpenResult(false, null, null, "ALGO_DISABLED");
        }

        // Idempotency: if a trade record already exists for this setupId, return it
        Optional<TradeRecord> existing = tradeRecordRepository.findBySetupId(req.setupId());
        if (existing.isPresent()) {
            TradeRecord ex = existing.get();
            log.warn("openTrade: setup {} already has a TradeRecord (state={}). Returning existing.",
                    req.setupId(), ex.getTradeState());
            Optional<ActiveTradeLock> existingLock = lockRepository.findBySetupId(req.setupId());
            String recId = ex.getId() != null ? ex.getId().toString() : "tr-" + req.setupId();
            String lkId = existingLock.map(l -> l.getId() != null ? l.getId().toString() : "lock-" + req.setupId()).orElse(null);
            return new TradeOpenResult(true, recId, lkId, null);
        }

        // Active lock check: verify no active lock is held for this account
        Optional<ActiveTradeLock> activeLockOpt = lockRepository.findActiveLockByAccountId(req.accountId());
        if (activeLockOpt.isPresent()) {
            String existingSetup = activeLockOpt.get().getSetupId();
            throw new TradeLockException(
                "Active trade lock already exists for account " + req.accountId() +
                " (setup: " + existingSetup + "). One-trade-at-a-time rule violated.");
        }

        // Acquire DB lock — the unique partial index will reject a duplicate
        ActiveTradeLock lock = new ActiveTradeLock(account, req.setupId(), req.symbol());
        try {
            lock = lockRepository.saveAndFlush(lock);
        } catch (DataIntegrityViolationException e) {
            // Partial unique index fired: another lock is already active
            Optional<ActiveTradeLock> activeLock = lockRepository.findActiveLockByAccountId(req.accountId());
            String existingSetup = activeLock.map(ActiveTradeLock::getSetupId).orElse("unknown");
            throw new TradeLockException(
                "Active trade lock already exists for account " + req.accountId() +
                " (setup: " + existingSetup + "). One-trade-at-a-time rule violated.");
        }

        // Persist the open trade record
        TradeRecord record = new TradeRecord(
                account, req.setupId(), req.symbol(), req.direction(),
                req.entryPrice(), req.quantity(), req.leverage(), req.preTradeBalance()
        );
        record.setOrderBlockUpper(req.orderBlockUpper());
        record.setOrderBlockLower(req.orderBlockLower());
        record.setStopLossPrice(req.stopLossPrice());
        record.setTakeProfitPrice(req.takeProfitPrice());
        record.setConfigurationVersion(req.configurationVersion() != null ? req.configurationVersion() : 1);
        if (req.maxLossPct() != null) record.setMaxLossPct(req.maxLossPct());
        if (req.targetRoePct() != null) record.setTargetRoePct(req.targetRoePct());
        record = tradeRecordRepository.saveAndFlush(record);

        auditLog(account, "TRADE_OPENED",
                "setup=" + req.setupId() + " symbol=" + req.symbol() +
                " direction=" + req.direction() + " balance=" + req.preTradeBalance());

        log.info("Trade opened: account={} setup={} symbol={} direction={} balance={}",
                req.accountId(), req.setupId(), req.symbol(), req.direction(), req.preTradeBalance());
        String recId = (record != null && record.getId() != null) ? record.getId().toString() : "tr-" + req.setupId();
        String lkId = (lock != null && lock.getId() != null) ? lock.getId().toString() : "lock-" + req.setupId();
        return new TradeOpenResult(true, recId, lkId, null);
    }

    /**
     * Atomically on trade close:
     * 1. Loads and updates the TradeRecord with authoritative P&amp;L.
     * 2. Updates the TradingAccount.currentBalance to post_trade_balance.
     * 3. Closes any associated Position.
     * 4. Releases the ActiveTradeLock (sets released_at = now).
     * 5. Marks the lock's lifecycle_state to POSITION_CLOSED.
     */
    @Transactional
    public TradeCloseResult closeTrade(TradeCloseRequest req) {
        // Load trade record
        TradeRecord record = tradeRecordRepository.findBySetupId(req.setupId())
                .orElseThrow(() -> new TradeNotFoundException(
                        "TradeRecord not found for setup: " + req.setupId()));

        if ("POSITION_CLOSED".equals(record.getTradeState())) {
            // Idempotent — already closed; return the stored result
            log.warn("closeTrade: setup {} is already POSITION_CLOSED (idempotent return).", req.setupId());
            return new TradeCloseResult(true, record.getNetPnl(), record.getPostTradeBalance(), null);
        }

        // Apply P&L and compute post balance
        record.close(req.grossPnl(), req.tradingFees(), req.fundingCosts(),
                req.otherCosts(), req.exitPrice(), req.closeReason(),
                req.authoritativeExchangeBalance());
        if (req.exitOrderId() != null) record.setTpOrderId(req.exitOrderId());
        tradeRecordRepository.saveAndFlush(record);

        // Update account's current_balance to authoritative post-trade balance
        TradingAccount account = accountRepository.findById(req.accountId())
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + req.accountId()));
        account.setCurrentBalance(record.getPostTradeBalance());
        account.setAvailableBalance(record.getPostTradeBalance());
        accountRepository.saveAndFlush(account);

        // Close associated open position if found
        positionRepository.findOpenByAccountIdAndSymbol(req.accountId(), record.getSymbol())
                .ifPresent(pos -> {
                    pos.markClosed(record.getNetPnl(), Instant.now());
                    if (req.exitOrderId() != null) {
                        pos.setCloseOrderId(req.exitOrderId());
                    }
                    positionRepository.saveAndFlush(pos);
                });

        // Release trade lock
        Optional<ActiveTradeLock> lockOpt = lockRepository.findActiveLockByAccountId(req.accountId());
        if (lockOpt.isPresent()) {
            ActiveTradeLock lock = lockOpt.get();
            lock.setReleasedAt(Instant.now());
            lock.setReleaseReason(req.closeReason() != null ? req.closeReason() : "POSITION_CLOSED");
            lock.setLifecycleState("POSITION_CLOSED");
            lockRepository.saveAndFlush(lock);
        } else {
            log.warn("closeTrade: no active lock found for account {} at close time (may have been force-released).",
                    req.accountId());
        }

        auditLog(account, "TRADE_CLOSED",
                "setup=" + req.setupId() + " grossPnl=" + req.grossPnl() +
                " fees=" + req.tradingFees() + " netPnl=" + record.getNetPnl() +
                " postBalance=" + record.getPostTradeBalance() +
                " reason=" + req.closeReason());

        log.info("Trade closed: account={} setup={} grossPnl={} fees={} netPnl={} postBalance={}",
                req.accountId(), req.setupId(), req.grossPnl(), req.tradingFees(),
                record.getNetPnl(), record.getPostTradeBalance());

        return new TradeCloseResult(true, record.getNetPnl(), record.getPostTradeBalance(), null);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Position Lifecycle Operations
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Records or updates an open position upon order fill execution.
     */
    @Transactional
    public Position openPosition(PositionOpenRequest req) {
        TradingAccount account = accountRepository.findById(req.accountId())
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + req.accountId()));

        Optional<Position> existingOpt = positionRepository.findOpenByAccountIdAndSymbol(req.accountId(), req.symbol());
        Position position;
        if (existingOpt.isPresent()) {
            position = existingOpt.get();
            // In case of incremental fills, recalculate weighted average entry price
            BigDecimal totalQty = position.getQuantity().add(req.quantity());
            if (totalQty.compareTo(BigDecimal.ZERO) > 0) {
                BigDecimal totalCost = position.getQuantity().multiply(position.getEntryPrice())
                        .add(req.quantity().multiply(req.entryPrice()));
                position.setEntryPrice(totalCost.divide(totalQty, 8, RoundingMode.HALF_UP));
            }
            position.setQuantity(totalQty);
            position.setLeverage(req.leverage());
        } else {
            position = new Position(account, req.symbol(), req.side(), req.entryPrice(), req.quantity(), req.leverage());
            position.setSetupId(req.setupId());
            position.setEntryOrderId(req.entryOrderId());
            position.setStopLossPrice(req.stopLossPrice());
            position.setTakeProfitPrice(req.takeProfitPrice());
        }
        position.setReconciliationState("AUTHORITATIVE");
        position.setLastReconciledAt(Instant.now());
        return positionRepository.saveAndFlush(position);
    }

    /**
     * Closes an existing position for the given account and symbol.
     */
    @Transactional
    public void closePosition(PositionCloseRequest req) {
        Optional<Position> posOpt = positionRepository.findOpenByAccountIdAndSymbol(req.accountId(), req.symbol());
        posOpt.ifPresent(pos -> {
            pos.markClosed(req.realizedPnl(), req.closedAt());
            if (req.closeOrderId() != null) {
                pos.setCloseOrderId(req.closeOrderId());
            }
            positionRepository.saveAndFlush(pos);
            log.info("Position closed: account={} symbol={} pnl={}", req.accountId(), req.symbol(), req.realizedPnl());
        });
    }

    /**
     * Reconciles local position state with authoritative exchange snapshot.
     */
    @Transactional
    public void reconcilePositions(String accountId, List<PositionReconciliationData> exchangePositions) {
        TradingAccount account = accountRepository.findById(accountId)
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + accountId));

        List<Position> localOpenPositions = positionRepository.findAllOpenByAccountId(accountId);

        // 1. Update or create positions present on exchange
        for (PositionReconciliationData exPos : exchangePositions) {
            Optional<Position> matchingLocal = localOpenPositions.stream()
                    .filter(p -> p.getSymbol().equalsIgnoreCase(exPos.symbol()))
                    .findFirst();

            if (matchingLocal.isPresent()) {
                Position local = matchingLocal.get();
                local.setQuantity(exPos.size());
                local.setEntryPrice(exPos.entryPrice());
                local.setCurrentPrice(exPos.markPrice());
                local.setUnrealizedPnl(exPos.unrealizedPnl());
                local.setRealizedPnl(exPos.realizedPnl());
                local.setLeverage(exPos.leverage());
                local.setMarginUsed(exPos.margin());
                local.setLiquidationPrice(exPos.liquidationPrice());
                local.setReconciliationState("AUTHORITATIVE");
                local.setLastReconciledAt(Instant.now());
                positionRepository.saveAndFlush(local);
            } else if (exPos.size().compareTo(BigDecimal.ZERO) > 0) {
                // Discrepancy: Position exists on exchange but was missing locally
                log.warn("Discrepancy resolved: Found exchange position not in local DB. Creating position for {} on account {}",
                        exPos.symbol(), accountId);
                Position newPos = new Position(account, exPos.symbol(), exPos.side(), exPos.entryPrice(), exPos.size(), exPos.leverage());
                newPos.setCurrentPrice(exPos.markPrice());
                newPos.setUnrealizedPnl(exPos.unrealizedPnl());
                newPos.setRealizedPnl(exPos.realizedPnl());
                newPos.setMarginUsed(exPos.margin());
                newPos.setLiquidationPrice(exPos.liquidationPrice());
                newPos.setReconciliationState("RECONCILED");
                newPos.setLastReconciledAt(Instant.now());
                positionRepository.saveAndFlush(newPos);
            }
        }

        // 2. Mark local positions as CLOSED if no longer open on exchange
        for (Position local : localOpenPositions) {
            boolean stillOpenOnExchange = exchangePositions.stream()
                    .anyMatch(ep -> ep.symbol().equalsIgnoreCase(local.getSymbol()) && ep.size().compareTo(BigDecimal.ZERO) > 0);

            if (!stillOpenOnExchange) {
                log.info("Reconciliation: Local position {} is closed on exchange. Marking as CLOSED.", local.getSymbol());
                local.markClosed(BigDecimal.ZERO, Instant.now());
                local.setReconciliationState("RECONCILED");
                positionRepository.saveAndFlush(local);
            }
        }
    }

    /**
     * Updates the lifecycle state of the active lock.
     */
    @Transactional
    public void updateLockState(String accountId, String newState) {
        lockRepository.findActiveLockByAccountId(accountId).ifPresent(lock -> {
            lock.setLifecycleState(newState);
            lockRepository.save(lock);
        });
    }

    /**
     * Force-releases a stuck lock after confirmed reconciliation with Delta.
     */
    @Transactional
    public void forceReleaseLock(String accountId, String reason) {
        TradingAccount account = accountRepository.findById(accountId)
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + accountId));

        Optional<ActiveTradeLock> lockOpt = lockRepository.findActiveLockByAccountId(accountId);
        if (lockOpt.isEmpty()) {
            log.info("forceReleaseLock: no active lock found for account {}. Nothing to release.", accountId);
            return;
        }
        ActiveTradeLock lock = lockOpt.get();
        lock.setReleasedAt(Instant.now());
        lock.setReleaseReason(reason);
        lock.setForceReleased(true);
        lock.setLifecycleState("FORCE_RELEASED");
        lockRepository.saveAndFlush(lock);

        auditLog(account, "TRADE_LOCK_FORCE_RELEASED",
                "setup=" + lock.getSetupId() + " reason=" + reason);

        log.warn("FORCE RELEASED trade lock: account={} setup={} reason={}",
                accountId, lock.getSetupId(), reason);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Query Methods
    // ─────────────────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public AccountStateSnapshot getAccountStateSnapshot(String accountId) {
        TradingAccount account = accountRepository.findById(accountId)
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + accountId));

        Optional<ActiveTradeLock> activeLock = lockRepository.findActiveLockByAccountId(accountId);
        Optional<BigDecimal> latestBalance = tradeRecordRepository.findLatestPostTradeBalance(accountId);
        BigDecimal totalNetPnl = tradeRecordRepository.sumNetPnlByAccountId(accountId);
        BigDecimal totalFees = tradeRecordRepository.sumTradingFeesByAccountId(accountId);
        long closedCount = tradeRecordRepository.countClosedTradesByAccountId(accountId);

        return new AccountStateSnapshot(
                accountId,
                activeLock.isPresent(),
                activeLock.map(ActiveTradeLock::getSetupId).orElse(null),
                activeLock.map(ActiveTradeLock::getSymbol).orElse(null),
                activeLock.map(ActiveTradeLock::getLifecycleState).orElse(null),
                activeLock.map(ActiveTradeLock::getAcquiredAt).orElse(null),
                account.getCurrentBalance(),
                latestBalance.orElse(null),
                closedCount,
                totalNetPnl,
                totalFees,
                Boolean.TRUE.equals(account.getAlgoEnabled()),
                Boolean.TRUE.equals(account.getKillSwitchActive())
        );
    }

    @Transactional(readOnly = true)
    public BigDecimal getNextTradeCapital(String accountId) {
        Optional<BigDecimal> lastBalance = tradeRecordRepository.findLatestPostTradeBalance(accountId);
        if (lastBalance.isPresent() && lastBalance.get().compareTo(BigDecimal.ZERO) > 0) {
            return lastBalance.get();
        }
        TradingAccount account = accountRepository.findById(accountId)
                .orElseThrow(() -> new IllegalArgumentException("Account not found: " + accountId));
        return account.getCurrentBalance();
    }

    @Transactional(readOnly = true)
    public Optional<Position> getOpenPosition(String accountId, String symbol) {
        return positionRepository.findOpenByAccountIdAndSymbol(accountId, symbol);
    }

    @Transactional(readOnly = true)
    public List<Position> getOpenPositions(String accountId) {
        return positionRepository.findAllOpenByAccountId(accountId);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Internals
    // ─────────────────────────────────────────────────────────────────────────

    private void auditLog(TradingAccount account, String action, String detail) {
        try {
            AuditLog log = new AuditLog();
            log.setTradingAccount(account);
            log.setAction(action);
            log.setResourceType("TRADE");
            auditLogRepository.save(log);
        } catch (Exception e) {
            TradePersistenceService.log.error("Audit log failed (non-fatal): action={} detail={}", action, detail, e);
        }
    }
}
