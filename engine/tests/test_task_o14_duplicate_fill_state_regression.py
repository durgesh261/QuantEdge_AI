"""
Task O §O14 -- a duplicate fill notification must never de-protect a position.

Observed on the live §O14 XRPUSD trade. `execute_trade_setup` saw the entry come
back `filled`, called `on_entry_fill` itself, and the position reached
`PROTECTED_POSITION` by adopting Delta's attached bracket. The operator's poll
loop then reported the same fill a second time, and the record's own history
ends:

    protected_position -> entry_filled   "Entry completely filled 83 @ 3.0007"

Three separate properties combined to produce that:

  1. `record_transition` records whatever it is given -- there is no transition
     validation anywhere in the state machine, so a backwards edge is writable.
  2. `on_entry_fill` / `on_entry_partial_fill` are PUBLIC and were unguarded.
     `_apply_entry_order_state` -- the documented convergence point for
     WebSocket and REST observations -- already refuses terminal records and
     duplicate fills (§M10), but `execute_trade_setup`'s synchronous
     immediate-fill branch and any operator poll loop call the handlers
     directly and so never met those guards.
  3. `_ensure_bracket_protection` correctly no-ops when the record is already
     protected for that size, so nothing restored `PROTECTED_POSITION`
     afterwards. The record was left claiming an unprotected position that was
     in fact protected, with both adopted leg ids still on it.

The fix puts the guards on the handlers themselves, which is where every caller
passes, and makes `filled_quantity` monotonic there as it already is in
`_apply_entry_order_state` and in reconciliation. A record that is NOT fully
protected is never refused, so the PROTECTION_FAILED retry path is untouched.

Groups: A the normal edge; B the backwards edge; C/D adopted leg ids; E
protected quantity; F no second pair; G reconciliation; H `close_position`; I
the unprotected/failure path; J the synchronous and polling paths together; K
a stale SMALLER fill cannot shrink protection; L a legitimate downward resize of
an adopted bracket.

Zero network access: every request is served by `httpx.MockTransport`, reusing
the §O13 exchange shapes so adoption is exercised through the real code path.
"""

from decimal import Decimal
from typing import Any, List

import httpx
import pytest

from quantedge.execution.models import OrderStatus
from quantedge.execution.trade_lifecycle import (
    CloseReason,
    TradeLifecycleState,
)

from test_task_o13_bracket_adoption import (  # noqa: E402
    ACCOUNT,
    ENGINE_SL_ID,
    ENGINE_TP_ID,
    ENTRY_ORDER_ID,
    ENTRY_PRICE,
    EXCHANGE_SL_LEG_ID,
    EXCHANGE_TP_LEG_ID,
    PRODUCT_ID,
    SETUP,
    SIZE,
    SL_PRICE,
    SYMBOL,
    TP_PRICE,
    USER,
    _actions,
    _client,
    _entry_json,
    _manager,
    _position_json,
    _record,
    _responder,
    _sl_leg,
    _store,
    _tp_leg,
)

pytestmark = pytest.mark.asyncio

FILL_PRICE = ENTRY_PRICE


@pytest.fixture(autouse=True)
def _no_adoption_sleep(monkeypatch):
    """Adoption retry SPACING is not what any test here is about, and the §O13
    suite asserts the bound itself. Patched to 0 so the paths where adoption
    legitimately fails to find legs do not spend seconds sleeping."""
    import quantedge.execution.trade_lifecycle as tl

    monkeypatch.setattr(tl, "BRACKET_ADOPTION_DELAY_SECONDS", 0.0)


def _registered(manager, **over):
    """A trade whose entry is live on the exchange, holding the portfolio slot.

    This is the state `execute_trade_setup` leaves behind immediately before it
    observes the fill, which is where both the synchronous handler call and any
    operator poll loop start from.
    """
    record = _record(state=TradeLifecycleState.ENTRY_SUBMITTED, **over)
    manager._active_trades[record.setup_id] = record
    manager.single_trade_lock.acquire_lock(USER, ACCOUNT, record.setup_id, SYMBOL)
    return record


def _edges(record) -> List[str]:
    return [f'{e["from_state"]}->{e["to_state"]}' for e in record.history]


