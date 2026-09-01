"""
Task O §O10 -- what a REFUSED cancellation is allowed to mean.

§O9 made the client refuse an unsuccessful envelope instead of returning it as
an answer. That turned a silently-wrong success into a raise, and both remaining
cancel sites caught the raise, logged a warning, and carried on as though the
order were gone:

  * `close_position` cancelled the SL and the TP, swallowed either failure, and
    then completed the closure -- archiving the trade, deleting the position and
    RELEASING the single-trade lock. A reduce-only bracket that is still resting
    on the exchange then belongs to nothing: reconciliation builds
    `claimed_order_ids` from `_active_trades` only, so the order becomes an
    orphan, and the freed lock admits a new entry that the stale bracket can
    close at a price no longer related to it.
  * `activate_kill_switch` appended an id to `cancelled_orders` on success only,
    logged a warning on failure, and archived the trade either way. The failure
    was therefore visible ONLY as an absence from a list -- exactly the shape a
    caller reads as "nothing to do".

A refused cancel is not evidence about the order. It is the absence of evidence.
So each refusal is now put back to the exchange (`_classify_refused_cancel`) and
exactly one verdict -- a positively confirmed terminal state -- lets a caller
proceed as if the cancellation had worked. FILLED, still-resting and
unclassifiable all fail closed, and `order_not_found` is NOT read as "gone": the
exchange declining to answer and the order not existing are different claims,
and only the second would justify proceeding.

The two alert codes per site are the distinction that survives into the alert
list: *_UNCONFIRMED means the verification GET answered and the answer was not
terminal; *_UNVERIFIED means the verification GET itself failed.

Zero network access and zero credentials: every request is served by
`httpx.MockTransport` through the real `DeltaIndiaClient.request()` path, so the
signing, status ladder and §O9 envelope guard all execute as in production.
"""

import ast
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

