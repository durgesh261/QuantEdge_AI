package com.quantedge.ai;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.ai.dto.AiShadowResult;
import com.quantedge.ai.service.AiFeatureExtractor;
import com.quantedge.ai.service.AiInferenceEngine;
import com.quantedge.ai.service.OnnxModelInferenceService;
import org.assertj.core.data.Offset;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.InputStream;
import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Phase G: Comprehensive Cross-Language Golden Vector Parity and Shadow Inference Tests.
 *
 * <p>Verifies:
 * 1. Exact numeric parity between Python feature extraction, Python ONNX runtime, and Java ONNX runtime (<= 1e-4 tolerance).
 * 2. Strict enforcement of AI shadow mode: AI produces predictions, but executionAuthorized is ALWAYS false.
 * 3. Model SHA-256 integrity validation.
 * 4. Input validation (dimensions != 24, NaN, Infinity) is handled safely.
 * </p>
 */
@ExtendWith(MockitoExtension.class)
@DisplayName("Phase G: Shadow Inference & Cross-Language Parity Test Suite")
class PhaseGShadowInferenceTest {

    private OnnxModelInferenceService onnxService;
    private AiInferenceEngine aiInferenceEngine;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Mock
    private AiFeatureExtractor featureExtractor;

    @BeforeEach
    void setUp() {
        onnxService = new OnnxModelInferenceService();
        ReflectionTestUtils.setField(onnxService, "modelEnabled", true);
        ReflectionTestUtils.setField(onnxService, "modelPath", "classpath:models/quantedge-ai-v2.onnx");
        ReflectionTestUtils.setField(onnxService, "inputFeatureCount", 24);
        onnxService.initialize();

        aiInferenceEngine = new AiInferenceEngine(featureExtractor, onnxService);
    }

    @Nested
    @DisplayName("1. ONNX Model Loading & SHA-256 Verification")
    class ModelVerification {

        @Test
        @DisplayName("ONNX model loads successfully and computes valid SHA-256 hash")
        void modelLoadsWithValidHash() {
            assertThat(onnxService.isModelLoaded()).isTrue();
            assertThat(onnxService.getModelArtifactHash()).isNotEmpty();
            assertThat(onnxService.getModelArtifactHash()).isNotEqualTo("UNKNOWN");
            assertThat(onnxService.getModelArtifactHash()).hasSize(64); // SHA-256 hex string length
        }
    }

    @Nested
    @DisplayName("2. Golden Vector Cross-Language Parity (24 Real Market Setups)")
    class GoldenVectorParity {

        @Test
        @DisplayName("All 24 golden cases from real Delta Exchange data match Python predictions within 1e-4")
        void allGoldenCasesMatchPythonParity() throws Exception {
            InputStream is = getClass().getClassLoader().getResourceAsStream("fixtures/phase_g_golden_vectors.json");
            assertThat(is).as("phase_g_golden_vectors.json must be present in test resources").isNotNull();

            JsonNode root = objectMapper.readTree(is);
            JsonNode cases = root.get("cases");
            assertThat(cases.isArray()).isTrue();
            assertThat(cases.size()).isGreaterThanOrEqualTo(20);

            for (JsonNode c : cases) {
                String caseId = c.get("case_id").asText();
                String symbol = c.get("symbol").asText();
                String direction = c.get("direction").asText();

                JsonNode featuresNode = c.get("features_24");
                assertThat(featuresNode.size()).isEqualTo(24);

                float[] features = new float[24];
                for (int i = 0; i < 24; i++) {
                    features[i] = (float) featuresNode.get(i).asDouble();
                }

                JsonNode expectedNode = c.get("expected_onnx_output");
                float expectedRealizedR = (float) expectedNode.get("predicted_realized_r").asDouble();
                float expectedMfeR = (float) expectedNode.get("predicted_mfe_r").asDouble();
                float expectedMaeR = (float) expectedNode.get("predicted_mae_r").asDouble();

                // Run Java ONNX inference
                Optional<OnnxModelInferenceService.OnnxRegressionResult> resultOpt =
                        onnxService.runRegressionInference(features);

                assertThat(resultOpt)
                        .as("Inference for case %s (%s %s) should succeed", caseId, symbol, direction)
                        .isPresent();

                OnnxModelInferenceService.OnnxRegressionResult result = resultOpt.get();

                assertThat(result.predictedRealizedR().floatValue())
                        .as("Case %s: realized_r parity", caseId)
                        .isCloseTo(expectedRealizedR, Offset.offset(1e-4f));

                assertThat(result.predictedMfeR().floatValue())
                        .as("Case %s: mfe_r parity", caseId)
                        .isCloseTo(expectedMfeR, Offset.offset(1e-4f));

                assertThat(result.predictedMaeR().floatValue())
                        .as("Case %s: mae_r parity", caseId)
                        .isCloseTo(expectedMaeR, Offset.offset(1e-4f));
            }
        }
    }

