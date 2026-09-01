"""
Task O §O7 -- the wallet balance / account state parse contracts.

`DeltaWalletBalance.from_dict` invented six observations. Five were numeric:

    balance=Decimal(str(data.get("balance", "0")))          # and the other four

and the sixth was identity: `asset_symbol=str(data.get("asset_symbol", ""))`.
The same five numeric fabrications, plus a *worse* identity one
(`asset_symbol` defaulted to `"USDT"` -- an unnamed frame asserting itself to be
the collateral wallet), lived in `EventValidator._normalize_margin`.

§O6 widened the position numerics to `Optional[Decimal]`, because a position's
`mark_price` is carried, compared and logged -- "unobserved" is a state its
consumers can hold. The wallet is the opposite case, and that is the whole of
§O7: every one of these five numbers is consumed ARITHMETICALLY at the moment it
arrives.

    get_account_summary:  margin_used = usdt.position_margin + usdt.order_margin
    validation gateway:   if required_margin > account.available_balance: reject
    capital allocator:    available_balance <= 0 -> CapitalAllocationError
    pre-trade gate:       available_balance <= 0 -> block
    WS margin frame:      account.margin_used = position_margin + order_margin

`None + None` is a `TypeError` raised in the wrong place, so widening was not
available here. Refusal at the boundary is. A fabricated zero, meanwhile, is not
a harmless placeholder in any of those five lines:

  * `available_balance = 0` reads as an account with no collateral, which the
    allocator and the pre-trade gate correctly refuse -- so the visible symptom
    is a false *block*, and the defect hides.
  * `position_margin = 0` and `order_margin = 0` read as an account with NOTHING
    committed, so `margin_used = 0` and the validation gateway compares a real
    requirement against an imaginary headroom. That direction authorizes.
  * `balance = 0` is the sharpest: `_authoritative_exchange_balance` already has
    an explicit `None` channel for "could not read", and a fabricated zero
    bypassed it and was written to `record.post_trade_balance`,
    `account.available_balance` and `account.total_equity` as an authoritative
    exchange reading of total loss of equity.

So: absent, blank, null, non-numeric, boolean and non-finite wallet numerics are
REFUSED (`DeltaResponseError`), an OBSERVED zero is kept as the distinct fact it
is, and a supplied value keeps its exact `Decimal` precision. The precedent for
refusing bools and non-finites is this repository's own, not an invented
exchange rule: `DeltaIndiaClient.get_ticker` and
`DeltaOrder.exchange_contract_count` already do exactly this.

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

from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.backend_client import BackendClient
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
    DeltaResponseError,
)
from quantedge.execution.models import (
    DeltaAccountSummary,
    DeltaWalletBalance,
    PositionSide,
    optional_decimal,
    required_decimal,
)
from quantedge.execution.multi_user_orchestrator import (
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.private_websocket import (
    DeltaMarginEvent,
    DeltaPrivateWebSocketClient,
    EventValidator,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    AccountRecord,
    LiveAccountSyncService,
    LocalStateStore,
    PositionRecord,
    PositionStatus,
    SyncResult,
)
from quantedge.execution.trade_lifecycle import TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments import delta_india_registry

ACCOUNT = "acc_task_o7"
USER = "user_task_o7"
SETUP = "BTCUSD_1h_MANUAL_SMC_O7_LONG"

#: The five wallet numerics that are consumed arithmetically. Absence of ANY of
#: them is refused; none of them may become a fabricated zero.
REQUIRED_WALLET_KEYS = ("balance", "available_balance", "position_margin",
                        "order_margin", "blocked_margin")

#: The four the WS `margins` frame carries. `DeltaMarginEvent` has no
#: `blocked_margin` field, so the frame is not expected to supply one.
REQUIRED_MARGIN_KEYS = ("balance", "available_balance", "position_margin",
                        "order_margin")

#: Every value a mandatory wallet numeric must refuse. `True`/`False` are here
#: because `bool` is an `int` subclass, so `Decimal(str(True))` is an
#: `InvalidOperation` but `Decimal(True)` would silently be `1` -- the same trap
#: `DeltaOrder.exchange_contract_count` rejects explicitly.
MALFORMED_NUMERICS = ("", "   ", None, True, False, "abc", "1.2.3", "--5",
                      "NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf",
                      [], {}, "1,000.00")


# ── Transport plumbing (identical in shape to the §O4/§O6 harness) ────────────


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
        api_key="TEST_KEY_TASK_O7_000000001",
        api_secret="TEST_SECRET_TASK_O7_00000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _wallet_json(**over) -> Dict[str, Any]:
    """A well-formed `/v2/wallet/balances` entry; overrides drive each case."""
    payload = {
        "asset_symbol": "USDT",
        "balance": "10000.12345678",
        "available_balance": "9500.87654321",
        "position_margin": "400.25",
        "order_margin": "99.00",
        "blocked_margin": "0.01",
        "user_id": 42,
        "id": 7001,
    }
    payload.update(over)
    return payload


def _margin_json(**over) -> Dict[str, Any]:
    """A well-formed `margins` stream frame."""
    payload = {
        "asset_symbol": "USDT",
        "balance": "10000.12345678",
        "available_balance": "9500.87654321",
        "position_margin": "400.25",
        "order_margin": "99.00",
    }
    payload.update(over)
    return payload


def _without(payload: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    """The same payload with `keys` absent -- the *absent* case, not the null one."""
    return {k: v for k, v in payload.items() if k not in keys}


def _code(func) -> str:
    """Executable source only: comments and docstrings cannot satisfy a `test_s*`.

    `ast.unparse` drops every comment and normalizes string literals to single
    quotes, which the assertions below account for. The §O7 docstrings quote the
    exact defaults they removed (`data.get('balance', '0')`, `'USDT'`), so a raw
    source search would match the prose that documents the fix.
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


# ══ A. `required_decimal`: the mandatory counterpart to `optional_decimal` ═════
#
# One helper, one contract. §O7 adds no second numeric parser and no second
# wallet model; `required_decimal` shares `optional_decimal`'s key precedence and
# its exactness, and differs only in what happens when nothing is there.


