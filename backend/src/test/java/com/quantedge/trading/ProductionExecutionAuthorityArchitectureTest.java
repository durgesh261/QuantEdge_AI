package com.quantedge.trading;

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
import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.OrderValidationGateway;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@DisplayName("Phase 5.24 — Production Execution Authority, Zero-Bypass & Multi-Tenant Architecture Test")
class ProductionExecutionAuthorityArchitectureTest {

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
    private TradingAccount accountB;
    private RiskConfiguration riskConfigA;
    private DeltaConnection connectionA;
    private StrategySetupRecord setupA;

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

        accountA = new TradingAccount(userA, "Account A", "LIVE", "USDT");
        accountA.setId("acct-a-uuid");
        accountA.setIsActive(true);
        accountA.setKillSwitchActive(false);
        accountA.setAlgoEnabled(true);
        accountA.setTotalEquity(new BigDecimal("10000.00"));
        accountA.setAvailableBalance(new BigDecimal("9000.00"));

        accountB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        accountB.setId("acct-b-uuid");
        accountB.setIsActive(true);
        accountB.setKillSwitchActive(false);
        accountB.setAlgoEnabled(true);

        riskConfigA = new RiskConfiguration();
        riskConfigA.setTradingAccount(accountA);
        riskConfigA.setMaxLeverage(10);
        riskConfigA.setRiskPerTradePercent(new BigDecimal("1.0"));
        riskConfigA.setMaxConcurrentTrades(3);

        connectionA = new DeltaConnection();
        connectionA.setTradingAccount(accountA);
        connectionA.setEncryptedApiKey("enc_api_key_A");
        connectionA.setEncryptedApiSecret("enc_api_secret_A");

        setupA = new StrategySetupRecord();
        setupA.setSetupId("setup-A-100");
        setupA.setSymbol("BTCUSD");
        setupA.setDirection("LONG");
        setupA.setSetupState("TRADE_SETUP_READY");
        setupA.setEntryPrice(new BigDecimal("60000.00"));
        setupA.setStopLoss(new BigDecimal("59000.00"));
        setupA.setTakeProfit(new BigDecimal("62000.00"));
        setupA.setRiskDistance(new BigDecimal("1000.00"));
        setupA.setExpiresAt(Instant.now().plusSeconds(3600));

