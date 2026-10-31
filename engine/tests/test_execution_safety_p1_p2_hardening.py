"""
Execution-safety hardening: the P1 and P2 findings of the read-only audit.

Every test here is served by `httpx.MockTransport` and a temporary directory.
No credential is read, no live endpoint is contacted, no order leaves the
process, and nothing composes production execution.

The fixtures are IMPORTED from `test_execution_identity_and_startup_gate` rather
than re-declared. That module already pins the P0 layer (deterministic identity,
durable state, the startup gate) against exactly these shapes, and a second copy
of `FakeExchange` / `_protected_record` would let the two drift until the same
name meant two different worlds.

What each section is for, and what was actually wrong before it:

  A. P1-1 OPPOSITE-SIDE POSITION MATCHING. `DeltaPosition` stores `abs(size)`
     with the sign carried in `side`, and reconciliation matched on product id
     alone -- so a local LONG "found" an exchange SHORT of identical size and
     reported the account reconciled. It then protects that short with a sell
     stop BELOW it: an opening order that doubles the position.

  B. P1-2 COMPLETE EXCHANGE INVENTORY. Four consumers act on a listing as if it
     were the whole account: the synchronizer CLOSES local positions missing
     from it, reconciliation CLEARS blocking alerts, the lock is force-released
     when the book looks flat, and the pre-trade gate AUTHORIZES on no exposure.
     One truncated page is therefore indistinguishable from a flat account.

  C. P1-3 AUTHORIZATION FRESHNESS. A clean `arm()` was permanent. Exchange and
     local state move afterwards; a verdict from before the move is not an
     authorization for what is there now.

  D. P1-4 RECONCILIATION/ENTRY CONCURRENCY. An admitted submission holds no lock
     while it awaits the exchange and its record does not exist yet, so a
     reconciliation run overlapping it sees neither the local record nor the
     order the exchange may already hold -- and would compute a clean world and
     clear the alerts protecting the account.

  E. P1-5 AMBIGUOUS SUBMISSION. A timeout or dropped connection AFTER the POST
     means the order may exist. That is a first-class safety condition, not a
     log line, and it must survive a restart.

  F. P1-6/P2-6 CANONICAL ADMISSION. Exactly one production submission identity
     path, and no way for a legacy path to submit around the contract.

  G. P1-7 TRANSITION/COMPOSITE INTEGRITY. Persisted state must not be able to
     claim a terminal state while still carrying live protection, and a body
     that contradicts itself must fail closed rather than be repaired.

  H-L. P2-1 product identity, P2-2 protection provenance, P2-3 store atomicity,
     P2-4 account identity, P2-5 no synthetic execution-critical values.
"""

import asyncio
import ast
import copy
import dataclasses
import inspect
import json
import os
import textwrap
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

import quantedge.execution.trade_lifecycle as trade_lifecycle_module
import quantedge.execution.execution_state_store as execution_state_store_module
from quantedge.execution.delta_client import (
    MAX_LISTING_PAGES,
    DeltaConnectionError,
    DeltaIndiaClient,
    DeltaListingIncompleteError,
    DeltaResponseError,
    generate_deterministic_client_order_id,
)
from quantedge.execution.entry_admission import (
    CANONICAL_ENTRY_SUBMISSION_PATHS,
    MANAGER_GATE_ATTRIBUTE,
    QUARANTINED_SUBMISSION_PATHS,
    EntryAdmissionError,
    assert_entry_admission_contract_intact,
    install_entry_admission,
)
from quantedge.execution.execution_state_store import (
    EXECUTION_STATE_SCHEMA_VERSION,
    ExecutionStateCorruptError,
    ExecutionStateIncompatibleError,
    ExecutionStatePersistenceError,
    export_execution_state,
    load_execution_state,
    persist_execution_state,
    restore_execution_state,
    restore_execution_state_from_file,
    serialize_execution_state_body,
    write_execution_state,
)
from quantedge.execution.models import PositionSide
from quantedge.execution.startup_gate import (
    GATE_REFUSAL_CODE,
    NOT_ARMED_REASON,
    STALE_AUTHORIZATION_REASON,
    ExecutionStartupGate,
    ExecutionStartupGateError,
)
from quantedge.execution.trade_lifecycle import (
    MAX_PROTECTION_GENERATION,
    ORDER_ROLE_ENTRY,
    ORDER_ROLE_STOP_LOSS,
    ORDER_ROLE_TAKE_PROFIT,
    PROTECTION_SOURCE_ENGINE,
    PROTECTION_SOURCE_EXCHANGE_ADOPTED,
    PROTECTION_SOURCES,
    REFUSAL_MISSING_ENTRY_PRICE,
    REFUSAL_MISSING_LEVERAGE,
    REFUSAL_MISSING_QUANTITY,
    CloseReason,
    ProductIdentityMismatchError,
    TradeDirection,
    TradeLifecycleManager,
    TradeLifecycleState,
    protection_role,
)
from quantedge.instruments import delta_india_registry
from quantedge.execution.validation import RejectionReasonCode

from test_execution_identity_and_startup_gate import (  # noqa: E402
    ACCOUNT,
    ENTRY_ORDER_ID,
    ENTRY_PRICE,
    LEVERAGE,
    OTHER_ACCOUNT,
    PRODUCT_ID,
    QUANTITY,
    SETUP,
    SL_ORDER_ID,
    STOP_LOSS,
    SYMBOL,
    TAKE_PROFIT,
    TP_ORDER_ID,
    USER,
    FakeExchange,
    _FakeRuntime,
    _clean_world,
    _decision,
    _flat_state,
    _gate,
    _live_state,
    _manager,
    _ok,
    _order_json,
    _position_json,
    _protected_record,
    _store,
)

#: A second real instrument, so a two-page listing carries two DISTINCT row
#: identities (positions are keyed by product id) rather than one repeated one.
OTHER_SYMBOL = "ETHUSD"
OTHER_PRODUCT_ID = delta_india_registry().get(OTHER_SYMBOL).product_id


# ── Shared helpers ────────────────────────────────────────────────────────────


def _alerts(manager: TradeLifecycleManager, code: str) -> List[Dict[str, Any]]:
    """Every raised alert carrying `code`, so a test can assert on its context."""
    return [a for a in manager.reconciliation_alerts if a.get("code") == code]


def _function_ast(func: Any) -> ast.AST:
    """The parsed source of one function, for claims prose must not be trusted on."""
    return ast.parse(textwrap.dedent(inspect.getsource(func)))


def _refusal(error: type, call: Any, *args: Any) -> str:
    """The message `call(*args)` refuses with, so a test can assert on its words.

    The message IS part of the safety contract here: an operator reading it has
    to be able to tell "this state contradicts itself and was not repaired" from
    "this file is from a different schema".
    """
    with pytest.raises(error) as excinfo:
        call(*args)
    return str(excinfo.value)


def _called_names(tree: ast.AST) -> set:
    """Every name called anywhere in a parsed function body."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


class _RefusingContract:
    """The smallest thing that satisfies the admission contract: it always refuses."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.calls = 0

    def admission_reason(self) -> Optional[str]:
        self.calls += 1
        return self.reason


def _writes(exchange: FakeExchange) -> List[str]:
    """Every request that would CHANGE exchange state.

    P1-1/P1-7 both require that a contradiction is reported and nothing is
    repaired, so "no reversal, closure or adoption was attempted" has to be
    asserted against the wire rather than against the summary.
    """
    return [
        f"{r.method} {r.url.path}"
        for r in exchange.requests
        if r.method in ("POST", "PUT", "DELETE", "PATCH")
    ]


def _opposite_side_world(size: str = "-1") -> FakeExchange:
    """`_clean_world`, except the position is the OTHER direction.

    `_position_json(size="-1")` is how Delta states a short: `DeltaPosition`
    keeps `abs(size)` and puts the sign in `side`. The absolute size therefore
    still equals the protected quantity, which is precisely the case that used
    to reconcile clean.
    """
    return FakeExchange(
        positions=[_position_json(size=size)],
        open_orders=[
            _order_json(order_id=SL_ORDER_ID, state="pending", reduce_only=True),
            _order_json(order_id=TP_ORDER_ID, reduce_only=True),
        ],
    )


class _PagedListing:
    """A listing endpoint that serves a fixed script of pages, in order.

    Pages are `(rows, meta)`; `meta is None` sends no `meta` key at all, which
    is what a single-page Delta response looks like. The `after` query parameter
    of every request is recorded, so a test can assert the client forwarded the
    previous page's cursor verbatim rather than merely asking twice.
    """

    def __init__(
        self,
        pages: Optional[List[Tuple[List[Any], Optional[Dict[str, Any]]]]] = None,
        *,
        factory: Any = None,
    ) -> None:
        self.pages = list(pages or [])
        self.factory = factory
        self.cursors: List[Optional[str]] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        index = len(self.cursors)
        self.cursors.append(request.url.params.get("after"))
        if self.factory is not None:
            rows, meta = self.factory(index)
        else:
            assert index < len(self.pages), (
                f"the client asked for page {index + 1} but only "
                f"{len(self.pages)} page(s) are scripted"
            )
            rows, meta = self.pages[index]
        body: Dict[str, Any] = {"success": True, "result": list(rows)}
        if meta is not None:
            body["meta"] = meta
        return httpx.Response(200, json=body)


class _PagedExchange(FakeExchange):
    """`FakeExchange` with either listing replaced by a scripted `_PagedListing`."""

    def __init__(
        self,
        *,
        positions_listing: Optional[_PagedListing] = None,
        orders_listing: Optional[_PagedListing] = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.positions_listing = positions_listing
        self.orders_listing = orders_listing

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            if path == "/v2/positions/margined" and self.positions_listing:
                self.requests.append(request)
                return self.positions_listing.respond(request)
            if path == "/v2/orders" and self.orders_listing:
                self.requests.append(request)
                return self.orders_listing.respond(request)
        return super().__call__(request)


# ══ A. P1-1: a position must match on DIRECTION, not just on size ═════════════


@pytest.mark.asyncio
async def test_a1_a_local_long_cannot_be_reconciled_by_an_exchange_short():
    """The exact case that used to report RECONCILIATION_CLEAN.

    Same product, same absolute size, opposite direction. Product-only matching
    saw "a position of 1 on product 27" and called the setup converged. It is
    not the same position, and the difference is the whole trade: the recorded
    LONG's protective stop is a SELL below the entry, which against a SHORT is
    an opening order that doubles the exposure.
    """
    exchange = _opposite_side_world()
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert SETUP in summary["unresolved"], summary
    assert summary["converged"] == [], summary
    assert summary["protection_restored"] == [], summary
    mismatch = _alerts(manager, "POSITION_SIDE_MISMATCH")
    assert len(mismatch) == 1, manager.reconciliation_alerts
    assert mismatch[0]["symbol"] == SYMBOL
    assert manager._active_trades[SETUP].state == (
        TradeLifecycleState.RECONCILIATION_REQUIRED
    )
    # And nothing was reversed, closed, adopted or protected.
    assert _writes(exchange) == [], _writes(exchange)


@pytest.mark.asyncio
async def test_a2_a_local_short_cannot_be_reconciled_by_an_exchange_long():
    """The mirror image, because the sign lives in one place and could be read
    the right way round by accident for one direction only."""
    exchange = FakeExchange(
        positions=[_position_json(size="1")],
        open_orders=[
            _order_json(order_id=SL_ORDER_ID, state="pending", reduce_only=True),
            _order_json(order_id=TP_ORDER_ID, reduce_only=True),
        ],
    )
    manager = _manager(exchange)
    record = _protected_record()
    record.direction = TradeDirection.SHORT
    manager._active_trades[SETUP] = record

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert SETUP in summary["unresolved"], summary
    assert len(_alerts(manager, "POSITION_SIDE_MISMATCH")) == 1
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert _writes(exchange) == []


@pytest.mark.asyncio
async def test_a3_the_contradicted_position_is_also_reported_as_unattributed():
    """Both halves of the contradiction are reported, not just the record's.

    Marking the setup unresolved would still leave the exchange position
    silently accounted for. It is not accounted for by anything: the product is
    left unclaimed so the orphan sweep reports it, and the alert names the local
    setups that claim the opposite direction so an operator can see the pair.
    """
    exchange = _opposite_side_world()
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert SYMBOL in summary["orphan_positions"], summary
    orphan = _alerts(manager, "ORPHAN_EXCHANGE_POSITION")
    assert len(orphan) == 1, manager.reconciliation_alerts
    assert SETUP in orphan[0]["details"]


class _CancellingExchange(FakeExchange):
    """`FakeExchange` that also answers the documented `DELETE /v2/orders`.

    Needed only by the same-side resize path, which cancels the resting pair
    before placing a correctly sized one. Kept out of `FakeExchange` itself so
    the modules that must never cancel still fail loudly if they try.
    """

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE" and request.url.path == "/v2/orders":
            self.requests.append(request)
            body = json.loads(request.content.decode())
            self.cancelled.append(body)
            return httpx.Response(200, json={"success": True, "result": body})
        return super().__call__(request)

    @property
    def cancelled(self) -> List[Dict[str, Any]]:
        if not hasattr(self, "_cancelled"):
            self._cancelled: List[Dict[str, Any]] = []
        return self._cancelled


@pytest.mark.asyncio
async def test_a4_a_same_side_position_of_the_protected_size_still_converges():
    """The regression guard: the fix must not make a legitimate match unclean.

    Same product, same direction, same size, both legs resting. This is the one
    world in which reconciliation is allowed to say the account is clean, and it
    still does. Cleanliness is asserted through the only observable it has --
    positive exchange confirmation is the sole route by which a blocking alert is
    ever cleared -- rather than by reading a list that a healthy account leaves
    empty for two different reasons.
    """
    exchange = _clean_world()
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()
    manager._raise_reconciliation_alert("PRIOR_CONDITION", SYMBOL, "seeded by the test")

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["checked"] == 1, summary
    assert summary["unresolved"] == [], summary
    assert summary["orphan_positions"] == [], summary
    assert summary["orphan_orders"] == [], summary
    assert summary.get("alerts_cleared") == 1, summary
    assert manager.reconciliation_alerts == []
    assert manager._active_trades[SETUP].state == (
        TradeLifecycleState.PROTECTED_POSITION
    )
    assert _writes(exchange) == []


@pytest.mark.asyncio
async def test_a5_a_same_side_size_disagreement_still_takes_the_protection_path():
    """A quantity difference is a protection finding, not a side finding.

    The side check is an ADDITIONAL gate in front of the existing size logic, so
    the case it must not swallow is the one that differs only in size: that still
    resizes protection through `_ensure_bracket_protection` exactly as before,
    and raises no `POSITION_SIDE_MISMATCH`.
    """
    exchange = _CancellingExchange(
        positions=[_position_json(size="2")],
        open_orders=[
            _order_json(order_id=SL_ORDER_ID, state="pending", reduce_only=True),
            _order_json(order_id=TP_ORDER_ID, reduce_only=True),
        ],
    )
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert _alerts(manager, "POSITION_SIDE_MISMATCH") == []
    assert SETUP in summary["protection_restored"], summary
    assert manager._active_trades[SETUP].protected_quantity == Decimal("2")
    # The resize really went through the exchange: the pair was cancelled and a
    # correctly sized pair placed. That is the pre-existing behaviour.
    assert len(exchange.cancelled) == 2, exchange.cancelled
    assert len(exchange.placed) == 2, exchange.placed


@pytest.mark.asyncio
async def test_a6_the_startup_gate_blocks_on_an_opposite_side_position(tmp_path):
    """The finding has to reach the boundary that stops trading, not just a log.

    A restart into this world is the dangerous case: the file says LONG, the
    exchange holds SHORT, and the old matcher would have armed the gate clean and
    admitted a new entry on top of a position it had misread.
    """
    path = _live_state(tmp_path / "state.json")
    manager = _manager(_opposite_side_world())
    # A runtime that AGREES with the persisted record, so the only disagreement
    # left for the gate to find is the one this test is about.
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    result = await gate.arm()

    assert result.admitted is False
    assert "did not return clean" in result.block_reason, result.block_reason
    assert SETUP in result.block_reason, result.block_reason
    assert _alerts(manager, "POSITION_SIDE_MISMATCH"), manager.reconciliation_alerts
    assert gate.authorization_current is False
    record = await manager.execute_trade_setup(
        _decision(setup_id="BTCUSD_1h_MANUAL_SMC_IDENT_LONG_2"), ACCOUNT, user_id=USER
    )
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == GATE_REFUSAL_CODE


# ══ B. P1-2: a listing is the COMPLETE inventory, or it is refused ════════════


@pytest.mark.asyncio
async def test_b1_a_two_page_position_listing_is_followed_to_exhaustion():
    """Both pages are returned, and the second request carries the first's cursor.

    Asserting the returned rows alone would pass for a client that asked twice
    with no cursor and got lucky. The cursor trail is the part that proves the
    listing was actually paged rather than sampled twice.
    """
    listing = _PagedListing([
        ([_position_json()], {"after": "cursor-1"}),
        ([_position_json(product_id=OTHER_PRODUCT_ID, product_symbol=OTHER_SYMBOL)],
         {"after": None}),
    ])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    positions = await client.get_positions()

    assert [p.product_symbol for p in positions] == [SYMBOL, OTHER_SYMBOL]
    assert listing.cursors == [None, "cursor-1"]


@pytest.mark.asyncio
async def test_b2_a_two_page_order_listing_keeps_the_callers_filter_on_page_two():
    """Paging must move the cursor and nothing else.

    `get_open_orders` asks for `states=open,pending` because an untriggered stop
    rests in `pending` and reconciliation decides `sl_live` from this list. If
    page two were requested with the cursor but without the states filter, the
    second page would answer a different question from the first -- and healthy
    protection could be reported as missing.
    """
    listing = _PagedListing([
        ([_order_json(order_id=SL_ORDER_ID, state="pending")], {"after": "ord-1"}),
        ([_order_json(order_id=TP_ORDER_ID)], None),
    ])
    exchange = _PagedExchange(orders_listing=listing)
    client = _manager(exchange).client

    orders = await client.get_open_orders()

    assert sorted(int(o.id) for o in orders) == [SL_ORDER_ID, TP_ORDER_ID]
    assert listing.cursors == [None, "ord-1"]
    states = [r.url.params.get("states") for r in exchange.requests]
    assert states == ["open,pending", "open,pending"], states


@pytest.mark.asyncio
async def test_b3_a_position_that_exists_only_on_page_two_is_discovered():
    """The exact shape of the old bug: page one is empty, so the account looked flat.

    Four consumers read flatness as authorisation -- the synchronizer closes
    local positions, reconciliation clears blocking alerts, the lock is
    force-released, the pre-trade gate admits a new order. An empty first page
    with a cursor is not flatness.
    """
    listing = _PagedListing([
        ([], {"after": "cursor-1"}),
        ([_position_json()], None),
    ])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    positions = await client.get_positions()

    assert len(positions) == 1
    assert positions[0].product_symbol == SYMBOL
    assert listing.cursors == [None, "cursor-1"]


@pytest.mark.asyncio
async def test_b4_a_re_issued_cursor_fails_closed_instead_of_looping():
    """An exchange that hands back a cursor it already gave is not advancing.

    Two answers are available and both are wrong: loop forever, or stop and
    report what arrived so far as the account. The third is to refuse, which is
    what happens -- and the rows already collected are discarded with it.
    """
    listing = _PagedListing([
        ([_position_json()], {"after": "cursor-1"}),
        ([], {"after": "cursor-1"}),
    ])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="re-issued cursor"):
        await client.get_positions()


