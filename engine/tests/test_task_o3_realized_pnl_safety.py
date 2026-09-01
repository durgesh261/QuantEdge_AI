"""
Task O §O3 -- a closure with no reported realized PnL must not book at zero.

Task N established the CRITICAL defect. Both position normalizers read

    Decimal(str(data.get("realised_pnl", data.get("realized_pnl", "0"))))

while Delta's documented `positions` stream update carries no `realized_pnl` at
all (the field is documented on the REST position object, described there as
"Net realized pnl since the position was opened"). The consequence was not a
cosmetic default: `handle_position_event` sees `size == 0`, calls
`handle_exchange_closure(exchange_realized_pnl=event.realized_pnl)`, and that
value was ALWAYS `Decimal("0")`. So

  * every stream-observed closure -- a winner, a stopped-out loser, a
    liquidation -- was booked as an exact break-even result, and
  * the `if exchange_realized_pnl is None:` fail-closed branch, together with
    the `CLOSURE_PNL_UNOBSERVED` alert and the reconciliation hold it raises,
    was unreachable dead code.

Evidence class: DOCUMENTATION-PROVEN for the field's absence from the stream
payload and its presence on the REST object. This file asserts the local
contract only: absent -> `None`, present -> that exact signed Decimal, and the
fail-closed branch actually reachable from a real event.

§O3 deliberately does NOT decide whether Delta's `realized_pnl` is gross or net
of fees and funding -- that is UNVERIFIED and deferred to a testnet observation.
Funding is not wired here either.
"""

import ast
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import quantedge.execution.models as execution_models
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
    DeltaPositionEvent,
    EventValidator,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.models import TradeDirection

ACCOUNT = "acc_task_o3"
USER = "user_task_o3"
SETUP = "BTCUSD_1h_MANUAL_SMC_O3_LONG"
ENTRY_ORDER_ID = "8001"
BTCUSD_PRODUCT_ID = 27
REQUESTED = Decimal("100")
ENTRY_PRICE = Decimal("95000.0")

PRODUCTION_ROOT = Path(execution_models.__file__).resolve().parents[1]


# ── Frame builders ────────────────────────────────────────────────────────────


def _position_frame(**over) -> dict:
    """A `positions` frame. Delta's stream update does NOT carry realized_pnl."""
    frame = {
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "size": "100",
        "entry_price": "95000.0",
        "mark_price": "95500.0",
        "margin": "1900",
        "leverage": "10",
        "liquidation_price": "90000.0",
    }
    frame.update(over)
    return frame


def _rest_position_frame(**over) -> dict:
    frame = dict(_position_frame(), unrealised_pnl="500.00")
    frame.update(over)
    return frame


