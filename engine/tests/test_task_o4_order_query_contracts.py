"""
Task O §O4 -- the cancel and order-query wire contracts.

Task N found four order-path calls whose shapes are not the documented ones.
Each is a different way of acting on an order the exchange never named:

  * `GET /v2/orders` was sent `state=open` + `product_id`. The documented
    parameters are `states` and `product_ids`, both CSV, both capped at 10.
    Undocumented parameters may simply be ignored, so a filtered question could
    come back as an unfiltered answer.
  * `DELETE /v2/orders/{id}` with a body of only `{product_id}`. The documented
    cancel is `DELETE /v2/orders` with `{id, client_order_id, product_id}` in
    the body -- so the old form's body identified no order at all.
  * `GET /v2/orders?client_order_id=...` followed by adopting `results[0]`.
    The documented lookup is the path `GET /v2/orders/client_order_id/{id}`,
    and the adoption is the dangerous half: `_resolve_entry_order` copies the
    returned `id` into `record.entry_order_id`, which the cancel and bracket
    paths then act on.
  * `GET /v2/fills` sent an `order_id` filter the endpoint does not document,
    and swallowed every exception into `[]` -- "no fills" reported for "we
    could not find out".

The `states=open,pending` value carries the sharpest safety consequence and is
pinned twice below, once on the wire and once through reconciliation: §O1 made
the stop-loss a documented `stop_loss_order`, an untriggered stop rests in
`pending`, and `reconcile_active_trades_with_exchange` decides `sl_live` from
membership of this list. Asking only for `open` reports healthy protection as
missing, and the reconciliation response to missing protection is to discard
the order id and place the bracket again -- a duplicate live stop.

Zero network access: every request is served by `httpx.MockTransport`.
"""

import ast
import json
import re
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
from quantedge.execution.models import OrderStatus
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.models import TradeDirection

PRODUCTION_ROOT = Path(execution_models.__file__).resolve().parents[1]

ACCOUNT = "acc_task_o4"
USER = "user_task_o4"
SETUP = "BTCUSD_1h_MANUAL_SMC_O4_LONG"
BTCUSD_PRODUCT_ID = 27
ENTRY_PRICE = Decimal("95000.0")


# ── Transport plumbing ────────────────────────────────────────────────────────


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

    def body(self, index: int = -1) -> Dict[str, Any]:
        return json.loads(self.requests[index].content.decode())


def _client(responder) -> tuple:
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O4_000000001",
        api_secret="TEST_SECRET_TASK_O4_00000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder


def _order_json(order_id: int = 5001, *, state: str = "open",
                client_order_id: str = "QE-O4-0001", **over) -> Dict[str, Any]:
    payload = {
        "id": order_id,
        "client_order_id": client_order_id,
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "user_id": 1,
        "side": "buy",
        "order_type": "limit_order",
        "size": "2",
        "unfilled_size": "2",
        "limit_price": "94500.00",
        "stop_price": None,
        "state": state,
        "reduce_only": False,
        "created_at": 1724261234000000,
    }
    payload.update(over)
    return payload


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


# ══ A. `GET /v2/orders` -- the documented query parameters ═════════════════════


@pytest.mark.asyncio
async def test_a_open_orders_sends_states_not_the_undocumented_singular_state():
    client, rec = _client(lambda r: _ok([_order_json()]))
    await client.get_open_orders()

    assert rec.last.url.path == "/v2/orders"
    assert rec.last.url.params["states"] == "open,pending"
    assert "state" not in dict(rec.last.url.params)


@pytest.mark.asyncio
async def test_a_open_orders_asks_for_pending_so_a_resting_stop_is_not_hidden():
    """The §O1 stop-loss rests in `pending` until it triggers."""
    client, rec = _client(lambda r: _ok([]))
    await client.get_open_orders()
    assert "pending" in rec.last.url.params["states"].split(",")


@pytest.mark.asyncio
async def test_a_a_pending_order_in_the_answer_is_returned_not_discarded():
    client, rec = _client(lambda r: _ok([_order_json(7001, state="pending")]))
    orders = await client.get_open_orders()

    assert [o.id for o in orders] == [7001]
    assert orders[0].state is OrderStatus.PENDING


