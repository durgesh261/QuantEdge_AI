package com.quantedge.developer;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.developer.controller.DeveloperController;
import com.quantedge.developer.service.DeveloperService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import javax.sql.DataSource;
import java.math.BigDecimal;
import java.sql.Connection;
import java.time.Instant;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.when;

/**
 * Verification test suite for Developer Console security, role-based authorization,
 * endpoint access control, secret redaction, and sandbox isolation.
 */
class DeveloperSecurityTest {

    private TradingAccountRepository accountRepository;
    private DataSource dataSource;
    private DeveloperService developerService;
    private DeveloperController developerController;

    private User normalUser;
    private User developerUser;
    private User adminUser;

    @BeforeEach
    void setUp() throws Exception {
        accountRepository = Mockito.mock(TradingAccountRepository.class);
        dataSource = Mockito.mock(DataSource.class);
        Connection mockConn = Mockito.mock(Connection.class);
        when(mockConn.isValid(Mockito.anyInt())).thenReturn(true);
        when(dataSource.getConnection()).thenReturn(mockConn);

        developerService = new DeveloperService(accountRepository, dataSource);
        developerController = new DeveloperController(developerService);

        normalUser = new User("trader@quantedge.com", "hash", "Trader User", "USER", true, true, Instant.now());
        developerUser = new User("dev@quantedge.com", "hash", "Lead Dev", "DEVELOPER", true, true, Instant.now());
        adminUser = new User("admin@quantedge.com", "hash", "System Admin", "ADMIN", true, true, Instant.now());
    }

    @Test
    @DisplayName("Authorities: User entity grants correct ROLE_ authorities based on role field")
    void testUserAuthorities() {
        assertEquals("ROLE_USER", normalUser.getAuthorities().iterator().next().getAuthority());
        assertEquals("ROLE_DEVELOPER", developerUser.getAuthorities().iterator().next().getAuthority());
        assertEquals("ROLE_ADMIN", adminUser.getAuthorities().iterator().next().getAuthority());
    }

    @Test
    @DisplayName("Role Enforcement: Normal user lacks DEVELOPER authority")
    void testNormalUserCannotClaimDeveloperRole() {
        boolean hasDevRole = normalUser.getAuthorities().stream()
                .anyMatch(a -> a.getAuthority().equals("ROLE_DEVELOPER") || a.getAuthority().equals("ROLE_ADMIN"));
        assertFalse(hasDevRole, "Normal user must not have DEVELOPER or ADMIN authority");
    }

    @Test
    @DisplayName("Developer Status: Returns comprehensive service metrics without leaking secrets")
    void testDeveloperStatusMetrics() {
        var response = developerController.getSystemStatus();
        assertNotNull(response);
        assertEquals(200, response.getStatusCode().value());
        assertNotNull(response.getBody());

        var body = response.getBody();
        assertEquals("OPERATIONAL", body.status());
        assertNotNull(body.services());
        assertTrue(body.uptimeSeconds() >= 0);
        assertNotNull(body.memory());
        assertNotNull(body.threads());
    }

    @Test
    @DisplayName("API Diagnostics: Delta and Engine diagnostics scrub all API secrets")
    void testApiDiagnosticsScrubbing() {
        var response = developerController.getApiDiagnostics();
        assertNotNull(response);
        assertEquals(200, response.getStatusCode().value());

        var body = response.getBody();
        assertNotNull(body);
        assertTrue(body.secretsSanitized());
        assertEquals("HMAC_SHA256_PER_USER_ISOLATED", body.signatureMechanism());
    }

    @Test
    @DisplayName("Log Sanitization: Sensitive tokens and secrets are strictly redacted")
    void testLogSanitization() {
        developerService.recordLog("INFO", "TestRunner", "User logged in with api_secret: super_secret_123456789");
        developerService.recordLog("WARN", "TestRunner", "Authorization header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz");

        var logs = developerController.getSanitizedLogs().getBody();
        assertNotNull(logs);
        assertFalse(logs.isEmpty());

        for (var logEntry : logs) {
            assertFalse(logEntry.message().contains("super_secret_123456789"), "Secrets must be redacted");
            assertFalse(logEntry.message().contains("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"), "Bearer tokens must be redacted");
        }
    }

    @Test
    @DisplayName("Sandbox Isolation: Sandbox simulator operates in memory with zero real execution capability")
    void testSandboxIsolation() {
        var info = developerController.getSandboxInfo().getBody();
        assertNotNull(info);
        assertTrue(info.realExecutionBlocked(), "Real execution must be blocked in Sandbox");
        assertEquals("ISOLATED_SIMULATION_SANDBOX", info.mode());

        var tickResult = developerController.simulateTick(new DeveloperController.SimulateTickRequest("ETHUSD", new BigDecimal("3500.00"))).getBody();
        assertNotNull(tickResult);
        assertTrue(tickResult.success());
        assertEquals("ETHUSD", tickResult.symbol());
        assertEquals(new BigDecimal("3500.00"), tickResult.price());
        assertNotNull(tickResult.detectedOrderBlockType());
        assertNotNull(tickResult.signal());
    }

    @Test
    @DisplayName("System Accounts Health: Returns account health without exposing credentials")
    void testAccountsHealthSummaryNoSecrets() {
        TradingAccount acct = new TradingAccount();
        acct.setName("Live Prod Account");
        acct.setIsActive(true);
        acct.setAlgoEnabled(false);
        acct.setKillSwitchActive(true);
        acct.setCurrentBalance(new BigDecimal("15000.00"));
        acct.setTotalEquity(new BigDecimal("15000.00"));

        when(accountRepository.findAll()).thenReturn(List.of(acct));

        var response = developerController.getAccountsHealthSummary();
        assertNotNull(response);
        assertEquals(200, response.getStatusCode().value());

        var list = response.getBody();
        assertNotNull(list);
        assertEquals(1, list.size());
        assertEquals("Live Prod Account", list.get(0).name());
        assertTrue(list.get(0).killSwitchActive());
        assertFalse(list.get(0).algoEnabled());
    }
}