def _order(order_id: int, *, size: Decimal = REQUESTED,
           reduce_only: bool = False,
           side: OrderSide = OrderSide.BUY) -> DeltaOrderResponse:
    return DeltaOrderResponse(
        id=order_id,
        client_order_id=f"QE_O3_{order_id}",
        user_id=1,
        product_id=BTCUSD_PRODUCT_ID,
        product_symbol="BTCUSD",
        side=side,
        order_type=OrderType.LIMIT_ORDER,
        size=size,
        unfilled_size=size,
        limit_price=ENTRY_PRICE,
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=reduce_only,
        created_at=datetime.now(timezone.utc),
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


def _position_event(size: Decimal, realized) -> DeltaPositionEvent:
    """A normalized position event whose realized PnL may legitimately be None."""
    return DeltaPositionEvent(
        symbol="BTCUSD",
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


def _fill_event(trade_id: str, *, fee, size: Decimal = REQUESTED) -> DeltaFillEvent:
    return DeltaFillEvent(
        trade_id=trade_id,
        order_id=ENTRY_ORDER_ID,
        symbol="BTCUSD",
        side=OrderSide.BUY,
        size=size,
        price=ENTRY_PRICE,
        fee=fee,
        role="taker",
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    mock = MagicMock(spec=DeltaIndiaClient)
    mock._api_key = "TEST_KEY_TASK_O3_0000000001"
    mock._api_secret = "TEST_SECRET_TASK_O3_00000000000000000001"
    placed = {"n": 0}

    async def _place(req):
        placed["n"] += 1
        return _order(9700 + placed["n"], size=req.size,
                      reduce_only=req.reduce_only, side=req.side)

    mock.place_order = AsyncMock(side_effect=_place)
    mock.cancel_order = AsyncMock(return_value={"success": True})
    mock.get_open_orders = AsyncMock(return_value=[])
    mock.get_positions = AsyncMock(return_value=[])
    mock.get_wallet_balances = AsyncMock(return_value=[_balance()])
    return mock


@pytest.fixture
def lock():
    return SingleTradeLockManager()


@pytest.fixture
def manager(client, lock):
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.algo_enabled = True
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=lock,
    )


def _live_trade(manager, lock, *,
                state=TradeLifecycleState.PROTECTED_POSITION,
                filled: Decimal = REQUESTED) -> TradeLifecycleRecord:
    """A filled, protected position -- the state a real closure arrives in."""
    record = TradeLifecycleRecord(
        setup_id=SETUP,
        account_id=ACCOUNT,
        user_id=USER,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        requested_quantity=REQUESTED,
        entry_price=ENTRY_PRICE,
        stop_loss_price=Decimal("94000.0"),
        take_profit_price=Decimal("98000.0"),
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("100"),
        reward_amount=Decimal("300"),
        entry_order_id=ENTRY_ORDER_ID,
        entry_client_order_id=f"QE_BTCUSD_ENTRY_{SETUP}",
        state=state,
    )
    record.filled_quantity = filled
    manager._active_trades[SETUP] = record
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    return record


def _normalize_stream(frame: dict) -> DeltaPositionEvent:
    return EventValidator()._normalize_position(frame)


def _alert_codes(manager) -> list:
    return [a["code"] for a in manager.reconciliation_alerts]


# ══ A-E. The stream normalization boundary ════════════════════════════════════


def test_a_the_documented_stream_frame_carries_no_realized_pnl_so_it_is_none():
    """
    The §N defect in one line. Delta's `positions` update has no `realized_pnl`,
    so the old `"0"` default fired on EVERY streamed closure.
    """
    assert _normalize_stream(_position_frame()).realized_pnl is None


def test_b_an_explicit_zero_is_an_observed_break_even_not_a_gap():
    """`"0"` present means the exchange said zero. That is a fact to keep."""
    event = _normalize_stream(_position_frame(realized_pnl="0"))
    assert event.realized_pnl == Decimal("0")
    assert event.realized_pnl is not None


def test_c_a_winning_realized_pnl_survives_exactly():
    event = _normalize_stream(_position_frame(realized_pnl="1234.56"))
    assert event.realized_pnl == Decimal("1234.56")
    assert str(event.realized_pnl) == "1234.56"  # exact, no float round-trip


def test_c_a_losing_realized_pnl_keeps_its_sign():
    event = _normalize_stream(_position_frame(realized_pnl="-987.65"))
    assert event.realized_pnl == Decimal("-987.65")
    assert event.realized_pnl < Decimal("0")


def test_d_the_british_spelling_is_read_as_the_same_field():
    """Delta's payloads use `realised_*` spellings; both must mean the same."""
    assert _normalize_stream(
        _position_frame(realised_pnl="-40.25")).realized_pnl == Decimal("-40.25")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_e_a_blank_realized_pnl_is_unobserved_rather_than_zero(blank):
    assert _normalize_stream(_position_frame(realized_pnl=blank)).realized_pnl is None


# ══ The REST position object normalizes identically ════════════════════════════


def test_the_rest_position_object_is_unobserved_when_the_field_is_absent():
    """
    `realized_pnl` IS documented on the REST position object ("Net realized pnl
    since the position was opened"), but a payload that omits it still tells us
    nothing -- and the second normalizer carried the identical `"0"` default.
    """
    assert DeltaPosition.from_dict(_rest_position_frame()).realized_pnl is None


def test_the_rest_position_object_preserves_an_observed_realized_pnl():
    pos = DeltaPosition.from_dict(_rest_position_frame(realized_pnl="-15.50"))
    assert pos.realized_pnl == Decimal("-15.50")
    assert DeltaPosition.from_dict(
        _rest_position_frame(realized_pnl="0")).realized_pnl == Decimal("0")


# ══ F. The closure that used to book at zero ═══════════════════════════════════


@pytest.mark.asyncio
async def test_f_a_flat_position_with_no_reported_pnl_does_not_close_at_zero(
        manager, lock):
    """
    The heart of §O3, and the exact scenario the old default produced on every
    single streamed closure: size 0, a genuinely filled trade, and no
    `realized_pnl` in the frame.

    Nothing may be booked. The lock is retained, the trade stays active, the
    state says reconciliation is required, and the alert that was previously
    dead code now fires.
    """
    record = _live_trade(manager, lock)

    handled = await manager.handle_position_event(_position_event(Decimal("0"), None))

    assert handled is True  # the event WAS ours; it just could not be settled
    assert "CLOSURE_PNL_UNOBSERVED" in _alert_codes(manager)
    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    assert record.state is not TradeLifecycleState.POSITION_CLOSED

    # No fabricated result anywhere on the record.
    assert record.gross_pnl is None
    assert record.net_pnl is None
    assert record.gross_pnl_source != "EXCHANGE_REALIZED_PNL"
    assert record.closed_at is None

    # Fail-safe direction: the slot is NOT recycled onto unverified accounting.
    assert SETUP in manager._active_trades
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    assert manager._trade_history == []


@pytest.mark.asyncio
async def test_f_the_flat_audit_entry_records_the_gap_as_null_not_zero(manager, lock):
    """The audit trail must let a reader tell "zero" from "never said"."""
    _live_trade(manager, lock)
    await manager.handle_position_event(_position_event(Decimal("0"), None))

    flat = [e for e in manager.state_store.audit_events
            if e["action"] == "EXCHANGE_POSITION_FLAT"][-1]["details"]
    assert flat["realized_pnl"] is None
    assert flat["realized_pnl_source"] == "UNOBSERVED"
    assert not any(e["action"] == "EXCHANGE_CLOSURE_OBSERVED"
                   for e in manager.state_store.audit_events)


# ══ G. An observed value still flows through untouched ═════════════════════════


async def _priced_live_trade(manager, lock, trade_id: str, fee: Decimal):
    """A trade whose entry executed WITH a reported commission (§O2 observed)."""
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))
    await manager.handle_fill_event(_fill_event(trade_id, fee=fee))
    manager._active_trades[SETUP].filled_quantity = REQUESTED
    return manager._active_trades[SETUP]


