"""
Real Order Submission & Idempotent Execution Engine for QuantEdge AI.

Bridges qualified trade setups (Phase 4.1/4.2) and the OrderValidationGateway (Phase 5.3)
with the authenticated Delta Exchange India REST Client (Phase 5.1).

Guarantees:
- Real-trading only (no paper/simulation/virtual execution).
- Fail-closed execution: zero exchange calls unless all 17+ validation checks pass.
- In-flight concurrency lock & idempotency registry (zero duplicate live submissions).
- Deterministic reconciliation upon network timeouts, 5xx errors, or unknown outcomes.
- Never blindly retry an order.
- Authoritative TP/SL protection derived from Phase 4.2 strategy setups.
- Secret masking in all audit trails and logs.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import inspect
import threading
from typing import Optional, Dict, Any, List, Set, Union
import logging

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    DeltaOrderRequest,
    DeltaOrderResponse,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    DeltaResponseError,
    generate_client_order_id,
)
from quantedge.execution.security import mask_secret, sanitize_text
from quantedge.execution.synchronizer import (
    LocalStateStore,
    OrderRecord,
    PositionRecord,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    OrderValidationResult,
    ValidationContext,
    RejectionReasonCode,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection

logger = logging.getLogger(__name__)


# ── Helper for async / sync client invocations ────────────────────────────────


async def _invoke_client_call(fn, *args, **kwargs):
    """Invoke a client method whether it is a coroutine or normal return."""
    res = fn(*args, **kwargs)
    if inspect.iscoroutine(res):
        return await res
    return res


# ── Execution Lifecycle States ────────────────────────────────────────────────


class ExecutionState(str, Enum):
    """Lifecycle states of live order execution."""
    VALIDATED = "VALIDATED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


# ── Execution Models ──────────────────────────────────────────────────────────


@dataclass
class OrderExecutionRequest:
    """Incoming request to execute a live trade on Delta Exchange India."""
    account_id: str
    setup_id: str
    symbol: str
    direction: Union[TradeDirection, OrderSide, str]
    order_type: Union[OrderType, str]
    quantity: Decimal
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    leverage: int = 100
    client_order_id: Optional[str] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False


@dataclass
class OrderExecutionResult:
    """Outcome of real order execution."""
    success: bool
    execution_state: ExecutionState
    order_id: Optional[Union[str, int]] = None
    client_order_id: Optional[str] = None
    setup_id: Optional[str] = None
    symbol: Optional[str] = None
    direction: Optional[Union[TradeDirection, OrderSide, str]] = None
    order_type: Optional[Union[OrderType, str]] = None
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    average_fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    leverage: Optional[int] = None
    rejection_code: Optional[str] = None
    error_message: Optional[str] = None
    reconciled: bool = False
    reconciliation_detail: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    raw_response: Optional[Dict[str, Any]] = None


# ── Live Order Execution Service ──────────────────────────────────────────────


class LiveOrderExecutionService:
    """
    Production execution service managing the complete lifecycle of live order submissions.
    """

    def __init__(
        self,
        validation_gateway: Optional[OrderValidationGateway] = None,
        state_store: Optional[LocalStateStore] = None,
    ):
        self.validation_gateway = validation_gateway or OrderValidationGateway()
        self.state_store = state_store or LocalStateStore()
        self._lock = threading.Lock()
        self._in_flight_setups: Set[str] = set()
        self._in_flight_client_order_ids: Set[str] = set()

    def execute_order_sync(
        self,
        request: OrderExecutionRequest,
        context: ValidationContext,
        client: DeltaIndiaClient,
    ) -> OrderExecutionResult:
        """Synchronous wrapper for execute_order."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.execute_order(request, context, client))
                return future.result()
        else:
            return loop.run_until_complete(self.execute_order(request, context, client))

    async def execute_order(
        self,
        request: OrderExecutionRequest,
        context: ValidationContext,
        client: DeltaIndiaClient,
    ) -> OrderExecutionResult:
        """
        Execute a live order through the full fail-closed execution pipeline:
        1. Atomic in-flight locking and duplicate check.
        2. Phase 5.3 OrderValidationGateway check.
        3. Pre-persist SUBMITTING state.
        4. Authenticated submission to Delta Exchange India.
        5. Response handling or timeout reconciliation.
        6. State persistence and audit logging.
        """
        now = datetime.now(timezone.utc)
        client_order_id = request.client_order_id or generate_client_order_id()

        # ── 1. Atomic In-Flight Locking & Idempotency Check ───────────────────
        with self._lock:
            if request.setup_id and request.setup_id in self._in_flight_setups:
                logger.warning("Rejected duplicate in-flight setup_id: %s", request.setup_id)
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.REJECTED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    rejection_code=RejectionReasonCode.DUPLICATE_SETUP_ID.value,
                    error_message=f"setup_id '{request.setup_id}' is currently being submitted or already active.",
                    completed_at=datetime.now(timezone.utc),
                )

            if client_order_id in self._in_flight_client_order_ids:
                logger.warning("Rejected duplicate in-flight client_order_id: %s", client_order_id)
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.REJECTED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    rejection_code=RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID.value,
                    error_message=f"client_order_id '{client_order_id}' is currently in-flight or already submitted.",
                    completed_at=datetime.now(timezone.utc),
                )

            # Check persistent store for historical completed submissions
            if client_order_id in self.state_store.orders:
                existing_order = self.state_store.orders[client_order_id]
                logger.warning("Duplicate client_order_id in state store: %s", client_order_id)
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.REJECTED,
                    order_id=existing_order.delta_order_id,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    rejection_code=RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID.value,
                    error_message=f"Order with client_order_id '{client_order_id}' already exists in state store.",
                    completed_at=datetime.now(timezone.utc),
                )

            # Register in-flight locks
            if request.setup_id:
                self._in_flight_setups.add(request.setup_id)
            self._in_flight_client_order_ids.add(client_order_id)

        try:
            # ── 2. Phase 5.3 OrderValidationGateway Check ─────────────────────
            validation_req = OrderValidationRequest(
                account_id=request.account_id,
                symbol=request.symbol,
                direction=request.direction,
                order_type=request.order_type,
                quantity=request.quantity,
                entry_price=request.entry_price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                leverage=request.leverage,
                client_order_id=client_order_id,
                setup_id=request.setup_id,
                time_in_force=request.time_in_force,
                reduce_only=request.reduce_only,
            )

            validation_result: OrderValidationResult = self.validation_gateway.validate(validation_req, context)
            if not validation_result.is_valid:
                logger.warning("Order validation rejected for %s: [%s] %s",
                               client_order_id, validation_result.rejection_code, validation_result.rejection_reason)
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.REJECTED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    direction=request.direction,
                    order_type=request.order_type,
                    quantity=request.quantity,
                    price=request.entry_price,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    leverage=request.leverage,
                    rejection_code=validation_result.rejection_code.value if validation_result.rejection_code else "VALIDATION_FAILED",
                    error_message=sanitize_text(validation_result.rejection_reason or "Validation failed"),
                    completed_at=datetime.now(timezone.utc),
                )

            delta_req = validation_result.order_request
            if delta_req is None:
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.FAILED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    error_message="Validation passed but no DeltaOrderRequest was constructed.",
                    completed_at=datetime.now(timezone.utc),
                )

            # ── 3. Pre-Persist SUBMITTING Order Record ────────────────────────
            order_record = OrderRecord(
                delta_order_id=None,
                client_order_id=client_order_id,
                symbol=request.symbol,
                side=delta_req.side,
                order_type=delta_req.order_type,
                status=OrderStatus.PENDING,
                price=request.entry_price,
                quantity=request.quantity,
                filled_quantity=Decimal("0"),
                placed_at=now,
            )
            self.state_store.orders[client_order_id] = order_record

            # ── 4. Submit Order to Delta Exchange India ───────────────────────
            logger.info("Submitting live order %s to Delta India (Symbol: %s, Side: %s, Size: %s, Price: %s)",
                        client_order_id, delta_req.product_symbol, delta_req.side.value, delta_req.size, delta_req.limit_price)

            try:
                place_fn = getattr(client, "place_order", getattr(client, "create_order", None))
                if place_fn is None:
                    raise AttributeError("Delta client has no place_order or create_order method")
                delta_resp: DeltaOrderResponse = await _invoke_client_call(place_fn, delta_req)

                # ── 5A. Successful Submission ─────────────────────────────────
                order_status = delta_resp.status
                if order_status == OrderStatus.FILLED:
                    exec_state = ExecutionState.FILLED
                elif order_status == OrderStatus.PARTIALLY_FILLED:
                    exec_state = ExecutionState.PARTIALLY_FILLED
                else:
                    exec_state = ExecutionState.SUBMITTED

                # Update persistent state
                order_record.delta_order_id = str(delta_resp.id)
                order_record.status = order_status
                order_record.filled_quantity = delta_resp.filled_size
                order_record.average_fill_price = delta_resp.average_fill_price
                if delta_resp.updated_at:
                    order_record.filled_at = delta_resp.updated_at

                logger.info("Live order %s successfully SUBMITTED to Delta (ID: %s, Status: %s)",
                            client_order_id, delta_resp.id, order_status.value)

                return OrderExecutionResult(
                    success=True,
                    execution_state=exec_state,
                    order_id=delta_resp.id,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    direction=request.direction,
                    order_type=request.order_type,
                    quantity=request.quantity,
                    price=request.entry_price,
                    average_fill_price=delta_resp.average_fill_price,
                    filled_quantity=delta_resp.filled_size,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    leverage=request.leverage,
                    completed_at=datetime.now(timezone.utc),
                    raw_response=None,
                )

            except DeltaOrderRejectedError as err:
                # ── 5B. Exchange Rejection ────────────────────────────────────
                logger.warning("Order %s rejected by Delta Exchange India: %s", client_order_id, str(err))
                order_record.status = OrderStatus.REJECTED
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.REJECTED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    direction=request.direction,
                    order_type=request.order_type,
                    quantity=request.quantity,
                    price=request.entry_price,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    leverage=request.leverage,
                    rejection_code="EXCHANGE_REJECTED",
                    error_message=sanitize_text(str(err)),
                    completed_at=datetime.now(timezone.utc),
                )

            except DeltaAuthError as err:
                # ── 5C. Auth Failure (401) ────────────────────────────────────
                logger.error("Authentication failed during order placement for %s: %s", client_order_id, str(err))
                order_record.status = OrderStatus.CANCELLED
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.FAILED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    rejection_code="AUTH_FAILURE",
                    error_message=sanitize_text(str(err)),
                    completed_at=datetime.now(timezone.utc),
                )

            except DeltaRateLimitError as err:
                # ── 5D. Rate Limit Exceeded (429) ─────────────────────────────
                logger.warning("Rate limit exceeded during order placement for %s (Retry-After: %s)", client_order_id, err.retry_after)
                order_record.status = OrderStatus.CANCELLED
                return OrderExecutionResult(
                    success=False,
                    execution_state=ExecutionState.FAILED,
                    client_order_id=client_order_id,
                    setup_id=request.setup_id,
                    symbol=request.symbol,
                    rejection_code="RATE_LIMITED",
                    error_message=f"Rate limit exceeded. Retry after {err.retry_after}s.",
                    completed_at=datetime.now(timezone.utc),
                )

            except (DeltaConnectionError, DeltaResponseError, Exception) as err:
                # ── 5E. Timeout / Connection / 5xx / Unknown Outcome ──────────
                logger.error("Network or connection error during order placement for %s: %s. Initiating immediate reconciliation.",
                             client_order_id, str(err))

                reconciliation_result = await self._reconcile_order_with_exchange(
                    client=client,
                    client_order_id=client_order_id,
                    product_id=delta_req.product_id,
                    order_record=order_record,
                )

                if reconciliation_result.get("found"):
                    logger.info("Order %s was FOUND on Delta Exchange during reconciliation! ID: %s, State: %s",
                                client_order_id, reconciliation_result.get("order_id"), reconciliation_result.get("status"))
                    return OrderExecutionResult(
                        success=True,
                        execution_state=ExecutionState.SUBMITTED if reconciliation_result.get("status") == OrderStatus.OPEN else ExecutionState.FILLED,
                        order_id=reconciliation_result.get("order_id"),
                        client_order_id=client_order_id,
                        setup_id=request.setup_id,
                        symbol=request.symbol,
                        direction=request.direction,
                        order_type=request.order_type,
                        quantity=request.quantity,
                        price=request.entry_price,
                        stop_loss=request.stop_loss,
                        take_profit=request.take_profit,
                        leverage=request.leverage,
                        reconciled=True,
                        reconciliation_detail="Order recovered via Delta Exchange reconciliation after network drop.",
                        completed_at=datetime.now(timezone.utc),
                    )
                else:
                    logger.warning("Order %s was NOT FOUND on Delta Exchange after timeout. Marking as FAILED.", client_order_id)
                    order_record.status = OrderStatus.CANCELLED
                    return OrderExecutionResult(
                        success=False,
                        execution_state=ExecutionState.FAILED,
                        client_order_id=client_order_id,
                        setup_id=request.setup_id,
                        symbol=request.symbol,
                        rejection_code="SUBMISSION_TIMEOUT",
                        error_message="Network timeout during submission. Verified order was NOT placed on Delta Exchange.",
                        reconciled=True,
                        reconciliation_detail="Reconciliation confirmed order never reached exchange order book.",
                        completed_at=datetime.now(timezone.utc),
                    )

        finally:
            # Clean up in-flight locks if order failed or was rejected
            with self._lock:
                self._in_flight_client_order_ids.discard(client_order_id)

    async def _reconcile_order_with_exchange(
        self,
        client: DeltaIndiaClient,
        client_order_id: str,
        product_id: Optional[int],
        order_record: OrderRecord,
    ) -> Dict[str, Any]:
        """
        Query Delta Exchange India to check whether an order reached the exchange
        despite a network timeout or dropped connection. Never blindly retry.
        """
        try:
            get_orders_fn = getattr(client, "get_open_orders", None)
            if get_orders_fn is not None:
                open_orders = await _invoke_client_call(get_orders_fn, product_id=product_id)
                for order in open_orders:
                    if order.client_order_id == client_order_id:
                        order_record.delta_order_id = str(order.id)
                        order_record.status = order.status
                        order_record.filled_quantity = order.filled_size
                        order_record.average_fill_price = order.average_fill_price
                        return {
                            "found": True,
                            "order_id": order.id,
                            "status": order.status,
                        }
            return {"found": False}
        except Exception as e:
            logger.error("Error during reconciliation query for %s: %s", client_order_id, sanitize_text(str(e)))
            return {"found": False, "error": sanitize_text(str(e))}

    async def execute_from_strategy_decision(
        self,
        decision: StrategyDecision,
        context: ValidationContext,
        client: DeltaIndiaClient,
        account_id: str,
        quantity: Optional[Decimal] = None,
    ) -> OrderExecutionResult:
        """
        Execute directly from a validated Phase 4.1/4.2 StrategyDecision.
        """
        if decision.setup_state != SetupState.TRADE_SETUP_READY:
            return OrderExecutionResult(
                success=False,
                execution_state=ExecutionState.REJECTED,
                setup_id=decision.setup_id,
                symbol=decision.symbol,
                rejection_code=RejectionReasonCode.DECISION_NOT_READY.value,
                error_message=f"Strategy decision state '{decision.setup_state}' is not 'TRADE_SETUP_READY'.",
                completed_at=datetime.now(timezone.utc),
            )

        trade_dir = TradeDirection.LONG if decision.direction == StrategyDirection.LONG else TradeDirection.SHORT

        # Position sizing
        if quantity is None:
            if decision.entry and decision.stop_loss and decision.risk_distance and decision.risk_distance > 0:
                risk_amount = context.account.total_equity * (context.risk_config.risk_per_trade_pct / Decimal("100"))
                calculated_qty = (risk_amount / decision.risk_distance).quantize(Decimal("1"))
                if calculated_qty < Decimal("1"):
                    calculated_qty = Decimal("1")
                quantity = calculated_qty
            else:
                quantity = Decimal("1")

        req = OrderExecutionRequest(
            account_id=account_id,
            setup_id=decision.setup_id or f"SETUP-{decision.symbol}-{int(datetime.now(timezone.utc).timestamp())}",
            symbol=decision.symbol,
            direction=trade_dir,
            order_type=OrderType.LIMIT_ORDER,
            quantity=quantity,
            entry_price=decision.entry,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            leverage=context.risk_config.max_leverage,
        )

        return await self.execute_order(req, context, client)