@pytest.mark.asyncio
async def test_b5_a_repeated_row_fails_closed_instead_of_being_de_duplicated():
    """The same product on two pages means the paging did not advance.

    De-duplicating would be the silent option: the listing would look complete
    because the duplicate vanished. One product cannot hold two distinct margined
    positions, so a repeat is evidence about the paging, not about the account.
    """
    listing = _PagedListing([
        ([_position_json()], {"after": "cursor-1"}),
        ([_position_json()], None),
    ])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="repeated row"):
        await client.get_positions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        {"has_more": True},
        {"next_cursor": "abc"},
        {"page": 2},
        {"last_evaluated_key": {"id": 1}},
    ],
)
async def test_b6_an_unfollowable_more_rows_signal_fails_closed(meta):
    """"There is more" in a dialect this repository cannot follow is a refusal.

    Guessing the parameter name for any of these would be inventing exchange
    semantics. The listing is refused instead, which is the fail-closed
    direction: a blocked reconciliation stops trading, a partial one authorises
    it.
    """
    listing = _PagedListing([([_position_json()], meta)])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="pagination field"):
        await client.get_positions()


@pytest.mark.asyncio
async def test_b7_a_non_string_cursor_fails_closed():
    """The documented cursor is an opaque string; an int is not a known dialect."""
    listing = _PagedListing([([_position_json()], {"after": 2})])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="opaque string"):
        await client.get_positions()


@pytest.mark.asyncio
async def test_b8_an_unfiltered_listing_that_undercounts_itself_fails_closed():
    """`total_count=2` with one row and no next page is a truncated listing.

    This is the case with no cursor to follow at all: the exchange offered no
    further page yet says more rows exist. Returning the one row would report a
    one-position account, which is a different account.
    """
    listing = _PagedListing([([_position_json()], {"total_count": 2})])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="total_count=2"):
        await client.get_positions()


@pytest.mark.asyncio
async def test_b9_total_count_is_deliberately_not_consulted_for_a_filtered_query():
    """A filtered query does not use `total_count`, and that is a decision.

    Whether Delta's `total_count` describes the filtered set or the whole account
    is not established by this repository. Reading it as filtered-scope could
    block reconciliation permanently on a number that was never about the filter,
    so it is used only where both readings agree: the unfiltered listing. This
    test exists so the omission stays deliberate rather than becoming a bug
    someone "fixes" by deleting the `unfiltered` guard.
    """
    listing = _PagedListing([([_position_json()], {"total_count": 99})])
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    positions = await client.get_positions(product_ids=[PRODUCT_ID])

    assert len(positions) == 1
    assert exchange.requests[0].url.params.get("product_ids") == str(PRODUCT_ID)


@pytest.mark.asyncio
async def test_b10_an_endlessly_paging_exchange_is_refused_at_the_page_budget():
    """Bounded paging, and the bound is a refusal rather than a truncation.

    Each page here is well-formed and offers a fresh cursor, so nothing else in
    the loop can stop it. `MAX_LISTING_PAGES` stops it, and what it returns is an
    exception -- not the rows collected so far, which is the answer that would
    look like a complete small account.
    """
    listing = _PagedListing(factory=lambda i: (
        [_position_json(product_id=1000 + i, product_symbol=f"P{i}")],
        {"after": f"cursor-{i}"},
    ))
    exchange = _PagedExchange(positions_listing=listing)
    client = _manager(exchange).client

    with pytest.raises(DeltaListingIncompleteError, match="still offering further"):
        await client.get_positions()

    assert len(listing.cursors) == MAX_LISTING_PAGES


@pytest.mark.asyncio
async def test_b11_a_single_page_listing_is_unchanged_and_asks_exactly_once():
    """The overwhelmingly common case must not have grown a second request.

    A single-page account sends no `after` at all: pagination is invisible unless
    the exchange asks for it.
    """
    exchange = FakeExchange(positions=[_position_json()], open_orders=[_order_json()])
    client = _manager(exchange).client

    positions = await client.get_positions()
    orders = await client.get_open_orders()

    assert len(positions) == 1 and len(orders) == 1
    assert exchange.reads() == ["GET /v2/positions/margined", "GET /v2/orders"]
    assert all(r.url.params.get("after") is None for r in exchange.requests)


@pytest.mark.asyncio
async def test_b12_a_truncated_listing_cannot_produce_a_clean_reconciliation():
    """The consequence that matters: an unprovable listing blocks, it does not clear.

    `DeltaListingIncompleteError` subclasses `DeltaResponseError`, so every
    existing caller's fail-closed path already handles it. Here that means the
    run reports `EXCHANGE_UNREACHABLE`, raises its own blocking alert, and leaves
    the pre-existing alert standing -- the opposite of the clean run in A4, which
    cleared it.
    """
    listing = _PagedListing([([_position_json()], {"has_more": True})])
    exchange = _PagedExchange(positions_listing=listing)
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()
    manager._raise_reconciliation_alert("PRIOR_CONDITION", SYMBOL, "seeded by the test")

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert "EXCHANGE_UNREACHABLE" in summary["unresolved"], summary
    assert summary.get("exchange_unreachable") is True, summary
    assert "alerts_cleared" not in summary, summary
    assert _alerts(manager, "PRIOR_CONDITION"), manager.reconciliation_alerts
    assert _alerts(manager, "EXCHANGE_UNREACHABLE")


# ══ C. P1-3: a clean startup verdict is not a permanent authorization ═════════


@pytest.mark.asyncio
async def test_c1_a_clean_arm_authorizes_the_next_entry(tmp_path):
    """The baseline the rest of this section is measured against.

    Nothing local, nothing on the exchange, so reconciliation is clean, the
    fingerprint is captured, and the entry path is admitted. If this did not
    pass, every staleness test below would be passing for the wrong reason.
    """
    path = _flat_state(tmp_path / "state.json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())

    result = await gate.arm()

    assert result.admitted is True, result.block_reason
    assert gate.authorization_current is True
    assert gate.admission_reason() is None
    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)
    assert record.state != TradeLifecycleState.ENTRY_REJECTED, record.error_message


@pytest.mark.asyncio
async def test_c2_the_state_change_a_submission_makes_invalidates_the_authorization(
    tmp_path,
):
    """An admitted entry is itself an execution-state change, so it expires the verdict.

    This is the whole of P1-3 in one sequence. The gate armed against an empty
    book; the moment an entry exists, the reconciliation that authorized it
    describes a book that no longer exists. The second entry is refused by the
    GATE -- checked before the single-trade lock, so the refusal code proves
    which authority stopped it -- and the message names staleness rather than
    some generic error.
    """
    path = _flat_state(tmp_path / "state.json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True

    first = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)
    assert first.state != TradeLifecycleState.ENTRY_REJECTED, first.error_message

    assert gate.authorization_current is False
    assert gate.admission_reason() == STALE_AUTHORIZATION_REASON
    second = await manager.execute_trade_setup(
        _decision(setup_id=SETUP + "_2"), ACCOUNT, user_id=USER
    )
    assert second.state == TradeLifecycleState.ENTRY_REJECTED
    assert second.rejection_code == GATE_REFUSAL_CODE
    assert STALE_AUTHORIZATION_REASON in second.error_message


