package com.quantedge.news;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.news.entity.NewsArticle;
import com.quantedge.news.provider.ExternalFinancialNewsProvider;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ExternalFinancialNewsProviderTest {

    private RestTemplate restTemplate;
    private ObjectMapper objectMapper;
    private ExternalFinancialNewsProvider provider;

    @BeforeEach
    void setUp() {
        restTemplate = mock(RestTemplate.class);
        objectMapper = new ObjectMapper();
        provider = new ExternalFinancialNewsProvider("https://min-api.cryptocompare.com/data/v2/news/", "", restTemplate, objectMapper);
    }

    @Test
    @DisplayName("Provider: Successfully parses real CryptoCompare JSON news payload")
    void testParseNewsPayloadSuccess() {
        String json = """
                {
                    "Type": 100,
                    "Message": "News list successfully returned",
                    "Data": [
                        {
                            "id": "1001",
                            "guid": "https://www.coindesk.com/markets/2026/08/23/btc-surge-ath",
                            "published_on": 1787400000,
                            "imageurl": "https://images.coindesk.com/btc.jpg",
                            "title": "Bitcoin Surges Past Key Resistance as Institutional Futures Inflows Accelerate",
                            "url": "https://www.coindesk.com/markets/2026/08/23/btc-surge-ath?utm_source=feed",
                            "source": "coindesk",
                            "body": "Bitcoin perpetual futures witnessed robust demand with open interest jumping 15% across global derivative exchanges.",
                            "tags": "BTC|Derivatives|Markets",
                            "categories": "BTC|Trading|Market",
                            "source_info": {
                                "name": "CoinDesk",
                                "lang": "EN"
                            }
                        },
                        {
                            "id": "1002",
                            "guid": "https://www.bloomberg.com/fed-fomc-rate-cut",
                            "published_on": 1787401000,
                            "title": "Federal Reserve Signals FOMC Rate Cut as Inflation Continues Moderation",
                            "url": "https://www.bloomberg.com/fed-fomc-rate-cut",
                            "source": "bloomberg",
                            "body": "Fed Chair Powell indicated potential policy easing in upcoming central bank meeting as CPI falls.",
                            "tags": "USD|Economy",
                            "categories": "Macro|Fed",
                            "source_info": {
                                "name": "Bloomberg"
                            }
                        }
                    ]
                }
                """;

        when(restTemplate.getForEntity(anyString(), eq(String.class)))
                .thenReturn(new ResponseEntity<>(json, HttpStatus.OK));

        List<NewsArticle> articles = provider.fetchLatestNews();

        assertThat(articles).hasSize(2);

        NewsArticle first = articles.get(0);
        assertThat(first.getTitle()).isEqualTo("Bitcoin Surges Past Key Resistance as Institutional Futures Inflows Accelerate");
        assertThat(first.getSource()).isEqualTo("CoinDesk");
        assertThat(first.getCategory()).isEqualTo("MARKETS");
        assertThat(first.getSentiment()).isEqualTo("BULLISH");
        assertThat(first.getImportance()).isEqualTo("HIGH");
        assertThat(first.getRelevantSymbols()).contains("BTC");
        assertThat(first.getFingerprint()).isNotBlank();
        assertThat(first.getExpiresAt()).isEqualTo(first.getPublishedAt().plus(7, ChronoUnit.DAYS));

        NewsArticle second = articles.get(1);
        assertThat(second.getTitle()).contains("Federal Reserve Signals FOMC Rate Cut");
        assertThat(second.getCategory()).isEqualTo("CENTRAL_BANKS");
        assertThat(second.getImportance()).isEqualTo("CRITICAL");
    }

    @Test
    @DisplayName("Deduplication: Computes identical fingerprint for same article with different tracking URLs")
    void testFingerprintDeduplicationWithTrackingUrls() {
        String title = "Bitcoin ETF Sees Inflows";
        String source = "CoinDesk";
        String url1 = "https://www.coindesk.com/article/123?utm_source=twitter&utm_medium=social";
        String url2 = "https://www.coindesk.com/article/123?ref=binance";

        String fp1 = ExternalFinancialNewsProvider.computeFingerprint(title, source, url1);
        String fp2 = ExternalFinancialNewsProvider.computeFingerprint(title, source, url2);

        assertThat(fp1).isEqualTo(fp2);
    }

    @Test
    @DisplayName("Resilience: Returns empty list on HTTP error without throwing exception")
    void testHttpErrorHandling() {
        when(restTemplate.getForEntity(anyString(), eq(String.class)))
                .thenThrow(new RuntimeException("Connection timed out to external news API"));

        List<NewsArticle> articles = provider.fetchLatestNews();

        assertThat(articles).isNotNull().isEmpty();
    }

    @Test
    @DisplayName("Classification: Correctly categorizes regulation and commodities news")
    void testCategoryClassification() {
        assertThat(ExternalFinancialNewsProvider.classifyCategory("SEC Sues Exchange Over Unregistered Securities", "Regulation"))
                .isEqualTo("REGULATION");
        assertThat(ExternalFinancialNewsProvider.classifyCategory("Crude Oil Prices Drop Amid Supply Glut", "Commodities"))
                .isEqualTo("COMMODITIES");
        assertThat(ExternalFinancialNewsProvider.classifyCategory("RBI Holds Repo Rate Steady at 6.5%", "Central Bank"))
                .isEqualTo("CENTRAL_BANKS");
        assertThat(ExternalFinancialNewsProvider.classifyCategory("Ethereum Network Gas Fees Drop", "ETH"))
                .isEqualTo("CRYPTO");
    }
}