def test_a01_a_supplied_value_keeps_its_exact_decimal_precision():
    """`Decimal(str(raw))`, never `float`. Eight decimal places survive."""
    got = required_decimal({"balance": "10000.12345678"}, "balance",
                           field_name="balance", context="ctx")
    assert got == Decimal("10000.12345678")
    assert str(got) == "10000.12345678"


def test_a02_an_observed_zero_is_returned_not_refused():
    """The distinction §O7 turns on: a wallet the exchange *says* is empty is a
    real observation and must pass. Only an UNOBSERVED figure is refused."""
    for raw in ("0", "0.00", 0, Decimal("0"), "-0"):
        got = required_decimal({"balance": raw}, "balance",
                               field_name="balance", context="ctx")
        assert got == Decimal("0")
        assert isinstance(got, Decimal)


def test_a03_a_negative_value_is_preserved_not_validated():
    """Sign is carried, not judged. The repository has no evidence about whether
    Delta can report a negative wallet figure, and rule #16 forbids guessing:
    inventing a `>= 0` rule here would refuse a real observation."""
    got = required_decimal({"balance": "-12.5"}, "balance",
                           field_name="balance", context="ctx")
    assert got == Decimal("-12.5")


def test_a04_the_first_present_key_wins_exactly_as_in_optional_decimal():
    """Key precedence is shared with `optional_decimal`, so an alias list means
    the same thing in both helpers."""
    data = {"available_balance": "5", "availableBalance": "9"}
    assert required_decimal(data, "available_balance", "availableBalance",
                            field_name="f", context="c") == Decimal("5")
    assert optional_decimal(data, "available_balance",
                            "availableBalance") == Decimal("5")


def test_a05_a_blank_first_key_falls_through_to_a_populated_alias():
    """A present-but-blank key is not an observation, so it does not shadow a
    real one behind it -- same fall-through as `optional_decimal`."""
    data = {"available_balance": "  ", "availableBalance": "9.25"}
    assert required_decimal(data, "available_balance", "availableBalance",
                            field_name="f", context="c") == Decimal("9.25")
    assert optional_decimal(data, "available_balance",
                            "availableBalance") == Decimal("9.25")


def test_a06_an_absent_key_is_refused_where_optional_decimal_returns_none():
    """The single behavioural difference between the two helpers, stated once."""
    assert optional_decimal({}, "balance") is None
    with pytest.raises(DeltaResponseError):
        required_decimal({}, "balance", field_name="balance", context="ctx")


@pytest.mark.parametrize("raw", MALFORMED_NUMERICS)
def test_a07_every_malformed_or_unobserved_value_is_refused(raw):
    """Blank, whitespace, null, boolean, unparseable, NaN and infinite. The
    bool cases matter because `bool` is an `int` subclass; the NaN/infinity cases
    follow `get_ticker`'s `is_finite()` check and
    `DeltaOrder.exchange_contract_count`'s explicit bool rejection."""
    with pytest.raises(DeltaResponseError):
        required_decimal({"balance": raw}, "balance",
                         field_name="balance", context="ctx")


def test_a08_the_refusal_names_the_field_and_the_context():
    """A refused wallet must be diagnosable from the log line alone, or the
    operator cannot tell which asset and which figure failed."""
    with pytest.raises(DeltaResponseError) as absent:
        required_decimal({}, "order_margin",
                         field_name="order_margin", context="Wallet balance BTC")
    assert "order_margin" in str(absent.value)
    assert "Wallet balance BTC" in str(absent.value)

    with pytest.raises(DeltaResponseError) as malformed:
        required_decimal({"order_margin": "abc"}, "order_margin",
                         field_name="order_margin", context="Wallet balance BTC")
    assert "order_margin" in str(malformed.value)
    assert "Wallet balance BTC" in str(malformed.value)


def test_a09_the_refusal_is_the_typed_exception_every_caller_already_handles():
    """Not `ValueError`, not `InvalidOperation`, not `KeyError`. The four
    consumers' fail-closed paths catch `DeltaResponseError` (or `Exception`), so
    an untyped leak would land somewhere with no fail-closed handling at all."""
    with pytest.raises(DeltaResponseError):
        required_decimal({"balance": "abc"}, "balance",
                         field_name="balance", context="ctx")
    assert issubclass(DeltaResponseError, Exception)


# ══ B. `DeltaWalletBalance.from_dict` ═════════════════════════════════════════


def test_b10_a_complete_payload_is_parsed_exactly():
    """The positive case, first: nothing about §O7 may cost precision, and the
    non-numeric passthrough fields are unaffected."""
    w = DeltaWalletBalance.from_dict(_wallet_json())

    assert w.asset_symbol == "USDT"
    assert str(w.balance) == "10000.12345678"
    assert str(w.available_balance) == "9500.87654321"
    assert w.position_margin == Decimal("400.25")
    assert w.order_margin == Decimal("99.00")
    assert w.blocked_margin == Decimal("0.01")
    assert w.user_id == 42
    assert w.wallet_id == 7001
    for value in (w.balance, w.available_balance, w.position_margin,
                  w.order_margin, w.blocked_margin):
        assert isinstance(value, Decimal)


def test_b11_an_all_zero_wallet_is_observed_not_refused():
    """An account that really is empty must still parse. This is the test that
    stops the §O7 fix from becoming "refuse everything at zero", which would
    break a genuinely flat account's reconciliation."""
    w = DeltaWalletBalance.from_dict(_wallet_json(
        balance="0", available_balance="0", position_margin="0",
        order_margin="0", blocked_margin="0"))

    assert w.balance == Decimal("0")
    assert w.available_balance == Decimal("0")
    assert w.position_margin == Decimal("0")
    assert w.order_margin == Decimal("0")
    assert w.blocked_margin == Decimal("0")


@pytest.mark.parametrize("key", REQUIRED_WALLET_KEYS)
def test_b12_an_absent_required_numeric_is_refused(key):
    """The core §O7 contract. Before this pass each of these five produced
    `Decimal("0")` -- an arithmetic input the exchange never supplied."""
    with pytest.raises(DeltaResponseError) as exc:
        DeltaWalletBalance.from_dict(_without(_wallet_json(), key))
    assert key in str(exc.value)


