package com.quantedge.portfolio.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "positions", indexes = {
        @Index(name = "idx_positions_trading_account", columnList = "trading_account_id"),
        @Index(name = "idx_positions_status", columnList = "status"),
        @Index(name = "idx_positions_symbol", columnList = "symbol"),
        @Index(name = "idx_positions_delta_position_id", columnList = "delta_position_id")
})
public class Position extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "delta_position_id", length = 100)
    private String deltaPositionId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "side", nullable = false, length = 10)
    private String side;

    @Column(name = "status", nullable = false, length = 20)
    private String status = "OPEN";

    @Column(name = "entry_price", precision = 20, scale = 8, nullable = false)
    private BigDecimal entryPrice;

    @Column(name = "current_price", precision = 20, scale = 8)
    private BigDecimal currentPrice;

    @Column(name = "quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal quantity;

    @Column(name = "leverage", nullable = false)
    private Integer leverage;

    @Column(name = "unrealized_pnl", precision = 20, scale = 8, nullable = false)
    private BigDecimal unrealizedPnl = BigDecimal.ZERO;

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

    @Column(name = "opened_at", nullable = false)
    private Instant openedAt = Instant.now();

    @Column(name = "closed_at")
    private Instant closedAt;

    public Position() {}

    public Position(TradingAccount tradingAccount, String symbol, String side, BigDecimal quantity, BigDecimal entryPrice, Integer leverage) {
        this.tradingAccount = tradingAccount;
        this.symbol = symbol;
        this.side = side;
        this.quantity = quantity;
        this.entryPrice = entryPrice;
        this.leverage = leverage;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getDeltaPositionId() { return deltaPositionId; }
    public void setDeltaPositionId(String deltaPositionId) { this.deltaPositionId = deltaPositionId; }

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

    public Instant getOpenedAt() { return openedAt; }
    public void setOpenedAt(Instant openedAt) { this.openedAt = openedAt; }

    public Instant getClosedAt() { return closedAt; }
    public void setClosedAt(Instant closedAt) { this.closedAt = closedAt; }
}
