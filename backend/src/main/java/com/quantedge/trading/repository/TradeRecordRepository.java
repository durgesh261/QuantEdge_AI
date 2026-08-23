package com.quantedge.trading.repository;

import com.quantedge.trading.entity.TradeRecord;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

/**
 * Repository for authoritative trade records (net P&amp;L, fees, compounded balance).
 */
@Repository
public interface TradeRecordRepository extends JpaRepository<TradeRecord, String> {

    /**
     * Returns all trade records for an account ordered by open time (newest first).
     * Used for trade history display and P&L reporting.
     */
    @Query("SELECT t FROM TradeRecord t WHERE t.tradingAccount.id = :accountId ORDER BY t.openedAt DESC")
    List<TradeRecord> findByAccountIdOrderByOpenedAtDesc(@Param("accountId") String accountId);

    /**
     * Returns a specific trade record ensuring tenant ownership.
     */
    @Query("SELECT t FROM TradeRecord t WHERE t.id = :id AND t.tradingAccount.id = :accountId")
    Optional<TradeRecord> findByIdAndTradingAccountId(@Param("id") String id, @Param("accountId") String accountId);

    /**
     * Returns the most recent CLOSED trade record to obtain the authoritative
     * compounded balance for the next trade's capital allocation.
     */
    @Query("SELECT t FROM TradeRecord t WHERE t.tradingAccount.id = :accountId " +
           "AND t.tradeState = 'POSITION_CLOSED' ORDER BY t.closedAt DESC LIMIT 1")
    Optional<TradeRecord> findLatestClosedByAccountId(@Param("accountId") String accountId);

    /**
     * Returns the current OPEN trade record for an account.
     * At most one should exist (enforced by active_trade_locks).
     */
    @Query("SELECT t FROM TradeRecord t WHERE t.tradingAccount.id = :accountId AND t.tradeState = 'OPEN'")
    Optional<TradeRecord> findOpenTradeByAccountId(@Param("accountId") String accountId);

    /**
     * Returns the trade record for a specific setup_id (for idempotency checks).
     */
    Optional<TradeRecord> findBySetupId(String setupId);

    /**
     * Returns the authoritative post-trade balance from the most recent closed trade.
     * Returns null if no closed trades exist (use account starting_balance instead).
     */
    @Query("SELECT t.postTradeBalance FROM TradeRecord t WHERE t.tradingAccount.id = :accountId " +
           "AND t.tradeState = 'POSITION_CLOSED' ORDER BY t.closedAt DESC LIMIT 1")
    Optional<BigDecimal> findLatestPostTradeBalance(@Param("accountId") String accountId);

    /**
     * Returns total net P&L for an account across all closed trades.
     */
    @Query("SELECT COALESCE(SUM(t.netPnl), 0) FROM TradeRecord t " +
           "WHERE t.tradingAccount.id = :accountId AND t.tradeState = 'POSITION_CLOSED'")
    BigDecimal sumNetPnlByAccountId(@Param("accountId") String accountId);

    /**
     * Returns total trading fees paid across all closed trades.
     */
    @Query("SELECT COALESCE(SUM(t.tradingFees), 0) FROM TradeRecord t " +
           "WHERE t.tradingAccount.id = :accountId AND t.tradeState = 'POSITION_CLOSED'")
    BigDecimal sumTradingFeesByAccountId(@Param("accountId") String accountId);

    /** Count of all closed trades for an account. */
    @Query("SELECT COUNT(t) FROM TradeRecord t WHERE t.tradingAccount.id = :accountId AND t.tradeState = 'POSITION_CLOSED'")
    long countClosedTradesByAccountId(@Param("accountId") String accountId);
}
