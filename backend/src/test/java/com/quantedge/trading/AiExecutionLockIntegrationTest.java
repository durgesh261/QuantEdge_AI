package com.quantedge.trading;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.account.service.LiveAccountSyncService;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.service.AiDecisionAuditService;
import com.quantedge.ai.service.CombinedDecisionEngine;
import com.quantedge.ai.service.CombinedDecisionEngine.DecisionResult;
import com.quantedge.ai.service.CombinedDecisionEngine.DecisionState;
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
import com.quantedge.trading.service.OrderExecutionService.ExecutionCommand;
import com.quantedge.trading.service.OrderExecutionService.ExecutionResult;
import com.quantedge.trading.service.OrderValidationGateway;
import com.quantedge.trading.service.OrderValidationGateway.RejectionReasonCode;
import com.quantedge.trading.service.OrderValidationGateway.ValidationResult;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@DisplayName("Phase D — AI Governance, Execution Lock & Zero-Bypass Integration Tests")
class AiExecutionLockIntegrationTest {

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
    private CombinedDecisionEngine combinedDecisionEngine;
    private AiDecisionAuditService aiDecisionAuditService;

    private User user;
    private TradingAccount account;
    private RiskConfiguration riskConfig;
    private DeltaConnection connection;
    private StrategySetupRecord setup;

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
        aiDecisionAuditService = mock(AiDecisionAuditService.class);
        objectMapper = new ObjectMapper();

        combinedDecisionEngine = new CombinedDecisionEngine(aiDecisionAuditService, riskConfigRepository);

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

        user = new User();
        user.setId("user-uuid-1");
        user.setEmail("trader@quantedge.ai");

        account = new TradingAccount(user, "Primary Trading Account", "LIVE", "USDT");
        account.setId("acct-uuid-1");
        account.setIsActive(true);
        account.setKillSwitchActive(false);
        account.setAlgoEnabled(true);
        account.setTotalEquity(new BigDecimal("100000.00"));
        account.setAvailableBalance(new BigDecimal("80000.00"));

        riskConfig = new RiskConfiguration();
        riskConfig.setTradingAccount(account);
        riskConfig.setMaxLeverage(10);
        riskConfig.setRiskPerTradePercent(new BigDecimal("1.0"));
        riskConfig.setMaxConcurrentTrades(3);
        riskConfig.setKillSwitchActive(false);

        connection = new DeltaConnection();
        connection.setTradingAccount(account);
        connection.setEncryptedApiKey("enc_api_key");
        connection.setEncryptedApiSecret("enc_api_secret");

        setup = new StrategySetupRecord();
        setup.setSetupId("setup-btc-100");
        setup.setSymbol("BTCUSD");
        setup.setDirection("LONG");
        setup.setSetupState("TRADE_SETUP_READY");
        setup.setEntryPrice(new BigDecimal("65000.00"));
        setup.setStopLoss(new BigDecimal("64500.00"));
        setup.setTakeProfit(new BigDecimal("66500.00"));
        setup.setRiskDistance(new BigDecimal("500.00"));
        setup.setExpiresAt(Instant.now().plusSeconds(3600));

        when(tradingAccountRepository.findById("acct-uuid-1")).thenReturn(Optional.of(account));
        when(riskConfigRepository.findByTradingAccountId("acct-uuid-1")).thenReturn(Optional.of(riskConfig));
        when(strategySetupRepository.findBySetupId("setup-btc-100")).thenReturn(Optional.of(setup));
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-uuid-1", "LIVE")).thenReturn(Optional.of(connection));
        when(deltaConnectionRepository.findByTradingAccountId("acct-uuid-1")).thenReturn(Optional.of(connection));
        when(credentialService.decrypt("enc_api_key")).thenReturn("valid_key");
        when(credentialService.decrypt("enc_api_secret")).thenReturn("valid_secret");

