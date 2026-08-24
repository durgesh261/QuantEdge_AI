package com.quantedge.account;

import com.quantedge.account.controller.DeltaSettingsController;
import com.quantedge.account.controller.DeltaSettingsController.DeltaConnectRequest;
import com.quantedge.account.controller.DeltaSettingsController.DeltaSettingsDto;
import com.quantedge.account.service.AccountManagementService;
import com.quantedge.account.service.AccountManagementService.*;
import com.quantedge.auth.entity.User;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

class DeltaSettingsControllerTest {

    private AccountManagementService accountService;
    private DeltaSettingsController controller;
    private User testUser;

    @BeforeEach
    void setUp() {
        accountService = Mockito.mock(AccountManagementService.class);
        controller = new DeltaSettingsController(accountService);

        testUser = new User("trader@quantedge.test", "hash", "Test Trader", true, true, Instant.now());
        testUser.setId("usr-test-uuid-12345");
    }

    @Test
    @DisplayName("1. GET /v1/settings/delta returns masked credentials and connection status")
    void testGetDeltaSettings() {
        Instant now = Instant.now();
        AccountStatusResponse status = new AccountStatusResponse(
                "acct-1", "Delta Live Account", true, "CONNECTED", "CONNECTED",
                "HEALTHY", "test***key1", "LIVE", now, now, now, 0, false, true, null
        );

        when(accountService.getAccountStatus(testUser, null)).thenReturn(status);

        ResponseEntity<DeltaSettingsDto> response = controller.getDeltaSettings(testUser, null);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertTrue(response.getBody().connected());
        assertEquals("CONNECTED", response.getBody().status());
        assertEquals("test***key1", response.getBody().apiKeyMasked());
        assertEquals("acct-1", response.getBody().accountId());
        assertEquals("LIVE", response.getBody().environment());
        assertEquals(now, response.getBody().lastVerifiedAt());
        assertNull(response.getBody().lastError());
    }

    @Test
    @DisplayName("2. POST /v1/settings/delta/connect with valid credentials returns 200 OK and CONNECTED")
    void testConnectDelta_Success() {
        Instant now = Instant.now();
        ConnectAccountResponse connectResponse = new ConnectAccountResponse(
                true, "acct-1", "Delta Live Account", "synt***0001", "CONNECTED",
                "CONNECTED", "HEALTHY", new BigDecimal("10000"), new BigDecimal("9000"),
                BigDecimal.ZERO, 0, 0, false, true, now, null
        );

        when(accountService.connectAccount(eq(testUser), any(ConnectAccountRequest.class))).thenReturn(connectResponse);

        DeltaConnectRequest request = new DeltaConnectRequest("synthetic_key_0001", "synthetic_secret_0001");
        ResponseEntity<DeltaSettingsDto> response = controller.connectDelta(testUser, null, request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertTrue(response.getBody().connected());
        assertEquals("CONNECTED", response.getBody().status());
        assertEquals("synt***0001", response.getBody().apiKeyMasked());
        assertEquals("acct-1", response.getBody().accountId());
    }

    @Test
    @DisplayName("3. POST /v1/settings/delta/connect with invalid credentials returns 400 Bad Request")
    void testConnectDelta_Failure() {
        ConnectAccountResponse connectResponse = new ConnectAccountResponse(
                false, "acct-1", "Delta Live Account", "synt***0001", "ERROR",
                "ERROR", "OFFLINE", BigDecimal.ZERO, BigDecimal.ZERO,
                BigDecimal.ZERO, 0, 0, false, true, null,
                "Failed to verify credentials with Delta Exchange India: 401 Unauthorized"
        );

        when(accountService.connectAccount(eq(testUser), any(ConnectAccountRequest.class))).thenReturn(connectResponse);

        DeltaConnectRequest request = new DeltaConnectRequest("invalid_key", "invalid_secret");
        ResponseEntity<DeltaSettingsDto> response = controller.connectDelta(testUser, null, request);

        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
        assertNotNull(response.getBody());
        assertFalse(response.getBody().connected());
        assertEquals("ERROR", response.getBody().status());
        assertTrue(response.getBody().lastError().contains("401 Unauthorized"));
    }

    @Test
    @DisplayName("4. DELETE /v1/settings/delta disconnects account and returns DISCONNECTED status")
    void testDisconnectDelta() {
        AccountStatusResponse status = new AccountStatusResponse(
                "acct-1", "Delta Live Account", false, "DISCONNECTED", "DISCONNECTED",
                "OFFLINE", null, "LIVE", null, null, null, 0, false, true, null
        );

        when(accountService.disconnectAccount(testUser, null)).thenReturn(status);

        ResponseEntity<DeltaSettingsDto> response = controller.disconnectDelta(testUser, null);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertFalse(response.getBody().connected());
        assertEquals("DISCONNECTED", response.getBody().status());
        assertNull(response.getBody().apiKeyMasked());
    }

    @Test
    @DisplayName("5. POST /v1/settings/delta/test verifies connection with Delta and returns 200 OK")
    void testTestDeltaConnection_Success() {
        Instant now = Instant.now();
        AccountSummaryResponse summary = new AccountSummaryResponse(
                true, "acct-1", "Delta Live Account", "CONNECTED", "CONNECTED",
                "HEALTHY", "synt***0001", new BigDecimal("10000"), new BigDecimal("9000"),
                BigDecimal.ZERO, "USDT", false, true, now, now,
                Collections.emptyList(), Collections.emptyList(), Collections.emptyList(), null
        );

        when(accountService.verifyConnection(testUser, null)).thenReturn(summary);

        ResponseEntity<DeltaSettingsDto> response = controller.testDeltaConnection(testUser, null);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertTrue(response.getBody().connected());
        assertEquals("CONNECTED", response.getBody().status());
        assertEquals("synt***0001", response.getBody().apiKeyMasked());
        assertEquals(now, response.getBody().lastVerifiedAt());
    }

    @Test
    @DisplayName("6. POST /v1/settings/delta/test returns 400 Bad Request when sync fails")
    void testTestDeltaConnection_Failure() {
        AccountSummaryResponse summary = new AccountSummaryResponse(
                false, "acct-1", "Delta Live Account", "ERROR", "ERROR",
                "DEGRADED", "synt***0001", BigDecimal.ZERO, BigDecimal.ZERO,
                BigDecimal.ZERO, "USDT", false, true, null, null,
                Collections.emptyList(), Collections.emptyList(), Collections.emptyList(),
                "Synchronization failed: Delta API timeout"
        );

        when(accountService.verifyConnection(testUser, null)).thenReturn(summary);

        ResponseEntity<DeltaSettingsDto> response = controller.testDeltaConnection(testUser, null);

        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
        assertNotNull(response.getBody());
        assertFalse(response.getBody().connected());
        assertEquals("ERROR", response.getBody().status());
        assertEquals("Synchronization failed: Delta API timeout", response.getBody().lastError());
    }

    @Test
    @DisplayName("7. Unauthenticated request throws AccessDeniedException")
    void testUnauthenticatedAccess_ThrowsAccessDenied() {
        assertThrows(AccessDeniedException.class, () -> controller.getDeltaSettings(null, null));
    }
}