@pytest.mark.asyncio
async def test_a_open_orders_sends_product_ids_csv_not_the_singular_product_id():
    client, rec = _client(lambda r: _ok([]))
    await client.get_open_orders(product_id=BTCUSD_PRODUCT_ID)

    assert rec.last.url.params["product_ids"] == "27"
    assert "product_id" not in dict(rec.last.url.params)


@pytest.mark.asyncio
async def test_a_several_products_are_joined_as_documented_csv():
    client, rec = _client(lambda r: _ok([]))
    await client.get_open_orders(product_ids=[27, 3136, 14823, 14969])
    assert rec.last.url.params["product_ids"] == "27,3136,14823,14969"


@pytest.mark.asyncio
async def test_a_no_product_filter_sends_no_product_parameter_at_all():
    client, rec = _client(lambda r: _ok([]))
    await client.get_open_orders()
    assert "product_ids" not in dict(rec.last.url.params)


@pytest.mark.asyncio
async def test_a_more_than_ten_products_fails_closed_without_asking():
    """
    Exceeding the documented cap would silently truncate the filter, i.e.
    answer about the wrong instruments. No request may leave.
    """
    client, rec = _client(lambda r: _ok([]))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders(product_ids=list(range(11)))
    assert rec.requests == []


@pytest.mark.asyncio
async def test_a_exactly_ten_products_is_still_allowed():
    client, rec = _client(lambda r: _ok([]))
    await client.get_open_orders(product_ids=[27] * 10)
    assert rec.last.url.params["product_ids"].count(",") == 9


@pytest.mark.asyncio
async def test_a_a_non_list_result_fails_closed_rather_than_reporting_no_orders():
    """
    Amended by Task O §O6 (was `..._is_no_orders_rather_than_a_crash`, which
    asserted `== []`). Returning `[]` for a malformed envelope is not a graceful
    degradation, it is a fabricated observation of *no working orders*:
    `reconcile_account` computes
    `exchange_is_flat = not exchange_positions and not exchange_orders`, so this
    path can force-release the single-trade lock (rules #11/#14) and let the
    pre-trade gate authorize a new order. The original intent -- "rather than a
    crash", i.e. no `TypeError`/`AttributeError` escaping from inside the parse
    loop -- is preserved and strengthened: the failure is now the deliberate,
    typed, catchable `DeltaResponseError` that every caller's fail-closed path
    already handles, and it is raised before any item is parsed.

    A genuinely empty list is a real observation and still returns `[]`.
    """
    client, rec = _client(lambda r: _ok({"id": 1}))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()

    client, rec = _client(lambda r: _ok([]))
    assert await client.get_open_orders() == []


# ══ B. `DELETE /v2/orders` -- the order is named in the body ═══════════════════


@pytest.mark.asyncio
async def test_b_cancel_uses_the_collection_path_with_the_id_in_the_body():
    client, rec = _client(lambda r: _ok({"id": 9001, "state": "cancelled"}))
    assert await client.cancel_order(order_id=9001, product_id=27) is True

    assert rec.last.method == "DELETE"
    assert rec.last.url.path == "/v2/orders"
    assert rec.body() == {"id": 9001, "product_id": 27}


@pytest.mark.asyncio
async def test_b_the_order_id_is_never_placed_in_the_path_again():
    client, rec = _client(lambda r: _ok({"id": 9001}))
    await client.cancel_order(order_id=9001, product_id=27)
    assert "9001" not in rec.last.url.path


@pytest.mark.asyncio
async def test_b_a_string_id_is_sent_as_the_documented_integer():
    """Callers hold `record.sl_order_id` as a string; `id` is documented int."""
    client, rec = _client(lambda r: _ok({"id": 9001}))
    await client.cancel_order(order_id="9001", product_id=27)

    body = rec.body()
    assert body["id"] == 9001
    assert isinstance(body["id"], int)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "abc", "90.5", "ORD-LIVE-1001", None])
async def test_b_an_unusable_id_fails_closed_without_sending_a_cancel(bad):
    """A cancel that cannot name its order must not be sent at all."""
    client, rec = _client(lambda r: _ok({}))
    with pytest.raises(DeltaResponseError):
        await client.cancel_order(order_id=bad, product_id=27)
    assert rec.requests == []


