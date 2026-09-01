"""
Task M §M12 — targeted regression matrix for the live order/position event loop.

Every test here exercises the EXISTING execution architecture through the wiring
Task M added: a private event or a REST snapshot arrives, converges on
`_apply_entry_order_state`, and drives the EXISTING `on_entry_fill` /
`on_entry_partial_fill` / `_ensure_bracket_protection` handlers.

Groups (§M12):
  A. private event routing into the lifecycle manager
  B. entry-fill lifecycle: OPEN -> PARTIAL -> MORE PARTIAL -> FILLED
  C. bracket protection for delayed and partial fills
  D. three-candle entry expiry cancelling the real order, incl. the fill race
  E. exchange-side position closure and portfolio-slot release
  F. reconciliation: startup, divergence, orphans, missing protection, restart

SECURITY: the exchange is a `MagicMock(spec=DeltaIndiaClient)` throughout. Zero
real orders, zero network access, zero credentials.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from quantedge.execution.private_websocket import (
    DeltaFillEvent,
    DeltaOrderEvent,
    DeltaPositionEvent,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    CloseReason,
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.models import TradeDirection

ACCOUNT = "acc_task_m"
USER = "user_task_m"
SETUP = "BTCUSD_1h_MANUAL_SMC_OB-1_LONG"
ENTRY_ORDER_ID = "5001"
BTCUSD_PRODUCT_ID = 27

# 100 contracts requested; every partial below is a slice of this.
REQUESTED = Decimal("100")
ENTRY_PRICE = Decimal("95000.0")
SL_PRICE = Decimal("94000.0")
TP_PRICE = Decimal("98000.0")


def _order(
    order_id: int = int(ENTRY_ORDER_ID),
    *,
    state: OrderStatus = OrderStatus.OPEN,
    size: Decimal = REQUESTED,
    filled: Decimal = Decimal("0"),
    avg: Decimal | None = None,
    reduce_only: bool = False,
    symbol: str = "BTCUSD",
    side: OrderSide = OrderSide.BUY,
) -> DeltaOrderResponse:
    """A REST order snapshot. `filled_size` is derived as size - unfilled_size."""
    return DeltaOrderResponse(
        id=order_id,
        client_order_id=f"QE_BTCUSD_ENTRY_{order_id}",
        user_id=1,
        product_id=BTCUSD_PRODUCT_ID,
        product_symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT_ORDER,
        size=size,
        unfilled_size=size - filled,
        limit_price=ENTRY_PRICE,
        stop_price=None,
        average_fill_price=avg,
        state=state,
        reduce_only=reduce_only,
        created_at=datetime.now(timezone.utc),
    )


def _position(size: Decimal, *, realized: Decimal = Decimal("0"),
              product_id: int = BTCUSD_PRODUCT_ID,
              symbol: str = "BTCUSD") -> DeltaPosition:
    return DeltaPosition(
        product_id=product_id,
        product_symbol=symbol,
        side=PositionSide.LONG,
        size=size,
        entry_price=ENTRY_PRICE,
        mark_price=ENTRY_PRICE,
        liquidation_price=None,
        unrealized_pnl=Decimal("0"),
        realized_pnl=realized,
        leverage=Decimal("10"),
        margin=Decimal("100"),
    )


def _balance(amount: str = "10000.00") -> DeltaWalletBalance:
    return DeltaWalletBalance(
        asset_symbol="USDT",
        balance=Decimal(amount),
        available_balance=Decimal(amount),
        position_margin=Decimal("0"),
        order_margin=Decimal("0"),
        blocked_margin=Decimal("0"),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """A mock exchange: flat, funded, and accepting bracket orders."""
    mock = MagicMock(spec=DeltaIndiaClient)
    mock._api_key = "TEST_KEY_TASK_M_0000000001"
    mock._api_secret = "TEST_SECRET_TASK_M_00000000000000000001"

    placed = {"n": 0}

    async def _place(req):
        placed["n"] += 1
        return _order(
            9000 + placed["n"],
            state=OrderStatus.OPEN,
            size=req.size,
            reduce_only=req.reduce_only,
            side=req.side,
        )

    mock.place_order = AsyncMock(side_effect=_place)
    mock.cancel_order = AsyncMock(return_value={"success": True})
    mock.get_order = AsyncMock(return_value=_order())
    mock.get_order_by_client_id = AsyncMock(return_value=None)
    mock.get_open_orders = AsyncMock(return_value=[])
    mock.get_positions = AsyncMock(return_value=[])
    mock.get_wallet_balances = AsyncMock(return_value=[_balance()])
    return mock


@pytest.fixture
def store():
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return store


@pytest.fixture
def lock():
    return SingleTradeLockManager()


@pytest.fixture
def manager(client, store, lock):
    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=lock,
    )


def _resting(manager, lock, *, state=TradeLifecycleState.ENTRY_SUBMITTED,
             setup_id: str = SETUP, symbol: str = "BTCUSD",
             entry_order_id: str | None = ENTRY_ORDER_ID) -> TradeLifecycleRecord:
    """Register a trade whose LIMIT entry is resting on the exchange.

    This is the exact state the pre-Task-M defect left unattended: the order is
    live, nothing is filled, and no protection exists yet.
    """
    record = TradeLifecycleRecord(
        setup_id=setup_id,
        account_id=ACCOUNT,
        user_id=USER,
        symbol=symbol,
        direction=TradeDirection.LONG,
        requested_quantity=REQUESTED,
        entry_price=ENTRY_PRICE,
        stop_loss_price=SL_PRICE,
        take_profit_price=TP_PRICE,
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("100"),
        reward_amount=Decimal("300"),
        entry_order_id=entry_order_id,
        entry_client_order_id=f"QE_{symbol}_ENTRY_{setup_id}",
        state=state,
    )
    manager._active_trades[setup_id] = record
    lock.acquire_lock(USER, ACCOUNT, setup_id, symbol)
    return record


def _order_event(status: OrderStatus, filled: Decimal, *,
                 order_id: str = ENTRY_ORDER_ID,
                 avg: Decimal | None = ENTRY_PRICE,
                 symbol: str = "BTCUSD") -> DeltaOrderEvent:
    return DeltaOrderEvent(
        order_id=order_id,
        client_order_id=None,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=REQUESTED,
        unfilled_quantity=REQUESTED - filled,
        filled_quantity=filled,
        status=status,
        price=ENTRY_PRICE,
        average_fill_price=avg,
    )


def _fill_event(trade_id: str, size: Decimal, *, order_id: str = ENTRY_ORDER_ID,
                fee: Decimal = Decimal("0.50"),
                price: Decimal = ENTRY_PRICE,
                symbol: str = "BTCUSD") -> DeltaFillEvent:
    return DeltaFillEvent(
        trade_id=trade_id,
        order_id=order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        size=size,
        price=price,
        fee=fee,
        role="maker",
    )


def _position_event(size: Decimal, realized: Decimal = Decimal("0"),
                    symbol: str = "BTCUSD") -> DeltaPositionEvent:
    return DeltaPositionEvent(
        symbol=symbol,
        side=PositionSide.LONG,
        size=size,
        entry_price=ENTRY_PRICE,
        mark_price=ENTRY_PRICE,
        liquidation_price=None,
        unrealized_pnl=Decimal("0"),
        realized_pnl=realized,
        margin=Decimal("100"),
        leverage=Decimal("10"),
    )


def _brackets(client):
    """The reduce-only orders the manager actually sent, by order type."""
    return [
        call.args[0]
        for call in client.place_order.call_args_list
        if call.args and call.args[0].reduce_only
    ]


# ══ A. Private event routing ═══════════════════════════════════════════════════


def test_a01_binding_registers_exactly_one_observer(manager):
    """§M1: the transport gains one observer, not a second state model."""
    stream = MagicMock()
    manager.bind_private_stream(stream)

    stream.register_event_observer.assert_called_once_with(manager.observe_private_event)
    assert manager._private_stream is stream


@pytest.mark.asyncio
async def test_a02_order_event_routes_to_the_entry_handler(manager, lock):
    record = _resting(manager, lock)
    await manager.observe_private_event(_order_event(OrderStatus.FILLED, REQUESTED))

    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION


@pytest.mark.asyncio
async def test_a03_fill_event_routes_to_the_execution_handler(manager, lock):
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))

    assert manager._entry_fill_sizes[SETUP] == Decimal("30")
    assert manager._observed_fill_fees[SETUP] == Decimal("0.50")
    assert record.filled_quantity == Decimal("30")


@pytest.mark.asyncio
async def test_a04_position_event_routes_to_the_position_handler(manager, lock):
    _resting(manager, lock, state=TradeLifecycleState.PROTECTED_POSITION)
    assert await manager.handle_position_event(_position_event(REQUESTED)) is True


@pytest.mark.asyncio
async def test_a05_an_unrelated_event_is_reported_not_applied(manager, lock):
    """§M11 case A: an order the manager does not own is an orphan, not a fill."""
    record = _resting(manager, lock)
    applied = await manager.handle_order_event(
        _order_event(OrderStatus.FILLED, REQUESTED, order_id="777777")
    )

    assert applied is False
    assert record.filled_quantity == Decimal("0")


@pytest.mark.asyncio
async def test_a06_a_handler_failure_becomes_a_blocking_alert(manager, lock):
    """§M15: a failed handler leaves state unknown, so it must block entries."""
    _resting(manager, lock)
    boom = RuntimeError("state store unavailable")
    manager.handle_order_event = AsyncMock(side_effect=boom)

    # The observer must not raise: a consumer defect cannot tear down the feed.
    await manager.observe_private_event(_order_event(OrderStatus.FILLED, REQUESTED))

    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "PRIVATE_EVENT_HANDLER_FAILED" in codes


@pytest.mark.asyncio
async def test_a07_a_blocking_alert_refuses_a_new_entry(manager, store):
    """§M15: RECONCILIATION_REQUIRED, lock retained, no new entry."""
    manager._raise_reconciliation_alert("ORPHAN_EXCHANGE_POSITION", "BTCUSD", "test")

    from quantedge.strategy.models import SetupState, StrategyDecision, StrategyDirection

    decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="BTCUSD_1h_MANUAL_SMC_OB-9_LONG",
        entry=ENTRY_PRICE,
        stop_loss=SL_PRICE,
        take_profit=TP_PRICE,
        risk_distance=Decimal("1000"),
        reward_distance=Decimal("3000"),
        risk_reward=Decimal("3.0"),
        confidence=90.0,
    )
    record = await manager.execute_trade_setup(
        decision=decision, account_id=ACCOUNT, user_id=USER
    )

    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "RECONCILIATION_REQUIRED"


def test_a08_alerts_are_cleared_only_by_an_authorized_operator(manager):
    manager._raise_reconciliation_alert("ENTRY_STATE_UNKNOWN", "BTCUSD", "test")
    assert len(manager.reconciliation_alerts) == 1

    assert manager.clear_reconciliation_alerts("operator_test") == 1
    assert manager.reconciliation_alerts == []


# ══ B. Entry-fill lifecycle: OPEN -> PARTIAL -> MORE PARTIAL -> FILLED ═════════


@pytest.mark.asyncio
async def test_b01_a_resting_order_stays_open_with_no_fill(manager, lock, client):
    """An OPEN event is not a fill: no protection, no state change."""
    record = _resting(manager, lock)
    applied = await manager.handle_order_event(_order_event(OrderStatus.OPEN, Decimal("0")))

    assert applied is False
    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    assert _brackets(client) == []


@pytest.mark.asyncio
async def test_b02_a_delayed_full_fill_is_detected(manager, lock):
    """THE defect §M2 exists to fix: a LIMIT that fills later must be seen."""
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.OPEN, Decimal("0")))
    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION


@pytest.mark.asyncio
async def test_b03_a_delayed_partial_fill_is_detected(manager, lock):
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.PARTIALLY_FILLED, Decimal("30")))

    assert record.filled_quantity == Decimal("30")
    assert record.protected_quantity == Decimal("30")


@pytest.mark.asyncio
async def test_b04_multiple_partials_accumulate_monotonically(manager, lock):
    """30 -> 70 -> 100 through the `user_trades` stream alone."""
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))
    assert record.filled_quantity == Decimal("30")

    await manager.observe_private_event(_fill_event("t-2", Decimal("40")))
    assert record.filled_quantity == Decimal("70")

    await manager.observe_private_event(_fill_event("t-3", Decimal("30")))
    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION


@pytest.mark.asyncio
async def test_b05_a_duplicate_execution_never_double_counts(manager, lock):
    """§M10: the same exchange trade_id replayed after a reconnect."""
    record = _resting(manager, lock)
    fill = _fill_event("t-dup", Decimal("30"))

    await manager.observe_private_event(fill)
    await manager.observe_private_event(fill)

    assert manager._entry_fill_sizes[SETUP] == Decimal("30")
    assert manager._observed_fill_fees[SETUP] == Decimal("0.50")
    assert record.filled_quantity == Decimal("30")


@pytest.mark.asyncio
async def test_b06_an_out_of_order_stale_frame_cannot_un_fill(manager, lock):
    """§M10: a late OPEN/partial frame may not shrink an observed fill."""
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    await manager.handle_order_event(_order_event(OrderStatus.PARTIALLY_FILLED, Decimal("30")))

    assert record.filled_quantity == REQUESTED
    assert record.protected_quantity == REQUESTED


@pytest.mark.asyncio
async def test_b07_ws_partial_then_rest_full_converges_without_double_counting(
    manager, lock, client
):
    """§M10: `WS PARTIAL + REST FULL` ends at FULL, protected exactly once."""
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))
    assert record.protected_quantity == Decimal("30")

    client.get_order = AsyncMock(
        return_value=_order(state=OrderStatus.FILLED, filled=REQUESTED, avg=ENTRY_PRICE)
    )
    status = await manager.refresh_entry_from_exchange(SETUP)

    assert status == OrderStatus.FILLED
    assert record.filled_quantity == REQUESTED
    assert record.protected_quantity == REQUESTED


@pytest.mark.asyncio
async def test_b08_rest_snapshot_cannot_regress_a_ws_fill(manager, lock, client):
    """REST wins on state, but never below what was already observed."""
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    client.get_order = AsyncMock(
        return_value=_order(state=OrderStatus.PARTIALLY_FILLED, filled=Decimal("30"))
    )
    await manager.refresh_entry_from_exchange(SETUP)

    assert record.filled_quantity == REQUESTED


@pytest.mark.asyncio
async def test_b09_rest_alone_walks_open_to_filled(manager, lock, client):
    """§M1B: with no WS event at all, repeated REST calls still converge."""
    record = _resting(manager, lock)

    for state, filled in (
        (OrderStatus.OPEN, Decimal("0")),
        (OrderStatus.PARTIALLY_FILLED, Decimal("30")),
        (OrderStatus.PARTIALLY_FILLED, Decimal("70")),
        (OrderStatus.FILLED, REQUESTED),
    ):
        client.get_order = AsyncMock(
            return_value=_order(state=state, filled=filled, avg=ENTRY_PRICE)
        )
        await manager.refresh_entry_from_exchange(SETUP)

    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION


@pytest.mark.asyncio
async def test_b10_an_overfill_is_protected_and_surfaced(manager, lock):
    """Protect what the exchange says exists, and flag the divergence."""
    record = _resting(manager, lock)
    await manager.handle_order_event(
        _order_event(OrderStatus.FILLED, Decimal("120"))
    )

    assert record.protected_quantity == Decimal("120")
    assert "ENTRY_OVERFILL" in [a["code"] for a in manager.reconciliation_alerts]


@pytest.mark.asyncio
async def test_b11_an_exchange_cancellation_with_zero_fill_releases_the_slot(
    manager, lock
):
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.CANCELLED, Decimal("0"), avg=None))

    assert record.state == TradeLifecycleState.ENTRY_CANCELLED
    assert SETUP not in manager._active_trades
    assert lock.is_locked(USER, ACCOUNT)[0] is False


# ══ C. Bracket protection ══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_c01_a_delayed_full_fill_gets_a_real_server_side_sl_and_tp(
    manager, lock, client
):
    """§M2, the highest-priority requirement: real exchange-side protection."""
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    brackets = _brackets(client)
    assert len(brackets) == 2
    sl, tp = brackets
    assert sl.order_type == OrderType.STOP_MARKET_ORDER
    assert sl.stop_price == SL_PRICE
    assert sl.side == OrderSide.SELL
    assert sl.reduce_only is True
    assert tp.order_type == OrderType.LIMIT_ORDER
    assert tp.limit_price == TP_PRICE
    assert tp.reduce_only is True
    assert record.sl_order_id and record.tp_order_id


@pytest.mark.asyncio
async def test_c02_a_partial_fill_protects_exactly_the_filled_quantity(
    manager, lock, client
):
    """100 requested, 30 filled -> protection is 30, never 100."""
    record = _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.PARTIALLY_FILLED, Decimal("30")))

    assert record.protected_quantity == Decimal("30")
    assert [b.size for b in _brackets(client)] == [Decimal("30"), Decimal("30")]


@pytest.mark.asyncio
async def test_c03_protection_expands_with_the_fill_and_is_not_duplicated(
    manager, lock, client
):
    """30 -> 70 -> 100: the stale pair is cancelled, then re-placed at the new
    size. The position ends with exactly one live SL and one live TP."""
    record = _resting(manager, lock)

    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))
    assert record.protected_quantity == Decimal("30")

    await manager.observe_private_event(_fill_event("t-2", Decimal("40")))
    assert record.protected_quantity == Decimal("70")

    await manager.observe_private_event(_fill_event("t-3", Decimal("30")))
    assert record.protected_quantity == REQUESTED

    sizes = [b.size for b in _brackets(client)]
    assert sizes == [
        Decimal("30"), Decimal("30"),
        Decimal("70"), Decimal("70"),
        REQUESTED, REQUESTED,
    ]
    # Two resizes, each cancelling the previous SL and TP: 4 cancels, so no
    # stale protective order is left resting alongside the new pair.
    assert client.cancel_order.await_count == 4


@pytest.mark.asyncio
async def test_c04_existing_protection_is_not_replaced_by_a_repeated_fill(
    manager, lock, client
):
    """§M2: a duplicate FILLED observation must not place a second bracket."""
    _resting(manager, lock)
    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))
    placed_after_first = client.place_order.await_count

    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    assert client.place_order.await_count == placed_after_first


@pytest.mark.asyncio
async def test_c05_a_failed_resize_cancel_refuses_to_add_a_second_bracket(
    manager, lock, client
):
    """Two live reduce-only stops on one position is the duplicate protection
    §M2 forbids, so a failed cancel stops the resize and blocks entries."""
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))
    placed = client.place_order.await_count

    client.cancel_order = AsyncMock(side_effect=RuntimeError("cancel rejected"))
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.OPEN))
    await manager.observe_private_event(_fill_event("t-2", Decimal("40")))

    assert client.place_order.await_count == placed
    assert record.state == TradeLifecycleState.PROTECTION_FAILED
    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "PROTECTION_RESIZE_CANCEL_FAILED" in codes


@pytest.mark.asyncio
async def test_c06_a_filled_bracket_blocks_a_resize_rather_than_reprotecting(
    manager, lock, client
):
    """A FILLED SL means the position is closing; protection must not be
    rebuilt on top of that."""
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("t-1", Decimal("30")))

    client.cancel_order = AsyncMock(side_effect=RuntimeError("order not open"))
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.FILLED, filled=REQUESTED))
    await manager.observe_private_event(_fill_event("t-2", Decimal("40")))

    assert record.state == TradeLifecycleState.PROTECTION_FAILED


@pytest.mark.asyncio
async def test_c07_a_fill_that_cannot_be_protected_blocks_further_entries(
    manager, lock, client
):
    """§M15: filled but unprotected is the worst state; it must fail closed."""
    record = _resting(manager, lock)
    client.place_order = AsyncMock(side_effect=RuntimeError("exchange rejected SL"))

    await manager.handle_order_event(_order_event(OrderStatus.FILLED, REQUESTED))

    assert record.state == TradeLifecycleState.PROTECTION_FAILED
    assert "POSITION_UNPROTECTED" in [a["code"] for a in manager.reconciliation_alerts]


# ══ D. Three-candle entry expiry cancels the REAL order ════════════════════════


@pytest.mark.asyncio
async def test_d01_expiry_sends_a_real_cancel_to_the_exchange(manager, lock, client):
    """§M3: the window closing must reach the exchange, not just local state."""
    _resting(manager, lock)
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.CANCELLED))

    await manager.expire_resting_entry(SETUP)

    client.cancel_order.assert_awaited_once_with(int(ENTRY_ORDER_ID), BTCUSD_PRODUCT_ID)


@pytest.mark.asyncio
async def test_d02_a_confirmed_cancellation_releases_the_portfolio_slot(
    manager, lock, client
):
    record = _resting(manager, lock)
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.CANCELLED))

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "CANCELLED"
    assert result["cancelled"] is True
    assert result["lock_retained"] is False
    assert record.state == TradeLifecycleState.ENTRY_TIMEOUT
    assert lock.is_locked(USER, ACCOUNT)[0] is False
    assert SETUP not in manager._active_trades


@pytest.mark.asyncio
async def test_d03_a_rejected_cancel_is_still_cancelled_if_the_exchange_says_so(
    manager, lock, client
):
    """A rejected cancel usually means the order is already terminal. The
    exchange's own order state decides, not the HTTP error."""
    _resting(manager, lock)
    client.cancel_order = AsyncMock(side_effect=RuntimeError("order not open"))
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.EXPIRED))

    result = await manager.expire_resting_entry(SETUP)

    assert result == {
        "setup_id": SETUP, "outcome": "CANCELLED",
        "cancelled": True, "lock_retained": False,
    }