@pytest.mark.asyncio
async def test_g_an_observed_realized_pnl_closes_the_trade_and_raises_no_pnl_alert(
        manager, lock):
    await _priced_live_trade(manager, lock, "g1", Decimal("0.30"))

    handled = await manager.handle_position_event(
        _position_event(Decimal("0"), Decimal("1234.56")))

    assert handled is True
    assert "CLOSURE_PNL_UNOBSERVED" not in _alert_codes(manager)
    record = manager._trade_history[-1]
    assert record.state is TradeLifecycleState.POSITION_CLOSED
    assert record.gross_pnl == Decimal("1234.56")
    assert record.gross_pnl_source == "EXCHANGE_REALIZED_PNL"
    assert record.trading_fees == Decimal("0.30")
    assert record.net_pnl == Decimal("1234.26")


@pytest.mark.asyncio
async def test_g_an_observed_loss_reaches_the_record_with_its_sign(manager, lock):
    await _priced_live_trade(manager, lock, "g2", Decimal("0.30"))

    await manager.handle_position_event(
        _position_event(Decimal("0"), Decimal("-500.00")))

    record = manager._trade_history[-1]
    assert record.gross_pnl == Decimal("-500.00")
    assert record.gross_pnl_source == "EXCHANGE_REALIZED_PNL"
    assert record.net_pnl == Decimal("-500.30")


