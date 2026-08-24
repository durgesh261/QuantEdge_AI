package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.dto.AiFeatureVector;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.strategy.entity.StrategySetupRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

/**
 * Production AI Intelligence Engine with proper inference pipeline.
 * 
 * Architecture:
 * 1. Feature Extraction (deterministic from market data)
 * 2. Feature Validation
 * 3. Model Inference (ONNX Runtime with trained model, fallback to deterministic)
 * 4. Confidence Calculation (from model internals)
 * 5. Post-processing & Explanation Generation
 * 6. Decision Integration
 */
@Service
public class AiInferenceEngine implements AiIntelligenceEngine {

    private static final Logger log = LoggerFactory.getLogger(AiInferenceEngine.class);
    
    // Model versioning
    private static final String MODEL_NAME = "QuantEdge-AI-Inference";
    private static final String MODEL_VERSION = "2.0.0";
    private static final String FEATURE_VERSION = "1.0";
    
    private final AiFeatureExtractor featureExtractor;
    private final OnnxModelInferenceService onnxInferenceService;

    public AiInferenceEngine(AiFeatureExtractor featureExtractor, OnnxModelInferenceService onnxInferenceService) {
        this.featureExtractor = featureExtractor;
        this.onnxInferenceService = onnxInferenceService;
    }

    @Override
    public String getVersion() {
        return MODEL_VERSION + (onnxInferenceService.isModelLoaded() ? "-ONNX" : "-DETERMINISTIC");
    }

    @Override
    public AiSignalEnrichment evaluate(TradingAccount account, StrategySetupRecord setup) {
        long startTime = System.nanoTime();
        
        try {
            // 1. Feature Extraction
            Optional<AiFeatureVector> featureOpt = featureExtractor.extractFeatures(account, setup);
            if (featureOpt.isEmpty()) {
                log.warn("Insufficient features for setup {}", setup.getSetupId());
                return createFallbackEnrichment(account, setup, "INSUFFICIENT_DATA", startTime);
            }
            
            AiFeatureVector features = featureOpt.get();
            
            // 2. Feature Validation
            if (!features.isValid()) {
                log.warn("Invalid feature vector for setup {}", setup.getSetupId());
                return createFallbackEnrichment(account, setup, "INVALID_FEATURES", startTime);
            }
            
            // 3. Model Inference - try ONNX first, fallback to deterministic
            InferenceResult result;
            boolean usedOnnx = false;
            
            if (onnxInferenceService.isModelLoaded()) {
                Optional<OnnxModelInferenceService.OnnxInferenceResult> onnxResult = onnxInferenceService.runInference(features);
                if (onnxResult.isPresent()) {
                    result = new InferenceResult(
                            onnxResult.get().patternScore(),
                            onnxResult.get().signalScore(),
                            onnxResult.get().confidence(),
                            onnxResult.get().marketRegime()
                    );
                    usedOnnx = true;
                    log.debug("ONNX inference used for setup {}", setup.getSetupId());
                } else {
                    log.warn("ONNX inference returned empty for setup {}, falling back to deterministic", setup.getSetupId());
                    result = runDeterministicInference(features);
                }
            } else {
                result = runDeterministicInference(features);
            }
            
            // 4. Post-processing & Explanation
            String explanation = generateExplanation(features, result);
            String supportingFactors = extractSupportingFactors(features, result);
            String riskFactors = extractRiskFactors(features, result);
            
            // 5. Build Enrichment
            String intelligenceVersion = MODEL_VERSION + (usedOnnx ? "-ONNX" : "-DETERMINISTIC");
            AiSignalEnrichment enrichment = new AiSignalEnrichment(
                    account != null ? account : setup.getTradingAccount(),
                    setup.getSetupId(),
                    setup.getSymbol(),
                    setup.getDirection(),
                    intelligenceVersion,
                    result.patternScore(),
                    result.signalScore(),
                    result.confidence(),
                    result.marketRegime(),
                    explanation,
                    buildModelMetadata(features, result, usedOnnx),
                    buildFeatureSummary(features, supportingFactors, riskFactors),
                    Instant.now()
            );
            
            long latencyMs = (System.nanoTime() - startTime) / 1_000_000;
            log.info("AI inference completed for setup {} in {}ms [{}{}]: patternScore={}, signalScore={}, confidence={}, regime={}",
                    setup.getSetupId(), latencyMs, usedOnnx ? "ONNX" : "DETERMINISTIC", 
                    usedOnnx ? "" : " (model not loaded)", 
                    result.patternScore(), result.signalScore(), result.confidence(), result.marketRegime());
            
            return enrichment;
            
        } catch (Exception e) {
            log.error("AI inference failed for setup {}: {}", setup.getSetupId(), e.getMessage());
            return createFallbackEnrichment(account, setup, "INFERENCE_ERROR: " + e.getMessage(), startTime);
        }
    }

