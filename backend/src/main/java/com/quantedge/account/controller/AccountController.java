package com.quantedge.account.controller;

import com.quantedge.account.service.AccountManagementService;
import com.quantedge.auth.entity.User;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/account")
public class AccountController {

    private final AccountManagementService accountService;

    public AccountController(AccountManagementService accountService) {
        this.accountService = accountService;
    }

    public record ConnectRequest(
            String accountId,
            String name,
            @NotBlank String apiKey,
            @NotBlank String apiSecret
    ) {}

    public record VerifyRequest(
            String accountId
    ) {}

    public record DisconnectRequest(
            String accountId
    ) {}

    @PostMapping("/connect")
    public ResponseEntity<AccountManagementService.ConnectAccountResponse> connectAccount(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @Valid @RequestBody ConnectRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AccountManagementService.ConnectAccountRequest req = new AccountManagementService.ConnectAccountRequest(
                request.accountId(),
                request.name(),
                request.apiKey(),
                request.apiSecret()
        );

        AccountManagementService.ConnectAccountResponse response = accountService.connectAccount(effectiveUser, req);
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }

    @PostMapping("/verify")
    public ResponseEntity<AccountManagementService.AccountSummaryResponse> verifyAccount(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody(required = false) VerifyRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String accountId = request != null ? request.accountId() : null;

        AccountManagementService.AccountSummaryResponse response = accountService.verifyConnection(effectiveUser, accountId);
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }

    @GetMapping("/status")
    public ResponseEntity<AccountManagementService.AccountStatusResponse> getAccountStatus(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AccountManagementService.AccountStatusResponse response = accountService.getAccountStatus(effectiveUser, accountId);
        return ResponseEntity.ok(response);
    }

    @GetMapping("/summary")
    public ResponseEntity<AccountManagementService.AccountSummaryResponse> getAccountSummary(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AccountManagementService.AccountSummaryResponse response = accountService.getAccountSummary(effectiveUser, accountId);
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }

    @PostMapping("/disconnect")
    public ResponseEntity<AccountManagementService.AccountStatusResponse> disconnectAccount(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody(required = false) DisconnectRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String accountId = request != null ? request.accountId() : null;

        AccountManagementService.AccountStatusResponse response = accountService.disconnectAccount(effectiveUser, accountId);
        return ResponseEntity.ok(response);
    }

    @GetMapping("/algo-config")
    public ResponseEntity<AccountManagementService.AlgoConfigResponse> getAlgoConfig(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AccountManagementService.AlgoConfigResponse response = accountService.getAlgoConfig(effectiveUser, accountId);
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }

    @PutMapping("/algo-config")
    public ResponseEntity<AccountManagementService.AlgoConfigResponse> updateAlgoConfig(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody AccountManagementService.UpdateAlgoConfigRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AccountManagementService.AlgoConfigResponse response = accountService.updateAlgoConfig(effectiveUser, request);
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }
}