@pytest.mark.asyncio
async def test_c3_a_local_execution_state_mutation_invalidates_it_too(tmp_path):
    """Freshness is structural, so it does not care WHO changed the state.

    A record restored by another path, a fill applied from the private stream, an
    alert raised by a periodic reconciliation: none of them go through the entry
    path, and all of them change what an entry would be entering into.
    """
    path = _flat_state(tmp_path / "state.json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True

    manager._active_trades[SETUP] = _protected_record()

    assert gate.admission_reason() == STALE_AUTHORIZATION_REASON


@pytest.mark.asyncio
async def test_c4_repeated_checks_against_unchanged_state_are_idempotent(tmp_path):
    """Nothing changed, so nothing changes: the same verdict, computed the same way.

    `admission_reason()` is called on every entry, so it must be side-effect free
    -- it must not mutate the recorded verdict, and it must not contact the
    exchange (it is synchronous, and the request log proves it stayed off the
    wire).
    """
    path = _flat_state(tmp_path / "state.json")
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True
    reads_after_arm = list(exchange.reads())

    verdicts = [gate.admission_reason() for _ in range(25)]

    assert verdicts == [None] * 25
    assert gate.result.admitted is True
    assert exchange.reads() == reads_after_arm


@pytest.mark.asyncio
async def test_c5_an_unarmed_gate_is_not_current_and_names_that_reason(tmp_path):
    """"Never armed" and "armed but stale" are different refusals, both blocking."""
    path = _flat_state(tmp_path / "state.json")
    gate = _gate(_manager(FakeExchange()), path, _FakeRuntime())

    assert gate.authorization_current is False
    assert gate.admission_reason() == NOT_ARMED_REASON


@pytest.mark.asyncio
async def test_c6_a_blocked_arm_can_never_later_look_fresh(tmp_path):
    """A refused arm stores no fingerprint, so it cannot be satisfied by accident.

    Without this, a gate blocked at 09:00 could be "re-validated" by any later
    state change that happened to reproduce the fingerprint it was carrying --
    freshness would then be arguing about the wrong verdict entirely. `_block`
    clears it, so the refusal survives every subsequent mutation.
    """
    path = _live_state(tmp_path / "state.json")
    manager = _manager(_opposite_side_world())
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
    assert (await gate.arm()).admitted is False

    assert gate._armed_fingerprint is None
    manager._active_trades.clear()
    manager.clear_reconciliation_alerts("test")

    assert gate.authorization_current is False
    assert gate.admission_reason() == gate.result.block_reason


@pytest.mark.asyncio
async def test_c7_state_that_can_no_longer_be_serialized_is_not_fresh(tmp_path):
    """Freshness is proved by comparing fingerprints, so no fingerprint = no proof.

    If the execution state stops being serializable the gate cannot compare it
    against what it armed on. That is an UNKNOWN, and an unknown blocks: the
    refusal names serialization rather than claiming staleness it did not
    establish.
    """
    path = _flat_state(tmp_path / "state.json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True

    broken = _protected_record()
    broken.protection_generation = MAX_PROTECTION_GENERATION + 5
    manager._active_trades[SETUP] = broken

    reason = gate.admission_reason()
    assert reason is not None
    assert "could not be serialized" in reason
    assert gate.authorization_current is False


@pytest.mark.asyncio
async def test_c8_a_fresh_re_arm_after_a_change_restores_authorization(tmp_path):
    """Staleness is recoverable, but only by re-establishing authoritative state.

    The trade closes: the record goes, the lock is released, and the exchange no
    longer shows the position or its legs. That invalidates the standing
    authorization, and the ONLY way back is a real reconciliation -- so this
    asserts the gate went back on the wire (two more authoritative reads) rather
    than re-blessing the fingerprint it was already carrying.
    """
    path = _live_state(tmp_path / "state.json")
    exchange = _clean_world()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
    assert (await gate.arm()).admitted is True
    reads_before = len(exchange.reads())

    manager._active_trades.clear()
    manager._entry_fill_sizes.clear()
    manager.single_trade_lock.release_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP)
    exchange.positions.clear()
    exchange.open_orders.clear()
    assert gate.admission_reason() == STALE_AUTHORIZATION_REASON

    persist_execution_state(manager, path)
    gate.strategy_runtime = _FakeRuntime()
    second = await gate.arm()

    assert second.admitted is True
    assert gate.admission_reason() is None
    assert len(exchange.reads()) == reads_before + 2


@pytest.mark.asyncio
async def test_c9_an_idempotent_re_arm_does_not_re_query_the_exchange(tmp_path):
    """Unchanged state re-arms from cache: idempotent, and provably off the wire."""
    path = _flat_state(tmp_path / "state.json")
    exchange = FakeExchange()
    gate = _gate(_manager(exchange), path, _FakeRuntime())
    first = await gate.arm()
    assert first.admitted is True
    reads_before = list(exchange.reads())

    second = await gate.arm()

    assert second is first
    assert exchange.reads() == reads_before


# ══ Section D — P1-4: reconciliation must not speak for an account mid-entry ══
#
# The TOCTOU the audit found: reconciliation reads the listings, a submission it
# cannot see lands, and the run then declares the world clean -- authorizing the
# next entry against evidence that was already incomplete when it was gathered.


class _ParkedSubmission:
    """Parks the entry POST so a reconciliation run can provably overlap it.

    Deterministic rather than timed: the test awaits `submitting` (the submission
    has entered the exchange call) and the submission awaits `release` (the test
    has finished its reconciliation run). No sleeps, so no ordering to lose on a
    slow machine.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._original = client.place_order
        self.submitting = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    def install(self) -> "_ParkedSubmission":
        self._client.place_order = self._place_order
        return self

    async def _place_order(self, request):
        self.calls += 1
        if self.calls == 1:
            self.submitting.set()
            await self.release.wait()
        return await self._original(request)


@pytest.mark.asyncio
async def test_d1_a_run_that_overlaps_a_submission_is_never_clean(tmp_path):
    """The exact interleaving: submit -> park -> reconcile -> the run is unresolved.

    While the POST is in flight the exchange listings cannot describe the
    account, so the run reports `SUBMISSION_IN_FLIGHT` and declares nothing
    clean. This is the property the whole finding is about, pinned at the one
    interleaving that used to produce a false CLEAN.
    """
    path = _flat_state(tmp_path / "state.json")
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True
    parked = _ParkedSubmission(manager.client).install()

    entry = asyncio.create_task(
        manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER))
    await asyncio.wait_for(parked.submitting.wait(), timeout=5)
    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)
    parked.release.set()
    record = await asyncio.wait_for(entry, timeout=5)

    assert "SUBMISSION_IN_FLIGHT" in summary["unresolved"]
    assert "alerts_cleared" not in summary
    assert record.state is not TradeLifecycleState.ENTRY_REJECTED


@pytest.mark.asyncio
async def test_d2_an_overlapping_run_cannot_clear_a_pre_existing_alert(tmp_path):
    """An in-flight submission cannot be the evidence that unblocks the account.

    Clearing alerts is the one thing a clean run DOES, so this is the concrete
    harm: without the in-flight check, an operator-blocking condition would be
    cleared by a run whose listings were taken mid-submission.
    """
    path = _flat_state(tmp_path / "state.json")
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True
    parked = _ParkedSubmission(manager.client).install()
    entry = asyncio.create_task(
        manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER))
    await asyncio.wait_for(parked.submitting.wait(), timeout=5)
    manager._raise_reconciliation_alert("PRIOR_CONDITION", SYMBOL, "seeded")

    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)

    assert "alerts_cleared" not in summary
    assert [a["code"] for a in manager.reconciliation_alerts] == ["PRIOR_CONDITION"]
    parked.release.set()
    await asyncio.wait_for(entry, timeout=5)


@pytest.mark.asyncio
async def test_d3_a_submission_admitted_during_a_run_is_caught_too(tmp_path):
    """The counter is read at BOTH ends of the run, not only before the listings.

    A submission that starts after the listings were read is just as invisible to
    them. Here the entry is admitted while the run is parked inside the position
    listing, so only the second (post-listing) read can catch it.
    """
    path = _flat_state(tmp_path / "state.json")
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())
    assert (await gate.arm()).admitted is True
    parked = _ParkedSubmission(manager.client).install()
    reading = asyncio.Event()
    proceed = asyncio.Event()
    original_positions = manager.client.get_positions

    async def _parked_positions(*a, **kw):
        reading.set()
        await proceed.wait()
        return await original_positions(*a, **kw)

    manager.client.get_positions = _parked_positions

    run = asyncio.create_task(manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER))
    await asyncio.wait_for(reading.wait(), timeout=5)
    entry = asyncio.create_task(
        manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER))
    await asyncio.wait_for(parked.submitting.wait(), timeout=5)
    proceed.set()
    summary = await asyncio.wait_for(run, timeout=5)
    parked.release.set()
    await asyncio.wait_for(entry, timeout=5)

    assert "SUBMISSION_IN_FLIGHT" in summary["unresolved"]


@pytest.mark.asyncio
async def test_d4_the_in_flight_condition_drains_on_success_and_on_refusal(tmp_path):
    """Transient, not sticky: the counter returns to zero down BOTH exits.

    A counter that leaked would make every later run permanently unresolved, and
    an operator facing a gate that never clears learns to bypass it. The refusal
    exit matters most -- it is the path that returns without ever reaching the
    exchange -- so it is driven here through the duplicate-setup guard and the
    single-trade lock.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    first = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)
    assert first.state is not TradeLifecycleState.ENTRY_REJECTED
    assert manager._submissions_in_flight == 0

    duplicate = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)
    assert duplicate is first
    assert manager._submissions_in_flight == 0

    locked = await manager.execute_trade_setup(
        _decision(setup_id="OTHER_SETUP"), ACCOUNT, user_id=USER)
    assert locked.rejection_code == RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value
    assert manager._submissions_in_flight == 0

    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)
    assert "SUBMISSION_IN_FLIGHT" not in summary["unresolved"]


@pytest.mark.asyncio
async def test_d5_concurrent_entries_admit_exactly_one_and_do_not_deadlock(tmp_path):
    """Eight entries race for one account: one owns it, seven are told why.

    The admission section takes a threading lock and performs no I/O inside it,
    so it must not be able to deadlock the event loop. `asyncio.wait_for` is the
    assertion for that -- a hang fails the test instead of hanging the suite
    (there is no pytest-timeout plugin here).
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    records = await asyncio.wait_for(asyncio.gather(*(
        manager.execute_trade_setup(_decision(setup_id=f"RACE_{i}"), ACCOUNT,
                                    user_id=USER)
        for i in range(8)
    )), timeout=20)

    admitted = [r for r in records
                if r.state is not TradeLifecycleState.ENTRY_REJECTED]
    refused = [r for r in records if r.state is TradeLifecycleState.ENTRY_REJECTED]
    assert len(admitted) == 1
    assert len(refused) == 7
    assert {r.rejection_code for r in refused} == {
        RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value}
    assert manager._submissions_in_flight == 0
    entries = [body for body in exchange.placed if not body.get("reduce_only")]
    assert len(entries) == 1                  # exactly one entry reached the wire


@pytest.mark.asyncio
async def test_d6_a_refused_entry_never_releases_the_owners_lock(tmp_path):
    """The refusal must not undo the admission that beat it.

    `_create_rejected_record` releases the account lock on most refusal paths, so
    a loser releasing on its way out would hand the account to the next caller
    while the winner is still submitting. Rules #11/#14: retaining is the
    fail-safe direction.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)
    parked = _ParkedSubmission(manager.client).install()
    winner = asyncio.create_task(
        manager.execute_trade_setup(_decision(setup_id="WINNER"), ACCOUNT,
                                    user_id=USER))
    await asyncio.wait_for(parked.submitting.wait(), timeout=5)

    loser = await manager.execute_trade_setup(
        _decision(setup_id="LOSER"), ACCOUNT, user_id=USER)

    assert loser.rejection_code == RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value
    locked, setup_id, _symbol = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is True
    assert setup_id == "WINNER"
    parked.release.set()
    assert (await asyncio.wait_for(winner, timeout=5)).state is not (
        TradeLifecycleState.ENTRY_REJECTED)


def test_d7_admission_and_reconciliation_share_the_managers_lock():
    """Structural: both sides enter `self._lock`, so neither can straddle the other.

    Read from the AST rather than asserted in prose, because "these two paths are
    synchronized" is exactly the kind of claim that quietly stops being true when
    someone refactors an `if` out of a `with`.
    """
    admission = _function_ast(TradeLifecycleManager.execute_trade_setup)
    reconcile = _function_ast(
        TradeLifecycleManager.reconcile_active_trades_with_exchange)

    for name, tree in (("execute_trade_setup", admission),
                       ("reconcile_active_trades_with_exchange", reconcile)):
        guards = [
            item for node in ast.walk(tree)
            if isinstance(node, ast.With)
            for item in node.items
            if isinstance(item.context_expr, ast.Attribute)
            and item.context_expr.attr == "_lock"
        ]
        assert guards, f"{name} does not enter self._lock anywhere"

    # ...and the section that admits must not await inside it: an await would
    # release the event loop while holding a threading lock the reconciliation
    # path also takes.
    for node in ast.walk(admission):
        if isinstance(node, ast.With) and any(
            isinstance(i.context_expr, ast.Attribute)
            and i.context_expr.attr == "_lock" for i in node.items
        ):
            assert not [n for n in ast.walk(node) if isinstance(n, ast.Await)], (
                "execute_trade_setup awaits inside the admission critical section"
            )


# ═════ Section E — P1-5: an ambiguous submission must be impossible to miss ════
#
# A timeout or dropped connection AFTER the POST leaves the one state the engine
# cannot reason about: the order MAY be live. The old code recorded that in the
# record's state and a retained lock -- neither of which is an alert, neither of
# which survives as an operator-facing condition, and both of which a later run
# could move past.


def _breaking_submission(manager: TradeLifecycleManager, error: BaseException):
    """Make the entry POST fail the way a lost answer fails, and count attempts.

    The failure is injected at the client boundary rather than in the transport
    so the test can prove the count: an ambiguous submission must NOT be retried,
    and a retry is only observable if something is counting.
    """
    calls: List[Any] = []

    async def _place_order(request):
        calls.append(request)
        raise error

    manager.client.place_order = _place_order
    return calls


async def _ambiguous_entry(manager: TradeLifecycleManager, error: BaseException):
    """Drive one entry to the ambiguous outcome and hand back the record."""
    calls = _breaking_submission(manager, error)
    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)
    return record, calls


