package com.quantedge.trading.dto;

import com.quantedge.trading.execution.OrderFill;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized, tenant-safe representation of an execution fill event.
 */
public record OrderFillDto(
        String id,
        String accountId,
        String orderId,
        String exchangeFillId,
        String clientOrderId,
        String deltaOrderId,
        String symbol,
        String side,
        BigDecimal fillQuantity,
        BigDecimal fillPrice,
        BigDecimal fee,
        String feeAsset,
        Instant filledAt
) {
    public static OrderFillDto fromEntity(OrderFill fill) {
        if (fill == null) return null;
        return new OrderFillDto(
                fill.getId(),
                fill.getTradingAccount() != null ? fill.getTradingAccount().getId() : null,
                fill.getOrder() != null ? fill.getOrder().getId() : null,
                fill.getExchangeFillId(),
                fill.getClientOrderId(),
                fill.getDeltaOrderId(),
                fill.getSymbol(),
                fill.getSide(),
                fill.getFillQuantity(),
                fill.getFillPrice(),
                fill.getFee(),
                fill.getFeeAsset(),
                fill.getFilledAt()
        );
    }
}
