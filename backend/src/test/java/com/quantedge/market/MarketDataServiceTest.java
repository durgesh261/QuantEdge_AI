package com.quantedge.market;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.market.client.DeltaMarketDataClient;
import com.quantedge.market.dto.*;
import com.quantedge.market.service.MarketDataService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 8: MarketDataService & TradingView DELTAIN Chart Tests")
class MarketDataServiceTest {

    @Mock private DeltaMarketDataClient deltaClient;

    private MarketDataService marketDataService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        marketDataService = new MarketDataService(deltaClient, new com.quantedge.market.service.InstrumentRegistry());
    }

    @Nested
    @DisplayName("Candle Normalization & Interval Handling")
    class CandleTests {

        @Test
        @DisplayName("Normalizes and returns TradingView chart candles from Delta India")
        void fetchesAndNormalizesCandles() throws Exception {
            String rawJson = """
                [
                    {"time": 1720000000, "open": "62000.0", "high": "62150.0", "low": "61900.0", "close": "62080.0", "volume": "123.45"},
                    {"time": 1720000300, "open": "62080.0", "high": "62200.0", "low": "62050.0", "close": "62190.0", "volume": "98.2"}
                ]
            """;
            when(deltaClient.fetchRawCandles(eq("BTCUSD"), eq("5m"), isNull(), isNull()))
                    .thenReturn(objectMapper.readTree(rawJson));

            ChartCandlesResponseDto response = marketDataService.getCandles("BTCUSD.P", "5m", null, null, 500);

            assertThat(response).isNotNull();
            assertThat(response.symbol()).isEqualTo("BTCUSD");
            assertThat(response.exchange()).isEqualTo("DELTAIN");
            assertThat(response.interval()).isEqualTo("5m");
            assertThat(response.candles()).hasSize(2);
            assertThat(response.candles().getFirst().open()).isEqualByComparingTo("62000.0");
            assertThat(response.candles().getFirst().close()).isEqualByComparingTo("62080.0");
        }

        @Test
        @DisplayName("Maps various timeframe strings correctly to Delta resolutions")
        void mapsTimeframes() {
            assertThat(marketDataService.mapIntervalToResolution("1m")).isEqualTo("1m");
            assertThat(marketDataService.mapIntervalToResolution("5m")).isEqualTo("5m");
            assertThat(marketDataService.mapIntervalToResolution("15m")).isEqualTo("15m");
            assertThat(marketDataService.mapIntervalToResolution("30m")).isEqualTo("30m");
            assertThat(marketDataService.mapIntervalToResolution("1h")).isEqualTo("1h");
            assertThat(marketDataService.mapIntervalToResolution("4h")).isEqualTo("4h");
            assertThat(marketDataService.mapIntervalToResolution("1d")).isEqualTo("1d");
        }
    }

    @Nested
    @DisplayName("Product Discovery & Ticker Tests")
    class ProductAndTickerTests {

        @Test
        @DisplayName("Dynamically discovers tradable products from Delta Exchange India")
        void discoversProducts() throws Exception {
            String rawJson = """
                [
                    {
                        "id": 1,
                        "symbol": "BTCUSD",
                        "description": "Bitcoin Perpetual",
                        "contract_type": "perpetual_futures",
                        "settling_asset": {"symbol": "USDT"},
                        "quoting_asset": {"symbol": "USDT"},
                        "tick_size": "0.5",
                        "contract_value": "1",
                        "minimum_order_size": "1",
                        "trading_status": "active"
                    },
                    {
                        "id": 2,
                        "symbol": "ETHUSD",
                        "description": "Ethereum Perpetual",
                        "contract_type": "perpetual_futures",
                        "settling_asset": {"symbol": "USDT"},
                        "quoting_asset": {"symbol": "USDT"},
                        "tick_size": "0.05",
                        "contract_value": "1",
                        "minimum_order_size": "1",
                        "trading_status": "active"
                    }
                ]
            """;
            when(deltaClient.fetchRawProducts()).thenReturn(objectMapper.readTree(rawJson));

            List<ProductDto> products = marketDataService.getProducts();

            assertThat(products).hasSize(2);
            assertThat(products.getFirst().symbol()).isEqualTo("BTCUSD");
            assertThat(products.getFirst().active()).isTrue();
            assertThat(products.get(1).symbol()).isEqualTo("ETHUSD");
        }

        @Test
        @DisplayName("Fetches real 24h ticker data")
        void fetchesTicker() throws Exception {
            String rawJson = """
                {
                    "symbol": "BTCUSD",
                    "mark_price": "64500.50",
                    "close": "64520.00",
                    "high": "65100.00",
                    "low": "63800.00",
                    "volume": "4520.5",
                    "turnover": "291500000",
                    "price_change_percent_24h": "1.25"
                }
            """;
            when(deltaClient.fetchRawTicker("BTCUSD")).thenReturn(objectMapper.readTree(rawJson));

            TickerDto ticker = marketDataService.getTicker("BTCUSD");

            assertThat(ticker).isNotNull();
            assertThat(ticker.symbol()).isEqualTo("BTCUSD");
            assertThat(ticker.markPrice()).isEqualByComparingTo("64500.50");
            assertThat(ticker.lastPrice()).isEqualByComparingTo("64520.00");
            assertThat(ticker.priceChangePercent24h()).isEqualByComparingTo("1.25");
        }
    }
}