@pytest.mark.parametrize("key", REQUIRED_WALLET_KEYS)
@pytest.mark.parametrize("raw", ("", "  ", None, True, "abc", "NaN",
                                "Infinity", "-Infinity"))
def test_b13_a_blank_null_or_malformed_required_numeric_is_refused(key, raw):
    """Present-but-unusable is the same fact as absent, and is refused the same
    way. A blank string was the sneakiest of these: `Decimal(str(""))` raises
    `InvalidOperation`, so before §O7 it did not even reach the old default -- it
    escaped as an untyped exception from inside the parse loop."""
    with pytest.raises(DeltaResponseError):
        DeltaWalletBalance.from_dict(_wallet_json(**{key: raw}))


@pytest.mark.parametrize("raw", (None, "", "   "))
def test_b14_a_wallet_with_no_asset_symbol_is_refused(raw):
    """Fabrication six, the identity one. `str(data.get("asset_symbol", ""))`
    produced `""`, and `get_account_summary` keys `balance_map` on it -- so an
    unnamed wallet entered the account summary as an asset named nothing, and
    `reconcile_account`'s `b.asset_symbol == "USDT"` scan silently skipped it.
    Rule #15: an unidentified asset fails closed."""
    with pytest.raises(DeltaResponseError) as exc:
        DeltaWalletBalance.from_dict(_wallet_json(asset_symbol=raw))
    assert "asset_symbol" in str(exc.value)


def test_b15_identity_is_resolved_before_any_numeric_is_read():
    """Ordering, preserved from §O6: when identity AND numerics are both broken,
    the identity failure takes precedence. An operator must be told *which*
    wallet could not be read before being told what was wrong with its figures,
    and a refusal message naming no asset is not actionable."""
    with pytest.raises(DeltaResponseError) as exc:
        DeltaWalletBalance.from_dict(
            {"asset_symbol": "", "balance": "abc"})

    message = str(exc.value)
    assert "asset_symbol" in message
    # The numeric refusal never fired: its message shape is absent.
    assert "unparseable" not in message
    assert "'abc'" not in message


def test_b16_a_present_asset_symbol_is_still_upper_case_folded():
    """Deliberately UNCHANGED by §O7. Every consumer keys on the literal
    `"USDT"`, there is no pinned asset registry to validate against, and
    case-folding a value the exchange really did send invents nothing. Only the
    *default* was a fabrication."""
    assert DeltaWalletBalance.from_dict(
        _wallet_json(asset_symbol="usdt")).asset_symbol == "USDT"
    assert DeltaWalletBalance.from_dict(
        _wallet_json(asset_symbol=" usdt ")).asset_symbol == "USDT"


def test_b17_a_non_usdt_wallet_is_parsed_under_its_own_name():
    """`USDT` is not privileged at the parse boundary -- only in the summary."""
    w = DeltaWalletBalance.from_dict(_wallet_json(
        asset_symbol="BTC", balance="0.00000001"))
    assert w.asset_symbol == "BTC"
    assert str(w.balance) == "1E-8"


def test_b18_the_optional_identity_fields_may_legitimately_be_absent():
    """`user_id` and `wallet_id` are `Optional` on the dataclass and no consumer
    does arithmetic on either, so §O7 does not touch them. Widening the refusal
    to cover them would refuse wallets the engine can safely use."""
    w = DeltaWalletBalance.from_dict(_without(_wallet_json(), "user_id", "id"))
    assert w.user_id is None
    assert w.wallet_id is None
    assert w.balance == Decimal("10000.12345678")


def test_b19_the_five_numerics_are_still_mandatory_decimals_on_the_dataclass():
    """§O7 must NOT widen these to `Optional[Decimal]`. That was the correct
    answer for §O6 positions and is the wrong one here: the type is what stops a
    `None` from reaching `position_margin + order_margin` as a `TypeError` deep
    inside `get_account_summary`."""
    hints = DeltaWalletBalance.__annotations__
    for key in REQUIRED_WALLET_KEYS:
        assert "Optional" not in str(hints[key]), key
        assert "Decimal" in str(hints[key]), key


# ══ C. `get_wallet_balances`: the pre-existing envelope contract is preserved ══


@pytest.mark.asyncio
async def test_c20_a_well_formed_list_is_parsed_and_costs_exactly_one_request():
    """`request()` has no retry, so one call is one HTTP request."""
    client, rec = _client(lambda r: _ok([_wallet_json(),
                                         _wallet_json(asset_symbol="BTC")]))

    balances = await client.get_wallet_balances()

    assert [b.asset_symbol for b in balances] == ["USDT", "BTC"]
    assert str(balances[0].balance) == "10000.12345678"
    assert len(rec.requests) == 1
    assert rec.last.url.path == "/v2/wallet/balances"
    assert rec.last.method == "GET"


@pytest.mark.asyncio
async def test_c21_an_empty_wallet_list_is_a_real_observation():
    """An account with no wallets at all is not a malformed answer."""
    client, _ = _client(lambda r: _ok([]))
    assert await client.get_wallet_balances() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ({"balances": []}, "none", None, 7, 0, True,
                                   "[]"))
async def test_c22_a_non_list_result_still_raises(result):
    """PRE-EXISTING contract, explicitly preserved. `get_wallet_balances` already
    refused a non-list envelope before §O7 -- it was the precedent §O6 cited when
    fixing `get_open_orders`. §O7 must not regress it."""
    client, rec = _client(lambda r: _ok(result))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_c23_a_missing_result_key_is_still_the_empty_list():
    """Also pre-existing: `data.get("result", [])`. An absent key is treated as
    an empty list rather than refused, and §O7 does not change that -- widening
    it is an envelope question, not a wallet-numeric one."""
    client, _ = _client(lambda r: httpx.Response(200, json={"success": True}))
    assert await client.get_wallet_balances() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", REQUIRED_WALLET_KEYS + ("asset_symbol",))
