package com.quantedge.trading.dto;

import com.quantedge.trading.position.Position;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized, tenant-safe representation of an active or historical position.
 */
public record PositionDto(
        String id,
        String accountId,
        String deltaPositionId,
        String setupId,
        String entryOrderId,
        String closeOrderId,
        String symbol,
        String side,
        String status,
        BigDecimal entryPrice,
        BigDecimal currentPrice,
        BigDecimal quantity,
        Integer leverage,
        BigDecimal unrealizedPnl,
        BigDecimal realizedPnl,
        BigDecimal liquidationPrice,
        BigDecimal marginUsed,
        BigDecimal stopLossPrice,
        BigDecimal takeProfitPrice,
        String reconciliationState,
        Instant lastReconciledAt,
        Instant openedAt,
        Instant closedAt
) {
    public static PositionDto fromEntity(Position pos) {
        if (pos == null) return null;
        return new PositionDto(
                pos.getId(),
                pos.getTradingAccount() != null ? pos.getTradingAccount().getId() : null,
                pos.getDeltaPositionId(),
                pos.getSetupId(),
                pos.getEntryOrderId(),
                pos.getCloseOrderId(),
                pos.getSymbol(),
                pos.getSide(),
                pos.getStatus(),
                pos.getEntryPrice(),
                pos.getCurrentPrice(),
                pos.getQuantity(),
                pos.getLeverage(),
                pos.getUnrealizedPnl(),
                pos.getRealizedPnl(),
                pos.getLiquidationPrice(),
                pos.getMarginUsed(),
                pos.getStopLossPrice(),
                pos.getTakeProfitPrice(),
                pos.getReconciliationState(),
                pos.getLastReconciledAt(),
                pos.getOpenedAt(),
                pos.getClosedAt()
        );
    }
}
