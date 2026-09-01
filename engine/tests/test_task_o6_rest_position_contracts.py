"""
Task O §O6 -- the REST position snapshot and order-state parse contracts.

Task O found that `GET /v2/positions/margined` and the two parses behind it
could answer a question the exchange never answered, and always in the same
direction: *nothing is open*.

  * The `product_ids` filter was serialized with no validation at all, so a
    float, a string, a bool, a zero, a negative or an over-long list became a
    query about instruments this engine did not mean to ask about. An ignored
    or truncated filter comes back as a confident, wrong answer.
  * An HTTP 404 was answered by silently retrying `/v2/positions` -- a
    different, undocumented endpoint with a different response shape -- and the
    result was then trusted as authoritative position state.
  * A non-list `result` was reported as `[]`, so a malformed envelope became an
    empty exchange.
  * `DeltaPosition.from_dict` defaulted an absent `size` to `"0"`, and
    `get_positions` filters on `size > 0`, so a position the exchange described
    without a size was deleted from the snapshot entirely.
  * `DeltaOrderResponse.from_dict` defaulted an absent `state` to `"OPEN"`, so
    an order the exchange never described was adopted as still resting.

False flatness is the sharpest hazard in this engine, which is why sections D
through G exist. Four consumers act on an empty snapshot: the synchronizer
CLOSES every local position missing from it, the trade lifecycle CLEARS
blocking reconciliation alerts on a clean run, reconciliation force-releases
the single-trade lock when the exchange "looks flat", and the multi-user
pre-trade gate AUTHORIZES a new order when it sees no exposure. Each of those
is a real action taken on a fabricated observation.

So absence is REFUSED where it decides open-versus-flat (`size`) or
resting-versus-terminal (`state`), and reported as `None` where it is merely
unobserved (the five optional numerics). An observed zero stays a distinct,
visible fact -- section E pins both halves, because a fix that turned a real
zero into `None` would lose an observation the exchange actually made.

The ten-value `product_ids` cap is documented evidence for `GET /v2/orders`
only (§O4). It is applied here as an inherited bound, NOT as a verified
`/v2/positions/margined` contract; see `_MAX_PRODUCT_IDS`.

Zero network access: every request is served by `httpx.MockTransport` and every
payload is a literal dict. No credentials, no order placement, no governance
change.
"""

import ast
import inspect
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import quantedge.execution.delta_client as delta_client_mod
from quantedge.execution.backend_client import BackendClient
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaAuthError,
    DeltaClientError,
    DeltaConnectionError,
    DeltaIndiaClient,
    DeltaRateLimitError,
    DeltaResponseError,
    _MAX_PRODUCT_IDS,
    _validated_product_ids,
)
from quantedge.execution.models import (
    DeltaAccountSummary,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    OrderStatus,
    PositionSide,
    UnknownOrderStateError,
    optional_decimal,
)
from quantedge.execution.multi_user_orchestrator import (
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.private_websocket import (
    AUDIT_WS_SEQUENCE_GAP,
    DeltaPositionEvent,
    DeltaPrivateWebSocketClient,
    EventValidator,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    LiveAccountSyncService,
    LocalStateStore,
    PositionRecord,
    PositionStatus,
    SyncResult,
)
from quantedge.execution.trade_lifecycle import TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments import UnknownInstrumentError

ACCOUNT = "acc_task_o6"
USER = "user_task_o6"
SETUP = "BTCUSD_1h_MANUAL_SMC_O6_LONG"
BTCUSD_PRODUCT_ID = 27
ETHUSD_PRODUCT_ID = 3136
SOLUSD_PRODUCT_ID = 14823

#: The five numerics whose absence is UNOBSERVED, not zero. `size` is not one
#: of them: it decides open-versus-flat, so its absence is refused instead.
OPTIONAL_NUMERIC_KEYS = ("entry_price", "mark_price", "unrealised_pnl",
                         "leverage", "margin")

#: Field name on `DeltaPosition` for each raw payload key above.
OPTIONAL_NUMERIC_FIELDS = {
    "entry_price": "entry_price",
    "mark_price": "mark_price",
    "unrealised_pnl": "unrealized_pnl",
    "leverage": "leverage",
    "margin": "margin",
}


# ── Transport plumbing (identical in shape to the §O4 harness) ────────────────


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

    @property
    def paths(self) -> List[str]:
        return [r.url.path for r in self.requests]


def _client(responder) -> tuple:
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O6_000000001",
        api_secret="TEST_SECRET_TASK_O6_00000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder

def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _position_json(**over) -> Dict[str, Any]:
    """A well-formed margined-position entry; overrides drive each case."""
    payload = {
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "size": "3",
        "entry_price": "95000.0",
        "mark_price": "95500.0",
        "liquidation_price": "88000.0",
        "unrealised_pnl": "1.50",
        "realised_pnl": "0.00",
        "leverage": "10",
        "margin": "28.65",
    }
    payload.update(over)
    return payload


def _order_json(order_id: int = 6001, **over) -> Dict[str, Any]:
    payload = {
        "id": order_id,
        "client_order_id": "QE-O6-0001",
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "user_id": 1,
        "side": "buy",
        "order_type": "limit_order",
        "size": "2",
        "unfilled_size": "2",
        "limit_price": "94500.00",
        "stop_price": None,
        "state": "open",
        "reduce_only": False,
        "created_at": 1724261234000000,
    }
    payload.update(over)
    return payload


def _without(payload: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    """`payload` with `keys` absent -- absent, not blank, not None."""
    return {k: v for k, v in payload.items() if k not in keys}


def _code(func) -> str:
    """Return `func`'s body as CODE ONLY -- no comments, no docstring.

    The §O6 explanatory comments quote the very defaults they removed (`"0"`,
    `"1"`, `"OPEN"`, `/v2/positions`), so a raw-source search would match the
    comment that documents the fix. `ast.unparse` normalizes string literals to
    single quotes, which the assertions below account for.
    """
    target = getattr(func, "__func__", func)
    tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
    node = tree.body[0]
    body = getattr(node, "body", [])
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        node.body = body[1:]
    return ast.unparse(tree)

# ══ A. `product_ids` request construction ═════════════════════════════════════


@pytest.mark.asyncio
async def test_a01_exactly_ten_product_ids_are_accepted():
    """The cap is a bound, not an off-by-one refusal of the legal maximum."""
    client, rec = _client(lambda r: _ok([]))
    ids = list(range(1, 11))
    assert await client.get_positions(product_ids=ids) == []

    assert len(rec.requests) == 1
    assert rec.last.url.params["product_ids"] == ",".join(str(i) for i in ids)


@pytest.mark.asyncio
async def test_a02_an_eleventh_product_id_is_refused_locally():
    """An over-long filter would be truncated into an answer about the wrong
    instruments, so it is refused before it can be asked."""
    client, rec = _client(lambda r: _ok([]))
    with pytest.raises(DeltaResponseError) as exc:
        await client.get_positions(product_ids=list(range(1, 12)))

    assert rec.requests == []
    assert "at most 10" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", (2.5, "27", True, None, -1, 0, Decimal("27"),
                                 b"27", [27], 27.0))
