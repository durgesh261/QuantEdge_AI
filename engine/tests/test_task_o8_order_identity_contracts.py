"""
Task O §O8 -- `DeltaOrderResponse.from_dict` must never invent an ORDER identity.

The parse read `int(data.get("id", 0))`, which failed in six distinct ways, not
one:

  * key absent, `0`, `"0"` or `False`  -> order 0, an identity the exchange never
    sent;
  * `True`                             -> order 1, a phantom identity belonging
    to a DIFFERENT REAL order;
  * `3.7` / `Decimal("5.5")`           -> silently truncated to order 3 / 5,
    again a different real order;
  * `None` / `[1]`                     -> `TypeError` leaking out of a parse
    boundary untyped;
  * `""` / `"  "` / `"abc"`            -> `ValueError`, likewise untyped.

An identity cannot be defaulted the way a number can. Every consumer immediately
stringifies this field, and `"0"` is *actionable*:

  * `synchronizer._reconcile_orders` uses `str(eo.id)` as a `state_store.orders`
    KEY and as a membership token, so every id-less order collapses onto one
    record and the stale-order inference then believes the local order is still
    open -- it is never marked FILLED or CANCELLED;
  * `reconciliation` and the §M11 orphan scan key the same way, so a genuinely
    orphaned exchange order is skipped as "claimed";
  * `multi_user_orchestrator` issues `GET /v2/orders/0`, and `cancel_order`'s
    `int()` guard passes `0` straight into a `DELETE /v2/orders {"id": 0}` body;
  * `execution_engine` reports it inside a `success=True` result;
  * decisively, `trade_lifecycle` already asserts `if not record.sl_order_id or
    not record.tp_order_id: raise RuntimeError("Exchange failed to confirm SL/TP
    bracket order IDs")` -- and `str(0)` is `"0"`, which is TRUTHY. The
    repository had already written down the requirement that the exchange must
    confirm bracket ids, and the parser silently satisfied it with a fabricated
    value. A stop-loss believed confirmed but unidentifiable is the worst single
    outcome in this codebase.

Order id is now resolved exactly like `product_id` (§ the same shape at
`models.py:829-840`): bool and `None` refused outright, exactness required so
truncation never decides which order this is, and `DeltaResponseError` raised --
the contract `delta_client` already uses for an order-identity violation
("refusing to adopt an order that is not ours" in `get_order_by_client_id`).
Zero is refused: nothing in this repository or in Delta's documentation
establishes it as a legitimate order id, and it is the exact value the old
default fabricated. Sign is not judged and no upper bound is invented.

The identity ordering symbol -> product_id -> state -> order id is deliberate and
asserted below, so `from_dict({})` still raises `UnknownInstrumentError`.

Out of scope by decision, recorded here so they are not mistaken for oversights:
the Path-B lock asymmetry in `multi_user_orchestrator` (I1) and the WS fill
identity `str(data.get("order_id", ""))` (I2) each have their own task.

Zero network access: every request is served by `httpx.MockTransport`.
"""

import ast
import inspect
import json
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

import quantedge.execution.models as execution_models
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
    DeltaResponseError,
)
from quantedge.execution.models import (
    DeltaOrderRequest,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    UnknownOrderStateError,
)
from quantedge.execution.private_websocket import EventValidator
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    AccountRecord,
    LiveAccountSyncService,
    LocalStateStore,
    OrderRecord,
    PositionRecord,
    PositionStatus,
    SyncResult,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments import UnknownInstrumentError, delta_india_registry
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
    TradeDirection,
)

PRODUCTION_ROOT = Path(execution_models.__file__).resolve().parents[1]

ACCOUNT = "acc_task_o8"
USER = "user_task_o8"
SETUP = "BTCUSD_1h_MANUAL_SMC_O8_LONG"
BTCUSD = "BTCUSD"
ENTRY_PRICE = Decimal("95000.0")
VALID_ORDER_ID = 910001

#: Every value that must NOT become an order id. `0`/`"0"`/`False` are refused
#: for the reason the whole task exists; the rest are refused for exactness or
#: type. Absence is tested separately because it has no value to parametrize.
MALFORMED_IDS = (
    None, "", "   ", "\t", "\n", "abc", "1.2.3", "--5", "5-", "0x1b",
    "twenty", "NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf",
    True, False, 3.7, "3.7", Decimal("5.5"), Decimal("NaN"),
    Decimal("Infinity"), [1], (1,), {"id": 1}, b"27", "1,000", " ", "+",
    0, "0", 0.0, Decimal("0"), "-0", "00", " 0 ",
)

#: Values that ARE usable identities: exactly integral, either sign, no invented
#: upper bound. Every one of these must survive the parse unchanged.
EXACT_IDS = (
    (910001, 910001), ("910001", 910001), (" 910001 ", 910001),
    (910001.0, 910001), (Decimal("910001"), 910001), (1, 1), ("1", 1),
    (-5, -5), ("-5", -5), (-910001, -910001), (10 ** 19, 10 ** 19),
    (Decimal("-3"), -3), ("+7", 7), ("1e3", 1000),
)


# ── Transport plumbing (identical in shape to §O4/§O6/§O7) ────────────────────


class Recorder:
    """Captures every request a client makes, so the wire can be asserted on."""

    def __init__(self, responder):
        self.requests: List[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]

    def paths(self) -> List[str]:
        return [r.url.path for r in self.requests]

    def body(self, index: int = -1) -> Dict[str, Any]:
        return json.loads(self.requests[index].content.decode())


