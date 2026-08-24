package com.quantedge.ai;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.ai.controller.AiIntelligenceController;
import com.quantedge.ai.controller.AiIntelligenceController.BulkIntelligenceRequest;
import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.service.AiDecisionAuditService;
import com.quantedge.ai.service.AiEnrichmentService;
import com.quantedge.account.service.AccountManagementService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@ExtendWith(MockitoExtension.class)
class SignalFilteringAndAiBulkTest {

    @Mock
    private AiEnrichmentService aiEnrichmentService;
    @Mock
    private AiDecisionAuditService auditService;
    @Mock
    private AccountManagementService accountManagementService;

    private MockMvc mockMvc;
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    @BeforeEach
    void setUp() {
        AiIntelligenceController controller = new AiIntelligenceController(aiEnrichmentService, auditService, accountManagementService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @Test
    @DisplayName("POST /v1/ai/enrichments/bulk should return mapped bulk enrichments")
    void shouldReturnBulkAiEnrichments() throws Exception {
        UUID setup1 = UUID.randomUUID();
        UUID setup2 = UUID.randomUUID();

        AiEnrichmentDto dto1 = new AiEnrichmentDto(
                UUID.randomUUID().toString(), setup1.toString(), "acc-1", "BTCUSD", "BUY",
                "1.0.0", new BigDecimal("0.88"), new BigDecimal("0.90"), new BigDecimal("0.85"),
                "TRENDING_BULLISH", "Strong institutional order flow", "v2-ml", "OB_RETEST,FVG", Instant.now()
        );

        AiEnrichmentDto dto2 = new AiEnrichmentDto(
                UUID.randomUUID().toString(), setup2.toString(), "acc-1", "ETHUSD", "SELL",
                "1.0.0", new BigDecimal("0.70"), new BigDecimal("0.75"), new BigDecimal("0.72"),
                "RANGING", "Liquidity sweep at resistance", "v2-ml", "SWEEP", Instant.now()
        );

        when(aiEnrichmentService.getBulkEnrichments(any(), any(), any()))
                .thenReturn(Map.of(setup1.toString(), dto1, setup2.toString(), dto2));

        BulkIntelligenceRequest request = new BulkIntelligenceRequest(List.of(setup1.toString(), setup2.toString()), null);

        mockMvc.perform(post("/v1/ai/enrichments/bulk")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$['" + setup1 + "'].confidence").value(0.85))
                .andExpect(jsonPath("$['" + setup2 + "'].confidence").value(0.72));
    }
}

