"""
Task O §O1 -- a stop must be a stop on the wire, not just in the local type name.

Task N proved the CRITICAL defect this file pins shut. Delta expresses a stop as
an ordinary `order_type` plus three fields (`POST /orders` body reference):

    "order_type":          "market_order",
    "stop_order_type":     "stop_loss_order",
    "stop_price":          "56000",
    "stop_trigger_method": "last_traded_price"

`OrderType.STOP_MARKET_ORDER.to_exchange()` is `"market_order"` -- correct, and
exactly why the local type name proves nothing. Before this change the
stop-loss protection order serialized as

    {"order_type": "market_order", "stop_price": "...", "reduce_only": true}

which under the documented contract is a plain market order carrying an ignored
trigger price: it would execute immediately and close the position the moment
protection was placed. No mock could see it, because the mock accepted whatever
payload the code produced.

Evidence class: DOCUMENTATION-PROVEN. The payload field names, the
`stop_order_type` and `stop_trigger_method` enumerations, and the statement that
`order_type` is only ever `limit_order`/`market_order` all come from Delta's
published Orders API reference. Whether testnet *rejects* the old shape or
silently accepts it is a Phase-2 probe; either way it is refused locally now.

Nothing here touches strategy semantics: the SL and TP price levels are read,
never recomputed.
"""

from decimal import Decimal

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import quantedge.execution.models
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    DeltaOrderRequest,
    DeltaOrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    StopOrderContractError,
    StopOrderType,
    StopTriggerMethod,
    TimeInForce,
)
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
)

BTC = ("BTCUSD", 27)


def _req(**over):
    """A reduce-only stop-loss for BTCUSD, contract-valid unless overridden."""
    base = dict(
        product_id=BTC[1],
        product_symbol=BTC[0],
        side=OrderSide.SELL,
        order_type=OrderType.STOP_MARKET_ORDER,
        size=Decimal("10"),
        stop_price=Decimal("98500.0"),
        stop_order_type=StopOrderType.STOP_LOSS_ORDER,
        stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
        reduce_only=True,
        client_order_id="QE_O1_SL",
    )
    base.update(over)
    return DeltaOrderRequest(**base)


# ── 1. The defective shape can no longer be produced ──────────────────────────


def test_a_stop_price_without_stop_order_type_is_refused():
    """The exact §N defect: market_order + stop_price and nothing else."""
    with pytest.raises(StopOrderContractError) as exc:
        _req(stop_order_type=None, stop_trigger_method=None).to_exchange_payload()
    assert "stop_order_type" in str(exc.value)


def test_the_refusal_also_covers_a_plain_market_order_carrying_a_stop_price():
    """`MARKET_ORDER` + `stop_price` serializes identically to the defect."""
    with pytest.raises(StopOrderContractError):
        _req(order_type=OrderType.MARKET_ORDER,
             stop_order_type=None, stop_trigger_method=None).to_exchange_payload()


def test_a_stop_typed_order_with_no_stop_fields_at_all_is_refused():
    """The local name `STOP_MARKET_ORDER` is not transmitted, so it proves nothing."""
    with pytest.raises(StopOrderContractError) as exc:
        _req(stop_price=None, stop_order_type=None,
             stop_trigger_method=None).to_exchange_payload()
    assert "STOP_MARKET_ORDER" in str(exc.value)


def test_a_trigger_without_a_trigger_level_is_refused():
    with pytest.raises(StopOrderContractError) as exc:
        _req(stop_price=None).to_exchange_payload()
    assert "stop_price" in str(exc.value)


def test_a_trigger_without_a_trigger_series_is_refused():
    """An unknown exchange semantic is never resolved by a local default."""
    with pytest.raises(StopOrderContractError) as exc:
        _req(stop_trigger_method=None).to_exchange_payload()
    assert "stop_trigger_method" in str(exc.value)


# ── 2. The correct payload is what reaches the wire ───────────────────────────


def test_a_contract_valid_stop_serializes_every_documented_field():
    payload = _req().to_exchange_payload()

    assert payload["order_type"] == "market_order"
    assert payload["stop_order_type"] == "stop_loss_order"
    assert payload["stop_price"] == "98500.0"
    assert payload["stop_trigger_method"] == "last_traded_price"
    assert payload["reduce_only"] is True
    assert payload["product_id"] == 27
    assert payload["product_symbol"] == "BTCUSD"
    assert payload["size"] == 10


def test_stop_fields_are_absent_from_an_ordinary_order():
    """A resting limit entry must not acquire stop semantics."""
    payload = DeltaOrderRequest(
        product_id=BTC[1], product_symbol=BTC[0], side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER, size=Decimal("10"),
        limit_price=Decimal("99100.0"), time_in_force=TimeInForce.GTC,
    ).to_exchange_payload()

    assert "stop_order_type" not in payload
    assert "stop_trigger_method" not in payload
    assert "stop_price" not in payload
    assert payload["order_type"] == "limit_order"


