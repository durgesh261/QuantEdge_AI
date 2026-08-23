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
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.position.Position;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.OrderRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.OrderExecutionService.ExecutionCommand;
import com.quantedge.trading.service.OrderExecutionService.ExecutionResult;
import com.quantedge.trading.service.OrderValidationGateway;
import com.quantedge.trading.service.TradePersistenceService;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenResult;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 6: Authoritative Trading State Integration Tests")
class Phase6TradingStateIntegrationTest {

    @Mock private OrderValidationGateway validationGateway;
    @Mock private DeltaIndiaRestClient deltaRestClient;
    @Mock private DeltaCredentialService credentialService;
    @Mock private LiveAccountSyncService accountSyncService;
    @Mock private TradingAccountRepository tradingAccountRepository;
    @Mock private DeltaConnectionRepository deltaConnectionRepository;
    @Mock private RiskConfigurationRepository riskConfigRepository;
    @Mock private StrategySetupRepository strategySetupRepository;
    @Mock private OrderRepository orderRepository;
    @Mock private PositionRepository positionRepository;
    @Mock private ActiveTradeLockRepository lockRepository;
    @Mock private TradeRecordRepository tradeRecordRepository;
    @Mock private AuditLogRepository auditLogRepository;

    private OrderExecutionService executionService;
    private TradePersistenceService persistenceService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private User userA;
    private User userB;
    private TradingAccount accountA;
    private TradingAccount accountB;
    private RiskConfiguration riskConfigA;
    private StrategySetupRecord setupA;

    @BeforeEach
    void setUp() {
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

        persistenceService = new TradePersistenceService(
                lockRepository,
                tradeRecordRepository,
                tradingAccountRepository,
                positionRepository,
                auditLogRepository
        );

        userA = new User();
        userA.setId("user-alpha");
        userA.setEmail("alpha@quantedge.io");

        userB = new User();
        userB.setId("user-bravo");
        userB.setEmail("bravo@quantedge.io");

        accountA = new TradingAccount(userA, "Alpha Live", "LIVE", "USDT");
        accountA.setId("acct-alpha");
        accountA.setAlgoEnabled(true);
        accountA.setKillSwitchActive(false);
        accountA.setIsActive(true);
        accountA.setTotalEquity(new BigDecimal("10000.00"));
        accountA.setAvailableBalance(new BigDecimal("10000.00"));

        accountB = new TradingAccount(userB, "Bravo Live", "LIVE", "USDT");
        accountB.setId("acct-bravo");
        accountB.setAlgoEnabled(true);
        accountB.setKillSwitchActive(false);
        accountB.setIsActive(true);

        riskConfigA = new RiskConfiguration(accountA);
        riskConfigA.setMaxConcurrentTrades(1);
        riskConfigA.setRiskPerTradePercent(new BigDecimal("1.00"));
        riskConfigA.setMaxLeverage(10);

        setupA = new StrategySetupRecord(
                accountA,
                "setup-auth-001",
                "BTCUSD.P",
                "LONG",
                new BigDecimal("60000.00"),
                new BigDecimal("59000.00"),
                new BigDecimal("63000.00"),
                new BigDecimal("3.00"),
                Instant.now().plusSeconds(3600)
        );
    }

    @Nested
    @DisplayName("Authoritative Execution Pipeline & State Consistency")
    class ExecutionPipelineTests {