    /**
     * Core deterministic inference logic - used as fallback when ONNX model unavailable.
     * This is rule-calibrated scoring based on extracted features.
     */
    private InferenceResult runDeterministicInference(AiFeatureVector f) {
        // Pattern Score: SMC structure quality (0-100)
        BigDecimal patternScore = calculatePatternScore(f);
        
        // Signal Score: Market regime alignment & momentum (0-100)
        BigDecimal signalScore = calculateSignalScore(f);
        
        // Confidence: Calibrated combination (0-100)
        BigDecimal confidence = calculateConfidence(f, patternScore, signalScore);
        
        // Market Regime Classification
        String regime = classifyMarketRegime(f);
        
        return new InferenceResult(patternScore, signalScore, confidence, regime);
    }

    private BigDecimal calculatePatternScore(AiFeatureVector f) {
        // Weighted combination of SMC structural features
        BigDecimal bos = f.bosStrength().multiply(BigDecimal.valueOf(0.25));
        BigDecimal choch = f.chochStrength().multiply(BigDecimal.valueOf(0.20));
        BigDecimal ob = f.orderBlockStrength().multiply(BigDecimal.valueOf(0.20));
        BigDecimal fvg = f.fvgStrength().multiply(BigDecimal.valueOf(0.15));
        BigDecimal liq = f.liquidityProximity().multiply(BigDecimal.valueOf(0.10));
        BigDecimal entryPrec = f.entryPrecision().multiply(BigDecimal.valueOf(0.10));
        
        BigDecimal raw = bos.add(choch).add(ob).add(fvg).add(liq).add(entryPrec);
        BigDecimal score = raw.multiply(BigDecimal.valueOf(100)).setScale(2, RoundingMode.HALF_UP);
        return score.min(BigDecimal.valueOf(100)).max(BigDecimal.ZERO);
    }

    private BigDecimal calculateSignalScore(AiFeatureVector f) {
        // Market regime alignment and momentum
        BigDecimal trendAlign = f.regimeAlignment() ? BigDecimal.valueOf(0.25) : BigDecimal.ZERO;
        BigDecimal trend1h = f.trendStrength1h().multiply(BigDecimal.valueOf(0.20));
        BigDecimal trend15m = f.trendStrength15m().multiply(BigDecimal.valueOf(0.15));
        BigDecimal trend4h = f.trendStrength4h().multiply(BigDecimal.valueOf(0.10));
        BigDecimal mom1h = f.momentum1h().abs().multiply(BigDecimal.valueOf(0.10));
        BigDecimal mom15m = f.momentum15m().abs().multiply(BigDecimal.valueOf(0.10));
        BigDecimal volProfile = f.volumeProfile().min(BigDecimal.ONE).multiply(BigDecimal.valueOf(0.10));
        
        BigDecimal raw = trendAlign.add(trend1h).add(trend15m).add(trend4h).add(mom1h).add(mom15m).add(volProfile);
        BigDecimal score = raw.multiply(BigDecimal.valueOf(100)).setScale(2, RoundingMode.HALF_UP);
        return score.min(BigDecimal.valueOf(100)).max(BigDecimal.ZERO);
    }