def _client(responder) -> tuple:
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O8_000000001",
        api_secret="TEST_SECRET_TASK_O8_00000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _order_json(order_id: Any = VALID_ORDER_ID, **over) -> Dict[str, Any]:
    """A well-formed `/v2/orders` object; `over` drives each case."""
    payload: Dict[str, Any] = {
        "id": order_id,
        "client_order_id": "QE-O8-0001",
        "user_id": 5511,
        "product_id": delta_india_registry().get(BTCUSD).product_id,
        "product_symbol": BTCUSD,
        "side": "buy",
        "order_type": "limit_order",
        "size": "3",
        "unfilled_size": "1",
        "limit_price": "94500.00",
        "stop_price": None,
        "average_fill_price": "94501.50",
        "state": "open",
        "reduce_only": False,
        "created_at": 1756339200000000,
    }
    payload.update(over)
    return payload


def _without_id() -> Dict[str, Any]:
    payload = _order_json()
    del payload["id"]
    return payload


# ══ A. The parse contract ═════════════════════════════════════════════════════


def test_a01_an_absent_order_id_fails_closed():
    """The defect itself: no `id` key used to produce order 0."""
    with pytest.raises(DeltaResponseError):
        DeltaOrderResponse.from_dict(_without_id())


@pytest.mark.parametrize("bad", MALFORMED_IDS)
def test_a02_no_unusable_order_id_is_accepted(bad):
    with pytest.raises(DeltaResponseError):
        DeltaOrderResponse.from_dict(_order_json(order_id=bad))


@pytest.mark.parametrize("raw,expected", EXACT_IDS)
def test_a03_an_exactly_integral_order_id_is_accepted(raw, expected):
    """JSON serialisers vary; an exactly integral id is not ambiguous."""
    resp = DeltaOrderResponse.from_dict(_order_json(order_id=raw))
    assert resp.id == expected
    assert isinstance(resp.id, int)
    assert not isinstance(resp.id, bool)


@pytest.mark.parametrize("zero", (0, "0", 0.0, Decimal("0"), "-0", "00",
                                 " 0 ", "0.0", Decimal("-0")))
def test_a04_a_reported_zero_order_id_fails_closed(zero):
    """
    Approved contract: `0` is refused. No repository or exchange evidence
    establishes it as a legitimate Delta order id, and it is the exact value the
    old default fabricated -- so accepting it would leave the hazard reachable
    through a payload that merely *states* what the default used to invent.
    """
    with pytest.raises(DeltaResponseError) as excinfo:
        DeltaOrderResponse.from_dict(_order_json(order_id=zero))
    assert "0" in str(excinfo.value)


def test_a05_the_zero_refusal_is_distinguishable_from_the_malformed_refusal():
    """Two different facts: "the exchange said 0" vs "we could not read it"."""
    with pytest.raises(DeltaResponseError) as zero:
        DeltaOrderResponse.from_dict(_order_json(order_id=0))
    with pytest.raises(DeltaResponseError) as unreadable:
        DeltaOrderResponse.from_dict(_order_json(order_id="abc"))
    assert str(zero.value) != str(unreadable.value)
    assert "'abc'" in str(unreadable.value)


@pytest.mark.parametrize("bad", (True, False))
def test_a06_a_bool_order_id_fails_closed(bad):
    """
    `bool` is an `int` subclass, so `int(True)` used to yield order 1 -- the
    identity of a DIFFERENT, REAL order. Same explicit rejection the
    `product_id` block and `exchange_contract_count` already make.
    """
    with pytest.raises(DeltaResponseError):
        DeltaOrderResponse.from_dict(_order_json(order_id=bad))


@pytest.mark.parametrize("bad", (3.7, "3.7", Decimal("5.5"), 910001.5,
                                 "-2.5", Decimal("910001.0001")))
def test_a07_a_fractional_order_id_is_never_truncated(bad):
    """Truncating 3.7 to 3 would silently name a different real order."""
    with pytest.raises(DeltaResponseError):
        DeltaOrderResponse.from_dict(_order_json(order_id=bad))


@pytest.mark.parametrize("bad", MALFORMED_IDS)
def test_a08_no_unusable_order_id_becomes_an_order_at_all(bad):
    """The sweep form of the precedent test for `product_id`."""
    try:
        resp = DeltaOrderResponse.from_dict(_order_json(order_id=bad))
    except DeltaResponseError:
        return
    pytest.fail(f"{bad!r} parsed into order id {resp.id!r} instead of "
                f"failing closed")


def test_a09_the_refusal_is_the_typed_client_contract():
    """
    `DeltaResponseError` is not a new exception: `get_order_by_client_id`
    already raises it for an order-identity violation, and §O7's
    `required_decimal` already raises it from inside `models.py`.
    """
    from quantedge.execution.delta_client import DeltaClientError

    with pytest.raises(DeltaResponseError) as excinfo:
        DeltaOrderResponse.from_dict(_without_id())
    assert isinstance(excinfo.value, DeltaClientError)
    assert not isinstance(excinfo.value, UnknownInstrumentError)
    assert not isinstance(excinfo.value, UnknownOrderStateError)


def test_a10_the_message_names_the_value_and_the_refusal():
    with pytest.raises(DeltaResponseError) as excinfo:
        DeltaOrderResponse.from_dict(_order_json(order_id="abc"))
    message = str(excinfo.value)
    assert "'abc'" in message
    assert BTCUSD in message
    assert "order id" in message


