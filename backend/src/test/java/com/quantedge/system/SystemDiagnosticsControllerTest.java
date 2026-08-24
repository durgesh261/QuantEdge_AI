package com.quantedge.system;

import com.quantedge.system.controller.SystemDiagnosticsController;
import com.quantedge.system.service.SystemDiagnosticsService;
import com.quantedge.system.service.SystemDiagnosticsService.ComponentHealth;
import com.quantedge.system.service.SystemDiagnosticsService.SystemDiagnosticsResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Instant;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@ExtendWith(MockitoExtension.class)
class SystemDiagnosticsControllerTest {

    @Mock
    private SystemDiagnosticsService systemDiagnosticsService;

    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        SystemDiagnosticsController controller = new SystemDiagnosticsController(systemDiagnosticsService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @Test
    @DisplayName("GET /v1/system/diagnostics should return system diagnostics payload")
    void shouldReturnSystemDiagnostics() throws Exception {
        Instant now = Instant.now();
        SystemDiagnosticsResponse dto = new SystemDiagnosticsResponse(
                "HEALTHY",
                "2.0.0-SNAPSHOT",
                1200L,
                now,
                new ComponentHealth("Spring Boot REST API", "ONLINE", 12L, "JVM active", now),
                new ComponentHealth("PostgreSQL Database", "ONLINE", 5L, "PostgreSQL responsive", now),
                new ComponentHealth("Delta Exchange India (DELTAIN)", "ONLINE", 25L, "Delta India REST live", now),
                new ComponentHealth("Python SMC Engine", "ONLINE", 8L, "Python SMC deterministic engine live", now),
                new ComponentHealth("AI Enrichment Layer", "ONLINE", 0L, "AI enrichment active: 4 setups evaluated", now),
                new ComponentHealth("Financial News Ingestion", "ONLINE", 0L, "News Ingestion operational", now),
                new ComponentHealth("Macroeconomic Calendar Ingestion", "ONLINE", 0L, "Macro Calendar operational", now),
                1L,
                4L
        );

        when(systemDiagnosticsService.getDiagnostics()).thenReturn(dto);

        mockMvc.perform(get("/v1/system/diagnostics")
                        .contentType(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.overallStatus").value("HEALTHY"))
                .andExpect(jsonPath("$.api.status").value("ONLINE"))
                .andExpect(jsonPath("$.database.status").value("ONLINE"))
                .andExpect(jsonPath("$.deltaExchange.status").value("ONLINE"))
                .andExpect(jsonPath("$.pythonEngine.status").value("ONLINE"))
                .andExpect(jsonPath("$.aiEngine.status").value("ONLINE"))
                .andExpect(jsonPath("$.buildVersion").value("2.0.0-SNAPSHOT"));
    }
}

