"""
Task O §O13 -- one position, exactly ONE protective pair.

§G2 made the entry payload carry Delta's documented attached-bracket fields
(`bracket_stop_loss_price`, `bracket_take_profit_price`,
`bracket_stop_trigger_method`), so the exchange builds the reduce-only SL/TP
legs itself the moment the entry fills. `_ensure_bracket_protection` then placed
its own reduce-only pair as well, leaving ONE position covered by two stop-loss
legs and two take-profit legs. Both pairs are reduce-only, so neither can flip
the position or over-close it -- but the surplus leg keeps resting after the
position is gone, and `close_position` only cancels the two ids it knows about.

The fix ADOPTS instead of suppressing: when the exchange confirms it holds the
bracket, its legs' real order ids become `record.sl_order_id` /
`record.tp_order_id`, so every existing consumer keeps working on live,
re-verifiable exchange ids -- the resize path, `close_position`'s cancel loop,
the kill switch, and decisively
`reconcile_active_trades_with_exchange`'s `sl_live`/`tp_live` membership test
against `get_open_orders`. Nothing is ever marked protected on the strength of a
field echoed once at entry time, which a boolean "exchange-protected" flag would
have done; group D asserts that protection which later vanishes is still caught
and rebuilt.

Two exchange-sourced confirmations are required and neither is inferred:

  1. the ENTRY order object echoes `bracket_stop_loss_price` and
     `bracket_take_profit_price` equal to the levels this record authorises;
  2. exactly one resting reduce-only leg per side matches product, side, size,
     `stop_order_type`, `stop_trigger_method` and `stop_price`.

Every failure to confirm falls through to the pre-§G2 placement, because
protection existing twice is recoverable and protection not existing is not.
Only when (1) held and (2) failed is `PROTECTION_DUPLICATED_ON_EXCHANGE`
recorded, with a blocking reconciliation alert.

The trigger series is CHECKED, not assumed. The engine arms its stops on
`last_traded_price` because Manual SMC's levels are derived from traded prices;
a leg armed on `mark_price` is a different exit contract, so it is refused
exactly as `_assert_stop_contract` refuses it for a standalone stop. If Delta
ignores `bracket_stop_trigger_method`, this fails loudly rather than silently
changing exit semantics.

Zero network access: every request is served by `httpx.MockTransport`.
Zero mutating requests are needed for groups A-C; where a POST is expected the
test asserts on its absence or presence, never on a real exchange.
"""

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
import pytest