import quantedge.execution.models as execution_models
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
)
from quantedge.execution.models import PositionSide
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    LocalStateStore,
    PositionRecord,
    PositionStatus,
)
from quantedge.execution.trade_lifecycle import (
    CANCEL_OUTCOME_FILLED,
    CANCEL_OUTCOME_GONE,
    CANCEL_OUTCOME_LIVE,
    CANCEL_OUTCOME_UNKNOWN,
    CloseReason,
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.models import TradeDirection

PRODUCTION_ROOT = Path(execution_models.__file__).resolve().parents[1]
LIFECYCLE_SOURCE = PRODUCTION_ROOT / "execution" / "trade_lifecycle.py"

ACCOUNT = "acc_task_o10"
USER = "user_task_o10"
SETUP = "BTCUSD_1h_MANUAL_SMC_O10_LONG"
BTCUSD_PRODUCT_ID = 27
ENTRY_PRICE = Decimal("95000.0")
SL_ID = "8901"
TP_ID = "8902"
ENTRY_ID = "8900"
SIZE = Decimal("3")

# ── Transport plumbing (no network, no credentials) ───────────────────────────


class Recorder:
    """Captures every request a client makes, so the wire can be asserted on."""

    def __init__(self, responder):
        self.requests: List[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    def of(self, method: str, path_prefix: str = "") -> List[httpx.Request]:
        return [
            r for r in self.requests
            if r.method == method and r.url.path.startswith(path_prefix)
        ]

    @staticmethod
    def body(request: httpx.Request) -> Dict[str, Any]:
        return json.loads(request.content.decode())


def _order_json(order_id: int, *, state: str = "open", **over) -> Dict[str, Any]:
    payload = {
        "id": order_id,
        "client_order_id": f"QE-O10-{order_id}",
        "product_id": BTCUSD_PRODUCT_ID,
        "product_symbol": "BTCUSD",
        "user_id": 1,
        "side": "sell",
        "order_type": "limit_order",
        "size": "3",
        "unfilled_size": "3",
        "limit_price": "98000.00",
        "stop_price": None,
        "state": state,
        "reduce_only": True,
        "created_at": 1724261234000000,
    }
    payload.update(over)
    return payload


def _ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _rejected(code: str = "order_not_found") -> httpx.Response:
    """The shape Delta answers a bad order-addressed request with: HTTP 400."""
    return httpx.Response(
        400, json={"success": False, "error": {"code": code, "message": code}}
    )

class Exchange:
    """A MockTransport responder: refuses named cancels, answers order lookups.

    `verify` maps an order id to how the verification GET behaves --
    `("state", "open")` for an answered lookup, `("not_found",)` for the HTTP 400
    an order-addressed endpoint answers an unknown id with, `("unsuccessful",)`
    for the HTTP 200 + `success: false` envelope §O9 refuses, and
    `("malformed",)` for a body with no usable `result`.
    """

    def __init__(self, *, refuse_cancels=(), verify: Optional[Dict[str, Tuple]] = None):
        self.refuse_cancels = {str(i) for i in refuse_cancels}
        self.verify = {str(k): v for k, v in (verify or {}).items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE" and path == "/v2/orders":
            order_id = str(Recorder.body(request).get("id"))
            if order_id in self.refuse_cancels:
                return _rejected()
            return _ok(_order_json(int(order_id), state="cancelled"))
        if request.method == "GET" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            spec = self.verify.get(order_id, ("state", "open"))
            if spec[0] == "state":
                return _ok(_order_json(int(order_id), state=spec[1]))
            if spec[0] == "not_found":
                return _rejected()
            if spec[0] == "unsuccessful":
                return httpx.Response(
                    200, json={"success": False, "error": {"code": "internal_error"}}
                )
            if spec[0] == "malformed":
                return _ok(None)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _client(responder) -> Tuple[DeltaIndiaClient, Recorder]:
    recorder = Recorder(responder)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    client = DeltaIndiaClient(
        api_key="TEST_KEY_TASK_O10_00000000001",
        api_secret="TEST_SECRET_TASK_O10_0000000000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )
    return client, recorder

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


def _record(manager, state: TradeLifecycleState) -> TradeLifecycleRecord:
    record = TradeLifecycleRecord(
        setup_id=SETUP,
        account_id=ACCOUNT,
        user_id=USER,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        requested_quantity=SIZE,
        entry_price=ENTRY_PRICE,
        stop_loss_price=Decimal("94000.0"),
        take_profit_price=Decimal("98000.0"),
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("100"),
        reward_amount=Decimal("300"),
        entry_order_id=ENTRY_ID,
        entry_client_order_id=f"QE_BTCUSD_ENTRY_{SETUP}",
        state=state,
    )
    record.pre_trade_balance = Decimal("10000.00")
    manager._active_trades[SETUP] = record
    manager.single_trade_lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    return record

def _protected(manager, *, sl_order_id: Optional[str] = SL_ID,
               tp_order_id: Optional[str] = TP_ID) -> TradeLifecycleRecord:
    """A live protected position: brackets resting, position held, lock acquired."""
    record = _record(manager, TradeLifecycleState.PROTECTED_POSITION)
    record.filled_quantity = SIZE
    record.protected_quantity = SIZE
    record.sl_order_id = sl_order_id
    record.tp_order_id = tp_order_id
    manager.state_store.positions["BTCUSD"] = PositionRecord(
        symbol="BTCUSD",
        side=PositionSide.LONG,
        quantity=SIZE,
        entry_price=ENTRY_PRICE,
        current_price=Decimal("95500.0"),
        unrealized_pnl=Decimal("1.50"),
        realized_pnl=None,
        leverage=Decimal("10"),
        margin_used=Decimal("28.50"),
        status=PositionStatus.OPEN,
        stop_loss_price=Decimal("94000.0"),
        take_profit_price=Decimal("98000.0"),
    )
    return record


async def _close(manager) -> TradeLifecycleRecord:
    return await manager.close_position(
        setup_id=SETUP,
        reason=CloseReason.MANUAL_CLOSE,
        gross_pnl=Decimal("100.00"),
        trading_fees=Decimal("2.00"),
        final_exchange_balance=Decimal("10098.00"),
    )


def _codes(manager) -> List[str]:
    return [a["code"] for a in manager.reconciliation_alerts]


def _audits(manager, action: str) -> List[Dict[str, Any]]:
    return [
        e for e in manager.state_store.audit_events
        if e.get("action") == action
    ]


def _is_locked(manager) -> bool:
    return manager.single_trade_lock.is_locked(USER, ACCOUNT)[0]

# ══ A. `close_position`: the SL bracket ════════════════════════════════════════


@pytest.mark.asyncio
async def test_a1_sl_cancel_refused_but_verified_cancelled_completes_the_closure():
    """A confirmed terminal state is the ONE verdict that lets the close finish."""
    client, rec = _client(Exchange(refuse_cancels=[SL_ID],
                                  verify={SL_ID: ("state", "cancelled")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.POSITION_CLOSED
    assert SETUP not in manager._active_trades
    assert record in manager._trade_history
    assert "BTCUSD" not in manager.state_store.positions
    assert _is_locked(manager) is False
    assert [c for c in _codes(manager) if c.startswith("PROTECTION_CANCEL_")] == []
    # The refusal was still verified rather than assumed away.
    assert len(rec.of("GET", "/v2/orders/")) == 1


@pytest.mark.asyncio
async def test_a2_sl_still_open_blocks_the_closure_and_retains_everything():
    client, _ = _client(Exchange(refuse_cancels=[SL_ID],
                                 verify={SL_ID: ("state", "open")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert record not in manager._trade_history
    assert "BTCUSD" in manager.state_store.positions
    assert manager.state_store.positions["BTCUSD"].status == PositionStatus.OPEN
    assert record.sl_order_id == SL_ID
    assert record.tp_order_id == TP_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNCONFIRMED"]
    # D1: the accounting the caller supplied still stands.
    assert record.net_pnl == Decimal("98.00")
    assert record.post_trade_balance == Decimal("10098.00")

@pytest.mark.asyncio
async def test_a3_sl_reported_filled_blocks_the_closure_as_unconfirmed():
    """FILLED is an answered lookup, so it is UNCONFIRMED, not UNVERIFIED."""
    client, _ = _client(Exchange(refuse_cancels=[SL_ID],
                                 verify={SL_ID: ("state", "filled")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert "BTCUSD" in manager.state_store.positions
    assert record.sl_order_id == SL_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNCONFIRMED"]
    audit = _audits(manager, "CLOSE_PROTECTION_CANCEL_UNRESOLVED")
    assert len(audit) == 1
    assert audit[0]["details"]["outcome"] == CANCEL_OUTCOME_FILLED
    assert audit[0]["details"]["exchange_state"] == "FILLED"
    assert audit[0]["details"]["verify_error"] is None


@pytest.mark.asyncio
async def test_a4_order_not_found_on_verification_is_unverified_never_gone():
    """`order_not_found` is the exchange declining to answer, not proof of absence."""
    client, _ = _client(Exchange(refuse_cancels=[SL_ID],
                                 verify={SL_ID: ("not_found",)}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert "BTCUSD" in manager.state_store.positions
    assert record.sl_order_id == SL_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNVERIFIED"]
    audit = _audits(manager, "CLOSE_PROTECTION_CANCEL_UNRESOLVED")
    assert audit[0]["details"]["outcome"] == CANCEL_OUTCOME_UNKNOWN
    assert audit[0]["details"]["exchange_state"] is None
    assert audit[0]["details"]["verify_error"]

# ══ B. `close_position`: the TP bracket ════════════════════════════════════════
#
# The SL cancel succeeds in every case below, so these pin the TP branch on its
# own -- a bracket loop that only judged its first iteration would pass A and
# fail here.


@pytest.mark.asyncio
async def test_b1_tp_cancel_refused_but_verified_cancelled_completes_the_closure():
    client, rec = _client(Exchange(refuse_cancels=[TP_ID],
                                  verify={TP_ID: ("state", "cancelled")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.POSITION_CLOSED
    assert SETUP not in manager._active_trades
    assert "BTCUSD" not in manager.state_store.positions
    assert _is_locked(manager) is False
    assert [c for c in _codes(manager) if c.startswith("PROTECTION_CANCEL_")] == []
    assert len(rec.of("DELETE", "/v2/orders")) == 2
    assert len(rec.of("GET", "/v2/orders/")) == 1


@pytest.mark.asyncio
async def test_b2_tp_still_open_blocks_the_closure_and_retains_everything():
    client, _ = _client(Exchange(refuse_cancels=[TP_ID],
                                 verify={TP_ID: ("state", "pending")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert "BTCUSD" in manager.state_store.positions
    assert record.tp_order_id == TP_ID
    assert record.sl_order_id == SL_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNCONFIRMED"]
    audit = _audits(manager, "CLOSE_PROTECTION_CANCEL_UNRESOLVED")
    assert len(audit) == 1
    assert audit[0]["details"]["role"] == "TP"
    assert audit[0]["details"]["order_id"] == TP_ID
    assert audit[0]["details"]["outcome"] == CANCEL_OUTCOME_LIVE

@pytest.mark.asyncio
async def test_b3_tp_reported_filled_blocks_the_closure_as_unconfirmed():
    client, _ = _client(Exchange(refuse_cancels=[TP_ID],
                                 verify={TP_ID: ("state", "filled")}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert record.tp_order_id == TP_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNCONFIRMED"]
    assert _audits(manager, "CLOSE_PROTECTION_CANCEL_UNRESOLVED")[0][
        "details"]["outcome"] == CANCEL_OUTCOME_FILLED


@pytest.mark.asyncio
async def test_b4_tp_verification_failure_is_unverified():
    client, _ = _client(Exchange(refuse_cancels=[TP_ID],
                                 verify={TP_ID: ("not_found",)}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert manager._active_trades.get(SETUP) is record
    assert record.tp_order_id == TP_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNVERIFIED"]


# ══ C. Two refusals are two independent questions ══════════════════════════════


@pytest.mark.asyncio
async def test_c1_both_brackets_refused_are_judged_and_reported_separately():
    """One resting, one unanswerable: both are named, neither is inferred."""
    client, rec = _client(Exchange(
        refuse_cancels=[SL_ID, TP_ID],
        verify={SL_ID: ("state", "open"), TP_ID: ("not_found",)},
    ))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    # Both cancels were attempted -- neither refusal aborted the other.
    assert len(rec.of("DELETE", "/v2/orders")) == 2
    assert len(rec.of("GET", "/v2/orders/")) == 2
    assert _codes(manager) == [
        "PROTECTION_CANCEL_UNCONFIRMED", "PROTECTION_CANCEL_UNVERIFIED",
    ]
    details = [a["details"] for a in _audits(manager, "CLOSE_PROTECTION_CANCEL_UNRESOLVED")]
    assert [d["role"] for d in details] == ["SL", "TP"]
    assert [d["outcome"] for d in details] == [CANCEL_OUTCOME_LIVE, CANCEL_OUTCOME_UNKNOWN]
    assert record.sl_order_id == SL_ID and record.tp_order_id == TP_ID
    assert _is_locked(manager) is True

# ══ D. The verification wire form ══════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d1_one_cancel_and_one_order_addressed_verification_get():
    """The cancel keeps its §O4 body form; the verification addresses ONE order."""
    client, rec = _client(Exchange(refuse_cancels=[SL_ID],
                                  verify={SL_ID: ("state", "open")}))
    manager = _manager(client)
    _protected(manager, tp_order_id=None)

    await _close(manager)

    assert len(rec.requests) == 2, [str(r.url) for r in rec.requests]
    cancel, verify = rec.requests

    assert cancel.method == "DELETE"
    assert cancel.url.path == "/v2/orders"
    body = Recorder.body(cancel)
    assert body == {"id": int(SL_ID), "product_id": BTCUSD_PRODUCT_ID}
    assert isinstance(body["id"], int) and not isinstance(body["id"], bool)

    assert verify.method == "GET"
    assert verify.url.path == f"/v2/orders/{int(SL_ID)}"
    assert verify.url.params.multi_items() == []


# ══ E. `activate_kill_switch` ══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e1_successful_entry_cancel_is_unchanged():
    client, rec = _client(Exchange())
    manager = _manager(client)
    record = _record(manager, TradeLifecycleState.ENTRY_SUBMITTED)

    result = await manager.activate_kill_switch("OPERATOR")

    assert result["kill_switch_active"] is True
    assert manager.state_store.account.kill_switch_active is True
    assert result["cancelled_orders"] == [ENTRY_ID]
    assert result["cancelled_orders_count"] == 1
    assert result["unverified_orders"] == []
    assert result["unverified_count"] == 0
    assert SETUP not in manager._active_trades
    assert record in manager._trade_history
    assert record.state == TradeLifecycleState.KILL_SWITCH_TRIGGERED
    assert _codes(manager) == []
    # No verification is needed when the cancel was not refused.
    assert rec.of("GET", "/v2/orders/") == []

@pytest.mark.asyncio
async def test_e2_refused_entry_cancel_verified_terminal_counts_as_cancelled():
    client, rec = _client(Exchange(refuse_cancels=[ENTRY_ID],
                                  verify={ENTRY_ID: ("state", "cancelled")}))
    manager = _manager(client)
    record = _record(manager, TradeLifecycleState.ENTRY_SUBMITTED)

    result = await manager.activate_kill_switch("OPERATOR")

    assert result["cancelled_orders"] == [ENTRY_ID]
    assert result["cancelled_orders_count"] == 1
    assert result["unverified_orders"] == []
    assert result["unverified_count"] == 0
    assert SETUP not in manager._active_trades
    assert record in manager._trade_history
    assert record.state == TradeLifecycleState.KILL_SWITCH_TRIGGERED
    assert _codes(manager) == []
    assert len(rec.of("GET", "/v2/orders/")) == 1


@pytest.mark.asyncio
async def test_e3_entry_order_still_open_keeps_the_trade_and_names_it_unverified():
    client, _ = _client(Exchange(refuse_cancels=[ENTRY_ID],
                                 verify={ENTRY_ID: ("state", "open")}))
    manager = _manager(client)
    record = _record(manager, TradeLifecycleState.ENTRY_SUBMITTED)

    result = await manager.activate_kill_switch("OPERATOR")

    # The switch still engages: this is never a reason to keep trading.
    assert result["kill_switch_active"] is True
    assert manager.state_store.account.kill_switch_active is True
    # D3: failure is stated, not implied by absence from `cancelled_orders`.
    assert result["cancelled_orders"] == []
    assert result["cancelled_orders_count"] == 0
    assert result["unverified_orders"] == [ENTRY_ID]
    assert result["unverified_count"] == 1
    # D4: the order keeps an owner.
    assert manager._active_trades.get(SETUP) is record
    assert record not in manager._trade_history
    assert record.entry_order_id == ENTRY_ID
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert _is_locked(manager) is True
    assert _codes(manager) == ["KILL_SWITCH_ENTRY_CANCEL_UNCONFIRMED"]
    audit = _audits(manager, "KILL_SWITCH_ENTRY_CANCEL_UNRESOLVED")
    assert len(audit) == 1
    assert audit[0]["details"]["outcome"] == CANCEL_OUTCOME_LIVE
    assert audit[0]["details"]["order_id"] == ENTRY_ID

@pytest.mark.asyncio
async def test_e4_entry_order_reported_filled_keeps_the_trade_tracked():
    """A filled entry is a position this switch has not accounted for."""
    client, _ = _client(Exchange(refuse_cancels=[ENTRY_ID],
                                 verify={ENTRY_ID: ("state", "filled")}))
    manager = _manager(client)
    record = _record(manager, TradeLifecycleState.ENTRY_SUBMITTED)

    result = await manager.activate_kill_switch("OPERATOR")

    assert result["kill_switch_active"] is True
    assert result["cancelled_orders"] == []
    assert result["unverified_orders"] == [ENTRY_ID]
    assert result["unverified_count"] == 1
    assert manager._active_trades.get(SETUP) is record
    assert record not in manager._trade_history
    assert record.entry_order_id == ENTRY_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["KILL_SWITCH_ENTRY_CANCEL_UNCONFIRMED"]
    assert _audits(manager, "KILL_SWITCH_ENTRY_CANCEL_UNRESOLVED")[0][
        "details"]["outcome"] == CANCEL_OUTCOME_FILLED


@pytest.mark.asyncio
async def test_e5_entry_verification_failure_is_unverified_and_still_tracked():
    client, _ = _client(Exchange(refuse_cancels=[ENTRY_ID],
                                 verify={ENTRY_ID: ("not_found",)}))
    manager = _manager(client)
    record = _record(manager, TradeLifecycleState.ENTRY_SUBMITTED)

    result = await manager.activate_kill_switch("OPERATOR")

    assert result["cancelled_orders"] == []
    assert result["unverified_orders"] == [ENTRY_ID]
    assert result["unverified_count"] == 1
    assert manager._active_trades.get(SETUP) is record
    assert record.entry_order_id == ENTRY_ID
    assert _is_locked(manager) is True
    assert _codes(manager) == ["KILL_SWITCH_ENTRY_UNVERIFIED"]
    details = _audits(manager, "KILL_SWITCH_ENTRY_CANCEL_UNRESOLVED")[0]["details"]
    assert details["outcome"] == CANCEL_OUTCOME_UNKNOWN
    assert details["exchange_state"] is None
    assert details["verify_error"]
    # The switch-level audit names the unresolved order too, so a reader of the
    # audit trail alone cannot mistake omission for success.
    switch_audit = _audits(manager, "KILL_SWITCH_ACTIVATED")[0]["details"]
    assert switch_audit["cancelled_orders"] == []
    assert switch_audit["unverified_orders"] == [ENTRY_ID]
    assert switch_audit["unverified_count"] == 1

# ══ F. The verdict mapping ═════════════════════════════════════════════════════
#
# Every state below is one `OrderStatus.from_exchange` already models -- §O5 gave
# it no fallback, so a name this engine does not model raises rather than
# arriving here as a verdict. No exchange state is invented by these tests.


@pytest.mark.asyncio
@pytest.mark.parametrize("exchange_state,expected", [
    ("cancelled", CANCEL_OUTCOME_GONE),
    ("canceled", CANCEL_OUTCOME_GONE),
    # EXPIRED is in the terminal pair `_cancel_existing_brackets` already accepts
    # and the pair `_expire_entry_order` finalises on; it is not assumed here.
    ("expired", CANCEL_OUTCOME_GONE),
    ("filled", CANCEL_OUTCOME_FILLED),
    ("closed", CANCEL_OUTCOME_FILLED),
    ("open", CANCEL_OUTCOME_LIVE),
    ("pending", CANCEL_OUTCOME_LIVE),
    ("partially_filled", CANCEL_OUTCOME_LIVE),
    # REJECTED is deliberately NOT read as gone: it is outside the terminal pair
    # the bracket path accepts, so it fails closed rather than being assumed.
    ("rejected", CANCEL_OUTCOME_UNKNOWN),
])
async def test_f1_reported_state_maps_to_exactly_one_verdict(exchange_state, expected):
    client, _ = _client(Exchange(verify={SL_ID: ("state", exchange_state)}))
    manager = _manager(client)

    outcome, state_name, verify_error = await manager._classify_refused_cancel(SL_ID)

    assert outcome == expected
    assert state_name is not None
    assert verify_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [("not_found",), ("unsuccessful",), ("malformed",)])
async def test_f2_a_verification_that_did_not_answer_is_unknown(mode):
    """No default, no salvage: a failed lookup is UNKNOWN, never GONE."""
    client, _ = _client(Exchange(verify={SL_ID: mode}))
    manager = _manager(client)

    outcome, state_name, verify_error = await manager._classify_refused_cancel(SL_ID)

    assert outcome == CANCEL_OUTCOME_UNKNOWN
    assert state_name is None
    assert verify_error

# ══ G. The invariants that hold across every non-GONE verdict ══════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("verify_mode", [
    ("state", "open"),
    ("state", "pending"),
    ("state", "partially_filled"),
    ("state", "filled"),
    ("state", "rejected"),
    ("not_found",),
    ("unsuccessful",),
    ("malformed",),
])
async def test_g1_no_non_gone_verdict_releases_the_lock_or_clears_the_ids(verify_mode):
    client, _ = _client(Exchange(refuse_cancels=[SL_ID], verify={SL_ID: verify_mode}))
    manager = _manager(client)
    record = _protected(manager)

    result = await _close(manager)

    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert _is_locked(manager) is True
    assert record.sl_order_id == SL_ID
    assert record.tp_order_id == TP_ID
    assert manager._active_trades.get(SETUP) is record
    assert "BTCUSD" in manager.state_store.positions
    assert len(_codes(manager)) == 1
    assert _codes(manager)[0] in (
        "PROTECTION_CANCEL_UNCONFIRMED", "PROTECTION_CANCEL_UNVERIFIED",
    )


@pytest.mark.asyncio
async def test_g2_an_unusable_order_id_fails_closed_without_touching_the_wire():
    """`int()` on a non-numeric id raises locally; that is still not "gone"."""
    client, rec = _client(Exchange())
    manager = _manager(client)
    record = _protected(manager, sl_order_id="not-an-id", tp_order_id=None)

    result = await _close(manager)

    assert rec.requests == []
    assert result.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert record.sl_order_id == "not-an-id"
    assert _is_locked(manager) is True
    assert _codes(manager) == ["PROTECTION_CANCEL_UNVERIFIED"]

# ══ H. Static safety: no cancel site may swallow the refusal again ═════════════


def _function(name: str) -> ast.AST:
    tree = ast.parse(LIFECYCLE_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {LIFECYCLE_SOURCE}")


def _is_log_only(handler: ast.ExceptHandler) -> bool:
    """True when a handler does nothing but log (or nothing at all)."""
    for stmt in handler.body:
        if isinstance(stmt, ast.Pass):
            continue
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and isinstance(stmt.value.func.value, ast.Name)
                and stmt.value.func.value.id == "logger"):
            continue
        return False
    return True


@pytest.mark.parametrize("site", ["close_position", "activate_kill_switch"])
def test_h1_neither_cancel_site_has_a_log_and_continue_exception_handler(site):
    node = _function(site)
    offenders = [
        h.lineno for h in ast.walk(node)
        if isinstance(h, ast.ExceptHandler) and _is_log_only(h)
    ]
    assert offenders == [], (
        f"{site} still absorbs an exception into a log line at "
        f"{LIFECYCLE_SOURCE}:{offenders}"
    )


@pytest.mark.parametrize("site", ["close_position", "activate_kill_switch"])
def test_h2_every_cancel_site_verifies_a_refusal(site):
    node = _function(site)
    calls = {
        n.func.attr for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "cancel_order" in calls
    assert "_classify_refused_cancel" in calls