def test_a11_the_empty_payload_contract_is_preserved():
    """
    Ordering is part of the contract: symbol -> product_id -> state -> order id.
    `from_dict({})` must still raise `UnknownInstrumentError`, so the existing
    tracked assertion in `test_order_response_identity_fail_closed.py` holds
    unchanged.
    """
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict({})


def test_a12_an_unusable_symbol_still_wins_over_an_unusable_order_id():
    payload = _order_json(order_id=None, product_symbol="FOOUSD")
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict(payload)


def test_a13_an_unusable_product_id_still_wins_over_an_unusable_order_id():
    payload = _order_json(order_id=0, product_id=None)
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict(payload)


def test_a14_an_absent_state_still_wins_over_an_unusable_order_id():
    """§O5/§O6's state refusal is upstream of §O8 and must stay that way."""
    payload = _order_json(order_id=0)
    del payload["state"]
    with pytest.raises(UnknownOrderStateError):
        DeltaOrderResponse.from_dict(payload)


def test_a15_the_state_refusal_still_reports_the_raw_id_without_parsing_it():
    """
    The `UnknownOrderStateError` message interpolates `data.get('id')` verbatim.
    That is a diagnostic, not an identity, so it must keep working for a payload
    whose id is itself unusable.
    """
    payload = _order_json(order_id="abc")
    del payload["state"]
    with pytest.raises(UnknownOrderStateError) as excinfo:
        DeltaOrderResponse.from_dict(payload)
    assert "'abc'" in str(excinfo.value)


def test_a16_no_order_id_alias_fallback_was_introduced():
    """
    Rule #16. The private stream reads `id` OR `order_id`; whether REST ever
    names it `order_id` is NOT established, so inventing that fallback here
    would be guessing an exchange contract. A payload carrying only `order_id`
    therefore fails closed.
    """
    payload = _without_id()
    payload["order_id"] = VALID_ORDER_ID
    with pytest.raises(DeltaResponseError):
        DeltaOrderResponse.from_dict(payload)


@pytest.mark.parametrize("negative", (-1, -5, "-5", -910001, Decimal("-3")))
def test_a17_a_negative_order_id_is_accepted_without_judgement(negative):
    """
    Deferred question J2: nothing establishes that Delta cannot emit a negative
    id. Refusing one merely because it looks suspicious would be inventing a
    contract, so sign is not judged -- only exactness and the fabricated zero.
    """
    resp = DeltaOrderResponse.from_dict(_order_json(order_id=negative))
    assert resp.id == int(str(negative).strip())


@pytest.mark.parametrize("large", (2 ** 31, 2 ** 63, 10 ** 19, 10 ** 30))
def test_a18_no_upper_bound_is_invented(large):
    assert DeltaOrderResponse.from_dict(
        _order_json(order_id=large)).id == large


def test_a19_a_full_valid_response_is_unchanged_by_o8():
    """Non-regression: §O8 touches identity only."""
    resp = DeltaOrderResponse.from_dict(_order_json())
    assert resp.id == VALID_ORDER_ID
    assert resp.client_order_id == "QE-O8-0001"
    assert resp.user_id == 5511
    assert resp.product_id == delta_india_registry().get(BTCUSD).product_id
    assert resp.product_symbol == BTCUSD
    assert resp.side == OrderSide.BUY
    assert resp.order_type == OrderType.LIMIT_ORDER
    assert resp.size == Decimal("3")
    assert resp.unfilled_size == Decimal("1")
    assert resp.filled_size == Decimal("2")
    assert resp.limit_price == Decimal("94500.00")
    assert resp.stop_price is None
    assert resp.average_fill_price == Decimal("94501.50")
    assert resp.state == OrderStatus.OPEN
    assert resp.status == OrderStatus.OPEN
    assert resp.reduce_only is False
    assert resp.created_at.year == 2025


# ══ B. Every REST path that reaches the parser refuses ════════════════════════

BTCUSD_PRODUCT_ID = delta_india_registry().get(BTCUSD).product_id