@pytest.mark.parametrize("error", [
    asyncio.TimeoutError("entry submission timed out"),
    DeltaConnectionError("connection reset during entry submission"),
], ids=["timeout", "connection_lost"])
@pytest.mark.asyncio
async def test_e1_an_unanswered_submission_raises_a_persisted_alert(error):
    """Both ways of losing the answer produce the same first-class condition.

    The alert carries the identity an operator needs to go and ASK the exchange
    what happened -- the setup, the deterministic client order id of the attempt,
    the size, the product and the account -- because the whole point is that the
    engine does not know and must not guess.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record, calls = await _ambiguous_entry(manager, error)

    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    raised = _alerts(manager, "AMBIGUOUS_ENTRY_SUBMISSION")
    assert len(raised) == 1
    context = raised[0]["context"]
    assert context["setup_id"] == SETUP
    assert context["symbol"] == SYMBOL
    assert context["direction"] == TradeDirection.LONG.value
    assert context["client_order_id"] == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    assert context["requested_quantity"] == str(QUANTITY)
    assert context["entry_price"] == str(ENTRY_PRICE)
    assert context["product_id"] == PRODUCT_ID
    assert context["account_id"] == ACCOUNT
    assert context["error_type"] == type(error).__name__
    # Not assumed failed, and not retried: exactly one attempt was made.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_e2_the_ambiguous_lock_is_retained_and_blocks_the_next_entry():
    """The account stays owned, and the alert refuses the next entry by name.

    Two independent refusals must both hold: the lock (rules #11/#14, because a
    position may exist) and the alert (§M11/§M15). Either alone would be one
    refactor away from admitting a second trade alongside a possibly-live,
    possibly-unprotected one.
    """
    manager = _manager(FakeExchange())
    await _ambiguous_entry(manager, asyncio.TimeoutError("no answer"))

    locked, setup_id, _symbol = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is True
    assert setup_id == SETUP

    nxt = await manager.execute_trade_setup(
        _decision(setup_id="NEXT_SETUP"), ACCOUNT, user_id=USER)
    assert nxt.state is TradeLifecycleState.ENTRY_REJECTED
    assert nxt.rejection_code == "RECONCILIATION_REQUIRED"
    assert "AMBIGUOUS_ENTRY_SUBMISSION" in nxt.error_message


@pytest.mark.asyncio
async def test_e3_an_empty_book_cannot_clear_an_unresolved_ambiguity():
    """Two momentarily empty listings do not prove the order never landed.

    This is the self-clearing route the finding was really about: the ambiguous
    record takes the zero-fill branch, so if nothing else lands in `unresolved`
    the run computes CLEAN and clears the very alert protecting the account. The
    record's own RECONCILIATION_REQUIRED state is the finding.
    """
    manager = _manager(FakeExchange())
    await _ambiguous_entry(manager, asyncio.TimeoutError("no answer"))

    summary = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)

    assert SETUP in summary["unresolved"]
    assert "alerts_cleared" not in summary
    assert len(_alerts(manager, "AMBIGUOUS_ENTRY_SUBMISSION")) == 1
    # And no second alert is stacked per run: the condition is already recorded.
    await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)
    assert len(_alerts(manager, "AMBIGUOUS_ENTRY_SUBMISSION")) == 1


@pytest.mark.asyncio
async def test_e4_the_ambiguity_survives_a_restart_and_still_blocks(tmp_path):
    """A process that dies mid-ambiguity must come back knowing it is ambiguous.

    Restart is exactly when an in-memory-only condition would be lost, and it is
    also when the account is least understood. The restored process refuses
    entries and its startup gate refuses to arm, naming the same condition.
    """
    path = tmp_path / "state.json"
    dying = _manager(FakeExchange())
    await _ambiguous_entry(dying, DeltaConnectionError("connection reset"))
    persist_execution_state(dying, path)

    revived = _manager(FakeExchange())
    restore_execution_state_from_file(revived, path)

    raised = _alerts(revived, "AMBIGUOUS_ENTRY_SUBMISSION")
    assert len(raised) == 1
    assert raised[0]["context"]["client_order_id"] == (
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY))
    assert revived._active_trades[SETUP].state is (
        TradeLifecycleState.RECONCILIATION_REQUIRED)

    blocked = await revived.execute_trade_setup(
        _decision(setup_id="AFTER_RESTART"), ACCOUNT, user_id=USER)
    assert blocked.rejection_code == "RECONCILIATION_REQUIRED"

    gate = _gate(revived, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
    result = await gate.arm()
    assert result.admitted is False
    # Blocked at reconciliation (step 2), which names the ambiguous setup -- the
    # gate's own run could not resolve it, and did not clear its alert either.
    assert "did not return clean" in result.block_reason
    assert SETUP in result.block_reason
    assert len(_alerts(revived, "AMBIGUOUS_ENTRY_SUBMISSION")) == 1
    assert gate.admission_reason() == result.block_reason


@pytest.mark.asyncio
async def test_e5_a_successful_submission_raises_no_ambiguity_alert():
    """The contrast case: the alert must mean something, so it cannot be routine."""
    manager = _manager(FakeExchange())

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    assert record.state is not TradeLifecycleState.RECONCILIATION_REQUIRED
    assert _alerts(manager, "AMBIGUOUS_ENTRY_SUBMISSION") == []
    assert manager.reconciliation_alerts == []


@pytest.mark.asyncio
async def test_e6_an_exchange_rejection_is_not_an_ambiguity():
    """An explicit "no" is authoritative: no order exists, so the lock is released.

    Kept next to the ambiguous cases because conflating the two is how a
    fail-closed condition gets softened into a routine one -- a rejection that
    retained the lock and raised an alert would make every ordinary rejection
    look like a possible live order.
    """
    manager = _manager(FakeExchange())
    _breaking_submission(manager, trade_lifecycle_module.DeltaOrderRejectedError(
        "insufficient margin"))

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert _alerts(manager, "AMBIGUOUS_ENTRY_SUBMISSION") == []
    locked, _setup_id, _symbol = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is False


# ══ Section F — P1-6 / P2-6: one submission identity, one admission contract ═══
#
# The audit found a THIRD submission path (`UserExecutionSession.execute_trade`)
# able to submit three legs under RANDOM client order ids without consulting any
# admission contract. It is quarantined rather than deleted, so what needs
# proving is that quarantine is real: intact on disk, unreachable from the
# package root, and unable to become production wiring by an import.


def test_f1_the_admission_contract_still_binds_every_canonical_path():
    """The structural assertion itself, plus what it actually looked at.

    Asserting the evidence rather than only "it did not raise" is the difference
    between proving the contract holds and proving the checker ran: an assertion
    that silently checked an empty manifest would pass either way.
    """
    evidence = assert_entry_admission_contract_intact()

    assert evidence["canonical_paths_consulting_contract"] == [
        "execution.trade_lifecycle.TradeLifecycleManager.execute_trade_setup"]
    assert evidence["submitting_half_callers"] == ["execute_trade_setup"]
    assert evidence["quarantined_paths_unreachable_from_root"] == [
        "execution.execution_engine.LiveOrderExecutionService",
        "execution.multi_user_orchestrator.UserExecutionSession.execute_trade",
        "execution.multi_user_orchestrator.MultiUserExecutionOrchestrator",
    ]
    # Three modules CALL a submission method: the canonical one and the two
    # quarantined ones. `delta_client` only defines it, so it is absent here.
    assert set(evidence["submission_call_sites"]) == {
        "execution/trade_lifecycle.py",
        "execution/execution_engine.py",
        "execution/multi_user_orchestrator.py",
    }


def test_f2_exactly_one_canonical_production_submission_identity_path():
    """One canonical path, and the third path named in the quarantine manifest."""
    assert len(CANONICAL_ENTRY_SUBMISSION_PATHS) == 1
    canonical = CANONICAL_ENTRY_SUBMISSION_PATHS[0]
    assert canonical.module == "execution.trade_lifecycle"
    assert canonical.qualname == "TradeLifecycleManager.execute_trade_setup"

    quarantined = {f"{p.module}.{p.qualname}" for p in QUARANTINED_SUBMISSION_PATHS}
    assert ("execution.multi_user_orchestrator.UserExecutionSession.execute_trade"
            in quarantined)


def test_f3_the_quarantined_third_path_is_intact_but_unreachable():
    """Quarantine is not deletion: the module still imports, the root does not.

    Both halves matter. Deleting it would lose the code the audit examined;
    leaving it exported would leave a production-reachable path able to submit
    under random ids, which is the finding itself.
    """
    from quantedge import execution as package
    from quantedge.execution import multi_user_orchestrator as orphan

    assert callable(orphan.UserExecutionSession.execute_trade)
    assert orphan.MultiUserExecutionOrchestrator is not None
    for name in ("UserExecutionSession", "MultiUserExecutionOrchestrator",
                 "LiveOrderExecutionService"):
        assert not hasattr(package, name)
        assert name not in (getattr(package, "__all__", ()) or ())


def test_f4_the_quarantined_path_still_uses_random_ids_and_no_contract():
    """Why it is quarantined, stated as a test rather than as a comment.

    If someone later routes this path through the canonical generator it becomes
    a candidate for production and this test must be revisited deliberately --
    which is exactly the review the mandate asks for. Until then the random ids
    are the reason it may not be wired, and the mandate preserves them.
    """
    from quantedge.execution import multi_user_orchestrator as orphan

    called = _called_names(_function_ast(orphan.UserExecutionSession.execute_trade))
    assert "generate_client_order_id" in called
    assert "generate_deterministic_client_order_id" not in called
    assert "_entry_admission_reason" not in called


def test_f5_an_installed_contract_is_never_replaced(tmp_path):
    """A live contract may be REFUSING; replacing it would default the door open."""
    manager = _manager(FakeExchange())
    installed = install_entry_admission(manager, _RefusingContract("first"))
    assert getattr(manager, MANAGER_GATE_ATTRIBUTE) is installed

    with pytest.raises(EntryAdmissionError, match="already installed"):
        install_entry_admission(manager, _RefusingContract("second"))

    assert manager._entry_admission_reason() == "first"


def test_f6_a_non_conforming_contract_is_refused_outright():
    """Filled-but-absent is the worst state: it looks installed and refuses nothing."""
    manager = _manager(FakeExchange())

    class _NotAContract:
        pass

    with pytest.raises(EntryAdmissionError, match="does not implement"):
        install_entry_admission(manager, _NotAContract())
    with pytest.raises(EntryAdmissionError, match="does not implement"):
        install_entry_admission(manager, object())

    assert getattr(manager, MANAGER_GATE_ATTRIBUTE) is None
    assert manager._entry_admission_reason() is None


@pytest.mark.asyncio
async def test_f7_a_contract_that_raises_refuses_the_entry():
    """A broken boundary must not open the door.

    An exception from the gate is neither an admission nor a crash the caller has
    to handle: it is a refusal, so the entry is rejected and nothing is POSTed.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    class _Exploding:
        def admission_reason(self):
            raise RuntimeError("gate is broken")

    install_entry_admission(manager, _Exploding())

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == GATE_REFUSAL_CODE
    assert "gate is broken" in record.error_message
    assert exchange.placed == []


@pytest.mark.asyncio
async def test_f8_the_canonical_path_identifies_every_leg_deterministically():
    """All three legs are named by the one generator, from (account, setup, role).

    P1-6's requirement in its positive form: the canonical path has exactly one
    identity scheme, and a restart can recompute the name of every order it sent.
    """
    exchange = _CancellingExchange(
        positions=[_position_json()],
        open_orders=[],
    )
    manager = _manager(exchange)

    await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    submitted = exchange.client_order_ids()
    assert submitted
    for role in ("ENTRY", "SL", "TP"):
        expected = generate_deterministic_client_order_id(ACCOUNT, SETUP, role)
        if any(cid.endswith(f"-{role}") for cid in submitted):
            assert expected in submitted, f"{role} leg was not deterministically named"
    assert len(set(submitted)) == len(submitted)     # no id reused across legs


# ── G. P1-7 transition and composite-state integrity ──────────────────────────


def _production_transition_targets() -> set:
    """Every state `trade_lifecycle.py` actually transitions a record TO.

    Read out of the production source instead of copied into a list here, so a
    transition added tomorrow is exercised by g1 automatically rather than
    quietly escaping it. One call site (`_finalize_unfilled_entry`) chooses its
    target at runtime; for that one every state its enclosing function names is
    taken as a possible target.

    This is the honest form of "every legitimate transition still valid": P1-7
    deliberately did NOT invent a whitelist, so the set of legitimate targets
    can only be read off the code that performs them.
    """
    tree = ast.parse(
        Path(trade_lifecycle_module.__file__).read_text(encoding="utf-8"))
    parents: Dict[Any, Any] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _enclosing(node: Any) -> Any:
        while node is not None and not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            node = parents.get(node)
        return node

    def _states_named_in(node: Any) -> set:
        return {
            n.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "TradeLifecycleState"
        }

    targets: set = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record_transition"
            and node.args
        ):
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.Attribute)
            and isinstance(first.value, ast.Name)
            and first.value.id == "TradeLifecycleState"
        ):
            targets.add(first.attr)
            continue
        enclosing = _enclosing(node)
        assert enclosing is not None, (
            f"trade_lifecycle.py:{node.lineno}: a transition outside any function"
        )
        targets |= _states_named_in(enclosing)
    assert targets, "no production transition targets found -- the AST scan is wrong"
    return targets
def _encoded(value: Any) -> Any:
    """`value` in the form the state FILE carries it in.

    The decode-side tests edit a real exported body, so the value they write has
    to look like something a writer could have written -- a Decimal as its
    string, a state as its `.value` -- otherwise the field codec would refuse it
    first and the composite check under test would never run.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, TradeLifecycleState):
        return value.value
    return value


#: The field COMBINATIONS no path in `trade_lifecycle.py` can produce, each with
#: the fragment of the refusal that names what is wrong. Every one of them
#: describes a record that would make an unsafe decision if it were restored:
#: protection with nothing to cancel invites a second bracket on a live one,
#: protection of an unfilled position hides a bare real position behind a
#: "protected" number, an unknown source or generation makes the next protective
#: order unnameable, an order id with no product id is the silent P2-1 rebinding,
#: and POSITION_CLOSED with no closing time is a terminal state that never ended.
_CONTRADICTIONS: List[Tuple[str, Dict[str, Any], str]] = [
    ("protection_with_nothing_to_cancel",
     {"sl_order_id": None, "tp_order_id": None},
     "cannot cancel, resize or verify"),
    ("protection_of_an_unfilled_position",
     {"filled_quantity": Decimal("0")},
     "does not account for"),
    ("a_negative_fill",
     {"filled_quantity": Decimal("-1")},
     "filled_quantity is negative"),
    ("a_negative_protected_quantity",
     {"protected_quantity": Decimal("-1")},
     "protected_quantity is negative"),
    ("a_protection_source_outside_the_vocabulary",
     {"protection_source": "OPERATOR_ASSUMED"},
     "which is not one of"),
    ("an_unnameable_protection_generation",
     {"protection_generation": MAX_PROTECTION_GENERATION + 1},
     "outside the nameable range"),
    ("a_negative_protection_generation",
     {"protection_generation": -1},
     "outside the nameable range"),
    ("a_product_id_that_is_not_a_product_id",
     {"product_id": 0},
     "not a positive int"),
    ("exchange_order_ids_with_no_product",
     {"product_id": None},
     "no product_id"),
    ("a_closed_position_with_no_closing_time",
     {"state": TradeLifecycleState.POSITION_CLOSED, "closed_at": None},
     "closed_at is not set"),
]

_CONTRADICTION_IDS = [case[0] for case in _CONTRADICTIONS]
@pytest.mark.parametrize("state_name", sorted(_production_transition_targets()))
def test_g1_every_transition_production_performs_is_still_accepted(
    tmp_path, state_name
):
    """P1-7's first requirement: no legitimate transition was made illegal.

    Each target production really uses is recorded, appends exactly one history
    event, and the resulting record still survives a persist/restore round trip.
    The last part matters: a composite check that refused a state the engine can
    actually reach would turn a live trade into an unrestorable file.
    """
    target = getattr(TradeLifecycleState, state_name)
    record = _protected_record()
    before = len(record.history)

    record.record_transition(target, f"g1 exercises {state_name}")

    assert record.state is target
    assert len(record.history) == before + 1
    assert record.history[-1]["to_state"] == target.value
    assert record.history[-1]["from_state"] == TradeLifecycleState.PROTECTED_POSITION.value

    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = record
    path = persist_execution_state(manager, tmp_path / "state.json")

    restored = _manager(FakeExchange())
    restore_execution_state_from_file(restored, path)
    assert restored._active_trades[SETUP].state is target


def test_g2_closing_a_position_stamps_the_time_that_makes_it_consistent():
    """The one contradiction that looks reachable is not, and this is why.

    `record_transition` sets `closed_at` on the POSITION_CLOSED transition
    unconditionally, so a closed record with no closing time can only come from
    corruption or a hand edit -- which is exactly what g3/g4 refuse. Asserted
    here so the refusal is known to be aimed at corruption, not at behaviour.
    """
    record = _protected_record(closed_at=None)
    assert record.closed_at is None

    record.record_transition(TradeLifecycleState.POSITION_CLOSED, "g2 closes it")

    assert record.state is TradeLifecycleState.POSITION_CLOSED
    assert record.closed_at is not None
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = record
    export_execution_state(manager)          # persists, so it is not a contradiction
@pytest.mark.parametrize(
    "label, fields, fragment", _CONTRADICTIONS, ids=_CONTRADICTION_IDS)
def test_g3_the_encode_boundary_refuses_a_record_that_contradicts_itself(
    tmp_path, label, fields, fragment
):
    """A corrupt in-memory record is never written, and never repaired.

    The refusal has to happen at the WRITE boundary as well as the read one:
    otherwise the next restart inherits the contradiction as if it were a fact,
    and the file that would have proved the state safe is the file that lost it.
    """
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = _protected_record(SETUP, **fields)
    path = tmp_path / "state.json"

    with pytest.raises(ExecutionStatePersistenceError, match=fragment):
        persist_execution_state(manager, path)

    assert "refusing to persist internally" in _refusal(
        ExecutionStatePersistenceError, export_execution_state, manager)
    assert not path.exists()                 # nothing half-written was left behind


@pytest.mark.parametrize(
    "label, fields, fragment", _CONTRADICTIONS, ids=_CONTRADICTION_IDS)
def test_g4_the_decode_boundary_refuses_a_file_that_contradicts_itself(
    tmp_path, label, fields, fragment
):
    """A hand-edited or truncated file is refused rather than best-effort parsed.

    Written with `write_bytes` on purpose: no production writer can emit this
    body (g3 refuses it at the encode boundary), so the only way such a file can
    exist is outside this module -- which is what the decode-side check is for.

    Verified layering, asserted below rather than assumed: `load_execution_state`
    checks the SHAPE of a body (schema, version, key set, containers, account
    identity) and does not decode records, so the contradiction is caught on the
    RESTORE path. That is the boundary that matters, because restoring is the
    only way a persisted record can reach a trading decision.
    """
    good = _live_state(tmp_path / "state.json")
    body = copy.deepcopy(load_execution_state(good))
    for name, value in fields.items():
        body["active_trades"][SETUP][name] = _encoded(value)

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(json.dumps(body).encode("utf-8"))
    manager = _manager(FakeExchange())

    message = _refusal(
        ExecutionStateCorruptError, restore_execution_state_from_file, manager, corrupt)
    assert fragment in message
    assert "refused rather than repaired" in message

    # The same body reaches the same refusal through the in-memory entry point,
    # so a caller that loaded the file itself cannot restore it either.
    assert fragment in _refusal(
        ExecutionStateCorruptError, restore_execution_state, manager, body)
    assert manager._active_trades == {}       # nothing was partially restored
def test_g5_a_refused_restore_leaves_the_live_state_exactly_as_it_was(tmp_path):
    """No silent repair, and no partial restore over good state.

    The dangerous version of this failure is not the exception -- it is a
    half-restored manager that has forgotten one live trade and kept another, or
    a "helpfully" corrected record. Both are checked by fingerprinting the whole
    body before and after the refusal.
    """
    good = _live_state(tmp_path / "state.json")
    body = copy.deepcopy(load_execution_state(good))
    body["active_trades"][SETUP]["protection_source"] = "OPERATOR_ASSUMED"
    corrupt = tmp_path / "corrupt.json"
    payload = json.dumps(body).encode("utf-8")
    corrupt.write_bytes(payload)

    manager = _manager(FakeExchange())
    restore_execution_state_from_file(manager, good)          # a real restart
    before = serialize_execution_state_body(export_execution_state(manager))
    good_bytes = Path(good).read_bytes()

    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state_from_file(manager, corrupt)

    assert serialize_execution_state_body(export_execution_state(manager)) == before
    assert manager._active_trades[SETUP].protection_source == PROTECTION_SOURCE_ENGINE
    assert corrupt.read_bytes() == payload    # not rewritten "fixed" on disk either
    assert Path(good).read_bytes() == good_bytes   # the good file was never touched


#: The three shapes the P1-7 note names as legitimate-but-odd. If a whitelist had
#: been invented from the state names alone, each of these real records would now
#: be unpersistable -- which is the reason no whitelist was invented.
_LEGITIMATE_ODD_SHAPES: List[Tuple[str, Dict[str, Any]]] = [
    ("a_rejected_entry_still_held_in_active_trades", {
        "state": TradeLifecycleState.ENTRY_REJECTED,
        "filled_quantity": Decimal("0"),
        "protected_quantity": Decimal("0"),
        "entry_order_id": None,
        "sl_order_id": None,
        "tp_order_id": None,
        "protection_source": None,
        "rejection_code": "INSUFFICIENT_MARGIN",
    }),
    ("a_closed_position_that_kept_its_bracket_ids", {
        "state": TradeLifecycleState.POSITION_CLOSED,
        "closed_at": datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
    }),
    ("a_protection_retry_re_entering_an_earlier_state", {
        "state": TradeLifecycleState.PROTECTION_PENDING,
        "protected_quantity": Decimal("0"),
        "sl_order_id": None,
        "tp_order_id": None,
        "protection_source": None,
        "protection_generation": 2,
    }),
]
@pytest.mark.parametrize(
    "label, fields",
    _LEGITIMATE_ODD_SHAPES,
    ids=[case[0] for case in _LEGITIMATE_ODD_SHAPES],
)
def test_g6_the_odd_but_real_record_shapes_still_persist(tmp_path, label, fields):
    """Structural checks only, no invented state machine.

    Each of these looks wrong from the state name alone and is produced by the
    engine on purpose. They round trip unchanged, field for field.
    """
    record = _protected_record(SETUP, **fields)
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = record

    path = persist_execution_state(manager, tmp_path / "state.json")
    restored = _manager(FakeExchange())
    restore_execution_state_from_file(restored, path)

    back = restored._active_trades[SETUP]
    assert back.state is record.state
    for name, value in fields.items():
        assert getattr(back, name) == value, f"{name} did not survive the round trip"


def test_g7_a_restart_round_trip_is_byte_deterministic(tmp_path):
    """Restore then re-persist reproduces the same bytes, twice over.

    Determinism is what makes the P1-3 freshness fingerprint mean anything: if
    serializing unchanged state produced different bytes, every check would look
    like a change and the gate would either block forever or be relaxed until it
    did not block at all.
    """
    path = _live_state(tmp_path / "state.json")
    first = Path(path).read_bytes()

    manager = _manager(FakeExchange())
    restore_execution_state_from_file(manager, path)
    second = Path(persist_execution_state(manager, tmp_path / "second.json")).read_bytes()
    assert second == first

    again = _manager(FakeExchange())
    restore_execution_state_from_file(again, tmp_path / "second.json")
    assert serialize_execution_state_body(export_execution_state(again)) == first

    # And re-exporting the SAME manager twice, having touched nothing, is stable.
    assert serialize_execution_state_body(export_execution_state(manager)) == first


# ── H. P2-1 persisted execution product identity ──────────────────────────────


def _repoint_registry(
    monkeypatch, symbol: str = SYMBOL, product_id: int = OTHER_PRODUCT_ID
) -> None:
    """Make the registry resolve `symbol` to a DIFFERENT product than before.

    This is the P2-1 hazard in its smallest form: nothing about the live position
    changed, only what the process now believes the symbol means. A snapshot
    correction, a re-listing or an editing mistake all look exactly like this,
    and every subsequent lookup, cancel and bracket placement for an existing
    record would silently address the other instrument.

    `_authoritative_product_specification` resolves through the module global, so
    the module global is what moves.
    """
    real = trade_lifecycle_module.get_product_specification

    def _resolve(requested: str):
        spec = real(requested)
        if requested == symbol:
            return dataclasses.replace(spec, product_id=product_id)
        return spec

    monkeypatch.setattr(trade_lifecycle_module, "get_product_specification", _resolve)


def _resting_entry_record(**over: Any):
    """A record whose entry is still resting on the exchange, and nothing else.

    Consistent by the P1-7 rules: an unfilled entry accounts for no fill and
    claims no protection, so it carries no bracket ids and no protection source.
    """
    fields: Dict[str, Any] = {
        "state": TradeLifecycleState.ENTRY_SUBMITTED,
        "filled_quantity": Decimal("0"),
        "protected_quantity": Decimal("0"),
        "average_fill_price": None,
        "sl_order_id": None,
        "sl_client_order_id": None,
        "tp_order_id": None,
        "tp_client_order_id": None,
        "protection_source": None,
    }
    fields.update(over)
    return _protected_record(SETUP, **fields)
@pytest.mark.asyncio
async def test_h1_the_entry_path_persists_the_product_it_submitted_against(tmp_path):
    """The identity is recorded from the submission, not re-derived on restore.

    The record's `product_id` is the same number the POST carried, and it survives
    a restart -- which is what makes the later comparison possible at all.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    assert record.product_id == PRODUCT_ID
    assert exchange.placed, "no order was submitted, so nothing was identified"
    assert exchange.placed[0]["product_id"] == PRODUCT_ID

    path = persist_execution_state(manager, tmp_path / "state.json")
    restored = _manager(FakeExchange())
    restore_execution_state_from_file(restored, path)
    assert restored._active_trades[SETUP].product_id == PRODUCT_ID