@pytest.mark.asyncio
async def test_d04_an_unverifiable_cancel_fails_closed(manager, lock, client):
    """§M3/§M15: cancel timeout with no authoritative answer -> lock retained."""
    record = _resting(manager, lock)
    client.cancel_order = AsyncMock(side_effect=TimeoutError("cancel timed out"))
    client.get_order = AsyncMock(side_effect=TimeoutError("verification timed out"))

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "RECONCILIATION_REQUIRED"
    assert result["cancelled"] is False
    assert result["lock_retained"] is True
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    assert "ENTRY_EXPIRY_UNVERIFIED" in [a["code"] for a in manager.reconciliation_alerts]


@pytest.mark.asyncio
async def test_d05_an_order_still_open_after_the_cancel_fails_closed(
    manager, lock, client
):
    """The cancel did not take effect: a stale resting order could still create
    an untracked position, so nothing may assume it is gone."""
    record = _resting(manager, lock)
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.OPEN))

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "RECONCILIATION_REQUIRED"
    assert result["lock_retained"] is True
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "ENTRY_EXPIRY_CANCEL_UNCONFIRMED" in codes


@pytest.mark.asyncio
async def test_d06_the_expiry_versus_fill_race_is_decided_by_the_exchange(
    manager, lock, client
):
    """§M3, explicitly required: expiry detected -> cancel requested -> exchange
    says already FILLED. That is NOT a cancellation. The fill is reconciled and
    the existing protection flow runs."""
    record = _resting(manager, lock)
    client.cancel_order = AsyncMock(side_effect=RuntimeError("order already filled"))
    client.get_order = AsyncMock(
        return_value=_order(state=OrderStatus.FILLED, filled=REQUESTED, avg=ENTRY_PRICE)
    )

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "FILLED_NOT_CANCELLED"
    assert result["cancelled"] is False
    assert result["lock_retained"] is True
    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert [b.size for b in _brackets(client)] == [REQUESTED, REQUESTED]
    assert lock.is_locked(USER, ACCOUNT)[0] is True


