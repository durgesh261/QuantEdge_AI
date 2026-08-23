package com.quantedge.ai.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Authoritative AI Signal Enrichment JPA Entity.
 * Linked to a deterministic strategy setup by {@code setup_id}.
 */
@Entity
@Table(name = "ai_signal_enrichments", indexes = {
        @Index(name = "idx_ai_enrichment_setup_id", columnList = "setup_id"),
        @Index(name = "idx_ai_enrichment_account", columnList = "trading_account_id"),
        @Index(name = "idx_ai_enrichment_generated_at", columnList = "generated_at")
})
public class AiSignalEnrichment extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", nullable = false, length = 100)
    private String setupId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    @Column(name = "direction", nullable = false, length = 10)
    private String direction;

    @Column(name = "intelligence_version", nullable = false, length = 30)
    private String intelligenceVersion = "1.0.0-baseline";

    @Column(name = "pattern_score", precision = 5, scale = 2, nullable = false)
    private BigDecimal patternScore;

    @Column(name = "signal_score", precision = 5, scale = 2, nullable = false)
    private BigDecimal signalScore;

    @Column(name = "confidence", precision = 5, scale = 2, nullable = false)
    private BigDecimal confidence;

    @Column(name = "market_regime", nullable = false, length = 50)
    private String marketRegime;

    @Column(name = "market_context", nullable = false, length = 100)
    private String marketContext;

    @Column(name = "model_metadata", columnDefinition = "TEXT")
    private String modelMetadata;

    @Column(name = "feature_summary", columnDefinition = "TEXT")
    private String featureSummary;

    @Column(name = "generated_at", nullable = false)
    private Instant generatedAt;

    public AiSignalEnrichment() {}

    public AiSignalEnrichment(
            TradingAccount tradingAccount,
            String setupId,
            String symbol,
            String direction,
            String intelligenceVersion,
            BigDecimal patternScore,
            BigDecimal signalScore,
            BigDecimal confidence,
            String marketRegime,
            String marketContext,
            String modelMetadata,
            String featureSummary,
            Instant generatedAt
    ) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.symbol = symbol;
        this.direction = direction;
        this.intelligenceVersion = intelligenceVersion;
        this.patternScore = patternScore;
        this.signalScore = signalScore;
        this.confidence = confidence;
        this.marketRegime = marketRegime;
        this.marketContext = marketContext;
        this.modelMetadata = modelMetadata;
        this.featureSummary = featureSummary;
        this.generatedAt = generatedAt;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String setupId) { this.setupId = setupId; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }

    public String getDirection() { return direction; }
    public void setDirection(String direction) { this.direction = direction; }

    public String getIntelligenceVersion() { return intelligenceVersion; }
    public void setIntelligenceVersion(String intelligenceVersion) { this.intelligenceVersion = intelligenceVersion; }

    public BigDecimal getPatternScore() { return patternScore; }
    public void setPatternScore(BigDecimal patternScore) { this.patternScore = patternScore; }

    public BigDecimal getSignalScore() { return signalScore; }
    public void setSignalScore(BigDecimal signalScore) { this.signalScore = signalScore; }

    public BigDecimal getConfidence() { return confidence; }
    public void setConfidence(BigDecimal confidence) { this.confidence = confidence; }

    public String getMarketRegime() { return marketRegime; }
    public void setMarketRegime(String marketRegime) { this.marketRegime = marketRegime; }

    public String getMarketContext() { return marketContext; }
    public void setMarketContext(String marketContext) { this.marketContext = marketContext; }

    public String getModelMetadata() { return modelMetadata; }
    public void setModelMetadata(String modelMetadata) { this.modelMetadata = modelMetadata; }

    public String getFeatureSummary() { return featureSummary; }
    public void setFeatureSummary(String featureSummary) { this.featureSummary = featureSummary; }

    public Instant getGeneratedAt() { return generatedAt; }
    public void setGeneratedAt(Instant generatedAt) { this.generatedAt = generatedAt; }
}