@pytest.mark.asyncio
async def test_h2_an_unchanged_registry_leaves_every_guarded_site_working(tmp_path):
    """The regression half: the guard must be invisible while the two agree.

    A close on the real registry still closes -- so the refusals below are the
    disagreement being caught, not the guard blocking normal operation.
    """
    exchange = _CancellingExchange(positions=[_position_json()], open_orders=[])
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()

    closed = await manager.close_position(SETUP, CloseReason.MANUAL_CLOSE)

    assert closed.state is TradeLifecycleState.POSITION_CLOSED
    assert closed.product_id == PRODUCT_ID     # still the product it traded on
    assert exchange.cancelled, "the brackets were never cancelled, so nothing ran"


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["ensure_bracket_protection", "expire_resting_entry",
                                 "close_position"])
async def test_h3_a_repointed_registry_blocks_every_action_on_existing_orders(
    monkeypatch, site
):
    """Mismatch blocks; it never silently rebinds the record to the new product.

    Each of these three sites acts on orders that already exist on the exchange,
    and each refuses BEFORE the wire: no cancel, no replacement bracket, no
    close. The record keeps its persisted product id -- the refusal is not a
    repair.
    """
    exchange = _CancellingExchange(positions=[_position_json()], open_orders=[])
    manager = _manager(exchange)
    record = (
        _resting_entry_record() if site == "expire_resting_entry"
        else _protected_record()
    )
    manager._active_trades[SETUP] = record
    _repoint_registry(monkeypatch)

    if site == "ensure_bracket_protection":
        record.protected_quantity = Decimal("0")
        record.sl_order_id = None
        record.tp_order_id = None
        call = manager._ensure_bracket_protection(record, QUANTITY)
    elif site == "expire_resting_entry":
        call = manager.expire_resting_entry(SETUP)
    else:
        call = manager.close_position(SETUP, CloseReason.MANUAL_CLOSE)

    with pytest.raises(ProductIdentityMismatchError, match="refusing to act"):
        await asyncio.wait_for(call, timeout=5)

    assert record.product_id == PRODUCT_ID     # not rebound to the registry's id
    assert SETUP in manager._active_trades     # not archived, not closed
    assert _writes(exchange) == []             # nothing reached the exchange
@pytest.mark.asyncio
async def test_h4_the_kill_switch_treats_a_repoint_as_an_unconfirmed_cancel(
    monkeypatch
):
    """The fourth site's real behaviour, verified rather than assumed.

    `activate_kill_switch` catches the refusal at its cancel site, so the
    mismatch surfaces as "this entry order was NOT confirmed cancelled": the
    switch still engages, the order is not claimed as cancelled, the trade keeps
    its order id and its owner, and a blocking alert is raised. That is the
    fail-closed shape for this site -- an exception here would abandon the rest
    of the sweep.
    """
    exchange = FakeExchange(open_orders=[_order_json()])
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _resting_entry_record()
    _repoint_registry(monkeypatch)

    result = await asyncio.wait_for(
        manager.activate_kill_switch("H4_OPERATOR_TRIGGER"), timeout=5)

    assert result["kill_switch_active"] is True
    assert manager.state_store.account.kill_switch_active is True
    assert result["cancelled_orders"] == []          # nothing claimed as cancelled
    assert result["unverified_orders"] == [str(ENTRY_ORDER_ID)]
    finding = result["unverified_details"][0]
    assert "refusing to act on this record's orders" in finding["cancel_error"]

    assert SETUP in manager._active_trades           # the order keeps an owner
    assert manager._active_trades[SETUP].entry_order_id == str(ENTRY_ORDER_ID)
    assert manager._active_trades[SETUP].product_id == PRODUCT_ID
    assert _alerts(manager, "KILL_SWITCH_ENTRY_CANCEL_UNCONFIRMED")
    assert _writes(exchange) == []                   # no cancel was attempted


@pytest.mark.asyncio
async def test_h5_reconciliation_reports_a_repoint_and_blocks_the_gate(
    monkeypatch, tmp_path
):
    """The sweep's own, richer version: report every record, abort on none.

    Reconciliation must not raise on the first mismatch -- it has to finish and
    name all of them -- so it reports this one as unresolved with a persisted
    alert, moves the record to RECONCILIATION_REQUIRED, and leaves the persisted
    product id alone. The startup gate then refuses to authorize an entry.
    """
    path = _live_state(tmp_path / "state.json")
    exchange = _clean_world()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
    _repoint_registry(monkeypatch)

    result = await asyncio.wait_for(gate.arm(), timeout=10)

    assert result.admitted is False
    assert SETUP in result.block_reason
    record = manager._active_trades[SETUP]
    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED
    assert record.product_id == PRODUCT_ID          # reported, never rebound

    alerts = _alerts(manager, "PRODUCT_IDENTITY_MISMATCH")
    assert len(alerts) == 1
    assert str(PRODUCT_ID) in alerts[0]["details"]        # what it was submitted on
    assert str(OTHER_PRODUCT_ID) in alerts[0]["details"]  # what the registry says now
    assert gate.admission_reason() is not None      # no entry may be admitted
    assert _writes(exchange) == []
def test_h6_no_site_resolves_a_products_identity_without_the_guard():
    """A drift guard: a new call site must not reintroduce the silent rebind.

    Two facts are pinned. First, the four sites that act on orders an existing
    record already owns all resolve through the guarded helper. Second, the RAW
    resolver is called from exactly three places, each safe for a stated reason:
    the guard itself, the entry path (which is CREATING the identity, so there is
    nothing yet to compare against), and the reconciliation sweep (which carries
    its own richer comparison so it can report every record instead of aborting).
    A fourth raw caller would be an unguarded rebind, and this test fails on it.
    """
    source = Path(trade_lifecycle_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents: Dict[Any, Any] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _enclosing_name(node: Any) -> Optional[str]:
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
            node = parents.get(node)
        return None

    guarded: set = set()
    raw: set = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == "_authoritative_product_specification":
            guarded.add(_enclosing_name(node))
        elif node.func.id == "get_product_specification":
            raw.add(_enclosing_name(node))

    assert guarded == {
        "_ensure_bracket_protection",     # places/resizes the protective pair
        "expire_resting_entry",           # cancels the resting entry
        "close_position",                 # cancels brackets and closes
        "activate_kill_switch",           # cancels pending entries
    }
    assert raw == {
        "_authoritative_product_specification",     # the guard itself
        "_execute_admitted_trade_setup",            # creates the identity
        "reconcile_active_trades_with_exchange",    # compares it itself
    }


@pytest.mark.asyncio
async def test_h7_a_record_that_never_reached_the_exchange_is_not_rebound(
    monkeypatch, tmp_path
):
    """No invented migration, and no blocking of state that binds nothing.

    A record with no persisted product id and no exchange order ids never reached
    an instrument, so there is no identity to contradict: it round trips and is
    unaffected by a repoint. The dangerous legacy shape -- order ids WITH no
    product id -- is refused outright at both persistence boundaries (g3/g4),
    which is the "fail closed rather than migrate" half of P2-1.
    """
    record = _resting_entry_record(
        state=TradeLifecycleState.ENTRY_PENDING,
        entry_order_id=None,
        entry_client_order_id=None,
        product_id=None,
    )
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = record

    path = persist_execution_state(manager, tmp_path / "state.json")
    restored = _manager(FakeExchange())
    restore_execution_state_from_file(restored, path)
    assert restored._active_trades[SETUP].product_id is None

    _repoint_registry(monkeypatch)
    outcome = await asyncio.wait_for(
        restored.expire_resting_entry(SETUP), timeout=5)
    assert outcome["outcome"] == "NO_EXCHANGE_ORDER"      # nothing to act on
    assert restored._active_trades[SETUP].product_id is None


# ══ I. P2-2: which protection contract exists, and who created it ═════════════
#
# The audit finding: a restored record could tell that it was protected but not
# WHAT protected it. That distinction is the whole safety content of the field.
# An engine-placed pair is two reduce-only orders this process named and can
# rebuild; an adopted pair is Delta's own attached bracket, named by Delta, which
# the engine must not duplicate. A restart that forgot the difference has exactly
# two ways to be wrong, and both are expensive: place a second pair on a position
# Delta already protects, or leave a real position bare because a "protected"
# number outlived the orders behind it.

#: The exchange's own client order ids for its attached-bracket legs. Deliberately
#: NOT the deterministic engine names: the point of the adopted case is that the
#: identity on the record came from Delta and cannot be recomputed, so a round
#: trip that "helpfully" regenerated it would be visible here.
ADOPTED_SL_ORDER_ID = 970501
ADOPTED_TP_ORDER_ID = 970502
ADOPTED_SL_CLIENT_ID = "delta-bracket-sl-970501"
ADOPTED_TP_CLIENT_ID = "delta-bracket-tp-970502"


def _adopted_leg(
    order_id: int, leg_type: str, trigger_price: Decimal, client_order_id: str, **over
) -> Dict[str, Any]:
    """One resting leg of Delta's attached bracket, in the adoptable shape.

    Every clause `_is_adoptable_bracket_leg` requires is stated positively:
    product, close side, reduce-only, a resting state, the exact size, the stop
    type, `last_traded_price` as the trigger series and the authorised stop
    price. Omitting any of them makes the leg unadoptable, which is a different
    (already covered) test -- here the leg IS adoptable, so adoption really runs.
    """
    payload: Dict[str, Any] = {
        "id": order_id,
        "client_order_id": client_order_id,
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
        "side": "sell",                      # closing side of a LONG
        "order_type": "market_order",
        "size": str(QUANTITY),
        "unfilled_size": str(QUANTITY),
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


class _AttachedBracketExchange(FakeExchange):
    """A Delta that holds its OWN bracket for the entry, and says so.

    Two answers make adoption possible, and the production path requires both
    independently: the ENTRY order object echoes the authorised bracket levels,
    and exactly one adoptable reduce-only leg per side is resting. Anything less
    and `_adopt_exchange_attached_bracket` declines and the engine places its own
    pair -- so a test that got this shape wrong would silently exercise the
    engine-placed case instead, which is why `placed` is asserted empty below.
    """

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/v2/orders/{ENTRY_ORDER_ID}":
            self.requests.append(request)
            return _ok(_order_json(
                order_id=ENTRY_ORDER_ID,
                bracket_stop_loss_price=str(STOP_LOSS),
                bracket_take_profit_price=str(TAKE_PROFIT),
            ))
        return super().__call__(request)


def _adopted_world() -> _AttachedBracketExchange:
    return _AttachedBracketExchange(
        positions=[_position_json()],
        open_orders=[
            _adopted_leg(
                ADOPTED_SL_ORDER_ID, "stop_loss_order", STOP_LOSS, ADOPTED_SL_CLIENT_ID),
            _adopted_leg(
                ADOPTED_TP_ORDER_ID, "take_profit_order", TAKE_PROFIT, ADOPTED_TP_CLIENT_ID),
        ],
    )


def _filled_unprotected_record(**over: Any):
    """A filled position with no protection yet: the state protection starts from."""
    fields: Dict[str, Any] = {
        "state": TradeLifecycleState.ENTRY_FILLED,
        "protected_quantity": Decimal("0"),
        "sl_order_id": None,
        "sl_client_order_id": None,
        "tp_order_id": None,
        "tp_client_order_id": None,
        "protection_source": None,
    }
    fields.update(over)
    return _protected_record(SETUP, **fields)


def _contract(record: Any) -> Dict[str, Any]:
    """The persisted protection contract: provenance, identity, size, generation."""
    return {
        "protection_source": record.protection_source,
        "sl_order_id": record.sl_order_id,
        "sl_client_order_id": record.sl_client_order_id,
        "tp_order_id": record.tp_order_id,
        "tp_client_order_id": record.tp_client_order_id,
        "protected_quantity": record.protected_quantity,
        "protection_generation": record.protection_generation,
        "state": record.state,
    }


def _round_trip(manager: TradeLifecycleManager, path: Path) -> TradeLifecycleManager:
    """Persist `manager` and bring a second one up from the file, as a restart does."""
    written = persist_execution_state(manager, path)
    restarted = _manager(FakeExchange())
    restore_execution_state_from_file(restarted, written)
    return restarted


@pytest.mark.asyncio
async def test_i1_an_engine_placed_pair_records_and_restores_its_own_provenance(
    tmp_path,
):
    """The engine-created contract, written by the path that creates it.

    The provenance is not set by the test: `_ensure_bracket_protection` places the
    pair and stamps ENGINE_PLACED itself, so what round trips is what production
    would have written. Both legs' client order ids are the deterministic names,
    which is what makes an engine-placed contract rebuildable after a restart.
    """
    exchange = FakeExchange(positions=[_position_json()])
    manager = _manager(exchange)
    record = _filled_unprotected_record()
    manager._active_trades[SETUP] = record

    await asyncio.wait_for(
        manager._ensure_bracket_protection(record, QUANTITY), timeout=5)

    assert record.protection_source == PROTECTION_SOURCE_ENGINE
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert len(exchange.placed) == 2, exchange.placed
    assert record.sl_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, protection_role(ORDER_ROLE_STOP_LOSS, 0))
    assert record.tp_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, protection_role(ORDER_ROLE_TAKE_PROFIT, 0))
    contract = _contract(record)

    restarted = _round_trip(manager, tmp_path / "state.json")

    assert _contract(restarted._active_trades[SETUP]) == contract