@pytest.mark.asyncio
async def test_d07_a_partial_fill_at_expiry_protects_the_filled_part(
    manager, lock, client
):
    """The unfilled remainder is cancelled; the 30 that exist are protected."""
    record = _resting(manager, lock)
    client.get_order = AsyncMock(
        return_value=_order(
            state=OrderStatus.PARTIALLY_FILLED, filled=Decimal("30"), avg=ENTRY_PRICE
        )
    )

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "PARTIALLY_FILLED"
    assert result["cancelled"] is False
    assert result["filled_quantity"] == "30"
    assert record.protected_quantity == Decimal("30")
    assert [b.size for b in _brackets(client)] == [Decimal("30"), Decimal("30")]


@pytest.mark.asyncio
async def test_d08_a_protected_position_is_never_cancelled_by_an_expiry(
    manager, lock, client
):
    """Nothing is resting once the position is protected; cancelling there would
    act on a stale assumption and could remove real protection."""
    _resting(manager, lock, state=TradeLifecycleState.PROTECTED_POSITION)

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "NOT_RESTING"
    assert result["cancelled"] is False
    client.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_d09_expiry_without_an_exchange_order_touches_nothing(
    manager, lock, client
):
    _resting(manager, lock, entry_order_id=None)
    manager._active_trades[SETUP].entry_client_order_id = None

    result = await manager.expire_resting_entry(SETUP)

    assert result["outcome"] == "NO_EXCHANGE_ORDER"
    client.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_d10_expiry_of_an_unknown_setup_is_a_no_op(manager, client):
    result = await manager.expire_resting_entry("BTCUSD_1h_MANUAL_SMC_OB-404_LONG")

    assert result["outcome"] == "NO_ACTIVE_TRADE"
    client.cancel_order.assert_not_awaited()


