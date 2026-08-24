package com.quantedge.ai.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Audit trail for AI-driven trading decisions.
 * Every AI evaluation that influences trading decisions is recorded here.
 */
@Entity
@Table(name = "ai_decision_audits", indexes = {
        @Index(name = "idx_ai_decision_setup_id", columnList = "setup_id"),
        @Index(name = "idx_ai_decision_account", columnList = "trading_account_id"),
        @Index(name = "idx_ai_decision_timestamp", columnList = "decision_timestamp"),
        @Index(name = "idx_ai_decision_combined", columnList = "combined_decision")
})
public class AiDecisionAudit extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", nullable = false, length = 100)
    private String setupId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "direction", nullable = false, length = 10)
    private String direction;

    @Column(name = "model_name", nullable = false, length = 50)
    private String modelName;

    @Column(name = "model_version", nullable = false, length = 30)
    private String modelVersion;

    @Column(name = "feature_version", nullable = false, length = 20)
    private String featureVersion;

    // SMC Input
    @Column(name = "smc_direction", length = 10)
    private String smcDirection;

    @Column(name = "smc_risk_reward", precision = 10, scale = 4)
    private BigDecimal smcRiskReward;

    @Column(name = "smc_confidence", precision = 10, scale = 4)
    private BigDecimal smcConfidence;

    @Column(name = "smc_setup_state", length = 30)
    private String smcSetupState;

    // AI Output
    @Column(name = "ai_pattern_score", precision = 10, scale = 4)
    private BigDecimal aiPatternScore;

    @Column(name = "ai_signal_score", precision = 10, scale = 4)
    private BigDecimal aiSignalScore;

    @Column(name = "ai_confidence", precision = 10, scale = 4)
    private BigDecimal aiConfidence;

    @Column(name = "ai_market_regime", length = 50)
    private String aiMarketRegime;

    @Column(name = "ai_explanation", columnDefinition = "TEXT")
    private String aiExplanation;

    @Column(name = "supporting_factors", columnDefinition = "TEXT")
    private String supportingFactors;

    @Column(name = "risk_factors", columnDefinition = "TEXT")
    private String riskFactors;

    // Combined Decision
    @Column(name = "combined_decision", nullable = false, length = 30)
    private String combinedDecision; // REJECTED, WATCH, QUALIFIED, EXECUTION_ELIGIBLE, BLOCKED_BY_RISK, BLOCKED_BY_AI_CONFIDENCE, BLOCKED_BY_SYSTEM, BLOCKED_BY_MARKET

    @Column(name = "decision_reason", columnDefinition = "TEXT")
    private String decisionReason;

    // Risk Engine Decision
    @Column(name = "risk_decision", length = 30)
    private String riskDecision;

    // Execution Decision
    @Column(name = "execution_decision", length = 30)
    private String executionDecision;

    // Metadata
    @Column(name = "inference_latency_ms")
    private Long inferenceLatencyMs;

    @Column(name = "feature_vector_hash", length = 64)
    private String featureVectorHash;

    @Column(name = "decision_timestamp", nullable = false)
    private Instant decisionTimestamp;

    public AiDecisionAudit() {}

    public AiDecisionAudit(
            TradingAccount tradingAccount,
            String setupId,
            String symbol,
            String direction,
            String modelName,
            String modelVersion,
            String featureVersion,
            String smcDirection,
            BigDecimal smcRiskReward,
            BigDecimal smcConfidence,
            String smcSetupState,
            BigDecimal aiPatternScore,
            BigDecimal aiSignalScore,
            BigDecimal aiConfidence,
            String aiMarketRegime,
            String aiExplanation,
            String supportingFactors,
            String riskFactors,
            String combinedDecision,
            String decisionReason,
            String riskDecision,
            String executionDecision,
            Long inferenceLatencyMs,
            String featureVectorHash,
            Instant decisionTimestamp
    ) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.symbol = symbol;
        this.direction = direction;
        this.modelName = modelName;
        this.modelVersion = modelVersion;
        this.featureVersion = featureVersion;
        this.smcDirection = smcDirection;
        this.smcRiskReward = smcRiskReward;
        this.smcConfidence = smcConfidence;
        this.smcSetupState = smcSetupState;
        this.aiPatternScore = aiPatternScore;
        this.aiSignalScore = aiSignalScore;
        this.aiConfidence = aiConfidence;
        this.aiMarketRegime = aiMarketRegime;
        this.aiExplanation = aiExplanation;
        this.supportingFactors = supportingFactors;
        this.riskFactors = riskFactors;
        this.combinedDecision = combinedDecision;
        this.decisionReason = decisionReason;
        this.riskDecision = riskDecision;
        this.executionDecision = executionDecision;
        this.inferenceLatencyMs = inferenceLatencyMs;
        this.featureVectorHash = featureVectorHash;
        this.decisionTimestamp = decisionTimestamp;
    }

    // Getters and Setters
    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String setupId) { this.setupId = setupId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getDirection() { return direction; }
    public void setDirection(String direction) { this.direction = direction; }

    public String getModelName() { return modelName; }
    public void setModelName(String modelName) { this.modelName = modelName; }

    public String getModelVersion() { return modelVersion; }
    public void setModelVersion(String modelVersion) { this.modelVersion = modelVersion; }

    public String getFeatureVersion() { return featureVersion; }
    public void setFeatureVersion(String featureVersion) { this.featureVersion = featureVersion; }

    public String getSmcDirection() { return smcDirection; }
    public void setSmcDirection(String smcDirection) { this.smcDirection = smcDirection; }

    public BigDecimal getSmcRiskReward() { return smcRiskReward; }
    public void setSmcRiskReward(BigDecimal smcRiskReward) { this.smcRiskReward = smcRiskReward; }

    public BigDecimal getSmcConfidence() { return smcConfidence; }
    public void setSmcConfidence(BigDecimal smcConfidence) { this.smcConfidence = smcConfidence; }

    public String getSmcSetupState() { return smcSetupState; }
    public void setSmcSetupState(String smcSetupState) { this.smcSetupState = smcSetupState; }

    public BigDecimal getAiPatternScore() { return aiPatternScore; }
    public void setAiPatternScore(BigDecimal aiPatternScore) { this.aiPatternScore = aiPatternScore; }

    public BigDecimal getAiSignalScore() { return aiSignalScore; }
    public void setAiSignalScore(BigDecimal aiSignalScore) { this.aiSignalScore = aiSignalScore; }

    public BigDecimal getAiConfidence() { return aiConfidence; }
    public void setAiConfidence(BigDecimal aiConfidence) { this.aiConfidence = aiConfidence; }

    public String getAiMarketRegime() { return aiMarketRegime; }
    public void setAiMarketRegime(String aiMarketRegime) { this.aiMarketRegime = aiMarketRegime; }

    public String getAiExplanation() { return aiExplanation; }
    public void setAiExplanation(String aiExplanation) { this.aiExplanation = aiExplanation; }

    public String getSupportingFactors() { return supportingFactors; }
    public void setSupportingFactors(String supportingFactors) { this.supportingFactors = supportingFactors; }

    public String getRiskFactors() { return riskFactors; }
    public void setRiskFactors(String riskFactors) { this.riskFactors = riskFactors; }

    public String getCombinedDecision() { return combinedDecision; }
    public void setCombinedDecision(String combinedDecision) { this.combinedDecision = combinedDecision; }

    public String getDecisionReason() { return decisionReason; }
    public void setDecisionReason(String decisionReason) { this.decisionReason = decisionReason; }

    public String getRiskDecision() { return riskDecision; }
    public void setRiskDecision(String riskDecision) { this.riskDecision = riskDecision; }

    public String getExecutionDecision() { return executionDecision; }
    public void setExecutionDecision(String executionDecision) { this.executionDecision = executionDecision; }

    public Long getInferenceLatencyMs() { return inferenceLatencyMs; }
    public void setInferenceLatencyMs(Long inferenceLatencyMs) { this.inferenceLatencyMs = inferenceLatencyMs; }

    public String getFeatureVectorHash() { return featureVectorHash; }
    public void setFeatureVectorHash(String featureVectorHash) { this.featureVectorHash = featureVectorHash; }

    public Instant getDecisionTimestamp() { return decisionTimestamp; }
    public void setDecisionTimestamp(Instant decisionTimestamp) { this.decisionTimestamp = decisionTimestamp; }
}