"""
Task O §O2 -- an unobserved commission must stay unobserved, never become zero.

Task N established the CRITICAL defect this file pins shut. The fill normalizer
read `data.get("fee", "0")`, but:

  * Delta's documented fill object has no `fee` field at all. The fee field is
    `commission`, and its reference notes that "negative value means commission
    was earned because of maker role".
  * Delta documents that `v2/user_trades` "doesn't contain commission data".

So the old expression read a key the exchange never sends, and substituted
`Decimal("0")` every single time. Every execution therefore looked like a
zero-fee execution, the closure path labelled the total `PRIVATE_USER_TRADES`
(observed!) because the accumulator key existed, and net P&L was booked as
though trading were free.

Evidence class: DOCUMENTATION-PROVEN. The field name, its sign convention, and
the statement that the private trade stream carries no commission all come from
Delta's published API reference. What this file asserts is the local contract:
absent -> `None`, present -> that exact signed Decimal, and no path anywhere
that turns the first case into the second.

Nothing here changes gross P&L semantics, and no fee is ever synthesized from
the pinned maker/taker rates, from `role`, or from size x price.
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
    DeltaWalletBalance,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from quantedge.execution.private_websocket import (
    DeltaFillEvent,
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

ACCOUNT = "acc_task_o2"
USER = "user_task_o2"
SETUP = "BTCUSD_1h_MANUAL_SMC_O2_LONG"
ENTRY_ORDER_ID = "7001"
BTCUSD_PRODUCT_ID = 27
REQUESTED = Decimal("100")
ENTRY_PRICE = Decimal("95000.0")

PRODUCTION_ROOT = Path(execution_models.__file__).resolve().parents[1]


# ── Frame builders ────────────────────────────────────────────────────────────


def _fill_frame(**over) -> dict:
    """A documented `user_trades` fill frame, commission included."""
    frame = {
        "id": 55001,
        "order_id": ENTRY_ORDER_ID,
        "product_symbol": "BTCUSD",
        "side": "buy",
        "size": "3",
        "price": "95000.0",
        "commission": "0.12",
        "role": "taker",
    }
    frame.update(over)
    return frame


def _normalize(frame: dict) -> DeltaFillEvent:
    return EventValidator()._normalize_fill(frame)


def _order(order_id: int, *, size: Decimal = REQUESTED,
           reduce_only: bool = False,
           side: OrderSide = OrderSide.BUY) -> DeltaOrderResponse:
    return DeltaOrderResponse(
        id=order_id,
        client_order_id=f"QE_O2_{order_id}",
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


def _fill_event(trade_id: str, *, fee, size: Decimal = Decimal("10"),
                order_id: str = ENTRY_ORDER_ID,
                role: str = "taker") -> DeltaFillEvent:
    """A normalized fill whose commission may legitimately be `None`."""
    return DeltaFillEvent(
        trade_id=trade_id,
        order_id=order_id,
        symbol="BTCUSD",
        side=OrderSide.BUY,
        size=size,
        price=ENTRY_PRICE,
        fee=fee,
        role=role,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    mock = MagicMock(spec=DeltaIndiaClient)
    mock._api_key = "TEST_KEY_TASK_O2_0000000001"
    mock._api_secret = "TEST_SECRET_TASK_O2_00000000000000000001"
    placed = {"n": 0}

    async def _place(req):
        placed["n"] += 1
        return _order(9500 + placed["n"], size=req.size,
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
    """A filled, protected position -- the state a closure actually arrives in."""
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


# ══ A-E. The normalization boundary ═══════════════════════════════════════════


def test_a_a_fill_with_no_commission_field_is_unobserved():
    """The §N defect: the exchange said nothing, so we know nothing."""
    frame = _fill_frame()
    del frame["commission"]
    assert _normalize(frame).fee is None


def test_b_a_positive_commission_is_the_exact_decimal_charged():
    event = _normalize(_fill_frame(commission="0.12"))
    assert event.fee == Decimal("0.12")
    assert str(event.fee) == "0.12"  # exact, not a float round-trip


def test_c_a_negative_commission_survives_as_a_maker_rebate():
    """Delta: "negative value means commission was earned because of maker role"."""
    event = _normalize(_fill_frame(commission="-0.12", role="maker"))
    assert event.fee == Decimal("-0.12")
    assert event.fee < Decimal("0")


def test_d_an_explicit_zero_commission_is_an_observation_not_a_gap():
    """`"0"` means the exchange charged nothing. That is a fact, not a silence."""
    event = _normalize(_fill_frame(commission="0"))
    assert event.fee == Decimal("0")
    assert event.fee is not None


def test_e_a_legacy_frame_carrying_only_fee_is_not_treated_as_commission():
    """
    `fee` is not a documented field, and `v2/user_trades` carries no commission
    data at all. A frame with `fee` and no `commission` is therefore unobserved:
    reading `fee` would resurrect the exact defect, because whatever produced it
    is not the exchange's documented commission.
    """
    frame = _fill_frame()
    del frame["commission"]
    frame["fee"] = "0.99"
    assert _normalize(frame).fee is None


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_commission_is_unobserved_rather_than_zero(blank):
    assert _normalize(_fill_frame(commission=blank)).fee is None


# ══ F-G. The accumulator ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f_observed_commissions_accumulate_as_an_exact_signed_decimal(
        manager, lock):
    """Maker rebate plus taker charge, summed exactly -- no float anywhere."""
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))

    await manager.handle_fill_event(
        _fill_event("t1", fee=Decimal("-0.12"), size=Decimal("40"), role="maker"))
    await manager.handle_fill_event(
        _fill_event("t2", fee=Decimal("0.30"), size=Decimal("60")))

    total = manager._observed_fill_fees[SETUP]
    assert total == Decimal("0.18")
    assert str(total) == "0.18"


@pytest.mark.asyncio
async def test_g_one_unpriced_leg_makes_the_whole_trade_fee_unobserved(manager, lock):
    """
    Summing only the priced legs would understate the cost while looking
    complete, so absence is contagious -- and permanent: the later priced leg
    below must not repair it.
    """
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))

    await manager.handle_fill_event(
        _fill_event("t1", fee=Decimal("0.30"), size=Decimal("40")))
    await manager.handle_fill_event(
        _fill_event("t2", fee=None, size=Decimal("30")))
    assert manager._observed_fill_fees[SETUP] is None

    await manager.handle_fill_event(
        _fill_event("t3", fee=Decimal("0.30"), size=Decimal("30")))
    assert manager._observed_fill_fees[SETUP] is None


# ══ H-I. Closure provenance ════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_h_a_closure_with_no_observed_commission_reports_it_as_unobserved(
        manager, lock):
    """
    The heart of §O2. Gross P&L is real, the fee is not, and the record says so:
    no fabricated zero, no `PRIVATE_USER_TRADES` label, a blocking alert, and a
    net figure explicitly marked as excluding an unknown cost.
    """
    _live_trade(manager, lock)

    record = await manager.handle_exchange_closure(
        SETUP, exchange_realized_pnl=Decimal("120.00"))

    assert record is not None
    assert record.trading_fees is None
    assert record.trading_fees_source == "UNOBSERVED"
    assert record.net_pnl_is_cost_complete is False
    assert "CLOSURE_FEES_UNOBSERVED" in [
        a["code"] for a in manager.reconciliation_alerts]

    audit = [e for e in manager.state_store.audit_events
             if e["action"] == "EXCHANGE_CLOSURE_OBSERVED"][-1]["details"]
    assert audit["trading_fees"] is None
    assert audit["trading_fees_source"] == "UNOBSERVED"
    # Gross P&L semantics are untouched by §O2.
    assert audit["gross_pnl"] == "120.00"
    assert record.gross_pnl == Decimal("120.00")


@pytest.mark.asyncio
async def test_h_an_unpriced_leg_cannot_be_labelled_private_user_trades(
        manager, lock):
    """A frame existing is not an observation: only a commission value is."""
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))
    await manager.handle_fill_event(_fill_event("t1", fee=None, size=REQUESTED))
    manager._active_trades[SETUP].filled_quantity = REQUESTED

    record = await manager.handle_exchange_closure(
        SETUP, exchange_realized_pnl=Decimal("50.00"))

    assert record.trading_fees is None
    assert record.trading_fees_source != "PRIVATE_USER_TRADES"
    assert record.trading_fees_source == "UNOBSERVED"


@pytest.mark.asyncio
async def test_i_an_observed_zero_commission_is_not_an_unobserved_commission(
        manager, lock):
    """A genuinely free execution must not raise the unobserved condition."""
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))
    await manager.handle_fill_event(
        _fill_event("t1", fee=Decimal("0"), size=REQUESTED))
    manager._active_trades[SETUP].filled_quantity = REQUESTED

    record = await manager.handle_exchange_closure(
        SETUP, exchange_realized_pnl=Decimal("120.00"))

    assert record.trading_fees == Decimal("0")
    assert record.trading_fees is not None
    assert record.trading_fees_source == "PRIVATE_USER_TRADES"
    assert record.net_pnl_is_cost_complete is True
    assert "CLOSURE_FEES_UNOBSERVED" not in [
        a["code"] for a in manager.reconciliation_alerts]
    assert record.net_pnl == Decimal("120.00")


# ══ L. The signed value survives the whole lifecycle ═══════════════════════════


@pytest.mark.asyncio
async def test_l_a_signed_commission_reaches_the_closed_record_unchanged(
        manager, lock):
    """
    Frame -> `_normalize_fill` -> `DeltaFillEvent` -> accumulator -> closure ->
    `record.trading_fees` -> net P&L. A maker rebate must still be a rebate at
    the end of that chain: net P&L is HIGHER than gross, not lower.
    """
    _live_trade(manager, lock, state=TradeLifecycleState.ENTRY_SUBMITTED,
                filled=Decimal("0"))

    entry = _normalize(_fill_frame(id=1, commission="-0.40", role="maker"))
    assert entry.fee == Decimal("-0.40")
    await manager.handle_fill_event(
        DeltaFillEvent(trade_id="L1", order_id=ENTRY_ORDER_ID, symbol="BTCUSD",
                       side=OrderSide.BUY, size=REQUESTED, price=ENTRY_PRICE,
                       fee=entry.fee, role=entry.role))
    manager._active_trades[SETUP].filled_quantity = REQUESTED

    record = await manager.handle_exchange_closure(
        SETUP, exchange_realized_pnl=Decimal("120.00"))

    assert record.trading_fees == Decimal("-0.40")
    assert record.trading_fees_source == "PRIVATE_USER_TRADES"
    assert record.net_pnl == Decimal("120.40")  # gross - (-0.40)
    assert record.net_pnl_is_cost_complete is True


# ══ J-K. Repository invariants, not samples ════════════════════════════════════


def _production_sources():
    files = sorted(PRODUCTION_ROOT.rglob("*.py"))
    assert len(files) > 20, "the sweep below must actually be reading the package"
    return [(p, p.read_text(encoding="utf-8")) for p in files]


def test_j_no_production_path_reads_an_undocumented_fee_field():
    """
    §O2 invariant. `data.get("fee", ...)` is the defect itself: it reads a field
    Delta does not send and substitutes a value it never reported. A new
    normalizer added anywhere under `src/quantedge/` fails here.
    """
    pattern = re.compile(r"""\.get\(\s*["']fee["']""")
    offenders = [
        f"{path.relative_to(PRODUCTION_ROOT)}:{i}"
        for path, text in _production_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_j_the_fill_normalizer_has_no_default_for_commission():
    """Stronger than the text sweep: no default may exist at the boundary."""
    tree = ast.parse((PRODUCTION_ROOT / "execution" / "private_websocket.py")
                     .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_normalize_fill")
    defaults = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in ("commission", "fee")
    ]
    assert defaults == []
    assert "optional_decimal" in ast.dump(fn)


def test_k_no_execution_path_synthesizes_a_fee_from_a_pinned_rate():
    """
    The pinned product snapshot records BTCUSD maker 0.0002 / taker 0.0005. They
    are reference data for validation, NOT a licence to compute a commission the
    exchange never reported: a rate-derived fee is a guess wearing an
    observation's clothes. Research code may model costs; the execution package
    may not.
    """
    offenders = [
        f"{path.relative_to(PRODUCTION_ROOT)}:{i}"
        for path, text in _production_sources()
        if path.parts[-2] == "execution"
        for i, line in enumerate(text.splitlines(), 1)
        if re.search(r"0\.0002|0\.0005|maker_fee|taker_fee|fee_rate", line)
    ]
    assert offenders == []


def test_k_the_fee_accumulator_never_touches_size_price_or_role():
    """A fee is added or it is unobserved; it is never reconstructed."""
    tree = ast.parse((PRODUCTION_ROOT / "execution" / "trade_lifecycle.py")
                     .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_accumulate_observed_fee")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "price" not in names and "price" not in attrs
    assert "role" not in names and "role" not in attrs
    mults = [n for n in ast.walk(fn) if isinstance(n, ast.BinOp)
             and isinstance(n.op, (ast.Mult, ast.Div))]
    assert mults == []


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

    touched = ("execution/private_websocket.py", "execution/trade_lifecycle.py",
               "execution/models.py", "execution/synchronizer.py")
    for rel in touched:
        text = (PRODUCTION_ROOT / rel).read_text(encoding="utf-8")
        hosts = set(re.findall(r"(?:https|wss)://[\w.\-]+", text))
        assert hosts <= {"https://api.india.delta.exchange",
                         "https://api-testnet.delta.exchange",
                         "wss://socket.india.delta.exchange"}, (rel, hosts)


def test_the_o2_path_reads_no_credential_material():
    """Neither the normalizer nor the accounting path touches secrets."""
    for rel in ("execution/private_websocket.py", "execution/trade_lifecycle.py"):
        text = (PRODUCTION_ROOT / rel).read_text(encoding="utf-8")
        assert "DELTA_API_SECRET" not in text
        assert "os.environ" not in text
        assert "getenv" not in text


