package com.quantedge.ai.service;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import com.quantedge.ai.dto.AiFeatureVector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.io.IOException;
import java.nio.FloatBuffer;
import java.util.Collections;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * ONNX Runtime-based ML Inference Service.
 * Loads and runs trained ONNX models for AI signal enrichment.
 * Falls back to deterministic scoring if model unavailable.
 */
@Service
public class OnnxModelInferenceService {

    private static final Logger log = LoggerFactory.getLogger(OnnxModelInferenceService.class);

    @Value("${ai.model.path:classpath:models/quantedge-ai-v2.onnx}")
    private String modelPath;

    @Value("${ai.model.enabled:true}")
    private boolean modelEnabled;

    private OrtEnvironment ortEnvironment;
    private OrtSession ortSession;
    private final Map<String, OrtSession> modelCache = new ConcurrentHashMap<>();
    private int inputFeatureCount = 24; // Expected feature vector size
    private boolean modelLoaded = false;

    @PostConstruct
    public void initialize() {
        if (!modelEnabled) {
            log.info("AI Model inference disabled via configuration");
            return;
        }

        try {
            ortEnvironment = OrtEnvironment.getEnvironment();
            
            // Try to load model from classpath or filesystem
            java.io.InputStream modelStream = getClass().getClassLoader().getResourceAsStream(
                    modelPath.replace("classpath:", ""));
            
            if (modelStream == null) {
                log.warn("ONNX model not found at {}. AI inference will use deterministic fallback.", modelPath);
                modelLoaded = false;
                return;
            }

            // Save to temp file for ONNX Runtime (requires file path)
            java.io.File tempModel = java.io.File.createTempFile("quantedge-ai-", ".onnx");
            tempModel.deleteOnExit();
            try (var os = new java.io.FileOutputStream(tempModel)) {
                modelStream.transferTo(os);
            }

            OrtSession.SessionOptions sessionOptions = new OrtSession.SessionOptions();
            sessionOptions.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
            sessionOptions.addConfigEntry("session.use_nnapi", "0");
            sessionOptions.addConfigEntry("session.use_cuda", "0");

            ortSession = ortEnvironment.createSession(tempModel.getAbsolutePath(), sessionOptions);
            modelLoaded = true;
            
            log.info("ONNX model loaded successfully from: {}", modelPath);
            log.info("Model input names: {}", ortSession.getInputInfo().keySet());
            log.info("Model output names: {}", ortSession.getOutputInfo().keySet());

        } catch (IOException | OrtException e) {
            log.warn("Failed to load ONNX model: {}. Will use deterministic fallback.", e.getMessage());
            modelLoaded = false;
        }
    }

    @PreDestroy
    public void cleanup() {
        try {
            if (ortSession != null) ortSession.close();
            if (ortEnvironment != null) ortEnvironment.close();
            modelCache.values().forEach(session -> {
                try { session.close(); } catch (Exception ignored) {}
            });
        } catch (Exception e) {
            log.warn("Error during ONNX cleanup: {}", e.getMessage());
        }
    }

    /**
     * Runs inference on the loaded ONNX model.
     * Returns Optional.empty() if model not loaded or inference fails.
     */
    public Optional<OnnxInferenceResult> runInference(AiFeatureVector features) {
        if (!modelLoaded || ortSession == null) {
            return Optional.empty();
        }

        try {
            // Delegate encoding to AiFeatureVector — the authoritative encoder.
            // This ensures the feature order and encoding logic can never diverge
            // between the DTO and the ONNX inference layer.
            float[] inputArray = features.toFloat32Array();

            // Create input tensor
            long[] inputShape = new long[]{1, inputFeatureCount};
            OnnxTensor inputTensor = OnnxTensor.createTensor(ortEnvironment, FloatBuffer.wrap(inputArray), inputShape);

            // Run inference
            Map<String, OnnxTensor> inputs = Collections.singletonMap(
                    ortSession.getInputInfo().keySet().iterator().next(), inputTensor);

            OrtSession.Result result = ortSession.run(inputs);

            // Parse outputs
            OnnxInferenceResult inferenceResult = parseOutputs(result);

            log.debug("ONNX inference completed: patternScore={}, signalScore={}, confidence={}",
                    inferenceResult.patternScore(), inferenceResult.signalScore(), inferenceResult.confidence());

            return Optional.of(inferenceResult);

        } catch (Exception e) {
            log.error("ONNX inference failed: {}", e.getMessage());
            return Optional.empty();
        }
    }

    /**
     * Parses ONNX model outputs into structured result.
     * Expected outputs: pattern_score, signal_score, confidence (each 0-100)
     */
    private OnnxInferenceResult parseOutputs(OrtSession.Result result) {
        try {
            // Get output tensor (assuming single output with 3 values)
            OnnxTensor outputTensor = (OnnxTensor) result.get(0);
            float[][] outputData = (float[][]) outputTensor.getValue();
            
            if (outputData.length > 0 && outputData[0].length >= 3) {
                float patternScore = outputData[0][0] * 100f;  // Model outputs 0-1, scale to 0-100
                float signalScore = outputData[0][1] * 100f;
                float confidence = outputData[0][2] * 100f;
                
                // Clamp to valid range
                patternScore = Math.max(0, Math.min(100, patternScore));
                signalScore = Math.max(0, Math.min(100, signalScore));
                confidence = Math.max(0, Math.min(100, confidence));

                // Determine regime from scores
                String regime = determineRegimeFromScores(patternScore, signalScore, confidence);

                return new OnnxInferenceResult(
                        java.math.BigDecimal.valueOf(patternScore),
                        java.math.BigDecimal.valueOf(signalScore),
                        java.math.BigDecimal.valueOf(confidence),
                        regime
                );
            }
        } catch (Exception e) {
            log.error("Failed to parse ONNX outputs: {}", e.getMessage());
        }
        return null;
    }

    private String determineRegimeFromScores(float pattern, float signal, float confidence) {
        if (confidence >= 70 && signal >= 60) return "STRONG_TREND";
        if (confidence >= 50 && signal >= 40) return "TRENDING";
        if (confidence < 30) return "AI_UNAVAILABLE";
        return "UNCERTAIN";
    }

    public boolean isModelLoaded() {
        return modelLoaded;
    }

    public String getModelInfo() {
        if (!modelLoaded) return "NOT_LOADED";
        try {
            return String.format("Model: %s, Inputs: %s, Outputs: %s", 
                    modelPath, ortSession.getInputInfo().keySet(), ortSession.getOutputInfo().keySet());
        } catch (Exception e) {
            return "ERROR: " + e.getMessage();
        }
    }

    /**
     * ONNX inference result container.
     */
    public record OnnxInferenceResult(
            java.math.BigDecimal patternScore,
            java.math.BigDecimal signalScore,
            java.math.BigDecimal confidence,
            String marketRegime
    ) {}
}