async def test_a03_a_malformed_product_id_is_refused_without_a_request(bad):
    """No coercion. Rule #8 forbids guessing a product id, and `int(2.5)` is a
    guess; `True` is an `int` subclass that means 1; `Decimal("27")` and `27.0`
    are exact but are still not the documented integer type."""
    client, rec = _client(lambda r: _ok([]))
    with pytest.raises(DeltaResponseError):
        await client.get_positions(product_ids=[BTCUSD_PRODUCT_ID, bad])

    assert rec.requests == [], "a malformed filter reached the network"


@pytest.mark.asyncio
async def test_a04_the_query_parameter_is_the_plural_documented_name():
    client, rec = _client(lambda r: _ok([]))
    await client.get_positions(product_ids=[BTCUSD_PRODUCT_ID])

    params = dict(rec.last.url.params)
    assert "product_ids" in params
    assert "product_id" not in params

@pytest.mark.asyncio
async def test_a05_the_csv_is_exact_and_ordered():
    client, rec = _client(lambda r: _ok([]))
    await client.get_positions(
        product_ids=[BTCUSD_PRODUCT_ID, ETHUSD_PRODUCT_ID, SOLUSD_PRODUCT_ID])

    assert rec.last.url.params["product_ids"] == "27,3136,14823"


@pytest.mark.asyncio
async def test_a06_none_omits_the_filter_entirely():
    """The unfiltered snapshot is the production call: all five callers pass
    nothing at all, so `None` must not become an empty filter."""
    client, rec = _client(lambda r: _ok([]))
    await client.get_positions()
    await client.get_positions(product_ids=None)

    for req in rec.requests:
        assert "product_ids" not in dict(req.url.params)


@pytest.mark.asyncio
async def test_a07_an_empty_list_omits_the_filter_rather_than_asking_for_nothing():
    client, rec = _client(lambda r: _ok([]))
    await client.get_positions(product_ids=[])

    assert len(rec.requests) == 1
    assert "product_ids" not in dict(rec.last.url.params)


@pytest.mark.asyncio
async def test_a08_a_single_valid_id_is_serialized_without_a_separator():
    client, rec = _client(lambda r: _ok([_position_json()]))
    positions = await client.get_positions(product_ids=[BTCUSD_PRODUCT_ID])

    assert rec.last.url.params["product_ids"] == "27"
    assert [p.product_symbol for p in positions] == ["BTCUSD"]


def test_a09_the_public_signature_still_exposes_product_ids_defaulting_to_none():
    sig = inspect.signature(DeltaIndiaClient.get_positions)
    assert "product_ids" in sig.parameters
    assert sig.parameters["product_ids"].default is None


def test_a10_the_helper_refuses_the_same_inputs_directly():
    """The helper is the single implementation of this rule, so it is pinned
    directly as well as through both of its callers."""
    assert _validated_product_ids([27, 3136], "GET /v2/x") == [27, 3136]
    assert _validated_product_ids([], "GET /v2/x") == []
    for bad in ([True], [1.0], ["1"], [0], [-1], [None], [Decimal("1")]):
        with pytest.raises(DeltaResponseError):
            _validated_product_ids(bad, "GET /v2/x")

# ══ B. The endpoint, and the removed 404 fallback ══════════════════════════════


@pytest.mark.asyncio
async def test_b11_the_documented_margined_endpoint_is_the_one_that_is_called():
    client, rec = _client(lambda r: _ok([_position_json()]))
    await client.get_positions()

    assert rec.paths == ["/v2/positions/margined"]


@pytest.mark.asyncio
async def test_b12_a_404_propagates_instead_of_becoming_a_second_opinion():
    """§O6 C2. A 404 was answered by retrying `/v2/positions`, an undocumented
    endpoint with a different response shape, and the reply was then trusted as
    authoritative position state -- so a routing or permission failure became
    "the account is flat"."""
    client, rec = _client(
        lambda r: httpx.Response(404, json={"success": False}))

    with pytest.raises(DeltaClientError) as exc:
        await client.get_positions()

    assert exc.value.status_code == 404
    assert not isinstance(exc.value, DeltaResponseError)


@pytest.mark.asyncio
async def test_b13_the_404_costs_exactly_one_request_to_one_endpoint():
    client, rec = _client(
        lambda r: httpx.Response(404, json={"success": False}))
    with pytest.raises(DeltaClientError):
        await client.get_positions()

    assert rec.paths == ["/v2/positions/margined"]
    assert "/v2/positions" not in rec.paths


@pytest.mark.asyncio
async def test_b14_no_empty_snapshot_is_fabricated_from_a_404():
    """The dangerous half of the old fallback: a caller received `[]`."""
    client, _ = _client(lambda r: httpx.Response(404, json={"success": False}))
    with pytest.raises(DeltaClientError) as exc:
        await client.get_positions()
    assert exc.value.status_code == 404

@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", (
    (401, DeltaAuthError),
    (429, DeltaRateLimitError),
    (500, DeltaConnectionError),
    (503, DeltaConnectionError),
))
async def test_b15_the_existing_status_mappings_are_unchanged(status, expected):
    """Removing the 404 branch must not have disturbed its neighbours."""
    client, rec = _client(lambda r: httpx.Response(status, json={"e": 1}))
    with pytest.raises(expected):
        await client.get_positions()
    assert rec.paths == ["/v2/positions/margined"]


