package com.quantedge.trading;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.auth.entity.User;
import com.quantedge.trading.controller.TradeExecutionController;
import com.quantedge.trading.dto.*;
import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.TradingQueryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 7: Trading API Controller & Security Tests")
class TradingApiControllerTest {

    @Mock private OrderExecutionService executionService;
    @Mock private TradingQueryService queryService;

    private MockMvc mockMvc;
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    private User testUser;

    @BeforeEach
    void setUp() {
        TradeExecutionController controller = new TradeExecutionController(executionService, queryService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new com.quantedge.common.exception.GlobalExceptionHandler())
                .build();

        testUser = new User();
        testUser.setId("usr-owner-123");
        testUser.setEmail("owner@quantedge.io");
    }

    @Nested
    @DisplayName("Trading System Status Endpoint")
    class StatusEndpointTests {

        @Test
        @DisplayName("GET /api/v1/trade/status returns 200 OK with sanitized status")
        void returnsStatus() throws Exception {
            TradingSystemStatusDto status = new TradingSystemStatusDto(
                    "acct-1", "Live Account", "USDT", true, "CONNECTED", "LIVE", "abc***xyz",
                    true, false, false, null, null, null, null,
                    0, 0, new BigDecimal("10000.00"), new BigDecimal("10000.00"),
                    new BigDecimal("10000.00"), BigDecimal.ZERO, Instant.now(), Instant.now()
            );
            when(queryService.getTradingSystemStatus(any(), eq("acct-1"))).thenReturn(status);

            mockMvc.perform(get("/v1/trade/status")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.accountId").value("acct-1"))
                    .andExpect(jsonPath("$.connected").value(true))
                    .andExpect(jsonPath("$.algoEnabled").value(true))
                    .andExpect(jsonPath("$.maskedApiKey").value("abc***xyz"));
        }
    }

    @Nested
    @DisplayName("Orders Endpoints")
    class OrdersEndpointTests {

