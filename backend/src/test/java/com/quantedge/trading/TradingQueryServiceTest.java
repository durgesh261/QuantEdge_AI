package com.quantedge.trading;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.exchange.entity.DeltaConnection;
import com.quantedge.exchange.repository.DeltaConnectionRepository;
import com.quantedge.exchange.service.DeltaCredentialService;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import com.quantedge.trading.dto.*;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.entity.TradeRecord;
import com.quantedge.trading.execution.OrderFill;
import com.quantedge.trading.execution.OrderFillRepository;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.position.Position;
import com.quantedge.trading.position.PositionRepository;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.OrderRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import com.quantedge.trading.service.TradingQueryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.access.AccessDeniedException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 7: TradingQueryService Unit Tests")
class TradingQueryServiceTest {

    @Mock private TradingAccountRepository accountRepository;
    @Mock private OrderRepository orderRepository;
    @Mock private PositionRepository positionRepository;
    @Mock private OrderFillRepository orderFillRepository;
    @Mock private TradeRecordRepository tradeRecordRepository;
    @Mock private StrategySetupRepository strategySetupRepository;
    @Mock private DeltaConnectionRepository deltaConnectionRepository;
    @Mock private ActiveTradeLockRepository lockRepository;
    @Mock private DeltaCredentialService credentialService;

    private TradingQueryService queryService;

    private User userA;
    private User userB;
    private TradingAccount accountA;
    private TradingAccount accountB;

    @BeforeEach
    void setUp() {
        queryService = new TradingQueryService(
                accountRepository,
                orderRepository,
                positionRepository,
                orderFillRepository,
                tradeRecordRepository,
                strategySetupRepository,
                deltaConnectionRepository,
                lockRepository,
                credentialService
        );

        userA = new User();
        userA.setId("user-a-uuid");
        userA.setEmail("user.a@quantedge.io");

        userB = new User();
        userB.setId("user-b-uuid");
        userB.setEmail("user.b@quantedge.io");

        accountA = new TradingAccount(userA, "Account A", "LIVE", "USDT");
        accountA.setId("acct-a-uuid");
        accountA.setIsActive(true);
        accountA.setTotalEquity(new BigDecimal("10000.00"));
        accountA.setAvailableBalance(new BigDecimal("9500.00"));
        accountA.setCurrentBalance(new BigDecimal("10000.00"));
        accountA.setMarginUsed(new BigDecimal("500.00"));

        accountB = new TradingAccount(userB, "Account B", "LIVE", "USDT");
        accountB.setId("acct-b-uuid");
        accountB.setIsActive(true);
    }

    @Nested
    @DisplayName("Tenant Ownership & IDOR Protection")
    class TenantIsolationTests {

        @Test
        @DisplayName("User A querying User B's account throws AccessDeniedException (403)")
        void crossTenantAccountAccessBlocked() {
            when(accountRepository.findById("acct-b-uuid")).thenReturn(Optional.of(accountB));

            assertThatThrownBy(() -> queryService.getOrders(userA, "acct-b-uuid", null, null, 100))
                    .isInstanceOf(AccessDeniedException.class)
                    .hasMessageContaining("Access denied: You do not own trading account acct-b-uuid");
        }

        @Test
        @DisplayName("Unauthenticated query throws AccessDeniedException")
        void unauthenticatedQueryBlocked() {
            assertThatThrownBy(() -> queryService.getTradingSystemStatus(null, "acct-a-uuid"))
                    .isInstanceOf(AccessDeniedException.class);
        }

        @Test
        @DisplayName("Query with no accountId resolves to user's default active account")
        void resolvesDefaultAccount() {
            when(accountRepository.findByUserId("user-a-uuid")).thenReturn(List.of(accountA));
            when(lockRepository.findActiveLockByAccountId("acct-a-uuid")).thenReturn(Optional.empty());
            when(positionRepository.findAllOpenByAccountId("acct-a-uuid")).thenReturn(List.of());
            when(orderRepository.countByTradingAccountIdAndStatusIn(anyString(), any())).thenReturn(0);

            TradingSystemStatusDto status = queryService.getTradingSystemStatus(userA, null);

            assertThat(status).isNotNull();
            assertThat(status.accountId()).isEqualTo("acct-a-uuid");
        }
    }

    @Nested
    @DisplayName("Orders Query & Filtering")
    class OrderQueryTests {

        @Test
        @DisplayName("Retrieves orders with symbol and status filters")
        void retrievesOrdersWithFilters() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            Order o1 = new Order(accountA, "setup-1", "client-1", "BTCUSD", "BUY", "LIMIT", BigDecimal.ONE, new BigDecimal("60000.00"));
            o1.setId("ord-1");
            o1.setStatus(OrderStatus.OPEN.name());

