package com.quantedge.strategy.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "strategy_setups", indexes = {
        @Index(name = "idx_strategy_setups_setup_id", columnList = "setup_id", unique = true),
        @Index(name = "idx_strategy_setups_account", columnList = "trading_account_id")
})
public class StrategySetupRecord extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id")
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", nullable = false, unique = true, length = 100)
    private String setupId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "direction", nullable = false, length = 10)
    private String direction;

    @Column(name = "timeframe", nullable = false, length = 10)
    private String timeframe = "1h";

    @Column(name = "setup_state", nullable = false, length = 30)
    private String setupState = "TRADE_SETUP_READY";

    @Column(name = "entry_price", precision = 20, scale = 8, nullable = false)
    private BigDecimal entryPrice;

    @Column(name = "stop_loss", precision = 20, scale = 8, nullable = false)
    private BigDecimal stopLoss;

    @Column(name = "take_profit", precision = 20, scale = 8, nullable = false)
    private BigDecimal takeProfit;

    @Column(name = "risk_distance", precision = 20, scale = 8)
    private BigDecimal riskDistance;

    @Column(name = "reward_distance", precision = 20, scale = 8)
    private BigDecimal rewardDistance;

    @Column(name = "risk_reward", precision = 10, scale = 4)
    private BigDecimal riskReward;

    @Column(name = "confidence", precision = 5, scale = 2)
    private BigDecimal confidence;

    @Column(name = "expires_at")
    private Instant expiresAt;

    public StrategySetupRecord() {}

    public StrategySetupRecord(
            TradingAccount tradingAccount,
            String setupId,
            String symbol,
            String direction,
            BigDecimal entryPrice,
            BigDecimal stopLoss,
            BigDecimal takeProfit,
            BigDecimal riskReward,
            Instant expiresAt
    ) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.symbol = symbol;
        this.direction = direction;
        this.entryPrice = entryPrice;
        this.stopLoss = stopLoss;
        this.takeProfit = takeProfit;
        this.riskReward = riskReward;
        this.expiresAt = expiresAt;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String setupId) { this.setupId = setupId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getDirection() { return direction; }
    public void setDirection(String direction) { this.direction = direction; }

    public String getTimeframe() { return timeframe; }
    public void setTimeframe(String timeframe) { this.timeframe = timeframe; }

    public String getSetupState() { return setupState; }
    public void setSetupState(String setupState) { this.setupState = setupState; }

    public BigDecimal getEntryPrice() { return entryPrice; }
    public void setEntryPrice(BigDecimal entryPrice) { this.entryPrice = entryPrice; }

    public BigDecimal getStopLoss() { return stopLoss; }
    public void setStopLoss(BigDecimal stopLoss) { this.stopLoss = stopLoss; }

    public BigDecimal getTakeProfit() { return takeProfit; }
    public void setTakeProfit(BigDecimal takeProfit) { this.takeProfit = takeProfit; }

    public BigDecimal getRiskDistance() { return riskDistance; }
    public void setRiskDistance(BigDecimal riskDistance) { this.riskDistance = riskDistance; }

    public BigDecimal getRewardDistance() { return rewardDistance; }
    public void setRewardDistance(BigDecimal rewardDistance) { this.rewardDistance = rewardDistance; }

    public BigDecimal getRiskReward() { return riskReward; }
    public void setRiskReward(BigDecimal riskReward) { this.riskReward = riskReward; }

    public BigDecimal getConfidence() { return confidence; }
    public void setConfidence(BigDecimal confidence) { this.confidence = confidence; }

    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant expiresAt) { this.expiresAt = expiresAt; }
}
