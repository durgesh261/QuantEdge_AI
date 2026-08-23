package com.quantedge.trading;

import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.OrderValidationGateway;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.account.service.LiveAccountSyncService;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.risk.entity.RiskConfiguration;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.repository.OrderRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@DisplayName("OrderExecutionService Safety & Automated-Only Architecture Tests")
class OrderExecutionServiceTest {

    private OrderValidationGateway validationGateway;
    private DeltaIndiaRestClient deltaRestClient;
    private DeltaCredentialService credentialService;
    private LiveAccountSyncService accountSyncService;
    private TradingAccountRepository tradingAccountRepository;
    private DeltaConnectionRepository deltaConnectionRepository;
    private RiskConfigurationRepository riskConfigRepository;
    private StrategySetupRepository strategySetupRepository;
    private OrderRepository orderRepository;
    private AuditLogRepository auditLogRepository;
    private ObjectMapper objectMapper;

    private OrderExecutionService executionService;

    private User userA;
    private User userB;
    private TradingAccount accountA;
    private RiskConfiguration riskConfigA;
    private DeltaConnection connectionA;
    private StrategySetupRecord validLongSetup;

    @BeforeEach
    void setUp() {
        validationGateway = mock(OrderValidationGateway.class);
        deltaRestClient = mock(DeltaIndiaRestClient.class);
        credentialService = mock(DeltaCredentialService.class);
        accountSyncService = mock(LiveAccountSyncService.class);
        tradingAccountRepository = mock(TradingAccountRepository.class);
        deltaConnectionRepository = mock(DeltaConnectionRepository.class);
        riskConfigRepository = mock(RiskConfigurationRepository.class);
        strategySetupRepository = mock(StrategySetupRepository.class);
        orderRepository = mock(OrderRepository.class);
        auditLogRepository = mock(AuditLogRepository.class);
        objectMapper = new ObjectMapper();

        executionService = new OrderExecutionService(
                validationGateway,
                deltaRestClient,
                credentialService,
                accountSyncService,
                tradingAccountRepository,
                deltaConnectionRepository,
                riskConfigRepository,
                strategySetupRepository,
                orderRepository,
                auditLogRepository,
                objectMapper
        );

        userA = new User();
        userA.setId("user-a-uuid");
        userA.setEmail("usera@quantedge.ai");

        userB = new User();
        userB.setId("user-b-uuid");
        userB.setEmail("userb@quantedge.ai");

        accountA = new TradingAccount(userA, "Live Test Account", "LIVE", "USDT");
        accountA.setId("acct-a-uuid");
        accountA.setIsActive(true);
        accountA.setKillSwitchActive(false);
        accountA.setAlgoEnabled(true);
        accountA.setTotalEquity(new BigDecimal("10000.00"));
        accountA.setAvailableBalance(new BigDecimal("9500.00"));

        riskConfigA = new RiskConfiguration();
        riskConfigA.setTradingAccount(accountA);
        riskConfigA.setMaxLeverage(10);
        riskConfigA.setRiskPerTradePercent(new BigDecimal("1.0")); // 1% = $100 risk
        riskConfigA.setMaxConcurrentTrades(3);

        connectionA = new DeltaConnection();
        connectionA.setTradingAccount(accountA);
        connectionA.setEncryptedApiKey("enc_api_key_A");
        connectionA.setEncryptedApiSecret("enc_api_secret_A");

        validLongSetup = new StrategySetupRecord();
        validLongSetup.setSetupId("setup-long-123");
        validLongSetup.setSymbol("BTCUSD");
        validLongSetup.setDirection("LONG");
        validLongSetup.setSetupState("TRADE_SETUP_READY");
        validLongSetup.setEntryPrice(new BigDecimal("60000.00"));
        validLongSetup.setStopLoss(new BigDecimal("59000.00")); // $1000 risk distance
        validLongSetup.setTakeProfit(new BigDecimal("62000.00"));
        validLongSetup.setRiskDistance(new BigDecimal("1000.00"));
        validLongSetup.setExpiresAt(Instant.now().plusSeconds(3600));

        when(tradingAccountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));
        when(riskConfigRepository.findByTradingAccountId("acct-a-uuid")).thenReturn(Optional.of(riskConfigA));
        when(strategySetupRepository.findBySetupId("setup-long-123")).thenReturn(Optional.of(validLongSetup));
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-a-uuid", "LIVE")).thenReturn(Optional.of(connectionA));
        when(deltaConnectionRepository.findByTradingAccountId("acct-a-uuid")).thenReturn(Optional.of(connectionA));
        when(credentialService.decrypt("enc_api_key_A")).thenReturn("decrypted_api_key");
        when(credentialService.decrypt("enc_api_secret_A")).thenReturn("decrypted_api_secret");

        when(accountSyncService.syncLiveAccount(eq("acct-a-uuid"), anyString(), anyString())).thenReturn(
                new LiveAccountSyncService.SyncSummary(
                        true,
                        Instant.now(),
                        "acct-a-uuid",
                        new BigDecimal("10000.00"),
                        new BigDecimal("9500.00"),
                        new BigDecimal("500.00"),
                        0,
                        0,
                        Collections.emptyList(),
                        Collections.emptyList(),
                        Collections.emptyList(),
                        Collections.emptyList(),
                        null
                )
        );