from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
)
from quantedge.execution.models import (
    DeltaOrderResponse,
    DeltaPosition,
    OrderSide,
    OrderStatus,
    PositionSide,
    StopOrderType,
    StopTriggerMethod,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    BRACKET_ADOPTION_ATTEMPTS,
    BRACKET_ADOPTION_DELAY_SECONDS,
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
import quantedge.execution.trade_lifecycle as trade_lifecycle
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments import delta_india_registry
from quantedge.strategy.models import TradeDirection

ACCOUNT = "acc_task_o13"
USER = "user_task_o13"
SETUP = "XRPUSD_1h_MANUAL_SMC_O13_LONG"
SYMBOL = "XRPUSD"
PRODUCT_ID = delta_india_registry().get(SYMBOL).product_id

ENTRY_PRICE = Decimal("2.9000")
SL_PRICE = Decimal("2.8000")
TP_PRICE = Decimal("3.1000")
SIZE = Decimal("1")

ENTRY_ORDER_ID = 1300001
EXCHANGE_SL_LEG_ID = 1300002
EXCHANGE_TP_LEG_ID = 1300003
ENGINE_SL_ID = 1300900
ENGINE_TP_ID = 1300901


# ── Transport plumbing (same shape as §O8) ────────────────────────────────────


class Recorder:
    """Captures every request, so "no second pair was placed" is assertable."""

    def __init__(self, responder):
        self.requests: List[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    def calls(self) -> List[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def posts(self) -> List[Dict[str, Any]]:
        return [
            json.loads(r.content.decode())
            for r in self.requests
            if r.method == "POST" and r.url.path == "/v2/orders"
        ]

    def deletes(self) -> List[Dict[str, Any]]:
        return [
            json.loads(r.content.decode())
            for r in self.requests
            if r.method == "DELETE" and r.url.path == "/v2/orders"
        ]


def _client(responder):
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O13_00000000",
        api_secret="TEST_SECRET_TASK_O13_0000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _store() -> LocalStateStore:
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.current_balance = Decimal("10000.00")
    store.account.margin_used = Decimal("50.00")
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
        symbol=SYMBOL,
        direction=TradeDirection.LONG,
        requested_quantity=SIZE,
        entry_price=ENTRY_PRICE,
        stop_loss_price=SL_PRICE,
        take_profit_price=TP_PRICE,
        risk_reward_ratio=Decimal("2"),
        risk_amount=Decimal("10"),
        reward_amount=Decimal("20"),
    )
    record.entry_order_id = str(ENTRY_ORDER_ID)
    for key, value in over.items():
        setattr(record, key, value)
    return record


def _actions(store: LocalStateStore) -> List[str]:
    return [e["action"] for e in store.audit_events]


# ── Exchange objects, shaped from the §G2 read-only evidence run ──────────────


def _entry_json(bracket: bool = True, **over) -> Dict[str, Any]:
    """The ENTRY order object.

    `bracket=True` echoes the two price fields Delta was observed to echo on a
    parent that carries an attached bracket. `bracket_stop_trigger_method` is
    deliberately absent: the evidence run found it is NOT echoed, so the engine
    must not read it, and the legs are asked for their trigger series instead.
    """
    payload: Dict[str, Any] = {
        "id": ENTRY_ORDER_ID,
        "client_order_id": "QE_XRPUSD_ENTRY_O13",
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
        "side": "buy",
        "order_type": "limit_order",
        "size": "1",
        "unfilled_size": "0",
        "limit_price": str(ENTRY_PRICE),
        "stop_price": None,
        "average_fill_price": str(ENTRY_PRICE),
        "state": "closed",
        "reduce_only": False,
        "bracket_order": False,
        "created_at": 1756339200000000,
    }
    if bracket:
        payload["bracket_stop_loss_price"] = str(SL_PRICE)
        payload["bracket_take_profit_price"] = str(TP_PRICE)
        payload["bracket_stop_loss_limit_price"] = None
        payload["bracket_take_profit_limit_price"] = None
        payload["bracket_trail_amount"] = None
    payload.update(over)
    return payload


def _leg_json(
    order_id: int,
    leg_type: str,
    trigger_price: Decimal,
    **over,
) -> Dict[str, Any]:
    """A bracket leg, in the exact shape every leg in the account's history had:
    reduce-only, `bracket_order: true`, a `stop_order_type`, a `stop_price` and
    a `stop_trigger_method`. The trigger series here is `last_traded_price`,
    which is what the engine asked for; the `mark_price` case is a test of its
    own because it is a different exit contract, not a formatting difference.
    """
    payload: Dict[str, Any] = {
        "id": order_id,
        "client_order_id": None,
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
        "side": "sell",
        "order_type": "market_order",
        "size": "1",
        "unfilled_size": "1",
        "limit_price": None,
        "stop_price": str(trigger_price),
        "average_fill_price": None,
        "state": "pending",
        "reduce_only": True,
        "bracket_order": True,
        "stop_order_type": leg_type,
        "stop_trigger_method": "last_traded_price",
        "created_at": 1756339200000000,
    }
    payload.update(over)
    return payload


def _sl_leg(**over) -> Dict[str, Any]:
    return _leg_json(EXCHANGE_SL_LEG_ID, "stop_loss_order", SL_PRICE, **over)


def _tp_leg(**over) -> Dict[str, Any]:
    return _leg_json(EXCHANGE_TP_LEG_ID, "take_profit_order", TP_PRICE, **over)


def _position_json(size: str = "1") -> Dict[str, Any]:
    return {
        "product_id": PRODUCT_ID, "product_symbol": SYMBOL,
        "size": size, "entry_price": str(ENTRY_PRICE), "mark_price": "2.9100",
        "liquidation_price": "2.5000", "unrealised_pnl": "0.10",
        "realised_pnl": "0.00", "leverage": "10", "margin": "5.80",
    }


def _placed_json(order_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Echo a POSTed order back the way Delta would, so a placement that DOES
    happen still produces a usable record -- the fallback path must remain fully
    functional, not merely reachable."""
    return {
        "id": order_id,
        "client_order_id": body.get("client_order_id"),
        "product_id": body.get("product_id", PRODUCT_ID),
        "product_symbol": SYMBOL,
        "side": body.get("side", "sell"),
        "order_type": body.get("order_type", "market_order"),
        "size": str(body.get("size", "1")),
        "unfilled_size": str(body.get("size", "1")),
        "limit_price": body.get("limit_price"),
        "stop_price": body.get("stop_price"),
        "average_fill_price": None,
        "state": "pending",
        "reduce_only": bool(body.get("reduce_only", True)),
        "bracket_order": False,
        "stop_order_type": body.get("stop_order_type"),
        "stop_trigger_method": body.get("stop_trigger_method"),
        "created_at": 1756339200000000,
    }


def _responder(
    entry: Optional[Dict[str, Any]] = None,
    resting: Any = (),
    positions: Any = (),
    placed: Any = (ENGINE_SL_ID, ENGINE_TP_ID),
    entry_error: Optional[int] = None,
):
    """Routes by (method, path). Any request this task does not expect is an
    error rather than a silent 200, so a stray call cannot pass unnoticed."""
    placed_ids = iter(placed)

    def responder(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if method == "GET" and path.startswith("/v2/orders/"):
            if entry_error is not None:
                return httpx.Response(
                    entry_error, json={"success": False, "error": {"code": "order_not_found"}}
                )
            return _ok(_entry_json() if entry is None else entry)
        if method == "GET" and path == "/v2/orders":
            return _ok(resting() if callable(resting) else list(resting))
        if method == "POST" and path == "/v2/orders":
            return _ok(_placed_json(next(placed_ids), json.loads(request.content.decode())))
        if method == "DELETE" and path == "/v2/orders":
            return _ok({"cancelled": True})
        if method == "GET" and path == "/v2/positions/margined":
            return _ok(positions() if callable(positions) else list(positions))
        raise AssertionError(f"unexpected request {method} {path}")

    return responder


# ══ A. The parse contract for the five new fields ═════════════════════════════


@pytest.fixture(autouse=True)
def _no_adoption_sleep(monkeypatch):
    """The retry's SPACING is not what any test here is about, and 1s per extra
    attempt would dominate the run. The bound itself is asserted for real in
    B06, and the declared values in A00 -- which reads this module's own
    import-time binding, so patching the production module cannot hide a change
    to them."""
    monkeypatch.setattr(trade_lifecycle, "BRACKET_ADOPTION_DELAY_SECONDS", 0.0)


def test_a00_the_retry_bound_is_declared_finite_and_short():
    """A wait inside the protection path is only safe because it happens after
    the exchange confirmed it holds the bracket. It must still be bounded."""
    assert BRACKET_ADOPTION_ATTEMPTS == 3
    assert 0 < BRACKET_ADOPTION_DELAY_SECONDS <= 2.0


def test_a01_a_bracket_parent_states_its_attached_levels():
    parsed = DeltaOrderResponse.from_dict(_entry_json())
    assert parsed.bracket_stop_loss_price == SL_PRICE
    assert parsed.bracket_take_profit_price == TP_PRICE
    assert parsed.bracket_order is False


def test_a02_a_bracket_leg_states_its_stop_contract():
    parsed = DeltaOrderResponse.from_dict(_sl_leg())
    assert parsed.bracket_order is True
    assert parsed.stop_order_type == "stop_loss_order"
    assert parsed.stop_trigger_method == "last_traded_price"
    assert parsed.stop_price == SL_PRICE
    assert parsed.reduce_only is True
    assert parsed.state is OrderStatus.PENDING


def test_a03_absence_is_answered_with_none_not_a_value():
    """`None` means "the exchange did not state it" and is never read as a
    value. An order object without these keys -- every non-bracket order, and
    possibly the live `/v2/orders` shape, which the evidence run could not
    observe because the account had no resting orders -- must parse cleanly."""
    parsed = DeltaOrderResponse.from_dict(_entry_json(bracket=False))
    assert parsed.bracket_stop_loss_price is None
    assert parsed.bracket_take_profit_price is None
    assert parsed.stop_order_type is None
    assert parsed.stop_trigger_method is None


@pytest.mark.parametrize("raw", (None, "", "   ", "abc", True, False, [1], {"a": 1}, "NaN"))
def test_a04_an_unreadable_bracket_price_is_never_adopted_as_a_level(raw):
    """A bracket price this engine cannot read must not become a level, and must
    not raise either: `get_open_orders` feeds reconciliation, so a parse that
    throws on one odd field would report the whole account as unreadable."""
    parsed = DeltaOrderResponse.from_dict(
        _entry_json(bracket_stop_loss_price=raw, bracket_take_profit_price=raw)
    )
    assert parsed.bracket_stop_loss_price is None
    assert parsed.bracket_take_profit_price is None


@pytest.mark.parametrize("raw", ("false", "true", 0, 1, "0", "1", None, [], "yes"))
def test_a05_only_a_real_boolean_counts_as_the_bracket_flag(raw):
    """`"false"` is a truthy string. Nothing but a real bool may answer here."""
    parsed = DeltaOrderResponse.from_dict(_entry_json(bracket_order=raw))
    assert parsed.bracket_order is None


@pytest.mark.parametrize("raw", ("stop_loss_order_v2", "SOMETHING_NEW", "trailing_stop"))
def test_a06_an_unrecognised_stop_descriptor_is_kept_raw_not_mapped(raw):
    """Kept as the wire string, so an unknown value can neither raise out of a
    plain order query nor be mapped onto a neighbouring enum member -- which
    would be an invented statement about what the order does."""
    parsed = DeltaOrderResponse.from_dict(_sl_leg(stop_order_type=raw))
    assert parsed.stop_order_type == raw
    assert parsed.stop_order_type != StopOrderType.STOP_LOSS_ORDER.to_exchange()


# ══ B. The gate: did the EXCHANGE say it holds a bracket? ═════════════════════


@pytest.mark.asyncio
async def test_b01_a_confirmed_exchange_bracket_is_adopted_and_nothing_is_placed():
    """The defect, closed. One position, one protective pair -- and that pair is
    the exchange's own, held by real order ids the rest of the engine can act
    on."""
    store = _store()
    client, rec = _client(_responder(resting=[_sl_leg(), _tp_leg()]))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert rec.posts() == [], "a second reduce-only pair was placed"
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    assert record.protected_quantity == SIZE
    assert "PROTECTION_ADOPTED_FROM_EXCHANGE" in _actions(store)
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_b02_no_echoed_bracket_falls_through_to_the_engines_own_pair():
    """The pre-§G2 path, unchanged and unalarmed: no exchange-side bracket means
    the engine's pair is the only protection there is."""
    store = _store()
    client, rec = _client(_responder(entry=_entry_json(bracket=False)))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert len(rec.posts()) == 2
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert record.protected_quantity == SIZE
    assert "PROTECTION_ADOPTED_FROM_EXCHANGE" not in _actions(store)
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_b03_a_bracket_at_unauthorised_levels_is_reported_not_adopted():
    """A live bracket at levels this record never authorised is neither adoptable
    nor ignorable. Protection is still placed -- the position must be covered at
    the authorised levels -- and the surplus pair is stated loudly."""
    store = _store()
    client, rec = _client(
        _responder(
            entry=_entry_json(
                bracket_stop_loss_price="2.7000", bracket_take_profit_price="3.2000"
            ),
            resting=[_sl_leg(), _tp_leg()],
        )
    )
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" in _actions(store)
    assert [a["code"] for a in manager.reconciliation_alerts] == [
        "PROTECTION_DUPLICATED_ON_EXCHANGE"
    ]
    assert len(rec.posts()) == 2
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == str(ENGINE_SL_ID)
    await client.close()


@pytest.mark.asyncio
async def test_b04_without_an_entry_order_id_the_exchange_is_not_even_asked():
    """There is nothing to ask about, so no order query is made at all."""
    store = _store()
    client, rec = _client(_responder())
    manager = _manager(client, store)
    record = _record(entry_order_id=None)

    await manager._ensure_bracket_protection(record, SIZE)

    assert not [c for c in rec.calls() if c.startswith("GET /v2/orders/")]
    assert len(rec.posts()) == 2
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_b05_an_unreadable_entry_confirms_nothing_and_alarms_nothing():
    """A failure to READ is a failure to confirm, so the caller places
    protection. No duplicate is asserted, because none was confirmed."""
    store = _store()
    client, rec = _client(_responder(entry_error=400))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert len(rec.posts()) == 2
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_b06_legs_that_never_appear_are_reported_and_protection_is_placed():
    """The gate held and the legs could not be found. Protection is placed
    anyway, because an unprotected position is the one unrecoverable outcome --
    and the duplication is recorded rather than hidden."""
    store = _store()
    client, rec = _client(_responder(resting=[]))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    queries = [c for c in rec.calls() if c == "GET /v2/orders"]
    assert len(queries) == BRACKET_ADOPTION_ATTEMPTS, "the retry was not bounded as declared"
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" in _actions(store)
    assert [a["code"] for a in manager.reconciliation_alerts] == [
        "PROTECTION_DUPLICATED_ON_EXCHANGE"
    ]
    assert len(rec.posts()) == 2
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


@pytest.mark.asyncio
async def test_b07_legs_that_appear_late_are_still_adopted():
    """Delta builds the legs when the entry fills, and this runs on the fill
    observation, so the lookup retries. The wait cannot open an unprotected
    window: it only happens after the exchange confirmed it holds the bracket."""
    store = _store()
    pages = [[], [_sl_leg(), _tp_leg()]]
    client, rec = _client(_responder(resting=lambda: pages.pop(0) if pages else []))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert rec.posts() == []
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    assert manager.reconciliation_alerts == []
    await client.close()


# ══ C. Which resting orders ARE those legs? ═══════════════════════════════════

#: Every mutation that must stop a leg being adopted, with the reason it is not
#: the protective order the engine authorised. The predicate is unanimous: one
#: failing clause refuses the whole leg.
UNADOPTABLE = {
    "trigger_series_is_mark_price": {"stop_trigger_method": "mark_price"},
    "trigger_series_not_stated": {"stop_trigger_method": None},
    "trigger_series_unknown": {"stop_trigger_method": "index_price"},
    "size_larger_than_the_position": {"size": "2", "unfilled_size": "2"},
    "size_smaller_than_the_position": {"size": "0", "unfilled_size": "0"},
    "not_reduce_only": {"reduce_only": False},
    "stop_price_is_not_the_authorised_level": {"stop_price": "2.7500"},
    "stop_price_not_stated": {"stop_price": None},
    "stop_type_not_stated": {"stop_order_type": None},
    "wrong_side_for_closing_a_long": {"side": "buy"},
    "already_terminal": {"state": "cancelled"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(UNADOPTABLE))
async def test_c01_a_leg_that_is_not_the_authorised_order_is_refused(case):
    """Refused, then the engine's own pair is placed and the duplication is
    stated. Refusing costs a surplus reduce-only leg; adopting the wrong leg
    costs the position its stop.

    `trigger_series_is_mark_price` is the load-bearing case: every leg in the
    account's history armed on `mark_price`, and `bracket_stop_trigger_method`
    is not echoed back, so if Delta ignores it this is the case that fires. It
    fails loudly instead of silently changing the exit contract.
    """
    store = _store()
    client, rec = _client(_responder(resting=[_sl_leg(**UNADOPTABLE[case]), _tp_leg()]))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.sl_order_id != str(EXCHANGE_SL_LEG_ID)
    assert len(rec.posts()) == 2
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" in _actions(store)
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


@pytest.mark.asyncio
async def test_c02_a_leg_on_another_product_is_not_this_positions_protection():
    store = _store()
    other = delta_india_registry().get("BTCUSD")
    client, rec = _client(
        _responder(
            resting=[
                _sl_leg(product_id=other.product_id, product_symbol=other.symbol),
                _tp_leg(),
            ]
        )
    )
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" in _actions(store)
    await client.close()


@pytest.mark.asyncio
async def test_c03_two_matching_legs_are_ambiguous_and_refused():
    """Adopting one of two would leave the other resting and untracked -- the
    exact failure this task exists to remove."""
    store = _store()
    client, rec = _client(
        _responder(resting=[_sl_leg(), _sl_leg(id=EXCHANGE_SL_LEG_ID + 50), _tp_leg()])
    )
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert len(rec.posts()) == 2
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" in _actions(store)
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", (True, False, None, "absent"))
async def test_c04_the_bracket_flag_is_informational_not_required(flag):
    """`bracket_order` is deliberately NOT part of the predicate. The live
    `/v2/orders` key set could not be observed -- the account had no resting
    orders during the evidence run -- so requiring the flag would turn a healthy
    adoption into the duplicated pair this task removes. Every other clause is
    exchange-stated and together they identify the order."""
    store = _store()
    over: Dict[str, Any] = {} if flag == "absent" else {"bracket_order": flag}
    sl, tp = _sl_leg(**over), _tp_leg(**over)
    if flag == "absent":
        sl.pop("bracket_order")
        tp.pop("bracket_order")
    client, rec = _client(_responder(resting=[sl, tp]))
    manager = _manager(client, store)
    record = _record()

    await manager._ensure_bracket_protection(record, SIZE)

    assert rec.posts() == []
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_c05_a_short_adopts_the_buy_side_legs():
    """The closing side is derived from the record's direction, not assumed."""
    store = _store()
    client, rec = _client(
        _responder(
            entry=_entry_json(
                side="sell",
                bracket_stop_loss_price=str(TP_PRICE),
                bracket_take_profit_price=str(SL_PRICE),
            ),
            resting=[
                _leg_json(EXCHANGE_SL_LEG_ID, "stop_loss_order", TP_PRICE, side="buy"),
                _leg_json(EXCHANGE_TP_LEG_ID, "take_profit_order", SL_PRICE, side="buy"),
            ],
        )
    )
    manager = _manager(client, store)
    record = _record(
        direction=TradeDirection.SHORT,
        stop_loss_price=TP_PRICE,
        take_profit_price=SL_PRICE,
    )

    await manager._ensure_bracket_protection(record, SIZE)

    assert rec.posts() == []
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    await client.close()


# ══ D. The consumers -- why adoption is not a claim ═══════════════════════════


@pytest.mark.asyncio
async def test_d01_reconciliation_sees_the_adopted_legs_as_live_protection():
    """`reconcile_active_trades_with_exchange` decides `sl_live`/`tp_live` from
    membership of `get_open_orders`. Because adoption stored the exchange's real
    ids, the check passes on the exchange's own legs with no rebuild -- which is
    the whole reason a boolean "exchange-protected" flag was rejected."""
    store = _store()
    legs = [_sl_leg(), _tp_leg()]
    client, rec = _client(_responder(resting=legs, positions=[_position_json("1")]))
    manager = _manager(client, store)
    record = _record()
    await manager._ensure_bracket_protection(record, SIZE)
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    manager._active_trades[record.setup_id] = record
    record.filled_quantity = SIZE

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["checked"] == 1
    assert summary["unresolved"] == []
    assert summary["orphan_orders"] == []
    assert "RECONCILIATION_PROTECTION_MISSING" not in _actions(store)
    assert rec.posts() == []
    assert manager.reconciliation_alerts == []
    await client.close()


@pytest.mark.asyncio
async def test_d02_adopted_protection_that_vanishes_is_still_rebuilt():
    """The decisive test. Adoption records ids, never a belief. When the legs are
    no longer on the exchange, reconciliation forgets them and the existing
    bracket path rebuilds protection for the real position size."""
    store = _store()
    pages: List[Any] = [[_sl_leg(), _tp_leg()]]
    client, rec = _client(
        _responder(
            resting=lambda: pages.pop(0) if pages else [],
            positions=[_position_json("1")],
        )
    )
    manager = _manager(client, store)
    record = _record()
    await manager._ensure_bracket_protection(record, SIZE)
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    manager._active_trades[record.setup_id] = record
    record.filled_quantity = SIZE

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert "RECONCILIATION_PROTECTION_MISSING" in _actions(store)
    assert summary["protection_restored"] == [record.setup_id]
    assert len(rec.posts()) == 2
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert record.protected_quantity == SIZE
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


@pytest.mark.asyncio
async def test_d03_the_cancel_path_acts_on_the_exchanges_own_leg_ids():
    """`close_position`, the kill switch and the resize path all cancel through
    `_cancel_existing_brackets`. After adoption it cancels the exchange's legs,
    so the pair the exchange built cannot outlive the position."""
    store = _store()
    client, rec = _client(_responder(resting=[_sl_leg(), _tp_leg()]))
    manager = _manager(client, store)
    record = _record()
    await manager._ensure_bracket_protection(record, SIZE)

    await manager._cancel_existing_brackets(record, PRODUCT_ID)

    cancelled = {body.get("id") for body in rec.deletes()}
    assert cancelled == {EXCHANGE_SL_LEG_ID, EXCHANGE_TP_LEG_ID}
    assert record.sl_order_id is None
    assert record.tp_order_id is None
    assert record.protected_quantity == Decimal("0")
    await client.close()


# ══ E. A growing partial fill ═════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e01_resizing_after_adoption_raises_no_false_duplicate():
    """§M2: a partial fill that grows must end with protection for exactly the
    filled size, so the adopted legs are cancelled first. Their absence
    afterwards is expected -- this call removed them -- and the entry's echo
    cannot distinguish that from a real duplicate, so no alert is raised."""
    store = _store()
    pages: List[Any] = [[_sl_leg(), _tp_leg()]]
    client, rec = _client(
        _responder(resting=lambda: pages.pop(0) if pages else [], placed=(ENGINE_SL_ID, ENGINE_TP_ID))
    )
    manager = _manager(client, store)
    record = _record()
    await manager._ensure_bracket_protection(record, SIZE)
    assert record.protected_quantity == SIZE

    await manager._ensure_bracket_protection(record, Decimal("2"))

    assert {b.get("id") for b in rec.deletes()} == {EXCHANGE_SL_LEG_ID, EXCHANGE_TP_LEG_ID}
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    assert [Decimal(str(b["size"])) for b in rec.posts()] == [Decimal("2"), Decimal("2")]
    assert record.protected_quantity == Decimal("2")
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


@pytest.mark.asyncio
async def test_e02_a_rebuilt_exchange_bracket_at_the_new_size_is_adopted_again():
    """If Delta does rebuild its legs at the new size, those are the right orders
    to hold, so adoption is still attempted after the resize cancel."""
    store = _store()
    resized = [
        _leg_json(EXCHANGE_SL_LEG_ID + 7, "stop_loss_order", SL_PRICE, size="2", unfilled_size="2"),
        _leg_json(EXCHANGE_TP_LEG_ID + 7, "take_profit_order", TP_PRICE, size="2", unfilled_size="2"),
    ]
    pages: List[Any] = [[_sl_leg(), _tp_leg()]]
    client, rec = _client(_responder(resting=lambda: pages.pop(0) if pages else resized))
    manager = _manager(client, store)
    record = _record()
    await manager._ensure_bracket_protection(record, SIZE)

    await manager._ensure_bracket_protection(record, Decimal("2"))

    assert rec.posts() == []
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID + 7)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID + 7)
    assert record.protected_quantity == Decimal("2")
    assert manager.reconciliation_alerts == []
    await client.close()