@pytest.mark.asyncio
async def test_b_a_failed_cancel_fails_closed_task_o9():
    """Task O §O9 STRENGTHENS this assertion; it is not weakened or deleted.

    O4 originally pinned `cancel_order(...) is False` for an HTTP 200 +
    `{"success": false}` envelope, encoding the client's `return
    bool(data.get("success", False))`. §O9 proved that boolean unsafe *as
    consumed*: every `src/` cancel call site discards the return value and
    reacts only to exceptions, so `_cancel_existing_brackets` treats a silent
    `False` as "the old stop is gone", nulls the id, and places a second live
    reduce-only stop (the §M2 duplicate-protection hazard); `activate_kill_switch`
    audits a still-live entry as cancelled.

    An application-level failure returned with HTTP 200 is now refused at the
    single envelope guard in `request()`, before any endpoint sees it, so a
    cancel that the exchange did not confirm raises `DeltaResponseError` rather
    than reporting a fabricated `False`. The wire form the rest of this section
    pins is unchanged; only the failure signal is strengthened from a discarded
    boolean into a raise a caller cannot ignore.

    Task O §O10 completes the other half, and STRENGTHENS this again. Raising is
    only an improvement if the caller does something with the raise, and the two
    call sites named above caught it, logged a warning, and carried on:
    `close_position` archived the trade, deleted the position and released the
    single-trade lock; `activate_kill_switch` archived the trade and left the
    failure visible only as an absence from `cancelled_orders`. §O10 makes each
    refusal a question put back to the exchange -- see
    `test_task_o10_cancel_outcome_contracts.py` -- where only a positively
    confirmed terminal state permits archival and lock release, and
    `order_not_found`, FILLED, still-resting and unclassifiable all fail closed.
    """
    client, rec = _client(
        lambda r: httpx.Response(200, json={"success": False, "result": None}))
    with pytest.raises(DeltaResponseError):
        await client.cancel_order(order_id=9001, product_id=27)
    # The refusal happens after exactly one request -- the DELETE was still sent
    # with the documented body; it is the *response* that is refused.
    assert len(rec.requests) == 1
    assert rec.last.method == "DELETE"
    assert rec.body() == {"id": 9001, "product_id": 27}


@pytest.mark.asyncio
async def test_b_cancel_by_client_id_still_uses_the_documented_body_form():
    """Unchanged by §O4; asserted so the correction cannot regress it."""
    client, rec = _client(lambda r: _ok({"id": 9001}))
    await client.cancel_order_by_client_id(client_order_id="QE-O4-0001",
                                           product_id=27)
    assert rec.last.url.path == "/v2/orders"
    assert rec.body() == {"product_id": 27, "client_order_id": "QE-O4-0001"}


# ══ C. `GET /v2/orders/client_order_id/{id}` -- identity is verified ═══════════


@pytest.mark.asyncio
async def test_c_the_lookup_uses_the_documented_path():
    client, rec = _client(lambda r: _ok(_order_json(client_order_id="QE-O4-ABC")))
    found = await client.get_order_by_client_id("QE-O4-ABC")

    assert rec.last.url.path == "/v2/orders/client_order_id/QE-O4-ABC"
    assert "client_order_id" not in dict(rec.last.url.params)
    assert found is not None
    assert found.id == 5001


@pytest.mark.asyncio
async def test_c_a_mismatched_client_order_id_is_not_adopted():
    """
    The heart of §O4. The old code returned `results[0]` whatever it was, and
    `_resolve_entry_order` writes the returned `id` into `record.entry_order_id`
    -- so an unverified row becomes an order this engine cancels and brackets.
    """
    client, rec = _client(
        lambda r: _ok(_order_json(4242, client_order_id="SOMEONE-ELSES-ORDER")))
    with pytest.raises(DeltaResponseError) as exc:
        await client.get_order_by_client_id("QE-O4-MINE")
    assert "4242" not in str(exc.value) or "refusing" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", ["qe-o4-mine", "QE-O4-MINE ", " QE-O4-MINE",
                                      "QE-O4-MINE\n", "QE_O4_MINE", None, ""])
async def test_c_identity_is_byte_exact_with_no_folding(returned):
    """Case, whitespace and separators are all identity, not formatting."""
    client, rec = _client(lambda r: _ok(_order_json(client_order_id=returned)))
    with pytest.raises(DeltaResponseError):
        await client.get_order_by_client_id("QE-O4-MINE")