def _entry_request() -> DeltaOrderRequest:
    return DeltaOrderRequest(
        product_id=BTCUSD_PRODUCT_ID,
        product_symbol=BTCUSD,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("3"),
        limit_price=Decimal("94500.00"),
        client_order_id="QE-O8-ENTRY",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_b20_get_open_orders_refuses_an_unidentifiable_order(bad):
    client, rec = _client(lambda r: _ok([_order_json(order_id=bad)]))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    assert len(rec.requests) == 1, "the client must not retry a refusal"
    await client.close()


@pytest.mark.asyncio
async def test_b21_one_unidentifiable_order_refuses_the_whole_page():
    """
    No partial adoption. `reconcile_account` reads
    `exchange_is_flat = not exchange_positions and not exchange_orders`, so a
    silently shortened list is a false statement about exchange state.
    """
    page = [_order_json(order_id=910001, client_order_id="QE-A"),
            _without_id(),
            _order_json(order_id=910003, client_order_id="QE-C")]
    client, _ = _client(lambda r: _ok(page))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    await client.close()


@pytest.mark.asyncio
async def test_b22_a_valid_page_still_parses():
    client, _ = _client(lambda r: _ok([_order_json(910001),
                                       _order_json(910002)]))
    orders = await client.get_open_orders()
    assert [o.id for o in orders] == [910001, 910002]
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_b23_get_order_refuses_an_unidentifiable_response(bad):
    client, rec = _client(lambda r: _ok(_order_json(order_id=bad)))
    with pytest.raises(DeltaResponseError):
        await client.get_order(910001)
    assert len(rec.requests) == 1
    assert rec.last.url.path == "/v2/orders/910001"
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_b24_the_client_id_lookup_refuses_an_unidentifiable_response(bad):
    """
    §O4 already verifies the returned `client_order_id` byte-for-byte. §O8 adds
    the exchange id: `_resolve_entry_order` copies `found.id` into
    `record.entry_order_id`, which the cancel and bracket paths then act on.
    """
    client, rec = _client(
        lambda r: _ok(_order_json(order_id=bad, client_order_id="QE-O8-ABC")))
    with pytest.raises(DeltaResponseError):
        await client.get_order_by_client_id("QE-O8-ABC")
    assert rec.last.url.path == "/v2/orders/client_order_id/QE-O8-ABC"
    assert len(rec.requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_b25_the_client_id_identity_check_still_wins():
    """A mismatched client id is refused before the exchange id is examined."""
    client, _ = _client(
        lambda r: _ok(_order_json(order_id=0, client_order_id="SOMEONE-ELSE")))
    with pytest.raises(DeltaResponseError) as excinfo:
        await client.get_order_by_client_id("QE-O8-ABC")
    assert "not ours" in str(excinfo.value)
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_b26_place_order_refuses_an_unidentifiable_placement(bad):
    """
    The sharpest one: a placement whose identity cannot be read must not be
    reported as a placed order. `execution_engine` used to put this value in an
    `OrderExecutionResult(success=True, order_id=0)`.
    """
    client, rec = _client(lambda r: _ok(_order_json(order_id=bad)))
    with pytest.raises(DeltaResponseError):
        await client.place_order(_entry_request())
    assert len(rec.requests) == 1
    assert rec.last.method == "POST"
    await client.close()


@pytest.mark.asyncio
async def test_b27_create_order_and_place_order_are_the_same_boundary():
    """`place_order` is an alias, so one refusal covers both names."""
    client, _ = _client(lambda r: _ok(_without_id()))
    with pytest.raises(DeltaResponseError):
        await client.create_order(_entry_request())
    await client.close()


@pytest.mark.asyncio
async def test_b28_a_valid_placement_still_returns_its_identity():
    client, _ = _client(lambda r: _ok(_order_json(910777)))
    resp = await client.place_order(_entry_request())
    assert resp.id == 910777
    assert resp.client_order_id == "QE-O8-0001"
    await client.close()


@pytest.mark.asyncio
async def test_b29_the_existing_envelope_guards_are_untouched():
    """§O4/§O6 guards fire before the parser is ever reached."""
    for result in (None, [], "", 0):
        client, _ = _client(lambda r, res=result: _ok(res))
        with pytest.raises(DeltaResponseError):
            await client.get_order(910001)
        await client.close()

    client, _ = _client(lambda r: _ok({"id": 1}))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    await client.close()


@pytest.mark.asyncio
async def test_b30_a_fabricated_zero_can_no_longer_reach_cancel_order():
    """
    `cancel_order` guards with `int(str(order_id).strip())`, which `0` passes --
    so before §O8 a parsed `id` of 0 produced a real
    `DELETE /v2/orders {"id": 0, "product_id": ...}`. The guard is deliberately
    left as it is (§O8 changes `models.py` only); what changes is that no parse
    can hand it a zero any more.
    """
    client, rec = _client(lambda r: _ok(_order_json(order_id=0)))
    with pytest.raises(DeltaResponseError):
        await client.get_order(910001)
    assert rec.paths() == ["/v2/orders/910001"]
    assert all(r.method != "DELETE" for r in rec.requests)
    await client.close()


# ══ C. Consumer proofs -- what a fabricated identity used to do ═══════════════


def _store() -> LocalStateStore:
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.current_balance = Decimal("10000.00")
    store.account.margin_used = Decimal("250.00")
    store.account.algo_enabled = True
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return store


def _manager(client, store: LocalStateStore) -> TradeLifecycleManager:
    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )


def _record(**over) -> TradeLifecycleRecord:
    record = TradeLifecycleRecord(
        setup_id=SETUP,
        account_id=ACCOUNT,
        user_id=USER,
        symbol=BTCUSD,
        direction=TradeDirection.LONG,
        requested_quantity=Decimal("3"),
        entry_price=ENTRY_PRICE,
        stop_loss_price=Decimal("94000.0"),
        take_profit_price=Decimal("98000.0"),
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("100"),
        reward_amount=Decimal("300"),
    )
    for key, value in over.items():
        setattr(record, key, value)
    return record


def _local_position() -> PositionRecord:
    return PositionRecord(
        symbol=BTCUSD,
        side=PositionSide.LONG,
        quantity=Decimal("3"),
        entry_price=ENTRY_PRICE,
        current_price=Decimal("95500.0"),
        unrealized_pnl=Decimal("1.50"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("10"),
        margin_used=Decimal("28.50"),
        status=PositionStatus.OPEN,
    )


def _wallet_json() -> Dict[str, Any]:
    """A §O7-clean wallet entry, so only the order id is ever the defect."""
    return {
        "asset_symbol": "USDT", "balance": "10000.12345678",
        "available_balance": "9500.87654321", "position_margin": "400.25",
        "order_margin": "99.00", "blocked_margin": "0.01",
        "user_id": 42, "id": 7001,
    }


def _position_json() -> Dict[str, Any]:
    """A §O6-clean margined position, for the same reason."""
    return {
        "product_id": BTCUSD_PRODUCT_ID, "product_symbol": BTCUSD,
        "size": "3", "entry_price": "95000.0", "mark_price": "95500.0",
        "liquidation_price": "88000.0", "unrealised_pnl": "1.50",
        "realised_pnl": "0.00", "leverage": "10", "margin": "28.65",
    }


def _sync_responder(order_payload):
    """Serves a healthy account whose ONLY defect is the order identity."""

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/wallet/balances":
            return _ok([_wallet_json()])
        if path == "/v2/positions/margined":
            return _ok([_position_json()])
        if path == "/v2/orders":
            return _ok([order_payload])
        raise AssertionError(f"unexpected request to {path}")

    return responder


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_c31_the_synchronizer_never_keys_state_by_a_fabricated_id(bad):
    """
    Consumer 1. `_reconcile_orders` did `self.state_store.orders[str(eo.id)]`,
    so every id-less order collapsed onto the single key `"0"` -- one record
    overwriting the next -- and `"0"` joined the membership set that decides
    whether a local order is still open.
    """
    store = _store()
    store.positions[BTCUSD] = _local_position()
    client, _ = _client(_sync_responder(_order_json(order_id=bad)))
    service = LiveAccountSyncService(client=client, state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert isinstance(result, SyncResult)
    assert result.success is False
    assert result.error
    assert "0" not in store.orders
    assert store.orders == {}
    assert store.connection.connection_status == "ERROR"
    assert store.connection.last_error
    # The order refusal aborts the cycle before any order write. Balances and
    # positions are reconciled earlier in `_do_sync`, so the point here is not
    # that nothing moved -- it is that no ORDER identity was invented and the
    # live position was not closed or orphaned by the failure.
    assert BTCUSD in store.positions
    assert store.positions[BTCUSD].status is PositionStatus.OPEN
    assert store.position_history == []
    assert result.orders_synced == 0
    assert [e["action"] for e in store.audit_events][-1] == "SYNC_FAILED"
    await client.close()


@pytest.mark.asyncio
async def test_c32_a_properly_identified_order_still_imports():
    """The control. §O8 refuses fabrications, not synchronization."""
    store = _store()
    store.positions[BTCUSD] = _local_position()
    client, _ = _client(_sync_responder(_order_json()))
    service = LiveAccountSyncService(client=client, state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert result.success is True, result.error
    assert result.orders_synced == 1
    assert str(VALID_ORDER_ID) in store.orders
    imported = store.orders[str(VALID_ORDER_ID)]
    assert isinstance(imported, OrderRecord)
    assert imported.delta_order_id == str(VALID_ORDER_ID)
    assert imported.symbol == BTCUSD
    assert store.connection.connection_status == "CONNECTED"
    await client.close()


@pytest.mark.asyncio
async def test_c33_two_unidentifiable_orders_cannot_collapse_onto_one_key():
    """
    The multiplicity hazard: keyed by `str(eo.id)`, N id-less orders became ONE
    record at key `"0"`, each overwriting the last, so the store under-reported
    live exposure. The refusal removes the whole class.
    """
    store = _store()
    page = [_without_id(), _order_json(order_id=None, client_order_id="QE-O8-2")]

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/wallet/balances":
            return _ok([_wallet_json()])
        if request.url.path == "/v2/positions/margined":
            return _ok([_position_json()])
        return _ok(page)

    client, _ = _client(responder)
    service = LiveAccountSyncService(client=client, state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert result.success is False
    assert store.orders == {}
    await client.close()


def _actions(store: LocalStateStore) -> List[str]:
    return [e["action"] for e in store.audit_events]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", True))
async def test_c34_an_unidentifiable_stop_loss_never_counts_as_protection(bad):
    """
    THE decisive consumer. `trade_lifecycle` already contains the requirement:

        if not record.sl_order_id or not record.tp_order_id:
            raise RuntimeError("Exchange failed to confirm SL/TP bracket order IDs")

    but `str(0)` is `"0"`, which is TRUTHY, so a fabricated identity walked
    straight through the repository's own guard and the trade was recorded as
    `PROTECTED_POSITION` with no protection on the exchange. The parser now
    refuses before the assignment, so the existing `except Exception` handler
    fires and the state is `PROTECTION_FAILED`.
    """
    store = _store()
    client, rec = _client(lambda request: _ok(_order_json(order_id=bad)))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, Decimal("3"))

    assert record.state is TradeLifecycleState.PROTECTION_FAILED
    assert record.state is not TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id is None
    assert record.sl_order_id != "0"
    assert record.tp_order_id is None
    assert record.tp_order_id != "0"
    assert record.protected_quantity != Decimal("3")
    assert "PROTECTION_PLACEMENT_FAILED" in _actions(store)
    # The refusal happened on the FIRST placement, so no TP was ever sent.
    assert len(rec.requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_c35_an_unidentifiable_take_profit_also_fails_protection():
    """The second leg is the same boundary: a placed SL is not a bracket."""
    store = _store()
    calls = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok(_order_json(order_id=VALID_ORDER_ID))
        return _ok(_without_id())

    client, rec = _client(responder)
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, Decimal("3"))

    assert record.state is TradeLifecycleState.PROTECTION_FAILED
    assert record.sl_order_id == str(VALID_ORDER_ID)
    assert record.tp_order_id is None
    assert record.tp_order_id != "0"
    assert "PROTECTION_PLACEMENT_FAILED" in _actions(store)
    assert len(rec.requests) == 2
    await client.close()


@pytest.mark.asyncio
async def test_c36_a_valid_bracket_still_protects():
    """Control: §O8 refuses fabrications, not protection."""
    store = _store()
    ids = iter((910001, 910002))
    client, _ = _client(lambda request: _ok(_order_json(order_id=next(ids))))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, Decimal("3"))

    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == "910001"
    assert record.tp_order_id == "910002"
    assert record.protected_quantity == Decimal("3")
    assert "PROTECTION_PLACEMENT_FAILED" not in _actions(store)
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7))
async def test_c37_the_entry_identity_is_never_backfilled_with_a_fabrication(bad):
    """
    §M1B's `_resolve_entry_order` adopts the exchange's id for an ambiguous
    submission: `record.entry_order_id = str(found.id)`. With the old default
    that wrote `"0"` as the trade's PERSISTED entry identity -- the value later
    fed to `int(record.entry_order_id)` for cancellation and to the reconciler's
    claimed-id set.
    """
    store = _store()
    client, _ = _client(lambda request: _ok(
        _order_json(order_id=bad, client_order_id="QE-O8-AMBIGUOUS")))
    manager = _manager(client, store)
    record = _record(entry_client_order_id="QE-O8-AMBIGUOUS")

    with pytest.raises(DeltaResponseError):
        await manager._resolve_entry_order(record)

    assert record.entry_order_id is None
    assert record.entry_order_id != "0"
    await client.close()


@pytest.mark.asyncio
async def test_c38_a_valid_ambiguous_submission_still_resolves():
    """Control for c37: a stated identity is still adopted."""
    store = _store()
    client, _ = _client(lambda request: _ok(
        _order_json(client_order_id="QE-O8-AMBIGUOUS")))
    manager = _manager(client, store)
    record = _record(entry_client_order_id="QE-O8-AMBIGUOUS")

    found = await manager._resolve_entry_order(record)

    assert found is not None
    assert found.id == VALID_ORDER_ID
    assert record.entry_order_id == str(VALID_ORDER_ID)
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc"))
async def test_c39_the_orphan_scan_never_reports_a_fabricated_order(bad):
    """
    §M11 case A logs, audits and alerts on every exchange order that no local
    trade claims. An id-less order used to be reported as "exchange order 0",
    and -- worse -- any local record holding the same fabricated `"0"` would
    have CLAIMED it, hiding a genuinely untracked order. Reading the exchange
    now fails, which the scan already treats as `EXCHANGE_UNREACHABLE`.
    """
    store = _store()

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/positions/margined":
            return _ok([])
        return _ok([_order_json(order_id=bad)])

    client, _ = _client(responder)
    manager = _manager(client, store)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT)

    assert summary["exchange_unreachable"] is True
    assert summary["unresolved"] == ["EXCHANGE_UNREACHABLE"]
    assert summary["orphan_orders"] == []
    assert "0" not in summary["orphan_orders"]
    assert "RECONCILIATION_ORPHAN_ORDER" not in _actions(store)
    assert any(a.startswith("RECONCILIATION_ALERT_EXCHANGE_UNREACHABLE")
               for a in _actions(store))
    await client.close()