@pytest.mark.asyncio
async def test_i2_an_adopted_bracket_records_and_restores_the_exchanges_identity(
    tmp_path,
):
    """The adopted contract, and the identity only Delta could have supplied.

    Delta named these legs, so their client order ids are unguessable strings that
    no generator can reproduce. They are the record's only handle on the orders
    protecting a real position, so the round trip has to carry them verbatim --
    and `placed` proves the engine placed nothing of its own alongside them.
    """
    exchange = _adopted_world()
    manager = _manager(exchange)
    record = _filled_unprotected_record()
    manager._active_trades[SETUP] = record

    await asyncio.wait_for(
        manager._ensure_bracket_protection(record, QUANTITY), timeout=5)

    assert record.protection_source == PROTECTION_SOURCE_EXCHANGE_ADOPTED
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert exchange.placed == [], exchange.placed        # no duplicate pair
    assert record.sl_order_id == str(ADOPTED_SL_ORDER_ID)
    assert record.tp_order_id == str(ADOPTED_TP_ORDER_ID)
    assert record.sl_client_order_id == ADOPTED_SL_CLIENT_ID
    assert record.tp_client_order_id == ADOPTED_TP_CLIENT_ID
    assert record.protected_quantity == QUANTITY
    contract = _contract(record)

    restarted = _round_trip(manager, tmp_path / "state.json")
    restored = restarted._active_trades[SETUP]

    assert _contract(restored) == contract
    assert restored.protection_source == PROTECTION_SOURCE_EXCHANGE_ADOPTED
    # Not recomputed from the generator, which would name an order Delta has
    # never heard of and quietly detach the record from the real legs.
    assert restored.sl_client_order_id != generate_deterministic_client_order_id(
        ACCOUNT, SETUP, protection_role(ORDER_ROLE_STOP_LOSS, 0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "world, source",
    [
        (FakeExchange, PROTECTION_SOURCE_ENGINE),
        (_AttachedBracketExchange, PROTECTION_SOURCE_EXCHANGE_ADOPTED),
    ],
    ids=["engine_placed", "exchange_adopted"],
)
async def test_i3_a_restored_contract_does_not_provoke_a_second_bracket(
    tmp_path, world, source
):
    """P2-2's headline requirement: no duplicate protection after a restart.

    A restored record that still knows its contract takes the early return, so
    the first protection decision after a restart places nothing -- for BOTH
    provenances. The adopted case is the one that used to be able to go wrong:
    its legs are Delta's, so a record that had forgotten them would have looked
    unprotected and asked for a pair of its own on top of Delta's.
    """
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = _protected_record(protection_source=source)

    restarted_exchange = world(positions=[_position_json()])
    restarted = _manager(restarted_exchange)
    restore_execution_state_from_file(
        restarted, persist_execution_state(manager, tmp_path / "state.json"))
    restored = restarted._active_trades[SETUP]
    assert restored.protection_source == source
    before = _contract(restored)

    await asyncio.wait_for(
        restarted._ensure_bracket_protection(restored, QUANTITY), timeout=5)

    assert restarted_exchange.placed == [], restarted_exchange.placed
    assert _writes(restarted_exchange) == []
    # Nothing was ASKED, either: the restored contract answered the question by
    # itself. In the adopted world this exchange would have confirmed its own legs
    # and re-adopted them, which would look like success while proving nothing
    # about what the file remembered.
    assert restarted_exchange.requests == [], restarted_exchange.paths()
    assert _contract(restored) == before


@pytest.mark.asyncio
async def test_i4_restored_pending_protection_is_not_read_as_protection(tmp_path):
    """The third state the field has to keep: protection intended, not yet held.

    PROTECTION_PENDING with no provenance and no legs is a filled position that is
    genuinely bare. Restoring it as anything else would be the opposite failure to
    i3's -- a real position left unprotected because the file implied otherwise --
    so the restored record must go on to place exactly one pair.
    """
    pending = _filled_unprotected_record(state=TradeLifecycleState.PROTECTION_PENDING)
    manager = _manager(FakeExchange())
    manager._active_trades[SETUP] = pending

    exchange = FakeExchange(positions=[_position_json()])
    restarted = _manager(exchange)
    restore_execution_state_from_file(
        restarted, persist_execution_state(manager, tmp_path / "state.json"))
    restored = restarted._active_trades[SETUP]
    assert restored.state == TradeLifecycleState.PROTECTION_PENDING
    assert restored.protection_source is None
    assert restored.protected_quantity == Decimal("0")

    await asyncio.wait_for(
        restarted._ensure_bracket_protection(restored, QUANTITY), timeout=5)

    assert len(exchange.placed) == 2, exchange.placed
    assert restored.protection_source == PROTECTION_SOURCE_ENGINE
    assert restored.state == TradeLifecycleState.PROTECTED_POSITION
    assert restored.protected_quantity == QUANTITY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [PROTECTION_SOURCE_ENGINE, PROTECTION_SOURCE_EXCHANGE_ADOPTED],
    ids=["engine_placed", "exchange_adopted"],
)
async def test_i5_cancelling_the_pair_forgets_the_contract_it_had(tmp_path, source):
    """Provenance describes a contract that exists, and stops when it stops.

    Once both legs are cancelled there is no contract of either kind, so the
    field is cleared rather than left saying ENGINE_PLACED or EXCHANGE_ADOPTED.
    A restart inheriting a stale value would believe in protection the exchange
    no longer holds -- the same wrong answer as i4's, arrived at from the file.
    """
    exchange = _CancellingExchange()
    manager = _manager(exchange)
    record = _protected_record(protection_source=source)
    manager._active_trades[SETUP] = record

    await asyncio.wait_for(
        manager._cancel_existing_brackets(record, PRODUCT_ID), timeout=5)

    assert len(exchange.cancelled) == 2, exchange.cancelled
    assert record.protection_source is None
    assert record.sl_order_id is None and record.sl_client_order_id is None
    assert record.tp_order_id is None and record.tp_client_order_id is None
    assert record.protected_quantity == Decimal("0")

    restarted = _round_trip(manager, tmp_path / "state.json")
    restored = restarted._active_trades[SETUP]

    assert restored.protection_source is None
    assert restored.protected_quantity == Decimal("0")
    assert restored.sl_order_id is None and restored.tp_order_id is None


@pytest.mark.asyncio
async def test_i6_a_resize_hands_the_contract_over_and_says_so(tmp_path):
    """An adopted contract that the engine replaces is re-stamped, not remembered.

    A growing partial fill cancels Delta's legs and places the engine's own
    correctly sized pair, so the contract changes hands mid-position. The
    provenance has to follow it: the restored record must say ENGINE_PLACED, and
    its generation must be the one that names the legs actually resting (SL2/TP2),
    because that is what the next resize or cancellation has to address.
    """
    exchange = _CancellingExchange(positions=[_position_json(size="2")])
    manager = _manager(exchange)
    record = _protected_record(
        protection_source=PROTECTION_SOURCE_EXCHANGE_ADOPTED,
        sl_order_id=str(ADOPTED_SL_ORDER_ID),
        sl_client_order_id=ADOPTED_SL_CLIENT_ID,
        tp_order_id=str(ADOPTED_TP_ORDER_ID),
        tp_client_order_id=ADOPTED_TP_CLIENT_ID,
        filled_quantity=Decimal("2"),
    )
    manager._active_trades[SETUP] = record

    await asyncio.wait_for(
        manager._ensure_bracket_protection(record, Decimal("2")), timeout=5)

    assert len(exchange.cancelled) == 2, exchange.cancelled
    assert len(exchange.placed) == 2, exchange.placed
    assert record.protection_source == PROTECTION_SOURCE_ENGINE
    assert record.protection_generation == 1
    assert record.protected_quantity == Decimal("2")
    assert record.sl_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, protection_role(ORDER_ROLE_STOP_LOSS, 1))
    contract = _contract(record)

    restarted = _round_trip(manager, tmp_path / "state.json")

    assert _contract(restarted._active_trades[SETUP]) == contract


@pytest.mark.asyncio
async def test_i7_reconciliation_clears_a_contract_the_exchange_no_longer_has(
    tmp_path,
):
    """The authoritative answer overrides the file, and the file records that.

    The exchange holds the position but neither leg: whatever the record believed
    it had, it has nothing. Reconciliation drops the dead identity, clears the
    provenance, and the bracket path rebuilds -- so the contract that survives the
    restart is the one the exchange can confirm, named ENGINE_PLACED because the
    engine is what placed it.
    """
    exchange = FakeExchange(positions=[_position_json()], open_orders=[])
    manager = _manager(exchange)
    record = _protected_record(
        protection_source=PROTECTION_SOURCE_EXCHANGE_ADOPTED,
        sl_order_id=str(ADOPTED_SL_ORDER_ID),
        sl_client_order_id=ADOPTED_SL_CLIENT_ID,
        tp_order_id=str(ADOPTED_TP_ORDER_ID),
        tp_client_order_id=ADOPTED_TP_CLIENT_ID,
    )
    manager._active_trades[SETUP] = record

    summary = await asyncio.wait_for(
        manager.reconcile_active_trades_with_exchange(ACCOUNT, USER), timeout=5)

    assert SETUP in summary["protection_restored"], summary
    assert record.protection_source == PROTECTION_SOURCE_ENGINE
    assert record.sl_order_id not in (None, str(ADOPTED_SL_ORDER_ID))
    assert record.tp_order_id not in (None, str(ADOPTED_TP_ORDER_ID))
    assert len(exchange.placed) == 2, exchange.placed
    contract = _contract(record)

    restarted = _round_trip(manager, tmp_path / "state.json")

    assert _contract(restarted._active_trades[SETUP]) == contract


# ══ J. P2-3: the durable store under concurrency and failure ══════════════════
#
# The findings were about the write, not the schema: two writers could interleave
# on one path, a failed write could take the previous good file with it, the
# rename was not proved durable, and a restore that merged would let a stale
# in-memory record outlive the state that authorised it. What follows tests the
# store the way it will actually be used -- several threads, a failing filesystem,
# and a restart into a process that is not empty.


def _leftover_temp_files(path: Path) -> List[str]:
    """Any temp file the store left in `path`'s directory."""
    return sorted(p.name for p in path.parent.glob(f"{path.name}.*.tmp"))


def _distinct_manager(setup_id: str) -> TradeLifecycleManager:
    """A manager whose persisted body is distinguishable from every other one's."""
    manager = _manager(FakeExchange())
    manager._active_trades[setup_id] = _protected_record(setup_id)
    return manager


def test_j1_concurrent_writers_leave_one_whole_body_not_a_blend(tmp_path):
    """Eight threads, one path: every write either lands whole or raises.

    The failure this prevents is not a torn file but a false SUCCESS: writer A
    replaces the target, writer B replaces it a microsecond later, and A's
    read-back verification then passes on B's bytes -- so A returns having
    "verified" a file that does not contain what A wrote. Serializing writers per
    path is what makes the read-back mean anything.
    """
    path = tmp_path / "state.json"
    setups = [f"{SETUP}_J1_{index}" for index in range(8)]
    # The managers are built ONCE and their bodies taken from those same objects:
    # a record stamps its own `created_at`, so a second manager for the same setup
    # id is a different body and the comparison below would prove nothing.
    managers = {setup: _distinct_manager(setup) for setup in setups}
    bodies = {
        setup: serialize_execution_state_body(export_execution_state(manager))
        for setup, manager in managers.items()
    }
    errors: List[BaseException] = []

    def _writer(setup: str) -> None:
        try:
            persist_execution_state(managers[setup], path)
        except BaseException as exc:                            # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(setup,)) for setup in setups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a writer never finished"

    assert errors == [], [repr(exc) for exc in errors]
    written = path.read_bytes()
    assert written in bodies.values(), "the file is not any single writer's body"
    body = load_execution_state(path)                # loads, validates, and is whole
    assert len(body["active_trades"]) == 1, body["active_trades"].keys()
    assert _leftover_temp_files(path) == []


