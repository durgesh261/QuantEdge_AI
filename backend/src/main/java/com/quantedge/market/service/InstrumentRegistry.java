package com.quantedge.market.service;

import com.quantedge.market.dto.ProductDto;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.*;

/**
 * Authoritative Centralized Instrument Registry for QuantEdge AI.
 * <p>
 * Defines canonical specifications for the 4 primary production instruments on Delta Exchange India:
 * <ul>
 *   <li>BTC/USD (BTCUSD)</li>
 *   <li>ETH/USD (ETHUSD)</li>
 *   <li>SOL/USD (SOLUSD)</li>
 *   <li>XRP/USD (XRPUSD)</li>
 * </ul>
 */
@Component
public class InstrumentRegistry {

    public record InstrumentSpecification(
            Long productId,
            String internalSymbol,
            String displaySymbol,
            String exchangeSymbol,
            String baseAsset,
            String quoteAsset,
            String settlementAsset,
            String contractType,
            int pricePrecision,
            int quantityPrecision,
            BigDecimal tickSize,
            BigDecimal lotSize,
            BigDecimal minOrderQty,
            int maxLeverage,
            List<String> supportedTimeframes,
            boolean enabled
    ) {
        public ProductDto toProductDto() {
            return new ProductDto(
                    productId,
                    exchangeSymbol,
                    displaySymbol + " Perpetual Futures",
                    contractType,
                    baseAsset,
                    quoteAsset,
                    settlementAsset,
                    tickSize,
                    lotSize,
                    minOrderQty,
                    enabled
            );
        }
    }

    private static final List<String> CANONICAL_TIMEFRAMES = List.of(
            "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"
    );

    private static final Map<String, InstrumentSpecification> REGISTRY = new LinkedHashMap<>();

    static {
        // 1. BTC/USD
        REGISTRY.put("BTCUSD", new InstrumentSpecification(
                27L,
                "BTCUSD",
                "BTC/USD",
                "BTCUSD",
                "BTC",
                "USDT",
                "USDT",
                "perpetual_futures",
                2,
                4,
                new BigDecimal("0.5"),
                BigDecimal.ONE,
                BigDecimal.ONE,
                100,
                CANONICAL_TIMEFRAMES,
                true
        ));

        // 2. ETH/USD
        REGISTRY.put("ETHUSD", new InstrumentSpecification(
                3136L,
                "ETHUSD",
                "ETH/USD",
                "ETHUSD",
                "ETH",
                "USDT",
                "USDT",
                "perpetual_futures",
                2,
                4,
                new BigDecimal("0.05"),
                BigDecimal.ONE,
                BigDecimal.ONE,
                100,
                CANONICAL_TIMEFRAMES,
                true
        ));

        // 3. SOL/USD
        REGISTRY.put("SOLUSD", new InstrumentSpecification(
                14823L,
                "SOLUSD",
                "SOL/USD",
                "SOLUSD",
                "SOL",
                "USDT",
                "USDT",
                "perpetual_futures",
                2,
                4,
                new BigDecimal("0.01"),
                BigDecimal.ONE,
                BigDecimal.ONE,
                50,
                CANONICAL_TIMEFRAMES,
                true
        ));

        // 4. XRP/USD
        REGISTRY.put("XRPUSD", new InstrumentSpecification(
                14969L,
                "XRPUSD",
                "XRP/USD",
                "XRPUSD",
                "XRP",
                "USDT",
                "USDT",
                "perpetual_futures",
                4,
                2,
                new BigDecimal("0.0001"),
                BigDecimal.ONE,
                BigDecimal.ONE,
                50,
                CANONICAL_TIMEFRAMES,
                true
        ));
    }

    public static String normalize(String symbol) {
        if (symbol == null || symbol.isBlank()) {
            return "BTCUSD";
        }
        String clean = symbol.trim().toUpperCase();
        if (clean.endsWith(".P")) {
            clean = clean.substring(0, clean.length() - 2);
        }
        if (clean.contains("/")) {
            clean = clean.replace("/", "");
        }
        if (clean.contains("-")) {
            clean = clean.replace("-", "");
        }
        return clean;
    }

    public static String normalizeSymbol(String symbol) {
        if (symbol == null) return null;
        return normalize(symbol);
    }

    public static boolean isSupported(String symbol) {
        if (symbol == null) return false;
        return REGISTRY.containsKey(normalize(symbol));
    }

    public static Optional<InstrumentSpecification> getSpec(String symbol) {
        if (symbol == null) return Optional.empty();
        return Optional.ofNullable(REGISTRY.get(normalize(symbol)));
    }

    public Optional<InstrumentSpecification> getSpecification(String symbol) {
        return getSpec(symbol);
    }

    public static List<InstrumentSpecification> getAllSupported() {
        return new ArrayList<>(REGISTRY.values());
    }

    public List<ProductDto> getDefaultProducts() {
        return createDefaultProducts();
    }

    public static List<ProductDto> createDefaultProducts() {
        return REGISTRY.values().stream().map(InstrumentSpecification::toProductDto).toList();
    }
}
