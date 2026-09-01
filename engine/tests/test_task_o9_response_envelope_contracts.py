"""
Task O §O9 -- the HTTP-200 `{"success": false}` envelope must fail closed.

Delta answers an application-level failure with HTTP 200 and a body of
`{"success": false, ...}` (and sometimes an absent or empty `result`). The
status-code ladder in `DeltaIndiaClient.request` cannot see that -- 401/429/400/
5xx are the only things it reacts to -- so until §O9 the decoded envelope was
returned as if it were a valid answer, and every endpoint then applied
`data.get("result", [])`. That default converted "the exchange refused to tell
us" into an EMPTY COLLECTION:

  * `get_wallet_balances`  -> `[]`, and through it `get_account_summary` builds a
    fabricated zero-equity account via `sum((...), Decimal("0"))`;
  * `get_positions`        -> `[]`, i.e. a FALSE FLAT exchange;
  * `get_open_orders`      -> `[]`, i.e. "no working orders" -- a resting stop
    reported as missing protection.

The §O6/§O7/§O8 guards could not catch this: they are row-level, and zero rows
means there is nothing for them to refuse. Downstream, the empty reads as a flat
exchange, which is the single most dangerous fabrication in this codebase --
`synchronizer` closes local positions and infers resting orders away,
`reconciliation` force-releases the single-trade lock, and `multi_user_
orchestrator` treats "no positions" as "safe to place a new trade".

UNKNOWN is not FLAT, and a default must never answer a safety question. §O9 adds
ONE envelope guard, in `request()`, after a successful JSON decode and before the
return: the body must be a `dict`, and `success` must be present and truthy, or
the existing `DeltaResponseError` is raised before `result` is ever read. This
mirrors the pre-existing `get_ticker` check (delta_client.py) and the ingestion
path, both of which already refuse a falsy-or-absent `success` on this same host.
`_connection_state` stays CONNECTED because the transport genuinely succeeded;
the status ladder keeps running first, so `DeltaAuthError`, `DeltaRateLimitError`,
`DeltaOrderRejectedError` and `DeltaConnectionError` keep their own taxonomy.

B1 decision (recorded): the guard applies to the DELETE/cancel paths too. The
O4 assertion that a `success=false` cancel is "reported as False" is
STRENGTHENED, in its own file, to expect the raise -- never weakened -- because
every `src/` cancel call site discards that boolean and reacts only to
exceptions. Fixing those discarding call sites is separate follow-up work and is
NOT done here.

Out of scope by decision, pinned below so they are not mistaken for oversights:
`get_fills`' non-list `-> []` (owned by §O4) and the WS fill identity
`str(data.get("order_id", ""))` (I2) each keep their own task.

Zero network access: every request is served by `httpx.MockTransport`.
"""

import ast
import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