        @Test
        @DisplayName("GET /api/v1/trade/orders returns 200 OK with list of sanitized orders")
        void returnsOrdersList() throws Exception {
            OrderDto o1 = new OrderDto(
                    "ord-1", "acct-1", "client-ord-1", "delta-1", "setup-1",
                    "BTCUSD", "BUY", "LIMIT", "OPEN", new BigDecimal("60000.00"),
                    new BigDecimal("59000.00"), BigDecimal.ONE, BigDecimal.ZERO,
                    null, 10, false, false, "gtc", Instant.now(), Instant.now(),
                    null, null, "RECONCILED", null
            );
            when(queryService.getOrders(any(), eq("acct-1"), isNull(), isNull(), eq(100)))
                    .thenReturn(List.of(o1));

            mockMvc.perform(get("/v1/trade/orders")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$[0].id").value("ord-1"))
                    .andExpect(jsonPath("$[0].clientOrderId").value("client-ord-1"))
                    .andExpect(jsonPath("$[0].status").value("OPEN"))
                    .andExpect(jsonPath("$[0].reconciliationState").value("RECONCILED"));
        }

        @Test
        @DisplayName("GET /api/v1/trade/orders/{orderId} returns 200 OK for single order")
        void returnsSingleOrder() throws Exception {
            OrderDto o1 = new OrderDto(
                    "ord-1", "acct-1", "client-ord-1", "delta-1", "setup-1",
                    "BTCUSD", "BUY", "LIMIT", "OPEN", new BigDecimal("60000.00"),
                    new BigDecimal("59000.00"), BigDecimal.ONE, BigDecimal.ZERO,
                    null, 10, false, false, "gtc", Instant.now(), Instant.now(),
                    null, null, "RECONCILED", null
            );
            when(queryService.getOrderById(any(), eq("acct-1"), eq("ord-1"))).thenReturn(o1);

            mockMvc.perform(get("/v1/trade/orders/ord-1")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.id").value("ord-1"))
                    .andExpect(jsonPath("$.clientOrderId").value("client-ord-1"));
        }
    }

    @Nested
    @DisplayName("Positions, Fills, History, Signals Endpoints")
    class AdditionalResourceEndpointTests {

        @Test
        @DisplayName("GET /api/v1/trade/positions returns list of sanitized positions")
        void returnsPositions() throws Exception {
            PositionDto pos = new PositionDto(
                    "pos-1", "acct-1", "delta-pos-1", "setup-1", "ord-1", null,
                    "BTCUSD", "LONG", "OPEN", new BigDecimal("60000.00"), new BigDecimal("61000.00"),
                    BigDecimal.ONE, 10, new BigDecimal("1000.00"), BigDecimal.ZERO,
                    new BigDecimal("54000.00"), new BigDecimal("6000.00"), new BigDecimal("59000.00"),
                    new BigDecimal("63000.00"), "AUTHORITATIVE", Instant.now(), Instant.now(), null
            );
            when(queryService.getPositions(any(), eq("acct-1"), eq("OPEN"))).thenReturn(List.of(pos));

            mockMvc.perform(get("/v1/trade/positions")
                            .param("accountId", "acct-1")
                            .param("status", "OPEN")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$[0].id").value("pos-1"))
                    .andExpect(jsonPath("$[0].symbol").value("BTCUSD"))
                    .andExpect(jsonPath("$[0].unrealizedPnl").value(1000.00));
        }

        @Test
        @DisplayName("GET /api/v1/trade/fills returns execution fill history")
        void returnsFills() throws Exception {
            OrderFillDto fill = new OrderFillDto(
                    "fill-1", "acct-1", "ord-1", "delta-fill-100", "client-1", "delta-ord-1",
                    "BTCUSD", "BUY", BigDecimal.ONE, new BigDecimal("60000.00"),
                    new BigDecimal("1.20"), "USDT", Instant.now()
            );
            when(queryService.getFills(any(), eq("acct-1"), isNull(), isNull(), eq(100)))
                    .thenReturn(List.of(fill));

            mockMvc.perform(get("/v1/trade/fills")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$[0].exchangeFillId").value("delta-fill-100"))
                    .andExpect(jsonPath("$[0].fillPrice").value(60000.00));
        }

        @Test
        @DisplayName("GET /api/v1/trade/history returns closed trade history")
        void returnsTradeHistory() throws Exception {
            TradeHistoryDto trade = new TradeHistoryDto(
                    "tr-1", "acct-1", "setup-1", "BTCUSD", "LONG",
                    new BigDecimal("60000.00"), new BigDecimal("62000.00"), BigDecimal.ONE, 10,
                    new BigDecimal("2000.00"), new BigDecimal("24.00"), BigDecimal.ZERO, BigDecimal.ZERO,
                    new BigDecimal("1976.00"), new BigDecimal("10000.00"), new BigDecimal("11976.00"),
                    "POSITION_CLOSED", "TAKE_PROFIT_HIT", Instant.now(), Instant.now()
            );
            when(queryService.getTradeHistory(any(), eq("acct-1"), eq(100))).thenReturn(List.of(trade));

            mockMvc.perform(get("/v1/trade/history")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$[0].id").value("tr-1"))
                    .andExpect(jsonPath("$[0].netPnl").value(1976.00))
                    .andExpect(jsonPath("$[0].tradeState").value("POSITION_CLOSED"));
        }

        @Test
        @DisplayName("GET /api/v1/trade/signals returns deterministic SMC setups")
        void returnsSignals() throws Exception {
            SignalSetupDto sig = new SignalSetupDto(
                    "sig-1", "acct-1", "setup-smc-1", "BTCUSD", "LONG", "15m", "TRADE_SETUP_READY",
                    "SMC_FVG_OB", "2.0.0", 1, new BigDecimal("60000.00"), new BigDecimal("59000.00"),
                    new BigDecimal("63000.00"), new BigDecimal("1000.00"), new BigDecimal("3000.00"),
                    new BigDecimal("3.00"), new BigDecimal("85.00"), Instant.now().plusSeconds(3600), Instant.now()
            );
            when(queryService.getSignals(any(), eq("acct-1"), isNull(), isNull(), eq(100)))
                    .thenReturn(List.of(sig));

            mockMvc.perform(get("/v1/trade/signals")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$[0].setupId").value("setup-smc-1"))
                    .andExpect(jsonPath("$[0].riskReward").value(3.00));
        }
    }

    @Nested
    @DisplayName("Security & Anti-Leakage Tests")
    class SecurityAndLeakageTests {

        @Test
        @DisplayName("Cross-tenant IDOR access throws 403 Forbidden")
        void idorAccessBlocked() throws Exception {
            when(queryService.getOrders(any(), eq("acct-attacker"), any(), any(), any()))
                    .thenThrow(new AccessDeniedException("Access denied: You do not own trading account acct-attacker"));

            mockMvc.perform(get("/v1/trade/orders")
                            .param("accountId", "acct-attacker")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isForbidden());
        }

        @Test
        @DisplayName("Responses never contain unmasked API secrets, passwords, or encryption keys")
        void noSecretsInSerializedResponses() throws Exception {
            TradingSystemStatusDto status = new TradingSystemStatusDto(
                    "acct-1", "Live Account", "USDT", true, "CONNECTED", "LIVE", "abc***xyz",
                    true, false, false, null, null, null, null,
                    0, 0, new BigDecimal("10000.00"), new BigDecimal("10000.00"),
                    new BigDecimal("10000.00"), BigDecimal.ZERO, Instant.now(), Instant.now()
            );
            when(queryService.getTradingSystemStatus(any(), any())).thenReturn(status);

            mockMvc.perform(get("/v1/trade/status")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(content().string(not(containsString("encryptedApiKey"))))
                    .andExpect(content().string(not(containsString("encryptedApiSecret"))))
                    .andExpect(content().string(not(containsString("password"))))
                    .andExpect(content().string(not(containsString("secret"))));
        }
    }
}
