package com.quantedge.market.dto;

import java.time.Instant;

/**
 * Live market data connectivity and health status.
 */
public record MarketStatusDto(
        boolean connected,
        String exchange,
        String primarySymbol,
        long latencyMs,
        Instant timestamp
) {}