@pytest.mark.asyncio
async def test_g_an_observed_zero_is_a_real_break_even_close(manager, lock):
    """Scratched out at exactly zero: a legitimate result, closed normally."""
    await _priced_live_trade(manager, lock, "g3", Decimal("0"))

    await manager.handle_position_event(_position_event(Decimal("0"), Decimal("0")))

    record = manager._trade_history[-1]
    assert record.state is TradeLifecycleState.POSITION_CLOSED
    assert record.gross_pnl == Decimal("0")
    assert record.gross_pnl_source == "EXCHANGE_REALIZED_PNL"
    assert "CLOSURE_PNL_UNOBSERVED" not in _alert_codes(manager)


# ══ H. Winner and loser alike: absence is never a result ════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("conceptual_outcome", ["a winner", "a stopped-out loser"])
async def test_h_neither_a_winner_nor_a_loser_may_be_booked_at_zero(
        manager, lock, conceptual_outcome):
    """
    The frame is byte-identical whichever way the trade actually went, because
    the stream carries no result at all. So the ONLY safe behaviour is the same
    in both cases: refuse to book, and say why.
    """
    record = await _priced_live_trade(manager, lock, "h1", Decimal("0.30"))

    await manager.handle_position_event(_position_event(Decimal("0"), None))

    assert record.gross_pnl != Decimal("0")
    assert record.gross_pnl is None
    assert record.net_pnl is None
    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    assert "CLOSURE_PNL_UNOBSERVED" in _alert_codes(manager), conceptual_outcome


@pytest.mark.asyncio
async def test_h_a_liquidation_frame_is_no_more_settleable_than_any_other(
        manager, lock):
    """A liquidation reports no realized PnL either; it must not book at zero."""
    record = await _priced_live_trade(manager, lock, "h2", Decimal("0.30"))
    event = _normalize_stream(_position_frame(size="0"))
    assert event.realized_pnl is None

    await manager.handle_position_event(event)

    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._trade_history == []


# ══ I. Provenance can never be claimed for a value that was absent ═════════════


@pytest.mark.asyncio
async def test_i_gross_pnl_source_is_never_exchange_realized_pnl_when_absent(
        manager, lock):
    record = await _priced_live_trade(manager, lock, "i1", Decimal("0.30"))
    await manager.handle_position_event(_position_event(Decimal("0"), None))

    assert record.gross_pnl_source != "EXCHANGE_REALIZED_PNL"
    for event in manager.state_store.audit_events:
        if event["details"].get("gross_pnl_source") == "EXCHANGE_REALIZED_PNL":
            pytest.fail(f"unobserved PnL claimed exchange provenance: {event}")


# ══ The O2 x O3 interaction -- the compounding-silence path ═════════════════════


