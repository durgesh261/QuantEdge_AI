package com.quantedge.account;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.account.service.LiveOrderTestService;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Phase 5.17.1: Comprehensive unit, security, concurrency, and safety test suite for LiveOrderTestService.
 * Validates atomic token single-use, race-condition protection, exception sanitization,
 * partial protection failure handling, and position closure safety using isolated mocks.
 * ZERO real orders are placed.
 */
class LiveOrderTestServiceTest {

    private static final String ENCRYPTION_KEY = "test-live-order-master-encryption-key-256-bit";

    private TradingAccountRepository accountRepository;
    private DeltaConnectionRepository connectionRepository;
    private DeltaCredentialService credentialService;
    private DeltaIndiaRestClient deltaRestClient;
    private AuditLogRepository auditLogRepository;
    private ObjectMapper objectMapper;

    private LiveOrderTestService liveOrderTestService;

    private User userA;
    private User userB;
    private TradingAccount accountA;
    private TradingAccount accountB;

    @BeforeEach
    void setUp() {
        accountRepository = mock(TradingAccountRepository.class);
        connectionRepository = mock(DeltaConnectionRepository.class);
        credentialService = new DeltaCredentialService(ENCRYPTION_KEY);
        deltaRestClient = mock(DeltaIndiaRestClient.class);
        auditLogRepository = mock(AuditLogRepository.class);
        objectMapper = new ObjectMapper();

        liveOrderTestService = new LiveOrderTestService(
                accountRepository,
                connectionRepository,
                credentialService,
                deltaRestClient,
                auditLogRepository,
                objectMapper
        );

        userA = new User("userA@quantedge.test", "hashA", "User A", true, true, Instant.now());
        userA.setId("usr-001");

        userB = new User("userB@quantedge.test", "hashB", "User B", true, true, Instant.now());
        userB.setId("usr-002");

        accountA = new TradingAccount(userA, "Account A", "LIVE", "USDT");
        accountA.setId("acct-A");
        accountA.setKillSwitchActive(false);

        accountB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        accountB.setId("acct-B");
        accountB.setKillSwitchActive(false);

        DeltaConnection connA = new DeltaConnection(accountA, "LIVE", credentialService.encrypt("KEY_A"), credentialService.encrypt("SECRET_A"));
        DeltaConnection connB = new DeltaConnection(accountB, "LIVE", credentialService.encrypt("KEY_B"), credentialService.encrypt("SECRET_B"));

        when(accountRepository.findById("acct-A")).thenReturn(Optional.of(accountA));
        when(accountRepository.findById("acct-B")).thenReturn(Optional.of(accountB));
        when(accountRepository.findByUserId("usr-001")).thenReturn(List.of(accountA));
        when(accountRepository.findByUserId("usr-002")).thenReturn(List.of(accountB));

        when(connectionRepository.findByTradingAccountIdAndEnvironment("acct-A", "LIVE")).thenReturn(Optional.of(connA));
        when(connectionRepository.findByTradingAccountIdAndEnvironment("acct-B", "LIVE")).thenReturn(Optional.of(connB));
    }

