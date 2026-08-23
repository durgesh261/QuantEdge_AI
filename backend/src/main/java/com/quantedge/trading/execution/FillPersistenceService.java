package com.quantedge.trading.execution;

import com.quantedge.trading.entity.Order;
import com.quantedge.trading.order.OrderStatus;
import com.quantedge.trading.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Optional;

/**
 * Authoritative service for persisting exchange fill events to the database.
 *
 * <h3>Deduplication Strategy</h3>
 * <ol>
 *   <li>Fast pre-check via {@link OrderFillRepository#existsByExchangeFillId(String)} before
 *       attempting any write.</li>
 *   <li>Database-level UNIQUE constraint on {@code order_fills.exchange_fill_id} acts as
 *       the final safety net against concurrent duplicate ingestion.</li>
 *   <li>{@link DataIntegrityViolationException} from the constraint is caught and converted
 *       to an idempotent no-op — never re-thrown.</li>
 * </ol>
 *
 * <h3>Partial Fill Handling</h3>
 * <p>Each call to {@link #recordFill(FillRequest)} persists one fill event.
 * {@link #updateOrderFromFills(Order)} then recomputes the aggregated
 * {@code filledQuantity}, {@code averageFillPrice}, and transitions the
 * order status to {@code PARTIALLY_FILLED} or {@code FILLED}.</p>
 *
 * <h3>ORDER SUBMITTED ≠ ORDER FILLED</h3>
 * <p>This service is ONLY called when the exchange reports an actual execution.
 * Order submission success does NOT trigger this service.</p>
 */
@Service
public class FillPersistenceService {

    private static final Logger log = LoggerFactory.getLogger(FillPersistenceService.class);

    private final OrderFillRepository fillRepository;
    private final OrderRepository orderRepository;

    public FillPersistenceService(OrderFillRepository fillRepository,
                                  OrderRepository orderRepository) {
        this.fillRepository = fillRepository;
        this.orderRepository = orderRepository;
    }

    // ── DTOs ──────────────────────────────────────────────────────────────────

    public record FillRequest(
            String orderId,            // UUID of Order entity (may be null)
            String exchangeFillId,     // exchange-assigned fill ID (required)
            String clientOrderId,
            String deltaOrderId,
            String symbol,
            String side,
            BigDecimal fillQuantity,
            BigDecimal fillPrice,
            BigDecimal fee,
            String feeAsset,
            java.time.Instant filledAt,
            String rawExchangeData     // raw JSON for audit
    ) {}

    public record FillResult(
            boolean success,
            boolean duplicate,
            String fillId,
            String message
    ) {
        public static FillResult duplicate(String exchangeFillId) {
            return new FillResult(true, true, null,
                    "Fill " + exchangeFillId + " already recorded (idempotent skip).");
        }
        public static FillResult recorded(String fillId) {
            return new FillResult(true, false, fillId, "Fill recorded.");
        }
        public static FillResult failed(String reason) {
            return new FillResult(false, false, null, reason);
        }
    }

    // ── Core Operations ───────────────────────────────────────────────────────

