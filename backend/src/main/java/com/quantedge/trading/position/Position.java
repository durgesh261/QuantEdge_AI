package com.quantedge.trading.position;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * JPA entity for the {@code positions} table.
 *
 * <p>The {@code positions} table was created in V1 but never had a JPA entity.
 * Phase 6 adds this entity and maps it to the existing schema, with additional
 * columns added by V4.</p>
 *
 * <h3>Position State Machine</h3>
 * <pre>
 *   OPEN → CLOSING → CLOSED
 *   OPEN → LIQUIDATED
 * </pre>
 *
 * <h3>Key Invariants</h3>
 * <ul>
 *   <li>Position existence does NOT automatically follow from order submission.
 *       A position is only created after confirmed fill data is received.</li>
 *   <li>Position quantity is derived from actual fills, not from the order quantity.</li>
 *   <li>Reconciliation state tracks whether this position has been verified against
 *       the exchange since the last restart or network disruption.</li>
 * </ul>
 */
@Entity
@Table(
    name = "positions",
    indexes = {
        @Index(name = "idx_positions_trading_account",   columnList = "trading_account_id"),
        @Index(name = "idx_positions_status",            columnList = "status"),
        @Index(name = "idx_positions_symbol",            columnList = "symbol"),
        @Index(name = "idx_positions_delta_position_id", columnList = "delta_position_id"),
        @Index(name = "idx_positions_setup_id",          columnList = "setup_id"),
        @Index(name = "idx_positions_entry_order_id",    columnList = "entry_order_id")
    }
)
public class Position extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    /** Exchange-assigned position identifier (may be null for some exchanges). */
    @Column(name = "delta_position_id", length = 100)
    private String deltaPositionId;

    /**
     * Strategy setup that triggered the entry order for this position.
     * Links position back to the originating signal.
     */
    @Column(name = "setup_id", length = 100)
    private String setupId;

    /**
     * Client order ID of the entry order that opened this position.
     * Used for reconciliation: if we find a position on exchange but no
     * matching entry_order_id in our DB, that is a discrepancy.
     */
    @Column(name = "entry_order_id", length = 100)
    private String entryOrderId;

    /**
     * Client order ID of the exit order that closed or is closing this position.
     * Null until a close order is submitted.
     */
    @Column(name = "close_order_id", length = 100)
    private String closeOrderId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    /** LONG or SHORT */
    @Column(name = "side", nullable = false, length = 10)
    private String side;

    /**
     * Position status.
     * <ul>
     *   <li>OPEN — position is live on exchange</li>
     *   <li>CLOSING — a close order has been submitted but not confirmed</li>
     *   <li>CLOSED — position has been fully closed (confirmed via fill or reconciliation)</li>
     *   <li>LIQUIDATED — position was liquidated by the exchange</li>
     * </ul>
     */
    @Column(name = "status", nullable = false, length = 20)
    private String status = "OPEN";

    /** Weighted average entry price from fills. */
    @Column(name = "entry_price", precision = 20, scale = 8, nullable = false)
    private BigDecimal entryPrice;

    /** Latest mark price from exchange (updated during sync). */
    @Column(name = "current_price", precision = 20, scale = 8)
    private BigDecimal currentPrice;

    /** Current open quantity (may decrease as partial close fills are received). */
    @Column(name = "quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal quantity;

    @Column(name = "leverage", nullable = false)
    private Integer leverage;

    /** Latest unrealized P&L from exchange sync (transient, updated on sync). */
    @Column(name = "unrealized_pnl", precision = 20, scale = 8, nullable = false)
    private BigDecimal unrealizedPnl = BigDecimal.ZERO;

    /** Cumulative realized P&L on this position (updated as partial closes occur). */
    @Column(name = "realized_pnl", precision = 20, scale = 8, nullable = false)
    private BigDecimal realizedPnl = BigDecimal.ZERO;

    @Column(name = "liquidation_price", precision = 20, scale = 8)
    private BigDecimal liquidationPrice;

    @Column(name = "margin_used", precision = 20, scale = 8, nullable = false)
    private BigDecimal marginUsed = BigDecimal.ZERO;

    @Column(name = "stop_loss_price", precision = 20, scale = 8)
    private BigDecimal stopLossPrice;

    @Column(name = "take_profit_price", precision = 20, scale = 8)
    private BigDecimal takeProfitPrice;

    /**
     * Reconciliation state for this position row.
     * <ul>
     *   <li>AUTHORITATIVE — confirmed live from exchange during last sync cycle</li>
     *   <li>STALE — not confirmed since last sync (exchange may have closed it)</li>
     *   <li>UNKNOWN — reconciliation query failed; state cannot be determined</li>
     *   <li>RECONCILED — was STALE/UNKNOWN, now confirmed closed or open</li>
     * </ul>
     */
    @Column(name = "reconciliation_state", nullable = false, length = 30)
    private String reconciliationState = "AUTHORITATIVE";

    @Column(name = "last_reconciled_at")
    private Instant lastReconciledAt;

    @Column(name = "opened_at", nullable = false)
    private Instant openedAt = Instant.now();

    @Column(name = "closed_at")
    private Instant closedAt;

    public Position() {}

    public Position(TradingAccount tradingAccount, String symbol, String side,
                    BigDecimal entryPrice, BigDecimal quantity, Integer leverage) {
        this.tradingAccount = tradingAccount;
        this.symbol = symbol;
        this.side = side;
        this.entryPrice = entryPrice;
        this.quantity = quantity;
        this.leverage = leverage;
        this.status = "OPEN";
        this.openedAt = Instant.now();
        this.reconciliationState = "AUTHORITATIVE";
    }

    /** Marks this position as fully closed with authoritative exit data. */
    public void markClosed(BigDecimal realizedPnl, Instant closedAt) {
        this.status = "CLOSED";
        this.realizedPnl = realizedPnl != null ? realizedPnl : BigDecimal.ZERO;
        this.unrealizedPnl = BigDecimal.ZERO;
        this.closedAt = closedAt != null ? closedAt : Instant.now();
        this.reconciliationState = "RECONCILED";
        this.lastReconciledAt = Instant.now();
    }

    /** Marks position as CLOSING — a close order has been submitted. */
    public void markClosing(String closeOrderId) {
        this.status = "CLOSING";
        this.closeOrderId = closeOrderId;
    }

    // ── Getters / Setters ──────────────────────────────────────────────────────

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getDeltaPositionId() { return deltaPositionId; }
    public void setDeltaPositionId(String deltaPositionId) { this.deltaPositionId = deltaPositionId; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String setupId) { this.setupId = setupId; }

    public String getEntryOrderId() { return entryOrderId; }
    public void setEntryOrderId(String entryOrderId) { this.entryOrderId = entryOrderId; }

    public String getCloseOrderId() { return closeOrderId; }
    public void setCloseOrderId(String closeOrderId) { this.closeOrderId = closeOrderId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getSide() { return side; }
    public void setSide(String side) { this.side = side; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public BigDecimal getEntryPrice() { return entryPrice; }
    public void setEntryPrice(BigDecimal entryPrice) { this.entryPrice = entryPrice; }

    public BigDecimal getCurrentPrice() { return currentPrice; }
    public void setCurrentPrice(BigDecimal currentPrice) { this.currentPrice = currentPrice; }

    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal quantity) { this.quantity = quantity; }

    public Integer getLeverage() { return leverage; }
    public void setLeverage(Integer leverage) { this.leverage = leverage; }

    public BigDecimal getUnrealizedPnl() { return unrealizedPnl; }
    public void setUnrealizedPnl(BigDecimal unrealizedPnl) { this.unrealizedPnl = unrealizedPnl; }

    public BigDecimal getRealizedPnl() { return realizedPnl; }
    public void setRealizedPnl(BigDecimal realizedPnl) { this.realizedPnl = realizedPnl; }

    public BigDecimal getLiquidationPrice() { return liquidationPrice; }
    public void setLiquidationPrice(BigDecimal liquidationPrice) { this.liquidationPrice = liquidationPrice; }

    public BigDecimal getMarginUsed() { return marginUsed; }
    public void setMarginUsed(BigDecimal marginUsed) { this.marginUsed = marginUsed; }

    public BigDecimal getStopLossPrice() { return stopLossPrice; }
    public void setStopLossPrice(BigDecimal stopLossPrice) { this.stopLossPrice = stopLossPrice; }

    public BigDecimal getTakeProfitPrice() { return takeProfitPrice; }
    public void setTakeProfitPrice(BigDecimal takeProfitPrice) { this.takeProfitPrice = takeProfitPrice; }

    public String getReconciliationState() { return reconciliationState; }
    public void setReconciliationState(String reconciliationState) { this.reconciliationState = reconciliationState; }

    public Instant getLastReconciledAt() { return lastReconciledAt; }
    public void setLastReconciledAt(Instant lastReconciledAt) { this.lastReconciledAt = lastReconciledAt; }

    public Instant getOpenedAt() { return openedAt; }
    public void setOpenedAt(Instant openedAt) { this.openedAt = openedAt; }

    public Instant getClosedAt() { return closedAt; }
    public void setClosedAt(Instant closedAt) { this.closedAt = closedAt; }
}