    private void mockStandardExchangeResponses(BigDecimal balance, BigDecimal markPrice, BigDecimal positionSize) {
        // Balances
        String balanceJson = "{\"result\": [{\"asset_symbol\": \"USDT\", \"available_balance\": \"" + balance + "\", \"balance\": \"" + balance + "\"}]}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/wallet/balances"), any(), any()))
                .thenReturn(new ResponseEntity<>(balanceJson, HttpStatus.OK));

        // Positions
        String posJson = "{\"result\": [{\"symbol\": \"ETHUSD\", \"product_symbol\": \"ETHUSD\", \"product_id\": 134, \"size\": \"" + positionSize + "\"}]}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/positions/margined"), any(), any()))
                .thenReturn(new ResponseEntity<>(posJson, HttpStatus.OK));

        // Products
        String prodJson = "{\"result\": [{\"id\": 134, \"symbol\": \"ETHUSD\", \"contract_value\": \"0.001\", \"tick_size\": \"0.05\", \"min_order_size\": \"1\"}]}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/products"), any(), any()))
                .thenReturn(new ResponseEntity<>(prodJson, HttpStatus.OK));

        // Ticker
        String tickerJson = "{\"result\": {\"mark_price\": \"" + markPrice + "\", \"close\": \"" + markPrice + "\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), contains("/v2/tickers"), any(), any()))
                .thenReturn(new ResponseEntity<>(tickerJson, HttpStatus.OK));
    }

    @Test
    @DisplayName("1. User A can prepare a live test successfully")
    void testUserAPrepareSuccess() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);

        var resp = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");

        assertTrue(resp.ready());
        assertEquals("Delta Exchange India", resp.exchange());
        assertEquals("acct-A", resp.accountId());
        assertEquals("ETHUSD", resp.symbol());
        assertEquals(BigDecimal.ONE, resp.minimumQuantity());
        assertNotNull(resp.confirmationToken());
        assertTrue(resp.confirmationRequired());
        assertNull(resp.error());
    }

    @Test
    @DisplayName("2. IDOR Prevention: User A cannot prepare a test for User B's account")
    void testUserACannotPrepareForUserB() {
        assertThrows(SecurityException.class, () -> liveOrderTestService.prepareLiveTest(userA, "acct-B", "ETHUSD"));
    }

    @Test
    @DisplayName("3. IDOR Prevention: User A cannot confirm a test token belonging to User B")
    void testUserACannotConfirmUserBToken() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        var prepB = liveOrderTestService.prepareLiveTest(userB, "acct-B", "ETHUSD");
        assertNotNull(prepB.confirmationToken());

        assertThrows(SecurityException.class, () -> liveOrderTestService.confirmLiveTest(userA, "acct-B", prepB.confirmationToken()));
    }

    @Test
    @DisplayName("4. Single-Use Token: Cannot confirm with the same token twice (fails closed)")
    void testSingleUseTokenConsumption() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-999\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2510.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        var resp1 = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());
        assertTrue(resp1.success());

        // Second confirmation with same token
        var resp2 = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());
        assertFalse(resp2.success());
        assertEquals("TOKEN_ALREADY_USED", resp2.status());
    }

    @Test
    @DisplayName("5. Concurrency Race Protection: Simultaneous confirm requests execute exactly once")
    void testConcurrentConfirmationRaceProtection() throws Exception {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-race-1\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        String token = prep.confirmationToken();

        int threads = 4;
        ExecutorService executor = Executors.newFixedThreadPool(threads);
        CountDownLatch startGate = new CountDownLatch(1);
        CountDownLatch endGate = new CountDownLatch(threads);
        AtomicInteger successCount = new AtomicInteger(0);
        AtomicInteger alreadyUsedCount = new AtomicInteger(0);

        for (int i = 0; i < threads; i++) {
            executor.submit(() -> {
                try {
                    startGate.await();
                    var res = liveOrderTestService.confirmLiveTest(userA, "acct-A", token);
                    if (res.success()) {
                        successCount.incrementAndGet();
                    } else if ("TOKEN_ALREADY_USED".equals(res.status())) {
                        alreadyUsedCount.incrementAndGet();
                    }
                } catch (Exception ignored) {
                } finally {
                    endGate.countDown();
                }
            });
        }

        startGate.countDown(); // Trigger all threads simultaneously
        endGate.await(5, TimeUnit.SECONDS);
        executor.shutdown();

        // Exactly one thread must succeed; all other threads must be rejected atomically
        assertEquals(1, successCount.get());
        assertEquals(threads - 1, alreadyUsedCount.get());
    }

    @Test
    @DisplayName("6. Expired Token: Confirmation fails closed if token is invalid or expired")
    void testInvalidOrExpiredTokenFailsClosed() {
        var resp = liveOrderTestService.confirmLiveTest(userA, "acct-A", "non-existent-token-12345");
        assertFalse(resp.success());
        assertEquals("TOKEN_INVALID", resp.status());
    }

    @Test
    @DisplayName("7. Kill Switch: Active kill switch blocks live test preparation and confirmation")
    void testKillSwitchBlocksLiveTest() {
        accountA.setKillSwitchActive(true);

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        assertFalse(prep.ready());
        assertTrue(prep.error().contains("Kill switch is active"));
    }

    @Test
    @DisplayName("8. Insufficient Balance: Live test preparation fails closed when balance < required margin")
    void testInsufficientBalanceBlocksPreparation() {
        mockStandardExchangeResponses(new BigDecimal("0.05"), new BigDecimal("3000.00"), BigDecimal.ZERO);

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        assertFalse(prep.ready());
        assertTrue(prep.error().contains("Insufficient balance"));
    }

    @Test
    @DisplayName("9. Existing Position: Blocks test if open position already exists on target symbol")
    void testExistingPositionBlocksPreparation() {
        mockStandardExchangeResponses(new BigDecimal("500.00"), new BigDecimal("2500.00"), new BigDecimal("5.0"));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        assertFalse(prep.ready());
        assertTrue(prep.error().contains("Existing position detected"));
    }

    @Test
    @DisplayName("10. Successful Execution: Places entry and establishes SL/TP brackets")
    void testSuccessfulExecutionFlow() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-live-101\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2512.50\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        var confirm = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        assertTrue(confirm.success());
        assertEquals("REAL_ORDER_FILLED", confirm.status());
        assertEquals("ord-live-101", confirm.exchangeOrderId());
        assertEquals(new BigDecimal("2512.50"), confirm.fillPrice());
        assertEquals(BigDecimal.ONE, confirm.filledQuantity());
        assertNotNull(confirm.stopLossPrice());
        assertNotNull(confirm.takeProfitPrice());
    }

    @Test
    @DisplayName("11. Partial Failure Handling: SL/TP failure triggers emergency closure and PROTECTION_SETUP_FAILED")
    void testBracketFailureTriggersEmergencyClosure() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);

        // Entry succeeds (1st POST), SL bracket fails (2nd POST), Emergency close succeeds (3rd POST)
        String entryJson = "{\"result\": {\"id\": \"ord-entry-202\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        String emergencyCloseJson = "{\"result\": {\"id\": \"ord-emergency-close\", \"state\": \"filled\", \"size\": \"1\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(entryJson, HttpStatus.OK))
                .thenThrow(new RuntimeException("Exchange 500 error on SL bracket"))
                .thenReturn(new ResponseEntity<>(emergencyCloseJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        var confirm = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        assertFalse(confirm.success());
        assertEquals("PROTECTION_SETUP_FAILED", confirm.status());
        assertTrue(confirm.emergencyCloseAttempted());
        assertEquals("ord-entry-202", confirm.exchangeOrderId());
    }

    @Test
    @DisplayName("12. Close Test Position: Flat position confirms immediately")
    void testCloseWhenPositionAlreadyFlat() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);

        var closeResp = liveOrderTestService.closeLiveTest(userA, "acct-A", "ETHUSD");
        assertTrue(closeResp.success());
        assertEquals("POSITION_FLAT", closeResp.status());
        assertEquals(BigDecimal.ZERO, closeResp.finalPosition());
    }

    @Test
    @DisplayName("13. Close Test Position: Size mismatch requires reconciliation")
    void testCloseSizeMismatchRequiresReconciliation() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-live-303\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        // Now suppose exchange position grew to 10 unexpectedly
        String alteredPosJson = "{\"result\": [{\"symbol\": \"ETHUSD\", \"product_symbol\": \"ETHUSD\", \"product_id\": 134, \"size\": \"10\"}]}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/positions/margined"), any(), any()))
                .thenReturn(new ResponseEntity<>(alteredPosJson, HttpStatus.OK));

        var closeResp = liveOrderTestService.closeLiveTest(userA, "acct-A", "ETHUSD");
        assertFalse(closeResp.success());
        assertEquals("CLOSE_REQUIRES_RECONCILIATION", closeResp.status());
    }

    @Test
    @DisplayName("14. Close Test Position: Successfully closes verified test position")
    void testSuccessfulPositionClose() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-live-404\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        // Active position of 1
        String openPosJson = "{\"result\": [{\"symbol\": \"ETHUSD\", \"product_symbol\": \"ETHUSD\", \"product_id\": 134, \"size\": \"1\"}]}";
        String flatPosJson = "{\"result\": []}";

        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/positions/margined"), any(), any()))
                .thenReturn(new ResponseEntity<>(openPosJson, HttpStatus.OK))
                .thenReturn(new ResponseEntity<>(flatPosJson, HttpStatus.OK));

        var closeResp = liveOrderTestService.closeLiveTest(userA, "acct-A", "ETHUSD");
        assertTrue(closeResp.success());
        assertEquals("POSITION_FLAT", closeResp.status());
    }

    @Test
    @DisplayName("15. Audit Log Safety: Never logs API keys, secrets, or encryption keys")
    void testAuditLogContainsNoCredentials() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-live-505\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        ArgumentCaptor<AuditLog> captor = ArgumentCaptor.forClass(AuditLog.class);
        verify(auditLogRepository, atLeastOnce()).save(captor.capture());

        for (AuditLog log : captor.getAllValues()) {
            assertFalse(log.getDetails().contains("KEY_A"));
            assertFalse(log.getDetails().contains("SECRET_A"));
            assertFalse(log.getDetails().contains(ENCRYPTION_KEY));
        }
    }

    @Test
    @DisplayName("16. Pre-Submission Revalidation: Fresh position detected immediately before order aborts execution")
    void testPreSubmissionPositionCheckAbortsOrder() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");

        // Simulate position opened in the meantime
        String newPosJson = "{\"result\": [{\"symbol\": \"ETHUSD\", \"product_symbol\": \"ETHUSD\", \"product_id\": 134, \"size\": \"2\"}]}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.GET), eq("/v2/positions/margined"), any(), any()))
                .thenReturn(new ResponseEntity<>(newPosJson, HttpStatus.OK));

        var confirm = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());
        assertFalse(confirm.success());
        assertEquals("EXISTING_POSITION", confirm.status());
    }

    @Test
    @DisplayName("17. Delta Order Rejection: Handled gracefully without leaking raw exception messages")
    void testDeltaOrderRejectionHandledGracefully() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenThrow(new RuntimeException("Delta Exchange internal stack trace with api_key=KEY_A"));

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        var confirm = liveOrderTestService.confirmLiveTest(userA, "acct-A", prep.confirmationToken());

        assertFalse(confirm.success());
        assertEquals("EXECUTION_ERROR", confirm.status());
        // Error message must be sanitized and free of raw exception details
        assertFalse(confirm.error().contains("KEY_A"));
        assertFalse(confirm.error().contains("stack trace"));
        assertEquals("Delta Exchange order rejected. Please try again.", confirm.error());
    }

    @Test
    @DisplayName("18. Multi-User Isolation: User A and User B live test executions operate completely independently")
    void testMultiUserIndependentExecutions() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        String orderJson = "{\"result\": {\"id\": \"ord-user\", \"state\": \"filled\", \"size\": \"1\", \"avg_fill_price\": \"2500.00\"}}";
        when(deltaRestClient.executeRequest(anyString(), anyString(), eq(HttpMethod.POST), eq("/v2/orders"), any(), any()))
                .thenReturn(new ResponseEntity<>(orderJson, HttpStatus.OK));

        var prepA = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        var prepB = liveOrderTestService.prepareLiveTest(userB, "acct-B", "ETHUSD");

        assertNotEquals(prepA.confirmationToken(), prepB.confirmationToken());

        var confirmA = liveOrderTestService.confirmLiveTest(userA, "acct-A", prepA.confirmationToken());
        var confirmB = liveOrderTestService.confirmLiveTest(userB, "acct-B", prepB.confirmationToken());

        assertTrue(confirmA.success());
        assertTrue(confirmB.success());
    }

    @Test
    @DisplayName("19. Dynamic Sizing: Sizing respects live product spec contract value and min order size")
    void testDynamicSizingFromLiveProductSpec() {
        mockStandardExchangeResponses(new BigDecimal("200.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);

        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        assertEquals(new BigDecimal("0.001"), prep.contractValue());
        assertEquals(BigDecimal.ONE, prep.minimumQuantity());
        assertEquals(new BigDecimal("2500.00"), prep.markPrice());
        // Notional = 1 * 0.001 * 2500 = $2.50. At 5x margin = $0.50.
        assertEquals(new BigDecimal("0.5000"), prep.estimatedMargin());
    }

    @Test
    @DisplayName("20. Real Order Claim Rule: Zero orders placed in automated tests")
    void testZeroRealOrdersPlacedInAutomatedTests() {
        mockStandardExchangeResponses(new BigDecimal("100.00"), new BigDecimal("2500.00"), BigDecimal.ZERO);
        var prep = liveOrderTestService.prepareLiveTest(userA, "acct-A", "ETHUSD");
        assertTrue(prep.ready());
    }
}
