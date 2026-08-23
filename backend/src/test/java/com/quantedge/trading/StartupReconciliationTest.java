package com.quantedge.trading;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.client.DeltaIndiaRestClient;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.OrderRepository;
import com.quantedge.trading.service.StartupReconciliationService;
import com.quantedge.trading.service.StartupReconciliationService.ReconciliationReport;
import com.quantedge.trading.service.TradePersistenceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 6: Startup Reconciliation & Crash Recovery Tests")
class StartupReconciliationTest {

    @Mock private OrderRepository orderRepository;
    @Mock private PositionRepository positionRepository;
    @Mock private ActiveTradeLockRepository lockRepository;
    @Mock private TradingAccountRepository accountRepository;
    @Mock private DeltaConnectionRepository deltaConnectionRepository;
    @Mock private DeltaCredentialService credentialService;
    @Mock private DeltaIndiaRestClient deltaRestClient;
    @Mock private TradePersistenceService tradePersistenceService;

    private StartupReconciliationService service;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private TradingAccount testAccount;

    @BeforeEach
    void setUp() {
        service = new StartupReconciliationService(
                orderRepository,
                positionRepository,
                lockRepository,
                accountRepository,
                deltaConnectionRepository,
                credentialService,
                deltaRestClient,
                tradePersistenceService,
                objectMapper
        );

        User user = new User();
        user.setId("usr-rec-1");
        testAccount = new TradingAccount(user, "Recon Account", "LIVE", "USDT");
        testAccount.setId("acct-rec-1");
    }

    @Test
    @DisplayName("Startup reconciliation recovers SUBMISSION_PENDING order found open on exchange")
    void recoversOrderFoundOnExchange() {
        when(accountRepository.findAll()).thenReturn(List.of(testAccount));

        DeltaConnection conn = new DeltaConnection(testAccount, "LIVE", "enc-key", "enc-sec");
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-rec-1", "LIVE"))
                .thenReturn(Optional.of(conn));
        when(credentialService.decrypt("enc-key")).thenReturn("api-key");
        when(credentialService.decrypt("enc-sec")).thenReturn("api-sec");

        Order pendingOrder = new Order(testAccount, "setup-rec-1", "client-rec-101", "BTCUSD", "BUY", "LIMIT", BigDecimal.ONE, new BigDecimal("60000.00"));
        pendingOrder.setStatus(OrderStatus.SUBMISSION_PENDING.name());
        when(orderRepository.findByTradingAccountIdAndStatusIn(eq("acct-rec-1"), anyCollection()))
                .thenReturn(List.of(pendingOrder));

        // Mock Delta open orders returning the order
        String deltaOrdersJson = """
                {
                    "success": true,
                    "result": [
                        {
                            "id": "delta-ord-999",
                            "client_order_id": "client-rec-101",
                            "product_symbol": "BTCUSD",
                            "state": "open"
                        }
                    ]
                }
                """;
        when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.GET), eq("/v2/orders"), eq("state=open"), isNull()))
                .thenReturn(ResponseEntity.ok(deltaOrdersJson));

        // Mock Delta positions returning empty
        when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.GET), eq("/v2/positions/margined"), isNull(), isNull()))
                .thenReturn(ResponseEntity.ok("{\"success\": true, \"result\": []}"));
        when(lockRepository.findActiveLockByAccountId("acct-rec-1")).thenReturn(Optional.empty());

        ReconciliationReport report = service.runFullReconciliation();

        assertThat(report.success()).isTrue();
        assertThat(report.ordersReconciled()).isEqualTo(1);
        assertThat(pendingOrder.getStatusEnum()).isEqualTo(OrderStatus.OPEN);
        assertThat(pendingOrder.getDeltaOrderId()).isEqualTo("delta-ord-999");
        assertThat(pendingOrder.getReconciliationState()).isEqualTo("RECONCILED");
        verify(orderRepository).saveAndFlush(pendingOrder);
    }

    @Test
    @DisplayName("Startup reconciliation marks SUBMISSION_PENDING order as FAILED when not found on exchange")
    void marksMissingOrderAsFailed() {
        when(accountRepository.findAll()).thenReturn(List.of(testAccount));

        DeltaConnection conn = new DeltaConnection(testAccount, "LIVE", "enc-key", "enc-sec");
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-rec-1", "LIVE"))
                .thenReturn(Optional.of(conn));
        when(credentialService.decrypt("enc-key")).thenReturn("api-key");
        when(credentialService.decrypt("enc-sec")).thenReturn("api-sec");

        Order unplacedOrder = new Order(testAccount, "setup-rec-2", "client-rec-unplaced", "BTCUSD", "BUY", "LIMIT", BigDecimal.ONE, new BigDecimal("60000.00"));
        unplacedOrder.setStatus(OrderStatus.SUBMISSION_PENDING.name());
        when(orderRepository.findByTradingAccountIdAndStatusIn(eq("acct-rec-1"), anyCollection()))
                .thenReturn(List.of(unplacedOrder));

        // Mock Delta open orders returning empty
        when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.GET), eq("/v2/orders"), eq("state=open"), isNull()))
                .thenReturn(ResponseEntity.ok("{\"success\": true, \"result\": []}"));

        // Mock Delta positions returning empty
        when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.GET), eq("/v2/positions/margined"), isNull(), isNull()))
                .thenReturn(ResponseEntity.ok("{\"success\": true, \"result\": []}"));
        when(lockRepository.findActiveLockByAccountId("acct-rec-1")).thenReturn(Optional.empty());

        ReconciliationReport report = service.runFullReconciliation();

        assertThat(report.success()).isTrue();
        assertThat(report.ordersFailed()).isEqualTo(1);
        assertThat(unplacedOrder.getStatusEnum()).isEqualTo(OrderStatus.FAILED);
        assertThat(unplacedOrder.getReconciliationState()).isEqualTo("RECONCILED");
        verify(orderRepository).saveAndFlush(unplacedOrder);
    }

    @Test
    @DisplayName("Startup reconciliation force-releases orphaned active lock if no exchange position exists")
    void clearsOrphanedActiveLock() {
        when(accountRepository.findAll()).thenReturn(List.of(testAccount));

        DeltaConnection conn = new DeltaConnection(testAccount, "LIVE", "enc-key", "enc-sec");
        when(deltaConnectionRepository.findByTradingAccountIdAndEnvironment("acct-rec-1", "LIVE"))
                .thenReturn(Optional.of(conn));
        when(credentialService.decrypt("enc-key")).thenReturn("api-key");
        when(credentialService.decrypt("enc-sec")).thenReturn("api-sec");

        when(orderRepository.findByTradingAccountIdAndStatusIn(eq("acct-rec-1"), anyCollection())).thenReturn(List.of());

        when(deltaRestClient.executeRequest(eq("api-key"), eq("api-sec"), eq(HttpMethod.GET), eq("/v2/positions/margined"), isNull(), isNull()))
                .thenReturn(ResponseEntity.ok("{\"success\": true, \"result\": []}"));

        ActiveTradeLock orphanLock = new ActiveTradeLock(testAccount, "setup-stuck-1", "BTCUSD");
        when(lockRepository.findActiveLockByAccountId("acct-rec-1")).thenReturn(Optional.of(orphanLock));

        ReconciliationReport report = service.runFullReconciliation();

        assertThat(report.success()).isTrue();
        assertThat(report.locksCleared()).isEqualTo(1);
        verify(tradePersistenceService).forceReleaseLock("acct-rec-1", "RECONCILIATION_NOT_FOUND_ON_EXCHANGE");
    }
}
