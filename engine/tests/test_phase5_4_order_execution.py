"""
Phase 5.4 — Real Order Submission & Idempotent Execution Test Suite.

Verifies:
- LiveOrderExecutionService integration with OrderValidationGateway and DeltaIndiaClient
- Successful market and limit order submissions to Delta Exchange India
- Fail-closed execution: zero exchange calls on validation failure, kill switch, disabled algo, or inactive account
- In-flight locking and idempotency: protection against double clicks and concurrent submissions
- Network timeout handling and immediate reconciliation query (no blind retries)
- Auth (401), Rate limit (429), 5xx, and exchange rejection error classifications
- Authoritative TP/SL geometry enforcement
- Exact Decimal precision preservation across financial calculations
- Zero credentials/secrets leaked in execution error messages
- 100% mocked HTTP transport — ZERO live orders placed on exchange.
"""

from datetime import datetime, timezone
from decimal import Decimal
import threading
from unittest.mock import MagicMock, AsyncMock
import pytest

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
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    DeltaResponseError,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    LocalStateStore,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    ValidationContext,
    RiskConfiguration,
    RejectionReasonCode,
)
from quantedge.execution.execution_engine import (
    ExecutionState,
    OrderExecutionRequest,
    OrderExecutionResult,
    LiveOrderExecutionService,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def execution_context():
    """Create healthy, valid account and connection context."""
    account = AccountRecord(
        account_id="acc_live_01",
        base_currency="USDT",
        current_balance=Decimal("10000.00"),
        available_balance=Decimal("10000.00"),
        margin_used=Decimal("0.00"),
        total_equity=Decimal("10000.00"),
        is_active=True,
    )
    connection = ConnectionRecord(
        connection_status="CONNECTED",
        last_connected_at=datetime.now(timezone.utc),
    )
    risk_config = RiskConfiguration(
        risk_per_trade_pct=Decimal("35.0"),
        target_reward_pct=Decimal("60.0"),
        max_leverage=100,
        max_concurrent_trades=1,
        minimum_risk_reward=Decimal("1.5"),
    )
    return ValidationContext(
        account=account,
        algo_enabled=True,
        kill_switch_active=False,
        connection=connection,
        api_key="valid_delta_api_key_123456",
        api_secret="valid_delta_api_secret_654321",
        risk_config=risk_config,
        open_positions=[],
        open_orders=[],
        active_client_order_ids=set(),
        active_setup_ids=set(),
    )


@pytest.fixture
def valid_execution_request():
    """Create a valid LONG execution request on BTCUSD."""
    return OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-BULLISH-BTC-101",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        leverage=50,
        client_order_id="QE-1724260001-01",
    )