    /**
     * Records a single fill event from the exchange.
     *
     * <p>If the fill already exists ({@code exchangeFillId} already recorded),
     * returns a duplicate result without any DB write — idempotent.</p>
     *
     * <p>After recording the fill, automatically calls
     * {@link #updateOrderFromFills(Order)} to recompute aggregate state and
     * transition order status if necessary.</p>
     *
     * @param req fill data from exchange
     * @return {@link FillResult} indicating success, duplicate, or failure
     */
    @Transactional
    public FillResult recordFill(FillRequest req) {
        if (req.exchangeFillId() == null || req.exchangeFillId().isBlank()) {
            log.warn("recordFill: missing exchangeFillId, rejecting fill for clientOrderId={}", req.clientOrderId());
            return FillResult.failed("exchangeFillId is required but was null/blank");
        }

        // Fast deduplication pre-check
        if (fillRepository.existsByExchangeFillId(req.exchangeFillId())) {
            log.debug("recordFill: fill {} already recorded — idempotent skip", req.exchangeFillId());
            return FillResult.duplicate(req.exchangeFillId());
        }

        // Resolve order entity (may be null if reconciliation discovers an orphan fill)
        Order order = null;
        if (req.orderId() != null && !req.orderId().isBlank()) {
            order = orderRepository.findById(req.orderId()).orElse(null);
        }
        if (order == null && req.clientOrderId() != null) {
            order = orderRepository.findByClientOrderId(req.clientOrderId()).orElse(null);
        }

        if (order == null) {
            log.warn("recordFill: no Order found for orderId={} / clientOrderId={}. " +
                     "Recording fill as orphan.", req.orderId(), req.clientOrderId());
        }

        // Derive trading account from order, or fail
        if (order == null) {
            return FillResult.failed("Cannot record fill — no matching Order found for " +
                    "exchangeFillId=" + req.exchangeFillId() + " clientOrderId=" + req.clientOrderId());
        }

        try {
            OrderFill fill = new OrderFill(
                    order.getTradingAccount(),
                    order,
                    req.exchangeFillId(),
                    req.clientOrderId(),
                    req.deltaOrderId(),
                    req.symbol(),
                    req.side(),
                    req.fillQuantity(),
                    req.fillPrice(),
                    req.fee(),
                    req.feeAsset(),
                    req.filledAt()
            );
            fill.setRawExchangeData(req.rawExchangeData());
            fill = fillRepository.saveAndFlush(fill);

            log.info("Fill recorded: exchangeFillId={} clientOrderId={} qty={} price={}",
                    req.exchangeFillId(), req.clientOrderId(), req.fillQuantity(), req.fillPrice());

            // Recompute order aggregate and transition status in the same transaction
            updateOrderFromFills(order);

            return FillResult.recorded(fill.getId());

        } catch (DataIntegrityViolationException ex) {
            // DB unique constraint fired concurrently — treat as duplicate
            log.warn("recordFill: concurrent duplicate fill {} caught at DB constraint", req.exchangeFillId());
            return FillResult.duplicate(req.exchangeFillId());
        }
    }

    /**
     * Recomputes aggregated fill state for an order from all recorded fills
     * and transitions the order status accordingly.
     *
     * <ul>
     *   <li>If summedQty == order.quantity → transitions to {@code FILLED}</li>
     *   <li>If 0 < summedQty < order.quantity → transitions to {@code PARTIALLY_FILLED}</li>
     * </ul>
     *
     * <p>Uses the deterministic state machine; invalid transitions throw
     * {@link IllegalStateException}. Does NOT save the order — caller is
     * responsible for the surrounding transaction.</p>
     *
     * @param order the order to update
     */
    @Transactional
    public void updateOrderFromFills(Order order) {
        BigDecimal summedQty = fillRepository.sumFillQuantityByOrderId(order.getId());
        Optional<BigDecimal> avgPrice = fillRepository.computeWeightedAverageFillPrice(order.getId());

        order.setFilledQuantity(summedQty);
        avgPrice.ifPresent(order::setAverageFillPrice);

        BigDecimal ordered = order.getQuantity();
        if (ordered == null || ordered.compareTo(BigDecimal.ZERO) <= 0) {
            log.warn("updateOrderFromFills: order {} has invalid quantity {}", order.getId(), ordered);
            return;
        }

        // Compare with tolerance (scale differences)
        int cmp = summedQty.setScale(8, RoundingMode.HALF_UP)
                           .compareTo(ordered.setScale(8, RoundingMode.HALF_UP));

        OrderStatus currentStatus = order.getStatusEnum();

        if (cmp >= 0) {
            // Fully filled
            if (!currentStatus.isFilled()) {
                try {
                    order.transitionStatus(OrderStatus.FILLED);
                    order.setFilledAt(java.time.Instant.now());
                    log.info("Order {} transitioned to FILLED (filledQty={}/{})",
                            order.getClientOrderId(), summedQty, ordered);
                } catch (IllegalStateException e) {
                    log.error("Cannot transition order {} from {} to FILLED: {}",
                            order.getClientOrderId(), currentStatus, e.getMessage());
                }
            }
        } else if (summedQty.compareTo(BigDecimal.ZERO) > 0) {
            // Partially filled — only if not already in a terminal state
            if (currentStatus == OrderStatus.OPEN || currentStatus == OrderStatus.SUBMITTED) {
                try {
                    order.transitionStatus(OrderStatus.PARTIALLY_FILLED);
                    log.info("Order {} transitioned to PARTIALLY_FILLED (filledQty={}/{})",
                            order.getClientOrderId(), summedQty, ordered);
                } catch (IllegalStateException e) {
                    log.error("Cannot transition order {} from {} to PARTIALLY_FILLED: {}",
                            order.getClientOrderId(), currentStatus, e.getMessage());
                }
            }
        }

        orderRepository.saveAndFlush(order);
    }
}
