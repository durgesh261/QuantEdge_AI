package com.quantedge.trading.order;

import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

/**
 * Deterministic Order State Machine for QuantEdge AI.
 *
 * <h3>Valid Lifecycle</h3>
 * <pre>
 * CREATED → SUBMISSION_PENDING
 * SUBMISSION_PENDING → SUBMITTED | FAILED | UNKNOWN
 * SUBMITTED → OPEN | FILLED | CANCELLED | REJECTED | FAILED | UNKNOWN
 * OPEN → PARTIALLY_FILLED | FILLED | CANCEL_PENDING | CANCELLED | REJECTED
 * PARTIALLY_FILLED → FILLED | CANCEL_PENDING | CANCELLED
 * CANCEL_PENDING → CANCELLED | OPEN | PARTIALLY_FILLED
 * UNKNOWN → SUBMITTED | OPEN | FILLED | CANCELLED | FAILED (via reconciliation)
 * </pre>
 *
 * <h3>Immutable Terminal States</h3>
 * <ul>
 *   <li>FILLED — order fully executed. Cannot transition to any other state.</li>
 *   <li>CANCELLED — order cancelled by exchange or user. Cannot become FILLED.</li>
 *   <li>REJECTED — order rejected by exchange. Cannot become FILLED.</li>
 *   <li>FAILED — submission failed (not placed on exchange). Cannot become FILLED.</li>
 *   <li>EXPIRED — GTC order expired. Cannot become FILLED.</li>
 * </ul>
 *
 * <p>Any attempt to call {@link #transitionTo(OrderStatus)} with an invalid
 * target state will throw {@link IllegalStateException}. This prevents
 * database state corruption even if a code path incorrectly constructs a
 * transition.</p>
 */
public enum OrderStatus {

    /**
     * Initial state — row created in DB before any network call to exchange.
     */
    CREATED,

    /**
     * Network call to Delta Exchange is in progress.
     * The row is persisted before the call so a crash during submission
     * leaves an auditable SUBMISSION_PENDING record.
     */
    SUBMISSION_PENDING,

    /**
     * Delta Exchange accepted the order (HTTP 200 received).
     * The exchange order ID has been recorded.
     * The order is now on the exchange order book but not yet open/matched.
     */
    SUBMITTED,

    /**
     * Order is live on the exchange order book.
     * Waiting for a counterparty match.
     */
    OPEN,

    /**
     * Order has been partially matched.
     * filledQuantity > 0 and < quantity.
     */
    PARTIALLY_FILLED,

    /**
     * Order has been fully matched.
     * filledQuantity == quantity.
     * TERMINAL STATE — no further transitions allowed.
     */
    FILLED,

    /**
     * A cancel request has been submitted to the exchange.
     * Awaiting confirmation.
     */
    CANCEL_PENDING,

    /**
     * Order was cancelled (by user, exchange, or kill switch).
     * TERMINAL STATE — cannot become FILLED.
     */
    CANCELLED,

    /**
     * Order was rejected by the exchange (invalid params, insufficient margin, etc.).
     * TERMINAL STATE — cannot become FILLED.
     */
    REJECTED,

    /**
     * Submission failed — the order was NOT placed on the exchange.
     * Determined by reconciliation after a network timeout.
     * TERMINAL STATE — cannot become FILLED.
     */
    FAILED,

    /**
     * Outcome is unknown — typically after a network timeout where reconciliation
     * also failed. Requires manual review or subsequent reconciliation pass.
     * Can transition to SUBMITTED, OPEN, FILLED, CANCELLED, or FAILED
     * once reconciliation determines the true state.
     */
    UNKNOWN,

    /**
     * GTC order expired on the exchange.
     * TERMINAL STATE — cannot become FILLED.
     */
    EXPIRED;

    // -------------------------------------------------------------------------
    // Valid transition map
    // -------------------------------------------------------------------------