# ══ C. Malformed result envelopes fail closed ══════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("result", (
    {"positions": []}, "none", None, 0, 7, True, "[]",
))
async def test_c16_a_non_list_positions_envelope_raises(result):
    """§O6 C3. `return []` reported a malformed envelope as an empty exchange.
    This mirrors `get_wallet_balances`, which has always raised here."""
    client, _ = _client(lambda r: _ok(result))
    with pytest.raises(DeltaResponseError):
        await client.get_positions()


@pytest.mark.asyncio
async def test_c17_a_genuinely_empty_list_is_still_a_legitimate_answer():
    """The refusal must not turn a real, observed flat account into an error."""
    client, _ = _client(lambda r: _ok([]))
    assert await client.get_positions() == []


@pytest.mark.asyncio
async def test_c18_a_missing_result_key_is_still_the_documented_empty_list():
    client, _ = _client(
        lambda r: httpx.Response(200, json={"success": True}))
    assert await client.get_positions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ({"orders": []}, "none", None, 7, 0, True,
                                   "[]"))
async def test_c19_a_non_list_open_orders_envelope_also_raises(result):
    """
    The orders half of C3, now implemented (the strict `xfail` that held this
    contract open while it was blocked has been removed as obsolete; the
    assertion itself is unchanged and extended).

    `reconcile_account` computes `exchange_is_flat = not exchange_positions and
    not exchange_orders`, so a fabricated empty orders list carries the same
    blast radius as a fabricated empty positions list: it can force-release the
    single-trade lock and let the pre-trade gate authorize a new order. The
    matching O4 assertion at
    `test_task_o4_order_query_contracts.py::test_a_a_non_list_result_fails_closed_rather_than_reporting_no_orders`
    was strengthened in place to the same contract.
    """
    client, rec = _client(lambda r: _ok(result))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders()
    # The refusal is the envelope's, not a parse error leaking from an item, so
    # the request was made and answered -- exactly one of them.
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_c20_an_empty_open_orders_list_is_still_a_legitimate_answer():
    """The other half of the contract above: a real empty list is an answer."""
    client, _ = _client(lambda r: _ok([]))
    assert await client.get_open_orders() == []


@pytest.mark.asyncio
async def test_c20b_a_missing_orders_result_key_is_still_the_empty_list():
    """Unchanged from §O4: absent `result` is the documented empty collection."""
    client, _ = _client(
        lambda r: httpx.Response(200, json={"success": True}))
    assert await client.get_open_orders() == []


# ══ D. An absent position `size` is refused; the five numerics are optional ════


def test_d21_a_missing_size_is_refused_at_the_parse_boundary():
    """§O6 C4. `Decimal(str(data.get("size", "0")))` turned an entry with no
    size into a flat LONG, and `get_positions` filters on `size > 0`, so the row
    vanished from the snapshot altogether."""
    with pytest.raises(DeltaResponseError) as exc:
        DeltaPosition.from_dict(_without(_position_json(), "size"))
    assert "size" in str(exc.value)


@pytest.mark.parametrize("raw", (None, "", " ", "   ", "\t", "\n", " \t\n "))
def test_d22_a_blank_or_none_size_is_refused_too(raw):
    """A present-but-empty size is no more an observation than an absent one."""
    with pytest.raises(DeltaResponseError):
        DeltaPosition.from_dict(_position_json(size=raw))


@pytest.mark.asyncio
async def test_d23_no_position_materializes_from_a_sizeless_entry():
    """End to end: the whole call fails closed rather than returning a shorter
    list with the sizeless row quietly dropped."""
    client, _ = _client(lambda r: _ok([
        _position_json(),
        _without(_position_json(product_id=ETHUSD_PRODUCT_ID,
                                product_symbol="ETHUSD"), "size"),
    ]))
    with pytest.raises(DeltaResponseError):
        await client.get_positions()


@pytest.mark.parametrize("key", OPTIONAL_NUMERIC_KEYS)
def test_d24_each_optional_numeric_is_none_when_absent(key):
    pos = DeltaPosition.from_dict(_without(_position_json(), key))
    assert getattr(pos, OPTIONAL_NUMERIC_FIELDS[key]) is None


def test_d25_all_five_can_be_absent_at_once_and_the_position_still_parses():
    """Absence of the optional numerics is not itself an error: the position is
    still identified, and its size is still an observation."""
    pos = DeltaPosition.from_dict(
        _without(_position_json(), *OPTIONAL_NUMERIC_KEYS))

    assert pos.product_symbol == "BTCUSD"
    assert pos.product_id == BTCUSD_PRODUCT_ID
    assert pos.size == Decimal("3")
    assert pos.side is PositionSide.LONG
    for key in OPTIONAL_NUMERIC_KEYS:
        assert getattr(pos, OPTIONAL_NUMERIC_FIELDS[key]) is None

def test_d26_present_values_keep_their_exact_decimal_precision():
    """The refusal to fabricate must not disturb parsing of what IS reported."""
    pos = DeltaPosition.from_dict(_position_json(
        size="3.00000001",
        entry_price="95000.12345678",
        mark_price="95500.87654321",
        unrealised_pnl="-1.50000009",
        leverage="12.5",
        margin="28.650000001",
    ))
    assert pos.size == Decimal("3.00000001")
    assert pos.entry_price == Decimal("95000.12345678")
    assert pos.mark_price == Decimal("95500.87654321")
    assert pos.unrealized_pnl == Decimal("-1.50000009")
    assert pos.leverage == Decimal("12.5")
    assert pos.margin == Decimal("28.650000001")
    assert str(pos.entry_price) == "95000.12345678"


def test_d27_both_pnl_spellings_are_accepted():
    british = DeltaPosition.from_dict(
        _without(_position_json(unrealised_pnl="7.77"), "unrealized_pnl"))
    assert british.unrealized_pnl == Decimal("7.77")

    american = DeltaPosition.from_dict(
        _without(_position_json(unrealized_pnl="8.88"), "unrealised_pnl"))
    assert american.unrealized_pnl == Decimal("8.88")


