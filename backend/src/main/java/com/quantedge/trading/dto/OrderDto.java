package com.quantedge.trading.dto;

import com.quantedge.trading.entity.Order;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Sanitized, tenant-safe representation of an order.
 * Exposes full execution and reconciliation state without sensitive credentials.
 */
public record OrderDto(
        String id,
        String accountId,
        String clientOrderId,
        String deltaOrderId,
        String setupId,
        String symbol,
        String side,
        String orderType,
        String status,
        BigDecimal price,
        BigDecimal stopPrice,
        BigDecimal quantity,
        BigDecimal filledQuantity,
        BigDecimal averageFillPrice,
        Integer leverage,
        Boolean reduceOnly,
        Boolean postOnly,
        String timeInForce,
        Instant placedAt,
        Instant submittedAt,
        Instant filledAt,
        Instant cancelledAt,
        String reconciliationState,
        String errorMessage
) {
    public static OrderDto fromEntity(Order order) {
        if (order == null) return null;
        return new OrderDto(
                order.getId(),
                order.getTradingAccount() != null ? order.getTradingAccount().getId() : null,
                order.getClientOrderId(),
                order.getDeltaOrderId(),
                order.getSetupId(),
                order.getSymbol(),
                order.getSide(),
                order.getOrderType(),
                order.getStatus(),
                order.getPrice(),
                order.getStopPrice(),
                order.getQuantity(),
                order.getFilledQuantity(),
                order.getAverageFillPrice(),
                order.getLeverage(),
                order.getReduceOnly(),
                order.getPostOnly(),
                order.getTimeInForce(),
                order.getPlacedAt(),
                order.getSubmittedAt(),
                order.getFilledAt(),
                order.getCancelledAt(),
                order.getReconciliationState(),
                order.getErrorMessage()
        );
    }
}