    private BigDecimal calculateConfidence(AiFeatureVector f, BigDecimal patternScore, BigDecimal signalScore) {
        // Confidence from pattern quality, signal alignment, and data sufficiency
        BigDecimal patternWeight = patternScore.divide(BigDecimal.valueOf(100), 4, RoundingMode.HALF_UP).multiply(BigDecimal.valueOf(0.40));
        BigDecimal signalWeight = signalScore.divide(BigDecimal.valueOf(100), 4, RoundingMode.HALF_UP).multiply(BigDecimal.valueOf(0.35));
        
        // Data quality factor
        BigDecimal dataQuality = calculateDataQualityFactor(f);
        BigDecimal qualityWeight = dataQuality.multiply(BigDecimal.valueOf(0.25));
        
        // SMC confidence from deterministic engine - use setup's actual confidence
        BigDecimal smcConf = f.riskReward().compareTo(BigDecimal.ZERO) > 0 ? BigDecimal.valueOf(0.75) : BigDecimal.ZERO;
        // Note: In production, this would come from the actual SMC setup confidence
        BigDecimal smcWeight = smcConf.multiply(BigDecimal.valueOf(0.25));
        
        BigDecimal raw = patternWeight.add(signalWeight).add(qualityWeight).add(smcWeight);
        BigDecimal confidence = raw.multiply(BigDecimal.valueOf(100)).setScale(2, RoundingMode.HALF_UP);
        return confidence.min(BigDecimal.valueOf(100)).max(BigDecimal.ZERO);
    }

    private BigDecimal calculateDataQualityFactor(AiFeatureVector f) {
        // Penalize if multi-timeframe data is missing
        int missing = 0;
        if (f.trendStrength1h().compareTo(BigDecimal.ZERO) == 0) missing++;
        if (f.trendStrength15m().compareTo(BigDecimal.ZERO) == 0) missing++;
        if (f.trendStrength4h().compareTo(BigDecimal.ZERO) == 0) missing++;
        if (f.volumeProfile().compareTo(BigDecimal.ZERO) == 0) missing++;
        
        return BigDecimal.valueOf(1.0 - missing * 0.15).max(BigDecimal.valueOf(0.4));
    }

    private String classifyMarketRegime(AiFeatureVector f) {
        // Multi-timeframe regime classification
        String r1h = f.regime1h();
        String r15m = f.regime15m();
        String r4h = f.regime4h();
        
        // Strong alignment
        if (r1h.equals(r15m) && r15m.equals(r4h)) {
            if (r1h.contains("BULLISH")) return "STRONG_BULLISH_TREND";
            if (r1h.contains("BEARISH")) return "STRONG_BEARISH_TREND";
            if (r1h.equals("RANGING")) return "CLEAR_RANGE";
        }
        
        // Partial alignment
        if (r1h.equals(r15m) || r15m.equals(r4h)) {
            if (r1h.contains("BULLISH") || r15m.contains("BULLISH")) return "BULLISH_TRENDING";
            if (r1h.contains("BEARISH") || r15m.contains("BEARISH")) return "BEARISH_TRENDING";
        }
        
        // Conflicting
        if ((r1h.contains("BULLISH") && r4h.contains("BEARISH")) || 
            (r1h.contains("BEARISH") && r4h.contains("BULLISH"))) {
            return "CONFLICTING_TIMEFRAMES";
        }
        
        return "UNCERTAIN";
    }

    private String generateExplanation(AiFeatureVector f, InferenceResult result) {
        StringBuilder sb = new StringBuilder();
        
        if (result.patternScore().compareTo(BigDecimal.valueOf(70)) >= 0) {
            sb.append("Strong SMC structure detected: ");
            if (f.bosStrength().compareTo(BigDecimal.valueOf(0.5)) > 0) sb.append("clear BOS; ");
            if (f.orderBlockStrength().compareTo(BigDecimal.valueOf(0.5)) > 0) sb.append("validated order block; ");
            if (f.fvgStrength().compareTo(BigDecimal.valueOf(0.5)) > 0) sb.append("active FVG; ");
        } else if (result.patternScore().compareTo(BigDecimal.valueOf(40)) >= 0) {
            sb.append("Moderate SMC structure: ");
            if (f.chochStrength().compareTo(BigDecimal.valueOf(0.5)) > 0) sb.append("CHOCH confirmed; ");
            else sb.append("structure developing; ");
        } else {
            sb.append("Weak SMC structure: setup lacks clear structural confirmation. ");
        }
        
        if (result.signalScore().compareTo(BigDecimal.valueOf(60)) >= 0) {
            sb.append("Market regime favors direction: ");
            sb.append(result.marketRegime().toLowerCase().replace("_", " ")).append(". ");
        } else {
            sb.append("Market regime ").append(result.marketRegime().toLowerCase().replace("_", " ")).append(". ");
        }
        
        if (!f.regimeAlignment()) {
            sb.append("CAUTION: Multi-timeframe disagreement detected. ");
        }
        
        return sb.toString().trim();
    }