def test_d28_the_british_spelling_keeps_precedence_when_both_are_present():
    """`optional_decimal` returns the FIRST present key, and the call sites pass
    the British spelling first. That ordering is pinned so a later edit cannot
    silently change which of two disagreeing values is believed."""
    pos = DeltaPosition.from_dict(
        _position_json(unrealised_pnl="1.11", unrealized_pnl="2.22",
                       realised_pnl="3.33", realized_pnl="4.44"))
    assert pos.unrealized_pnl == Decimal("1.11")
    assert pos.realized_pnl == Decimal("3.33")


def test_d29_a_short_position_still_reports_an_absolute_size_and_the_side():
    pos = DeltaPosition.from_dict(_position_json(size="-4"))
    assert pos.side is PositionSide.SHORT
    assert pos.size == Decimal("4")


def test_d30_an_observed_zero_size_is_still_an_observation():
    """A reported flat position is a fact the exchange stated. Only its ABSENCE
    is refused."""
    pos = DeltaPosition.from_dict(_position_json(size="0"))
    assert pos.size == Decimal("0")
    assert pos.side is PositionSide.LONG

# ══ E. Provenance: an observed zero is distinguishable from an unobserved one ══


@pytest.mark.parametrize("key", OPTIONAL_NUMERIC_KEYS)
def test_e31_an_observed_zero_is_not_none(key):
    """The fix must not overshoot. Reporting a real zero as `None` would lose an
    observation the exchange actually made -- the mirror image of the defect."""
    pos = DeltaPosition.from_dict(_position_json(**{key: "0"}))
    assert getattr(pos, OPTIONAL_NUMERIC_FIELDS[key]) == Decimal("0")
    assert getattr(pos, OPTIONAL_NUMERIC_FIELDS[key]) is not None


@pytest.mark.parametrize("key", OPTIONAL_NUMERIC_KEYS)
def test_e32_absent_and_observed_zero_are_different_facts(key):
    field = OPTIONAL_NUMERIC_FIELDS[key]
    absent = getattr(DeltaPosition.from_dict(
        _without(_position_json(), key)), field)
    observed = getattr(DeltaPosition.from_dict(
        _position_json(**{key: "0"})), field)

    assert absent is None
    assert observed == Decimal("0")
    assert absent != observed


def test_e33_a_blank_string_is_absence_rather_than_zero():
    """A present-but-empty field states nothing, so it must not become `0`."""
    pos = DeltaPosition.from_dict(_position_json(
        **{k: "" for k in OPTIONAL_NUMERIC_KEYS}))
    for key in OPTIONAL_NUMERIC_KEYS:
        assert getattr(pos, OPTIONAL_NUMERIC_FIELDS[key]) is None


def test_e34_leverage_absence_is_not_the_old_fabricated_one_times():
    """`leverage` defaulted to `"1"`, not `"0"` -- a plausible-looking value that
    is arguably worse, because 1x is a real leverage an operator might act on."""
    pos = DeltaPosition.from_dict(_without(_position_json(), "leverage"))
    assert pos.leverage is None
    assert pos.leverage != Decimal("1")
    assert DeltaPosition.from_dict(
        _position_json(leverage="1")).leverage == Decimal("1")


def test_e35_an_absent_mark_price_is_not_silently_the_entry_price():
    """The stream parse used to fall back `mark_price -> entry_price`, which
    reports a position as marked exactly at entry -- an unchanged, break-even
    mark the engine never received."""
    pos = DeltaPosition.from_dict(_without(_position_json(), "mark_price"))
    assert pos.mark_price is None
    assert pos.entry_price == Decimal("95000.0")

# ══ F. An absent REST order state is refused ═══════════════════════════════════


def test_f36_a_missing_state_and_status_is_refused():
    """§O6 C6. `str(data.get("state", "OPEN"))` adopted an order the exchange
    never described as still resting: the bracket logic keeps waiting for a fill
    that may already have happened."""
    with pytest.raises(UnknownOrderStateError) as exc:
        DeltaOrderResponse.from_dict(_without(_order_json(), "state"))
    assert "no state and no status" in str(exc.value)


@pytest.mark.parametrize("raw", (None, "", " ", "   ", "\t", "\n"))
def test_f37_a_blank_state_with_no_status_is_refused(raw):
    with pytest.raises(UnknownOrderStateError):
        DeltaOrderResponse.from_dict(_order_json(state=raw))


@pytest.mark.parametrize("state", (None, "", "   "))
@pytest.mark.parametrize("status", (None, "", "  ", "\t"))
def test_f38_a_blank_state_and_a_blank_status_is_refused(state, status):
    with pytest.raises(UnknownOrderStateError):
        DeltaOrderResponse.from_dict(_order_json(state=state, status=status))


def test_f39_the_status_key_is_honoured_as_a_fallback():
    """Absence of `state` is only refused when `status` is absent too."""
    order = DeltaOrderResponse.from_dict(
        _without(_order_json(status="filled"), "state"))
    assert order.state is OrderStatus.FILLED

    blank = DeltaOrderResponse.from_dict(_order_json(state="  ", status="open"))
    assert blank.state is OrderStatus.OPEN


def test_f40_state_still_takes_precedence_over_status_when_both_are_present():
    order = DeltaOrderResponse.from_dict(
        _order_json(state="cancelled", status="open"))
    assert order.state is OrderStatus.CANCELLED


@pytest.mark.parametrize("raw,expected", (
    ("open", OrderStatus.OPEN),
    ("pending", OrderStatus.PENDING),
    ("closed", OrderStatus.FILLED),
    ("cancelled", OrderStatus.CANCELLED),
    ("canceled", OrderStatus.CANCELLED),
    ("filled", OrderStatus.FILLED),
    ("partially_filled", OrderStatus.PARTIALLY_FILLED),
    ("rejected", OrderStatus.REJECTED),
    ("expired", OrderStatus.EXPIRED),
    (" OPEN ", OrderStatus.OPEN),
))
def test_f41_every_state_this_engine_accepts_still_maps(raw, expected):
    assert DeltaOrderResponse.from_dict(
        _order_json(state=raw)).state is expected

@pytest.mark.parametrize("unknown", ("purged", "settled", "OPEN_PENDING",
                                     "unknown", "liquidated"))
