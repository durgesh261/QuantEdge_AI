package com.quantedge.developer.controller;

import com.quantedge.developer.service.DeveloperService;
import com.quantedge.developer.service.DeveloperService.*;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.util.List;

/**
 * Controller exposing developer-only endpoints for system diagnostics,
 * service health monitoring, audit log viewing, and isolated strategy sandboxing.
 *
 * <p>SECURITY: Strict server-side enforcement. Only authenticated users with
 * ROLE_DEVELOPER or ROLE_ADMIN are permitted. Regular trading users (ROLE_USER)
 * receive HTTP 403 Forbidden.
 */
@RestController
@RequestMapping("/api/v1/developer")
@PreAuthorize("hasAnyRole('DEVELOPER', 'ADMIN')")
public class DeveloperController {

    private final DeveloperService developerService;

    public DeveloperController(DeveloperService developerService) {
        this.developerService = developerService;
    }

    public record SimulateTickRequest(
            String symbol,
            BigDecimal price
    ) {}

    @GetMapping("/status")
    public ResponseEntity<DeveloperStatusResponse> getSystemStatus() {
        return ResponseEntity.ok(developerService.getSystemStatus());
    }

    @GetMapping("/diagnostics")
    public ResponseEntity<ApiDiagnosticsResponse> getApiDiagnostics() {
        return ResponseEntity.ok(developerService.getApiDiagnostics());
    }

    @GetMapping("/logs")
    public ResponseEntity<List<LogEntry>> getSanitizedLogs() {
        return ResponseEntity.ok(developerService.getSanitizedLogs());
    }

    @GetMapping("/sandbox/info")
    public ResponseEntity<SandboxInfoResponse> getSandboxInfo() {
        return ResponseEntity.ok(developerService.getSandboxInfo());
    }

    @PostMapping("/sandbox/simulate-tick")
    public ResponseEntity<SimulatedTickResult> simulateTick(@RequestBody(required = false) SimulateTickRequest request) {
        String symbol = request != null ? request.symbol() : "BTCUSD";
        BigDecimal price = request != null ? request.price() : new BigDecimal("65000.00");
        return ResponseEntity.ok(developerService.simulateTick(symbol, price));
    }

    @GetMapping("/system/accounts")
    public ResponseEntity<List<AccountHealthSummary>> getAccountsHealthSummary() {
        return ResponseEntity.ok(developerService.getAccountsHealthSummary());
    }
}
