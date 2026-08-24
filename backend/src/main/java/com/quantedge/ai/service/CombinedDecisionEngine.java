package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.risk.entity.RiskConfiguration;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Combined SMC + AI Decision Engine.
 * 
 * Architecture:
 * SMC Engine → Qualified Setup → AI Evaluation → Combined Decision → Risk Engine → Execution Authority
 * 
 * The AI Engine must NEVER modify SMC structure. It only provides additive intelligence.
 * Risk Engine remains authoritative for execution authorization.
 */
@Service
public class CombinedDecisionEngine {

    private static final Logger log = LoggerFactory.getLogger(CombinedDecisionEngine.class);

    private final AiDecisionAuditService auditService;
    private final RiskConfigurationRepository riskConfigRepository;

    public CombinedDecisionEngine(AiDecisionAuditService auditService, RiskConfigurationRepository riskConfigRepository) {
        this.auditService = auditService;
        this.riskConfigRepository = riskConfigRepository;
    }

    /**
     * Decision states for the combined SMC + AI engine.
     */
    public enum DecisionState {
        REJECTED,                    // SMC invalid or expired
        WATCH,                       // Low AI confidence, monitoring
        QUALIFIED,                   // SMC valid, AI moderate confidence
        EXECUTION_ELIGIBLE,          // SMC valid, AI high confidence, risk OK
        BLOCKED_BY_RISK,             // Risk engine rejection
        BLOCKED_BY_SYSTEM,           // AI unavailable, kill switch, etc.
        BLOCKED_BY_AI_CONFIDENCE,    // AI confidence below threshold
        BLOCKED_BY_MARKET            // Market conditions unfavorable
    }

    /**
     * Evaluates a qualified SMC setup through the complete decision pipeline.
     */
    @Transactional
    public DecisionResult evaluate(
            TradingAccount account,
            StrategySetupRecord setup,
            AiSignalEnrichment enrichment,
            RiskConfiguration riskConfig,
            boolean killSwitchActive,
            boolean algoEnabled
    ) {
        long startTime = System.currentTimeMillis();

        // 1. SMC Validation (deterministic engine authority)
        if (!"TRADE_SETUP_READY".equalsIgnoreCase(setup.getSetupState())) {
            return recordAndReturn(DecisionState.REJECTED, "SMC setup not in TRADE_SETUP_READY state", 
                    setup, enrichment, riskConfig, startTime, "SMC_INVALID");
        }
        if (setup.getExpiresAt() != null && setup.getExpiresAt().isBefore(Instant.now())) {
            return recordAndReturn(DecisionState.REJECTED, "SMC setup expired", 
                    setup, enrichment, riskConfig, startTime, "SMC_EXPIRED");
        }

        // 2. System State Checks
        if (killSwitchActive) {
            return recordAndReturn(DecisionState.BLOCKED_BY_SYSTEM, "Emergency kill switch active", 
                    setup, enrichment, riskConfig, startTime, "KILL_SWITCH");
        }
        if (!algoEnabled) {
            return recordAndReturn(DecisionState.BLOCKED_BY_SYSTEM, "Algorithmic trading disabled", 
                    setup, enrichment, riskConfig, startTime, "ALGO_DISABLED");
        }

        // 3. AI Evaluation
        String aiRegime = enrichment.getMarketRegime();
        BigDecimal aiConfidence = enrichment.getConfidence();
        BigDecimal aiPatternScore = enrichment.getPatternScore();
        BigDecimal aiSignalScore = enrichment.getSignalScore();

        if ("AI_UNAVAILABLE".equals(aiRegime) || "INSUFFICIENT_DATA".equals(aiRegime) || "AI_PROMOTION_REJECTED".equals(aiRegime)) {
            return recordAndReturn(DecisionState.BLOCKED_BY_SYSTEM, "AI intelligence unavailable or promotion rejected: " + enrichment.getMarketContext(), 
                    setup, enrichment, riskConfig, startTime, "AI_UNAVAILABLE");
        }


        // 4. AI Confidence Threshold
        if (aiConfidence == null || aiConfidence.compareTo(BigDecimal.valueOf(30)) < 0) {
            return recordAndReturn(DecisionState.BLOCKED_BY_AI_CONFIDENCE, "AI confidence below minimum threshold (30%)", 
                    setup, enrichment, riskConfig, startTime, "LOW_AI_CONFIDENCE");
        }

        // 5. Market Regime Assessment
        if ("CONFLICTING_TIMEFRAMES".equals(aiRegime)) {
            return recordAndReturn(DecisionState.WATCH, "Conflicting multi-timeframe signals, monitoring", 
                    setup, enrichment, riskConfig, startTime, "REGIME_CONFLICT");
        }

        // 6. Risk Engine Evaluation
        String riskDecision = evaluateRisk(account, setup, enrichment, riskConfig);
        if ("BLOCKED".equals(riskDecision) || "REJECTED".equals(riskDecision)) {
            return recordAndReturn(DecisionState.BLOCKED_BY_RISK, "Risk engine rejection: " + riskDecision, 
                    setup, enrichment, riskConfig, startTime, "RISK_REJECTION");
        }

        // 7. Final Combined Decision
        DecisionState finalDecision;
        String reason;

        if (aiConfidence.compareTo(BigDecimal.valueOf(70)) >= 0 && 
                (aiRegime.contains("BULLISH") || aiRegime.contains("BEARISH"))) {
            finalDecision = DecisionState.EXECUTION_ELIGIBLE;
            reason = "High AI confidence (" + aiConfidence + "%) with favorable regime (" + aiRegime + ")";
        } else if (aiConfidence.compareTo(BigDecimal.valueOf(50)) >= 0) {
            finalDecision = DecisionState.QUALIFIED;
            reason = "Moderate AI confidence (" + aiConfidence + "%), qualified for review";
        } else {
            finalDecision = DecisionState.WATCH;
            reason = "Low AI confidence (" + aiConfidence + "%), monitoring";
        }

        return recordAndReturn(finalDecision, reason, setup, enrichment, riskConfig, startTime, null);
    }

