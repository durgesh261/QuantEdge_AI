package com.quantedge.account.controller;

import com.quantedge.account.service.AccountManagementService;
import com.quantedge.account.service.AccountManagementService.AccountStatusResponse;
import com.quantedge.account.service.AccountManagementService.AccountSummaryResponse;
import com.quantedge.account.service.AccountManagementService.ConnectAccountRequest;
import com.quantedge.account.service.AccountManagementService.ConnectAccountResponse;
import com.quantedge.auth.entity.User;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;

/**
 * Controller exposing authenticated REST endpoints for Delta Exchange account
 * credential management, live connection verification, and disconnection.
 *
 * <p>SECURITY INVARIANTS:
 * <ul>
 *   <li>All operations enforce user ownership derived from Spring Security Context.</li>
 *   <li>API secrets are never returned in responses, query parameters, or error messages.</li>
 *   <li>Credentials are verified against Delta India REST API before saving as CONNECTED.</li>
 * </ul>
 */
@RestController
@RequestMapping("/v1/settings/delta")
public class DeltaSettingsController {

    private final AccountManagementService accountService;

    public DeltaSettingsController(AccountManagementService accountService) {
        this.accountService = accountService;
    }

    public record DeltaConnectRequest(
            @NotBlank(message = "API Key is required")
            String apiKey,

            @NotBlank(message = "API Secret is required")
            String apiSecret
    ) {}

    public record DeltaSettingsDto(
            boolean connected,
            String status,
            String apiKeyMasked,
            String accountId,
            String accountName,
            String environment,
            Instant lastVerifiedAt,
            String lastError
    ) {}

    private User getEffectiveUser(User user, User requestUser) {
        User effective = user != null ? user : requestUser;
        if (effective == null || effective.getId() == null) {
            throw new AccessDeniedException("Authentication required to access exchange credentials.");
        }
        return effective;
    }

    @GetMapping
    public ResponseEntity<DeltaSettingsDto> getDeltaSettings(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser
    ) {
        User effectiveUser = getEffectiveUser(user, requestUser);
        AccountStatusResponse status = accountService.getAccountStatus(effectiveUser, null);

        DeltaSettingsDto dto = new DeltaSettingsDto(
                status.connected(),
                status.connectionStatus(),
                status.maskedApiKey(),
                status.accountId(),
                status.name(),
                status.environment() != null ? status.environment() : "LIVE",
                status.lastConnectedAt(),
                status.lastError()
        );

        return ResponseEntity.ok(dto);
    }

    @PostMapping("/connect")
    public ResponseEntity<DeltaSettingsDto> connectDelta(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @Valid @RequestBody DeltaConnectRequest request
    ) {
        User effectiveUser = getEffectiveUser(user, requestUser);

        ConnectAccountRequest req = new ConnectAccountRequest(
                null,
                "Delta Live Account",
                request.apiKey().trim(),
                request.apiSecret().trim()
        );

        ConnectAccountResponse response = accountService.connectAccount(effectiveUser, req);

        DeltaSettingsDto dto = new DeltaSettingsDto(
                response.success(),
                response.connectionStatus(),
                response.maskedApiKey(),
                response.accountId(),
                response.name(),
                "LIVE",
                response.lastConnectedAt(),
                response.error()
        );

        if (response.success()) {
            return ResponseEntity.ok(dto);
        } else {
            return ResponseEntity.badRequest().body(dto);
        }
    }

    @DeleteMapping
    public ResponseEntity<DeltaSettingsDto> disconnectDelta(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser
    ) {
        User effectiveUser = getEffectiveUser(user, requestUser);
        AccountStatusResponse status = accountService.disconnectAccount(effectiveUser, null);

        DeltaSettingsDto dto = new DeltaSettingsDto(
                false,
                "DISCONNECTED",
                null,
                status.accountId(),
                status.name(),
                "LIVE",
                null,
                null
        );

        return ResponseEntity.ok(dto);
    }

    @PostMapping("/test")
    public ResponseEntity<DeltaSettingsDto> testDeltaConnection(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser
    ) {
        User effectiveUser = getEffectiveUser(user, requestUser);
        AccountSummaryResponse summary = accountService.verifyConnection(effectiveUser, null);

        DeltaSettingsDto dto = new DeltaSettingsDto(
                summary.success(),
                summary.connectionStatus(),
                summary.maskedApiKey(),
                summary.accountId(),
                summary.name(),
                "LIVE",
                summary.lastSyncedAt(),
                summary.error()
        );

        if (summary.success()) {
            return ResponseEntity.ok(dto);
        } else {
            return ResponseEntity.badRequest().body(dto);
        }
    }
}
