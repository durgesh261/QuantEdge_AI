package com.quantedge.ai.service;

import com.quantedge.ai.dto.AiFeatureVector;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.entity.AiDecisionAudit;
import com.quantedge.ai.repository.AiDecisionAuditRepository;
import com.quantedge.ai.repository.AiSignalEnrichmentRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

/**
 * AI Backtesting and Evaluation Infrastructure.
 * 
 * Compares SMC-only vs SMC+AI performance to validate AI value-add.
 * Prevents data leakage with strict temporal splits.
 */
@Service
public class AiBacktestService {

    private static final Logger log = LoggerFactory.getLogger(AiBacktestService.class);

    private final AiSignalEnrichmentRepository enrichmentRepository;
    private final AiDecisionAuditRepository auditRepository;
    private final StrategySetupRepository setupRepository;

    public AiBacktestService(
            AiSignalEnrichmentRepository enrichmentRepository,
            AiDecisionAuditRepository auditRepository,
            StrategySetupRepository setupRepository
    ) {
        this.enrichmentRepository = enrichmentRepository;
        this.auditRepository = auditRepository;
        this.setupRepository = setupRepository;
    }

    /**
     * Runs a backtest evaluation over a historical date range.
     * Uses out-of-sample evaluation to prevent look-ahead bias.
     */
    @Transactional(readOnly = true)
    public BacktestResult runBacktest(BacktestConfig config) {
        log.info("Starting AI backtest: {} to {} for symbols {}", 
                config.from(), config.to(), config.symbols());

        // 1. Fetch all setups in date range
        List<StrategySetupRecord> allSetups = setupRepository.findByCreatedAtBetween(
                config.from(), config.to()
        ).stream()
                .filter(s -> config.symbols().isEmpty() || config.symbols().contains(s.getSymbol()))
                .toList();

        log.info("Found {} setups in date range", allSetups.size());

        // 2. Split into train/validation/test temporally
        List<StrategySetupRecord> trainSetups = new ArrayList<>();
        List<StrategySetupRecord> testSetups = new ArrayList<>();
        
        Instant splitPoint = config.from().plusSeconds(
                config.to().getEpochSecond() - config.from().getEpochSecond() * (100 - config.testSplitPercent()) / 100
        );

        for (StrategySetupRecord setup : allSetups) {
            if (setup.getCreatedAt().isBefore(splitPoint)) {
                trainSetups.add(setup);
            } else {
                testSetups.add(setup);
            }
        }

        log.info("Train: {} setups, Test: {} setups", trainSetups.size(), testSetups.size());

        // 3. Evaluate on test set
        TestMetrics smcOnlyMetrics = evaluateSetups(testSetups, false);
        TestMetrics smcPlusAiMetrics = evaluateSetups(testSetups, true);

        // 4. Calculate improvement
        BigDecimal accuracyImprovement = calculateImprovement(smcOnlyMetrics.accuracy(), smcPlusAiMetrics.accuracy());
        BigDecimal precisionImprovement = calculateImprovement(smcOnlyMetrics.precision(), smcPlusAiMetrics.precision());
        BigDecimal recallImprovement = calculateImprovement(smcOnlyMetrics.recall(), smcPlusAiMetrics.recall());
        BigDecimal f1Improvement = calculateImprovement(smcOnlyMetrics.f1Score(), smcPlusAiMetrics.f1Score());

        return new BacktestResult(
                config.datasetVersion(),
                config.modelVersion(),
                config.from(),
                config.to(),
                config.symbols(),
                config.testSplitPercent(),
                trainSetups.size(),
                testSetups.size(),
                smcOnlyMetrics,
                smcPlusAiMetrics,
                accuracyImprovement,
                precisionImprovement,
                recallImprovement,
                f1Improvement,
                Instant.now()
        );
    }

