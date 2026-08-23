package com.quantedge.trading.position;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Repository for {@link Position} entities.
 *
 * <p>All queries are tenant-scoped via {@code trading_account_id} to prevent
 * cross-tenant data access.</p>
 */
@Repository
public interface PositionRepository extends JpaRepository<Position, String> {

    /**
     * Returns the currently OPEN position for a trading account and symbol.
     * In a net-position model there is at most one open position per symbol.
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId " +
           "AND p.symbol = :symbol AND p.status = 'OPEN'")
    Optional<Position> findOpenByAccountIdAndSymbol(@Param("accountId") String accountId,
                                                    @Param("symbol") String symbol);

    /**
     * Returns all OPEN positions for a trading account.
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId AND p.status = 'OPEN'")
    List<Position> findAllOpenByAccountId(@Param("accountId") String accountId);

    /**
     * Returns all positions (any state) for a trading account ordered by open time DESC.
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId ORDER BY p.openedAt DESC")
    List<Position> findAllByAccountIdOrderByOpenedAtDesc(@Param("accountId") String accountId);

    /**
     * Returns positions for a trading account filtered by status ordered by open time DESC.
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId AND p.status = :status ORDER BY p.openedAt DESC")
    List<Position> findByTradingAccountIdAndStatusOrderByOpenedAtDesc(@Param("accountId") String accountId, @Param("status") String status);

    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId AND p.status = :status")
    List<Position> findByTradingAccountIdAndStatus(@Param("accountId") String accountId, @Param("status") String status);

    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId AND p.symbol = :symbol AND p.status = :status")
    Optional<Position> findByTradingAccountIdAndSymbolAndStatus(@Param("accountId") String accountId, @Param("symbol") String symbol, @Param("status") String status);

    /**
     * Returns a specific position by ID ensuring tenant ownership.
     */
    @Query("SELECT p FROM Position p WHERE p.id = :id AND p.tradingAccount.id = :accountId")
    Optional<Position> findByIdAndTradingAccountId(@Param("id") String id, @Param("accountId") String accountId);

    /**
     * Returns the position opened by a specific strategy setup.
     */
    Optional<Position> findBySetupId(String setupId);

    /**
     * Returns the position linked to a specific entry order (by client_order_id).
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId " +
           "AND p.entryOrderId = :clientOrderId")
    Optional<Position> findByAccountIdAndEntryOrderId(@Param("accountId") String accountId,
                                                      @Param("clientOrderId") String clientOrderId);

    /**
     * Returns all positions that are OPEN or CLOSING — used by reconciliation
     * to determine which positions need to be verified against the exchange.
     */
    @Query("SELECT p FROM Position p WHERE p.tradingAccount.id = :accountId " +
           "AND p.status IN ('OPEN', 'CLOSING')")
    List<Position> findActiveByAccountId(@Param("accountId") String accountId);

    /**
     * Returns all OPEN or CLOSING positions across ALL accounts.
     * Used by {@code StartupReconciliationService} at application boot.
     */
    @Query("SELECT p FROM Position p WHERE p.status IN ('OPEN', 'CLOSING')")
    List<Position> findAllActivePositions();

    /**
     * Returns positions with STALE or UNKNOWN reconciliation state
     * (for the startup reconciliation pass).
     */
    @Query("SELECT p FROM Position p WHERE p.reconciliationState IN ('STALE', 'UNKNOWN') " +
           "AND p.status IN ('OPEN', 'CLOSING')")
    List<Position> findUnreconciled();

    /**
     * True if an OPEN position exists for the given account and symbol.
     */
    @Query("SELECT COUNT(p) > 0 FROM Position p WHERE p.tradingAccount.id = :accountId " +
           "AND p.symbol = :symbol AND p.status = 'OPEN'")
    boolean existsOpenByAccountIdAndSymbol(@Param("accountId") String accountId,
                                           @Param("symbol") String symbol);
}