@pytest.mark.asyncio
async def test_c40_a_genuinely_untracked_order_is_still_reported():
    """
    Control for c39, and the point of the whole task: a REAL untracked order is
    still surfaced as data (safety rule #9 -- reported, never auto-cancelled).
    """
    store = _store()

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/positions/margined":
            return _ok([])
        return _ok([_order_json()])

    client, rec = _client(responder)
    manager = _manager(client, store)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT)

    assert summary["exchange_unreachable"] is False
    assert summary["orphan_orders"] == [str(VALID_ORDER_ID)]
    assert "RECONCILIATION_ORPHAN_ORDER" in _actions(store)
    # Reported, not cancelled: no DELETE was issued.
    assert all(r.method != "DELETE" for r in rec.requests)
    await client.close()


# ══ D. Lock direction on a refused placement ══════════════════════════════════
#
# The refusal is raised INSIDE `place_order`'s response parse, i.e. after the
# POST has already reached the exchange. The order may therefore exist. Safety
# rules #11/#14 make lock RETENTION the fail-safe direction, and
# `trade_lifecycle` already documents exactly that for its catch-all handler
# ("This handler also covers failures after place_order returned (response
# parsing, ...)"). These tests pin that direction so a future refactor cannot
# quietly turn a possibly-live unprotected order into a free slot for a second
# trade.