@pytest.mark.asyncio
async def test_c_an_exact_match_is_accepted():
    client, rec = _client(lambda r: _ok(_order_json(client_order_id="QE-O4-MINE")))
    found = await client.get_order_by_client_id("QE-O4-MINE")
    assert found is not None
    assert found.client_order_id == "QE-O4-MINE"


@pytest.mark.asyncio
async def test_c_a_null_result_is_a_genuine_absence():
    """The order does not exist. That is an answer, and it is not an error."""
    client, rec = _client(lambda r: _ok(None))
    assert await client.get_order_by_client_id("QE-O4-MISSING") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [
    [_order_json(1, client_order_id="QE-O4-MINE"),
     _order_json(2, client_order_id="QE-O4-MINE")],
    [_order_json(1, client_order_id="QE-O4-MINE")],
    [],
    "QE-O4-MINE",
    17,
])
async def test_c_an_ambiguous_or_malformed_result_fails_closed(shape):
    """
    A list means the endpoint did not behave as a single-order lookup. Picking
    an element out of it is precisely the defect being removed.
    """
    client, rec = _client(lambda r: _ok(shape))
    with pytest.raises(DeltaResponseError):
        await client.get_order_by_client_id("QE-O4-MINE")


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n", None, 17])
async def test_c_a_blank_client_order_id_never_reaches_the_wire(blank):
    """
    An empty id would address `/v2/orders/client_order_id/`, i.e. the
    collection, and any order in that answer could then be adopted.
    """
    client, rec = _client(lambda r: _ok([_order_json()]))
    with pytest.raises(DeltaResponseError):
        await client.get_order_by_client_id(blank)
    assert rec.requests == []


@pytest.mark.asyncio
async def test_c_an_id_with_url_significant_characters_is_quoted():
    """No client id may escape its path segment."""
    client, rec = _client(lambda r: _ok(_order_json(client_order_id="QE/O4?x=1")))
    found = await client.get_order_by_client_id("QE/O4?x=1")

    assert rec.last.url.raw_path == b"/v2/orders/client_order_id/QE%2FO4%3Fx%3D1"
    assert rec.last.url.params == httpx.QueryParams()
    assert found is not None


# ══ D. `GET /v2/fills` -- no phantom filter, no swallowed failure ══════════════


@pytest.mark.asyncio
async def test_d_the_fills_request_never_sends_an_order_id_parameter():
    client, rec = _client(lambda r: _ok([]))
    await client.get_fills(order_id=9001, product_id=27)

    assert rec.last.url.path == "/v2/fills"
    assert "order_id" not in dict(rec.last.url.params)
    assert "order_id" not in str(rec.last.url)


@pytest.mark.asyncio
async def test_d_the_order_filter_is_applied_locally_to_what_came_back():
    fills = [
        {"id": 1, "order_id": 9001, "size": "1"},
        {"id": 2, "order_id": 9002, "size": "1"},
        {"id": 3, "order_id": "9001", "size": "2"},
    ]
    client, rec = _client(lambda r: _ok(fills))
    got = await client.get_fills(order_id=9001, product_id=27)
    assert [f["id"] for f in got] == [1, 3]


@pytest.mark.asyncio
async def test_d_a_fill_that_names_no_order_is_not_attributed_to_one():
    fills = [{"id": 1, "size": "1"}, {"id": 2, "order_id": None}]
    client, rec = _client(lambda r: _ok(fills))
    assert await client.get_fills(order_id=9001) == []


@pytest.mark.asyncio
async def test_d_without_an_order_id_every_returned_fill_is_kept():
    fills = [{"id": 1, "order_id": 9001}, {"id": 2, "order_id": 9002}]
    client, rec = _client(lambda r: _ok(fills))
    assert await client.get_fills(product_id=27) == fills


@pytest.mark.asyncio
async def test_d_a_transport_failure_is_no_longer_reported_as_no_fills():
    """
    "No fills" and "we could not find out" are different facts. The blanket
    `except Exception: return []` reported the first for the second.
    """
    client, rec = _client(lambda r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(Exception) as exc:
        await client.get_fills(order_id=9001)
    assert not isinstance(exc.value, AssertionError)


@pytest.mark.asyncio
async def test_d_a_non_list_result_is_no_fills():
    client, rec = _client(lambda r: _ok({"id": 1}))
    assert await client.get_fills() == []


# ══ E. The reconciliation consequence of `states=open,pending` ═════════════════


def _manager(client) -> TradeLifecycleManager:
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
        single_trade_lock=SingleTradeLockManager(),
    )


