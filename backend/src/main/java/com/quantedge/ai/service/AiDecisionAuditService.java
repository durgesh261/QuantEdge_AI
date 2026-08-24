package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.entity.AiDecisionAudit;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.repository.AiDecisionAuditRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * Service for recording AI decision audit trail.
 * Every AI evaluation that influences trading decisions is recorded here for traceability.
 */
@Service
public class AiDecisionAuditService {

    private static final Logger log = LoggerFactory.getLogger(AiDecisionAuditService.class);

    private final AiDecisionAuditRepository auditRepository;

    public AiDecisionAuditService(AiDecisionAuditRepository auditRepository) {
        this.auditRepository = auditRepository;
    }

    /**
     * Records an AI decision audit entry.
     */
    @Transactional
    public AiDecisionAudit recordDecision(
            TradingAccount account,
            StrategySetupRecord setup,
            AiSignalEnrichment enrichment,
            String combinedDecision,
            String decisionReason,
            String riskDecision,
            String executionDecision,
            long inferenceLatencyMs
    ) {
        try {
            // Extract SMC data from setup
            String smcDirection = setup.getDirection();
            java.math.BigDecimal smcRiskReward = setup.getRiskReward() != null ? setup.getRiskReward() : java.math.BigDecimal.valueOf(2.0);
            java.math.BigDecimal smcConfidence = setup.getConfidence() != null ? setup.getConfidence() : java.math.BigDecimal.valueOf(0.75);
            String smcSetupState = setup.getSetupState();

            // Extract AI data from enrichment
            java.math.BigDecimal aiPatternScore = enrichment.getPatternScore();
            java.math.BigDecimal aiSignalScore = enrichment.getSignalScore();
            java.math.BigDecimal aiConfidence = enrichment.getConfidence();
            String aiMarketRegime = enrichment.getMarketRegime();
            String aiExplanation = enrichment.getMarketContext();
            String modelMetadata = enrichment.getModelMetadata();
            String featureSummary = enrichment.getFeatureSummary();

            // Extract supporting/risk factors from feature summary
            String supportingFactors = extractFactors(featureSummary, "supportingFactors");
            String riskFactors = extractFactors(featureSummary, "riskFactors");

            // Hash feature vector for integrity
            String featureVectorHash = hashFeatureVector(featureSummary);

            AiDecisionAudit audit = new AiDecisionAudit(
                    account,
                    setup.getSetupId(),
                    setup.getSymbol(),
                    setup.getDirection(),
                    enrichment.getIntelligenceVersion().split("-")[0], // model name
                    enrichment.getIntelligenceVersion(),
                    "1.0", // feature version
                    smcDirection,
                    smcRiskReward,
                    smcConfidence,
                    smcSetupState,
                    aiPatternScore,
                    aiSignalScore,
                    aiConfidence,
                    aiMarketRegime,
                    aiExplanation,
                    supportingFactors,
                    riskFactors,
                    combinedDecision,
                    decisionReason,
                    riskDecision,
                    executionDecision,
                    inferenceLatencyMs,
                    featureVectorHash,
                    Instant.now()
            );

            AiDecisionAudit saved = auditRepository.save(audit);
            log.info("Recorded AI decision audit for setup {}: decision={}, confidence={}", 
                    setup.getSetupId(), combinedDecision, aiConfidence);
            return saved;

        } catch (Exception e) {
            log.error("Failed to record AI decision audit for setup {}: {}", setup.getSetupId(), e.getMessage());
            // Don't throw - audit failure shouldn't block trading
            return null;
        }
    }

    /**
     * Determines the combined decision based on SMC validity, AI confidence, and risk state.
     */
    public String determineCombinedDecision(
            StrategySetupRecord setup,
            AiSignalEnrichment enrichment,
            String riskDecision
    ) {
        // SMC must be valid
        if (!"TRADE_SETUP_READY".equalsIgnoreCase(setup.getSetupState())) {
            return "REJECTED";
        }
        if (setup.getExpiresAt() != null && setup.getExpiresAt().isBefore(Instant.now())) {
            return "REJECTED";
        }

        // AI confidence threshold
        java.math.BigDecimal aiConfidence = enrichment.getConfidence();
        if (aiConfidence == null || aiConfidence.compareTo(java.math.BigDecimal.valueOf(30)) < 0) {
            return "BLOCKED_BY_AI_CONFIDENCE";
        }

        // Market regime check
        String regime = enrichment.getMarketRegime();
        if ("AI_UNAVAILABLE".equals(regime) || "INSUFFICIENT_DATA".equals(regime)) {
            return "BLOCKED_BY_SYSTEM";
        }
        if ("CONFLICTING_TIMEFRAMES".equals(regime)) {
            return "WATCH";
        }

        // Risk decision takes precedence
        if ("BLOCKED".equals(riskDecision) || "REJECTED".equals(riskDecision)) {
            return "BLOCKED_BY_RISK";
        }

        // High confidence + favorable regime = execution eligible
        if (aiConfidence.compareTo(java.math.BigDecimal.valueOf(70)) >= 0 && 
                (regime.contains("BULLISH") || regime.contains("BEARISH"))) {
            return "EXECUTION_ELIGIBLE";
        }

        // Moderate confidence = qualified but needs human review
        if (aiConfidence.compareTo(java.math.BigDecimal.valueOf(50)) >= 0) {
            return "QUALIFIED";
        }

        return "WATCH";
    }

    @Transactional(readOnly = true)
    public List<AiDecisionAudit> getDecisionHistory(String accountId, int limit) {
        return auditRepository.findByTradingAccountIdOrderByDecisionTimestampDesc(accountId)
                .stream().limit(limit > 0 ? limit : 100).toList();
    }

    @Transactional(readOnly = true)
    public List<AiDecisionAudit> getDecisionsForSetup(String setupId) {
        return auditRepository.findBySetupIdOrderByDecisionTimestampDesc(setupId);
    }

    @Transactional(readOnly = true)
    public List<AiDecisionAudit> getDecisionsBySymbol(String symbol, int limit) {
        return auditRepository.findBySymbolOrderByDecisionTimestampDesc(symbol)
                .stream().limit(limit > 0 ? limit : 100).toList();
    }

    @Transactional(readOnly = true)
    public List<AiDecisionAudit> getDecisionsByDecision(String decision, int limit) {
        return auditRepository.findByCombinedDecision(decision)
                .stream().limit(limit > 0 ? limit : 100).toList();
    }

    private String extractFactors(String featureSummary, String field) {
        if (featureSummary == null || !featureSummary.contains(field)) return "";
        try {
            int start = featureSummary.indexOf("\"" + field + "\":\"") + field.length() + 4;
            int end = featureSummary.indexOf("\"", start);
            if (start > field.length() + 3 && end > start) {
                return featureSummary.substring(start, end);
            }
        } catch (Exception e) {
            // Ignore
        }
        return "";
    }

    private String hashFeatureVector(String featureSummary) {
        if (featureSummary == null) return "";
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(featureSummary.getBytes());
            StringBuilder hex = new StringBuilder();
            for (byte b : hash) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString().substring(0, 16); // Short hash
        } catch (NoSuchAlgorithmException e) {
            return "";
        }
    }
}