            when(orderRepository.findByTradingAccountIdAndSymbolAndStatusOrderByPlacedAtDesc("acct-a-uuid", "BTCUSD", "OPEN"))
                    .thenReturn(List.of(o1));

            List<OrderDto> orders = queryService.getOrders(userA, "acct-a-uuid", "BTCUSD", "OPEN", 10);

            assertThat(orders).hasSize(1);
            assertThat(orders.getFirst().clientOrderId()).isEqualTo("client-1");
            assertThat(orders.getFirst().symbol()).isEqualTo("BTCUSD");
            assertThat(orders.getFirst().status()).isEqualTo("OPEN");
        }

        @Test
        @DisplayName("Retrieves single order by ID or clientOrderId")
        void retrievesSingleOrder() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            Order o1 = new Order(accountA, "setup-1", "client-101", "BTCUSD", "BUY", "LIMIT", BigDecimal.ONE, new BigDecimal("60000.00"));
            o1.setId("ord-101");
            when(orderRepository.findByIdAndTradingAccountId("client-101", "acct-a-uuid")).thenReturn(Optional.empty());
            when(orderRepository.findByClientOrderIdAndTradingAccountId("client-101", "acct-a-uuid")).thenReturn(Optional.of(o1));

            OrderDto dto = queryService.getOrderById(userA, "acct-a-uuid", "client-101");

            assertThat(dto).isNotNull();
            assertThat(dto.clientOrderId()).isEqualTo("client-101");
        }
    }

    @Nested
    @DisplayName("Positions, Fills, History, and Signals Queries")
    class ResourceQueryTests {

        @Test
        @DisplayName("Retrieves positions filtered by status")
        void retrievesPositions() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            Position pos = new Position(accountA, "BTCUSD", "LONG", new BigDecimal("60000.00"), BigDecimal.ONE, 10);
            pos.setId("pos-1");
            when(positionRepository.findByTradingAccountIdAndStatusOrderByOpenedAtDesc("acct-a-uuid", "OPEN"))
                    .thenReturn(List.of(pos));

            List<PositionDto> positions = queryService.getPositions(userA, "acct-a-uuid", "OPEN");

            assertThat(positions).hasSize(1);
            assertThat(positions.getFirst().symbol()).isEqualTo("BTCUSD");
            assertThat(positions.getFirst().status()).isEqualTo("OPEN");
        }

        @Test
        @DisplayName("Retrieves fills for order")
        void retrievesFills() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            OrderFill fill = new OrderFill(accountA, null, "fill-ex-1", "client-1", "delta-1", "BTCUSD", "BUY",
                    BigDecimal.ONE, new BigDecimal("60000.00"), BigDecimal.ZERO, "USDT", Instant.now());
            fill.setId("fill-1");
            when(orderFillRepository.findByTradingAccountIdOrderByFilledAtDesc("acct-a-uuid"))
                    .thenReturn(List.of(fill));

            List<OrderFillDto> fills = queryService.getFills(userA, "acct-a-uuid", null, null, 10);

            assertThat(fills).hasSize(1);
            assertThat(fills.getFirst().exchangeFillId()).isEqualTo("fill-ex-1");
        }

        @Test
        @DisplayName("Retrieves trade history")
        void retrievesTradeHistory() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            TradeRecord tr = new TradeRecord(accountA, "setup-1", "BTCUSD", "LONG",
                    new BigDecimal("60000.00"), BigDecimal.ONE, 10, new BigDecimal("10000.00"));
            tr.setId("tr-1");
            tr.setNetPnl(new BigDecimal("500.00"));
            when(tradeRecordRepository.findByAccountIdOrderByOpenedAtDesc("acct-a-uuid"))
                    .thenReturn(List.of(tr));

            List<TradeHistoryDto> history = queryService.getTradeHistory(userA, "acct-a-uuid", 10);

            assertThat(history).hasSize(1);
            assertThat(history.getFirst().netPnl()).isEqualByComparingTo("500.00");
        }

        @Test
        @DisplayName("Retrieves strategy signals/setups")
        void retrievesSignals() {
            when(accountRepository.findById("acct-a-uuid")).thenReturn(Optional.of(accountA));

            StrategySetupRecord setup = new StrategySetupRecord(accountA, "setup-100", "BTCUSD", "LONG",
                    new BigDecimal("60000.00"), new BigDecimal("59000.00"), new BigDecimal("63000.00"),
                    new BigDecimal("3.0"), Instant.now().plusSeconds(3600));
            setup.setId("set-100");
            when(strategySetupRepository.findByTradingAccountIdOrderByCreatedAtDesc("acct-a-uuid"))
                    .thenReturn(List.of(setup));

            List<SignalSetupDto> signals = queryService.getSignals(userA, "acct-a-uuid", null, null, 10);

            assertThat(signals).hasSize(1);
            assertThat(signals.getFirst().setupId()).isEqualTo("setup-100");
        }
    }
}