def _protected_record(manager, sl_order_id: str, tp_order_id: str,
                      size: Decimal) -> TradeLifecycleRecord:
    record = TradeLifecycleRecord(
        setup_id=SETUP,
        account_id=ACCOUNT,
        user_id=USER,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        requested_quantity=size,
        entry_price=ENTRY_PRICE,
        stop_loss_price=Decimal("94000.0"),
        take_profit_price=Decimal("98000.0"),
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("100"),
        reward_amount=Decimal("300"),
        entry_order_id="8000",
        entry_client_order_id=f"QE_BTCUSD_ENTRY_{SETUP}",
        state=TradeLifecycleState.PROTECTED_POSITION,
    )
    record.filled_quantity = size
    record.protected_quantity = size
    record.sl_order_id = sl_order_id
    record.tp_order_id = tp_order_id
    manager._active_trades[SETUP] = record
    return record


def _position_json(size: str = "3") -> Dict[str, Any]:
    return {
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "size": size,
        "entry_price": "95000.0",
        "mark_price": "95500.0",
        "liquidation_price": "90000.0",
        "unrealised_pnl": "1.50",
        "realised_pnl": "0.00",
        "leverage": "10",
        "margin": "28.50",
    }


@pytest.mark.asyncio
async def test_e_a_resting_pending_stop_counts_as_live_protection():
    """
    The whole point of `pending`. The SL is a §O1 `stop_loss_order` that has not
    triggered, so the exchange reports it in `pending`. Reconciliation must see
    it, keep `sl_order_id`, and place nothing.
    """
    size = Decimal("3")

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/positions/margined":
            return _ok([_position_json(str(size))])
        if request.url.path == "/v2/orders" and request.method == "GET":
            return _ok([
                _order_json(9101, state="pending", client_order_id="QE-SL",
                            order_type="stop_loss_order", size=str(size),
                            unfilled_size=str(size), reduce_only=True),
                _order_json(9102, state="open", client_order_id="QE-TP",
                            size=str(size), unfilled_size=str(size),
                            reduce_only=True),
            ])
        raise AssertionError(f"unexpected call {request.method} {request.url.path}")

    client, rec = _client(responder)
    manager = _manager(client)
    record = _protected_record(manager, "9101", "9102", size)

    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)

    assert record.sl_order_id == "9101"
    assert record.tp_order_id == "9102"
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.protected_quantity == size
    assert summary["unresolved"] == []
    assert not any(e["action"] == "RECONCILIATION_PROTECTION_MISSING"
                   for e in manager.state_store.audit_events)
    # Nothing was placed: the only writes to /v2/orders would be POSTs.
    assert [r for r in rec.requests if r.method == "POST"] == []


@pytest.mark.asyncio
async def test_e_an_sl_the_exchange_does_not_report_is_still_treated_as_missing():
    """
    The correction widens the question, it does not weaken the answer. An order
    absent from `open,pending` is genuinely gone, and the existing rebuild must
    still fire.
    """
    size = Decimal("3")

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/positions/margined":
            return _ok([_position_json(str(size))])
        if request.url.path == "/v2/orders" and request.method == "GET":
            return _ok([_order_json(9102, state="open", client_order_id="QE-TP",
                                    reduce_only=True)])
        return _ok({"id": 1})

    client, rec = _client(responder)
    manager = _manager(client)
    record = _protected_record(manager, "9101", "9102", size)

    await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)

    assert any(e["action"] == "RECONCILIATION_PROTECTION_MISSING"
               for e in manager.state_store.audit_events)


# ══ F. Repository invariants, not samples ═════════════════════════════════════


def _production_sources():
    files = sorted(PRODUCTION_ROOT.rglob("*.py"))
    assert len(files) > 20, "the sweep below must actually be reading the package"
    return [(p, p.read_text(encoding="utf-8")) for p in files]


