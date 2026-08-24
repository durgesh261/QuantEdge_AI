package com.quantedge.system.controller;

import com.quantedge.system.service.SystemDiagnosticsService;
import com.quantedge.system.service.SystemDiagnosticsService.SystemDiagnosticsResponse;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Controller exposing real-time system diagnostics and health metrics
 * for the authenticated User App and operations monitoring.
 */
@RestController
@RequestMapping("/v1/system")
public class SystemDiagnosticsController {

    private final SystemDiagnosticsService diagnosticsService;

    public SystemDiagnosticsController(SystemDiagnosticsService diagnosticsService) {
        this.diagnosticsService = diagnosticsService;
    }

    @GetMapping("/diagnostics")
    public ResponseEntity<SystemDiagnosticsResponse> getSystemDiagnostics() {
        return ResponseEntity.ok(diagnosticsService.getDiagnostics());
    }
}
