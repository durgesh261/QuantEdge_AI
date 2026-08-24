package com.quantedge.ai.config;

import com.quantedge.ai.service.AiIntelligenceEngine;
import com.quantedge.ai.service.AiInferenceEngine;
import com.quantedge.ai.service.DeterministicBaselineIntelligenceEngine;
import com.quantedge.ai.service.OnnxModelInferenceService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;

/**
 * AI Engine Configuration.
 * Defines which AI Intelligence Engine implementation to use as primary.
 */
@Configuration
public class AiEngineConfig {

    /**
     * Primary AI Inference Engine with full feature extraction and inference pipeline.
     * Uses ONNX Runtime with trained model, falls back to deterministic scoring.
     */
    @Bean
    @Primary
    public AiIntelligenceEngine aiInferenceEngine(AiInferenceEngine inferenceEngine) {
        return inferenceEngine;
    }

    /**
     * ONNX Model Inference Service for ML-based inference.
     */
    @Bean
    public OnnxModelInferenceService onnxModelInferenceService() {
        return new OnnxModelInferenceService();
    }

    /**
     * Baseline deterministic engine kept for fallback/testing.
     * Not primary - only used when explicitly requested.
     */
    @Bean
    public AiIntelligenceEngine deterministicBaselineEngine(DeterministicBaselineIntelligenceEngine baselineEngine) {
        return baselineEngine;
    }
}