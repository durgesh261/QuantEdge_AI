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
    private String modelArtifactHash = "UNKNOWN";

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

            // Read model bytes and compute SHA-256
            byte[] modelBytes = modelStream.readAllBytes();
            try {
                java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
                byte[] digest = md.digest(modelBytes);
                StringBuilder sb = new StringBuilder();
                for (byte b : digest) {
                    sb.append(String.format("%02x", b));
                }
                modelArtifactHash = sb.toString();
                log.info("ONNX Model SHA-256: {}", modelArtifactHash);
            } catch (Exception e) {
                log.warn("Failed to compute ONNX model SHA-256: {}", e.getMessage());
            }

            // Save to temp file for ONNX Runtime (requires file path)
            java.io.File tempModel = java.io.File.createTempFile("quantedge-ai-", ".onnx");
            tempModel.deleteOnExit();
            try (var os = new java.io.FileOutputStream(tempModel)) {
                os.write(modelBytes);
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
     * Runs raw 3-output regression inference on the loaded ONNX model.
     * Validates input length (exactly 24), checks for NaN/Infinity.
     * Returns Optional.empty() if model not loaded, inputs invalid, or inference fails.
     */
    public Optional<OnnxRegressionResult> runRegressionInference(float[] inputArray) {
        if (!modelLoaded || ortSession == null || inputArray == null) {
            return Optional.empty();
        }

        // Validate dimension
        if (inputArray.length != inputFeatureCount) {
            log.error("ONNX input dimension mismatch: expected {}, got {}", inputFeatureCount, inputArray.length);
            return Optional.empty();
        }

        // Validate finite values (no NaN / Infinity)
        for (int i = 0; i < inputArray.length; i++) {
            float v = inputArray[i];
            if (Float.isNaN(v) || Float.isInfinite(v)) {
                log.error("ONNX input contains invalid float at index {}: {}", i, v);
                return Optional.empty();
            }
        }

        try {
            long[] inputShape = new long[]{1, inputFeatureCount};
            OnnxTensor inputTensor = OnnxTensor.createTensor(ortEnvironment, FloatBuffer.wrap(inputArray), inputShape);

            Map<String, OnnxTensor> inputs = Collections.singletonMap(
                    ortSession.getInputInfo().keySet().iterator().next(), inputTensor);

            OrtSession.Result result = ortSession.run(inputs);
            OnnxTensor outputTensor = (OnnxTensor) result.get(0);
            float[][] outputData = (float[][]) outputTensor.getValue();

            if (outputData != null && outputData.length > 0 && outputData[0].length >= 3) {
                float realizedR = outputData[0][0];
                float mfeR = outputData[0][1];
                float maeR = outputData[0][2];

                return Optional.of(new OnnxRegressionResult(
                        java.math.BigDecimal.valueOf(realizedR),
                        java.math.BigDecimal.valueOf(mfeR),
                        java.math.BigDecimal.valueOf(maeR)
                ));
            }
            return Optional.empty();

        } catch (Exception e) {
            log.error("ONNX regression inference failed: {}", e.getMessage());
            return Optional.empty();
        }
    }

    /**
     * Runs inference on the loaded ONNX model using an AiFeatureVector.
     * Returns Optional.empty() if model not loaded or inference fails.
     */
    public Optional<OnnxInferenceResult> runInference(AiFeatureVector features) {
        if (!modelLoaded || ortSession == null) {
            return Optional.empty();
        }

        try {
            float[] inputArray = features.toFloat32Array();
            Optional<OnnxRegressionResult> regOpt = runRegressionInference(inputArray);
            if (regOpt.isEmpty()) {
                return Optional.empty();
            }

            OnnxRegressionResult reg = regOpt.get();
            // Map regression R-multiple to legacy pattern / signal scores for UI backward-compatibility
            float r = reg.predictedRealizedR().floatValue();
            float conf = Math.max(0f, Math.min(100f, (r + 1.0f) * 33.33f));
            float patternScore = Math.max(0f, Math.min(100f, 50f + r * 25f));
            float signalScore = Math.max(0f, Math.min(100f, 50f + r * 25f));
            String regime = r >= 0.5f ? "STRONG_TREND" : (r >= 0.0f ? "TRENDING" : "UNCERTAIN");

            return Optional.of(new OnnxInferenceResult(
                    java.math.BigDecimal.valueOf(patternScore),
                    java.math.BigDecimal.valueOf(signalScore),
                    java.math.BigDecimal.valueOf(conf),
                    regime
            ));

        } catch (Exception e) {
            log.error("ONNX inference failed: {}", e.getMessage());
            return Optional.empty();
        }
    }

    public boolean isModelLoaded() {
        return modelLoaded;
    }

    public String getModelArtifactHash() {
        return modelArtifactHash;
    }

    public String getModelInfo() {
        if (!modelLoaded) return "NOT_LOADED";
        try {
            return String.format("Model: %s, Hash: %s, Inputs: %s, Outputs: %s", 
                    modelPath, modelArtifactHash, ortSession.getInputInfo().keySet(), ortSession.getOutputInfo().keySet());
        } catch (Exception e) {
            return "ERROR: " + e.getMessage();
        }
    }

    /**
     * ONNX inference result container (legacy score mapping).
     */
    public record OnnxInferenceResult(
            java.math.BigDecimal patternScore,
            java.math.BigDecimal signalScore,
            java.math.BigDecimal confidence,
            String marketRegime
    ) {}

    /**
     * Raw ONNX regression result container matching the 3 trained targets.
     */
    public record OnnxRegressionResult(
            java.math.BigDecimal predictedRealizedR,
            java.math.BigDecimal predictedMfeR,
            java.math.BigDecimal predictedMaeR
    ) {}
}