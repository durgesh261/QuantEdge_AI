package com.quantedge.trading.dto;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Consolidated view of trading system status for the authenticated user.
 */
public record TradingSystemStatusDto(
        String accountId,
        String accountName,
        String baseCurrency,
        boolean connected,
        String connectionStatus,
        String environment,
        String maskedApiKey,
        boolean algoEnabled,
        boolean killSwitchActive,
        boolean hasActiveTrade,
        String activeSetupId,
        String activeSymbol,
        String activeLockState,
        Instant lockAcquiredAt,
        int openPositionsCount,
        int openOrdersCount,
        BigDecimal totalEquity,
        BigDecimal availableBalance,
        BigDecimal currentBalance,
        BigDecimal marginUsed,
        Instant lastSyncedAt,
        Instant lastConnectedAt
) {}
