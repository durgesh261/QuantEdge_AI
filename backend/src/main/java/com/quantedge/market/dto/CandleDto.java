package com.quantedge.market.dto;

import java.math.BigDecimal;

/**
 * Normalized OHLCV candle formatted for TradingView chart integration.
 */
public record CandleDto(
        long timestamp,
        BigDecimal open,
        BigDecimal high,
        BigDecimal low,
        BigDecimal close,
        BigDecimal volume
) {}
