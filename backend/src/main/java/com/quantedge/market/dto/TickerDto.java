package com.quantedge.market.dto;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * 24-hour Ticker and mark price data from Delta Exchange India (DELTAIN).
 */
public record TickerDto(
        String symbol,
        BigDecimal markPrice,
        BigDecimal lastPrice,
        BigDecimal high24h,
        BigDecimal low24h,
        BigDecimal volume24h,
        BigDecimal turnover24h,
        BigDecimal priceChangePercent24h,
        Instant timestamp
) {}
