package com.quantedge.trading.dto;

import com.quantedge.trading.entity.TradeRecord;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized, tenant-safe representation of a trade history record.
 */
public record TradeHistoryDto(
        String id,
        String accountId,
        String setupId,
        String symbol,
        String direction,
        BigDecimal entryPrice,
        BigDecimal exitPrice,
        BigDecimal quantity,
        Integer leverage,
        BigDecimal grossPnl,
        BigDecimal tradingFees,
        BigDecimal fundingCosts,
        BigDecimal otherCosts,
        BigDecimal netPnl,
        BigDecimal preTradeBalance,
        BigDecimal postTradeBalance,
        String tradeState,
        String closeReason,
        Instant openedAt,
        Instant closedAt
) {
    public static TradeHistoryDto fromEntity(TradeRecord record) {
        if (record == null) return null;
        return new TradeHistoryDto(
                record.getId(),
                record.getTradingAccount() != null ? record.getTradingAccount().getId() : null,
                record.getSetupId(),
                record.getSymbol(),
                record.getDirection(),
                record.getEntryPrice(),
                record.getExitPrice(),
                record.getQuantity(),
                record.getLeverage(),
                record.getGrossPnl(),
                record.getTradingFees(),
                record.getFundingCosts(),
                record.getOtherCosts(),
                record.getNetPnl(),
                record.getPreTradeBalance(),
                record.getPostTradeBalance(),
                record.getTradeState(),
                record.getCloseReason(),
                record.getOpenedAt(),
                record.getClosedAt()
        );
    }
}
