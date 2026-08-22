package com.quantedge.trading.repository;

import com.quantedge.trading.entity.ActiveTradeLock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;

/**
 * Repository for the DB-enforced single-trade-at-a-time lock.
 *
 * <p>The partial unique index {@code idx_active_trade_locks_account_active}
 * (WHERE released_at IS NULL) in the database prevents two concurrent
 * open rows for the same trading_account_id. These query methods use
 * {@code SELECT FOR UPDATE} where appropriate to prevent race conditions
 * under concurrent HTTP requests or Python engine retries.</p>
 */
@Repository
public interface ActiveTradeLockRepository extends JpaRepository<ActiveTradeLock, String> {

    /**
     * Finds the currently active (unreleased) lock for a trading account.
     * Returns empty if no active trade exists.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT l FROM ActiveTradeLock l WHERE l.tradingAccount.id = :accountId AND l.releasedAt IS NULL")
    Optional<ActiveTradeLock> findActiveLockByAccountId(@Param("accountId") String accountId);

    /**
     * Fast existence check — does an active (unreleased) lock exist for this account?
     */
    @Query("SELECT COUNT(l) > 0 FROM ActiveTradeLock l WHERE l.tradingAccount.id = :accountId AND l.releasedAt IS NULL")
    boolean existsActiveLockByAccountId(@Param("accountId") String accountId);

    /**
     * Returns all active locks (for health-check and reconciliation purposes).
     */
    @Query("SELECT l FROM ActiveTradeLock l WHERE l.releasedAt IS NULL")
    List<ActiveTradeLock> findAllActiveLocks();

    /**
     * Returns full lock history for an account (newest first).
     */
    @Query("SELECT l FROM ActiveTradeLock l WHERE l.tradingAccount.id = :accountId ORDER BY l.acquiredAt DESC")
    List<ActiveTradeLock> findAllByAccountIdOrderByAcquiredAtDesc(@Param("accountId") String accountId);

    /**
     * Finds the lock for a specific setup_id (for idempotent duplicate-signal detection).
     */
    Optional<ActiveTradeLock> findBySetupId(String setupId);
}