import quantedge.execution.delta_client as delta_client_module
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaAuthError,
    DeltaConnectionError,
    DeltaIndiaClient,
    DeltaOrderRejectedError,
    DeltaRateLimitError,
    DeltaResponseError,
)
from quantedge.execution.models import DeltaOrderRequest, OrderSide, OrderType, PositionSide
from quantedge.execution.private_websocket import EventValidator
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    LiveAccountSyncService,
    LocalStateStore,
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
from quantedge.execution.multi_user_orchestrator import (
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.instruments import delta_india_registry
from quantedge.strategy.models import TradeDirection

PRODUCTION_ROOT = Path(delta_client_module.__file__).resolve().parents[1]

ACCOUNT = "acc_task_o9"
USER = "user_task_o9"
SETUP = "BTCUSD_1h_MANUAL_SMC_O9_LONG"
BTCUSD = "BTCUSD"
BTCUSD_PRODUCT_ID = delta_india_registry().get(BTCUSD).product_id
ENTRY_PRICE = Decimal("95000.0")
VALID_ORDER_ID = 950009


# ── Transport plumbing (identical in shape to §O4/§O6/§O7/§O8) ────────────────


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

    def methods(self) -> List[str]:
        return [r.method for r in self.requests]

    def body(self, index: int = -1) -> Dict[str, Any]:
        return json.loads(self.requests[index].content.decode())


def _client(responder) -> tuple:
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O9_000000001",
        api_secret="TEST_SECRET_TASK_O9_00000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _unsuccessful(result=None, **over) -> httpx.Response:
    """An HTTP 200 body whose `success` is explicitly false -- the §O9 defect."""
    body: Dict[str, Any] = {"success": False, "result": result,
                            "error": {"code": "some_rejection"}}
    body.update(over)
    return httpx.Response(200, json=body)


def _order_json(order_id: Any = VALID_ORDER_ID, **over) -> Dict[str, Any]:
    """A well-formed `/v2/orders` object; `over` drives each case."""
    payload: Dict[str, Any] = {
        "id": order_id,
        "client_order_id": "QE-O9-0001",
        "user_id": 5511,
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": BTCUSD,
        "side": "buy",
        "order_type": "limit_order",
        "size": "3",
        "unfilled_size": "3",
        "limit_price": "94500.00",
        "stop_price": None,
        "average_fill_price": None,
        "state": "open",
        "reduce_only": False,
        "created_at": 1756339200000000,
    }
    payload.update(over)
    return payload


def _wallet_json() -> Dict[str, Any]:
    """A §O7-clean wallet entry, so only the envelope is ever the defect."""
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


# ══ A. The single envelope guard refuses every fail-open getter ═══════════════
#
# Areas 1-6: false+empty, false+absent, missing success, false positions,
# false open orders, false wallet/account summary.


@pytest.mark.asyncio
async def test_a01_false_with_empty_result_refuses_the_wallet():
    """Area 1. `success=false` + `result=[]` must not become an empty wallet."""
    client, rec = _client(lambda r: _unsuccessful(result=[]))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
    assert len(rec.requests) == 1  # request() has no retries
    await client.close()


@pytest.mark.asyncio
async def test_a02_false_with_absent_result_refuses_the_wallet():
    """Area 2. Absence of `result` must not read as emptiness either."""
    client, rec = _client(
        lambda r: httpx.Response(200, json={"success": False}))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
    assert len(rec.requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_a03_a_missing_success_key_fails_closed():
    """Area 3. An envelope that never says it worked has not said it worked."""
    client, _ = _client(
        lambda r: httpx.Response(200, json={"result": [_wallet_json()]}))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
    await client.close()


@pytest.mark.asyncio
async def test_a04_false_positions_never_read_as_a_flat_exchange():
    """Area 4. The worst fabrication: a false envelope must not mean 'flat'."""
    client, _ = _client(lambda r: _unsuccessful(result=[]))
    with pytest.raises(DeltaResponseError):
        await client.get_positions()
    await client.close()


@pytest.mark.asyncio
async def test_a05_false_open_orders_never_read_as_no_working_orders():
    """Area 5. A resting stop must not be reported as missing protection."""
    client, _ = _client(lambda r: _unsuccessful(result=[]))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    await client.close()


@pytest.mark.asyncio
async def test_a06_false_wallet_refuses_before_zero_equity_is_fabricated():
    """Area 6. `get_account_summary` must raise, not sum an empty list to 0."""
    client, _ = _client(lambda r: _unsuccessful(result=[]))
    with pytest.raises(DeltaResponseError):
        await client.get_account_summary()
    await client.close()


@pytest.mark.asyncio
async def test_a07_a_successful_but_empty_wallet_still_parses_normally():
    """Control: §O9 changes nothing about a genuinely successful response."""
    client, _ = _client(lambda r: _ok([]))
    balances = await client.get_wallet_balances()
    assert balances == []
    await client.close()


@pytest.mark.asyncio
async def test_a08_a_healthy_envelope_is_unaffected_end_to_end():
    """Control: a well-formed success envelope reaches the row parsers intact."""
    client, _ = _client(lambda r: _ok([_wallet_json()]))
    summary = await client.get_account_summary()
    assert summary.total_equity == Decimal("10000.12345678")
    assert summary.user_id == 42
    await client.close()


# ══ B. The order paths: a populated `result` cannot rescue a false envelope ════
#
# Areas 7-10: false+populated order result on POST, false individual-order
# GET, successful client-order lookup with result=null stays None, unsuccessful
# client-order lookup raises.


def _entry_request() -> DeltaOrderRequest:
    return DeltaOrderRequest(
        product_id=BTCUSD_PRODUCT_ID,
        product_symbol=BTCUSD,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("3"),
        limit_price=Decimal("94500.00"),
        client_order_id="QE-O9-ENTRY-1",
    )


@pytest.mark.asyncio
async def test_b09_false_with_a_populated_order_result_is_not_a_placed_order():
    """Area 7. A rejected order carrying a full `result` must not parse as live.

    This is the class-2 hole §O9 closes: `create_order` only guarded against a
    falsy/empty `result`, so a `success=false` envelope WITH a well-formed order
    object would have been adopted as a placed order.
    """
    client, rec = _client(
        lambda r: _unsuccessful(result=_order_json(state="cancelled")))
    with pytest.raises(DeltaResponseError):
        await client.create_order(_entry_request())
    assert rec.methods() == ["POST"]  # sent once, then the response is refused
    await client.close()


@pytest.mark.asyncio
async def test_b10_place_order_is_the_same_boundary_as_create_order():
    """`place_order` is an alias; the guard sits below both."""
    client, _ = _client(
        lambda r: _unsuccessful(result=_order_json(state="cancelled")))
    with pytest.raises(DeltaResponseError):
        await client.place_order(_entry_request())
    await client.close()


@pytest.mark.asyncio
async def test_b11_false_individual_order_response_refuses():
    """Area 8. `GET /v2/orders/{id}` under a false envelope must raise."""
    client, _ = _client(
        lambda r: _unsuccessful(result=_order_json()))
    with pytest.raises(DeltaResponseError):
        await client.get_order(VALID_ORDER_ID)
    await client.close()


@pytest.mark.asyncio
async def test_b12_successful_client_lookup_with_null_result_stays_none():
    """Area 9. A *successful* 'no such order' answer must still be None.

    §O9 must not over-correct: `success=true` + `result=null` is the documented
    'not found', and `_resolve_entry_order` (§M4 case C) relies on None meaning
    exactly that. Turning this into a raise would break a legitimate lookup.
    """
    client, _ = _client(
        lambda r: httpx.Response(200, json={"success": True, "result": None}))
    found = await client.get_order_by_client_id("QE-O9-NOSUCH")
    assert found is None
    await client.close()


@pytest.mark.asyncio
async def test_b13_unsuccessful_client_lookup_raises_not_returns_none():
    """Area 10. A FAILED lookup is different from a 'no such order' answer."""
    client, _ = _client(lambda r: _unsuccessful(result=None))
    with pytest.raises(DeltaResponseError):
        await client.get_order_by_client_id("QE-O9-0001")
    await client.close()


@pytest.mark.asyncio
async def test_b14_a_valid_placement_still_returns_its_identity():
    """Control: the healthy order path is untouched by the guard."""
    client, _ = _client(lambda r: _ok(_order_json()))
    resp = await client.create_order(_entry_request())
    assert resp.id == VALID_ORDER_ID
    await client.close()


# ══ C. The HTTP status ladder keeps running AHEAD of the envelope guard ════════
#
# Area 15. §O9 sits AFTER the status ladder (delta_client.py request()), so a
# transport-level failure keeps its own exception type. Each of these carries a
# `success=false` body as well: if the envelope guard ran first it would raise
# the generic `DeltaResponseError`; because the ladder is ahead, the SPECIFIC
# type wins. `DeltaResponseError` is a *sibling* of these (all subclass
# `DeltaClientError`), so `pytest.raises(DeltaAuthError)` genuinely proves the
# ladder fired -- the envelope guard could not have produced it.


@pytest.mark.asyncio
async def test_c15_http_401_stays_auth_error_not_the_envelope_guard():
    """401 -> DeltaAuthError, even with a false envelope in the body."""
    client, _ = _client(lambda r: httpx.Response(401, json={"success": False}))
    with pytest.raises(DeltaAuthError) as exc:
        await client.get_wallet_balances()
    assert not isinstance(exc.value, DeltaResponseError)
    await client.close()


@pytest.mark.asyncio
async def test_c16_http_429_stays_rate_limit_not_the_envelope_guard():
    """429 -> DeltaRateLimitError, ahead of the guard."""
    client, _ = _client(lambda r: httpx.Response(
        429, headers={"Retry-After": "3"}, json={"success": False}))
    with pytest.raises(DeltaRateLimitError) as exc:
        await client.get_positions()
    assert not isinstance(exc.value, DeltaResponseError)
    await client.close()


@pytest.mark.asyncio
async def test_c17_http_400_stays_order_rejected_not_the_envelope_guard():
    """400 -> DeltaOrderRejectedError, ahead of the guard."""
    client, _ = _client(lambda r: httpx.Response(
        400, json={"success": False, "error": {"message": "bad request"}}))
    with pytest.raises(DeltaOrderRejectedError) as exc:
        await client.get_open_orders()
    assert not isinstance(exc.value, DeltaResponseError)
    await client.close()


@pytest.mark.asyncio
async def test_c18_http_500_stays_connection_error_not_the_envelope_guard():
    """5xx -> DeltaConnectionError, ahead of the guard."""
    client, _ = _client(lambda r: httpx.Response(500, text="upstream boom"))
    with pytest.raises(DeltaConnectionError) as exc:
        await client.get_wallet_balances()
    assert not isinstance(exc.value, DeltaResponseError)
    await client.close()


# ══ D. A non-dict HTTP-200 body fails closed, it does not AttributeError ═══════
#
# Area 16. Before §O9 a JSON body that decoded to a list/str/None reached
# `data.get(...)` and blew up with an AttributeError -- an accidental refusal
# that depends on the endpoint happening to call `.get`. The `isinstance(data,
# dict)` arm makes the refusal deliberate and uniform, with a real message.


@pytest.mark.asyncio
async def test_d19_a_bare_list_body_is_refused_not_attributeerror():
    """`[...]` at the top level is not the documented envelope."""
    client, _ = _client(lambda r: httpx.Response(200, json=[_wallet_json()]))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
    await client.close()


@pytest.mark.asyncio
async def test_d20_a_string_body_is_refused_not_attributeerror():
    """A bare JSON string is not the documented envelope."""
    client, _ = _client(lambda r: httpx.Response(200, json="ok"))
    with pytest.raises(DeltaResponseError):
        await client.get_positions()
    await client.close()


@pytest.mark.asyncio
async def test_d21_a_null_body_is_refused_not_attributeerror():
    """A bare JSON null is not the documented envelope."""
    client, _ = _client(lambda r: httpx.Response(200, json=None))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    await client.close()


@pytest.mark.asyncio
async def test_d22_get_ticker_pre_existing_success_check_is_untouched():
    """The precedent §O9 mirrors still refuses a falsy `success` on this host."""
    client, _ = _client(lambda r: httpx.Response(
        200, json={"success": False, "result": {"symbol": BTCUSD}}))
    with pytest.raises(DeltaResponseError):
        await client.get_ticker(BTCUSD)
    await client.close()


# ══ F. Static invariants: the guard is where the contract says, and only there ═
#
# Area 17. These read the source, not the behaviour, so they pin the SHAPE of
# the fix: inline in request(), after CONNECTED, reusing the existing exception,
# adding no new type, and -- for this suite itself -- never touching the network.

_DELTA_CLIENT_SRC = (PRODUCTION_ROOT / "execution" / "delta_client.py").read_text(
    encoding="utf-8")
_REQUEST_SRC = inspect.getsource(DeltaIndiaClient.request)


def test_f23_the_o9_guard_lives_inline_in_request():
    """The dict-check, the truthy-success check and sanitize_text are in request()."""
    assert "isinstance(data, dict)" in _REQUEST_SRC
    assert 'data.get("success", False)' in _REQUEST_SRC
    assert "sanitize_text(" in _REQUEST_SRC
    assert "was unsuccessful" in _REQUEST_SRC
    assert "success/result envelope" in _REQUEST_SRC
    # No salvage: `result` is never read inside the guard's own reasoning.
    guard = _REQUEST_SRC[_REQUEST_SRC.index("isinstance(data, dict)"):]
    assert 'data.get("result"' not in guard


def test_f24_no_new_exception_type_was_introduced():
    """§O9 reuses DeltaResponseError; the exception taxonomy is unchanged."""
    tree = ast.parse(_DELTA_CLIENT_SRC)
    exc_classes = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(b, ast.Name) and b.id.startswith("Delta")
            or isinstance(b, ast.Name) and b.id == "Exception"
            for b in node.bases
        )
    }
    assert exc_classes == {
        "DeltaClientError", "DeltaAuthError", "DeltaRateLimitError",
        "DeltaOrderRejectedError", "DeltaConnectionError", "DeltaResponseError",
        "DeltaExecutionAuthorityError",
    }


