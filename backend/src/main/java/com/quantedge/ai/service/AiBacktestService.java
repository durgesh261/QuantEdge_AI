package com.quantedge.ai.service;

import com.quantedge.ai.dto.AiFeatureVector;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.entity.AiDecisionAudit;
import com.quantedge.ai.repository.AiDecisionAuditRepository;
import com.quantedge.ai.repository.AiSignalEnrichmentRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.repository.OrderRepository;
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
 * 
 * NOTE: This is evaluation infrastructure. For actual model training,
 * a separate ML pipeline (Python/TensorFlow/PyTorch) should be used.
 */
@Service
public class AiBacktestService {

    private static final Logger log = LoggerFactory.getLogger(AiBacktestService.class);

    private final AiSignalEnrichmentRepository enrichmentRepository;
    private final AiDecisionAuditRepository auditRepository;
    private final StrategySetupRepository setupRepository;
    private final OrderRepository orderRepository;

    public AiBacktestService(
            AiSignalEnrichmentRepository enrichmentRepository,
            AiDecisionAuditRepository auditRepository,
            StrategySetupRepository setupRepository,
            OrderRepository orderRepository
    ) {
        this.enrichmentRepository = enrichmentRepository;
        this.auditRepository = auditRepository;
        this.setupRepository = setupRepository;
        this.orderRepository = orderRepository;
    }

