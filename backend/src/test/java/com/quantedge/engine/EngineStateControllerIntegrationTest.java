package com.quantedge.engine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.trading.service.TradePersistenceService;
import com.quantedge.trading.service.TradePersistenceService.AccountStateSnapshot;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseResult;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenResult;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import com.quantedge.common.config.JwtTokenProvider;
import org.springframework.security.core.userdetails.UserDetailsService;

import java.math.BigDecimal;
import java.time.Instant;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = EngineStateController.class)
@AutoConfigureMockMvc(addFilters = false)
class EngineStateControllerIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockBean
    private TradePersistenceService persistenceService;

    @MockBean
    private TradingAccountRepository accountRepository;

    @MockBean
    private JwtTokenProvider jwtTokenProvider;

    @MockBean
    private UserDetailsService userDetailsService;

    @Test
    @DisplayName("GET /api/engine/state/{accountId} - returns complete state snapshot")
    void testGetAccountState() throws Exception {
        AccountStateSnapshot snapshot = new AccountStateSnapshot(
                "acct-100",
                true,
                "setup-100",
                "BTCUSDT",
                "ENTRY_SUBMITTED",
                Instant.now(),
                new BigDecimal("10000.00"),
                new BigDecimal("10000.00"),
                5,
                new BigDecimal("1500.00"),
                new BigDecimal("120.00"),
                true,
                false
        );

        when(persistenceService.getAccountStateSnapshot("acct-100")).thenReturn(snapshot);
        when(persistenceService.getNextTradeCapital("acct-100")).thenReturn(new BigDecimal("10000.00"));

        mockMvc.perform(get("/api/engine/state/acct-100")
                        .contentType(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accountId").value("acct-100"))
                .andExpect(jsonPath("$.hasActiveTrade").value(true))
                .andExpect(jsonPath("$.activeSetupId").value("setup-100"))
                .andExpect(jsonPath("$.activeSymbol").value("BTCUSDT"))
                .andExpect(jsonPath("$.currentBalance").value(10000.00))
                .andExpect(jsonPath("$.totalClosedTrades").value(5))
                .andExpect(jsonPath("$.totalNetPnl").value(1500.00))
                .andExpect(jsonPath("$.algoEnabled").value(true))
                .andExpect(jsonPath("$.killSwitchActive").value(false));
    }

    @Test
    @DisplayName("POST /api/engine/trade/open/{accountId} - successfully opens trade")
    void testOpenTradeSuccess() throws Exception {
        TradeOpenResult openResult = new TradeOpenResult(true, "tr-100", "lock-100", null);
        when(persistenceService.openTrade(any(TradeOpenRequest.class))).thenReturn(openResult);

        EngineStateController.TradeOpenApiRequest req = new EngineStateController.TradeOpenApiRequest(
                "setup-101", "BTCUSDT", "LONG",
                new BigDecimal("50000.00"), new BigDecimal("0.10"), 17,
                new BigDecimal("10000.00"),
                new BigDecimal("50200.00"), new BigDecimal("49800.00"),
                new BigDecimal("49800.00"), new BigDecimal("58470.00"),
                1, new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        mockMvc.perform(post("/api/engine/trade/open/acct-100")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.tradeRecordId").value("tr-100"))
                .andExpect(jsonPath("$.data.lockId").value("lock-100"));
    }

    @Test
    @DisplayName("POST /api/engine/trade/open/{accountId} - returns 409 CONFLICT on active trade lock")
    void testOpenTradeConflict() throws Exception {
        when(persistenceService.openTrade(any(TradeOpenRequest.class)))
                .thenThrow(new TradePersistenceService.TradeLockException("One-trade-at-a-time rule violated"));

        EngineStateController.TradeOpenApiRequest req = new EngineStateController.TradeOpenApiRequest(
                "setup-102", "BTCUSDT", "LONG",
                new BigDecimal("50000.00"), new BigDecimal("0.10"), 17,
                new BigDecimal("10000.00"),
                null, null, null, null,
                1, new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        mockMvc.perform(post("/api/engine/trade/open/acct-100")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error").value("One-trade-at-a-time rule violated"));
    }

    @Test
    @DisplayName("POST /api/engine/trade/close/{accountId} - closes trade and returns net PnL and post balance")
    void testCloseTradeSuccess() throws Exception {
        TradeCloseResult closeResult = new TradeCloseResult(
                true,
                new BigDecimal("520.00"),
                new BigDecimal("10520.00"),
                null
        );
        when(persistenceService.closeTrade(any(TradeCloseRequest.class))).thenReturn(closeResult);

        EngineStateController.TradeCloseApiRequest req = new EngineStateController.TradeCloseApiRequest(
                "setup-103",
                new BigDecimal("600.00"),
                new BigDecimal("72.00"),
                new BigDecimal("8.00"),
                BigDecimal.ZERO,
                new BigDecimal("56000.00"),
                "TAKE_PROFIT",
                null,
                "tp-100"
        );

        mockMvc.perform(post("/api/engine/trade/close/acct-100")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.netPnl").value(520.00))
                .andExpect(jsonPath("$.data.postTradeBalance").value(10520.00));
    }

    @Test
    @DisplayName("GET /api/engine/capital/{accountId} - returns next trade capital")
    void testGetNextTradeCapital() throws Exception {
        when(persistenceService.getNextTradeCapital("acct-100")).thenReturn(new BigDecimal("10520.00"));

        mockMvc.perform(get("/api/engine/capital/acct-100")
                        .contentType(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data").value(10520.00));
    }

    @Test
    @DisplayName("POST /api/engine/trade/force-release/{accountId} - force releases lock")
    void testForceReleaseLock() throws Exception {
        doNothing().when(persistenceService).forceReleaseLock("acct-100", "DELTA_RECONCILED");

        mockMvc.perform(post("/api/engine/trade/force-release/acct-100")
                        .param("reason", "DELTA_RECONCILED")
                        .contentType(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(persistenceService, times(1)).forceReleaseLock("acct-100", "DELTA_RECONCILED");
    }
}