@pytest.fixture
def mock_delta_client():
    """Create mock Delta India client with successful default responses."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "valid_delta_api_key_123456"
    client._api_secret = "valid_delta_api_secret_654321"

    # Default place_order response
    client.place_order = AsyncMock(return_value=DeltaOrderResponse(
        id=998877,
        client_order_id="QE-1724260001-01",
        user_id=1,
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("2"),
        unfilled_size=Decimal("2"),
        limit_price=Decimal("95000.0"),
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    ))
    client.create_order = client.place_order
    client.get_open_orders = AsyncMock(return_value=[])
    return client


# ── 1. Positive Order Execution Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_limit_order_execution(execution_context, valid_execution_request, mock_delta_client):
    """Verify valid limit order is validated, submitted, and returned with SUBMITTED state."""
    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is True
    assert result.execution_state == ExecutionState.SUBMITTED
    assert result.order_id == 998877
    assert result.client_order_id == "QE-1724260001-01"
    assert result.setup_id == "SETUP-BULLISH-BTC-101"
    assert result.quantity == Decimal("2")
    assert result.price == Decimal("95000.0")
    assert result.stop_loss == Decimal("94000.0")
    assert result.take_profit == Decimal("97000.0")

    # Verify Delta client was called exactly once with correct payload
    mock_delta_client.place_order.assert_called_once()
    req_arg = mock_delta_client.place_order.call_args[0][0]
    assert req_arg.product_symbol == "BTCUSD"
    assert req_arg.side == OrderSide.BUY
    assert req_arg.size == Decimal("2")
    assert req_arg.limit_price == Decimal("95000.0")
    assert req_arg.stop_loss_price == Decimal("94000.0")
    assert req_arg.take_profit_price == Decimal("97000.0")


@pytest.mark.asyncio
async def test_successful_market_order_execution(execution_context, valid_execution_request, mock_delta_client):
    """Verify market order submission returning FILLED state."""
    valid_execution_request.order_type = OrderType.MARKET_ORDER
    mock_delta_client.place_order.return_value = DeltaOrderResponse(
        id=123,
        client_order_id=valid_execution_request.client_order_id,
        user_id=1,
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET_ORDER,
        size=Decimal("2"),
        unfilled_size=Decimal("0"),
        limit_price=None,
        stop_price=None,
        average_fill_price=Decimal("95010.5"),
        state=OrderStatus.FILLED,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is True
    assert result.execution_state == ExecutionState.FILLED
    assert result.order_id == 123
    assert result.average_fill_price == Decimal("95010.5")
    assert result.filled_quantity == Decimal("2")


# ── 2. Fail-Closed Validation Tests (Zero Exchange Calls) ─────────────────────


@pytest.mark.asyncio
async def test_validation_rejection_blocks_exchange_call(execution_context, valid_execution_request, mock_delta_client):
    """Verify validation rejection halts execution immediately with 0 exchange calls."""
    valid_execution_request.quantity = Decimal("0")  # Invalid quantity

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_kill_switch_active_blocks_order(execution_context, valid_execution_request, mock_delta_client):
    """Verify kill switch active blocks exchange call."""
    execution_context.kill_switch_active = True

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_algo_disabled_blocks_order(execution_context, valid_execution_request, mock_delta_client):
    """Verify algo_enabled=False blocks exchange call."""
    execution_context.algo_enabled = False

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.ALGO_DISABLED.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_inactive_account_blocks_order(execution_context, valid_execution_request, mock_delta_client):
    """Verify inactive account blocks exchange call."""
    execution_context.account.is_active = False

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.ACCOUNT_DISABLED.value
    mock_delta_client.place_order.assert_not_called()


# ── 3. Idempotency and Concurrency Protection ─────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_click_protection(execution_context, valid_execution_request, mock_delta_client):
    """Verify submitting the same request twice is blocked by idempotency."""
    service = LiveOrderExecutionService()
    res1 = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)
    assert res1.success is True

    # Second click with same client_order_id / setup_id
    res2 = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)
    assert res2.success is False
    assert res2.execution_state == ExecutionState.REJECTED
    assert res2.rejection_code in (RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID.value, RejectionReasonCode.DUPLICATE_SETUP_ID.value)
    # Delta client should only have been called once across both attempts
    assert mock_delta_client.place_order.call_count == 1


def test_concurrent_duplicate_requests_atomic_protection(execution_context, valid_execution_request, mock_delta_client):
    """Verify concurrent multi-threaded submissions for same setup_id produce exactly 1 live order."""
    service = LiveOrderExecutionService()
    results = []

    def run_submission(order_idx):
        req = OrderExecutionRequest(
            account_id="acc_live_01",
            setup_id="CONCURRENT-SETUP-999",
            symbol="BTCUSD",
            direction=TradeDirection.LONG,
            order_type=OrderType.LIMIT_ORDER,
            quantity=Decimal("2"),
            entry_price=Decimal("95000.0"),
            stop_loss=Decimal("94000.0"),
            take_profit=Decimal("97000.0"),
            leverage=50,
            client_order_id=f"QE-CONCURRENT-{order_idx}",
        )
        res = service.execute_order_sync(req, execution_context, mock_delta_client)
        results.append(res)

    threads = [threading.Thread(target=run_submission, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success]
    rejections = [r for r in results if not r.success]

    assert len(successes) == 1
    assert len(rejections) == 4
    assert mock_delta_client.place_order.call_count == 1


# ── 4. Timeout & Network Error Reconciliation ─────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_followed_by_successful_reconciliation(execution_context, valid_execution_request, mock_delta_client):
    """Verify network timeout on place_order queries Delta, finds order, and marks as SUBMITTED (reconciled=True)."""
    # place_order raises timeout
    mock_delta_client.place_order.side_effect = DeltaConnectionError("Connection timeout to Delta India")

    # get_open_orders returns the order that reached exchange
    mock_delta_client.get_open_orders.return_value = [
        DeltaOrderResponse(
            id=1001,
            client_order_id=valid_execution_request.client_order_id,
            user_id=1,
            product_id=27,
            product_symbol="BTCUSD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_ORDER,
            size=Decimal("2"),
            unfilled_size=Decimal("2"),
            limit_price=Decimal("95000.0"),
            stop_price=None,
            average_fill_price=None,
            state=OrderStatus.OPEN,
            reduce_only=False,
            created_at=datetime.now(timezone.utc),
        )
    ]

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is True
    assert result.execution_state == ExecutionState.SUBMITTED
    assert result.order_id == 1001
    assert result.reconciled is True
    assert "recovered via Delta Exchange reconciliation" in result.reconciliation_detail


@pytest.mark.asyncio
async def test_timeout_reconciliation_order_not_found(execution_context, valid_execution_request, mock_delta_client):
    """Verify network timeout where order never reached exchange resolves to FAILED (reconciled=True)."""
    mock_delta_client.place_order.side_effect = DeltaConnectionError("Connection reset by peer")
    # get_open_orders returns empty list (order never reached exchange)
    mock_delta_client.get_open_orders.return_value = []

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.FAILED
    assert result.rejection_code == "SUBMISSION_TIMEOUT"
    assert result.reconciled is True


# ── 5. Delta Error Hierarchy Handling ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_delta_401_auth_error_handling(execution_context, valid_execution_request, mock_delta_client):
    """Verify HTTP 401 DeltaAuthError is captured and mapped to FAILED."""
    mock_delta_client.place_order.side_effect = DeltaAuthError("Invalid API key or signature")

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.FAILED
    assert result.rejection_code == "AUTH_FAILURE"


@pytest.mark.asyncio
async def test_delta_429_rate_limit_handling(execution_context, valid_execution_request, mock_delta_client):
    """Verify HTTP 429 DeltaRateLimitError is captured and mapped to FAILED."""
    mock_delta_client.place_order.side_effect = DeltaRateLimitError("Too Many Requests", retry_after=5)

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.FAILED
    assert result.rejection_code == "RATE_LIMITED"
    assert "Retry after 5s" in result.error_message


@pytest.mark.asyncio
async def test_exchange_rejected_order_handling(execution_context, valid_execution_request, mock_delta_client):
    """Verify exchange rejection (e.g. price outside collar) is captured and mapped to REJECTED."""
    mock_delta_client.place_order.side_effect = DeltaOrderRejectedError("Order price exceeds price collar limit")

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == "EXCHANGE_REJECTED"
    assert "price collar limit" in result.error_message


# ── 6. Precision, Secret Redaction, and Strategy Integration ──────────────────


@pytest.mark.asyncio
async def test_deterministic_client_order_id_generation(execution_context, valid_execution_request, mock_delta_client):
    """Verify client_order_id auto-generation format."""
    valid_execution_request.client_order_id = None  # let service auto-generate

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is True
    assert result.client_order_id.startswith("QE-")


@pytest.mark.asyncio
async def test_decimal_precision_preservation(execution_context, valid_execution_request, mock_delta_client):
    """Verify Decimal types are preserved without float conversion."""
    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert isinstance(result.quantity, Decimal)
    assert isinstance(result.price, Decimal)
    assert isinstance(result.stop_loss, Decimal)
    assert isinstance(result.take_profit, Decimal)


@pytest.mark.asyncio
async def test_invalid_tp_sl_geometry_rejection(execution_context, valid_execution_request, mock_delta_client):
    """Verify inverted SL/TP (e.g. LONG with SL > Entry) is rejected prior to submission."""
    valid_execution_request.stop_loss = Decimal("96000.0")  # SL > Entry for LONG is invalid

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_audit_log_secret_redaction(execution_context, valid_execution_request, mock_delta_client):
    """Verify sensitive credentials are never displayed in execution error messages."""
    mock_delta_client.place_order.side_effect = Exception("Failed with secret: valid_delta_api_secret_654321")
    mock_delta_client.get_open_orders.return_value = []

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert "valid_delta_api_secret_654321" not in str(result.error_message)


@pytest.mark.asyncio
async def test_strategy_decision_integration(execution_context, mock_delta_client):
    """Verify direct execution from a validated StrategyDecision(TRADE_SETUP_READY)."""
    decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-DECISION-EXEC-01",
        entry=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        risk_distance=Decimal("1000.0"),
        reward_distance=Decimal("2000.0"),
        risk_reward=Decimal("2.0"),
        confidence=90.0,
    )

    service = LiveOrderExecutionService()
    result = await service.execute_from_strategy_decision(
        decision=decision,
        context=execution_context,
        client=mock_delta_client,
        account_id="acc_live_01",
        quantity=Decimal("2"),
    )

    assert result.success is True
    assert result.execution_state == ExecutionState.SUBMITTED
    assert result.setup_id == "SETUP-DECISION-EXEC-01"
    assert result.quantity == Decimal("2")


@pytest.mark.asyncio
async def test_delta_5xx_server_error_and_reconciliation(execution_context, valid_execution_request, mock_delta_client):
    """Verify HTTP 500 server error triggers immediate reconciliation."""
    mock_delta_client.place_order.side_effect = DeltaConnectionError("Delta Exchange server error (HTTP 500)")
    mock_delta_client.get_open_orders.return_value = []

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.FAILED
    assert result.reconciled is True


@pytest.mark.asyncio
async def test_malformed_response_handling(execution_context, valid_execution_request, mock_delta_client):
    """Verify DeltaResponseError triggers reconciliation without crashing."""
    mock_delta_client.place_order.side_effect = DeltaResponseError("Malformed JSON in order response")
    mock_delta_client.get_open_orders.return_value = []

    service = LiveOrderExecutionService()
    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.FAILED
    assert result.reconciled is True


@pytest.mark.asyncio
async def test_short_order_execution_and_geometry(execution_context, mock_delta_client):
    """Verify valid SHORT order (SL > Entry > TP) is submitted with SELL side."""
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-BEARISH-BTC-202",
        symbol="BTCUSD",
        direction=TradeDirection.SHORT,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("96000.0"),
        take_profit=Decimal("93000.0"),
        leverage=50,
        client_order_id="QE-SHORT-001",
    )
    mock_delta_client.place_order.return_value = DeltaOrderResponse(
        id=776655,
        client_order_id="QE-SHORT-001",
        user_id=1,
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("2"),
        unfilled_size=Decimal("2"),
        limit_price=Decimal("95000.0"),
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    )

    service = LiveOrderExecutionService()
    result = await service.execute_order(req, execution_context, mock_delta_client)

    assert result.success is True
    assert result.execution_state == ExecutionState.SUBMITTED
    assert result.direction == TradeDirection.SHORT


@pytest.mark.asyncio
async def test_order_persistence_and_lifecycle_updates(execution_context, valid_execution_request, mock_delta_client):
    """Verify order records in local state store are correctly updated through execution."""
    store = LocalStateStore()
    service = LiveOrderExecutionService(state_store=store)

    result = await service.execute_order(valid_execution_request, execution_context, mock_delta_client)

    assert result.success is True
    assert valid_execution_request.client_order_id in store.orders
    rec = store.orders[valid_execution_request.client_order_id]
    assert rec.delta_order_id == "998877"
    assert rec.status == OrderStatus.OPEN
    assert rec.quantity == Decimal("2")