def _function(rel: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((PRODUCTION_ROOT / rel).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


def test_f_no_production_path_sends_the_undocumented_singular_state_param():
    pattern = re.compile(r"""["']state["']\s*:\s*["']open["']""")
    offenders = [
        f"{path.relative_to(PRODUCTION_ROOT)}:{i}"
        for path, text in _production_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_f_no_production_path_deletes_an_order_by_path_segment():
    """`DELETE /v2/orders/{id}` is the undocumented cancel route."""
    offenders = [
        f"{path.relative_to(PRODUCTION_ROOT)}:{i}"
        for path, text in _production_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if re.search(r"""["']DELETE["'].*/v2/orders/""", line)
    ]
    assert offenders == []


def test_f_the_open_orders_query_is_built_from_the_documented_names():
    fn = _function("execution/delta_client.py", "get_open_orders")
    literals = {n.value for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "states" in literals
    assert "product_ids" in literals
    assert "state" not in literals
    assert "product_id" not in literals
    assert "open,pending" in literals


def test_f_the_client_order_id_lookup_compares_before_it_constructs():
    """
    Structural guarantee for §O4's core rule: the function must contain an
    equality test against the requested id, and must not index a result.
    """
    fn = _function("execution/delta_client.py", "get_order_by_client_id")
    compares = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)
                and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in n.ops)]
    assert compares, "the returned identity must be compared to the requested one"

    # The return annotation is `Optional[...]`, itself a Subscript, so only the
    # body is inspected: no element may be picked out of a result.
    subscripts = [n for stmt in fn.body for n in ast.walk(stmt)
                  if isinstance(n, ast.Subscript)]
    assert subscripts == [], "no element may be picked out of a result"
    for folder in ("upper", "lower", "casefold"):
        assert folder not in {n.attr for n in ast.walk(fn)
                              if isinstance(n, ast.Attribute)}


def test_f_the_fills_request_has_no_order_id_in_its_param_dict():
    fn = _function("execution/delta_client.py", "get_fills")
    keys = {
        node.value
        for assign in ast.walk(fn)
        if isinstance(assign, ast.Subscript)
        for node in ast.walk(assign)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "order_id" not in keys


def test_f_the_fills_path_no_longer_swallows_every_exception():
    fn = _function("execution/delta_client.py", "get_fills")
    handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    assert handlers == []


def test_f_no_caller_signature_was_broken():
    """
    `execution_engine` invokes the orders query by keyword, and the lifecycle
    cancels by positional pair. Both shapes must still exist.
    """
    import inspect

    params = inspect.signature(DeltaIndiaClient.get_open_orders).parameters
    assert "product_id" in params
    assert params["product_id"].default is None
    assert params["product_ids"].default is None

    cancel = inspect.signature(DeltaIndiaClient.cancel_order).parameters
    assert list(cancel) == ["self", "order_id", "product_id"]


# ══ Task O global guards -- unchanged by O4 ════════════════════════════════════


def test_the_governance_state_is_untouched():
    from quantedge.execution.synchronizer import AccountRecord
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AccountRecord.__dataclass_fields__["algo_enabled"].default is False
    assert AccountRecord.__dataclass_fields__["kill_switch_active"].default is True


def test_no_new_exchange_host_was_introduced():
    from quantedge.execution import delta_client, private_websocket

    assert delta_client.DELTA_INDIA_PRODUCTION_URL == "https://api.india.delta.exchange"
    assert delta_client.DELTA_INDIA_TESTNET_URL == "https://api-testnet.delta.exchange"
    assert private_websocket.WS_ENDPOINT == "wss://socket.india.delta.exchange"

    text = (PRODUCTION_ROOT / "execution" / "delta_client.py").read_text(
        encoding="utf-8")
    hosts = set(re.findall(r"(?:https|wss)://[\w.\-]+", text))
    assert hosts <= {"https://api.india.delta.exchange",
                     "https://api-testnet.delta.exchange"}, hosts


def test_the_execution_authority_guard_still_precedes_the_cancel():
    """
    §O4 changed the wire shape of `cancel_order`; the production block on direct
    cancellation must still come first.
    """
    fn = _function("execution/delta_client.py", "cancel_order")
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    names = {
        n.id for r in raises for n in ast.walk(r) if isinstance(n, ast.Name)
    }
    assert "DeltaExecutionAuthorityError" in names

    guard = next(n for n in fn.body if isinstance(n, ast.If))
    assert "DeltaExecutionAuthorityError" in ast.dump(guard)
    requests = [n for n in ast.walk(fn) if isinstance(n, ast.Await)]
    assert all(n.lineno > guard.end_lineno for n in requests)
