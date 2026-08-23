package com.quantedge.trading.execution;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

/**
 * Repository for {@link OrderFill} entities.
 *
 * <p>The primary deduplication guarantee is the UNIQUE constraint on
 * {@code exchange_fill_id} enforced by the V4 Flyway migration.
 * {@link #existsByExchangeFillId(String)} is used as a fast pre-check
 * before attempting to persist a fill.</p>
 */
@Repository
public interface OrderFillRepository extends JpaRepository<OrderFill, String> {

    /**
     * Fast deduplication check — returns true if a fill with this
     * exchange-assigned fill ID already exists.
     */
    boolean existsByExchangeFillId(String exchangeFillId);

    /**
     * Find a fill by its exchange-assigned fill ID (for idempotent upsert).
     */
    Optional<OrderFill> findByExchangeFillId(String exchangeFillId);

    /**
     * Returns all fills for a specific order entity.
     */
    @Query("SELECT f FROM OrderFill f WHERE f.order.id = :orderId ORDER BY f.filledAt ASC")
    List<OrderFill> findByOrderIdOrderByFilledAtAsc(@Param("orderId") String orderId);

    /**
     * Returns all fills for a given client_order_id (used when order entity ID
     * is not known but client_order_id is, e.g. during reconciliation).
     */
    List<OrderFill> findByClientOrderIdOrderByFilledAtAsc(String clientOrderId);

    /**
     * Computes the total filled quantity for an order from all recorded fills.
     * Used by {@code FillPersistenceService#updateOrderFromFills(Order)}.
     */
    @Query("SELECT COALESCE(SUM(f.fillQuantity), 0) FROM OrderFill f WHERE f.order.id = :orderId")
    BigDecimal sumFillQuantityByOrderId(@Param("orderId") String orderId);

    /**
     * Computes the quantity-weighted average fill price for an order.
     * Returns null if no fills exist.
     */
    @Query("SELECT SUM(f.fillQuantity * f.fillPrice) / NULLIF(SUM(f.fillQuantity), 0) " +
           "FROM OrderFill f WHERE f.order.id = :orderId")
    Optional<BigDecimal> computeWeightedAverageFillPrice(@Param("orderId") String orderId);

    /**
     * Returns total fees paid across all fills for a specific order.
     */
    @Query("SELECT COALESCE(SUM(f.fee), 0) FROM OrderFill f WHERE f.order.id = :orderId")
    BigDecimal sumFeesByOrderId(@Param("orderId") String orderId);

    /**
     * Returns all fills for a trading account — used for account history and reconciliation.
     */
    @Query("SELECT f FROM OrderFill f WHERE f.tradingAccount.id = :accountId ORDER BY f.filledAt DESC")
    List<OrderFill> findByTradingAccountIdOrderByFilledAtDesc(@Param("accountId") String accountId);

    /**
     * Returns fills for a trading account filtered by symbol.
     */
    @Query("SELECT f FROM OrderFill f WHERE f.tradingAccount.id = :accountId AND f.symbol = :symbol ORDER BY f.filledAt DESC")
    List<OrderFill> findByTradingAccountIdAndSymbolOrderByFilledAtDesc(@Param("accountId") String accountId, @Param("symbol") String symbol);

    /**
     * Returns a specific fill by ID with tenant isolation check.
     */
    @Query("SELECT f FROM OrderFill f WHERE f.id = :id AND f.tradingAccount.id = :accountId")
    Optional<OrderFill> findByIdAndTradingAccountId(@Param("id") String id, @Param("accountId") String accountId);

    /**
     * Count of fill records per order.
     */
    @Query("SELECT COUNT(f) FROM OrderFill f WHERE f.order.id = :orderId")
    long countByOrderId(@Param("orderId") String orderId);
}