    private TestMetrics evaluateSetups(List<StrategySetupRecord> setups, boolean useAi) {
        int total = setups.size();
        if (total == 0) return emptyMetrics();

        int truePositives = 0;
        int falsePositives = 0;
        int falseNegatives = 0;
        int trueNegatives = 0;

        BigDecimal totalReturn = BigDecimal.ZERO;
        int winningTrades = 0;
        int losingTrades = 0;
        BigDecimal maxDrawdown = BigDecimal.ZERO;
        BigDecimal peakEquity = BigDecimal.ZERO;
        BigDecimal currentEquity = BigDecimal.valueOf(10000); // Starting equity

        for (StrategySetupRecord setup : setups) {
            // Determine actual outcome (would come from fill data)
            // For now, simulate based on setup state
            boolean actualWin = "COMPLETED".equals(setup.getSetupState()) && 
                    setup.getTakeProfit() != null && setup.getEntryPrice() != null &&
                    (setup.getDirection().equals("LONG") ? 
                            setup.getTakeProfit().compareTo(setup.getEntryPrice()) > 0 :
                            setup.getTakeProfit().compareTo(setup.getEntryPrice()) < 0);

            // AI decision
            boolean aiWouldTrade = useAi ? wouldAiTrade(setup) : true; // SMC-only trades all qualified
            
            if (aiWouldTrade) {
                if (actualWin) {
                    truePositives++;
                    winningTrades++;
                    BigDecimal pnl = calculatePnL(setup);
                    currentEquity = currentEquity.add(pnl);
                    totalReturn = totalReturn.add(pnl);
                } else {
                    falsePositives++;
                    losingTrades++;
                    BigDecimal pnl = calculatePnL(setup).negate();
                    currentEquity = currentEquity.add(pnl);
                    totalReturn = totalReturn.add(pnl);
                }
            } else {
                if (actualWin) {
                    falseNegatives++;
                } else {
                    trueNegatives++;
                }
            }

            // Track drawdown
            if (currentEquity.compareTo(peakEquity) > 0) {
                peakEquity = currentEquity;
            }
            BigDecimal drawdown = peakEquity.subtract(currentEquity);
            if (drawdown.compareTo(maxDrawdown) > 0) {
                maxDrawdown = drawdown;
            }
        }

        BigDecimal precision = (truePositives + falsePositives) > 0 ? 
                BigDecimal.valueOf(truePositives).divide(BigDecimal.valueOf(truePositives + falsePositives), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal recall = (truePositives + falseNegatives) > 0 ? 
                BigDecimal.valueOf(truePositives).divide(BigDecimal.valueOf(truePositives + falseNegatives), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal f1 = (precision.add(recall)).compareTo(BigDecimal.ZERO) > 0 ?
                precision.multiply(recall).multiply(BigDecimal.valueOf(2)).divide(precision.add(recall), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal accuracy = BigDecimal.valueOf(truePositives + trueNegatives).divide(BigDecimal.valueOf(total), 4, RoundingMode.HALF_UP);

        BigDecimal winRate = (winningTrades + losingTrades) > 0 ?
                BigDecimal.valueOf(winningTrades).divide(BigDecimal.valueOf(winningTrades + losingTrades), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        
        BigDecimal profitFactor = losingTrades > 0 && totalReturn.compareTo(BigDecimal.ZERO) < 0 ?
                totalReturn.abs().divide(totalReturn.abs(), 4, RoundingMode.HALF_UP) : BigDecimal.ONE;

        return new TestMetrics(
                accuracy, precision, recall, f1,
                winRate, totalReturn, profitFactor, maxDrawdown,
                truePositives, falsePositives, trueNegatives, falseNegatives
        );
    }

    private boolean wouldAiTrade(StrategySetupRecord setup) {
        // Simplified: check if AI enrichment exists and has high confidence
        List<AiSignalEnrichment> enrichments = enrichmentRepository.findBySetupId(setup.getSetupId());
        if (enrichments.isEmpty()) return false;
        
        AiSignalEnrichment latest = enrichments.getFirst();
        return latest.getConfidence() != null && latest.getConfidence().compareTo(BigDecimal.valueOf(50)) >= 0;
    }

    private BigDecimal calculatePnL(StrategySetupRecord setup) {
        // Simplified PnL calculation
        if (setup.getRiskReward() != null) {
            return BigDecimal.valueOf(100).multiply(setup.getRiskReward().divide(BigDecimal.valueOf(100), 4, RoundingMode.HALF_UP));
        }
        return BigDecimal.valueOf(200); // Default 2R win
    }

    private BigDecimal calculateImprovement(BigDecimal baseline, BigDecimal improved) {
        if (baseline.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.ZERO;
        return improved.subtract(baseline).divide(baseline, 4, RoundingMode.HALF_UP).multiply(BigDecimal.valueOf(100));
    }

    private TestMetrics emptyMetrics() {
        return new TestMetrics(
                BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                0, 0, 0, 0
        );
    }

    /**
     * Backtest configuration.
     */
    public record BacktestConfig(
            String datasetVersion,
            String modelVersion,
            Instant from,
            Instant to,
            List<String> symbols,
            int testSplitPercent // e.g., 20 for 80/20 split
    ) {}

    /**
     * Backtest result with comparison metrics.
     */
    public record BacktestResult(
            String datasetVersion,
            String modelVersion,
            Instant from,
            Instant to,
            List<String> symbols,
            int testSplitPercent,
            int trainSize,
            int testSize,
            TestMetrics smcOnly,
            TestMetrics smcPlusAi,
            BigDecimal accuracyImprovementPercent,
            BigDecimal precisionImprovementPercent,
            BigDecimal recallImprovementPercent,
            BigDecimal f1ImprovementPercent,
            Instant evaluatedAt
    ) {}

    /**
     * Test metrics for a single configuration.
     */
    public record TestMetrics(
            BigDecimal accuracy,
            BigDecimal precision,
            BigDecimal recall,
            BigDecimal f1Score,
            BigDecimal winRate,
            BigDecimal totalReturn,
            BigDecimal profitFactor,
            BigDecimal maxDrawdown,
            int truePositives,
            int falsePositives,
            int trueNegatives,
            int falseNegatives
    ) {}
}