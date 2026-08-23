package com.quantedge.market.dto;

import java.util.List;

/**
 * Standard response container for TradingView chart candles from Delta Exchange India (DELTAIN).
 */
public record ChartCandlesResponseDto(
        String symbol,
        String exchange,
        String interval,
        List<CandleDto> candles
) {}