def test_f25_connected_is_set_before_the_guard_and_never_reset_by_it():
    """Directive #5: the transport succeeded, so CONNECTED stands under the guard."""
    connected = "self._connection_state = ConnectionState.CONNECTED"
    guard = "if not isinstance(data, dict):"
    assert connected in _REQUEST_SRC and guard in _REQUEST_SRC
    assert _REQUEST_SRC.index(connected) < _REQUEST_SRC.index(guard)
    # From the guard onward, _connection_state is never reassigned.
    tail = _REQUEST_SRC[_REQUEST_SRC.index(guard):]
    assert "self._connection_state =" not in tail


def test_f26_this_suite_is_offline_every_client_is_mocktransport_backed():
    """Zero network access: no AsyncClient in this file omits a MockTransport."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    async_clients = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "AsyncClient"
    ]
    assert async_clients, "expected at least one httpx.AsyncClient construction"
    for call in async_clients:
        transport = next(
            (kw.value for kw in call.keywords if kw.arg == "transport"), None)
        assert transport is not None, "an AsyncClient was built without a transport"
        assert (
            isinstance(transport, ast.Call)
            and isinstance(transport.func, ast.Attribute)
            and transport.func.attr == "MockTransport"
        ), "an AsyncClient uses a transport other than httpx.MockTransport"


# ══ G. Deferral pins: what §O9 deliberately did NOT touch ══════════════════════
#
# Area 18. §O9 is one envelope guard and nothing more. These two facts are OWNED
# by other tasks; they are pinned here only so a future reader does not mistake
# their survival for a §O9 oversight and "fix" them under this task's name.


@pytest.mark.asyncio
async def test_g27_o4_owns_get_fills_non_list_result_still_returns_empty():
    """A SUCCESSFUL envelope with a non-list `result` still yields [] (§O4, not §O9).

    §O9 guards the envelope, not the row shape. `get_fills` collapsing a non-list
    `result` to [] under `success=true` is deferred §O4 work and stays as-is; the
    envelope guard never reaches it because the envelope here is well-formed.
    """
    client, _ = _client(lambda r: _ok({"unexpected": "object, not a list"}))
    fills = await client.get_fills(order_id=VALID_ORDER_ID)
    assert fills == []
    await client.close()


def test_g28_i2_owns_the_ws_fill_order_id_fallback_untouched():
    """The WS `_normalize_fill` empty-string order_id fallback is still present (I2)."""
    ws_src = (PRODUCTION_ROOT / "execution" / "private_websocket.py").read_text(
        encoding="utf-8")
    assert 'order_id = str(data.get("order_id", ""))' in ws_src


# ══ E. Consumer proofs: the four dangerous downstream paths, end to end ════════
#
# Areas 11-14. §A-§D prove the guard raises at the boundary. §E proves the
# consumers that read a FLAT/ZERO exchange as a safety fact never see a
# fabricated one, because the raise reaches them. Each drives the REAL
# `DeltaIndiaClient.request()` through MockTransport -- a MagicMock would bypass
# the very guard under test.


def _local_position() -> PositionRecord:
    """An OPEN local position that must SURVIVE a failed sync (never auto-closed)."""
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


def _store() -> LocalStateStore:
    """A CONNECTED store carrying local truth the fabrication would overwrite."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.current_balance = Decimal("10000.00")
    store.account.margin_used = Decimal("250.00")
    store.account.algo_enabled = True
    store.connection.connection_status = "CONNECTED"
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


