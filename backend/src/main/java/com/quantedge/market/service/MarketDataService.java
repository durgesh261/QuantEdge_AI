package com.quantedge.market.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.quantedge.market.client.DeltaMarketDataClient;
import com.quantedge.market.dto.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Service providing normalized market data from Delta Exchange India (DELTAIN)
 * specifically formatted for TradingView charts and market overview.
 */
@Service
public class MarketDataService {

    private static final Logger log = LoggerFactory.getLogger(MarketDataService.class);
    private static final String EXCHANGE_NAME = "DELTAIN";

    private final DeltaMarketDataClient deltaClient;
    private final InstrumentRegistry instrumentRegistry;
    private final Map<String, ProductDto> productCache = new ConcurrentHashMap<>();
    private volatile Instant lastProductFetch = Instant.MIN;

    public MarketDataService(DeltaMarketDataClient deltaClient, InstrumentRegistry instrumentRegistry) {
        this.deltaClient = deltaClient;
        this.instrumentRegistry = instrumentRegistry;
    }

    /**
     * Normalizes trading symbol (e.g. BTCUSD.P -> BTCUSD).
     */
    public String normalizeSymbol(String symbol) {
        return InstrumentRegistry.normalize(symbol);
    }

    /**
     * Maps user interval string to Delta Exchange resolution string.
     */
    public String mapIntervalToResolution(String interval) {
        if (interval == null || interval.isBlank()) return "1h";
        return switch (interval.trim().toLowerCase()) {
            case "1m", "1" -> "1m";
            case "5m", "5" -> "5m";
            case "15m", "15" -> "15m";
            case "30m", "30" -> "30m";
            case "1h", "60" -> "1h";
            case "2h", "120" -> "2h";
            case "4h", "240" -> "4h";
            case "1d", "d", "1440" -> "1d";
            default -> "1h";
        };
    }

    /**
     * Fetches normalized OHLCV candles formatted for TradingView chart integration.
     */
    public ChartCandlesResponseDto getCandles(String symbol, String interval, Long start, Long end, Integer limit) {
        String cleanSymbol = normalizeSymbol(symbol);
        String resolution = mapIntervalToResolution(interval);
        int maxCandles = (limit != null && limit > 0) ? Math.min(limit, 2000) : 500;

        JsonNode raw = deltaClient.fetchRawCandles(cleanSymbol, resolution, start, end);
        List<CandleDto> candles = new ArrayList<>();

        if (raw != null && raw.isArray()) {
            for (JsonNode node : raw) {
                long ts = node.path("time").asLong(0);
                if (ts == 0) ts = node.path("timestamp").asLong(0);

                BigDecimal open = parseDecimal(node.path("open"));
                BigDecimal high = parseDecimal(node.path("high"));
                BigDecimal low = parseDecimal(node.path("low"));
                BigDecimal close = parseDecimal(node.path("close"));
                BigDecimal volume = parseDecimal(node.path("volume"));

                if (open != null && high != null && low != null && close != null) {
                    candles.add(new CandleDto(ts, open, high, low, close, volume != null ? volume : BigDecimal.ZERO));
                }
            }
        }

        // Sort ascending by timestamp for TradingView charting library
        candles.sort(Comparator.comparingLong(CandleDto::timestamp));

        // Enforce limit if size exceeds
        if (candles.size() > maxCandles) {
            candles = candles.subList(candles.size() - maxCandles, candles.size());
        }

        return new ChartCandlesResponseDto(cleanSymbol, EXCHANGE_NAME, resolution, candles);
    }

    /**
     * Fetches real 24-hour ticker statistics from Delta India.
     */
    public TickerDto getTicker(String symbol) {
        String cleanSymbol = normalizeSymbol(symbol);
        JsonNode raw = deltaClient.fetchRawTicker(cleanSymbol);

        BigDecimal markPrice = BigDecimal.ZERO;
        BigDecimal lastPrice = BigDecimal.ZERO;
        BigDecimal high24h = BigDecimal.ZERO;
        BigDecimal low24h = BigDecimal.ZERO;
        BigDecimal volume24h = BigDecimal.ZERO;
        BigDecimal turnover24h = BigDecimal.ZERO;
        BigDecimal priceChangePercent = BigDecimal.ZERO;

        if (raw != null) {
            markPrice = parseDecimal(raw.path("mark_price"));
            lastPrice = parseDecimal(raw.path("close"));
            if (lastPrice == null) lastPrice = parseDecimal(raw.path("last_price"));
            high24h = parseDecimal(raw.path("high"));
            low24h = parseDecimal(raw.path("low"));
            volume24h = parseDecimal(raw.path("volume"));
            turnover24h = parseDecimal(raw.path("turnover"));
            priceChangePercent = parseDecimal(raw.path("price_change_percent_24h"));
            if (priceChangePercent == null) priceChangePercent = parseDecimal(raw.path("price_change_24h"));
        }

        return new TickerDto(
                cleanSymbol,
                markPrice != null ? markPrice : BigDecimal.ZERO,
                lastPrice != null ? lastPrice : BigDecimal.ZERO,
                high24h != null ? high24h : BigDecimal.ZERO,
                low24h != null ? low24h : BigDecimal.ZERO,
                volume24h != null ? volume24h : BigDecimal.ZERO,
                turnover24h != null ? turnover24h : BigDecimal.ZERO,
                priceChangePercent != null ? priceChangePercent : BigDecimal.ZERO,
                Instant.now()
        );
    }