def test_f42_an_unknown_state_value_still_raises_per_o5(unknown):
    """§O6 added absence-refusal WITHOUT relaxing §O5's value-refusal."""
    with pytest.raises(UnknownOrderStateError):
        DeltaOrderResponse.from_dict(_order_json(state=unknown))


def test_f43_an_identity_failure_still_wins_over_the_state_refusal():
    """Ordering is load-bearing. An unusable payload must be reported as the
    identity failure it is, so `UnknownInstrumentError` is raised first."""
    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict(
            _without(_order_json(product_symbol="FOOUSD"), "state"))

    with pytest.raises(UnknownInstrumentError):
        DeltaOrderResponse.from_dict({})


def test_f44_an_identity_failure_still_wins_over_the_size_refusal():
    """The same ordering on the position parse (§O6's explicit constraint)."""
    with pytest.raises(UnknownInstrumentError):
        DeltaPosition.from_dict(
            _without(_position_json(product_symbol="FOOUSD"), "size"))

    with pytest.raises(UnknownInstrumentError):
        DeltaPosition.from_dict(
            _without(_position_json(product_id=9999), "size"))

    with pytest.raises(UnknownInstrumentError):
        DeltaPosition.from_dict({})


@pytest.mark.asyncio
async def test_f45_a_stateless_order_fails_the_whole_open_orders_call():
    """`get_open_orders` feeds `reconcile_with_exchange`'s `sl_live` decision, so
    a stateless row must not be silently adopted as a resting order."""
    client, _ = _client(lambda r: _ok([_without(_order_json(), "state")]))
    with pytest.raises(UnknownOrderStateError):
        await client.get_open_orders()


@pytest.mark.asyncio
async def test_f46_well_formed_orders_still_parse_through_the_client():
    client, _ = _client(lambda r: _ok([_order_json(state="pending")]))
    orders = await client.get_open_orders()
    assert [o.state for o in orders] == [OrderStatus.PENDING]

# ══ G. The four consumers of the snapshot fail closed on a refusal ═════════════
#
# Every assertion below is about the SAME thing: a refusal must not be converted
# into "the exchange is flat". The whole point of §O6 is that these four code
# paths take irreversible action on an empty snapshot.


