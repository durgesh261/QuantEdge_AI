package com.quantedge.trading.execution;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import com.quantedge.trading.entity.Order;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Represents a single execution fill from Delta Exchange.
 *
 * <h3>Deduplication</h3>
 * <p>The {@code exchange_fill_id} column has a UNIQUE constraint (enforced by
 * V4 migration). Any attempt to persist a fill with an already-recorded
 * {@code exchangeFillId} will throw {@link org.springframework.dao.DataIntegrityViolationException}
 * at the database level, which {@link FillPersistenceService} catches and converts
 * to an idempotent no-op.</p>
 *
 * <h3>Partial Fill Tracking</h3>
 * <p>One {@link Order} may have multiple {@code OrderFill} rows. The sum of
 * {@code fillQuantity} across all fills for an order must equal {@code Order.filledQuantity}.
 * {@link FillPersistenceService#updateOrderFromFills(Order)} recomputes the
 * aggregate values and transitions the order status accordingly.</p>
 *
 * <h3>ORDER SUBMITTED ≠ ORDER FILLED</h3>
 * <p>A fill row only exists when the exchange reports an actual execution.
 * Order submission success does NOT create a fill row.</p>
 */
@Entity
@Table(
    name = "order_fills",
    indexes = {
        @Index(name = "idx_order_fills_order_id",         columnList = "order_id"),
        @Index(name = "idx_order_fills_trading_account",  columnList = "trading_account_id"),
        @Index(name = "idx_order_fills_client_order_id",  columnList = "client_order_id"),
        @Index(name = "idx_order_fills_exchange_fill_id", columnList = "exchange_fill_id", unique = true),
        @Index(name = "idx_order_fills_filled_at",        columnList = "filled_at")
    }
)
public class OrderFill extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    /**
     * FK to the {@link Order} entity this fill belongs to.
     * May be null if reconciliation discovers a fill for an order not yet in DB.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "order_id")
    private Order order;

    /**
     * Exchange-assigned fill/trade ID.
     * UNIQUE — the database rejects duplicate fill ingestion at the constraint level.
     */
    @Column(name = "exchange_fill_id", nullable = false, unique = true, length = 100)
    private String exchangeFillId;

    /** Our internal client order ID (matches {@link Order#clientOrderId}). */
    @Column(name = "client_order_id", length = 100)
    private String clientOrderId;

    /** Exchange-assigned order ID (Delta order ID). */
    @Column(name = "delta_order_id", length = 100)
    private String deltaOrderId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    /** BUY or SELL */
    @Column(name = "side", nullable = false, length = 10)
    private String side;

    /** Quantity filled in this specific fill event. */
    @Column(name = "fill_quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal fillQuantity;

    /** Execution price for this specific fill. */
    @Column(name = "fill_price", precision = 20, scale = 8, nullable = false)
    private BigDecimal fillPrice;

    /** Fee charged for this fill (in the fee asset). */
    @Column(name = "fee", precision = 20, scale = 8, nullable = false)
    private BigDecimal fee = BigDecimal.ZERO;

    /** Asset in which the fee was charged (e.g. "USDT"). */
    @Column(name = "fee_asset", length = 20)
    private String feeAsset;

    /** Timestamp of the fill as reported by the exchange. */
    @Column(name = "filled_at", nullable = false)
    private Instant filledAt;

    /** Raw JSON from the exchange fill event — preserved for audit and reconciliation. */
    @Column(name = "raw_exchange_data", columnDefinition = "TEXT")
    private String rawExchangeData;

    public OrderFill() {}

    public OrderFill(TradingAccount tradingAccount, Order order, String exchangeFillId,
                     String clientOrderId, String deltaOrderId,
                     String symbol, String side,
                     BigDecimal fillQuantity, BigDecimal fillPrice,
                     BigDecimal fee, String feeAsset, Instant filledAt) {
        this.tradingAccount = tradingAccount;
        this.order = order;
        this.exchangeFillId = exchangeFillId;
        this.clientOrderId = clientOrderId;
        this.deltaOrderId = deltaOrderId;
        this.symbol = symbol;
        this.side = side;
        this.fillQuantity = fillQuantity;
        this.fillPrice = fillPrice;
        this.fee = fee != null ? fee : BigDecimal.ZERO;
        this.feeAsset = feeAsset;
        this.filledAt = filledAt;
    }

    // ── Getters / Setters ──────────────────────────────────────────────────────

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public Order getOrder() { return order; }
    public void setOrder(Order order) { this.order = order; }

    public String getExchangeFillId() { return exchangeFillId; }
    public void setExchangeFillId(String exchangeFillId) { this.exchangeFillId = exchangeFillId; }

    public String getClientOrderId() { return clientOrderId; }
    public void setClientOrderId(String clientOrderId) { this.clientOrderId = clientOrderId; }

    public String getDeltaOrderId() { return deltaOrderId; }
    public void setDeltaOrderId(String deltaOrderId) { this.deltaOrderId = deltaOrderId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getSide() { return side; }
    public void setSide(String side) { this.side = side; }

    public BigDecimal getFillQuantity() { return fillQuantity; }
    public void setFillQuantity(BigDecimal fillQuantity) { this.fillQuantity = fillQuantity; }

    public BigDecimal getFillPrice() { return fillPrice; }
    public void setFillPrice(BigDecimal fillPrice) { this.fillPrice = fillPrice; }

    public BigDecimal getFee() { return fee; }
    public void setFee(BigDecimal fee) { this.fee = fee; }

    public String getFeeAsset() { return feeAsset; }
    public void setFeeAsset(String feeAsset) { this.feeAsset = feeAsset; }

    public Instant getFilledAt() { return filledAt; }
    public void setFilledAt(Instant filledAt) { this.filledAt = filledAt; }

    public String getRawExchangeData() { return rawExchangeData; }
    public void setRawExchangeData(String rawExchangeData) { this.rawExchangeData = rawExchangeData; }
}