def test_take_profit_order_type_is_available_and_serializes():
    """Delta documents both stop_order_type values; both must round-trip."""
    payload = _req(
        stop_order_type=StopOrderType.TAKE_PROFIT_ORDER).to_exchange_payload()
    assert payload["stop_order_type"] == "take_profit_order"


@pytest.mark.parametrize("method,wire", [
    (StopTriggerMethod.MARK_PRICE, "mark_price"),
    (StopTriggerMethod.LAST_TRADED_PRICE, "last_traded_price"),
    (StopTriggerMethod.SPOT_PRICE, "spot_price"),
])
def test_every_documented_trigger_method_serializes_exactly(method, wire):
    assert _req(stop_trigger_method=method).to_exchange_payload()[
        "stop_trigger_method"] == wire


# ── 3. The production protection path emits the corrected payload ─────────────
#
# Sections 1-2 prove the model refuses the defect. They do not prove the live
# protection path stopped producing it -- a guard is only worth the call sites
# that satisfy it. These cases drive the real `TradeLifecycleManager` and
# serialize whatever request it actually handed the client.


@pytest.fixture
def protection_client():
    """
    Records every DeltaOrderRequest instead of reaching an exchange.

    It calls `to_exchange_payload()` first, exactly as
    `DeltaIndiaClient.create_order` does before its POST. Task O rule 18 -- a
    mock is not evidence -- cuts both ways: a fake that never serializes cannot
    observe a payload-contract violation, which is precisely how the §N stop
    defect survived a green suite. Serializing here means a request this fake
    accepts is one the real client would have been able to send.
    """
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "TEST_KEY_TASK_O1_00000000001"
    client._api_secret = "TEST_SECRET_TASK_O1_000000000000000000001"
    client.submitted = []
    client.payloads = []

    def _accept(req):
        payload = req.to_exchange_payload()
        client.submitted.append(req)
        client.payloads.append(payload)
        return DeltaOrderResponse(
            id=900100 + len(client.submitted),
            client_order_id=req.client_order_id,
            user_id=1,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=req.size,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=None,
            state=OrderStatus.OPEN,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )

    client.place_order = AsyncMock(side_effect=_accept)
    client.cancel_order = AsyncMock(return_value={"success": True})
    return client


@pytest.fixture
def protection_manager(protection_client):
    store = LocalStateStore(account_id="acc_task_o1")
    store.account.user_id = "user_task_o1"
    store.account.total_equity = Decimal("25000.00")
    store.account.available_balance = Decimal("20000.00")
    store.account.margin_used = Decimal("5000.00")
    store.account.is_active = True
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return TradeLifecycleManager(
        client=protection_client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        daily_loss_limit=Decimal("500.00"),
        max_stale_seconds=120,
    )


LONG_SETUP = StrategyDecision(
    timestamp=datetime.now(timezone.utc),
    symbol="BTCUSD",
    timeframe="1h",
    direction=StrategyDirection.LONG,
    setup_state=SetupState.TRADE_SETUP_READY,
    setup_id="SETUP-TASK-O1-LONG",
    entry=Decimal("95000.00"),
    stop_loss=Decimal("94000.00"),
    take_profit=Decimal("98000.00"),
    risk_distance=Decimal("1000.00"),
    reward_distance=Decimal("3000.00"),
    risk_reward=Decimal("3.0"),
    confidence=85.0,
)


async def _fill(manager, filled=Decimal("1.0")):
    record = await manager.execute_trade_setup(LONG_SETUP, "acc_task_o1")
    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    await manager.on_entry_fill(LONG_SETUP.setup_id, filled, Decimal("95000.00"))
    return record


@pytest.mark.asyncio
async def test_the_live_stop_loss_reaches_the_wire_as_a_documented_stop(
        protection_manager, protection_client):
    """The §N defect, re-checked where it actually lived."""
    await _fill(protection_manager)

    entry, sl, tp = protection_client.submitted
    payload = sl.to_exchange_payload()

    assert payload["order_type"] == "market_order"
    assert payload["stop_order_type"] == "stop_loss_order"
    assert payload["stop_price"] == "94000.00"
    assert payload["stop_trigger_method"] == "last_traded_price"
    assert payload["reduce_only"] is True
    # Not a bare market order that fires the moment protection is placed.
    assert "stop_order_type" in payload and "stop_trigger_method" in payload