@pytest.mark.asyncio
async def test_both_gaps_together_do_not_collapse_into_a_complete_zero_closure(
        manager, lock):
    """
    The dangerous composition named in the directive: missing PnL -> 0, missing
    fee -> 0, net -> 0, closure marked complete. It is severed at the FIRST gate:
    the PnL guard returns before the fee branch, so nothing is booked, the lock
    stays held, and both silences remain individually visible -- the fee total is
    still `None` (never repaired into a zero) and the flat audit entry still says
    the realized PnL was unobserved.
    """
    record = _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                         filled=Decimal("0"))
    await manager.handle_fill_event(_fill_event("x1", fee=None))
    manager._active_trades[SETUP].filled_quantity = REQUESTED
    assert manager._observed_fill_fees[SETUP] is None  # §O2 silence

    await manager.handle_position_event(_position_event(Decimal("0"), None))

    # Nothing was booked, and no "complete" closure exists.
    assert manager._trade_history == []
    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    assert record.gross_pnl is None
    assert record.net_pnl is None
    assert not any(e["action"] == "EXCHANGE_CLOSURE_OBSERVED"
                   for e in manager.state_store.audit_events)
    assert SETUP in manager._active_trades
    assert lock.is_locked(USER, ACCOUNT)[0] is True

    # Both silences are still individually legible.
    assert manager._observed_fill_fees[SETUP] is None
    assert "CLOSURE_PNL_UNOBSERVED" in _alert_codes(manager)
    flat = [e for e in manager.state_store.audit_events
            if e["action"] == "EXCHANGE_POSITION_FLAT"][-1]["details"]
    assert flat["realized_pnl_source"] == "UNOBSERVED"


@pytest.mark.asyncio
async def test_an_observed_pnl_with_an_unobserved_fee_keeps_the_fee_gap_visible(
        manager, lock):
    """
    The other half of the interaction: one gap must not mask or repair the other.
    The PnL is real, so the closure completes -- but it completes CARRYING the
    §O2 fee gap, not a zero.
    """
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))
    await manager.handle_fill_event(_fill_event("x2", fee=None))
    manager._active_trades[SETUP].filled_quantity = REQUESTED

    await manager.handle_position_event(
        _position_event(Decimal("0"), Decimal("-500.00")))

    record = manager._trade_history[-1]
    assert record.gross_pnl == Decimal("-500.00")
    assert record.gross_pnl_source == "EXCHANGE_REALIZED_PNL"
    assert record.trading_fees is None
    assert record.trading_fees_source == "UNOBSERVED"
    assert record.net_pnl_is_cost_complete is False
    assert "CLOSURE_FEES_UNOBSERVED" in _alert_codes(manager)
    assert "CLOSURE_PNL_UNOBSERVED" not in _alert_codes(manager)


# ══ Repository invariants, not samples ═════════════════════════════════════════


def _production_sources():
    files = sorted(PRODUCTION_ROOT.rglob("*.py"))
    assert len(files) > 20, "the sweep below must actually be reading the package"
    return [(p, p.read_text(encoding="utf-8")) for p in files]


def _function(rel: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((PRODUCTION_ROOT / rel).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


def test_invariant_no_production_path_defaults_an_absent_realized_pnl_to_zero():
    """
    §O3 invariant, repository-wide. `("realised_pnl", ... "0")` is the defect
    itself; a third normalizer added anywhere under `src/quantedge/` fails here.
    """
    pattern = re.compile(r"""\.get\(\s*["']real[is]zed_pnl["']\s*,""")
    offenders = [
        f"{path.relative_to(PRODUCTION_ROOT)}:{i}"
        for path, text in _production_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "rel,name",
    [("execution/private_websocket.py", "_normalize_position"),
     ("execution/models.py", "from_dict")],
)
def test_invariant_neither_position_normalizer_carries_a_realized_pnl_default(rel, name):
    """Stronger than the text sweep: no default may exist at either boundary."""
    tree = ast.parse((PRODUCTION_ROOT / rel).read_text(encoding="utf-8"))
    cls = None
    if name == "from_dict":
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "DeltaPosition")
    scope = cls or tree
    fn = next(n for n in ast.walk(scope)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == name)
    defaults = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in ("realised_pnl", "realized_pnl")
    ]
    assert defaults == []
    assert "optional_decimal" in ast.dump(fn)