# ── E1 (area 11): synchronizer never overwrites local truth with a fabrication ─


@pytest.mark.asyncio
async def test_e29_a_false_wallet_envelope_fails_the_whole_sync_closed():
    """A false wallet envelope must not zero the account or flatten positions.

    `_do_sync` fetches the account summary FIRST, so the guard raises before any
    reconcile step runs: the local OPEN position survives, nothing is moved to
    history, and the cycle is audited as a failure with the link marked ERROR.
    """
    store = _store()
    store.positions[BTCUSD] = _local_position()
    client, rec = _client(lambda r: _unsuccessful(result=[]))
    service = LiveAccountSyncService(client=client, state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert result.success is False
    assert result.error
    assert result.positions_synced == 0 and result.orders_synced == 0
    # The single most dangerous fabrication -- a false flat -- did not happen:
    assert BTCUSD in store.positions
    assert store.positions[BTCUSD].status is PositionStatus.OPEN
    assert store.position_history == []
    assert store.connection.connection_status == "ERROR"
    assert [e["action"] for e in store.audit_events][-1] == "SYNC_FAILED"
    assert len(rec.requests) == 1  # failed on the first (wallet) call, no retry
    await client.close()


# ── E2 (area 12): a false envelope must NOT force-release the single-trade lock ─


@pytest.mark.asyncio
async def test_e30_reconciliation_keeps_the_lock_when_the_exchange_is_unknown():
    """A held lock stays held: UNKNOWN is not the 'flat exchange' that releases it.

    `reconcile_account` reads the wallet first; the false envelope raises there,
    so it returns the fail-closed EXCHANGE_UNREACHABLE report and never reaches
    the auto-resolve `release_lock`. Even with `auto_resolve=True`, the lock the
    account genuinely holds is retained (safety rules #11/#14).
    """
    store = _store()
    lock = SingleTradeLockManager()
    lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP,
        symbol=BTCUSD, allow_replay=False,
    )
    assert lock.is_locked(USER, ACCOUNT)[0] is True

    client, _ = _client(lambda r: _unsuccessful(result=[]))
    service = DeltaReconciliationService(
        client=client, state_store=store, single_trade_lock=lock)

    report = await service.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)

    assert report.is_synchronized is False
    assert report.actions_taken == ["EXCHANGE_UNREACHABLE_FAIL_CLOSED"]
    assert not any(a.startswith("RELEASED_ORPHANED_LOCK_") for a in report.actions_taken)
    # The lock the account really holds is still held:
    held, active_setup, _ = lock.is_locked(USER, ACCOUNT)
    assert held is True
    assert active_setup == SETUP
    await client.close()