def test_j2_concurrent_writers_never_share_a_temp_file(tmp_path):
    """The temp name carries pid, thread id and a sequence number, and must.

    A fixed `<target>.tmp` is the other way two writers corrupt each other: both
    open the same temp file, one fsyncs a half-written blend of the two payloads,
    and `os.replace` makes it the state file. The observation is taken at the real
    `os.replace` call, so it is the name the store actually used.
    """
    path = tmp_path / "state.json"
    seen: List[str] = []
    guard = threading.Lock()
    real_replace = os.replace

    def _recording_replace(src, dst, *args, **kwargs):
        with guard:
            seen.append(Path(src).name)
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(execution_state_store_module.os, "replace", _recording_replace)
        threads = [
            threading.Thread(
                target=persist_execution_state,
                args=(_distinct_manager(f"{SETUP}_J2_{index}"), path),
            )
            for index in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)

    assert len(seen) == 6, seen
    assert len(set(seen)) == 6, seen                  # no two writers shared a name
    for name in seen:
        assert name.startswith(f"{path.name}.{os.getpid()}."), name
        assert name.endswith(".tmp"), name
    assert _leftover_temp_files(path) == []


def test_j3_a_failed_write_leaves_the_previous_state_recoverable(tmp_path):
    """A write that cannot complete must not consume the state it was replacing.

    The state file is how a restart discovers a live trade. If a failed write
    truncated or removed it, the failure mode is a process that comes up believing
    the account is flat while a real position is open -- so the previous body must
    still be there, byte for byte, and still restorable.
    """
    path = tmp_path / "state.json"
    first = _distinct_manager(f"{SETUP}_J3_FIRST")
    persist_execution_state(first, path)
    good = path.read_bytes()

    def _refuse(src, dst, *args, **kwargs):
        raise OSError(28, "simulated: no space left on device")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(execution_state_store_module.os, "replace", _refuse)
        message = _refusal(
            ExecutionStatePersistenceError,
            persist_execution_state,
            _distinct_manager(f"{SETUP}_J3_SECOND"),
            path,
        )

    assert "could not write execution state" in message
    assert path.read_bytes() == good                 # untouched, not truncated
    assert _leftover_temp_files(path) == []          # the temp file was cleaned up
    recovered = _manager(FakeExchange())
    restore_execution_state_from_file(recovered, path)
    assert list(recovered._active_trades) == [f"{SETUP}_J3_FIRST"]


def test_j4_repeated_persistence_of_unchanged_state_is_byte_identical(tmp_path):
    """Deterministic serialization, and a stale temp file that changes nothing.

    Byte-identical output is what makes the gate's fingerprint a usable freshness
    signal, so it is asserted across separate writes rather than one. The planted
    temp file is what a crashed earlier write leaves behind: the loader must not
    read it, and the next write must not collide with it.
    """
    path = tmp_path / "state.json"
    manager = _distinct_manager(f"{SETUP}_J4")
    stale = path.with_name(f"{path.name}.999999.1.999.tmp")
    stale.write_bytes(b"{ this is a crashed write, not state }")

    persist_execution_state(manager, path)
    once = path.read_bytes()
    persist_execution_state(manager, path)
    twice = path.read_bytes()

    assert once == twice
    assert load_execution_state(path)["active_trades"].keys() == {f"{SETUP}_J4"}
    assert stale.read_bytes() == b"{ this is a crashed write, not state }"
    stale.unlink()
    assert _leftover_temp_files(path) == []


def test_j5_restore_replaces_live_collections_instead_of_merging_them(tmp_path):
    """A record the file does not name must not survive the restore.

    This is the merge bug in its dangerous direction: a stale in-memory trade that
    outlived the state authorising it is a position this process would go on to
    manage -- resize, close, kill-switch -- without any persisted evidence that it
    exists. The collections are also asserted to be the SAME objects, because they
    are mutated in place so a collaborator holding a reference sees the restore.
    """
    donor = _distinct_manager(f"{SETUP}_J5_FILE")
    donor._raise_reconciliation_alert("FILE_CONDITION", SYMBOL, "written to the file")
    path = persist_execution_state(donor, tmp_path / "state.json")

    live = _manager(FakeExchange())
    live._active_trades[f"{SETUP}_J5_STALE"] = _protected_record(f"{SETUP}_J5_STALE")
    live._entry_fill_sizes[f"{SETUP}_J5_STALE"] = QUANTITY
    live._processed_fill_trade_ids.add("stale-fill-1")
    live._raise_reconciliation_alert("STALE_CONDITION", SYMBOL, "in memory only")
    trades_object = live._active_trades
    alerts_object = live._reconciliation_alerts

    restore_execution_state_from_file(live, path)

    assert list(live._active_trades) == [f"{SETUP}_J5_FILE"]
    assert live._entry_fill_sizes == {}
    assert live._processed_fill_trade_ids == set()
    assert [alert["code"] for alert in live._reconciliation_alerts] == ["FILE_CONDITION"]
    assert live._active_trades is trades_object
    assert live._reconciliation_alerts is alerts_object


@pytest.mark.asyncio
async def test_j6_a_held_lock_the_file_never_mentioned_survives_and_blocks(tmp_path):
    """The one structure restored by merge, and why that is the safe direction.

    Dropping a held in-memory lock would permit a second concurrent trade, so
    `load_state` keeps a lock the file does not name. The disagreement that leaves
    -- a lock held for a setup with no live record -- is not repaired here either:
    the startup gate refuses to admit an entry until an operator resolves it.
    """
    path = _flat_state(tmp_path / "state.json")
    manager = _manager(FakeExchange())
    manager.single_trade_lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP, symbol=SYMBOL)

    restore_execution_state_from_file(manager, path)

    assert manager._active_trades == {}                        # the file replaced these
    held, setup_id, symbol = manager.single_trade_lock.is_locked(USER, ACCOUNT)
    assert (held, setup_id, symbol) == (True, SETUP, SYMBOL)   # the lock was kept

    gate = _gate(manager, path, _FakeRuntime())
    result = await asyncio.wait_for(gate.arm(), timeout=10)

    assert result.admitted is False
    assert "no live execution record" in result.block_reason, result.block_reason
    assert SETUP in result.block_reason, result.block_reason
    assert gate.authorization_current is False


def test_j7_a_restore_that_fails_halfway_leaves_the_manager_untouched(tmp_path):
    """All-or-nothing on the way in, not just on the way out.

    One good record and one corrupt record in the same body: the corrupt one is
    refused, and the good one is NOT applied. A half-restored manager is the worst
    of both files -- some trades from the state that was authorised and some from
    the state that is live -- and it is what "no partial state accepted" has to
    mean for the in-memory object as well as the file.
    """
    donor = _distinct_manager(f"{SETUP}_J7_GOOD")
    donor._active_trades[f"{SETUP}_J7_BAD"] = _protected_record(f"{SETUP}_J7_BAD")
    body = export_execution_state(donor)
    body["active_trades"][f"{SETUP}_J7_BAD"]["filled_quantity"] = _encoded(Decimal("-1"))

    live = _manager(FakeExchange())
    live._active_trades[f"{SETUP}_J7_LIVE"] = _protected_record(f"{SETUP}_J7_LIVE")
    before = copy.deepcopy(export_execution_state(live))

    message = _refusal(ExecutionStateCorruptError, restore_execution_state, live, body)

    assert "filled_quantity is negative" in message
    assert list(live._active_trades) == [f"{SETUP}_J7_LIVE"]
    assert export_execution_state(live) == before


# ══ K. P2-4: whose account this execution state belongs to ════════════════════
#
# One state file, one account. The finding was that nothing tied a persisted body
# to the account whose orders it describes, so the same file restored into a
# process configured for another account would hand that process a live trade,
# a fill ledger and a portfolio lock belonging to someone else -- and every
# subsequent close, resize or kill-switch would be aimed at the wrong book.

#: What a persisted identity may not be. Each mutation is applied to a real
#: exported body, so nothing but the identity is wrong with the file.
_IDENTITY_MUTATIONS: List[Tuple[str, Any, str]] = [
    ("not_an_object", "acc_exec_identity", "expected an object"),
    ("an_empty_account_id",
     {"account_id": "", "account_record_id": ACCOUNT, "user_id": USER},
     "cannot prove which account"),
    ("a_blank_account_record_id",
     {"account_id": ACCOUNT, "account_record_id": "   ", "user_id": USER},
     "cannot prove which account"),
    ("a_blank_user_id",
     {"account_id": ACCOUNT, "account_record_id": ACCOUNT, "user_id": " "},
     "present but empty"),
    ("a_missing_field",
     {"account_id": ACCOUNT, "account_record_id": ACCOUNT},
     "user_id"),
]

_IDENTITY_MUTATION_IDS = [case[0] for case in _IDENTITY_MUTATIONS]


def _foreign_account_body(setup_id: str) -> Dict[str, Any]:
    """A well-formed body belonging to a DIFFERENT account.

    Written by a manager whose store really is configured for that account, so
    the identity in it is the one that account's process would have written --
    not a field edited after the fact.
    """
    donor = _manager(FakeExchange(), store=_store(account_id=OTHER_ACCOUNT))
    record = _protected_record(setup_id)
    record.account_id = OTHER_ACCOUNT
    donor._active_trades[setup_id] = record
    return export_execution_state(donor)


def test_k1_a_body_states_the_account_it_belongs_to_and_round_trips(tmp_path):
    """The identity is persisted, is the manager's own, and survives the restart.

    `user_id` is carried as-is because `AccountRecord.user_id` is legitimately
    optional -- persisted as None rather than as an invented value, since an
    identity this module made up would prove nothing on restore.
    """
    manager = _distinct_manager(f"{SETUP}_K1")
    body = export_execution_state(manager)

    assert body["account_identity"] == {
        "account_id": ACCOUNT,
        "account_record_id": ACCOUNT,
        "user_id": USER,
    }

    restarted = _round_trip(manager, tmp_path / "state.json")

    assert list(restarted._active_trades) == [f"{SETUP}_K1"]
    assert export_execution_state(restarted)["account_identity"] == (
        body["account_identity"])


def test_k2_a_body_from_another_account_is_refused_before_it_is_decoded():
    """Identity is checked FIRST, and the manager is left exactly as it was.

    The foreign body also carries a record that would fail the composite check,
    so the error type is the proof of ordering: `ExecutionStateIncompatibleError`
    means the account was compared before any record was decoded. Corrupt would
    have meant the file was already being interpreted -- and a body half-applied
    from another account is the failure this check exists to prevent.
    """
    body = _foreign_account_body(f"{SETUP}_K2_FOREIGN")
    body["active_trades"][f"{SETUP}_K2_FOREIGN"]["filled_quantity"] = _encoded(
        Decimal("-1"))

    live = _distinct_manager(f"{SETUP}_K2_LIVE")
    before = copy.deepcopy(export_execution_state(live))

    message = _refusal(
        ExecutionStateIncompatibleError, restore_execution_state, live, body)

    assert "written for a different account" in message
    assert OTHER_ACCOUNT in message and ACCOUNT in message
    assert "Refusing to restore one account's live trades" in message
    assert list(live._active_trades) == [f"{SETUP}_K2_LIVE"]
    assert export_execution_state(live) == before


@pytest.mark.parametrize(
    "label, identity, fragment", _IDENTITY_MUTATIONS, ids=_IDENTITY_MUTATION_IDS)
def test_k3_an_unprovable_identity_is_refused(tmp_path, label, identity, fragment):
    """A file that cannot say which account it is for is not restored.

    Missing, empty and whitespace-only are all treated as "not stated": there is
    no substitution and no default account, so the only thing left to do with such
    a file is refuse it. The refusal is asserted through the FILE path too, since
    that is how a restart meets it.
    """
    manager = _distinct_manager(f"{SETUP}_K3")
    body = export_execution_state(manager)
    body["account_identity"] = identity

    live = _distinct_manager(f"{SETUP}_K3")
    assert fragment in _refusal(
        ExecutionStateCorruptError, restore_execution_state, live, body)

    path = tmp_path / "state.json"
    path.write_bytes(json.dumps(body, indent=2, sort_keys=True).encode("utf-8"))
    assert fragment in _refusal(
        ExecutionStateCorruptError, restore_execution_state_from_file, live, path)
    assert list(live._active_trades) == [f"{SETUP}_K3"]      # untouched


@pytest.mark.parametrize(
    "field, value, fragment",
    [
        ("store_account_id", "", "state_store.account_id"),
        ("store_account_id", None, "state_store.account_id"),
        ("record_account_id", "  ", "state_store.account.account_id"),
        ("record_user_id", "", "user_id is present but is not"),
    ],
    ids=["store_id_empty", "store_id_absent", "record_id_blank", "user_id_empty"],
)
def test_k4_a_process_that_cannot_state_its_account_does_not_persist(
    tmp_path, field, value, fragment
):
    """The write side of the same rule: no identity, no file.

    Persisting execution state without the account it belongs to would create
    exactly the file k2 and k3 refuse, one restart later. So the refusal happens
    here, before anything is written -- and nothing is left on disk.
    """
    store = _store()
    if field == "store_account_id":
        store.account_id = value
    elif field == "record_account_id":
        store.account.account_id = value
    else:
        store.account.user_id = value
    manager = _manager(FakeExchange(), store=store)
    manager._active_trades[f"{SETUP}_K4"] = _protected_record(f"{SETUP}_K4")
    path = tmp_path / "state.json"

    message = _refusal(
        ExecutionStatePersistenceError, persist_execution_state, manager, path)

    assert "account_identity" in message
    assert fragment in message
    assert not path.exists()


@pytest.mark.asyncio
async def test_k5_the_gate_refuses_to_arm_on_another_accounts_state(tmp_path):
    """A restart pointed at the wrong file cannot trade, and says why.

    The gate treats an unloadable state file as inputs it cannot form a verdict
    from: it records the block, then raises rather than returning a verdict. Both
    halves are asserted, because the raise is what stops a composition from
    proceeding and the recorded block is what keeps refusing entries afterwards.
    """
    path = tmp_path / "state.json"
    donor = _manager(FakeExchange(), store=_store(account_id=OTHER_ACCOUNT))
    persist_execution_state(donor, path)

    manager = _manager(FakeExchange())                    # a DIFFERENT account
    gate = _gate(manager, path, _FakeRuntime())

    with pytest.raises(ExecutionStartupGateError) as excinfo:
        await asyncio.wait_for(gate.arm(), timeout=10)

    assert "written for a different account" in str(excinfo.value)
    assert gate.authorization_current is False
    reason = gate.admission_reason()
    assert reason is not None
    assert "could not be loaded" in reason, reason
    assert manager._active_trades == {}                   # nothing was adopted