        when(tradingAccountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));
        when(tradingAccountRepository.findById("acct-b-uuid")).thenReturn(Optional.of(accountB));
        when(riskConfigRepository.findByTradingAccountId("acct-a-uuid")).thenReturn(Optional.of(riskConfigA));
        when(strategySetupRepository.findBySetupId("setup-A-100")).thenReturn(Optional.of(setupA));
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-a-uuid", "LIVE")).thenReturn(Optional.of(connectionA));
        when(deltaConnectionRepository.findByTradingAccountId("acct-a-uuid")).thenReturn(Optional.of(connectionA));
        when(credentialService.decrypt("enc_api_key_A")).thenReturn("valid_api_key");
        when(credentialService.decrypt("enc_api_secret_A")).thenReturn("valid_api_secret");

        when(accountSyncService.syncLiveAccount(eq("acct-a-uuid"), anyString(), anyString())).thenReturn(
                new LiveAccountSyncService.SyncSummary(
                        true,
                        Instant.now(),
                        "acct-a-uuid",
                        new BigDecimal("10000.00"),
                        new BigDecimal("9000.00"),
                        new BigDecimal("1000.00"),
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
            if (o.getId() == null) {
                o.setId("ord-rec-uuid");
            }
            return o;
        });
    }

    @Nested
    @DisplayName("1. Multi-Tenant Isolation & IDOR Protection")
    class MultiTenantIsolationTests {

        @Test
        @DisplayName("User A cannot execute orders against User B's TradingAccount")
        void testUserACannotExecuteUserBAccount() {
            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-b-uuid", // Account B belongs to User B
                    "setup-A-100",
                    "client-ord-idor-1",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("FORBIDDEN", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("Unauthenticated request with null userId is strictly blocked")
        void testNullUserIdBlocked() {
            var command = new OrderExecutionService.ExecutionCommand(
                    null,
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-anon",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("FORBIDDEN", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("User A cannot view User B's active orders or order history (throws SecurityException)")
        void testUserCannotViewOtherUserOrders() {
            assertThrows(SecurityException.class, () -> executionService.getActiveOrders("user-a-uuid", "acct-b-uuid"));
            assertThrows(SecurityException.class, () -> executionService.getOrderHistory("user-a-uuid", "acct-b-uuid"));
        }
    }

    @Nested
    @DisplayName("2. Execution Gates & Fail-Closed Safety")
    class ExecutionGateTests {

        @Test
        @DisplayName("Kill Switch Active completely blocks order dispatch")
        void testKillSwitchBlocksOrder() {
            accountA.setKillSwitchActive(true);

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-ks",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("KILL_SWITCH_ACTIVE", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("Algo Disabled completely blocks order dispatch")
        void testAlgoDisabledBlocksOrder() {
            accountA.setAlgoEnabled(false);

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-disabled",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("ALGO_DISABLED", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("Deactivated TradingAccount blocks order dispatch")
        void testInactiveAccountBlocksOrder() {
            accountA.setIsActive(false);

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-inactive",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("ACCOUNT_DISABLED", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("Invalid Short TP/SL Geometry fails closed (StopLoss must be > Entry)")
        void testInvalidShortGeometryFailsClosed() {
            StrategySetupRecord shortSetup = new StrategySetupRecord();
            shortSetup.setSetupId("setup-short-invalid");
            shortSetup.setSymbol("ETHUSD");
            shortSetup.setDirection("SHORT");
            shortSetup.setSetupState("TRADE_SETUP_READY");
            shortSetup.setEntryPrice(new BigDecimal("3000.00"));
            shortSetup.setStopLoss(new BigDecimal("2900.00")); // Inverted for SHORT: SL < Entry
            shortSetup.setTakeProfit(new BigDecimal("2800.00"));
            when(strategySetupRepository.findBySetupId("setup-short-invalid")).thenReturn(Optional.of(shortSetup));

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-short-invalid",
                    "client-ord-short-inv",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("INVALID_TP_SL_GEOMETRY", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }
    }

    @Nested
    @DisplayName("3. Idempotency & Concurrency Protection")
    class IdempotencyTests {

        @Test
        @DisplayName("Duplicate setup execution is rejected when order already exists in DB")
        void testDuplicateSetupIdInDbRejected() {
            when(orderRepository.existsBySetupIdAndStatusIn(eq("setup-A-100"), anyList())).thenReturn(true);

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-dup-1",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("DUPLICATE_SETUP_ID", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }

        @Test
        @DisplayName("Duplicate client_order_id is rejected when already in DB")
        void testDuplicateClientOrderIdInDbRejected() {
            when(orderRepository.existsByClientOrderId("client-ord-dup-2")).thenReturn(true);

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-dup-2",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals("DUPLICATE_CLIENT_ORDER_ID", result.rejectionCode());
            verifyNoInteractions(deltaRestClient);
        }
    }

    @Nested
    @DisplayName("4. Network Timeout & Exchange Reconciliation")
    class TimeoutReconciliationTests {

        @Test
        @DisplayName("Network timeout during POST /v2/orders queries Delta open orders to recover state without duplicate orders")
        void testNetworkTimeoutReconciliationFindsOpenOrder() {
            // First call (POST /v2/orders) throws connection timeout
            when(deltaRestClient.executeRequest(
                    eq("valid_api_key"),
                    eq("valid_api_secret"),
                    eq(HttpMethod.POST),
                    eq("/v2/orders"),
                    isNull(),
                    anyMap()
            )).thenThrow(new RuntimeException("Connection timeout to Delta India REST API"));

            // Reconciliation call (GET /v2/orders?state=open) returns the accepted order
            String mockOpenOrders = """
                    {
                        "success": true,
                        "result": [
                            {
                                "id": "delta-rec-9988",
                                "client_order_id": "client-ord-timeout-1",
                                "product_symbol": "BTCUSD",
                                "state": "open",
                                "size": 1,
                                "limit_price": "60000.00"
                            }
                        ]
                    }
                    """;

            when(deltaRestClient.executeRequest(
                    eq("valid_api_key"),
                    eq("valid_api_secret"),
                    eq(HttpMethod.GET),
                    eq("/v2/orders"),
                    eq("state=open"),
                    isNull()
            )).thenReturn(ResponseEntity.ok(mockOpenOrders));

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-timeout-1",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);

            // Reconciled successfully!
            assertTrue(result.success());
            assertEquals(OrderExecutionService.ExecutionState.SUBMITTED, result.state());
            assertEquals("delta-rec-9988", result.orderId());
            assertEquals("client-ord-timeout-1", result.clientOrderId());

            // Proves that exactly 1 POST /v2/orders was attempted and 1 GET /v2/orders reconciliation query
            verify(deltaRestClient, times(1)).executeRequest(
                    eq("valid_api_key"), eq("valid_api_secret"), eq(HttpMethod.POST), eq("/v2/orders"), isNull(), anyMap()
            );
            verify(deltaRestClient, times(1)).executeRequest(
                    eq("valid_api_key"), eq("valid_api_secret"), eq(HttpMethod.GET), eq("/v2/orders"), eq("state=open"), isNull()
            );
        }

        @Test
        @DisplayName("Network timeout where exchange NEVER received order marks state as UNCONFIRMED_TIMEOUT safely")
        void testNetworkTimeoutWhereOrderNotFound() {
            when(deltaRestClient.executeRequest(
                    eq("valid_api_key"),
                    eq("valid_api_secret"),
                    eq(HttpMethod.POST),
                    eq("/v2/orders"),
                    isNull(),
                    anyMap()
            )).thenThrow(new RuntimeException("Connection reset by peer"));

            String mockEmptyOpenOrders = """
                    {
                        "success": true,
                        "result": []
                    }
                    """;

            when(deltaRestClient.executeRequest(
                    eq("valid_api_key"),
                    eq("valid_api_secret"),
                    eq(HttpMethod.GET),
                    eq("/v2/orders"),
                    eq("state=open"),
                    isNull()
            )).thenReturn(ResponseEntity.ok(mockEmptyOpenOrders));

            var command = new OrderExecutionService.ExecutionCommand(
                    "user-a-uuid",
                    "acct-a-uuid",
                    "setup-A-100",
                    "client-ord-timeout-empty",
                    false
            );

            var result = executionService.executeAuthoritativeOrder(command);
            assertFalse(result.success());
            assertEquals(OrderExecutionService.ExecutionState.FAILED, result.state());
            assertEquals("SUBMISSION_TIMEOUT", result.rejectionCode());
        }
    }
}