    /**
     * Discovers all tradable products from Delta Exchange India.
     */
    public List<ProductDto> getProducts() {
        if (!productCache.isEmpty() && Instant.now().isBefore(lastProductFetch.plusSeconds(300))) {
            return new ArrayList<>(productCache.values());
        }

        JsonNode raw = deltaClient.fetchRawProducts();
        List<ProductDto> list = new ArrayList<>();

        if (raw != null && raw.isArray()) {
            productCache.clear();
            for (JsonNode node : raw) {
                Long id = node.path("id").asLong();
                String sym = node.path("symbol").asText("");
                String desc = node.path("description").asText("");
                String contractType = node.path("contract_type").asText("perpetual_futures");
                String baseAsset = node.path("settling_asset").path("symbol").asText("USD");
                String quoteAsset = node.path("quoting_asset").path("symbol").asText("USDT");
                String settlementAsset = node.path("settling_asset").path("symbol").asText("USDT");

                BigDecimal tickSize = parseDecimal(node.path("tick_size"));
                BigDecimal lotSize = parseDecimal(node.path("contract_value"));
                BigDecimal minQty = parseDecimal(node.path("minimum_order_size"));
                boolean active = "active".equalsIgnoreCase(node.path("trading_status").asText("active"));

                ProductDto dto = new ProductDto(
                        id, sym, desc, contractType, baseAsset, quoteAsset, settlementAsset,
                        tickSize != null ? tickSize : new BigDecimal("0.5"),
                        lotSize != null ? lotSize : BigDecimal.ONE,
                        minQty != null ? minQty : BigDecimal.ONE,
                        active
                );
                productCache.put(sym, dto);
                list.add(dto);
            }
            lastProductFetch = Instant.now();
        }

        if (list.isEmpty() && !productCache.isEmpty()) {
            return new ArrayList<>(productCache.values());
        }

        if (list.isEmpty()) {
            return instrumentRegistry.getDefaultProducts();
        }

        return list;
    }

    /**
     * Checks market connectivity status and latency.
     */
    public MarketStatusDto getMarketStatus(String symbol) {
        String cleanSymbol = normalizeSymbol(symbol);
        long start = System.currentTimeMillis();
        JsonNode t = deltaClient.fetchRawTicker(cleanSymbol);
        long latency = System.currentTimeMillis() - start;
        boolean connected = t != null;

        return new MarketStatusDto(
                connected,
                EXCHANGE_NAME,
                cleanSymbol,
                latency,
                Instant.now()
        );
    }

    /**
     * Fetches tickers for all supported symbols in a single request.
     * Returns a map of symbol -> TickerDto for efficient frontend loading.
     */
    public Map<String, TickerDto> getAllTickers() {
        Map<String, TickerDto> result = new LinkedHashMap<>();
        String[] supportedSymbols = {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"};
        
        for (String symbol : supportedSymbols) {
            try {
                TickerDto ticker = getTicker(symbol);
                result.put(symbol, ticker);
            } catch (Exception e) {
                log.warn("Failed to fetch ticker for {}: {}", symbol, e.getMessage());
                // Return zero ticker for failed symbols to maintain consistent response structure
                result.put(symbol, new TickerDto(
                        symbol,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        BigDecimal.ZERO,
                        Instant.now()
                ));
            }
        }
        return result;
    }

    private BigDecimal parseDecimal(JsonNode node) {
        if (node == null || node.isMissingNode() || node.isNull()) return null;
        try {
            if (node.isNumber()) return BigDecimal.valueOf(node.asDouble());
            String text = node.asText().trim();
            if (text.isEmpty()) return null;
            return new BigDecimal(text);
        } catch (Exception e) {
            return null;
        }
    }
}
