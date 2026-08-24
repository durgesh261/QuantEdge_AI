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

    @Column(name = "strategy_name", nullable = false, length = 50)
    private String strategyName = "SMC";

    @Column(name = "strategy_version", nullable = false, length = 20)
    private String strategyVersion = "2.1";

    @Column(name = "configuration_version")
    private Integer configurationVersion = 1;

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

    // SMC Structural Data
    @Column(name = "structure_break_price", precision = 20, scale = 8)
    private BigDecimal structureBreakPrice;

    @Column(name = "order_block_price", precision = 20, scale = 8)
    private BigDecimal orderBlockPrice;

    @Column(name = "ob_mitigated")
    private Boolean obMitigated;

    @Column(name = "fvg_price", precision = 20, scale = 8)
    private BigDecimal fvgPrice;

    @Column(name = "fvg_mitigated")
    private Boolean fvgMitigated;

    @Column(name = "choch_price", precision = 20, scale = 8)
    private BigDecimal chochPrice;

    @Column(name = "liquidity_level_high", precision = 20, scale = 8)
    private BigDecimal liquidityLevelHigh;

    @Column(name = "liquidity_level_low", precision = 20, scale = 8)
    private BigDecimal liquidityLevelLow;

    @Column(name = "leverage")
    private Integer leverage;

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
        this.strategyName = "SMC";
        this.strategyVersion = "2.1";
        this.configurationVersion = 1;
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

    public String getStrategyName() { return strategyName; }
    public void setStrategyName(String strategyName) { this.strategyName = strategyName; }

    public String getStrategyVersion() { return strategyVersion; }
    public void setStrategyVersion(String strategyVersion) { this.strategyVersion = strategyVersion; }

    public Integer getConfigurationVersion() { return configurationVersion; }
    public void setConfigurationVersion(Integer configurationVersion) { this.configurationVersion = configurationVersion; }

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

    public BigDecimal getStructureBreakPrice() { return structureBreakPrice; }
    public void setStructureBreakPrice(BigDecimal structureBreakPrice) { this.structureBreakPrice = structureBreakPrice; }

    public BigDecimal getOrderBlockPrice() { return orderBlockPrice; }
    public void setOrderBlockPrice(BigDecimal orderBlockPrice) { this.orderBlockPrice = orderBlockPrice; }

    public Boolean getObMitigated() { return obMitigated; }
    public void setObMitigated(Boolean obMitigated) { this.obMitigated = obMitigated; }

    public BigDecimal getFvgPrice() { return fvgPrice; }
    public void setFvgPrice(BigDecimal fvgPrice) { this.fvgPrice = fvgPrice; }

    public Boolean getFvgMitigated() { return fvgMitigated; }
    public void setFvgMitigated(Boolean fvgMitigated) { this.fvgMitigated = fvgMitigated; }

    public BigDecimal getChochPrice() { return chochPrice; }
    public void setChochPrice(BigDecimal chochPrice) { this.chochPrice = chochPrice; }

    public BigDecimal getLiquidityLevelHigh() { return liquidityLevelHigh; }
    public void setLiquidityLevelHigh(BigDecimal liquidityLevelHigh) { this.liquidityLevelHigh = liquidityLevelHigh; }

    public BigDecimal getLiquidityLevelLow() { return liquidityLevelLow; }
    public void setLiquidityLevelLow(BigDecimal liquidityLevelLow) { this.liquidityLevelLow = liquidityLevelLow; }

    public Integer getLeverage() { return leverage; }
    public void setLeverage(Integer leverage) { this.leverage = leverage; }
}
