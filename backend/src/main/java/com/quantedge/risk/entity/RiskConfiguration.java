package com.quantedge.risk.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;

@Entity
@Table(name = "risk_configurations")
public class RiskConfiguration extends BaseEntity {

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false, unique = true)
    private TradingAccount tradingAccount;

    @Column(name = "risk_per_trade_percent", precision = 5, scale = 2, nullable = false)
    private BigDecimal riskPerTradePercent = new BigDecimal("35.00");

    @Column(name = "target_reward_percent", precision = 5, scale = 2, nullable = false)
    private BigDecimal targetRewardPercent = new BigDecimal("60.00");

    @Column(name = "max_leverage", nullable = false)
    private Integer maxLeverage = 100;

    @Column(name = "max_concurrent_trades", nullable = false)
    private Integer maxConcurrentTrades = 1;

    @Column(name = "minimum_risk_reward", precision = 5, scale = 2, nullable = false)
    private BigDecimal minimumRiskReward = new BigDecimal("1.50");

    @Column(name = "max_daily_loss_percent", precision = 5, scale = 2)
    private BigDecimal maxDailyLossPercent;

    @Column(name = "max_drawdown_percent", precision = 5, scale = 2)
    private BigDecimal maxDrawdownPercent;

    public RiskConfiguration() {}

    public RiskConfiguration(TradingAccount tradingAccount) {
        this.tradingAccount = tradingAccount;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public BigDecimal getRiskPerTradePercent() { return riskPerTradePercent; }
    public void setRiskPerTradePercent(BigDecimal riskPerTradePercent) { this.riskPerTradePercent = riskPerTradePercent; }

    public BigDecimal getTargetRewardPercent() { return targetRewardPercent; }
    public void setTargetRewardPercent(BigDecimal targetRewardPercent) { this.targetRewardPercent = targetRewardPercent; }

    public Integer getMaxLeverage() { return maxLeverage; }
    public void setMaxLeverage(Integer maxLeverage) { this.maxLeverage = maxLeverage; }

    public Integer getMaxConcurrentTrades() { return maxConcurrentTrades; }
    public void setMaxConcurrentTrades(Integer maxConcurrentTrades) { this.maxConcurrentTrades = maxConcurrentTrades; }

    public BigDecimal getMinimumRiskReward() { return minimumRiskReward; }
    public void setMinimumRiskReward(BigDecimal minimumRiskReward) { this.minimumRiskReward = minimumRiskReward; }

    public BigDecimal getMaxDailyLossPercent() { return maxDailyLossPercent; }
    public void setMaxDailyLossPercent(BigDecimal maxDailyLossPercent) { this.maxDailyLossPercent = maxDailyLossPercent; }

    public BigDecimal getMaxDrawdownPercent() { return maxDrawdownPercent; }
    public void setMaxDrawdownPercent(BigDecimal maxDrawdownPercent) { this.maxDrawdownPercent = maxDrawdownPercent; }
}