    private String evaluateRisk(TradingAccount account, StrategySetupRecord setup, AiSignalEnrichment enrichment, RiskConfiguration riskConfig) {
        // Risk checks that can block execution
        if (account.getTotalEquity() == null || account.getTotalEquity().compareTo(BigDecimal.ZERO) <= 0) {
            return "INSUFFICIENT_EQUITY";
        }
        if (account.getAvailableBalance() == null || account.getAvailableBalance().compareTo(BigDecimal.ZERO) <= 0) {
            return "NO_AVAILABLE_MARGIN";
        }
        if (riskConfig.getMaxConcurrentTrades() > 0) {
            // Would check active positions count here
        }
        if (setup.getRiskReward() != null && setup.getRiskReward().compareTo(BigDecimal.valueOf(1.5)) < 0) {
            return "INSUFFICIENT_RISK_REWARD";
        }
        return "PASSED";
    }

    private DecisionResult recordAndReturn(
            DecisionState decision,
            String reason,
            StrategySetupRecord setup,
            AiSignalEnrichment enrichment,
            RiskConfiguration riskConfig,
            long startTime,
            String riskDetail
    ) {
        long latencyMs = System.currentTimeMillis() - startTime;
        
        // Record audit trail (non-blocking)
        try {
            auditService.recordDecision(
                    setup.getTradingAccount(),
                    setup,
                    enrichment,
                    decision.name(),
                    reason,
                    riskDetail != null ? "BLOCKED" : "PASSED",
                    decision == DecisionState.EXECUTION_ELIGIBLE ? "AUTHORIZED" : "BLOCKED",
                    latencyMs
            );
        } catch (Exception e) {
            log.warn("Failed to record decision audit: {}", e.getMessage());
        }

        log.info("Combined decision for setup {}: {} - {}", setup.getSetupId(), decision, reason);
        return new DecisionResult(decision, reason, riskDetail, latencyMs);
    }

    public record DecisionResult(
            DecisionState decision,
            String reason,
            String riskDetail,
            long latencyMs
    ) {}
}