    private String extractSupportingFactors(AiFeatureVector f, InferenceResult result) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        
        if (f.bosStrength().compareTo(BigDecimal.valueOf(0.6)) > 0) { append(sb, first, "Strong BOS"); first = false; }
        if (f.chochStrength().compareTo(BigDecimal.valueOf(0.6)) > 0) { append(sb, first, "Clear CHOCH"); first = false; }
        if (f.orderBlockStrength().compareTo(BigDecimal.valueOf(0.6)) > 0) { append(sb, first, "Validated Order Block"); first = false; }
        if (f.fvgStrength().compareTo(BigDecimal.valueOf(0.6)) > 0) { append(sb, first, "Active FVG"); first = false; }
        if (f.regimeAlignment()) { append(sb, first, "Multi-timeframe Alignment"); first = false; }
        if (f.trendStrength1h().compareTo(BigDecimal.valueOf(0.4)) > 0) { append(sb, first, "Strong 1H Trend"); first = false; }
        if (f.momentum1h().compareTo(BigDecimal.ZERO) > 0 && "BUY".equalsIgnoreCase(f.direction())) { append(sb, first, "Positive Momentum"); first = false; }
        if (f.momentum1h().compareTo(BigDecimal.ZERO) < 0 && "SELL".equalsIgnoreCase(f.direction())) { append(sb, first, "Negative Momentum"); first = false; }
        if (f.volumeProfile().compareTo(BigDecimal.ONE) > 0) { append(sb, first, "Increasing Volume"); first = false; }
        if (f.entryPrecision().compareTo(BigDecimal.valueOf(0.7)) > 0) { append(sb, first, "Precise Entry"); first = false; }
        