@pytest.mark.asyncio
async def test_protection_is_still_stop_loss_first_then_take_profit(
        protection_manager, protection_client):
    """§O1: SL-before-TP sequencing is preserved by the payload correction."""
    await _fill(protection_manager)

    entry, sl, tp = protection_client.submitted
    assert entry.order_type is OrderType.LIMIT_ORDER and not entry.reduce_only
    assert sl.order_type is OrderType.STOP_MARKET_ORDER
    assert sl.stop_order_type is StopOrderType.STOP_LOSS_ORDER
    assert tp.order_type is OrderType.LIMIT_ORDER and tp.reduce_only is True
    assert tp.stop_order_type is None  # a resting TP limit is not a stop


@pytest.mark.asyncio
async def test_protected_quantity_equals_filled_quantity_on_a_full_fill(
        protection_manager, protection_client):
    record = await _fill(protection_manager)

    entry, sl, tp = protection_client.submitted
    assert record.filled_quantity == Decimal("1.0")
    assert record.protected_quantity == record.filled_quantity
    assert sl.size == record.filled_quantity
    assert tp.size == record.filled_quantity


@pytest.mark.asyncio
async def test_protection_tracks_the_filled_quantity_across_a_partial_fill(
        protection_manager, protection_client):
    """
    §O1 'protection quantity equals filled quantity' must hold at every step,
    and the resized stop must still be a contract-valid stop.

    Fill quantities are whole contract counts because Delta types order `size`
    as an integer contract count (`OrderSizeContractError`); a fractional
    partial fill is not submittable at all. The record's requested quantity is
    widened locally so the 2 -> 5 resize is a partial fill of its own order
    rather than an overfill.
    """
    record = await protection_manager.execute_trade_setup(LONG_SETUP, "acc_task_o1")
    record.requested_quantity = Decimal("5")

    await protection_manager.on_entry_partial_fill(
        record.setup_id, Decimal("2"), Decimal("95000.00"))

    first_sl = [r for r in protection_client.submitted
                if r.order_type is OrderType.STOP_MARKET_ORDER][-1]
    assert first_sl.size == Decimal("2") == record.protected_quantity

    await protection_manager.on_entry_fill(
        record.setup_id, Decimal("5"), Decimal("95000.00"))

    stops = [r for r in protection_client.submitted
             if r.order_type is OrderType.STOP_MARKET_ORDER]
    assert stops[-1].size == Decimal("5") == record.protected_quantity
    assert record.protected_quantity == record.filled_quantity
    for stop in stops:
        payload = stop.to_exchange_payload()
        assert payload["stop_order_type"] == "stop_loss_order"
        assert payload["stop_trigger_method"] == "last_traded_price"
        assert payload["reduce_only"] is True


@pytest.mark.asyncio
async def test_no_protection_order_is_submitted_when_the_stop_level_is_absent(
        protection_manager, protection_client):
    """
    Fail-closed: a record with no stop level must not produce a naked TP.

    The SL is submitted first, so its refusal happens before any protective
    order reaches the exchange -- the position is never left holding
    take-profit-only 'protection', and the failure is recorded rather than
    swallowed into a PROTECTED state.
    """
    record = await protection_manager.execute_trade_setup(LONG_SETUP, "acc_task_o1")
    record.stop_loss_price = None
    before = len(protection_client.submitted)

    await protection_manager.on_entry_fill(
        record.setup_id, Decimal("1.0"), Decimal("95000.00"))

    assert protection_client.submitted[before:] == []
    assert record.state is TradeLifecycleState.PROTECTION_FAILED
    assert record.sl_order_id is None and record.tp_order_id is None
    actions = [entry.get("action") for entry
               in protection_manager.state_store.audit_events]
    assert "PROTECTION_PLACEMENT_FAILED" in actions


# ── 4. Every production construction site, not just the ones exercised ────────


PRODUCTION_ROOT = Path(
    quantedge.execution.models.__file__).resolve().parents[1]


def _stop_request_sites():
    """(file, lineno, kwargs) for every DeltaOrderRequest(...) under src/."""
    sites = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "DeltaOrderRequest"):
                sites.append((path, node.lineno,
                              {kw.arg for kw in node.keywords if kw.arg}))
    return sites


def test_the_audit_actually_found_the_production_construction_sites():
    """Guard against the sweep below passing because it found nothing."""
    assert len(_stop_request_sites()) >= 3


def test_no_production_site_can_build_a_stop_without_the_stop_contract():
    """
    An invariant rather than a sample: any construction that passes `stop_price`
    must pass both documented companions. A new protective order added anywhere
    under `src/quantedge/` fails here rather than at the exchange.
    """
    offenders = [
        f"{path.name}:{line} -> {sorted(kwargs)}"
        for path, line, kwargs in _stop_request_sites()
        if "stop_price" in kwargs
        and not {"stop_order_type", "stop_trigger_method"} <= kwargs
    ]
    assert offenders == []
