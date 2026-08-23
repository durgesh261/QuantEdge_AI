package com.quantedge.trading.dto;

import com.quantedge.strategy.entity.StrategySetupRecord;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized, tenant-safe representation of a persisted strategy setup/signal.
 */
public record SignalSetupDto(
        String id,
        String accountId,
        String setupId,
        String symbol,
        String direction,
        String timeframe,
        String setupState,
        String strategyName,
        String strategyVersion,
        Integer configurationVersion,
        BigDecimal entryPrice,
        BigDecimal stopLoss,
        BigDecimal takeProfit,
        BigDecimal riskDistance,
        BigDecimal rewardDistance,
        BigDecimal riskReward,
        BigDecimal confidence,
        Instant expiresAt,
        Instant createdAt
) {
    public static SignalSetupDto fromEntity(StrategySetupRecord setup) {
        if (setup == null) return null;
        return new SignalSetupDto(
                setup.getId(),
                setup.getTradingAccount() != null ? setup.getTradingAccount().getId() : null,
                setup.getSetupId(),
                setup.getSymbol(),
                setup.getDirection(),
                setup.getTimeframe(),
                setup.getSetupState(),
                setup.getStrategyName(),
                setup.getStrategyVersion(),
                setup.getConfigurationVersion(),
                setup.getEntryPrice(),
                setup.getStopLoss(),
                setup.getTakeProfit(),
                setup.getRiskDistance(),
                setup.getRewardDistance(),
                setup.getRiskReward(),
                setup.getConfidence(),
                setup.getExpiresAt(),
                setup.getCreatedAt()
        );
    }
}
