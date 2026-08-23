package com.quantedge.trading;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.auth.entity.User;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.execution.FillPersistenceService;
import com.quantedge.trading.execution.FillPersistenceService.FillRequest;
import com.quantedge.trading.execution.FillPersistenceService.FillResult;
import com.quantedge.trading.execution.OrderFill;
import com.quantedge.trading.execution.OrderFillRepository;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.repository.OrderRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("Phase 6: Fill Persistence & Partial Fills Tests")
class FillPersistenceTest {

    @Mock private OrderFillRepository fillRepository;
    @Mock private OrderRepository orderRepository;

    private FillPersistenceService service;
    private Order testOrder;
    private TradingAccount testAccount;

    @BeforeEach
    void setUp() {
        service = new FillPersistenceService(fillRepository, orderRepository);

        User user = new User();
        user.setId("usr-fill-1");
        testAccount = new TradingAccount(user, "Fill Test Account", "LIVE", "USDT");
        testAccount.setId("acct-fill-1");

        testOrder = new Order(
                testAccount,
                "setup-fill-1",
                "client-fill-100",
                "BTCUSD",
                "BUY",
                "LIMIT",
                new BigDecimal("10.0"),
                new BigDecimal("60000.00")
        );
        testOrder.setId("ord-uuid-100");
        testOrder.setStatus(OrderStatus.OPEN.name());
    }

    @Nested
    @DisplayName("Fill Deduplication & Safety")
    class DeduplicationTests {

        @Test
        @DisplayName("Records new fill successfully")
        void recordsNewFill() {
            when(fillRepository.existsByExchangeFillId("delta-fill-1")).thenReturn(false);
            when(orderRepository.findById("ord-uuid-100")).thenReturn(Optional.of(testOrder));
            when(fillRepository.saveAndFlush(any(OrderFill.class))).thenAnswer(inv -> {
                OrderFill f = inv.getArgument(0);
                f.setId("fill-db-1");
                return f;
            });
            when(fillRepository.sumFillQuantityByOrderId("ord-uuid-100")).thenReturn(new BigDecimal("4.0"));
            when(fillRepository.computeWeightedAverageFillPrice("ord-uuid-100")).thenReturn(Optional.of(new BigDecimal("60000.00")));

            FillRequest req = new FillRequest(
                    "ord-uuid-100",
                    "delta-fill-1",
                    "client-fill-100",
                    "delta-ord-1",
                    "BTCUSD",
                    "BUY",
                    new BigDecimal("4.0"),
                    new BigDecimal("60000.00"),
                    new BigDecimal("1.20"),
                    "USDT",
                    Instant.now(),
                    "{}"
            );

            FillResult res = service.recordFill(req);

            assertThat(res.success()).isTrue();
            assertThat(res.duplicate()).isFalse();
            assertThat(res.fillId()).isEqualTo("fill-db-1");
            verify(fillRepository).saveAndFlush(any(OrderFill.class));
        }

        @Test
        @DisplayName("Duplicate exchangeFillId is safely ignored without DB write")
        void duplicateFillIsIgnored() {
            when(fillRepository.existsByExchangeFillId("delta-fill-dup")).thenReturn(true);

            FillRequest req = new FillRequest(
                    "ord-uuid-100",
                    "delta-fill-dup",
                    "client-fill-100",
                    "delta-ord-1",
                    "BTCUSD",
                    "BUY",
                    new BigDecimal("4.0"),
                    new BigDecimal("60000.00"),
                    BigDecimal.ZERO,
                    "USDT",
                    Instant.now(),
                    "{}"
            );

            FillResult res = service.recordFill(req);

            assertThat(res.success()).isTrue();
            assertThat(res.duplicate()).isTrue();
            verify(fillRepository, never()).saveAndFlush(any(OrderFill.class));
        }
    }

    @Nested
    @DisplayName("Partial Fill Sequences (4 + 3 + 3 = 10)")
    class PartialFillSequenceTests {

        @Test
        @DisplayName("Partial fill transitions order to PARTIALLY_FILLED then FILLED")
        void partialFillTransitions() {
            // First fill: 4.0 out of 10.0
            when(fillRepository.sumFillQuantityByOrderId("ord-uuid-100")).thenReturn(new BigDecimal("4.0"));
            when(fillRepository.computeWeightedAverageFillPrice("ord-uuid-100")).thenReturn(Optional.of(new BigDecimal("60000.00")));

            service.updateOrderFromFills(testOrder);

            assertThat(testOrder.getStatusEnum()).isEqualTo(OrderStatus.PARTIALLY_FILLED);
            assertThat(testOrder.getFilledQuantity()).isEqualByComparingTo("4.0");

            // Second fill: 7.0 out of 10.0
            when(fillRepository.sumFillQuantityByOrderId("ord-uuid-100")).thenReturn(new BigDecimal("7.0"));
            when(fillRepository.computeWeightedAverageFillPrice("ord-uuid-100")).thenReturn(Optional.of(new BigDecimal("60100.00")));

            service.updateOrderFromFills(testOrder);

            assertThat(testOrder.getStatusEnum()).isEqualTo(OrderStatus.PARTIALLY_FILLED);
            assertThat(testOrder.getFilledQuantity()).isEqualByComparingTo("7.0");

            // Final fill: 10.0 out of 10.0
            when(fillRepository.sumFillQuantityByOrderId("ord-uuid-100")).thenReturn(new BigDecimal("10.0"));
            when(fillRepository.computeWeightedAverageFillPrice("ord-uuid-100")).thenReturn(Optional.of(new BigDecimal("60050.00")));

            service.updateOrderFromFills(testOrder);

            assertThat(testOrder.getStatusEnum()).isEqualTo(OrderStatus.FILLED);
            assertThat(testOrder.getFilledQuantity()).isEqualByComparingTo("10.0");
            assertThat(testOrder.getFilledAt()).isNotNull();
            verify(orderRepository, times(3)).saveAndFlush(testOrder);
        }
    }
}
