package com.quantedge.trading.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "orders", indexes = {
        @Index(name = "idx_orders_trading_account", columnList = "trading_account_id"),
        @Index(name = "idx_orders_status", columnList = "status"),
        @Index(name = "idx_orders_client_order_id", columnList = "client_order_id", unique = true),
        @Index(name = "idx_orders_delta_order_id", columnList = "delta_order_id")
})
public class Order extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", length = 100)
    private String setupId;

    @Column(name = "delta_order_id", length = 100)
    private String deltaOrderId;

    @Column(name = "client_order_id", unique = true, length = 100)
    private String clientOrderId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "side", nullable = false, length = 10)
    private String side;

    @Column(name = "order_type", nullable = false, length = 20)
    private String orderType;

    @Column(name = "status", nullable = false, length = 20)
    private String status = "PENDING";

    @Column(name = "price", precision = 20, scale = 8)
    private BigDecimal price;

    @Column(name = "stop_price", precision = 20, scale = 8)
    private BigDecimal stopPrice;

    @Column(name = "quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal quantity;

    @Column(name = "filled_quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal filledQuantity = BigDecimal.ZERO;

    @Column(name = "average_fill_price", precision = 20, scale = 8)
    private BigDecimal averageFillPrice;

    @Column(name = "leverage")
    private Integer leverage;

    @Column(name = "reduce_only", nullable = false)
    private Boolean reduceOnly = false;

    @Column(name = "post_only", nullable = false)
    private Boolean postOnly = false;

    @Column(name = "time_in_force", nullable = false, length = 10)
    private String timeInForce = "GTC";

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "placed_at", nullable = false)
    private Instant placedAt = Instant.now();

    @Column(name = "filled_at")
    private Instant filledAt;

    @Column(name = "cancelled_at")
    private Instant cancelledAt;

    public Order() {}

    public Order(TradingAccount tradingAccount, String setupId, String clientOrderId, String symbol, String side, String orderType, BigDecimal quantity, BigDecimal price) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.clientOrderId = clientOrderId;
        this.symbol = symbol;
        this.side = side;
        this.orderType = orderType;
        this.quantity = quantity;
        this.price = price;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String setupId) { this.setupId = setupId; }

    public String getDeltaOrderId() { return deltaOrderId; }
    public void setDeltaOrderId(String deltaOrderId) { this.deltaOrderId = deltaOrderId; }

    public String getClientOrderId() { return clientOrderId; }
    public void setClientOrderId(String clientOrderId) { this.clientOrderId = clientOrderId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getSide() { return side; }
    public void setSide(String side) { this.side = side; }

    public String getOrderType() { return orderType; }
    public void setOrderType(String orderType) { this.orderType = orderType; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public BigDecimal getPrice() { return price; }
    public void setPrice(BigDecimal price) { this.price = price; }

    public BigDecimal getStopPrice() { return stopPrice; }
    public void setStopPrice(BigDecimal stopPrice) { this.stopPrice = stopPrice; }

    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal quantity) { this.quantity = quantity; }

    public BigDecimal getFilledQuantity() { return filledQuantity; }
    public void setFilledQuantity(BigDecimal filledQuantity) { this.filledQuantity = filledQuantity; }

    public BigDecimal getAverageFillPrice() { return averageFillPrice; }
    public void setAverageFillPrice(BigDecimal averageFillPrice) { this.averageFillPrice = averageFillPrice; }

    public Integer getLeverage() { return leverage; }
    public void setLeverage(Integer leverage) { this.leverage = leverage; }

    public Boolean getReduceOnly() { return reduceOnly; }
    public void setReduceOnly(Boolean reduceOnly) { this.reduceOnly = reduceOnly; }

    public Boolean getPostOnly() { return postOnly; }
    public void setPostOnly(Boolean postOnly) { this.postOnly = postOnly; }

    public String getTimeInForce() { return timeInForce; }
    public void setTimeInForce(String timeInForce) { this.timeInForce = timeInForce; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

    public Instant getPlacedAt() { return placedAt; }
    public void setPlacedAt(Instant placedAt) { this.placedAt = placedAt; }

    public Instant getFilledAt() { return filledAt; }
    public void setFilledAt(Instant filledAt) { this.filledAt = filledAt; }

    public Instant getCancelledAt() { return cancelledAt; }
    public void setCancelledAt(Instant cancelledAt) { this.cancelledAt = cancelledAt; }
}