# ── E3 (area 13): the multi-user path places NO order on a false exchange read ─


@pytest.mark.asyncio
async def test_e31_muo_places_no_order_when_positions_read_is_unsuccessful():
    """A false positions envelope (step 5) must stop the trade BEFORE the POST.

    The proven safety property is 'no order was placed': the guard raises inside
    `get_positions`, the catch-all handles it, and because the entry POST was
    never attempted the lock is correctly released -- nothing can be resting.
    """

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/wallet/balances":
            return _ok([_wallet_json()])
        if request.url.path == "/v2/positions/margined":
            return _unsuccessful(result=[])
        raise AssertionError(f"unexpected request to {request.url.path}")

    client, rec = _client(responder)
    config = UserAccountConfig(
        user_id=USER, account_id=ACCOUNT,
        is_active=True, algo_enabled=True, kill_switch_active=False,
        api_key="TEST_KEY_O9", api_secret="TEST_SECRET_O9",
        client_factory=lambda api_key, api_secret: client,
    )
    session = UserExecutionSession(config, SingleTradeLockManager(), CapitalAllocator())

    result = await session.execute_trade(
        setup_id=SETUP, symbol=BTCUSD, direction=TradeDirection.LONG,
        planned_entry_price=ENTRY_PRICE,
        stop_loss_price=Decimal("94000.00"),
        take_profit_price=Decimal("98000.00"),
    )

    assert result.status != "EXECUTED"
    assert result.status == "ERROR"
    assert result.entry_order_id is None
    # The order path was never reached: no POST ever left the client.
    assert all(r.method != "POST" for r in rec.requests)
    assert "/v2/positions/margined" in rec.paths()
    # execute_trade closes the client in its own finally; nothing to close here.