def _entry_store() -> LocalStateStore:
    """A store that passes the pre-trade gate, so submission is actually reached."""
    store = _store()
    store.account.is_active = True
    store.account.kill_switch_active = False
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.account.total_equity = Decimal("25000.00")
    store.account.available_balance = Decimal("20000.00")
    store.account.margin_used = Decimal("5000.00")
    return store


LONG_SETUP = StrategyDecision(
    timestamp=datetime.now(timezone.utc),
    symbol=BTCUSD,
    timeframe="1h",
    direction=StrategyDirection.LONG,
    setup_state=SetupState.TRADE_SETUP_READY,
    setup_id=SETUP,
    entry=ENTRY_PRICE,
    stop_loss=Decimal("94000.00"),
    take_profit=Decimal("98000.00"),
    risk_distance=Decimal("1000.00"),
    reward_distance=Decimal("3000.00"),
    risk_reward=Decimal("3.0"),
    confidence=85.0,
)


def _entry_manager(client, store: LocalStateStore, lock: SingleTradeLockManager):
    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=lock,
        daily_loss_limit=Decimal("500.00"),
        max_stale_seconds=120,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (None, 0, "abc", 3.7, True))
async def test_d41_a_refused_placement_retains_the_single_trade_lock(bad):
    """The POST was sent; exchange state is unknown; the slot stays taken."""
    store = _entry_store()
    client, rec = _client(lambda request: _ok(_order_json(order_id=bad)))
    lock = SingleTradeLockManager()
    manager = _entry_manager(client, store, lock)

    record = await manager.execute_trade_setup(LONG_SETUP, ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.error_message
    held, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    assert held is True, "a possibly-live order must not free the account slot"
    assert setup_id == SETUP
    assert symbol == BTCUSD
    # No identity was invented anywhere along the failed path.
    assert record.entry_order_id is None
    assert record.entry_order_id != "0"
    assert store.orders == {}
    assert "0" not in store.orders
    assert record.sl_order_id is None
    assert record.tp_order_id is None
    # Exactly one HTTP request: the entry POST. No bracket, no cancel.
    assert [r.method for r in rec.requests] == ["POST"]
    await client.close()


@pytest.mark.asyncio
async def test_d42_an_explicit_exchange_rejection_still_releases_the_lock():
    """
    The contrast that proves d41 is about ambiguity, not about failing closed
    everywhere: an explicit rejection means the order provably does not exist,
    and `trade_lifecycle` releases the lock for that case. §O8 must not have
    changed this direction.
    """
    store = _entry_store()
    client, _ = _client(lambda request: httpx.Response(
        400, json={"success": False,
                   "error": {"code": "insufficient_margin", "context": {}}}))
    lock = SingleTradeLockManager()
    manager = _entry_manager(client, store, lock)

    record = await manager.execute_trade_setup(LONG_SETUP, ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert store.orders == {}
    assert record.entry_order_id is None
    await client.close()


@pytest.mark.asyncio
async def test_d43_a_valid_placement_still_records_the_real_identity():
    """Control for section D: nothing about the healthy path moved."""
    store = _entry_store()
    # A resting, wholly unfilled entry, so the control ends at ENTRY_SUBMITTED
    # instead of running on into the bracket path.
    client, _ = _client(lambda request: _ok(
        _order_json(unfilled_size="3", average_fill_price=None)))
    lock = SingleTradeLockManager()
    manager = _entry_manager(client, store, lock)

    record = await manager.execute_trade_setup(LONG_SETUP, ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_SUBMITTED
    assert record.entry_order_id == str(VALID_ORDER_ID)
    assert str(VALID_ORDER_ID) in store.orders
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    await client.close()


# ══ E. Non-regression: the fence around §O8 ═══════════════════════════════════


def test_e44_the_websocket_order_identity_contract_is_unchanged():
    """
    The stream was never the defect: `_normalize_order` already refused a frame
    with no order id, which is what made REST the outlier. §O8 aligns REST with
    the stream and must not have altered the stream.
    """
    validator = EventValidator()
    with pytest.raises(ValueError, match="missing order_id"):
        validator._normalize_order({"product_symbol": BTCUSD, "state": "open"})

    event = validator._normalize_order({
        "id": VALID_ORDER_ID, "product_symbol": BTCUSD, "state": "open",
        "side": "buy", "size": "3", "unfilled_size": "3",
    })
    assert event.order_id == str(VALID_ORDER_ID)


def test_e45_the_websocket_fill_identity_is_deliberately_left_alone():
    """
    Documents the fence, not an endorsement. `_normalize_fill` still reads
    `str(data.get("order_id", ""))`, so a fill that names no order becomes `""`.
    That is a DIFFERENT model, a different consumer set and a different task
    (recorded as I2); §O8 was scoped to the REST order parser by instruction, so
    this test pins the current state to keep the item visible rather than
    silently widening the change here.
    """
    source = inspect.getsource(EventValidator._normalize_fill)
    assert 'data.get("order_id", "")' in source
    fill = EventValidator()._normalize_fill({
        "id": "t-1", "product_symbol": BTCUSD, "side": "buy",
        "size": "3", "price": "95000.0", "commission": "0.10",
    })
    assert fill.order_id == ""


def test_e46_the_o6_position_contract_is_untouched():
    """§O6: a position that states no size is refused; numerics stay optional."""
    with pytest.raises(DeltaResponseError):
        DeltaPosition.from_dict(
            {k: v for k, v in _position_json().items() if k != "size"})
    pos = DeltaPosition.from_dict(
        {k: v for k, v in _position_json().items() if k != "realised_pnl"})
    assert pos.realized_pnl is None
    assert pos.size == Decimal("3")


def test_e47_the_o7_wallet_contract_is_untouched():
    """§O7: wallet numerics are required, never defaulted to a safe-looking 0."""
    for field in ("balance", "available_balance", "position_margin",
                  "order_margin", "asset_symbol"):
        payload = {k: v for k, v in _wallet_json().items() if k != field}
        with pytest.raises(DeltaResponseError):
            DeltaWalletBalance.from_dict(payload)
    wallet = DeltaWalletBalance.from_dict(_wallet_json())
    assert wallet.asset_symbol == "USDT"


def test_e48_the_instrument_and_state_contracts_still_win_over_the_id():
    """
    The ordering the approval made explicit: symbol -> product_id -> state ->
    order id. §O8's refusal is LAST, so an unusable payload still reports the
    more fundamental failure first.
    """
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict({})
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict(_order_json(order_id=0, product_symbol=None))
    with pytest.raises(UnknownOrderStateError):
        payload = _order_json(order_id=0)
        del payload["state"]
        DeltaOrderResponse.from_dict(payload)


def test_e49_the_governance_state_is_unchanged():
    """§O8 is a parser contract. It authorizes nothing."""
    from quantedge.ai.research.displacement_gated_retest_engine import (
        AI_PROMOTION_STATUS,
    )
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AI_PROMOTION_STATUS == "REJECTED"

    account = AccountRecord(account_id=ACCOUNT)
    assert account.algo_enabled is False
    assert account.kill_switch_active is True


def test_e50_the_product_id_in_every_fixture_comes_from_the_registry():
    """No verified exchange constant is hardcoded by this suite either."""
    spec = delta_india_registry().get(BTCUSD)
    assert _order_json()["product_id"] == spec.product_id
    assert _position_json()["product_id"] == spec.product_id
    assert BTCUSD_PRODUCT_ID == spec.product_id


# ══ S. Static invariants and the safety scan ══════════════════════════════════


def _code(obj) -> str:
    """Normalized source: `ast.unparse` strips comments and unifies quoting, so
    these assertions describe behaviour rather than formatting."""
    return ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(obj))))


