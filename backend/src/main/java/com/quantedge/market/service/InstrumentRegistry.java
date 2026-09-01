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

    /**
     * Authoritative maximum leverage: the 1x..100x band, uniform across every
     * instrument. Mirrors {@code quantedge.execution.leverage.MAX_LEVERAGE} in
     * the Python engine, which is the primary definition.
     * <p>
     * SOLUSD and XRPUSD previously carried 50 here, a value retained from the
     * pre-registry gateway. That made the Python gateway, this gateway and the
     * order-ticket UI all refuse a requested 100x on those two symbols while
     * every other layer permitted it. The owner authorised a uniform band, so
     * the two 50s were raised rather than the other layers lowered.
     * <p>
     * NOT an exchange fact: Delta India publishes no leverage ceiling. The
     * snapshot records a {@code default_leverage} of 100 for SOLUSD/XRPUSD
     * (200 for BTCUSD/ETHUSD), so 100 is no looser than a figure Delta itself
     * records -- corroboration of direction only, not verification.
     */
    public static final int MAX_LEVERAGE = 100;

    /**
     * Smallest leverage that is a trade; below this there is no position.
     * Mirrors {@code quantedge.execution.leverage.MIN_LEVERAGE}. Declared
     * beside its maximum so a validator cannot reference one bound from here
     * and hardcode the other.
     */
    public static final int MIN_LEVERAGE = 1;

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
                MAX_LEVERAGE,
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
                MAX_LEVERAGE,
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
                MAX_LEVERAGE,
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
                MAX_LEVERAGE,
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