        when(accountSyncService.syncLiveAccount(eq("acct-uuid-1"), anyString(), anyString())).thenReturn(
                new LiveAccountSyncService.SyncSummary(
                        true,
                        Instant.now(),
                        "acct-uuid-1",
                        new BigDecimal("100000.00"),
                        new BigDecimal("80000.00"),
                        new BigDecimal("20000.00"),
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
                o.setId("ord-uuid-123");
            }
            return o;
        });
    }

    @Test
    @DisplayName("Gate 1: AI_UNAVAILABLE regime strictly returns BLOCKED_BY_SYSTEM decision")
    void testAiUnavailableBlocksDecision() {
        AiSignalEnrichment unavailEnrichment = new AiSignalEnrichment();
        unavailEnrichment.setSymbol("BTCUSD");
        unavailEnrichment.setMarketRegime("AI_UNAVAILABLE");
        unavailEnrichment.setMarketContext("Model unverified or missing");
        unavailEnrichment.setConfidence(BigDecimal.ZERO);
        unavailEnrichment.setPatternScore(BigDecimal.ZERO);
        unavailEnrichment.setSignalScore(BigDecimal.ZERO);

        DecisionResult result = combinedDecisionEngine.evaluate(
                account,
                setup,
                unavailEnrichment,
                riskConfig,
                false, // killSwitchActive
                true   // algoEnabled
        );

        assertEquals(DecisionState.BLOCKED_BY_SYSTEM, result.decision());
        assertEquals("AI_UNAVAILABLE", result.riskDetail());
    }

    @Test
    @DisplayName("Gate 2: AI_PROMOTION_REJECTED regime strictly returns BLOCKED_BY_SYSTEM decision")
    void testAiPromotionRejectedBlocksDecision() {
        AiSignalEnrichment rejectedEnrichment = new AiSignalEnrichment();
        rejectedEnrichment.setSymbol("BTCUSD");
        rejectedEnrichment.setMarketRegime("AI_PROMOTION_REJECTED");
        rejectedEnrichment.setMarketContext("AI model failed out-of-sample promotion gate");
        rejectedEnrichment.setConfidence(new BigDecimal("85.0"));
        rejectedEnrichment.setPatternScore(new BigDecimal("0.85"));
        rejectedEnrichment.setSignalScore(new BigDecimal("0.85"));

        DecisionResult result = combinedDecisionEngine.evaluate(
                account,
                setup,
                rejectedEnrichment,
                riskConfig,
                false,
                true
        );

        assertEquals(DecisionState.BLOCKED_BY_SYSTEM, result.decision());
    }

    @Test
    @DisplayName("Gate 3: Kill Switch Active strictly blocks execution with zero Delta Exchange API calls")
    void testKillSwitchBlocksOrderExecutionZeroApiCalls() {
        account.setKillSwitchActive(true);

        ExecutionCommand command = new ExecutionCommand(
                "user-uuid-1",
                "acct-uuid-1",
                "setup-btc-100",
                "client-ord-kill-1",
                false
        );

        ExecutionResult result = executionService.executeAuthoritativeOrder(command);

        assertFalse(result.success(), "Order must fail when kill switch is active");
        assertEquals("KILL_SWITCH_ACTIVE", result.rejectionCode());

        // MANDATORY VERIFICATION: Zero interactions with Delta Exchange India REST Client
        verifyNoInteractions(deltaRestClient);
    }

    @Test
    @DisplayName("Gate 4: Strategy setup not ready or rejected strictly halts order with zero Delta Exchange API calls")
    void testUnreadySetupBlocksOrderWithZeroApiCalls() {
        setup.setSetupState("AI_REJECTED");

        ExecutionCommand command = new ExecutionCommand(
                "user-uuid-1",
                "acct-uuid-1",
                "setup-btc-100",
                "client-ord-fail-1",
                false
        );

        ExecutionResult result = executionService.executeAuthoritativeOrder(command);

        assertFalse(result.success(), "Order execution must fail when setup state is not TRADE_SETUP_READY");
        assertEquals("DECISION_NOT_READY", result.rejectionCode());

        // MANDATORY VERIFICATION: Zero calls to exchange REST API
        verifyNoInteractions(deltaRestClient);
    }

}