def _parse_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(DeltaOrderResponse.from_dict)))


def test_s51_the_order_id_is_never_read_with_a_default():
    """
    The regression fence. `data.get('id', 0)` -- in any spelling, with any
    default -- is what this task removed; a default must never answer a safety
    question (rule #13).
    """
    defaults = [
        node for node in ast.walk(_parse_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        and node.args and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "id"
        and len(node.args) > 1
    ]
    assert defaults == [], "`id` is read with a fallback default again"

    code = _code(DeltaOrderResponse.from_dict)
    assert "data.get('id')" in code
    assert "data.get('id', 0)" not in code
    assert "int(data.get('id'" not in code


def test_s52_no_raw_lookup_is_coerced_straight_into_an_identity():
    """
    `int(data.get(...))` is the defective shape itself: it truncates, it accepts
    `bool`, and with a default it fabricates. The parse must read, validate, then
    convert -- never convert first.
    """
    offenders = []
    for node in ast.walk(_parse_tree()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "int" and node.args):
            arg = node.args[0]
            if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "get"):
                offenders.append(ast.unparse(node))
    assert offenders == [], offenders


def test_s53_the_refusal_requires_exactness_and_rejects_bool():
    """Structural: the same three guards the `product_id` block already uses."""
    code = _code(DeltaOrderResponse.from_dict)
    assert "isinstance(raw_id, bool)" in code
    assert "is_finite()" in code
    assert "if id_decimal != order_id" in code
    assert "if order_id == 0" in code
    # The exception is the client's existing order-identity contract, imported
    # function-locally because `delta_client` imports this module.
    assert "from quantedge.execution.delta_client import DeltaResponseError" in code
    assert "DeltaResponseError" in code