# ══ E. Exchange-side closure and portfolio-slot release ════════════════════════


async def _filled_and_protected(manager, lock, client):
    """Drive a trade to a real protected position through the event loop."""
    record = _resting(manager, lock)
    await manager.observe_private_event(_fill_event("entry-1", REQUESTED))
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    return record


@pytest.mark.asyncio
async def test_e01_a_stop_loss_execution_closes_the_trade_as_stop_loss(
    manager, lock, client
):
    """§M5: the reason comes from which protective order actually executed on the
    exchange, never from strategy OHLC."""
    record = await _filled_and_protected(manager, lock, client)

    await manager.observe_private_event(
        _fill_event("exit-sl", REQUESTED, order_id=record.sl_order_id, fee=Decimal("1.25"))
    )
    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("-97.50")))

    assert record.close_reason == CloseReason.STOP_LOSS
    assert record.state == TradeLifecycleState.POSITION_CLOSED


@pytest.mark.asyncio
async def test_e02_a_take_profit_execution_closes_the_trade_as_take_profit(
    manager, lock, client
):
    record = await _filled_and_protected(manager, lock, client)

    await manager.observe_private_event(
        _fill_event("exit-tp", REQUESTED, order_id=record.tp_order_id, fee=Decimal("1.25"))
    )
    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("300.00")))

    assert record.close_reason == CloseReason.TAKE_PROFIT
    assert record.state == TradeLifecycleState.POSITION_CLOSED