def _mock_exchange(*, positions_error: Exception,
                   balance: str = "1000.00") -> MagicMock:
    """A client whose balance query succeeds and whose position query refuses."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "O6_KEY"
    client._api_secret = "O6_SECRET"
    client.submitted = []
    wallet = DeltaWalletBalance(
        asset_symbol="USDT",
        balance=Decimal(balance),
        available_balance=Decimal(balance),
        position_margin=Decimal("0"),
        order_margin=Decimal("0"),
        blocked_margin=Decimal("0"),
    )
    client.get_wallet_balances = AsyncMock(return_value=[wallet])
    client.get_account_summary = AsyncMock(return_value=DeltaAccountSummary(
        user_id=1,
        balances={"USDT": wallet},
        total_equity=Decimal(balance),
        available_balance=Decimal(balance),
        margin_used=Decimal("0"),
    ))
    client.get_positions = AsyncMock(side_effect=positions_error)
    client.get_open_orders = AsyncMock(return_value=[])
    client.get_ticker = AsyncMock(return_value={"mark_price": "95000"})
    client.place_order = AsyncMock(
        side_effect=AssertionError("an order was submitted on a refused snapshot"))
    client.close = AsyncMock()
    return client


def _local_position(symbol: str = "BTCUSD") -> PositionRecord:
    return PositionRecord(
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=Decimal("3"),
        entry_price=Decimal("95000"),
        current_price=Decimal("95500"),
        unrealized_pnl=Decimal("1.50"),
        realized_pnl=None,
        leverage=Decimal("10"),
        margin_used=Decimal("28.65"),
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("error", (
    DeltaResponseError("position arrived with no size"),
    DeltaClientError("not found", status_code=404),
    UnknownOrderStateError("no state and no status"),
))
async def test_g47_the_synchronizer_does_not_close_a_local_position_on_a_refusal(error):
    """Consumer 1. `_reconcile_positions` pops and CLOSES every local symbol
    absent from the snapshot. A refusal must therefore never reach it."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.positions["BTCUSD"] = _local_position()
    service = LiveAccountSyncService(
        client=_mock_exchange(positions_error=error), state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert isinstance(result, SyncResult)
    assert result.success is False
    assert result.error
    # The local position is untouched: still present, still OPEN, not archived.
    assert "BTCUSD" in store.positions
    assert store.positions["BTCUSD"].status is PositionStatus.OPEN
    assert store.positions["BTCUSD"].closed_at is None
    assert store.position_history == []


@pytest.mark.asyncio
async def test_g48_reconciliation_reports_unreachable_and_keeps_the_lock():
    """Consumer 3, the most dangerous one: with `auto_resolve=True` a "flat"
    exchange releases the single-trade lock AND calls
    `backend_client.force_release_lock`. Rules #11/#14 make lock RETENTION the
    fail-safe direction, so a refusal must do neither."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.positions["BTCUSD"] = _local_position()
    lock = SingleTradeLockManager()
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True

    backend = MagicMock(spec=BackendClient)
    backend.force_release_lock = AsyncMock()
    service = DeltaReconciliationService(
        client=_mock_exchange(
            positions_error=DeltaResponseError("no size; refusing")),
        state_store=store,
        single_trade_lock=lock,
        backend_client=backend,
    )

    report = await service.reconcile_account(
        ACCOUNT, user_id=USER, auto_resolve=True)

    assert report.is_synchronized is False
    assert report.actions_taken == ["EXCHANGE_UNREACHABLE_FAIL_CLOSED"]

    # The lock is still held, by the same setup, and nothing was force-released.
    is_locked, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    assert is_locked is True
    assert setup_id == SETUP
    assert symbol == "BTCUSD"
    backend.force_release_lock.assert_not_called()
    assert "DELTA_RECONCILED_FLAT" not in report.actions_taken


@pytest.mark.asyncio
async def test_g49_the_trade_lifecycle_does_not_clear_blocking_alerts():
    """Consumer 2. A run that reaches the exchange and finds nothing unresolved
    CLEARS prior alerts -- the only self-healing route in the engine. A refusal
    is not "found nothing"; it is "could not find out"."""
    store = LocalStateStore(account_id=ACCOUNT)
    manager = TradeLifecycleManager(
        client=_mock_exchange(
            positions_error=DeltaResponseError("no size; refusing")),
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )
    manager._raise_reconciliation_alert(
        "PROTECTION_UNVERIFIED", ACCOUNT, "pre-existing blocking alert")
    assert len(manager.reconciliation_alerts) == 1

    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)

    assert summary["exchange_unreachable"] is True
    assert "EXCHANGE_UNREACHABLE" in summary["unresolved"]
    # The pre-existing alert survives, and a second one is raised for the refusal.
    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "PROTECTION_UNVERIFIED" in codes
    assert "EXCHANGE_UNREACHABLE" in codes
    assert not any(e["action"] == "RECONCILIATION_ALERTS_CLEARED"
                   for e in store.audit_events)


def _session(client: MagicMock) -> UserExecutionSession:
    return UserExecutionSession(
        config=UserAccountConfig(
            user_id=USER,
            account_id=ACCOUNT,
            is_active=True,
            algo_enabled=True,
            kill_switch_active=False,
            api_key="O6_KEY",
            api_secret="O6_SECRET",
            client_factory=lambda _k, _s: client,
        ),
        lock_manager=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("error", (
    DeltaResponseError("position arrived with no size; refusing"),
    DeltaClientError("not found", status_code=404),
))
async def test_g50_the_pre_trade_gate_refuses_instead_of_authorizing(error):
    """Consumer 4. "Verify Zero Existing Exposure on Exchange" AUTHORIZES a new
    order when it sees `[]`. On a refusal it must submit nothing at all."""
    client = _mock_exchange(positions_error=error)
    session = _session(client)

    result = await session.execute_trade(
        setup_id=SETUP,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=Decimal("95000"),
        stop_loss_price=Decimal("94000"),
        take_profit_price=Decimal("97000"),
        default_leverage=10,
    )

    assert result.status == "ERROR"
    assert result.error
    client.place_order.assert_not_called()
    assert client.submitted == []
    # Nothing can exist, so the lock is released rather than orphaned.
    assert session.lock_manager.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_g51_the_gate_still_refuses_on_an_observed_open_position():
    """The control case: real exposure must still block, so the fail-closed
    behaviour above is not simply "everything errors"."""
    client = _mock_exchange(positions_error=DeltaResponseError("x"))
    client.get_positions = AsyncMock(return_value=[
        DeltaPosition.from_dict(_position_json())])
    session = _session(client)

    result = await session.execute_trade(
        setup_id=SETUP,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=Decimal("95000"),
        stop_loss_price=Decimal("94000"),
        take_profit_price=Decimal("97000"),
        default_leverage=10,
    )

    assert result.status == "ERROR"
    client.place_order.assert_not_called()

# ══ H. §O1-§O5 compatibility and governance state ══════════════════════════════


def test_h52_the_optional_decimal_helper_itself_is_unchanged():
    """§O6 reused the §O2/§O3 helper; it did not edit it."""
    assert optional_decimal({}, "a") is None
    assert optional_decimal({"a": None}, "a") is None
    assert optional_decimal({"a": ""}, "a") is None
    assert optional_decimal({"a": "  "}, "a") is None
    assert optional_decimal({"a": "0"}, "a") == Decimal("0")
    assert optional_decimal({"a": "1.23456789"}, "a") == Decimal("1.23456789")
    # First PRESENT key wins, and a blank first key falls through to the second.
    assert optional_decimal({"a": "1", "b": "2"}, "a", "b") == Decimal("1")
    assert optional_decimal({"a": "", "b": "2"}, "a", "b") == Decimal("2")


def test_h53_o3_realized_pnl_absence_semantics_are_unchanged():
    """§O3: an unreported realized PnL must not look like an observed
    break-even close. §O6 widened its neighbours, not its rule."""
    pos = DeltaPosition.from_dict(
        _without(_position_json(), "realised_pnl", "realized_pnl"))
    assert pos.realized_pnl is None
    assert DeltaPosition.from_dict(
        _position_json(realised_pnl="0")).realized_pnl == Decimal("0")


@pytest.mark.asyncio
async def test_h54_o4_open_orders_still_asks_for_open_and_pending():
    """§O4: an untriggered `stop_loss_order` rests in `pending`, and
    `reconcile_with_exchange` decides `sl_live` from this list. Asking only for
    `open` reports healthy protection as missing, and the response to missing
    protection is to place the bracket again -- a duplicate live stop."""
    client, rec = _client(lambda r: _ok([_order_json()]))
    await client.get_open_orders()

    assert rec.last.url.path == "/v2/orders"
    assert rec.last.url.params["states"] == "open,pending"
    assert "state" not in dict(rec.last.url.params)


@pytest.mark.asyncio
async def test_h55_o4_order_product_ids_are_still_capped_at_ten():
    """The shared helper must not have relaxed §O4's documented cap."""
    client, rec = _client(lambda r: _ok([]))
    with pytest.raises(DeltaResponseError):
        await client.get_open_orders(product_ids=list(range(1, 12)))
    assert rec.requests == []

    await client.get_open_orders(product_ids=[BTCUSD_PRODUCT_ID] * 10)
    assert rec.last.url.params["product_ids"] == ",".join(["27"] * 10)

def _ws_client(store: LocalStateStore) -> DeltaPrivateWebSocketClient:
    sync_service = MagicMock(spec=LiveAccountSyncService)
    sync_service.sync = AsyncMock(return_value=SyncResult(
        success=True,
        synced_at=datetime.now(timezone.utc),
        account_id=store.account_id,
        equity=Decimal("1000"),
        available_balance=Decimal("1000"),
        margin_used=Decimal("0"),
        positions_synced=0,
        orders_synced=0,
    ))
    ws = DeltaPrivateWebSocketClient(
        api_key="TEST_O6_WS_KEY_0000000001",
        api_secret="TEST_O6_WS_SECRET_00000000000000001",
        state_store=store,
        sync_service=sync_service,
    )
    return ws, sync_service


def test_h56_a_ws_delete_frame_with_no_size_is_still_a_valid_closure():
    """§O6 C7 explicitly does NOT add the REST size-refusal to the stream: a
    documented `delete` frame may omit `size`, and §O5 answers closure from
    `is_closure`. Refusing here would weaken closure detection, not strengthen
    it -- the opposite of the §O6 goal."""
    event = EventValidator()._normalize_position(
        {"product_symbol": "BTCUSD"}, action="delete")

    assert isinstance(event, DeltaPositionEvent)
    assert event.size == Decimal("0")
    assert event.is_closure is True
    assert event.symbol == "BTCUSD"


def test_h57_o5_closure_detection_is_untouched():
    kwargs = dict(symbol="BTCUSD", side=PositionSide.LONG,
                  entry_price=Decimal("94000"), mark_price=Decimal("95000"),
                  liquidation_price=None, unrealized_pnl=None,
                  realized_pnl=None, margin=None, leverage=None)
    assert DeltaPositionEvent(size=Decimal("0"), **kwargs).is_closure is True
    assert DeltaPositionEvent(size=Decimal("1.5"), action="delete",
                              **kwargs).is_closure is True
    assert DeltaPositionEvent(size=Decimal("1.5"), action="update",
                              **kwargs).is_closure is False


@pytest.mark.parametrize("key,field", tuple(OPTIONAL_NUMERIC_FIELDS.items()))
def test_h58_the_stream_reports_the_same_absence_as_rest(key, field):
    """§O6 C7. The two boundaries must not disagree about the same missing fact:
    a `PositionRecord` field cannot be `None` from REST and `Decimal("0")` from
    the stream."""
    validator = EventValidator()
    absent = validator._normalize_position(
        _without(_position_json(), key, "product_id"), action="update")
    observed = validator._normalize_position(
        {**_without(_position_json(), "product_id"), key: "0"}, action="update")

    assert getattr(absent, field) is None
    assert getattr(observed, field) == Decimal("0")

@pytest.mark.asyncio
async def test_h59_o5_sequence_continuity_still_behaves_as_it_did():
    """§O5's `seq_no` handling is downstream of the parse §O6 changed, so it is
    re-pinned here: in-order advances silently, a gap audits and resynchronizes,
    and an absent `seq_no` claims nothing in either direction."""
    import json as _json

    def frame(**payload):
        base = {"product_symbol": "BTCUSD", "size": "1.5",
                "entry_price": "94000", "mark_price": "95000",
                "action": "update"}
        base.update(payload)
        return _json.dumps({"channel": "positions", "payload": base})

    store = LocalStateStore(account_id=ACCOUNT)
    client, sync_service = _ws_client(store)

    await client._handle_message(frame(seq_no=10))
    await client._handle_message(frame(seq_no=11))
    assert client.sequence_gap_count == 0
    assert client._sequence_state == {"positions": 11}
    sync_service.sync.assert_not_called()

    await client._handle_message(frame(seq_no=20))
    assert client.sequence_gap_count == 1
    assert [e for e in store.audit_events
            if e["action"] == AUDIT_WS_SEQUENCE_GAP]
    sync_service.sync.assert_awaited()

    store2 = LocalStateStore(account_id=ACCOUNT)
    client2, sync2 = _ws_client(store2)
    await client2._handle_message(frame())
    assert client2.missing_sequence_events_count == 1
    assert client2.sequence_gap_count == 0
    assert client2._sequence_state == {}
    sync2.sync.assert_not_called()


def test_h60_o5_subscription_scoping_is_unchanged():
    """§O5 removed `symbols: ["all"]`; §O6 touched no subscription logic."""
    code = _code(DeltaPrivateWebSocketClient.build_subscribe_payload)
    assert "'all'" not in code
    assert '"all"' not in code
    assert "delta_india_registry()" in code


def test_h61_governance_state_is_untouched():
    """§O6 changed no governance or authorization value."""
    from quantedge.ai.research.displacement_gated_retest_engine import (
        AI_PROMOTION_STATUS,
    )
    from quantedge.execution.synchronizer import AccountRecord
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AI_PROMOTION_STATUS == "REJECTED"

    fresh = AccountRecord(account_id=ACCOUNT)
    assert fresh.algo_enabled is False
    assert fresh.kill_switch_active is True

# ══ Static source invariants (the defects cannot quietly return) ═══════════════
#
# Structural assertions. These exist because every §O6 defect was a
# plausible-looking default that read as harmless at the call site; a behavioural
# test can be satisfied by re-adding one somewhere else.


def test_s01_get_positions_calls_the_documented_margined_endpoint():
    assert "'/v2/positions/margined'" in _code(DeltaIndiaClient.get_positions)


def test_s02_the_undocumented_positions_fallback_is_gone_from_the_code():
    """Two `/v2/positions` occurrences are expected -- the request path and the
    label handed to the validator -- and BOTH must be the margined endpoint."""
    code = _code(DeltaIndiaClient.get_positions)
    assert "'/v2/positions'" not in code
    assert code.count("/v2/positions") == code.count("/v2/positions/margined")


def test_s03_no_executable_404_branch_remains():
    code = _code(DeltaIndiaClient.get_positions)
    assert "404" not in code
    assert "status_code" not in code


def test_s04_a_malformed_positions_envelope_cannot_return_an_empty_list():
    code = _code(DeltaIndiaClient.get_positions)
    assert "return []" not in code
    assert "raise DeltaResponseError" in code


def test_s04b_a_malformed_orders_envelope_cannot_return_an_empty_list():
    """The orders half of the same rule (C3), implemented in this pass."""
    code = _code(DeltaIndiaClient.get_open_orders)
    assert "return []" not in code
    assert "raise DeltaResponseError" in code


def test_s19_the_ws_path_still_has_no_size_refusal():
    """§O6 C7, structurally. The REST/WS asymmetry is intentional: a documented
    `delete` frame may omit `size`, so the stream must keep its `'0'` size
    fallback and must NOT acquire the REST refusal. Behavioural twin: H56."""
    code = _code(EventValidator._normalize_position)
    assert "DeltaResponseError" not in code
    assert "data.get('size', '0')" in code
    # The five optional numerics still go through the shared helper, i.e. C7 is
    # in effect on this path even though the size rule is not.
    for key in OPTIONAL_NUMERIC_KEYS:
        assert f"_optional_decimal(data, '{key}'" in code


def test_s05_the_position_parse_has_no_two_argument_numeric_defaults():
    """A `data.get(key, default)` on any of these keys IS the defect."""
    code = _code(DeltaPosition.from_dict)
    for key in ("size", "entry_price", "mark_price", "unrealised_pnl",
                "unrealized_pnl", "leverage", "margin", "liquidation_price"):
        assert f"data.get('{key}'," not in code, key
    assert "data.get('size')" in code


def test_s06_all_five_optional_numerics_go_through_the_shared_helper():
    code = _code(DeltaPosition.from_dict)
    assert "_optional_decimal(data, 'entry_price')" in code
    assert "_optional_decimal(data, 'mark_price')" in code
    assert "_optional_decimal(data, 'unrealised_pnl', 'unrealized_pnl')" in code
    assert "_optional_decimal(data, 'leverage')" in code
    assert "_optional_decimal(data, 'margin')" in code


def test_s07_an_absent_size_raises_rather_than_defaulting():
    """The `Decimal("0")` comparisons that decide LONG vs SHORT are legitimate;
    a `"0"` or `"1"` used as a `data.get` FALLBACK is the defect."""
    code = _code(DeltaPosition.from_dict)
    assert "raise DeltaResponseError" in code
    assert ", '0')" not in code
    assert ", '1')" not in code
    assert ", 0)" not in code

def test_s08_identity_resolution_precedes_every_numeric_refusal():
    """§O6's explicit ordering constraint, asserted structurally as well as
    behaviourally (F43/F44). Numeric parsing must not move ahead of identity."""
    code = _code(DeltaPosition.from_dict)
    first_identity = code.index("UnknownInstrumentError")
    assert first_identity < code.index("DeltaResponseError")
    assert first_identity < code.index("_optional_decimal")
    assert code.index("delta_india_registry()") < first_identity


def test_s09_the_order_parse_has_no_open_default():
    code = _code(DeltaOrderResponse.from_dict)
    assert "'OPEN'" not in code
    assert '"OPEN"' not in code
    assert "data.get('state'," not in code
    assert "data.get('status'," not in code


def test_s10_the_order_parse_refuses_an_absent_state_explicitly():
    assert "raise UnknownOrderStateError" in _code(DeltaOrderResponse.from_dict)


def test_s11_the_order_parse_still_delegates_the_value_to_from_exchange():
    """Absence belongs to this boundary; VALUE interpretation stays in §O5's
    single mapping, so the two rules cannot drift apart."""
    assert "OrderStatus.from_exchange(" in _code(DeltaOrderResponse.from_dict)


def test_s12_from_exchange_is_still_o5_strict():
    code = _code(OrderStatus.from_exchange)
    assert "raise UnknownOrderStateError" in code
    assert "return cls.PENDING" not in code
    assert "mapping.get(" not in code


def test_s13_the_get_positions_signature_still_exposes_the_filter():
    src = inspect.getsource(DeltaIndiaClient.get_positions)
    assert "product_ids: Optional[List[int]] = None" in src


def test_s14_there_is_exactly_one_product_ids_validator():
    src = inspect.getsource(delta_client_mod)
    assert src.count("def _validated_product_ids(") == 1


def test_s15_both_query_builders_use_that_one_validator():
    assert "_validated_product_ids(" in _code(DeltaIndiaClient.get_positions)
    assert "_validated_product_ids(" in _code(DeltaIndiaClient.get_open_orders)

def test_s16_the_cap_lives_in_one_place_and_is_ten():
    """A duplicated literal is how the two endpoints' caps drift apart, and the
    cap is the reason an over-long filter cannot be silently truncated."""
    assert _MAX_PRODUCT_IDS == 10
    src = inspect.getsource(delta_client_mod)
    assert src.count("_MAX_PRODUCT_IDS = 10") == 1

    for func in (DeltaIndiaClient.get_positions, DeltaIndiaClient.get_open_orders):
        code = _code(func)
        assert "_MAX_PRODUCT_IDS" not in code
        assert "10" not in code

    helper = _code(_validated_product_ids)
    assert "_MAX_PRODUCT_IDS" in helper
    assert "10" not in helper


# ══ Documentation provenance (NOT a structural invariant) ═════════════════════
#
# The one test below reads raw source rather than unparsed AST, so unlike every
# `test_s*` above it IS comment-dependent. That is deliberate and it is kept
# separate: no executable contract rests on it. The cap's structural guarantees
# live in S14/S15/S16; this only pins the honesty of the label, because rule #16
# turns on the difference between an evidenced number and a verified one.


def test_d1_the_inherited_cap_is_documented_as_inherited_not_verified():
    """Rule #16: the evidenced maximum is for `GET /v2/orders`. Applying it to
    `/v2/positions/margined` is a fail-closed inheritance, and the comment
    must say so rather than implying a verified positions contract."""
    src = inspect.getsource(delta_client_mod)
    block = src[src.index("#: Maximum number of"):
                src.index("def _validated_product_ids(")]
    assert "_MAX_PRODUCT_IDS = 10" in block
    assert "/v2/orders" in block
    assert "/v2/positions/margined" in block
    assert "NOT" in block and "documented" in block


#: Fragments assembled at runtime so that this file's own safety scan (S18)
#: cannot match the needles it is searching for.
_FORBIDDEN_FRAGMENTS = (("os.", "environ"), ("get", "env"), ("dot", "env"),
                        ("place_", "order("), ("cancel_", "order("),
                        ("requests", ".get"), ("http", "s://api.delta"))


def test_s18_the_test_module_reaches_no_real_endpoint_and_reads_no_credential():
    """This file's own safety property, asserted from its own source. The scan
    stops at this function so that the needles below are not their own match."""
    module = inspect.getmodule(
        test_s01_get_positions_calls_the_documented_margined_endpoint)
    src = inspect.getsource(module)
    body = src[:src.index("_FORBIDDEN_FRAGMENTS = (")]

    for head, tail in _FORBIDDEN_FRAGMENTS:
        assert head + tail not in body, head + tail
    assert "MockTransport" in body