        return sb.toString();
    }

    private void append(StringBuilder sb, boolean first, String factor) {
        if (!first) sb.append("; ");
        sb.append(factor);
    }

    private String extractRiskFactors(AiFeatureVector f, InferenceResult result) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        
        if (!f.regimeAlignment()) { append(sb, first, "Timeframe Conflict"); first = false; }
        if (f.volatility1h().compareTo(BigDecimal.valueOf(0.02)) > 0) { append(sb, first, "High Volatility"); first = false; }
        if (f.liquidityProximity().compareTo(BigDecimal.valueOf(0.3)) < 0) { append(sb, first, "Near Liquidity"); first = false; }
        if (f.accountUtilization().compareTo(BigDecimal.valueOf(0.7)) > 0) { append(sb, first, "High Account Utilization"); first = false; }
        if (f.leverageRatio().compareTo(BigDecimal.valueOf(0.5)) > 0) { append(sb, first, "High Leverage"); first = false; }
        if (f.riskReward().compareTo(BigDecimal.valueOf(2.0)) < 0) { append(sb, first, "Suboptimal R:R"); first = false; }
        if (f.entryPrecision().compareTo(BigDecimal.valueOf(0.4)) < 0) { append(sb, first, "Entry Off Structure"); first = false; }
        
        return sb.toString();
    }

    private String buildModelMetadata(AiFeatureVector f, InferenceResult result, boolean usedOnnx) {
        return String.format(
                "{\"model\":\"%s\",\"version\":\"%s\",\"featureVersion\":\"%s\",\"inferenceId\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"1h\",\"backend\":\"%s\"}",
                MODEL_NAME, MODEL_VERSION, FEATURE_VERSION, UUID.randomUUID().toString().substring(0, 8), f.symbol(),
                usedOnnx ? "ONNX_RUNTIME" : "DETERMINISTIC_FALLBACK"
        );
    }

    private String buildFeatureSummary(AiFeatureVector f, String supporting, String risks) {
        return String.format(
                "{\"features\":%s,\"supportingFactors\":\"%s\",\"riskFactors\":\"%s\"}",
                f.toFeatureSummary(), supporting, risks
        );
    }

    private AiSignalEnrichment createFallbackEnrichment(TradingAccount account, StrategySetupRecord setup, String reason, long startTime) {
        long latencyMs = (System.nanoTime() - startTime) / 1_000_000;
        
        return new AiSignalEnrichment(
                account != null ? account : setup.getTradingAccount(),
                setup.getSetupId(),
                setup.getSymbol(),
                setup.getDirection(),
                MODEL_VERSION + "-FALLBACK",
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                "AI_UNAVAILABLE",
                "AI intelligence unavailable: " + reason,
                String.format("{\"model\":\"%s\",\"version\":\"%s\",\"fallback\":true,\"reason\":\"%s\",\"latencyMs\":%d}", 
                        MODEL_NAME, MODEL_VERSION, reason, latencyMs),
                String.format("{\"fallback\":true,\"reason\":\"%s\"}", reason),
                Instant.now()
        );
    }

    /**
     * Phase G: Executes shadow inference on a live strategy setup.
     * Computes raw ONNX regression predictions, logs result, but STRICTLY enforces:
     * - governanceStatus = "REJECTED"
     * - executionAuthorized = false
     */
    public com.quantedge.ai.dto.AiShadowResult evaluateShadow(TradingAccount account, StrategySetupRecord setup) {
        Optional<AiFeatureVector> featureOpt = featureExtractor.extractFeatures(account, setup);
        float[] vector = featureOpt.map(AiFeatureVector::toFloat32Array).orElse(new float[24]);
        
        return evaluateShadowInternal(
                setup.getSymbol(),
                setup.getDirection(),
                setup.getSetupId(),
                setup.getCreatedAt() != null ? setup.getCreatedAt() : Instant.now(),
                setup.getOrderBlockPrice() != null ? "OB_" + setup.getOrderBlockPrice().toPlainString() : "OB_UNKNOWN",
                vector
        );
    }

    /**
     * Phase G: Executes shadow inference directly on a feature vector.
     */
    public com.quantedge.ai.dto.AiShadowResult evaluateShadow(String symbol, String direction, float[] featureVector) {
        return evaluateShadowInternal(
                symbol,
                direction,
                "manual-shadow-" + UUID.randomUUID().toString().substring(0, 8),
                Instant.now(),
                "OB_MANUAL",
                featureVector
        );
    }

    private com.quantedge.ai.dto.AiShadowResult evaluateShadowInternal(
            String symbol,
            String direction,
            String setupId,
            Instant setupTime,
            String obIdentifier,
            float[] vector
    ) {
        BigDecimal predR = BigDecimal.ZERO;
        BigDecimal predMfe = BigDecimal.ZERO;
        BigDecimal predMae = BigDecimal.ZERO;

        if (onnxInferenceService.isModelLoaded() && vector != null && vector.length == 24) {
            Optional<OnnxModelInferenceService.OnnxRegressionResult> regOpt =
                    onnxInferenceService.runRegressionInference(vector);
            if (regOpt.isPresent()) {
                predR = regOpt.get().predictedRealizedR();
                predMfe = regOpt.get().predictedMfeR();
                predMae = regOpt.get().predictedMaeR();
            }
        }

        BigDecimal threshold = BigDecimal.valueOf(0.50);
        boolean accepted = predR.compareTo(threshold) >= 0;

        com.quantedge.ai.dto.AiShadowResult shadowResult = new com.quantedge.ai.dto.AiShadowResult(
                symbol,
                Instant.now(),
                setupTime,
                direction,
                obIdentifier,
                "quantedge-ai-v2.onnx",
                MODEL_VERSION,
                onnxInferenceService.getModelArtifactHash(),
                "2.0.0",
                vector,
                predR,
                predMfe,
                predMae,
                threshold,
                accepted,
                "REJECTED", // Governance Status: REJECTED
                false       // Execution Authorized: FALSE (Invariant!)
        );

        log.info("[AI SHADOW INFERENCE] setupId={}, symbol={}, dir={}, predRealizedR={}, predMfe={}, predMae={}, threshold={}, accepted={}, governance={}, executionAuthorized={}",
                setupId, symbol, direction, predR, predMfe, predMae, threshold, accepted,
                shadowResult.governanceStatus(), shadowResult.executionAuthorized());

        return shadowResult;
    }

    /**
     * Internal inference result record.
     */
    private record InferenceResult(
            BigDecimal patternScore,
            BigDecimal signalScore,
            BigDecimal confidence,
            String marketRegime
    ) {}
}