@pytest.mark.asyncio
async def test_e03_closure_needs_no_strategy_candle(manager, lock, client, store):
    """The whole point of §M5: the exchange closed the position, and the local
    close happens from that observation alone -- no 1H candle, no strategy tick."""
    record = await _filled_and_protected(manager, lock, client)
    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("250.00")))

    assert record.state == TradeLifecycleState.POSITION_CLOSED
    assert "EXCHANGE_CLOSURE_OBSERVED" in [e["action"] for e in store.audit_events]


@pytest.mark.asyncio
async def test_e04_closure_releases_the_portfolio_slot(manager, lock, client):
    """§M18: the slot is released only after a confirmed exchange closure."""
    await _filled_and_protected(manager, lock, client)
    assert lock.is_locked(USER, ACCOUNT)[0] is True

    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("250.00")))

    assert lock.is_locked(USER, ACCOUNT)[0] is False
    assert SETUP not in manager._active_trades


@pytest.mark.asyncio
async def test_e05_realized_values_come_only_from_authoritative_exchange_data(
    manager, lock, client
):
    """`gross_pnl` is the exchange's realized PnL; `trading_fees` is the sum of
    the observed execution fees; the balance is read back from the wallet. None
    of the three is derived from the strategy's theoretical prices."""
    record = await _filled_and_protected(manager, lock, client)  # entry fee 0.50
    await manager.observe_private_event(
        _fill_event("exit-sl", REQUESTED, order_id=record.sl_order_id, fee=Decimal("1.25"))
    )
    client.get_wallet_balances = AsyncMock(return_value=[_balance("10152.25")])

    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("-97.50")))

    assert record.gross_pnl == Decimal("-97.50")
    assert record.trading_fees == Decimal("1.75")  # 0.50 entry + 1.25 exit
    assert record.post_trade_balance == Decimal("10152.25")
    # Fabricating funding from a rate is forbidden, so it stays zero and the
    # audit trail records it as unobserved rather than as a real cost.
    assert record.funding_costs == Decimal("0")