def _adopting_client(**over):
    """The §O14 live shape: Delta echoes the attached bracket on the entry and
    holds exactly one matching reduce-only leg per side."""
    return _client(_responder(resting=[_sl_leg(), _tp_leg()], **over))


# ══ A. The normal edge still works ════════════════════════════════════════════


async def test_a01_a_first_fill_still_reaches_protected_position():
    """The forward path, unchanged: ENTRY_FILLED then PROTECTED_POSITION."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = _registered(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert _edges(record) == [
        f"{TradeLifecycleState.ENTRY_SUBMITTED.value}->{TradeLifecycleState.ENTRY_FILLED.value}",
        f"{TradeLifecycleState.ENTRY_FILLED.value}->{TradeLifecycleState.PROTECTION_PENDING.value}",
        f"{TradeLifecycleState.PROTECTION_PENDING.value}->{TradeLifecycleState.PROTECTED_POSITION.value}",
    ]
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == SIZE
    assert record.average_fill_price == FILL_PRICE
    assert rec.posts() == []
    assert "PROTECTION_ADOPTED_FROM_EXCHANGE" in _actions(store)
    await client.close()


async def _protected(manager):
    """A position that has reached PROTECTED_POSITION by adopting Delta's legs."""
    record = _registered(manager)
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    return record


# ══ B. The backwards edge, which is the defect ════════════════════════════════


async def test_b01_a_duplicate_fill_cannot_rewind_protected_position():
    """The §O14 defect itself: `protected_position -> entry_filled`.

    The duplicate is not merely tolerated -- it must leave no trace, because a
    transition that is recorded is a transition that happened.
    """
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)
    before = list(_edges(record))

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert _edges(record) == before
    backwards = f"{TradeLifecycleState.PROTECTED_POSITION.value}->{TradeLifecycleState.ENTRY_FILLED.value}"
    assert backwards not in _edges(record)
    assert rec.posts() == []
    await client.close()


async def test_b02_ten_duplicate_fills_change_nothing_at_all():
    """An operator poll loop reports the same fill every couple of seconds."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)
    before = list(_edges(record))

    for _ in range(10):
        await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert _edges(record) == before
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert rec.posts() == []
    assert rec.deletes() == []
    await client.close()


# ══ C/D/E. What the record still holds afterwards ═════════════════════════════


async def test_c01_the_adopted_stop_loss_order_id_survives_a_duplicate_fill():
    """`close_position`, the kill switch and reconciliation all act on this id.
    Losing it would leave the exchange's own stop resting and untracked."""
    store = _store()
    client, _rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    assert record.sl_client_order_id == _sl_leg()["client_order_id"]
    await client.close()


async def test_d01_the_adopted_take_profit_order_id_survives_a_duplicate_fill():
    store = _store()
    client, _rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.tp_order_id == str(EXCHANGE_TP_LEG_ID)
    assert record.tp_client_order_id == _tp_leg()["client_order_id"]
    await client.close()


async def test_e01_protected_quantity_still_matches_the_filled_quantity():
    """Reconciliation compares `protected_quantity` against the exchange's real
    position size, so a duplicate must not disturb either number."""
    store = _store()
    client, _rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.filled_quantity == SIZE
    assert record.protected_quantity == SIZE
    assert record.protected_quantity == record.filled_quantity
    await client.close()


# ══ F. No second protective pair ══════════════════════════════════════════════


