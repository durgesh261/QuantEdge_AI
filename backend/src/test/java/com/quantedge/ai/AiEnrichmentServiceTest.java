package com.quantedge.ai;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.repository.AiSignalEnrichmentRepository;
import com.quantedge.ai.service.AiDecisionAuditService;
import com.quantedge.ai.service.AiEnrichmentService;
import com.quantedge.ai.service.CombinedDecisionEngine;
import com.quantedge.ai.service.DeterministicBaselineIntelligenceEngine;
import com.quantedge.auth.entity.User;
import com.quantedge.risk.entity.RiskConfiguration;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.access.AccessDeniedException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 7.5: AiEnrichmentService Tests")
class AiEnrichmentServiceTest {

    @Mock private AiSignalEnrichmentRepository enrichmentRepository;
    @Mock private TradingAccountRepository accountRepository;
    @Mock private StrategySetupRepository setupRepository;
    @Mock private RiskConfigurationRepository riskConfigRepository;
    @Mock private AiDecisionAuditService auditService;
    @Mock private CombinedDecisionEngine decisionEngine;

    private DeterministicBaselineIntelligenceEngine baselineEngine;
    private AiEnrichmentService enrichmentService;

    private User userA;
    private User userB;
    private TradingAccount accountA;
    private TradingAccount accountB;
    private StrategySetupRecord setupRecord;
    private RiskConfiguration riskConfig;

    @BeforeEach
    void setUp() {
        baselineEngine = new DeterministicBaselineIntelligenceEngine();
        
        enrichmentService = new AiEnrichmentService(
                enrichmentRepository,
                accountRepository,
                setupRepository,
                riskConfigRepository,
                baselineEngine,
                auditService,
                decisionEngine
        );

        userA = new User();
        userA.setId("user-a-123");
        userA.setEmail("user.a@quantedge.io");

        userB = new User();
        userB.setId("user-b-456");
        userB.setEmail("user.b@quantedge.io");

        accountA = new TradingAccount(userA, "Account A", "LIVE", "USDT");
        accountA.setId("acct-a-uuid");
        accountA.setIsActive(true);

        accountB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        accountB.setId("acct-b-uuid");
        accountB.setIsActive(true);

        setupRecord = new StrategySetupRecord(
                accountA,
                "setup-det-100",
                "BTCUSD",
                "LONG",
                new BigDecimal("60000.00"),
                new BigDecimal("59000.00"),
                new BigDecimal("63000.00"),
                new BigDecimal("3.00"),
                Instant.now().plusSeconds(3600)
        );
        setupRecord.setConfidence(new BigDecimal("80.00"));

        riskConfig = new RiskConfiguration(accountA);
    }

    @Nested
    @DisplayName("Deterministic Intelligence Evaluation & Invariance")
    class EvaluationTests {

        @Test
        @DisplayName("Evaluates setup and produces valid bounded scores without mutating SMC setup")
        void evaluatesSetupCorrectly() {
            BigDecimal origEntry = setupRecord.getEntryPrice();
            BigDecimal origSl = setupRecord.getStopLoss();
            BigDecimal origTp = setupRecord.getTakeProfit();
            BigDecimal origRr = setupRecord.getRiskReward();
            String origDir = setupRecord.getDirection();
            String origId = setupRecord.getSetupId();

            when(enrichmentRepository.save(any(AiSignalEnrichment.class)))
                    .thenAnswer(invocation -> invocation.getArgument(0));

            AiEnrichmentDto dto = enrichmentService.enrichAndSave(accountA, setupRecord);

            // Verify scores are bounded [0.00, 100.00]
            assertThat(dto).isNotNull();
            assertThat(dto.setupId()).isEqualTo("setup-det-100");
            assertThat(dto.patternScore()).isBetween(BigDecimal.ZERO, BigDecimal.valueOf(100.00));
            assertThat(dto.signalScore()).isBetween(BigDecimal.ZERO, BigDecimal.valueOf(100.00));
            assertThat(dto.confidence()).isBetween(BigDecimal.ZERO, BigDecimal.valueOf(100.00));
            assertThat(dto.marketRegime()).isEqualTo("BULLISH_TRENDING");

            // Verify SMC setup parameters are 100% unchanged
            assertThat(setupRecord.getEntryPrice()).isEqualTo(origEntry);
            assertThat(setupRecord.getStopLoss()).isEqualTo(origSl);
            assertThat(setupRecord.getTakeProfit()).isEqualTo(origTp);
            assertThat(setupRecord.getRiskReward()).isEqualTo(origRr);
            assertThat(setupRecord.getDirection()).isEqualTo(origDir);
            assertThat(setupRecord.getSetupId()).isEqualTo(origId);
        }
    }

    @Nested
    @DisplayName("Tenant Isolation & IDOR Protection")
    class TenantIsolationTests {

        @Test
        @DisplayName("User A querying User B's account throws AccessDeniedException (403)")
        void crossTenantQueryBlocked() {
            when(accountRepository.findById("acct-b-uuid")).thenReturn(Optional.of(accountB));

            assertThatThrownBy(() -> enrichmentService.getEnrichmentBySetupId(userA, "setup-det-100", "acct-b-uuid"))
                    .isInstanceOf(AccessDeniedException.class)
                    .hasMessageContaining("Access denied: You do not own trading account acct-b-uuid");
        }

        @Test
        @DisplayName("Unauthenticated user throws AccessDeniedException")
        void unauthenticatedQueryBlocked() {
            assertThatThrownBy(() -> enrichmentService.getEnrichmentBySetupId(null, "setup-det-100", "acct-a-uuid"))
                    .isInstanceOf(AccessDeniedException.class);
        }
    }
}