@pytest.mark.asyncio
async def test_e06_the_existing_closure_rescan_flow_receives_the_real_numbers(
    manager, lock, client
):
    """§M5: closure runs the EXISTING handler, with the six values it expects."""
    seen = {}

    async def _handler(**kwargs):
        seen.update(kwargs)
        return None

    manager.register_closure_handler(_handler)
    record = await _filled_and_protected(manager, lock, client)
    await manager.observe_private_event(
        _fill_event("exit-tp", REQUESTED, order_id=record.tp_order_id, fee=Decimal("2.00"))
    )

    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("300.00")))

    assert seen["setup_id"] == SETUP
    assert seen["reason"] == CloseReason.TAKE_PROFIT
    assert seen["gross_pnl"] == Decimal("300.00")
    assert seen["trading_fees"] == Decimal("2.50")
    assert seen["funding_costs"] == Decimal("0")
    assert seen["final_exchange_balance"] == Decimal("10000.00")


@pytest.mark.asyncio
async def test_e07_a_closure_without_realized_pnl_is_deferred_not_invented(
    manager, lock, client
):
    """§M15: never fabricate P&L. The close waits for authoritative data."""
    record = await _filled_and_protected(manager, lock, client)

    result = await manager.handle_exchange_closure(SETUP, exchange_realized_pnl=None)

    assert result is None
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert "CLOSURE_PNL_UNOBSERVED" in [a["code"] for a in manager.reconciliation_alerts]
    assert lock.is_locked(USER, ACCOUNT)[0] is True


@pytest.mark.asyncio
async def test_e08_unobserved_fees_are_surfaced_rather_than_estimated(
    manager, lock, client
):
    """No fee is back-computed from a rate: the gap becomes a blocking alert."""
    _resting(manager, lock, state=TradeLifecycleState.PROTECTED_POSITION)
    manager._active_trades[SETUP].filled_quantity = REQUESTED

    await manager.handle_exchange_closure(SETUP, exchange_realized_pnl=Decimal("120.00"))

    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "CLOSURE_FEES_UNOBSERVED" in codes
    # Task O §O2: strengthened. This previously asserted `Decimal("0")`, which is
    # the fabricated zero the alert was warning about -- the record claimed a
    # free trade while the alert said the cost was unknown. Unobserved now stays
    # unobserved on the record too, and net PnL is marked cost-incomplete.
    closed = manager._trade_history[-1]
    assert closed.trading_fees is None
    assert closed.trading_fees_source == "UNOBSERVED"
    assert closed.net_pnl_is_cost_complete is False


@pytest.mark.asyncio
async def test_e09_an_unreadable_wallet_is_surfaced_rather_than_assumed(
    manager, lock, client
):
    record = await _filled_and_protected(manager, lock, client)
    client.get_wallet_balances = AsyncMock(side_effect=RuntimeError("wallet unavailable"))

    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("120.00")))

    assert record.state == TradeLifecycleState.POSITION_CLOSED
    assert "CLOSURE_BALANCE_UNOBSERVED" in [a["code"] for a in manager.reconciliation_alerts]


@pytest.mark.asyncio
async def test_e10_a_flat_position_with_no_local_fill_never_closes_a_trade(
    manager, lock, client
):
    """A resting, never-filled entry is not a closed position."""
    record = _resting(manager, lock)

    await manager.observe_private_event(_position_event(Decimal("0")))

    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    assert SETUP in manager._active_trades