def test_s54_models_does_not_import_the_client_at_module_scope():
    """The circular-import constraint that forces the function-local import."""
    tree = ast.parse(Path(execution_models.__file__).read_text(encoding="utf-8"))
    module_level = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in module_level:
        text = ast.unparse(node)
        assert "delta_client" not in text, text


def test_s55_the_identity_refusal_is_the_last_check_in_the_parse():
    """
    The ordering the approval made a requirement, pinned structurally rather
    than only through `from_dict({})`: symbol, then `product_id`, then state,
    then the order id.
    """
    code = _code(DeltaOrderResponse.from_dict)
    symbol_at = code.index("data.get('product_symbol'")
    product_at = code.index("data.get('product_id')")
    state_at = code.index("data.get('state'")
    id_at = code.index("raw_id = data.get('id')")
    assert symbol_at < product_at < state_at < id_at


def test_s56_no_verified_exchange_constant_was_introduced():
    """
    Extends the tracked `product_id` invariant to §O8's edit: the only integer
    literal the refusal needs is `0`, and no verified product id may appear as a
    literal anywhere in the parse (provenance stays with the registry).
    """
    verified = {
        spec.product_id for spec in
        (delta_india_registry().get(sym) for sym in ("BTCUSD", "ETHUSD"))
    }
    literals = {
        node.value for node in ast.walk(_parse_tree())
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    assert literals & verified == set(), literals & verified
    # `0` is the sole integer literal the whole parse needs -- and it is a
    # comparison the refusal makes, not a value it assigns.
    assert literals == {0}, literals


_FORBIDDEN_FRAGMENTS = (
    ("os.env", "iron"),
    ("load_dot", "env"),
    (".en", "v"),
    ("httpx.AsyncHTTPTrans", "port"),
    ("force_release_lo", "ck("),
    ("api_key=os", "."),
    ("LIVE_EXECUTION_AUTHORIZED =", " True"),
    ("AI_PROMOTION_STATUS =", " "),
)


def _this_source() -> str:
    src = inspect.getsource(inspect.getmodule(test_s57_this_suite_touches_nothing_live))
    # Everything from the needle list onward is excluded, so the scan cannot
    # match the list itself.
    return src[:src.index("_FORBIDDEN_FRAGMENTS = (")]


def test_s57_this_suite_touches_nothing_live():
    """
    No credential read, no real transport, no governance mutation. Every
    request in this file is served by `httpx.MockTransport` and every payload is
    a literal dict, so the orders "placed" here exist only in memory.
    """
    src = _this_source()
    for head, tail in _FORBIDDEN_FRAGMENTS:
        assert (head + tail) not in src, head + tail
    assert "httpx.MockTransport" in src


def test_s58_every_client_in_this_suite_is_mock_transported():
    """Structural: `_client` is the only `DeltaIndiaClient` constructor here."""
    code = _code(_client)
    assert "httpx.MockTransport" in code
    assert "http_client=http" in code

    tree = ast.parse(_this_source())
    constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "DeltaIndiaClient"
    ]
    assert len(constructions) == 1

