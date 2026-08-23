package com.quantedge.trading;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.auth.entity.User;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.order.OrderStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("Phase 6: Order State Machine Unit Tests")
class OrderStateMachineTest {

    private Order order;
    private TradingAccount account;

    @BeforeEach
    void setUp() {
        User user = new User();
        user.setId("usr-sm-1");
        account = new TradingAccount(user, "State Machine Test Account", "LIVE", "USDT");
        account.setId("acct-sm-1");

        order = new Order(
                account,
                "setup-sm-1",
                "client-sm-1",
                "BTCUSD",
                "BUY",
                "LIMIT",
                BigDecimal.TEN,
                new BigDecimal("60000.00")
        );
    }

    @Nested
    @DisplayName("Valid State Machine Lifecycle Transitions")
    class ValidTransitions {

        @Test
        @DisplayName("CREATED -> SUBMISSION_PENDING -> SUBMITTED -> OPEN -> PARTIALLY_FILLED -> FILLED")
        void fullHappyPathLifecycle() {
            order.setStatus(OrderStatus.CREATED.name());
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.CREATED);

            order.transitionStatus(OrderStatus.SUBMISSION_PENDING);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.SUBMISSION_PENDING);

            order.transitionStatus(OrderStatus.SUBMITTED);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.SUBMITTED);

            order.transitionStatus(OrderStatus.OPEN);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.OPEN);

            order.transitionStatus(OrderStatus.PARTIALLY_FILLED);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.PARTIALLY_FILLED);

            order.transitionStatus(OrderStatus.FILLED);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.FILLED);
            assertThat(order.getStatusEnum().isFilled()).isTrue();
            assertThat(order.getStatusEnum().isTerminal()).isTrue();
        }

        @Test
        @DisplayName("SUBMISSION_PENDING -> SUBMITTED -> FILLED (Immediate Match)")
        void immediateFillLifecycle() {
            order.setStatus(OrderStatus.SUBMISSION_PENDING.name());
            order.transitionStatus(OrderStatus.SUBMITTED);
            order.transitionStatus(OrderStatus.FILLED);

            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.FILLED);
            assertThat(order.getStatusEnum().isTerminal()).isTrue();
        }

        @Test
        @DisplayName("SUBMISSION_PENDING -> FAILED (Exchange Unreachable/Timeout)")
        void failedSubmissionLifecycle() {
            order.setStatus(OrderStatus.SUBMISSION_PENDING.name());
            order.transitionStatus(OrderStatus.FAILED);

            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.FAILED);
            assertThat(order.getStatusEnum().isTerminal()).isTrue();
        }

        @Test
        @DisplayName("UNKNOWN -> OPEN (Reconciliation Recovery)")
        void unknownToOpenReconciliation() {
            order.setStatus(OrderStatus.SUBMISSION_PENDING.name());
            order.transitionStatus(OrderStatus.UNKNOWN);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.UNKNOWN);

            order.transitionStatus(OrderStatus.OPEN);
            assertThat(order.getStatusEnum()).isEqualTo(OrderStatus.OPEN);
        }
    }

    @Nested
    @DisplayName("Invalid State Machine Transitions (Must Throw IllegalStateException)")
    class InvalidTransitions {

        @Test
        @DisplayName("CANCELLED -> FILLED is blocked")
        void cancelledToFilledBlocked() {
            order.setStatus(OrderStatus.CANCELLED.name());
            assertThatThrownBy(() -> order.transitionStatus(OrderStatus.FILLED))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Invalid order status transition");
        }

        @Test
        @DisplayName("REJECTED -> FILLED is blocked")
        void rejectedToFilledBlocked() {
            order.setStatus(OrderStatus.REJECTED.name());
            assertThatThrownBy(() -> order.transitionStatus(OrderStatus.FILLED))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Invalid order status transition");
        }

        @Test
        @DisplayName("FAILED -> FILLED is blocked")
        void failedToFilledBlocked() {
            order.setStatus(OrderStatus.FAILED.name());
            assertThatThrownBy(() -> order.transitionStatus(OrderStatus.FILLED))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Invalid order status transition");
        }

        @Test
        @DisplayName("FILLED -> CANCELLED is blocked (Terminal state)")
        void filledToCancelledBlocked() {
            order.setStatus(OrderStatus.FILLED.name());
            assertThatThrownBy(() -> order.transitionStatus(OrderStatus.CANCELLED))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Invalid order status transition");
        }

        @Test
        @DisplayName("CREATED -> FILLED is blocked (Must go through submission)")
        void createdToFilledBlocked() {
            order.setStatus(OrderStatus.CREATED.name());
            assertThatThrownBy(() -> order.transitionStatus(OrderStatus.FILLED))
                    .isInstanceOf(IllegalStateException.class);
        }
    }

    @Nested
    @DisplayName("Delta State String Parsing and Properties")
    class DeltaStateParsing {

        @Test
        @DisplayName("Maps Delta exchange state strings correctly")
        void deltaStateMapping() {
            assertThat(OrderStatus.fromDeltaState("open")).isEqualTo(OrderStatus.OPEN);
            assertThat(OrderStatus.fromDeltaState("filled")).isEqualTo(OrderStatus.FILLED);
            assertThat(OrderStatus.fromDeltaState("cancelled")).isEqualTo(OrderStatus.CANCELLED);
            assertThat(OrderStatus.fromDeltaState("rejected")).isEqualTo(OrderStatus.REJECTED);
            assertThat(OrderStatus.fromDeltaState("pending")).isEqualTo(OrderStatus.SUBMITTED);
            assertThat(OrderStatus.fromDeltaState("partially_filled")).isEqualTo(OrderStatus.PARTIALLY_FILLED);
            assertThat(OrderStatus.fromDeltaState("unknown_random")).isEqualTo(OrderStatus.UNKNOWN);
        }

        @Test
        @DisplayName("Identifies terminal vs active statuses")
        void terminalAndActiveChecks() {
            assertThat(OrderStatus.FILLED.isTerminal()).isTrue();
            assertThat(OrderStatus.CANCELLED.isTerminal()).isTrue();
            assertThat(OrderStatus.REJECTED.isTerminal()).isTrue();
            assertThat(OrderStatus.FAILED.isTerminal()).isTrue();

            assertThat(OrderStatus.OPEN.isTerminal()).isFalse();
            assertThat(OrderStatus.OPEN.isActive()).isTrue();
            assertThat(OrderStatus.SUBMITTED.isActive()).isTrue();
            assertThat(OrderStatus.PARTIALLY_FILLED.isActive()).isTrue();
        }
    }
}