def test_k6_no_credential_material_reaches_the_persisted_body():
    """P2-4's "do not log secrets", asserted against the bytes that hit the disk.

    The identity is account ids, which are not secrets; the API key and secret the
    client was constructed with must not appear anywhere in the file. This is
    checked on the serialized body rather than on the identity dict, because the
    whole body is what an operator, a backup and a support bundle will contain.
    """
    manager = _distinct_manager(f"{SETUP}_K6")
    payload = serialize_execution_state_body(export_execution_state(manager))

    assert b"TEST_KEY_EXEC_IDENTITY" not in payload
    assert b"TEST_SECRET_EXEC_IDENTITY" not in payload
    lowered = payload.lower()
    for forbidden in (b"api_key", b"api_secret", b"signature", b"authorization"):
        assert forbidden not in lowered, forbidden


# ══ L. P2-5: no execution-critical value is this module's own invention ═══════
#
# A value that decides the SIZE, the PRICE, the NAME or the INSTRUMENT of a real
# order either comes from the strategy or the order is not sent. What happened
# instead was substitution: quantity became one contract, leverage became 100x --
# the top of the authorised band, the one value guaranteed to pass the band check
# downstream -- and an absent setup id became the literal string "None" inside a
# client order id, which is the P0-1 collision class re-entering through an
# unguarded identity rather than through a random suffix.
#
# Every test here asserts against `_writes(exchange)`. "Refused" has to mean no
# request that could change exchange state was ever made -- not that a rejected
# record was returned after the POST.

#: Forms an execution-critical price or size can arrive in that CANNOT be used.
#: `True` is included because `bool` is an int subclass, so an unguarded `> 0`
#: test reads it as one contract; a non-numeric string because that is what an
#: upstream JSON boundary produces when a field went missing.
_UNUSABLE_NUMBERS: List[Tuple[str, Any]] = [
    ("none", None),
    ("zero", Decimal("0")),
    ("negative", Decimal("-1")),
    ("bool_true", True),
    ("non_numeric", "not-a-number"),
]
_UNUSABLE_NUMBER_IDS = [case[0] for case in _UNUSABLE_NUMBERS]

#: Identities that cannot NAME an order. `str()` of any of them is either empty
#: or a value no strategy chose, and `generate_deterministic_client_order_id`
#: would hash it into a real, shared order name.
_UNUSABLE_IDENTITIES: List[Tuple[str, Any]] = [
    ("none", None),
    ("empty", ""),
    ("whitespace", "   "),
    ("bool_true", True),
]
_UNUSABLE_IDENTITY_IDS = [case[0] for case in _UNUSABLE_IDENTITIES]


def _refused_cleanly(
    record: Any,
    exchange: FakeExchange,
    manager: TradeLifecycleManager,
    code: str,
    *,
    setup_id: Any = SETUP,
    account_id: str = ACCOUNT,
) -> None:
    """The whole shape of a P2-5 refusal, asserted in one place.

    Four claims, all of which must hold for "fail closed" to be true: the record
    says it was rejected and why; it invents NO figure (a refusal used to report
    the risk and reward of a trade that never existed, computed from a quantity
    this module chose); the exchange was never asked to change anything; and the
    account's single-trade lock is free, so one refused setup does not lock the
    account out of the next one.
    """
    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == code, record.rejection_code
    zero = Decimal("0")
    assert record.requested_quantity == zero          # never "1 contract"
    assert record.entry_price == zero
    assert record.stop_loss_price == zero
    assert record.take_profit_price == zero
    assert record.risk_reward_ratio == zero
    assert record.risk_amount == zero                 # never a derived figure
    assert record.reward_amount == zero
    assert _writes(exchange) == [], _writes(exchange)
    assert setup_id not in manager._active_trades
    locked, _held, _sym = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=account_id)
    assert locked is False


def _numeric_or_fallbacks(func: Any) -> List[Tuple[int, str]]:
    """Every `<something> or <number>` in `func`, with real line numbers.

    Only the TOP-LEVEL operands of an `or` are examined. `getattr(d, "x", None)
    or getattr(d, "y", Decimal("2.0"))` is therefore not reported: its right
    operand is a call, and the literal inside it is that call's default, not a
    substituted execution value. Both of the surviving reporting-only defaults
    are pinned by l8 instead of being hidden by a looser scan here.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    base = func.__code__.co_firstlineno - 1
    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        for operand in node.values[1:]:
            literal = (
                isinstance(operand, ast.Constant)
                and isinstance(operand.value, (int, float))
                and not isinstance(operand.value, bool)
            )
            wrapped = (
                isinstance(operand, ast.Call)
                and isinstance(operand.func, ast.Name)
                and operand.func.id in ("Decimal", "int", "float")
                and len(operand.args) == 1
                and isinstance(operand.args[0], ast.Constant)
            )
            if literal or wrapped:
                found.append((base + operand.lineno, ast.unparse(node)))
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [case[1] for case in _UNUSABLE_NUMBERS], ids=_UNUSABLE_NUMBER_IDS)
async def test_l1_an_unusable_entry_price_is_refused_before_any_order(value):
    """The entry price is what a real order rests at. There is no substitute.

    `decision.entry_price` is a property returning `decision.entry`, so the
    `entry or entry_price` read in the production path cannot rescue any of these
    forms from a second field -- which is exactly why the refusal has to be here.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(entry=value), ACCOUNT, user_id=USER)

    _refused_cleanly(record, exchange, manager, REFUSAL_MISSING_ENTRY_PRICE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [case[1] for case in _UNUSABLE_NUMBERS], ids=_UNUSABLE_NUMBER_IDS)
async def test_l2_an_unusable_quantity_is_refused_before_any_order(value):
    """The quantity is the size of a real position, and it was defaulting to 1."""
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(quantity=value), ACCOUNT, user_id=USER)

    _refused_cleanly(record, exchange, manager, REFUSAL_MISSING_QUANTITY)


@pytest.mark.asyncio
async def test_l3_an_absent_calculated_leverage_is_refused_before_any_order():
    """Absence is refused; a PRESENT out-of-band value is left to the gateway.

    The distinction is the whole point. `or 100` substituted the top of the
    authorised band, which multiplies real margin exposure by up to 100 and is
    guaranteed to pass the band check downstream. Refusing absence does not
    touch the authorised 1x-100x policy: `CHECK_LEVERAGE_CAP` still owns
    present-but-invalid, and it rejects rather than clamps.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(calculated_leverage=None), ACCOUNT, user_id=USER)

    _refused_cleanly(record, exchange, manager, REFUSAL_MISSING_LEVERAGE)


def test_l4_no_numeric_fallback_survives_in_the_canonical_submission_path():
    """Structural: prose cannot be trusted on "the defaults are gone".

    A refusal test only proves the paths it walks. This walks the code, so a
    numeric substitution reintroduced anywhere in either half of the canonical
    path fails here even if no test happens to exercise that branch.
    """
    offenders = (
        _numeric_or_fallbacks(TradeLifecycleManager.execute_trade_setup)
        + _numeric_or_fallbacks(TradeLifecycleManager._execute_admitted_trade_setup)
    )

    assert offenders == [], "\n".join(
        f"trade_lifecycle.py:{line}: {src}" for line, src in offenders)


@pytest.mark.asyncio
async def test_l5_an_unregistered_product_is_refused_before_any_order():
    """The instrument is execution-critical: there is no default product.

    A symbol the registry does not know cannot be given a product id, a tick
    size or a contract value, and every one of those decides what a submitted
    order actually buys. `UNSUPPORTED_SYMBOL` is the established code, and the
    lock taken at step 0 is released on the way out.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(symbol="NOSUCHUSD"), ACCOUNT, user_id=USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL.value
    assert record.product_id is None                   # never a guessed product
    assert _writes(exchange) == [], _writes(exchange)
    locked, _held, _sym = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is False
    # Documented, deliberately not "fixed" here: this refusal happens AFTER the
    # record is registered, so the setup stays in `_active_trades` as rejected
    # and the same setup id cannot be retried in this process. That is the
    # restrictive direction (the duplicate-setup guard refuses it, the lock is
    # free for other setups) and reshaping it is not part of P2-5.
    assert manager._active_trades[SETUP] is record


def test_l6_an_absent_setup_id_would_name_every_order_the_same():
    """Why the l6/l7 guards exist, stated as arithmetic rather than as prose.

    `generate_deterministic_client_order_id` stringifies its inputs, so `None`
    hashes to exactly what the literal string "None" hashes to. Two unrelated
    setups with no id therefore submit under ONE client order id, and Delta's
    idempotency on that name means the second entry is either rejected as a
    duplicate or silently treated as the first -- the P0-1 collision class,
    re-entered through an unguarded identity instead of a random suffix.
    """
    from_none = generate_deterministic_client_order_id(ACCOUNT, None, ORDER_ROLE_ENTRY)
    from_string = generate_deterministic_client_order_id(
        ACCOUNT, "None", ORDER_ROLE_ENTRY)

    assert from_none == from_string, "the collision this guard exists to prevent"
    assert from_none == generate_deterministic_client_order_id(
        ACCOUNT, None, ORDER_ROLE_ENTRY)          # and it is stable, so it recurs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [case[1] for case in _UNUSABLE_IDENTITIES], ids=_UNUSABLE_IDENTITY_IDS)
async def test_l7_an_unusable_setup_identity_is_refused_before_the_lock(value):
    """A setup that cannot be named cannot be submitted.

    Refused BEFORE the admission critical section, so no lock is taken and no
    duplicate-setup slot is occupied by a setup id that is not one.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(setup_id=value), ACCOUNT, user_id=USER)

    _refused_cleanly(
        record, exchange, manager,
        trade_lifecycle_module.REFUSAL_MISSING_SETUP_IDENTITY, setup_id=value)
    assert manager._active_trades == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [case[1] for case in _UNUSABLE_IDENTITIES], ids=_UNUSABLE_IDENTITY_IDS)
async def test_l8_an_unusable_account_identity_is_refused_before_the_lock(value):
    """The account is half of every order name and all of the lock's scope.

    A blank account id hashes into a real client order id and takes a lock under
    a blank key, so two different accounts would share one lock and one order
    name. Nothing is substituted: the entry is refused.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(), value, user_id=USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == (
        trade_lifecycle_module.REFUSAL_MISSING_ACCOUNT_IDENTITY)
    assert _writes(exchange) == [], _writes(exchange)
    assert manager._active_trades == {}
    locked, _held, _sym = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["stop_loss", "take_profit"])
@pytest.mark.parametrize("value", [None, "not-a-price"], ids=["none", "non_numeric"])
async def test_l9_an_absent_stop_or_target_is_refused_not_crashed(missing, value):
    """The stop is the only thing bounding the position's loss.

    Before the guard this did not refuse -- it CRASHED. Step 4's
    `stop_loss < entry_price < take_profit` raised TypeError outside every
    handler, so the caller got an exception instead of a rejected record and the
    single-trade lock taken at step 0 was stranded until the process restarted:
    the account could not trade again at all. The code is the repository's
    existing vocabulary for this condition, the one
    `OrderValidationGateway.CHECK_MISSING_SL` / `CHECK_MISSING_TP` already
    emits, so an operator sees one code whichever layer refuses first.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)
    expected = {
        "stop_loss": RejectionReasonCode.MISSING_STOP_LOSS.value,
        "take_profit": RejectionReasonCode.MISSING_TAKE_PROFIT.value,
    }[missing]

    record = await manager.execute_trade_setup(
        _decision(**{missing: value}), ACCOUNT, user_id=USER)

    _refused_cleanly(record, exchange, manager, expected)


@pytest.mark.asyncio
async def test_l10_a_refused_stop_does_not_strand_the_account_lock():
    """The consequence that made the crash worse than the refusal.

    A stranded lock is not a one-setup problem: the NEXT setup, which is
    perfectly valid, is refused too. So the proof is that a second entry still
    reaches the exchange after the first was refused.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    refused = await manager.execute_trade_setup(
        _decision(setup_id="L10_NO_STOP", stop_loss=None), ACCOUNT, user_id=USER)
    following = await manager.execute_trade_setup(
        _decision(setup_id="L10_VALID"), ACCOUNT, user_id=USER)

    assert refused.rejection_code == RejectionReasonCode.MISSING_STOP_LOSS.value
    assert following.state is not TradeLifecycleState.ENTRY_REJECTED
    assert following.rejection_code is None
    assert [w for w in _writes(exchange) if w.startswith("POST")], _writes(exchange)


@pytest.mark.asyncio
async def test_l11_the_surviving_reward_risk_default_never_reaches_the_wire():
    """A reporting-only substitution, pinned as reporting-only.

    `risk_reward or Decimal("2.0")` survives on purpose: reward:risk is COMPUTED
    AND REPORTED, never enforced (there is no minimum-R:R admission rule), so the
    figure decides nothing about the order. What must stay true is that it is not
    executable -- it cannot appear in a payload, and it cannot change what is
    sent. Deleting it instead would change the persisted ledger shape of every
    historical record for no safety gain; leaving it unpinned is how a reporting
    default later becomes an executable one.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(risk_reward=None), ACCOUNT, user_id=USER)

    assert record.state is not TradeLifecycleState.ENTRY_REJECTED
    assert record.risk_reward_ratio == Decimal("2.0")      # reported, not chosen
    assert record.requested_quantity == QUANTITY           # the executable values
    assert record.entry_price == ENTRY_PRICE               # are the strategy's
    for request in exchange.requests:
        if request.method == "POST":
            assert b"risk_reward" not in request.content.lower(), request.content


@pytest.mark.asyncio
async def test_l12_an_absent_user_id_resolves_the_same_on_acquire_and_release():
    """The one remaining string default, and why it can only over-restrict.

    `user_id or account.user_id or "default_user"` is resolved identically when
    the lock is TAKEN and when it is RELEASED, so it cannot release a lock it did
    not take and cannot leave one behind. It scopes a local lock; it is never an
    account identity sent anywhere. Proven by refusing a setup and then admitting
    the next one on a store that has no user id at all.
    """
    exchange = FakeExchange()
    store = _store()
    store.account.user_id = None
    manager = _manager(exchange, store=store)

    refused = await manager.execute_trade_setup(
        _decision(setup_id="L12_NO_QTY", quantity=None), ACCOUNT)
    following = await manager.execute_trade_setup(
        _decision(setup_id="L12_VALID"), ACCOUNT)

    assert refused.rejection_code == REFUSAL_MISSING_QUANTITY
    assert following.state is not TradeLifecycleState.ENTRY_REJECTED
    locked, held, _sym = manager.single_trade_lock.is_locked(
        user_id="default_user", account_id=ACCOUNT)
    assert (locked, held) == (True, "L12_VALID")           # taken by the survivor


@pytest.mark.asyncio
async def test_l13_an_unusable_credential_refuses_before_the_exchange_call():
    """The credential boundary that IS enforced, pinned here as the contrast case.

    `ValidationContext(api_key=getattr(self.client, "_api_key", "MOCKED_API_KEY"))`
    substitutes a placeholder only when the injected client has NO such attribute
    at all -- which no real `DeltaIndiaClient` can be, since it always sets it and
    refuses to sign without it. When the attribute is present but unusable, the
    gateway's `CHECK_API_CREDENTIALS` refuses and nothing is submitted. No
    credential value is constructed, read from the environment, or logged here:
    the key below is a literal four-character test string.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)
    manager.client._api_key = "abcd"                       # present, but too short

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, user_id=USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.INVALID_CREDENTIALS.value
    assert _writes(exchange) == [], _writes(exchange)
    locked, _held, _sym = manager.single_trade_lock.is_locked(
        user_id=USER, account_id=ACCOUNT)
    assert locked is False