    /**
     * Runs a backtest evaluation over a historical date range.
     * Uses out-of-sample evaluation to prevent look-ahead bias.
     */
    @Transactional(readOnly = true)
    public BacktestResult runBacktest(BacktestConfig config) {
        log.info("Starting AI backtest: {} to {} for symbols {}, starting equity: {}", 
                config.from(), config.to(), config.symbols(), config.startingEquity());

        // 1. Fetch all setups in date range
        List<StrategySetupRecord> allSetups = setupRepository.findByCreatedAtBetween(
                config.from(), config.to()
        ).stream()
                .filter(s -> config.symbols().isEmpty() || config.symbols().contains(s.getSymbol()))
                .toList();

        log.info("Found {} setups in date range", allSetups.size());

        // 2. Split into train/validation/test temporally (strict chronological split)
        // Train: [from, splitPoint), Test: [splitPoint, to]
        // No overlapping, no look-ahead
        long totalSeconds = config.to().getEpochSecond() - config.from().getEpochSecond();
        long trainSeconds = (totalSeconds * (100 - config.testSplitPercent())) / 100;
        Instant splitPoint = config.from().plusSeconds(trainSeconds);

        List<StrategySetupRecord> trainSetups = new ArrayList<>();
        List<StrategySetupRecord> testSetups = new ArrayList<>();
        
        for (StrategySetupRecord setup : allSetups) {
            if (setup.getCreatedAt().isBefore(splitPoint)) {
                trainSetups.add(setup);
            } else {
                testSetups.add(setup);
            }
        }

        log.info("Train: {} setups ({} to {}), Test: {} setups ({} to {})", 
                trainSetups.size(), config.from(), splitPoint,
                testSetups.size(), splitPoint, config.to());

        // 3. Evaluate on test set only (train set would be used for model training in separate pipeline)
        TestMetrics smcOnlyMetrics = evaluateSetups(testSetups, false, config.startingEquity());
        TestMetrics smcPlusAiMetrics = evaluateSetups(testSetups, true, config.startingEquity());

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

    /**
     * Evaluates setups using simplified outcome determination based on setup state.
     * In production, this would use actual fill data from a separate historical data service.
     */
    private TestMetrics evaluateSetups(List<StrategySetupRecord> setups, boolean useAi, BigDecimal startingEquity) {
        int total = setups.size();
        if (total == 0) return emptyMetrics();

        int truePositives = 0;
        int falsePositives = 0;
        int falseNegatives = 0;
        int trueNegatives = 0;

        BigDecimal totalReturn = BigDecimal.ZERO;
        int winningTrades = 0;
        int losingTrades = 0;
        BigDecimal grossProfit = BigDecimal.ZERO;
        BigDecimal grossLoss = BigDecimal.ZERO;
        BigDecimal maxDrawdown = BigDecimal.ZERO;
        BigDecimal peakEquity = startingEquity;
        BigDecimal currentEquity = startingEquity;

        int evaluatedTotal = 0;

        for (StrategySetupRecord setup : setups) {
            // Determine actual outcome from setup state (simplified for evaluation infrastructure)
            // In production, this would query actual fill data from historical data service
            ActualOutcome outcome = determineActualOutcomeFromState(setup);
            
            if (outcome == ActualOutcome.NO_EXECUTION_DATA) {
                // Skip setups without execution data - cannot evaluate
                continue;
            }

            evaluatedTotal++;
            boolean actualWin = outcome == ActualOutcome.WIN;

            // AI decision
            boolean aiWouldTrade = useAi ? wouldAiTrade(setup) : true; // SMC-only trades all qualified
            
            if (aiWouldTrade) {
                if (actualWin) {
                    truePositives++;
                    winningTrades++;
                    BigDecimal pnl = calculatePnL(setup);
                    currentEquity = currentEquity.add(pnl);
                    totalReturn = totalReturn.add(pnl);
                    grossProfit = grossProfit.add(pnl.max(BigDecimal.ZERO));
                } else {
                    falsePositives++;
                    losingTrades++;
                    BigDecimal pnl = calculatePnL(setup).negate();
                    currentEquity = currentEquity.add(pnl);
                    totalReturn = totalReturn.add(pnl);
                    grossLoss = grossLoss.add(pnl.abs());
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

        if (evaluatedTotal == 0) return emptyMetrics();

        BigDecimal precision = (truePositives + falsePositives) > 0 ? 
                BigDecimal.valueOf(truePositives).divide(BigDecimal.valueOf(truePositives + falsePositives), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal recall = (truePositives + falseNegatives) > 0 ? 
                BigDecimal.valueOf(truePositives).divide(BigDecimal.valueOf(truePositives + falseNegatives), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal f1 = (precision.add(recall)).compareTo(BigDecimal.ZERO) > 0 ?
                precision.multiply(recall).multiply(BigDecimal.valueOf(2)).divide(precision.add(recall), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        BigDecimal accuracy = BigDecimal.valueOf(truePositives + trueNegatives).divide(BigDecimal.valueOf(evaluatedTotal), 4, RoundingMode.HALF_UP);

        BigDecimal winRate = (winningTrades + losingTrades) > 0 ?
                BigDecimal.valueOf(winningTrades).divide(BigDecimal.valueOf(winningTrades + losingTrades), 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        
        // Correct profit factor: gross profit / gross loss
        BigDecimal profitFactor = grossLoss.compareTo(BigDecimal.ZERO) > 0 ?
                grossProfit.divide(grossLoss, 4, RoundingMode.HALF_UP) : BigDecimal.ONE;

        return new TestMetrics(
                accuracy, precision, recall, f1,
                BigDecimal.valueOf(winningTrades).divide(BigDecimal.valueOf(evaluatedTotal), 4, RoundingMode.HALF_UP),
                totalReturn,
                profitFactor,
                maxDrawdown,
                truePositives, falsePositives, trueNegatives, falseNegatives
        );
    }

    private ActualOutcome determineActualOutcomeFromState(StrategySetupRecord setup) {
        // Simplified outcome determination from setup state
        // In production, this would query actual historical fill data
        String state = setup.getSetupState();
        if ("COMPLETED".equalsIgnoreCase(state)) {
            // Determine win/loss based on whether TP or SL was hit
            // Simplified: assume WIN if COMPLETED with valid TP/SL
            if (setup.getTakeProfit() != null && setup.getEntryPrice() != null && setup.getStopLoss() != null) {
                boolean isLong = "LONG".equalsIgnoreCase(setup.getDirection()) || "BUY".equalsIgnoreCase(setup.getDirection());
                if (isLong) {
                    return setup.getTakeProfit().compareTo(setup.getEntryPrice()) > 0 ? ActualOutcome.WIN : ActualOutcome.LOSS;
                } else {
                    return setup.getTakeProfit().compareTo(setup.getEntryPrice()) < 0 ? ActualOutcome.WIN : ActualOutcome.LOSS;
                }
            }
            return ActualOutcome.NO_EXECUTION_DATA;
        }
        if ("INVALIDATED".equalsIgnoreCase(state) || "STOPPED_OUT".equalsIgnoreCase(state)) {
            return ActualOutcome.LOSS;
        }
        // For other states (ACTIVE, QUALIFIED, PENDING), no execution data yet
        return ActualOutcome.NO_EXECUTION_DATA;
    }

    private enum ActualOutcome {
        WIN, LOSS, NO_EXECUTION_DATA
    }

    private boolean wouldAiTrade(StrategySetupRecord setup) {
        // Check if AI enrichment exists and has high confidence
        List<AiSignalEnrichment> enrichments = enrichmentRepository.findBySetupId(setup.getSetupId());
        if (enrichments.isEmpty()) return false;
        
        AiSignalEnrichment latest = enrichments.getFirst();
        return latest.getConfidence() != null && latest.getConfidence().compareTo(BigDecimal.valueOf(50)) >= 0;
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
 * Calculates PnL for a setup record.
 * PnL = (exitPrice - entryPrice) * direction multiplier * position size
 */
private BigDecimal calculatePnL(StrategySetupRecord setup) {
    BigDecimal entry = setup.getEntryPrice();
    BigDecimal exit = setup.getTakeProfit() != null ? setup.getTakeProfit() : entry;
    BigDecimal directionMult = "LONG".equalsIgnoreCase(setup.getDirection()) || "BUY".equalsIgnoreCase(setup.getDirection()) ? BigDecimal.ONE : BigDecimal.valueOf(-1);
    return exit.subtract(entry).multiply(directionMult);
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
            int testSplitPercent, // e.g., 20 for 80/20 split
            BigDecimal startingEquity // Configurable starting equity
    ) {
        public static BacktestConfig builder() {
            return new BacktestConfig(
                    "1.0", "2.0.0", 
                    Instant.now().minusSeconds(86400 * 30), // 30 days ago
                    Instant.now(),
                    List.of("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"),
                    20, // 80/20 split
                    BigDecimal.valueOf(10000) // Default starting equity
            );
        }
    }

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