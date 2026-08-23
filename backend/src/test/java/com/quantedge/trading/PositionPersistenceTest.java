package com.quantedge.trading;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.trading.position.Position;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import com.quantedge.trading.service.TradePersistenceService;
import com.quantedge.trading.service.TradePersistenceService.PositionCloseRequest;
import com.quantedge.trading.service.TradePersistenceService.PositionOpenRequest;
import com.quantedge.trading.service.TradePersistenceService.PositionReconciliationData;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 6: Position Persistence & Reconciliation Tests")
class PositionPersistenceTest {

    @Mock private ActiveTradeLockRepository lockRepository;
    @Mock private TradeRecordRepository tradeRecordRepository;
    @Mock private TradingAccountRepository accountRepository;
    @Mock private PositionRepository positionRepository;
    @Mock private AuditLogRepository auditLogRepository;

    private TradePersistenceService service;
    private TradingAccount testAccount;

    @BeforeEach
    void setUp() {
        service = new TradePersistenceService(
                lockRepository,
                tradeRecordRepository,
                accountRepository,
                positionRepository,
                auditLogRepository
        );

        User user = new User();
        user.setId("usr-pos-1");
        testAccount = new TradingAccount(user, "Position Test Account", "LIVE", "USDT");
        testAccount.setId("acct-pos-1");
    }

    @Nested
    @DisplayName("Position Creation & Incremental Fills")
    class PositionLifecycleTests {

        @Test
        @DisplayName("Creates new position upon initial fill")
        void createsNewPositionOnFill() {
            when(accountRepository.findById("acct-pos-1")).thenReturn(Optional.of(testAccount));
            when(positionRepository.findOpenByAccountIdAndSymbol("acct-pos-1", "BTCUSD")).thenReturn(Optional.empty());
            when(positionRepository.saveAndFlush(any(Position.class))).thenAnswer(inv -> inv.getArgument(0));

            PositionOpenRequest req = new PositionOpenRequest(
                    "acct-pos-1",
                    "setup-pos-1",
                    "client-ord-1",
                    "BTCUSD",
                    "LONG",
                    new BigDecimal("60000.00"),
                    new BigDecimal("2.0"),
                    10,
                    new BigDecimal("59000.00"),
                    new BigDecimal("63000.00")
            );

            Position pos = service.openPosition(req);

            assertThat(pos).isNotNull();
            assertThat(pos.getSymbol()).isEqualTo("BTCUSD");
            assertThat(pos.getSide()).isEqualTo("LONG");
            assertThat(pos.getQuantity()).isEqualByComparingTo("2.0");
            assertThat(pos.getEntryPrice()).isEqualByComparingTo("60000.00");
            assertThat(pos.getStatus()).isEqualTo("OPEN");
            assertThat(pos.getReconciliationState()).isEqualTo("AUTHORITATIVE");
        }

        @Test
        @DisplayName("Updates existing position on incremental fill with weighted average entry price")
        void updatesPositionOnIncrementalFill() {
            when(accountRepository.findById("acct-pos-1")).thenReturn(Optional.of(testAccount));

            Position existing = new Position(testAccount, "BTCUSD", "LONG", new BigDecimal("60000.00"), new BigDecimal("2.0"), 10);
            when(positionRepository.findOpenByAccountIdAndSymbol("acct-pos-1", "BTCUSD")).thenReturn(Optional.of(existing));
            when(positionRepository.saveAndFlush(any(Position.class))).thenAnswer(inv -> inv.getArgument(0));

            // Add 2.0 more at 62000.00 -> New average: (2*60000 + 2*62000)/4 = 61000.00
            PositionOpenRequest req = new PositionOpenRequest(
                    "acct-pos-1",
                    "setup-pos-1",
                    "client-ord-2",
                    "BTCUSD",
                    "LONG",
                    new BigDecimal("62000.00"),
                    new BigDecimal("2.0"),
                    10,
                    new BigDecimal("59000.00"),
                    new BigDecimal("63000.00")
            );

            Position updated = service.openPosition(req);

            assertThat(updated.getQuantity()).isEqualByComparingTo("4.0");
            assertThat(updated.getEntryPrice()).isEqualByComparingTo("61000.00");
        }

        @Test
        @DisplayName("Closes position and records exit metrics")
        void closesPosition() {
            Position existing = new Position(testAccount, "BTCUSD", "LONG", new BigDecimal("60000.00"), new BigDecimal("2.0"), 10);
            when(positionRepository.findOpenByAccountIdAndSymbol("acct-pos-1", "BTCUSD")).thenReturn(Optional.of(existing));
            when(positionRepository.saveAndFlush(any(Position.class))).thenAnswer(inv -> inv.getArgument(0));

            PositionCloseRequest closeReq = new PositionCloseRequest(
                    "acct-pos-1",
                    "BTCUSD",
                    "close-ord-99",
                    new BigDecimal("500.00"),
                    Instant.now()
            );

            service.closePosition(closeReq);

            assertThat(existing.getStatus()).isEqualTo("CLOSED");
            assertThat(existing.getRealizedPnl()).isEqualByComparingTo("500.00");
            assertThat(existing.getCloseOrderId()).isEqualTo("close-ord-99");
            assertThat(existing.getReconciliationState()).isEqualTo("RECONCILED");
        }
    }

    @Nested
    @DisplayName("Exchange Position Reconciliation")
    class ReconciliationTests {

        @Test
        @DisplayName("Reconciliation resolves missing local position by creating it")
        void resolvesMissingLocalPosition() {
            when(accountRepository.findById("acct-pos-1")).thenReturn(Optional.of(testAccount));
            when(positionRepository.findAllOpenByAccountId("acct-pos-1")).thenReturn(List.of());
            when(positionRepository.saveAndFlush(any(Position.class))).thenAnswer(inv -> inv.getArgument(0));

            PositionReconciliationData exPos = new PositionReconciliationData(
                    "ETHUSD", "LONG", new BigDecimal("5.0"),
                    new BigDecimal("3000.00"), new BigDecimal("3100.00"),
                    new BigDecimal("500.00"), BigDecimal.ZERO,
                    10, new BigDecimal("1500.00"), new BigDecimal("2700.00")
            );

            service.reconcilePositions("acct-pos-1", List.of(exPos));

            ArgumentCaptor<Position> captor = ArgumentCaptor.forClass(Position.class);
            verify(positionRepository).saveAndFlush(captor.capture());

            Position saved = captor.getValue();
            assertThat(saved.getSymbol()).isEqualTo("ETHUSD");
            assertThat(saved.getQuantity()).isEqualByComparingTo("5.0");
            assertThat(saved.getReconciliationState()).isEqualTo("RECONCILED");
        }

        @Test
        @DisplayName("Reconciliation marks local position as CLOSED when not present on exchange")
        void closesLocalPositionWhenMissingOnExchange() {
            when(accountRepository.findById("acct-pos-1")).thenReturn(Optional.of(testAccount));

            Position localOpen = new Position(testAccount, "BTCUSD", "LONG", new BigDecimal("60000.00"), new BigDecimal("1.0"), 10);
            when(positionRepository.findAllOpenByAccountId("acct-pos-1")).thenReturn(List.of(localOpen));

            // Exchange returns empty position list
            service.reconcilePositions("acct-pos-1", List.of());

            assertThat(localOpen.getStatus()).isEqualTo("CLOSED");
            assertThat(localOpen.getReconciliationState()).isEqualTo("RECONCILED");
            verify(positionRepository).saveAndFlush(localOpen);
        }
    }
}