async def test_c24_one_malformed_entry_refuses_the_whole_snapshot(key):
    """A wallet list is a snapshot, not a bag of independent rows: skipping the
    unreadable entry would hand `get_account_summary` a partial account and it
    would sum what remained into a confident, wrong equity. So the snapshot is
    refused whole."""
    client, _ = _client(lambda r: _ok([
        _wallet_json(asset_symbol="BTC"),
        _without(_wallet_json(), key),
    ]))
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()


# ══ D. `get_account_summary`: the arithmetic that made this mandatory ══════════


@pytest.mark.asyncio
async def test_d25_the_usdt_branch_computes_margin_used_from_real_figures():
    """`margin_used = usdt.position_margin + usdt.order_margin`, exactly the
    line that cannot accept a `None` and must not accept a fabricated zero."""
    client, _ = _client(lambda r: _ok([_wallet_json()]))

    summary = await client.get_account_summary()

    assert isinstance(summary, DeltaAccountSummary)
    assert str(summary.total_equity) == "10000.12345678"
    assert str(summary.available_balance) == "9500.87654321"
    assert summary.margin_used == Decimal("499.25")  # 400.25 + 99.00
    assert summary.user_id == 42
    assert set(summary.balances) == {"USDT"}


@pytest.mark.asyncio
async def test_d26_the_non_usdt_fallback_sums_only_real_figures():
    """The `else` branch sums across every wallet. Before §O7 a wallet with an
    absent `position_margin` contributed a silent zero to `margin_used`, which
    is the direction that AUTHORIZES: the validation gateway compares a real
    `required_margin` against headroom that was never observed."""
    client, _ = _client(lambda r: _ok([
        _wallet_json(asset_symbol="BTC", balance="1", available_balance="1",
                     position_margin="2", order_margin="3", blocked_margin="0"),
        _wallet_json(asset_symbol="ETH", balance="10", available_balance="10",
                     position_margin="20", order_margin="30", blocked_margin="0"),
    ]))

    summary = await client.get_account_summary()

    assert summary.total_equity == Decimal("11")
    assert summary.available_balance == Decimal("11")
    assert summary.margin_used == Decimal("55")
    assert set(summary.balances) == {"BTC", "ETH"}


@pytest.mark.asyncio
@pytest.mark.parametrize("key", REQUIRED_WALLET_KEYS)
async def test_d27_a_malformed_wallet_refuses_the_summary_rather_than_computing(key):
    """The refusal reaches the arithmetic consumer as a refusal, not as a number
    and not as a `TypeError`."""
    client, _ = _client(lambda r: _ok([_without(_wallet_json(), key)]))
    with pytest.raises(DeltaResponseError):
        await client.get_account_summary()


@pytest.mark.asyncio
async def test_d28_an_empty_wallet_list_still_summarizes_to_zero():
    """UNCHANGED pre-existing behaviour, pinned so §O7 is not read as banning it:
    `sum((), Decimal("0"))` over no wallets is `0`, and "the exchange returned no
    wallets" is a real answer with no fabricated field in it. The refusal is
    about a wallet that EXISTS and does not say what it holds."""
    client, _ = _client(lambda r: _ok([]))

    summary = await client.get_account_summary()

    assert summary.total_equity == Decimal("0")
    assert summary.available_balance == Decimal("0")
    assert summary.margin_used == Decimal("0")
    assert summary.user_id is None
    assert summary.balances == {}


@pytest.mark.asyncio
async def test_d29_a_genuinely_empty_usdt_wallet_summarizes_to_observed_zero():
    """The positive twin of D27: observed zeros flow through the arithmetic."""
    client, _ = _client(lambda r: _ok([_wallet_json(
        balance="0", available_balance="0", position_margin="0",
        order_margin="0", blocked_margin="0")]))

    summary = await client.get_account_summary()

    assert summary.total_equity == Decimal("0")
    assert summary.margin_used == Decimal("0")
    assert summary.balances["USDT"].available_balance == Decimal("0")


# ══ E. The WebSocket `margins` frame, closed at the same boundary ══════════════
#
# `_apply_margin_event` writes `available_balance` and
# `position_margin + order_margin` onto `LocalStateStore.account` on EVERY frame.
# Fixing only the REST parser would have relocated the defect, not closed it, and
# would have recreated exactly the REST/WS disagreement §O6 C7 outlawed.


def _validator() -> EventValidator:
    return EventValidator()


def test_e30_a_complete_margin_frame_is_parsed_exactly():
    event = _validator()._normalize_margin(_margin_json())

    assert isinstance(event, DeltaMarginEvent)
    assert event.asset_symbol == "USDT"
    assert str(event.balance) == "10000.12345678"
    assert str(event.available_balance) == "9500.87654321"
    assert event.position_margin == Decimal("400.25")
    assert event.order_margin == Decimal("99.00")


def test_e31_an_all_zero_margin_frame_is_observed_not_refused():
    event = _validator()._normalize_margin(_margin_json(
        balance="0", available_balance="0", position_margin="0",
        order_margin="0"))

    assert event.balance == Decimal("0")
    assert event.available_balance == Decimal("0")
    assert event.position_margin == Decimal("0")
    assert event.order_margin == Decimal("0")


@pytest.mark.parametrize("key", REQUIRED_MARGIN_KEYS)
def test_e32_an_absent_margin_numeric_is_refused(key):
    with pytest.raises(DeltaResponseError) as exc:
        _validator()._normalize_margin(_without(_margin_json(), key))
    assert key in str(exc.value)


@pytest.mark.parametrize("key", REQUIRED_MARGIN_KEYS)
@pytest.mark.parametrize("raw", ("", None, True, "abc", "NaN", "Infinity"))
def test_e33_a_malformed_margin_numeric_is_refused(key, raw):
    with pytest.raises(DeltaResponseError):
        _validator()._normalize_margin(_margin_json(**{key: raw}))


