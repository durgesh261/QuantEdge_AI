package com.quantedge.ai;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.ai.controller.AiIntelligenceController;
import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.service.AiEnrichmentService;
import com.quantedge.auth.entity.User;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 7.5: AI Intelligence Architecture & Security Tests")
class AiIntelligenceArchitectureTest {

    @Mock private AiEnrichmentService enrichmentService;

    private MockMvc mockMvc;
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    private User testUser;

    @BeforeEach
    void setUp() {
        AiIntelligenceController controller = new AiIntelligenceController(enrichmentService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new com.quantedge.common.exception.GlobalExceptionHandler())
                .build();

        testUser = new User();
        testUser.setId("user-auth-123");
        testUser.setEmail("auth@quantedge.io");
    }

    @Nested
    @DisplayName("Architectural Boundary & Invariants")
    class InvariantTests {

        @Test
        @DisplayName("AI module packages have zero dependencies on exchange execution clients")
        void aiModuleHasNoExecutionDependencies() {
            Class<?>[] aiClasses = new Class<?>[]{
                    com.quantedge.ai.entity.AiSignalEnrichment.class,
                    com.quantedge.ai.service.AiIntelligenceEngine.class,
                    com.quantedge.ai.service.DeterministicBaselineIntelligenceEngine.class,
                    com.quantedge.ai.service.AiEnrichmentService.class,
                    com.quantedge.ai.controller.AiIntelligenceController.class,
                    com.quantedge.ai.dto.AiEnrichmentDto.class
            };

            for (Class<?> clazz : aiClasses) {
                Field[] fields = clazz.getDeclaredFields();
                for (Field field : fields) {
                    String typeName = field.getType().getName();
                    assertThat(typeName)
                            .withFailMessage("AI class %s has forbidden dependency on %s", clazz.getSimpleName(), typeName)
                            .doesNotContain("DeltaIndiaRestClient")
                            .doesNotContain("DeltaExchangeClient");
                }
            }
        }
    }

    @Nested
    @DisplayName("Controller Endpoints & Security")
    class ControllerSecurityTests {

        @Test
        @DisplayName("GET /api/v1/trade/signals/{setupId}/intelligence returns 200 OK for owner")
        void returnsIntelligenceDto() throws Exception {
            AiEnrichmentDto dto = new AiEnrichmentDto(
                    "ai-enrich-1", "setup-det-100", "acct-1", "BTCUSD", "LONG",
                    "1.0.0-baseline", new BigDecimal("85.00"), new BigDecimal("82.00"),
                    new BigDecimal("78.50"), "BULLISH_TRENDING", "FAVORABLE_TREND_CONTINUATION",
                    "{\"engine\":\"baseline\"}", "{\"rr\":\"3.0\"}", Instant.now()
            );

            when(enrichmentService.getEnrichmentBySetupId(any(), eq("setup-det-100"), eq("acct-1")))
                    .thenReturn(dto);

            mockMvc.perform(get("/v1/trade/signals/setup-det-100/intelligence")
                            .param("accountId", "acct-1")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.setupId").value("setup-det-100"))
                    .andExpect(jsonPath("$.patternScore").value(85.00))
                    .andExpect(jsonPath("$.marketRegime").value("BULLISH_TRENDING"));
        }

        @Test
        @DisplayName("Cross-tenant IDOR access throws 403 Forbidden")
        void idorAccessBlocked() throws Exception {
            when(enrichmentService.getEnrichmentBySetupId(any(), eq("setup-det-100"), eq("acct-attacker")))
                    .thenThrow(new AccessDeniedException("Access denied: You do not own trading account acct-attacker"));

            mockMvc.perform(get("/v1/trade/signals/setup-det-100/intelligence")
                            .param("accountId", "acct-attacker")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isForbidden());
        }

        @Test
        @DisplayName("AI intelligence responses never leak secrets or passwords")
        void responsesDoNotLeakSecrets() throws Exception {
            AiEnrichmentDto dto = new AiEnrichmentDto(
                    "ai-enrich-1", "setup-det-100", "acct-1", "BTCUSD", "LONG",
                    "1.0.0-baseline", new BigDecimal("85.00"), new BigDecimal("82.00"),
                    new BigDecimal("78.50"), "BULLISH_TRENDING", "FAVORABLE_TREND_CONTINUATION",
                    "{\"engine\":\"baseline\"}", "{\"rr\":\"3.0\"}", Instant.now()
            );

            when(enrichmentService.getEnrichmentBySetupId(any(), any(), any()))
                    .thenReturn(dto);

            mockMvc.perform(get("/v1/trade/signals/setup-det-100/intelligence")
                            .requestAttr("currentUser", testUser)
                            .accept(MediaType.APPLICATION_JSON))
                    .andExpect(status().isOk())
                    .andExpect(content().string(not(containsString("password"))))
                    .andExpect(content().string(not(containsString("apiSecret"))))
                    .andExpect(content().string(not(containsString("encryptedApiKey"))));
        }
    }
}
