package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.strategy.entity.StrategySetupRecord;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;

/**
 * Deterministic baseline rule-calibrated AI Intelligence Engine.
 * Requires zero external LLMs or cloud API keys.
 */
@Service
public class DeterministicBaselineIntelligenceEngine implements AiIntelligenceEngine {

    private static final String VERSION = "1.0.0-baseline";

    @Override
    public String getVersion() {
        return VERSION;
    }

    @Override
    public AiSignalEnrichment evaluate(TradingAccount account, StrategySetupRecord setup) {
        if (setup == null) {
            throw new IllegalArgumentException("StrategySetupRecord cannot be null");
        }

        // 1. Risk-Reward efficiency factor (normalized to 0.0 - 1.0)
        BigDecimal rr = setup.getRiskReward() != null ? setup.getRiskReward() : BigDecimal.valueOf(2.0);
        BigDecimal rrFactor = rr.divide(BigDecimal.valueOf(3.0), 4, RoundingMode.HALF_UP).min(BigDecimal.ONE).max(BigDecimal.ZERO);

        // 2. Pattern Score (0.00 - 100.00)
        BigDecimal rawPattern = BigDecimal.valueOf(60.0).add(rrFactor.multiply(BigDecimal.valueOf(30.0)));
        BigDecimal patternScore = rawPattern.min(BigDecimal.valueOf(100.00)).max(BigDecimal.ZERO).setScale(2, RoundingMode.HALF_UP);

        // 3. Signal Score (0.00 - 100.00)
        BigDecimal rawSignal = patternScore.multiply(BigDecimal.valueOf(0.70)).add(rrFactor.multiply(BigDecimal.valueOf(30.0)));
        BigDecimal signalScore = rawSignal.min(BigDecimal.valueOf(100.00)).max(BigDecimal.ZERO).setScale(2, RoundingMode.HALF_UP);

        // 4. Calibrated Confidence (0.00 - 100.00)
        BigDecimal smcConf = setup.getConfidence() != null ? setup.getConfidence() : BigDecimal.valueOf(75.00);
        BigDecimal rawConf = smcConf.multiply(BigDecimal.valueOf(0.60)).add(signalScore.multiply(BigDecimal.valueOf(0.40)));
        BigDecimal confidence = rawConf.min(BigDecimal.valueOf(100.00)).max(BigDecimal.ZERO).setScale(2, RoundingMode.HALF_UP);

        // 5. Market Regime & Context
        String regime = "LONG".equalsIgnoreCase(setup.getDirection()) ? "BULLISH_TRENDING" : "BEARISH_TRENDING";
        String context = rr.compareTo(BigDecimal.valueOf(2.5)) >= 0 ? "FAVORABLE_TREND_CONTINUATION" : "EQUILIBRIUM_REVERSION";

        String modelMetadata = String.format("{\"engine\":\"DeterministicBaselineIntelligenceEngine\",\"version\":\"%s\"}", VERSION);
        String featureSummary = String.format("{\"rrFactor\":\"%s\",\"smcConfidence\":\"%s\"}", rrFactor, smcConf);

        return new AiSignalEnrichment(
                account != null ? account : setup.getTradingAccount(),
                setup.getSetupId(),
                setup.getSymbol(),
                setup.getDirection(),
                VERSION,
                patternScore,
                signalScore,
                confidence,
                regime,
                context,
                modelMetadata,
                featureSummary,
                Instant.now()
        );
    }
}