@pytest.mark.parametrize("raw", (None, "", "   "))
def test_e34_an_unnamed_margin_frame_no_longer_claims_to_be_the_usdt_wallet(raw):
    """The worst of the six fabrications. `asset_symbol` defaulted to `"USDT"`,
    and `_apply_margin_event` acts ONLY on `("USDT", "USD")` -- so an unnamed
    frame was routed straight onto the collateral account record by the very
    default that invented its identity. Rule #15."""
    with pytest.raises(DeltaResponseError) as exc:
        _validator()._normalize_margin(_margin_json(asset_symbol=raw))
    assert "asset_symbol" in str(exc.value)


def _margin_frame(**over) -> str:
    import json
    return json.dumps({"type": "margins", "channel": "margins",
                       "payload": _margin_json(**over)})


@pytest.mark.parametrize("key", REQUIRED_MARGIN_KEYS + ("asset_symbol",))
def test_e35_a_refused_margin_frame_is_quarantined_and_counted(key):
    """§O5 owns what happens to a frame that fails normalization, and §O7 does
    not change it: `parse_and_validate` returns `None`, the frame is quarantined
    with its error text, and `malformed_events_count` rises. The refusal is
    therefore observable and auditable rather than silent -- and, decisively, no
    event object reaches `_apply_margin_event`."""
    import json
    validator = _validator()
    frame = json.dumps({"type": "margins", "channel": "margins",
                        "payload": _without(_margin_json(), key)})

    assert validator.parse_and_validate(frame) is None
    assert validator.malformed_events_count == 1
    assert validator.valid_events_count == 0
    assert len(validator.quarantined_events) == 1
    assert key in validator.quarantined_events[0]["error"]


def test_e36_a_valid_margin_frame_still_routes_through_the_dispatcher():
    """The control case for E35: the `margins` branch is still reachable, so the
    quarantine above is a refusal and not a routing failure."""
    validator = _validator()

    event = validator.parse_and_validate(_margin_frame())

    assert isinstance(event, DeltaMarginEvent)
    assert validator.valid_events_count == 1
    assert validator.malformed_events_count == 0
    assert validator.quarantined_events == []


def test_e37_seq_continuity_is_still_recorded_for_a_refused_margin_frame():
    """§O5 explicitly records continuity for a frame whose normalization then
    fails, so a sequence gap cannot be masked by the very frame that revealed a
    problem. §O7's new refusal must not bypass that."""
    import json
    validator = _validator()
    frame = json.dumps({"type": "margins", "channel": "margins",
                        "payload": dict(_without(_margin_json(), "balance"),
                                        seq_no=99)})

    assert validator.parse_and_validate(frame) is None
    assert validator.last_frame_continuity is not None
    assert validator.last_frame_continuity.channel == "margins"
    assert validator.last_frame_continuity.seq_no == 99


# ══ F. Consumer-level fail-closed behaviour ════════════════════════════════════
#
# Five places act on wallet numbers. Each assertion below is the same claim: a
# refused wallet must leave local account state EXACTLY as it was, because every
# fabricated value here is arithmetic input to an authorization decision.


def _wallet_client(responder) -> DeltaIndiaClient:
    """A REAL client over `MockTransport`, so the whole chain is exercised:
    HTTP -> envelope -> `from_dict` -> consumer. No monkeypatching of the parser,
    and no mock standing in for the code under test."""
    client, _ = _client(responder)
    return client


def _malformed_wallet_client(missing: str = "balance") -> DeltaIndiaClient:
    return _wallet_client(lambda r: _ok([_without(_wallet_json(), missing)]))


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
@pytest.mark.parametrize("missing", REQUIRED_WALLET_KEYS)
async def test_f38_the_synchronizer_leaves_the_account_record_untouched(missing):
    """Consumer 1. `_reconcile_balances` overwrites `available_balance`,
    `margin_used`, `current_balance` and `total_equity` from the summary. A
    refusal must leave the last known-good figures in place -- and, because
    `get_account_summary` is awaited BEFORE `_reconcile_positions`, it must also
    leave every local position alone rather than closing it."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.available_balance = Decimal("5000")
    store.account.margin_used = Decimal("250")
    store.account.total_equity = Decimal("5250")
    store.account.current_balance = Decimal("5250")
    store.positions["BTCUSD"] = _local_position()

    service = LiveAccountSyncService(
        client=_malformed_wallet_client(missing), state_store=store)

    result = await service.synchronize(ACCOUNT)

    assert isinstance(result, SyncResult)
    assert result.success is False
    assert result.error
    assert store.account.available_balance == Decimal("5000")
    assert store.account.margin_used == Decimal("250")
    assert store.account.total_equity == Decimal("5250")
    assert store.account.current_balance == Decimal("5250")
    assert store.connection.connection_status == "ERROR"
    # The position half never ran, so nothing was closed on a refused snapshot.
    assert "BTCUSD" in store.positions
    assert store.positions["BTCUSD"].status is PositionStatus.OPEN
    assert store.position_history == []


@pytest.mark.asyncio
async def test_f39_the_synchronizer_still_writes_a_well_formed_summary():
    """The control case: F38 is a refusal, not a broken synchronizer."""
    store = LocalStateStore(account_id=ACCOUNT)
    service = LiveAccountSyncService(
        client=_wallet_client(lambda r: _ok([_wallet_json()])
                              if r.url.path == "/v2/wallet/balances"
                              else _ok([])),
        state_store=store,
    )

    result = await service.synchronize(ACCOUNT)

    assert result.success is True
    assert str(store.account.available_balance) == "9500.87654321"
    assert store.account.margin_used == Decimal("499.25")
    assert str(store.account.total_equity) == "10000.12345678"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", REQUIRED_WALLET_KEYS)
async def test_f40_reconciliation_reports_unreachable_and_keeps_the_lock(missing):
    """Consumer 2, the most dangerous one. `get_wallet_balances` is the FIRST
    exchange call in `reconcile_account`, so a refused wallet fails closed before
    `exchange_is_flat` can even be computed -- no `force_release_lock`, no
    `ACCOUNT_BALANCE_SYNCHRONIZED_FROM_DELTA`. Rules #11/#14 make lock RETENTION
    the fail-safe direction."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.user_id = USER
    store.account.available_balance = Decimal("5000")
    store.account.total_equity = Decimal("5250")
    store.positions["BTCUSD"] = _local_position()

    lock = SingleTradeLockManager()
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True

    backend = MagicMock(spec=BackendClient)
    backend.force_release_lock = AsyncMock()
    service = DeltaReconciliationService(
        client=_malformed_wallet_client(missing),
        state_store=store,
        single_trade_lock=lock,
        backend_client=backend,
    )

    report = await service.reconcile_account(
        ACCOUNT, user_id=USER, auto_resolve=True)

    assert report.is_synchronized is False
    assert report.actions_taken == ["EXCHANGE_UNREACHABLE_FAIL_CLOSED"]
    assert "ACCOUNT_BALANCE_SYNCHRONIZED_FROM_DELTA" not in report.actions_taken
    assert "DELTA_RECONCILED_FLAT" not in report.actions_taken

    is_locked, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    assert is_locked is True
    assert setup_id == SETUP
    assert symbol == "BTCUSD"
    backend.force_release_lock.assert_not_called()
    # The authoritative-balance overwrite at step 7 never ran.
    assert store.account.available_balance == Decimal("5000")
    assert store.account.total_equity == Decimal("5250")


