package com.quantedge.market.dto;

import java.math.BigDecimal;

/**
 * Tradable product specification from Delta Exchange India (DELTAIN).
 */
public record ProductDto(
        Long productId,
        String symbol,
        String description,
        String contractType,
        String baseAsset,
        String quoteAsset,
        String settlementAsset,
        BigDecimal tickSize,
        BigDecimal lotSize,
        BigDecimal minOrderQty,
        boolean active
) {}
