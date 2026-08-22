package com.quantedge.account;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.account.service.AccountManagementService;
import com.quantedge.account.service.LiveAccountSyncService;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.portfolio.repository.PositionRepository;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.repository.OrderRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * Verification test suite for per-user credential isolation, multi-tenant safety,
 * and IDOR authorization boundary checks in AccountManagementService.
 */
class PerUserCredentialIsolationTest {

    private static final String MASTER_ENCRYPTION_KEY = "test-per-user-isolation-master-encryption-key-256-bits";

    private TradingAccountRepository accountRepository;
    private DeltaConnectionRepository connectionRepository;
    private PositionRepository positionRepository;
    private OrderRepository orderRepository;
    private AuditLogRepository auditLogRepository;
    private DeltaCredentialService credentialService;
    private LiveAccountSyncService syncService;
    private RiskConfigurationRepository riskConfigRepository;
    private StrategySetupRepository strategySetupRepository;

    private AccountManagementService accountManagementService;

    private User userA;
    private User userB;

    @BeforeEach
    void setUp() {
        accountRepository = Mockito.mock(TradingAccountRepository.class);
        connectionRepository = Mockito.mock(DeltaConnectionRepository.class);
        positionRepository = Mockito.mock(PositionRepository.class);
        orderRepository = Mockito.mock(OrderRepository.class);
        auditLogRepository = Mockito.mock(AuditLogRepository.class);
        credentialService = new DeltaCredentialService(MASTER_ENCRYPTION_KEY);
        syncService = Mockito.mock(LiveAccountSyncService.class);
        riskConfigRepository = Mockito.mock(RiskConfigurationRepository.class);
        strategySetupRepository = Mockito.mock(StrategySetupRepository.class);

        accountManagementService = new AccountManagementService(
                accountRepository,
                connectionRepository,
                positionRepository,
                orderRepository,
                auditLogRepository,
                credentialService,
                syncService,
                riskConfigRepository,
                strategySetupRepository
        );

        userA = new User("userA@quantedge.test", "hashA", "User A", true, true, Instant.now());
        userA.setId("usr-uuid-00000000000000000001");

        userB = new User("userB@quantedge.test", "hashB", "User B", true, true, Instant.now());
        userB.setId("usr-uuid-00000000000000000002");
    }

    @Test
    @DisplayName("1. User Isolation: Two users with distinct API credentials remain strictly isolated")
    void testPerUserCredentialIsolation() {
        String keyA = "SYNTHETIC_API_KEY_USER_A_00000000001";
        String secretA = "SYNTHETIC_API_SECRET_USER_A_0000000000000000000000000001";

        String keyB = "SYNTHETIC_API_KEY_USER_B_00000000002";
        String secretB = "SYNTHETIC_API_SECRET_USER_B_0000000000000000000000000002";

        TradingAccount acctA = new TradingAccount(userA, "Account A", "LIVE", "USDT");
        acctA.setId("acct-A-100");

        TradingAccount acctB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        acctB.setId("acct-B-200");

        when(accountRepository.findByUserId(userA.getId())).thenReturn(List.of(acctA));
        when(accountRepository.findByUserId(userB.getId())).thenReturn(List.of(acctB));
        when(accountRepository.findById("acct-A-100")).thenReturn(Optional.of(acctA));
        when(accountRepository.findById("acct-B-200")).thenReturn(Optional.of(acctB));

        when(connectionRepository.findByTradingAccountIdAndEnvironment("acct-A-100", "LIVE")).thenReturn(Optional.empty());
        when(connectionRepository.findByTradingAccountIdAndEnvironment("acct-B-200", "LIVE")).thenReturn(Optional.empty());
        when(connectionRepository.save(any(DeltaConnection.class))).thenAnswer(i -> i.getArgument(0));
        when(accountRepository.save(any(TradingAccount.class))).thenAnswer(i -> i.getArgument(0));

        when(syncService.syncLiveAccount(eq("acct-A-100"), any(), any()))
                .thenReturn(new LiveAccountSyncService.SyncSummary(
                        true, Instant.now(), "acct-A-100", new BigDecimal("5000.00"), new BigDecimal("4500.00"),
                        new BigDecimal("500.00"), 1, 0, Collections.emptyList(), Collections.emptyList(), Collections.emptyList(), Collections.emptyList(), null
                ));

        when(syncService.syncLiveAccount(eq("acct-B-200"), any(), any()))
                .thenReturn(new LiveAccountSyncService.SyncSummary(
                        true, Instant.now(), "acct-B-200", new BigDecimal("12000.00"), new BigDecimal("10000.00"),
                        new BigDecimal("2000.00"), 2, 1, Collections.emptyList(), Collections.emptyList(), Collections.emptyList(), Collections.emptyList(), null
                ));

        // Connect User A
        var reqA = new AccountManagementService.ConnectAccountRequest("acct-A-100", "Account A", keyA, secretA);
        var respA = accountManagementService.connectAccount(userA, reqA);
        assertTrue(respA.success());
        assertEquals("SYNT***0001", respA.maskedApiKey());

        // Connect User B
        var reqB = new AccountManagementService.ConnectAccountRequest("acct-B-200", "Account B", keyB, secretB);
        var respB = accountManagementService.connectAccount(userB, reqB);
        assertTrue(respB.success());
        assertEquals("SYNT***0002", respB.maskedApiKey());
    }

    @Test
    @DisplayName("2. IDOR Prevention: User A cannot verify, update, or disconnect User B's account")
    void testIDORCrossUserAccessPrevention() {
        TradingAccount acctB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        acctB.setId("acct-B-200");

        when(accountRepository.findById("acct-B-200")).thenReturn(Optional.of(acctB));

        // User A attempts to verify connection on User B's account ID
        assertThrows(SecurityException.class, () -> accountManagementService.verifyConnection(userA, "acct-B-200"));

        // User A attempts to disconnect User B's account ID
        assertThrows(SecurityException.class, () -> accountManagementService.disconnectAccount(userA, "acct-B-200"));

        // User A attempts to connect credentials to User B's account ID
        var maliciousReq = new AccountManagementService.ConnectAccountRequest("acct-B-200", "Malicious Attach", "KEY", "SECRET");
        assertThrows(SecurityException.class, () -> accountManagementService.connectAccount(userA, maliciousReq));
    }

    @Test
    @DisplayName("3. Encryption Decryption Verification: Stored credentials decrypt to individual owner's plaintext")
    void testDecryptionMatchesOriginalPerUser() {
        String keyA = "SYNTHETIC_API_KEY_USER_A_00000000001";
        String secretA = "SYNTHETIC_API_SECRET_USER_A_0000000000000000000000000001";

        String keyB = "SYNTHETIC_API_KEY_USER_B_00000000002";
        String secretB = "SYNTHETIC_API_SECRET_USER_B_0000000000000000000000000002";

        String encKeyA = credentialService.encrypt(keyA);
        String encSecretA = credentialService.encrypt(secretA);

        String encKeyB = credentialService.encrypt(keyB);
        String encSecretB = credentialService.encrypt(secretB);

        // Verify independent decryption
        assertEquals(keyA, credentialService.decrypt(encKeyA));
        assertEquals(secretA, credentialService.decrypt(encSecretA));

        assertEquals(keyB, credentialService.decrypt(encKeyB));
        assertEquals(secretB, credentialService.decrypt(encSecretB));

        // Verify ciphertexts do not match between users
        assertNotEquals(encKeyA, encKeyB);
        assertNotEquals(encSecretA, encSecretB);
    }
}