async def test_f01_a_duplicate_fill_places_no_second_pair_after_adoption():
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    await _protected(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert rec.posts() == []
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    await client.close()


async def test_f02_a_duplicate_fill_neither_replaces_nor_cancels_an_engine_pair():
    """The pre-§G2 path: no exchange-attached bracket, so the engine's own pair
    is the only protection. A duplicate must not cancel and re-place it -- there
    is a window in every cancel/replace where the position is uncovered."""
    store = _store()
    client, rec = _client(_responder(entry=_entry_json(bracket=False)))
    manager = _manager(client, store)
    record = _registered(manager)
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert len(rec.posts()) == 2

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert len(rec.posts()) == 2
    assert rec.deletes() == []
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


# ══ I. The unprotected and protection-failed paths are untouched ══════════════


def _post_gated_responder(gate: dict, **over):
    """`_responder`, but POST /v2/orders is refused while `gate["reject"]`.

    Lets one test drive a real protection failure and then a real retry, which
    is the case the new guard must NOT swallow: a record that is not protected
    has to keep re-attempting exactly as it did before.
    """
    base = _responder(**over)

    def responder(request: httpx.Request) -> httpx.Response:
        if gate["reject"] and request.method == "POST" and request.url.path == "/v2/orders":
            return httpx.Response(
                400, json={"success": False, "error": {"code": "insufficient_margin"}}
            )
        return base(request)

    return responder


async def test_i01_a_protection_failure_still_fails_closed_then_retries():
    """PROTECTION_FAILED keeps no bracket ids, so the guard cannot fire on it.
    The next fill notification must still try again and still succeed."""
    store = _store()
    gate = {"reject": True}
    client, rec = _client(_post_gated_responder(gate, entry=_entry_json(bracket=False)))
    manager = _manager(client, store)
    record = _registered(manager)

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.state is TradeLifecycleState.PROTECTION_FAILED
    assert record.sl_order_id is None and record.tp_order_id is None
    assert record.protected_quantity == Decimal("0")
    assert "PROTECTION_PLACEMENT_FAILED" in _actions(store)

    gate["reject"] = False
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert record.protected_quantity == SIZE
    # Three POSTs: the refused SL attempt, then the SL and TP of the retry. The
    # TP is never attempted while the SL is refused, so the position is never
    # left holding take-profit-only "protection".
    assert len(rec.posts()) == 3
    assert [p["order_type"] for p in rec.posts()] == [
        "market_order", "market_order", "limit_order",
    ]
    await client.close()


async def test_i02_a_half_protected_record_is_never_refused():
    """Only a record whose protection covers its whole filled quantity is a
    duplicate. Protection that covers part of it must still be completed."""
    store = _store()
    client, _rec = _adopting_client()
    manager = _manager(client, store)
    record = _registered(
        manager,
        filled_quantity=SIZE,
        protected_quantity=Decimal("0"),
        sl_order_id=str(EXCHANGE_SL_LEG_ID),
        tp_order_id=None,
    )

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.protected_quantity == SIZE
    await client.close()


# ══ H. `close_position` still cancels the exchange's own legs ═════════════════


async def test_h01_close_position_cancels_the_adopted_ids_after_a_duplicate():
    """If the duplicate had cleared the adopted ids, the exchange's pair would
    outlive the position: `close_position` cancels only the ids it holds."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    closed = await manager.close_position(
        setup_id=SETUP,
        reason=CloseReason.TAKE_PROFIT,
        realized_pnl=Decimal("0.20"),
        trading_fees=Decimal("0.01"),
    )

    assert {b.get("id") for b in rec.deletes()} == {EXCHANGE_SL_LEG_ID, EXCHANGE_TP_LEG_ID}
    assert closed.state is TradeLifecycleState.POSITION_CLOSED
    assert SETUP not in manager._active_trades
    assert manager.single_trade_lock.is_locked(USER, ACCOUNT)[0] is False
    assert record.close_reason is CloseReason.TAKE_PROFIT
    await client.close()


async def test_h02_a_fill_notification_after_closure_cannot_resurrect_the_trade():
    """A late duplicate arriving after the position is gone must not re-open a
    terminal record, and must not put a fresh bracket on a flat position."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)
    await manager.close_position(
        setup_id=SETUP, reason=CloseReason.TAKE_PROFIT,
        realized_pnl=Decimal("0.20"), trading_fees=Decimal("0.01"),
    )
    posts_before, edges_before = list(rec.posts()), list(_edges(record))

    manager._active_trades[SETUP] = record  # a stale reference still holding it
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert record.state is TradeLifecycleState.POSITION_CLOSED
    assert _edges(record) == edges_before
    assert rec.posts() == posts_before
    await client.close()


# ══ G. Reconciliation still recognises the adopted legs ═══════════════════════


async def test_g01_reconciliation_still_sees_live_protection_after_a_duplicate():
    """The membership test is `sl_order_id in get_open_orders`. Because the
    duplicate left both adopted ids in place, reconciliation converges with no
    rebuild, no alert and no mutating request."""
    store = _store()
    client, rec = _client(
        _responder(resting=[_sl_leg(), _tp_leg()], positions=[_position_json(str(SIZE))])
    )
    manager = _manager(client, store)
    record = await _protected(manager)
    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["checked"] == 1
    assert summary["unresolved"] == []
    assert summary["protection_restored"] == []
    assert summary["orphan_positions"] == []
    assert "RECONCILIATION_PROTECTION_MISSING" not in _actions(store)
    assert rec.posts() == []
    assert manager.reconciliation_alerts == []
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    await client.close()


# ══ J. The synchronous path and the polling path together ═════════════════════


async def test_j01_a_sync_fill_then_a_rest_poll_does_not_double_process():
    """`execute_trade_setup` calls the handler itself when the entry comes back
    filled; the poll that follows goes through `_apply_entry_order_state`. The
    same fill seen twice by two different routes stays one fill."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = await _protected(manager)          # the synchronous branch
    before = list(_edges(record))

    status = await manager.refresh_entry_from_exchange(SETUP)   # the polling branch

    assert status is OrderStatus.FILLED
    assert _edges(record) == before
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == SIZE
    assert record.protected_quantity == SIZE
    assert rec.posts() == []
    await client.close()


async def test_j02_a_rest_poll_then_a_direct_handler_call_does_not_double_process():
    """The §O14 order of events: the exchange snapshot protects the position and
    the operator's loop then reports the fill straight into the handler."""
    store = _store()
    client, rec = _adopting_client()
    manager = _manager(client, store)
    record = _registered(manager)

    status = await manager.refresh_entry_from_exchange(SETUP)
    assert status is OrderStatus.FILLED
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    before = list(_edges(record))

    await manager.on_entry_fill(SETUP, SIZE, FILL_PRICE)

    assert _edges(record) == before
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert rec.posts() == []
    await client.close()


# ══ K. A stale SMALLER fill cannot shrink protection ══════════════════════════

BIG = Decimal("5")


def _big_client(**over):
    """The same adoption shape, for a 5-contract position."""
    return _client(
        _responder(
            resting=[
                _sl_leg(size=str(BIG), unfilled_size=str(BIG)),
                _tp_leg(size=str(BIG), unfilled_size=str(BIG)),
            ],
            **over,
        )
    )


async def test_k01_a_stale_smaller_fill_cannot_resize_protection_downwards():
    """The consequence that made this a safety fix rather than a cosmetic one.

    Before the guard, a replayed notification carrying a SMALLER size overwrote
    `filled_quantity` outright, so a 5-contract position started claiming 2 and
    `_ensure_bracket_protection` entered its resize path (`protected_quantity !=
    target`) for a position whose protection was already correct. Where the
    stale pair cancels cleanly that path replaces the live full-size bracket
    with a smaller one; where it does not, the fail-closed branch drives the
    record to PROTECTION_FAILED and raises PROTECTION_RESIZE_CANCEL_FAILED
    against a position that is in fact fully protected. Both outcomes are
    produced by nothing but a duplicate callback, and both are prevented here by
    refusing the notification, so the resize path is never entered at all.
    """
    store = _store()
    client, rec = _big_client()
    manager = _manager(client, store)
    record = _registered(manager, requested_quantity=BIG)
    await manager.on_entry_fill(SETUP, BIG, FILL_PRICE)
    assert record.protected_quantity == BIG
    before = list(_edges(record))

    await manager.on_entry_fill(SETUP, Decimal("2"), FILL_PRICE)

    assert record.filled_quantity == BIG
    assert record.protected_quantity == BIG
    assert _edges(record) == before
    assert rec.deletes() == [], "the live protective pair was cancelled"
    assert rec.posts() == []
    assert record.sl_order_id == str(EXCHANGE_SL_LEG_ID)
    await client.close()


async def test_k02_an_unprotected_record_still_protects_the_larger_known_size():
    """The clamp is not the guard. An unprotected record is never refused, and a
    stale smaller number must not become the size that gets protected."""
    store = _store()
    client, rec = _big_client()
    manager = _manager(client, store)
    record = _registered(manager, requested_quantity=BIG, filled_quantity=BIG)

    await manager.on_entry_partial_fill(SETUP, Decimal("2"), FILL_PRICE)

    assert record.filled_quantity == BIG
    assert record.protected_quantity == BIG
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert rec.posts() == []
    await client.close()


# ══ L. A legitimate DOWNWARD resize of an adopted bracket ═════════════════════

SMALLER = Decimal("2")


async def test_l01_a_clean_cancel_resizes_an_adopted_bracket_downwards():
    """K refuses a FALSE smaller target. This is the TRUE one, end to end.

    Reconciliation legitimately passes a smaller target when the exchange
    reports a position smaller than the record's protected quantity (partial
    manual close, partial TP fill). That is the one composite the existing
    suites do not cover together: §O13 e01 proves cancel-then-replace while
    growing 1 -> 2, and task_m f07 proves a smaller replacement pair where the
    legs are already gone so nothing is cancelled. Here both halves happen at
    once, from an ADOPTED bracket, in the shrinking direction -- the two exchange
    leg ids are cancelled by id, one replacement pair is placed at the smaller
    size, and `stale_bracket_cancelled` suppresses the duplicate-protection
    alert that the post-cancel re-adoption attempt would otherwise raise when it
    finds no legs.

    `filled_quantity` deliberately stays at 5: `_ensure_bracket_protection`
    protects the target it is given and does not reinterpret the fill. That is
    precisely why the §O14 guard belongs on the handlers -- the size a caller
    passes here is taken at face value.
    """
    store = _store()
    cancelled: List[Any] = []
    base = _responder(
        resting=lambda: (
            []
            if cancelled
            else [
                _sl_leg(size=str(BIG), unfilled_size=str(BIG)),
                _tp_leg(size=str(BIG), unfilled_size=str(BIG)),
            ]
        ),
        placed=(ENGINE_SL_ID, ENGINE_TP_ID),
    )

    def responder(request: httpx.Request) -> httpx.Response:
        """Delta stops listing a leg once it is cancelled, so the re-adoption
        attempt after the cancel correctly finds nothing to adopt."""
        if request.method == "DELETE" and request.url.path == "/v2/orders":
            cancelled.append(request.url.path)
        return base(request)

    client, rec = _client(responder)
    manager = _manager(client, store)
    record = _registered(manager, requested_quantity=BIG)

    await manager.on_entry_fill(SETUP, BIG, FILL_PRICE)
    assert record.protected_quantity == BIG
    assert (record.sl_order_id, record.tp_order_id) == (
        str(EXCHANGE_SL_LEG_ID),
        str(EXCHANGE_TP_LEG_ID),
    )
    assert rec.posts() == [], "the exchange's own bracket was adopted, not replaced"

    await manager._ensure_bracket_protection(record, SMALLER)

    # The exchange's own leg ids, cancelled by id, SL then TP, and nothing else.
    assert [b.get("id") for b in rec.deletes()] == [EXCHANGE_SL_LEG_ID, EXCHANGE_TP_LEG_ID]
    # Exactly one replacement pair, both legs at the smaller size.
    posts = rec.posts()
    assert [Decimal(str(p["size"])) for p in posts] == [SMALLER, SMALLER]
    assert [p["order_type"] for p in posts] == ["market_order", "limit_order"]
    assert all(p["reduce_only"] for p in posts)
    # §O13/§8 semantics survive the downsize: the SL is still a triggered stop on
    # last traded price, the TP still a resting reduce-only limit.
    assert posts[0]["stop_order_type"] == "stop_loss_order"
    assert posts[0]["stop_trigger_method"] == "last_traded_price"
    assert Decimal(str(posts[0]["stop_price"])) == SL_PRICE
    assert Decimal(str(posts[1]["limit_price"])) == TP_PRICE
    # The record tracks the replacements, not the cancelled legs.
    assert record.protected_quantity == SMALLER
    assert record.sl_order_id == str(ENGINE_SL_ID)
    assert record.tp_order_id == str(ENGINE_TP_ID)
    assert record.sl_client_order_id == posts[0]["client_order_id"]
    assert record.tp_client_order_id == posts[1]["client_order_id"]
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == BIG
    # A resize is not a duplicate.
    assert "PROTECTION_DUPLICATED_ON_EXCHANGE" not in _actions(store)
    assert manager.reconciliation_alerts == []
    await client.close()

