package com.quantedge.trading.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Authoritative per-trade record.
 *
 * <p>Stores the complete lifecycle of one trade: entry, exit, gross P&amp;L,
 * all fee components, net P&amp;L, and the resulting compounded balance.
 * One row = one complete SMC trade execution from entry to close.</p>
 *
 * <h3>Net P&amp;L Formula (immutable law, never gross):</h3>
 * <pre>
 *   net_pnl = gross_pnl - trading_fees - funding_costs - other_costs
 *   post_trade_balance = pre_trade_balance + net_pnl
 * </pre>
 *
 * <p>The {@code post_trade_balance} column is the authoritative starting capital
 * for the next trade. It is either computed from the formula above OR overridden
 * by the authoritative exchange-reported balance (whichever is lower/larger
 * per reconciliation).</p>
 */
@Entity
@Table(
    name = "trade_records",
    indexes = {
        @Index(name = "idx_trade_records_account", columnList = "trading_account_id"),
        @Index(name = "idx_trade_records_setup_id", columnList = "setup_id"),
        @Index(name = "idx_trade_records_state", columnList = "trade_state"),
        @Index(name = "idx_trade_records_opened_at", columnList = "opened_at")
    }
)
public class TradeRecord extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", nullable = false, length = 100)
    private String setupId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "direction", nullable = false, length = 10)
    private String direction;  // LONG | SHORT

    @Column(name = "entry_price", precision = 20, scale = 8, nullable = false)
    private BigDecimal entryPrice;

    @Column(name = "exit_price", precision = 20, scale = 8)
    private BigDecimal exitPrice;

    @Column(name = "quantity", precision = 20, scale = 8, nullable = false)
    private BigDecimal quantity;

    @Column(name = "leverage", nullable = false)
    private Integer leverage;

    // ── P&L breakdown — all populated from authoritative Delta data on close ─

    /** Gross P&L reported by Delta (before any cost deduction). */
    @Column(name = "gross_pnl", precision = 20, scale = 8, nullable = false)
    private BigDecimal grossPnl = BigDecimal.ZERO;

    /** Exchange trading fees (maker/taker) from Delta fill data. */
    @Column(name = "trading_fees", precision = 20, scale = 8, nullable = false)
    private BigDecimal tradingFees = BigDecimal.ZERO;

    /** Funding rate charges/rebates from Delta. */
    @Column(name = "funding_costs", precision = 20, scale = 8, nullable = false)
    private BigDecimal fundingCosts = BigDecimal.ZERO;

    /** Any other applicable costs (e.g. liquidation penalties). */
    @Column(name = "other_costs", precision = 20, scale = 8, nullable = false)
    private BigDecimal otherCosts = BigDecimal.ZERO;

    /**
     * Net P&L = gross_pnl - trading_fees - funding_costs - other_costs.
     * This is the value used for balance compounding. NEVER use gross_pnl.
     */
    @Column(name = "net_pnl", precision = 20, scale = 8, nullable = false)
    private BigDecimal netPnl = BigDecimal.ZERO;

    // ── Balance compounding ───────────────────────────────────────────────────

    /** Available balance BEFORE this trade was entered (100% allocated). */
    @Column(name = "pre_trade_balance", precision = 20, scale = 8, nullable = false)
    private BigDecimal preTradeBalance = BigDecimal.ZERO;

    /**
     * Authoritative compounded balance AFTER this trade closes.
     * = pre_trade_balance + net_pnl  (floor: 0, never negative).
     * If Delta's REST API reports a different balance, that value takes precedence.
     */
    @Column(name = "post_trade_balance", precision = 20, scale = 8, nullable = false)
    private BigDecimal postTradeBalance = BigDecimal.ZERO;

    // ── Immutable configuration snapshot ─────────────────────────────────────

    @Column(name = "configuration_version", nullable = false)
    private Integer configurationVersion = 1;

    /** Maximum planned account loss % (default 35.0). */
    @Column(name = "max_loss_pct", precision = 5, scale = 2, nullable = false)
    private BigDecimal maxLossPct = new BigDecimal("35.00");

    /** Target ROE % (default 60.0). */
    @Column(name = "target_roe_pct", precision = 5, scale = 2, nullable = false)
    private BigDecimal targetRoePct = new BigDecimal("60.00");

    // ── Lifecycle & order references ──────────────────────────────────────────

    @Column(name = "trade_state", nullable = false, length = 30)
    private String tradeState = "OPEN";  // OPEN | PROTECTED_POSITION | POSITION_CLOSED

    @Column(name = "close_reason", length = 50)
    private String closeReason;  // TAKE_PROFIT | STOP_LOSS | MANUAL | KILL_SWITCH

    @Column(name = "order_block_upper", precision = 20, scale = 8)
    private BigDecimal orderBlockUpper;

    @Column(name = "order_block_lower", precision = 20, scale = 8)
    private BigDecimal orderBlockLower;

    @Column(name = "stop_loss_price", precision = 20, scale = 8)
    private BigDecimal stopLossPrice;

    @Column(name = "take_profit_price", precision = 20, scale = 8)
    private BigDecimal takeProfitPrice;

    @Column(name = "entry_order_id", length = 100)
    private String entryOrderId;

    @Column(name = "sl_order_id", length = 100)
    private String slOrderId;

    @Column(name = "tp_order_id", length = 100)
    private String tpOrderId;

    @Column(name = "opened_at", nullable = false)
    private Instant openedAt = Instant.now();

    @Column(name = "closed_at")
    private Instant closedAt;

    public TradeRecord() {}

    public TradeRecord(TradingAccount tradingAccount, String setupId, String symbol,
                       String direction, BigDecimal entryPrice, BigDecimal quantity,
                       Integer leverage, BigDecimal preTradeBalance) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.symbol = symbol;
        this.direction = direction;
        this.entryPrice = entryPrice;
        this.quantity = quantity;
        this.leverage = leverage;
        this.preTradeBalance = preTradeBalance;
        this.postTradeBalance = preTradeBalance;  // will be updated on close
        this.tradeState = "OPEN";
        this.openedAt = Instant.now();
    }

    /**
     * Closes this trade record with authoritative P&amp;L data.
     * Applies: net_pnl = gross_pnl - fees - funding - other.
     * Computes: post_trade_balance = pre_trade_balance + net_pnl (floor: 0).
     */
    public void close(BigDecimal grossPnl, BigDecimal tradingFees,
                      BigDecimal fundingCosts, BigDecimal otherCosts,
                      BigDecimal exitPrice, String closeReason,
                      BigDecimal authoritativeExchangeBalance) {
        this.exitPrice = exitPrice;
        this.grossPnl = grossPnl != null ? grossPnl : BigDecimal.ZERO;
        this.tradingFees = tradingFees != null ? tradingFees : BigDecimal.ZERO;
        this.fundingCosts = fundingCosts != null ? fundingCosts : BigDecimal.ZERO;
        this.otherCosts = otherCosts != null ? otherCosts : BigDecimal.ZERO;

        // Net P&L formula — law, never use gross
        this.netPnl = this.grossPnl
                .subtract(this.tradingFees)
                .subtract(this.fundingCosts)
                .subtract(this.otherCosts);

        // Compounded balance (floor at 0)
        BigDecimal computed = this.preTradeBalance.add(this.netPnl);
        BigDecimal floor = BigDecimal.ZERO;
        if (authoritativeExchangeBalance != null) {
            // Exchange-reported balance is authoritative; use it directly
            this.postTradeBalance = authoritativeExchangeBalance.max(floor);
        } else {
            this.postTradeBalance = computed.max(floor);
        }

        this.closeReason = closeReason;
        this.tradeState = "POSITION_CLOSED";
        this.closedAt = Instant.now();
    }

    // ── Getters / Setters ─────────────────────────────────────────────────────

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount t) { this.tradingAccount = t; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String s) { this.setupId = s; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String s) { this.symbol = s; }

    public String getDirection() { return direction; }
    public void setDirection(String d) { this.direction = d; }

    public BigDecimal getEntryPrice() { return entryPrice; }
    public void setEntryPrice(BigDecimal p) { this.entryPrice = p; }

    public BigDecimal getExitPrice() { return exitPrice; }
    public void setExitPrice(BigDecimal p) { this.exitPrice = p; }

    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal q) { this.quantity = q; }

    public Integer getLeverage() { return leverage; }
    public void setLeverage(Integer l) { this.leverage = l; }

    public BigDecimal getGrossPnl() { return grossPnl; }
    public void setGrossPnl(BigDecimal v) { this.grossPnl = v; }

    public BigDecimal getTradingFees() { return tradingFees; }
    public void setTradingFees(BigDecimal v) { this.tradingFees = v; }

    public BigDecimal getFundingCosts() { return fundingCosts; }
    public void setFundingCosts(BigDecimal v) { this.fundingCosts = v; }

    public BigDecimal getOtherCosts() { return otherCosts; }
    public void setOtherCosts(BigDecimal v) { this.otherCosts = v; }

    public BigDecimal getNetPnl() { return netPnl; }
    public void setNetPnl(BigDecimal v) { this.netPnl = v; }

    public BigDecimal getPreTradeBalance() { return preTradeBalance; }
    public void setPreTradeBalance(BigDecimal v) { this.preTradeBalance = v; }

    public BigDecimal getPostTradeBalance() { return postTradeBalance; }
    public void setPostTradeBalance(BigDecimal v) { this.postTradeBalance = v; }

    public Integer getConfigurationVersion() { return configurationVersion; }
    public void setConfigurationVersion(Integer v) { this.configurationVersion = v; }

    public BigDecimal getMaxLossPct() { return maxLossPct; }
    public void setMaxLossPct(BigDecimal v) { this.maxLossPct = v; }

    public BigDecimal getTargetRoePct() { return targetRoePct; }
    public void setTargetRoePct(BigDecimal v) { this.targetRoePct = v; }

    public String getTradeState() { return tradeState; }
    public void setTradeState(String s) { this.tradeState = s; }

    public String getCloseReason() { return closeReason; }
    public void setCloseReason(String s) { this.closeReason = s; }

    public BigDecimal getOrderBlockUpper() { return orderBlockUpper; }
    public void setOrderBlockUpper(BigDecimal v) { this.orderBlockUpper = v; }

    public BigDecimal getOrderBlockLower() { return orderBlockLower; }
    public void setOrderBlockLower(BigDecimal v) { this.orderBlockLower = v; }

    public BigDecimal getStopLossPrice() { return stopLossPrice; }
    public void setStopLossPrice(BigDecimal v) { this.stopLossPrice = v; }

    public BigDecimal getTakeProfitPrice() { return takeProfitPrice; }
    public void setTakeProfitPrice(BigDecimal v) { this.takeProfitPrice = v; }

    public String getEntryOrderId() { return entryOrderId; }
    public void setEntryOrderId(String s) { this.entryOrderId = s; }

    public String getSlOrderId() { return slOrderId; }
    public void setSlOrderId(String s) { this.slOrderId = s; }

    public String getTpOrderId() { return tpOrderId; }
    public void setTpOrderId(String s) { this.tpOrderId = s; }

    public Instant getOpenedAt() { return openedAt; }
    public void setOpenedAt(Instant t) { this.openedAt = t; }

    public Instant getClosedAt() { return closedAt; }
    public void setClosedAt(Instant t) { this.closedAt = t; }
}