    private static final Map<OrderStatus, Set<OrderStatus>> VALID_TRANSITIONS = Map.ofEntries(
            Map.entry(CREATED,            EnumSet.of(SUBMISSION_PENDING)),
            Map.entry(SUBMISSION_PENDING, EnumSet.of(SUBMITTED, OPEN, FILLED, FAILED, UNKNOWN, CANCELLED, REJECTED)),
            Map.entry(SUBMITTED,          EnumSet.of(OPEN, FILLED, CANCELLED, REJECTED, FAILED, UNKNOWN, PARTIALLY_FILLED)),
            Map.entry(OPEN,               EnumSet.of(PARTIALLY_FILLED, FILLED, CANCEL_PENDING, CANCELLED, REJECTED, UNKNOWN)),
            Map.entry(PARTIALLY_FILLED,   EnumSet.of(FILLED, CANCEL_PENDING, CANCELLED, UNKNOWN)),
            Map.entry(CANCEL_PENDING,     EnumSet.of(CANCELLED, OPEN, PARTIALLY_FILLED, FILLED)),
            Map.entry(UNKNOWN,            EnumSet.of(SUBMITTED, OPEN, FILLED, CANCELLED, FAILED, PARTIALLY_FILLED)),
            // Terminal states — empty set means no further transitions allowed
            Map.entry(FILLED,             EnumSet.noneOf(OrderStatus.class)),
            Map.entry(CANCELLED,          EnumSet.noneOf(OrderStatus.class)),
            Map.entry(REJECTED,           EnumSet.noneOf(OrderStatus.class)),
            Map.entry(FAILED,             EnumSet.noneOf(OrderStatus.class)),
            Map.entry(EXPIRED,            EnumSet.noneOf(OrderStatus.class))
    );

    /**
     * Returns true if this status is a terminal (no-exit) state.
     */
    public boolean isTerminal() {
        Set<OrderStatus> allowed = VALID_TRANSITIONS.get(this);
        return allowed == null || allowed.isEmpty();
    }

    /**
     * Returns true if this status represents a successfully executed order.
     */
    public boolean isFilled() {
        return this == FILLED;
    }

    /**
     * Returns true if this status represents an active (non-terminal, non-failed) order.
     */
    public boolean isActive() {
        return this == SUBMISSION_PENDING || this == SUBMITTED
                || this == OPEN || this == PARTIALLY_FILLED
                || this == CANCEL_PENDING || this == UNKNOWN;
    }

    /**
     * Validates that transitioning from {@code this} to {@code target} is permitted
     * by the state machine. Throws {@link IllegalStateException} if not.
     *
     * @param target the target state to transition to
     * @throws IllegalStateException if the transition is not permitted
     */
    public void transitionTo(OrderStatus target) {
        Set<OrderStatus> allowed = VALID_TRANSITIONS.get(this);
        if (allowed == null || !allowed.contains(target)) {
            throw new IllegalStateException(
                    String.format(
                            "Invalid order status transition: %s → %s. " +
                            "Allowed transitions from %s: %s",
                            this, target, this, allowed
                    )
            );
        }
    }

    /**
     * Convenience factory method — parses a raw string (from DB or exchange API)
     * to an {@link OrderStatus}, falling back to {@link #UNKNOWN} for unrecognized values.
     *
     * @param raw string value, may be null
     * @return corresponding enum constant, or UNKNOWN if not recognized
     */
    public static OrderStatus fromString(String raw) {
        if (raw == null || raw.isBlank()) return UNKNOWN;
        try {
            return OrderStatus.valueOf(raw.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            return UNKNOWN;
        }
    }

    /**
     * Maps a Delta Exchange order state string to an {@link OrderStatus}.
     *
     * <p>Delta states: open, filled, cancelled, rejected, pending</p>
     *
     * @param deltaState the "state" field from the Delta Exchange REST API response
     * @return the corresponding internal status
     */
    public static OrderStatus fromDeltaState(String deltaState) {
        if (deltaState == null) return UNKNOWN;
        return switch (deltaState.toLowerCase().trim()) {
            case "open"               -> OPEN;
            case "filled"             -> FILLED;
            case "cancelled", "canceled" -> CANCELLED;
            case "rejected"           -> REJECTED;
            case "pending"            -> SUBMITTED;
            case "partially_filled"   -> PARTIALLY_FILLED;
            default                   -> UNKNOWN;
        };
    }
}