        when(orderRepository.save(any(Order.class))).thenAnswer(inv -> {
            Order o = inv.getArgument(0);
            o.setId("order-101");
            return o;
        });
    }

    @Test
    @DisplayName("Safety: Unauthorized user cannot execute trades on another user's account (IDOR protection)")
    void testUnauthorizedUserExecutionBlocked() {
        var command = new OrderExecutionService.ExecutionCommand(
                "user-b-uuid", // User B trying to execute on User A's account
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-1",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("FORBIDDEN", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Kill switch blocks automated order execution")
    void testKillSwitchActiveBlocksExecution() {
        accountA.setKillSwitchActive(true);

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-2",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("KILL_SWITCH_ACTIVE", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Disabled algorithmic trading blocks automated order execution")
    void testAlgoDisabledBlocksExecution() {
        accountA.setAlgoEnabled(false);

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-3",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("ALGO_DISABLED", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Expired or unready strategy setup is rejected")
    void testUnreadySetupRejected() {
        validLongSetup.setSetupState("PENDING_ANALYSIS");

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-4",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("DECISION_NOT_READY", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Invalid SL/TP geometry for LONG setup is rejected")
    void testInvalidLongGeometryRejected() {
        // Inverted: SL > Entry
        validLongSetup.setStopLoss(new BigDecimal("61000.00"));
        validLongSetup.setTakeProfit(new BigDecimal("62000.00"));

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-5",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("INVALID_TP_SL_GEOMETRY", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Max concurrent trades limit is enforced from live exchange state")
    void testMaxConcurrentTradesExceeded() {
        riskConfigA.setMaxConcurrentTrades(2);
        when(accountSyncService.syncLiveAccount(eq("acct-a-uuid"), anyString(), anyString())).thenReturn(
                new LiveAccountSyncService.SyncSummary(
                        true,
                        Instant.now(),
                        "acct-a-uuid",
                        new BigDecimal("10000.00"),
                        new BigDecimal("9500.00"),
                        new BigDecimal("500.00"),
                        2, // 2 open positions already active
                        0,
                        Collections.emptyList(),
                        Collections.emptyList(),
                        Collections.emptyList(),
                        Collections.emptyList(),
                        null
                )
        );

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-6",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("MAX_CONCURRENT_TRADES_EXCEEDED", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Safety: Duplicate setup execution is rejected by idempotency check")
    void testDuplicateSetupIdRejected() {
        when(orderRepository.existsBySetupIdAndStatusIn(eq("setup-long-123"), anyList())).thenReturn(true);

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-7",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertFalse(result.success());
        assertEquals("DUPLICATE_SETUP_ID", result.rejectionCode());
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Execution: Successful automated order dispatches POST /v2/orders with authoritative TP/SL payload")
    @SuppressWarnings("unchecked")
    void testSuccessfulAutomatedExecution() {
        String mockDeltaSuccess = """
                {
                    "success": true,
                    "result": {
                        "id": "delta-ord-9988",
                        "product_symbol": "BTCUSD",
                        "state": "open",
                        "size": 1,
                        "limit_price": "60000.00",
                        "stop_loss_price": "59000.00",
                        "take_profit_price": "62000.00"
                    }
                }
                """;

        when(deltaRestClient.executeRequest(
                eq("decrypted_api_key"),
                eq("decrypted_api_secret"),
                eq(HttpMethod.POST),
                eq("/v2/orders"),
                isNull(),
                anyMap()
        )).thenReturn(ResponseEntity.ok(mockDeltaSuccess));

        var command = new OrderExecutionService.ExecutionCommand(
                "user-a-uuid",
                "acct-a-uuid",
                "setup-long-123",
                "client-ord-success",
                false
        );

        var result = executionService.executeAuthoritativeOrder(command);
        assertTrue(result.success());
        assertEquals(OrderExecutionService.ExecutionState.SUBMITTED, result.state());
        assertEquals("delta-ord-9988", result.orderId());
        assertEquals("client-ord-success", result.clientOrderId());

        ArgumentCaptor<Map<String, Object>> payloadCaptor = ArgumentCaptor.forClass(Map.class);
        verify(deltaRestClient).executeRequest(
                eq("decrypted_api_key"),
                eq("decrypted_api_secret"),
                eq(HttpMethod.POST),
                eq("/v2/orders"),
                isNull(),
                payloadCaptor.capture()
        );

        Map<String, Object> payload = payloadCaptor.getValue();
        assertEquals("BTCUSD", payload.get("product_symbol"));
        assertEquals("buy", payload.get("side"));
        assertEquals("limit_order", payload.get("order_type"));
        assertEquals("60000.00", payload.get("limit_price"));
        assertEquals("59000.00", payload.get("stop_loss_price"));
        assertEquals("62000.00", payload.get("take_profit_price"));
        assertEquals("client-ord-success", payload.get("client_order_id"));
    }

    @Test
    @DisplayName("Control: Kill switch activation and reset")
    void testKillSwitchActivationAndReset() {
        var activateResp = executionService.activateKillSwitch("user-a-uuid", "acct-a-uuid", "Market spike");
        assertTrue(activateResp.success());
        assertTrue(activateResp.killSwitchActive());
        verify(tradingAccountRepository, atLeastOnce()).save(accountA);

        var resetResp = executionService.resetKillSwitch("user-a-uuid", "acct-a-uuid");
        assertTrue(resetResp.success());
        assertFalse(resetResp.killSwitchActive());
    }

    @Test
    @DisplayName("Control: Algorithm enable and disable toggle")
    void testAlgoToggle() {
        var disableResp = executionService.setAlgoEnabled("user-a-uuid", "acct-a-uuid", false);
        assertTrue(disableResp.success());
        assertFalse(disableResp.algoEnabled());

        var enableResp = executionService.setAlgoEnabled("user-a-uuid", "acct-a-uuid", true);
        assertTrue(enableResp.success());
        assertTrue(enableResp.algoEnabled());
    }
}