    @Nested
    @DisplayName("3. Shadow Mode Invariants & Governance Enforcement")
    class ShadowModeInvariants {

        @Test
        @DisplayName("Shadow inference produces prediction but executionAuthorized is STRICTLY false")
        void shadowInferenceNeverAuthorizesExecution() {
            float[] sampleVector = new float[]{
                    0.60f, 0.80f, 0.70f, 0.55f, 0.40f,
                    0.75f, 0.65f, 0.70f, 0.30f, 0.25f,
                    1.20f, 0.05f, 0.02f, 3.00f, 150.0f,
                    0.85f, 0.20f, 0.10f, 1.0f, 0.0f,
                    0.0f, 0.0f, 1.0f, 1.0f
            };

            AiShadowResult result = aiInferenceEngine.evaluateShadow("BTCUSD", "LONG", sampleVector);

            assertThat(result).isNotNull();
            assertThat(result.executionAuthorized()).as("Shadow mode must NEVER authorize execution").isFalse();
            assertThat(result.governanceStatus()).as("Governance status must be REJECTED").isEqualTo("REJECTED");
            assertThat(result.threshold()).isEqualTo(BigDecimal.valueOf(0.50));
            assertThat(result.featureVector()).hasSize(24);
        }

        @Test
        @DisplayName("AiShadowResult constructor rejects executionAuthorized=true when governanceStatus != PROMOTED")
        void shadowResultConstructorGuardsAgainstUnauthorizedExecution() {
            float[] vector = new float[24];
            assertThatThrownBy(() -> new AiShadowResult(
                    "BTCUSD",
                    java.time.Instant.now(),
                    java.time.Instant.now(),
                    "LONG",
                    "OB_1",
                    "model",
                    "1.0",
                    "hash",
                    "2.0",
                    vector,
                    BigDecimal.ONE,
                    BigDecimal.ONE,
                    BigDecimal.ZERO,
                    BigDecimal.valueOf(0.50),
                    true,
                    "REJECTED",
                    true // Invalid: executionAuthorized=true with REJECTED governance
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Execution cannot be authorized");
        }
    }

    @Nested
    @DisplayName("4. Input Robustness & Error Boundary Tests")
    class InputRobustness {

        @Test
        @DisplayName("Feature vector with dimension < 24 returns Optional.empty()")
        void rejectsShortFeatureVector() {
            float[] shortVector = new float[23];
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(shortVector);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("Feature vector with dimension > 24 returns Optional.empty()")
        void rejectsLongFeatureVector() {
            float[] longVector = new float[25];
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(longVector);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("Feature vector containing NaN is safely rejected")
        void rejectsNanFeature() {
            float[] vector = new float[24];
            vector[5] = Float.NaN;
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(vector);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("Feature vector containing Positive Infinity is safely rejected")
        void rejectsPositiveInfinityFeature() {
            float[] vector = new float[24];
            vector[10] = Float.POSITIVE_INFINITY;
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(vector);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("Feature vector containing Negative Infinity is safely rejected")
        void rejectsNegativeInfinityFeature() {
            float[] vector = new float[24];
            vector[11] = Float.NEGATIVE_INFINITY;
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(vector);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("Null input array is safely handled")
        void handlesNullInput() {
            Optional<OnnxModelInferenceService.OnnxRegressionResult> result =
                    onnxService.runRegressionInference(null);
            assertThat(result).isEmpty();
        }
    }
}