def _session(client) -> UserExecutionSession:
    return UserExecutionSession(
        config=UserAccountConfig(
            user_id=USER,
            account_id=ACCOUNT,
            is_active=True,
            algo_enabled=True,
            kill_switch_active=False,
            api_key="O7_KEY",
            api_secret="O7_SECRET",
            client_factory=lambda _k, _s: client,
        ),
        lock_manager=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", REQUIRED_WALLET_KEYS)
async def test_f41_the_pre_trade_gate_submits_nothing_on_a_refused_wallet(missing):
    """Consumer 3. Step 4 of `execute_trade` is the "Authoritative Live Balance
    Query (Dynamic, NEVER hardcoded)". It runs BEFORE the exposure check and
    before any order, so a refused wallet stops the trade at the earliest gate
    and the lock is released because nothing can exist yet."""
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/wallet/balances", (
            f"a refused wallet must stop the trade before {request.url.path}")
        return _ok([_without(_wallet_json(), missing)])

    session = _session(_wallet_client(responder))

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
    assert session.lock_manager.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_f42_the_gate_still_refuses_an_observed_zero_available_balance():
    """The observed-zero case reaches the allocator's own `<= 0` rule and is
    refused there, by the pre-existing check, on a REAL observation. §O7 changes
    which of the two refusals fires, not whether one does -- and that is the
    operational payoff: an empty account is now REPORTED DIFFERENTLY from an
    unreadable one (`BLOCKED_MARGIN` vs `ERROR`), where before §O7 both produced
    the identical "Insufficient available balance" message."""
    session = _session(_wallet_client(lambda r: _ok([_wallet_json(
        balance="0", available_balance="0", position_margin="0",
        order_margin="0", blocked_margin="0")])))

    result = await session.execute_trade(
        setup_id=SETUP,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=Decimal("95000"),
        stop_loss_price=Decimal("94000"),
        take_profit_price=Decimal("97000"),
        default_leverage=10,
    )

    assert result.status == "BLOCKED_MARGIN"
    assert result.status != "ERROR"
    assert "balance" in (result.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", REQUIRED_WALLET_KEYS)
async def test_f43_the_lifecycle_balance_channel_reports_unavailable_not_zero(missing):
    """Consumer 4, the sharpest one. `_authoritative_exchange_balance` ALREADY
    had an explicit `None` = "could not read" channel, and a fabricated
    `Decimal("0")` bypassed it: the caller writes a non-`None` result to
    `record.post_trade_balance`, `account.available_balance` and
    `account.total_equity` as an authoritative exchange reading. Booking total
    loss of equity as an observation is the worst outcome in §O7, and it is now
    routed into the `None` channel the code already had."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.available_balance = Decimal("5000")
    manager = TradeLifecycleManager(
        client=_malformed_wallet_client(missing),
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )

    got = await manager._authoritative_exchange_balance("BTCUSD")

    assert got is None
    assert got != Decimal("0")
    # The refusal did not touch local state on its way past.
    assert store.account.available_balance == Decimal("5000")


@pytest.mark.asyncio
async def test_f44_the_lifecycle_balance_channel_still_reads_a_real_balance():
    """The control case for F43, including exact precision through the channel."""
    store = LocalStateStore(account_id=ACCOUNT)
    manager = TradeLifecycleManager(
        client=_wallet_client(lambda r: _ok([_wallet_json()])),
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )

    got = await manager._authoritative_exchange_balance("BTCUSD")

    assert str(got) == "10000.12345678"


@pytest.mark.asyncio
async def test_f45_an_observed_zero_balance_is_still_a_real_reading():
    """The distinction one more time, at the consumer that suffers most from
    losing it: a wallet the exchange says is empty must NOT be reported as
    "unavailable", or a genuinely liquidated account would fall back to a
    computed balance instead of the exchange's own figure."""
    store = LocalStateStore(account_id=ACCOUNT)
    manager = TradeLifecycleManager(
        client=_wallet_client(lambda r: _ok([_wallet_json(
            balance="0", available_balance="0", position_margin="0",
            order_margin="0", blocked_margin="0")])),
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )

    got = await manager._authoritative_exchange_balance("BTCUSD")

    assert got == Decimal("0")
    assert got is not None


def test_f46_a_refused_margin_frame_never_reaches_the_account_record():
    """Consumer 5. `_apply_margin_event` is the only writer of
    `account.available_balance` and `account.margin_used` on the stream path, and
    it acts only on `("USDT", "USD")`. A quarantined frame produces no event, so
    the record keeps its last known-good figures."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.available_balance = Decimal("5000")
    store.account.margin_used = Decimal("250")
    store.account.total_equity = Decimal("5250")

    validator = _validator()
    assert validator.parse_and_validate(
        _margin_frame(available_balance="")) is None

    assert store.account.available_balance == Decimal("5000")
    assert store.account.margin_used == Decimal("250")
    assert store.account.total_equity == Decimal("5250")


def test_f47_a_well_formed_margin_frame_still_updates_the_account_record():
    """The control case for F46, through the real apply path."""
    store = LocalStateStore(account_id=ACCOUNT)
    client = DeltaPrivateWebSocketClient(
        api_key="TEST_O7_WS_KEY_0000000001",
        api_secret="TEST_O7_WS_SECRET_00000000000000001",
        state_store=store,
    )

    event = client.validator.parse_and_validate(_margin_frame())
    assert client.apply_event(event) is True

    assert str(store.account.available_balance) == "9500.87654321"
    assert store.account.margin_used == Decimal("499.25")
    assert str(store.account.total_equity) == "10000.12345678"


@pytest.mark.asyncio
async def test_f48_the_validation_gateway_headroom_cannot_be_fabricated():
    """Consumer 6, the one that AUTHORIZES. `validation.py` rejects when
    `required_margin > context.account.available_balance`. Before §O7 an absent
    `available_balance` became `0` -- which happens to reject -- but an absent
    `position_margin`/`order_margin` became a `margin_used` of `0`, i.e. an
    account with nothing committed. This test pins the source of that number: the
    gateway reads the LOCAL record, and the local record is only ever written
    from a wallet the exchange actually described."""
    store = LocalStateStore(account_id=ACCOUNT)
    store.account.available_balance = Decimal("5000")
    store.account.margin_used = Decimal("250")

    service = LiveAccountSyncService(
        client=_malformed_wallet_client("order_margin"), state_store=store)
    result = await service.synchronize(ACCOUNT)

    assert result.success is False
    assert store.account.margin_used == Decimal("250")
    assert store.account.available_balance == Decimal("5000")
    assert isinstance(store.account, AccountRecord)


# ══ G. §O1-§O6 compatibility ═══════════════════════════════════════════════════


def test_g49_optional_decimal_is_unchanged_and_still_returns_none():
    """§O7 adds a helper beside `optional_decimal`; it does not alter it. §O2/§O3
    depend on the `None`-returning behaviour for the position numerics."""
    assert optional_decimal({}, "mark_price") is None
    assert optional_decimal({"mark_price": ""}, "mark_price") is None
    assert optional_decimal({"mark_price": "  "}, "mark_price") is None
    assert optional_decimal({"mark_price": None}, "mark_price") is None
    assert optional_decimal({"mark_price": "0"}, "mark_price") == Decimal("0")
    assert str(optional_decimal({"mark_price": "1.00000001"},
                                "mark_price")) == "1.00000001"


def test_g50_the_o6_position_numerics_are_still_optional_not_refused():
    """The two sections must not converge. A position's absent `mark_price` is
    still `None` -- widening §O7's refusal onto §O6's fields would refuse
    positions the exchange described perfectly legally."""
    from quantedge.execution.models import DeltaPosition

    pos = DeltaPosition.from_dict({
        "product_id": 27, "product_symbol": "BTCUSD", "size": "3",
    })
    assert pos.mark_price is None
    assert pos.entry_price is None
    assert pos.unrealized_pnl is None
    assert pos.margin is None
    assert pos.leverage is None


def test_g51_the_o6_ws_position_path_is_untouched():
    """§O7 changed `_normalize_margin` only. The position frame still tolerates an
    absent `size`, because §O6 C7 established that a documented `delete` frame may
    omit it and §O5 answers closure from the action."""
    event = _validator()._normalize_position(
        {"product_symbol": "BTCUSD", "entry_price": "77000.0"}, action="delete")
    assert event.size == Decimal("0")
    assert event.is_closure is True


def test_g52_governance_state_is_unchanged():
    """No §O7 edit may move the engine closer to live trading."""
    from quantedge.ai.research.displacement_gated_retest_engine import (
        AI_PROMOTION_STATUS,
    )
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AI_PROMOTION_STATUS == "REJECTED"
    assert AccountRecord(account_id=ACCOUNT).algo_enabled is False
    assert AccountRecord(account_id=ACCOUNT).kill_switch_active is True


def test_g53_the_instrument_registry_is_untouched():
    """§O6's provenance-backed registry is the identity authority and §O7 does not
    read, extend or bypass it."""
    spec = delta_india_registry().get("BTCUSD")
    assert spec.product_id == 27
    assert spec.contract_value == Decimal("0.001")


# ══ Static source invariants (the defects cannot quietly return) ═══════════════
#
# Every §O7 defect was a plausible-looking default that read as harmless at the
# call site, and a behavioural test can be satisfied by re-adding one somewhere
# else. These assertions inspect UNPARSED AST, so no comment or docstring can
# satisfy them; `ast.unparse` normalizes string literals to single quotes.


def test_s54_the_wallet_parser_has_no_numeric_fallback_left():
    code = _code(DeltaWalletBalance.from_dict)
    for key in REQUIRED_WALLET_KEYS:
        assert f"'{key}', '0'" not in code, key
        assert f"data.get('{key}', '0')" not in code, key
    assert ", '0')" not in code
    assert ", '1')" not in code
    assert ", 0)" not in code


def test_s55_the_wallet_parser_routes_every_required_numeric_through_one_helper():
    """No second numeric parser, and no field quietly left on the old path."""
    code = _code(DeltaWalletBalance.from_dict)
    for key in REQUIRED_WALLET_KEYS:
        assert f"_required_decimal(data, '{key}'" in code, key
    assert code.count("_required_decimal(") == len(REQUIRED_WALLET_KEYS)
    assert "Decimal(str(" not in code


def test_s56_the_wallet_parser_has_no_asset_symbol_fallback_left():
    code = _code(DeltaWalletBalance.from_dict)
    assert "data.get('asset_symbol', '')" not in code
    assert "'asset_symbol', ''" not in code
    assert "'USDT'" not in code
    assert "raise DeltaResponseError" in code


def test_s57_the_margin_normalizer_has_no_fallback_left():
    code = _code(EventValidator._normalize_margin)
    for key in REQUIRED_MARGIN_KEYS:
        assert f"data.get('{key}', '0')" not in code, key
        assert f"_required_decimal(data, '{key}'" in code, key
    assert code.count("_required_decimal(") == len(REQUIRED_MARGIN_KEYS)
    assert "data.get('asset_symbol', 'USDT')" not in code
    assert "'USDT'" not in code
    assert ", '0')" not in code
    assert "Decimal(str(" not in code
    assert "raise DeltaResponseError" in code


def test_s58_identity_precedes_numerics_in_both_parsers():
    """The §O6 ordering constraint, structurally: the `asset_symbol` refusal must
    appear before the first `_required_decimal` call in each parser."""
    for func in (DeltaWalletBalance.from_dict, EventValidator._normalize_margin):
        code = _code(func)
        assert code.index("asset_symbol") < code.index("_required_decimal(")
        assert code.index("raise DeltaResponseError") < code.index(
            "_required_decimal(")


def test_s59_required_decimal_refuses_bools_and_non_finite_values():
    """The three checks whose precedent is this repository's own -- `get_ticker`
    and `DeltaOrder.exchange_contract_count` -- rather than an invented exchange
    rule."""
    code = _code(required_decimal)
    assert "isinstance(raw, bool)" in code
    assert "is_finite()" in code
    assert "InvalidOperation" in code
    assert "Decimal(str(raw))" in code
    assert code.count("raise DeltaResponseError") >= 4
    # No default value can be returned in place of a missing observation.
    assert "return Decimal('0')" not in code
    assert "return None" not in code


def test_s60_required_decimal_returns_an_observed_zero_rather_than_refusing_it():
    """Structurally: there is no `if not value` / `== 0` / `<= 0` guard, so an
    observed zero cannot be mistaken for an absence. §O7's whole distinction dies
    if one is ever added here."""
    code = _code(required_decimal)
    assert "if not value:" not in code
    assert "if value == 0" not in code
    assert "if value <= 0" not in code
    assert "value == Decimal" not in code


def test_s61_the_two_helpers_share_one_key_precedence_implementation():
    """Both walk `*keys`, both skip an absent key, both treat blank as absent.
    §O7 must not fork the alias semantics."""
    for func in (optional_decimal, required_decimal):
        code = _code(func)
        assert "for key in keys" in code
        assert "if key not in data" in code
        assert "str(raw).strip() == ''" in code


def test_s62_the_wallet_envelope_check_is_still_in_the_client():
    """Pre-existing, preserved: §O7 must not remove the non-list refusal it
    inherited."""
    code = _code(DeltaIndiaClient.get_wallet_balances)
    assert "isinstance(results, list)" in code
    assert "raise DeltaResponseError" in code
    assert "return []" not in code


def test_s63_the_account_summary_arithmetic_is_unchanged():
    """§O7 fixed the INPUTS to this arithmetic, not the arithmetic. If a future
    change moved the sum behind a `getattr(..., 0)` or an `or Decimal('0')`, the
    parser refusal would be silently undone one call downstream."""
    code = _code(DeltaIndiaClient.get_account_summary)
    assert "position_margin + usdt_bal.order_margin" in code
    assert "or Decimal('0')" not in code
    assert "getattr(" not in code


def test_s64_the_five_wallet_fields_are_not_optional_in_the_source():
    """The dataclass declaration itself, read structurally: a later `Optional[...]`
    widening would reintroduce the `None`-into-arithmetic hazard §O7 exists to
    prevent, and would pass every behavioural test above."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(DeltaWalletBalance)))
    annotations = {
        node.target.id: ast.unparse(node.annotation)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for key in REQUIRED_WALLET_KEYS:
        assert annotations[key] == "Decimal", (key, annotations[key])
    # The identity passthroughs stay optional -- nothing does arithmetic on them.
    assert "Optional" in annotations["user_id"]
    assert "Optional" in annotations["wallet_id"]


def test_s65_the_margin_event_fields_are_not_optional_in_the_source():
    tree = ast.parse(textwrap.dedent(inspect.getsource(DeltaMarginEvent)))
    annotations = {
        node.target.id: ast.unparse(node.annotation)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for key in REQUIRED_MARGIN_KEYS:
        assert annotations[key] == "Decimal", (key, annotations[key])
    assert annotations["asset_symbol"] == "str"


# ══ Safety scans: no network, no credentials, no order mutation ════════════════
#
# The fragments are assembled at runtime from split halves so this scan cannot
# match its own needle list -- the failure mode that made the first version of the
# §O6 equivalent fail against itself.

_FORBIDDEN_FRAGMENTS = (
    ("os.env", "iron"),
    ("load_dot", "env"),
    (".en", "v"),
    ("api.india.del", "ta.exchange"),
    ("place_or", "der("),
    ("cancel_or", "der("),
    ("close_posi", "tion("),
    ("httpx.AsyncHTTPTrans", "port"),
    ("force_release_lo", "ck("),
)


def _this_source() -> str:
    src = inspect.getsource(inspect.getmodule(test_s66_this_suite_touches_nothing_live))
    # Everything from the needle list onward is excluded, so the scan cannot
    # match the list itself.
    return src[:src.index("_FORBIDDEN_FRAGMENTS = (")]


def test_s66_this_suite_touches_nothing_live():
    """No credential read, no real transport, no order mutation, no governance
    call anywhere in this file. Every request is served by `MockTransport` and
    every payload is a literal dict."""
    src = _this_source()
    for head, tail in _FORBIDDEN_FRAGMENTS:
        assert (head + tail) not in src, head + tail

    assert "httpx.MockTransport" in src
    assert src.count("MockTransport") >= 1


def test_s67_every_client_in_this_suite_is_mock_transported():
    """Structural: `_client` is the only constructor of a `DeltaIndiaClient` here,
    and it always installs a `MockTransport`."""
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


def test_s68_the_api_credentials_in_this_suite_are_literal_placeholders():
    """No key or secret is read from the environment, a file, or a fixture."""
    tree = ast.parse(_this_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in ("api_key", "api_secret"):
            assert isinstance(node.value, ast.Constant), ast.unparse(node.value)
            assert node.value.value.startswith(("TEST_", "O7_"))