def test_invariant_the_closure_path_synthesizes_no_realized_pnl():
    """
    §O3 forbids inferring the result from price x quantity, from a balance
    change, or from fills. `handle_exchange_closure` therefore does no arithmetic
    at all: it either has the exchange's number or it has nothing.
    """
    fn = _function("execution/trade_lifecycle.py", "handle_exchange_closure")
    arithmetic = [n for n in ast.walk(fn) if isinstance(n, ast.BinOp)
                  and isinstance(n.op, (ast.Mult, ast.Div, ast.Sub, ast.Add))]
    assert arithmetic == []
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    for forbidden in ("mark_price", "entry_price", "average_fill_price",
                      "filled_quantity", "total_equity", "available_balance"):
        assert forbidden not in attrs


def test_invariant_the_flat_position_branch_derives_nothing_either():
    """`handle_position_event` passes the reported value through, or `None`."""
    fn = _function("execution/trade_lifecycle.py", "handle_position_event")
    mults = [n for n in ast.walk(fn) if isinstance(n, ast.BinOp)
             and isinstance(n.op, (ast.Mult, ast.Div))]
    assert mults == []


def test_invariant_exchange_provenance_is_unreachable_before_the_none_guard():
    """
    §O3 invariant 6, proven structurally rather than by sampling: every
    `"EXCHANGE_REALIZED_PNL"` literal in `handle_exchange_closure` lies AFTER the
    `if exchange_realized_pnl is None:` guard, and that guard's body ends in a
    bare `return`. So the label cannot be attached to an absent value.
    """
    fn = _function("execution/trade_lifecycle.py", "handle_exchange_closure")
    guards = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.ops[0], ast.Is)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "exchange_realized_pnl"
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value is None
    ]
    assert len(guards) == 1, "the fail-closed PnL guard must exist exactly once"
    guard = guards[0]
    last = guard.body[-1]
    assert isinstance(last, ast.Return)
    # `return` or `return None` -- what matters is that no record escapes.
    assert last.value is None or (
        isinstance(last.value, ast.Constant) and last.value.value is None)

    labels = [n for n in ast.walk(fn) if isinstance(n, ast.Constant)
              and n.value == "EXCHANGE_REALIZED_PNL"]
    assert labels, "the observed path must still record its provenance"
    assert all(n.lineno > guard.end_lineno for n in labels)


def test_invariant_funding_is_not_wired_by_o3():
    """
    §O3 explicitly defers funding. The closure still passes `Decimal("0")` for
    `funding_costs` and labels the source UNOBSERVED; no funding value is read
    from anywhere, which is the O7 wallet-ledger item's job.
    """
    text = (PRODUCTION_ROOT / "execution" / "trade_lifecycle.py").read_text(
        encoding="utf-8")
    assert '"funding_costs_source": "UNOBSERVED"' in text
    assert "realized_funding" not in text
    assert "wallet/transactions" not in text


# ══ Task O global guards -- unchanged by O2/O3 ═════════════════════════════════


def test_the_governance_state_is_untouched():
    """Rules 3-5: no live execution, no promotion, no algo default flip."""
    from quantedge.execution.synchronizer import AccountRecord
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AccountRecord.__dataclass_fields__["algo_enabled"].default is False
    assert AccountRecord.__dataclass_fields__["kill_switch_active"].default is True


def test_no_new_exchange_host_was_introduced():
    """Rule: do not contact a Delta host. The reachable set stays exactly these."""
    from quantedge.execution import delta_client, private_websocket

    assert delta_client.DELTA_INDIA_PRODUCTION_URL == "https://api.india.delta.exchange"
    assert delta_client.DELTA_INDIA_TESTNET_URL == "https://api-testnet.delta.exchange"
    assert private_websocket.WS_ENDPOINT == "wss://socket.india.delta.exchange"


def test_the_o3_path_reads_no_credential_material():
    for rel in ("execution/private_websocket.py", "execution/trade_lifecycle.py",
                "execution/models.py", "execution/synchronizer.py"):
        text = (PRODUCTION_ROOT / rel).read_text(encoding="utf-8")
        assert "DELTA_API_SECRET" not in text
        assert "os.environ" not in text
        assert "getenv" not in text