@pytest.mark.asyncio
async def test_e11_closure_is_idempotent_across_duplicate_flat_snapshots(
    manager, lock, client
):
    """§M10: `SL event + TP event + position snapshot` must converge once."""
    record = await _filled_and_protected(manager, lock, client)
    await manager.observe_private_event(
        _fill_event("exit-sl", REQUESTED, order_id=record.sl_order_id)
    )

    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("-97.50")))
    await manager.observe_private_event(_position_event(Decimal("0"), Decimal("-97.50")))

    assert len([r for r in manager._trade_history if r.setup_id == SETUP]) == 1


# ══ F. Reconciliation: startup, divergence, orphans, restart ═══════════════════


@pytest.mark.asyncio
async def test_f01_a_clean_run_converges_a_resting_entry_from_rest_alone(
    manager, lock, client
):
    """§M4 direction of authority: exchange snapshot -> local convergence."""
    _resting(manager, lock)
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.OPEN))
    client.get_open_orders = AsyncMock(return_value=[_order(state=OrderStatus.OPEN)])

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["checked"] == 1
    assert summary["converged"] == [SETUP]
    assert summary["unresolved"] == []
    assert summary["orphan_orders"] == []


@pytest.mark.asyncio
async def test_f02_an_unreachable_exchange_blocks_rather_than_assumes(
    manager, lock, client
):
    """§M15: no exchange answer means no conclusion at all."""
    _resting(manager, lock)
    client.get_positions = AsyncMock(side_effect=RuntimeError("connection reset"))

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["exchange_unreachable"] is True
    assert summary["unresolved"] == ["EXCHANGE_UNREACHABLE"]
    assert "EXCHANGE_UNREACHABLE" in [a["code"] for a in manager.reconciliation_alerts]


@pytest.mark.asyncio
async def test_f03_a_local_position_missing_on_the_exchange_retains_the_lock(
    manager, lock, client
):
    """§M11 case D: local ACTIVE with no exchange position. Closure needs
    authoritative realized values a position snapshot cannot supply, so this
    fails closed instead of being closed with invented numbers."""
    record = await _filled_and_protected(manager, lock, client)
    client.get_positions = AsyncMock(return_value=[])

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["unresolved"] == [SETUP]
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "LOCAL_POSITION_MISSING_ON_EXCHANGE" in codes
    assert SETUP in manager._active_trades


@pytest.mark.asyncio
async def test_f04_an_orphan_exchange_position_is_reported_never_guessed(
    manager, lock, client, store
):
    """§M11 case C: protective levels for an unknown position are unknowable, so
    no bracket may be derived -- it blocks trading instead."""
    client.get_positions = AsyncMock(
        return_value=[_position(Decimal("40"), product_id=3136, symbol="ETHUSD")]
    )

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["orphan_positions"] == ["ETHUSD"]
    assert "ORPHAN_EXCHANGE_POSITION" in [a["code"] for a in manager.reconciliation_alerts]
    assert "RECONCILIATION_ORPHAN_POSITION" in [e["action"] for e in store.audit_events]
    # Reported, not "fixed": no order of any kind was sent for it.
    client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_f05_an_orphan_exchange_order_is_reported_not_cancelled(
    manager, lock, client, store
):
    """Safety rule #9: an unexplained order is data, not something to act on."""
    client.get_open_orders = AsyncMock(return_value=[_order(6001, state=OrderStatus.OPEN)])

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["orphan_orders"] == ["6001"]
    assert "ORPHAN_EXCHANGE_ORDER" in [a["code"] for a in manager.reconciliation_alerts]
    assert "RECONCILIATION_ORPHAN_ORDER" in [e["action"] for e in store.audit_events]
    client.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_f06_a_position_whose_brackets_vanished_is_reprotected(
    manager, lock, client, store
):
    """§M11 case E/F: the exchange holds the position but the protective orders
    are gone. The EXISTING bracket path rebuilds them for the real size."""
    record = await _filled_and_protected(manager, lock, client)
    stale_sl, stale_tp = record.sl_order_id, record.tp_order_id
    client.get_positions = AsyncMock(return_value=[_position(REQUESTED)])
    client.get_open_orders = AsyncMock(return_value=[])  # neither bracket is live

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["protection_restored"] == [SETUP]
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.protected_quantity == REQUESTED
    assert record.sl_order_id not in (None, stale_sl)
    assert record.tp_order_id not in (None, stale_tp)
    assert "RECONCILIATION_PROTECTION_MISSING" in [e["action"] for e in store.audit_events]


@pytest.mark.asyncio
async def test_f07_protection_is_resized_to_the_real_exchange_position(
    manager, lock, client
):
    """The exchange says 70 contracts exist; protection must be 70, not 100."""
    record = await _filled_and_protected(manager, lock, client)
    client.get_positions = AsyncMock(return_value=[_position(Decimal("70"))])
    client.get_open_orders = AsyncMock(return_value=[])

    await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert record.protected_quantity == Decimal("70")
    assert [b.size for b in _brackets(client)][-2:] == [Decimal("70"), Decimal("70")]