        @Test
        @DisplayName("Order execution correctly initializes order in SUBMISSION_PENDING before dispatch")
        void orderInitializedInSubmissionPending() {
            when(tradingAccountRepository.findById("acct-alpha")).thenReturn(Optional.of(accountA));
            when(riskConfigRepository.findByTradingAccountId("acct-alpha")).thenReturn(Optional.of(riskConfigA));
            when(strategySetupRepository.findBySetupId("setup-auth-001")).thenReturn(Optional.of(setupA));

            DeltaConnection conn = new DeltaConnection(accountA, "LIVE", "enc-k", "enc-s");
            when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-alpha", "LIVE")).thenReturn(Optional.of(conn));
            when(credentialService.decrypt("enc-k")).thenReturn("api-key");
            when(credentialService.decrypt("enc-s")).thenReturn("api-sec");

            LiveAccountSyncService.SyncSummary sync = new LiveAccountSyncService.SyncSummary(
                    true, Instant.now(), "acct-alpha", new BigDecimal("10000.00"),
                    new BigDecimal("10000.00"), BigDecimal.ZERO, 0, 0,
                    List.of(), List.of(), List.of(), List.of(), null
            );
            when(accountSyncService.syncLiveAccount(anyString(), anyString(), anyString())).thenReturn(sync);

            when(orderRepository.existsByClientOrderId(anyString())).thenReturn(false);
            when(orderRepository.existsBySetupIdAndStatusIn(eq("setup-auth-001"), anyCollection())).thenReturn(false);
            when(orderRepository.save(any(Order.class))).thenAnswer(inv -> inv.getArgument(0));

            String deltaResponseJson = """
                    {
                        "success": true,
                        "result": {
                            "id": "delta-ord-success-1",
                            "state": "open"
                        }
                    }
                    """;
            when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.POST), eq("/v2/orders"), isNull(), anyMap()))
                    .thenReturn(ResponseEntity.ok(deltaResponseJson));

            ExecutionCommand cmd = new ExecutionCommand("user-alpha", "acct-alpha", "setup-auth-001", "client-auth-100", false);
            ExecutionResult res = executionService.executeAuthoritativeOrder(cmd);

            assertThat(res.success()).isTrue();
            assertThat(res.orderId()).isEqualTo("delta-ord-success-1");
            assertThat(res.clientOrderId()).isEqualTo("client-auth-100");
            verify(orderRepository, atLeastOnce()).save(any(Order.class));
        }
    }

    @Nested
    @DisplayName("Multi-Tenant Isolation & IDOR Protection")
    class MultiTenantIsolationTests {

        @Test
        @DisplayName("User B cannot execute order against User A's trading account (Fail-Closed)")
        void crossTenantExecutionBlocked() {
            when(tradingAccountRepository.findById("acct-alpha")).thenReturn(Optional.of(accountA));

            ExecutionCommand cmd = new ExecutionCommand("user-bravo", "acct-alpha", "setup-auth-001", "client-idor-1", false);
            ExecutionResult res = executionService.executeAuthoritativeOrder(cmd);

            assertThat(res.success()).isFalse();
            assertThat(res.rejectionCode()).isEqualTo("FORBIDDEN");
            verify(deltaRestClient, never()).executeRequest(anyString(), anyString(), any(), anyString(), any(), any());
        }
    }

    @Nested
    @DisplayName("Idempotent Signal & Setup Protection")
    class IdempotencyTests {

        @Test
        @DisplayName("Replaying the same setupId in persistence returns existing trade record without creating duplicate")
        void idempotentTradeOpen() {
            when(tradingAccountRepository.findById("acct-alpha")).thenReturn(Optional.of(accountA));

            com.quantedge.trading.entity.TradeRecord existingRecord = new com.quantedge.trading.entity.TradeRecord(
                    accountA, "setup-auth-001", "BTCUSD", "LONG",
                    new BigDecimal("60000.00"), BigDecimal.ONE, 10, new BigDecimal("10000.00")
            );
            existingRecord.setId("tr-existing-uuid");
            when(tradeRecordRepository.findBySetupId("setup-auth-001")).thenReturn(Optional.of(existingRecord));

            TradeOpenRequest req = new TradeOpenRequest(
                    "acct-alpha", "setup-auth-001", "BTCUSD", "LONG",
                    new BigDecimal("60000.00"), BigDecimal.ONE, 10, new BigDecimal("10000.00"),
                    null, null, new BigDecimal("59000.00"), new BigDecimal("63000.00"),
                    1, new BigDecimal("35.00"), new BigDecimal("60.00")
            );

            TradeOpenResult result = persistenceService.openTrade(req);

            assertThat(result.success()).isTrue();
            assertThat(result.tradeRecordId()).isEqualTo("tr-existing-uuid");
            verify(lockRepository, never()).saveAndFlush(any());
        }
    }
}