# ── E4 (area 14): a false entry-order envelope raises ENTRY_STATE_UNKNOWN ──────


@pytest.mark.asyncio
async def test_e32_entry_refresh_alerts_unknown_and_never_backfills_on_failure():
    """A false `get_order` envelope must not converge the entry to a guessed state.

    `refresh_entry_from_exchange` resolves the entry via `get_order`; the guard
    raises inside it, so the method raises ENTRY_STATE_UNKNOWN, returns None,
    leaves the lifecycle state untouched, and adopts nothing from the refused body.
    """
    store = _store()
    client, rec = _client(lambda r: _unsuccessful(result=_order_json()))
    manager = _manager(client, store)
    record = _record(entry_order_id=str(VALID_ORDER_ID))
    manager._active_trades[SETUP] = record

    status = await manager.refresh_entry_from_exchange(SETUP, alert_on_failure=True)

    assert status is None
    assert record.state is TradeLifecycleState.ENTRY_PENDING  # not advanced
    assert record.entry_order_id == str(VALID_ORDER_ID)  # not corrupted/backfilled
    alerts = manager.reconciliation_alerts
    assert len(alerts) == 1
    assert alerts[0]["code"] == "ENTRY_STATE_UNKNOWN"
    assert alerts[0]["symbol"] == BTCUSD
    assert "RECONCILIATION_ALERT_ENTRY_STATE_UNKNOWN" in [
        e["action"] for e in store.audit_events]
    assert len(rec.requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_e33_a_healthy_entry_refresh_converges_and_raises_no_alert():
    """Control: a successful snapshot converges normally, with no false alert."""
    store = _store()
    client, _ = _client(lambda r: _ok(
        _order_json(state="open", unfilled_size="3", average_fill_price=None)))
    manager = _manager(client, store)
    record = _record(entry_order_id=str(VALID_ORDER_ID))
    manager._active_trades[SETUP] = record

    status = await manager.refresh_entry_from_exchange(SETUP, alert_on_failure=True)

    assert status is not None
    assert manager.reconciliation_alerts == []
    await client.close()