@pytest.mark.asyncio
async def test_f08_live_protection_is_recognized_and_not_replaced(
    manager, lock, client
):
    """§M12: existing protection must be recognized, so reconciliation does not
    churn a healthy position into new bracket orders."""
    record = await _filled_and_protected(manager, lock, client)
    placed = client.place_order.await_count
    client.get_positions = AsyncMock(return_value=[_position(REQUESTED)])
    client.get_open_orders = AsyncMock(
        return_value=[
            _order(int(record.sl_order_id), state=OrderStatus.OPEN, reduce_only=True),
            _order(int(record.tp_order_id), state=OrderStatus.OPEN, reduce_only=True),
        ]
    )

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert client.place_order.await_count == placed
    assert summary["protection_restored"] == []
    assert summary["unresolved"] == []
    assert summary["orphan_orders"] == []


@pytest.mark.asyncio
async def test_f09_a_clean_run_is_the_only_thing_that_clears_a_block(
    manager, lock, client
):
    """Self-healing is allowed only on positive exchange confirmation."""
    manager._raise_reconciliation_alert("ENTRY_STATE_UNKNOWN", "BTCUSD", "earlier ambiguity")

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["alerts_cleared"] == 1
    assert manager.reconciliation_alerts == []


@pytest.mark.asyncio
async def test_f10_reconciliation_recovers_a_restart_with_a_resting_order(
    manager, lock, client
):
    """§M6 scenario A: local memory says SUBMITTED; the exchange says it filled
    while the process was down. No duplicate entry is placed -- the existing
    fill and protection path runs off the snapshot."""
    record = _resting(manager, lock)
    client.get_order = AsyncMock(
        return_value=_order(state=OrderStatus.FILLED, filled=REQUESTED, avg=ENTRY_PRICE)
    )
    client.get_positions = AsyncMock(return_value=[_position(REQUESTED)])

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["converged"] == [SETUP]
    assert record.filled_quantity == REQUESTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    entries = [
        call.args[0] for call in client.place_order.call_args_list
        if not call.args[0].reduce_only
    ]
    assert entries == []


@pytest.mark.asyncio
async def test_f11_reconciliation_recovers_a_restart_with_a_dead_order(
    manager, lock, client
):
    """§M6: the order died while the process was down, so the slot is freed."""
    _resting(manager, lock)
    client.get_order = AsyncMock(return_value=_order(state=OrderStatus.CANCELLED))

    await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert SETUP not in manager._active_trades
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_f12_an_unresolvable_entry_state_is_left_unresolved(
    manager, lock, client
):
    record = _resting(manager, lock)
    client.get_order = AsyncMock(side_effect=RuntimeError("gateway timeout"))

    summary = await manager.reconcile_active_trades_with_exchange(account_id=ACCOUNT)

    assert summary["unresolved"] == [SETUP]
    assert summary["converged"] == []
    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    assert lock.is_locked(USER, ACCOUNT)[0] is True


# ── F(ii). Reconciliation is reachable from production wiring (§M4 A/B/E) ──────


def _wired_runtime(manager):
    """A runtime whose orchestrator exposes the real lifecycle manager."""
    from quantedge.runtime.manual_smc_runtime import ManualSMCRuntime

    orchestrator = MagicMock()
    orchestrator.lifecycle_manager = manager
    orchestrator.handle_trade_closure_and_rescan = AsyncMock(return_value=None)
    return ManualSMCRuntime(
        symbols=["BTCUSD"], timeframe="1h", orchestrator=orchestrator,
        account_id=ACCOUNT,
    ), orchestrator


@pytest.mark.asyncio
async def test_f13_startup_reconciliation_is_reachable_from_the_runtime(manager):
    """§M4 case A: `reconcile_account()` and active-trade convergence both run
    from one production entry point."""
    runtime, _ = _wired_runtime(manager)
    service = MagicMock()
    service.reconcile_account = AsyncMock(return_value={"ok": True})
    runtime.bind_execution_feeds(reconciliation_service=service)

    report = await runtime.reconcile()

    service.reconcile_account.assert_awaited_once_with(account_id=ACCOUNT, user_id=None)
    assert report["account"] == {"ok": True}
    assert "trades" in report
    assert runtime.reconciliations_run == 1


@pytest.mark.asyncio
async def test_f14_every_private_ws_reconnect_triggers_reconciliation(manager):
    """§M4 case B/D: the hook the transport calls on each successful connect."""
    runtime, _ = _wired_runtime(manager)
    stream = MagicMock()
    runtime.bind_execution_feeds(private_stream=stream)

    stream.register_reconciliation_hook.assert_called_once_with(
        runtime._reconciliation_hook
    )
    stream.register_event_observer.assert_called_once_with(manager.observe_private_event)

    await runtime._reconciliation_hook()
    await runtime._reconciliation_hook()

    assert runtime.reconciliations_run == 2


@pytest.mark.asyncio
async def test_f15_a_failing_account_reconciliation_never_hides_the_trade_pass(
    manager,
):
    """The trade-level pass is the fail-closed one, so it must still run."""
    runtime, _ = _wired_runtime(manager)
    service = MagicMock()
    service.reconcile_account = AsyncMock(side_effect=RuntimeError("account API down"))
    runtime.bind_execution_feeds(reconciliation_service=service)

    report = await runtime.reconcile()

    assert "account API down" in report["account_error"]
    assert report["trades"]["checked"] == 0


def test_f16_the_existing_closure_rescan_flow_is_the_bound_handler(manager):
    """§M5: the runtime binds the orchestrator's existing flow, not a new one."""
    runtime, orchestrator = _wired_runtime(manager)
    runtime.bind_execution_feeds()

    assert manager._closure_handler is orchestrator.handle_trade_closure_and_rescan
