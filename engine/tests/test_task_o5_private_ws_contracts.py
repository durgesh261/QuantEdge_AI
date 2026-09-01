"""
Task O §O5 — Private WebSocket contract regression suite.

Pins the corrected private-stream contract:

A. `key-auth` timestamp is NUMERIC seconds, strictly validated, and signs
   `GET` + str(timestamp) + `/live`.
B. Symbol-scoped private channels name their instruments EXPLICITLY from the
   provenance-backed registry; `margins` is account-scoped and carries no
   `symbols`; an unknown channel or an empty symbol set fails closed.
C. The documented `action` field is consumed, and `delete` is folded into the
   SINGLE closure definition (`DeltaPositionEvent.is_closure`) while the
   exchange-reported size is preserved exactly as reported.
D. An unknown or absent order state fails closed instead of being adopted as
   `PENDING` / `OPEN`.
E. `seq_no` is consumed AND COMPARED per channel: an in-order frame advances, a
   replay is a replay, an absent sequence makes no claim, and a proven gap is
   audited and resynchronized -- with the fail-closed state RETAINED when the
   resync cannot re-establish trust.
F. §O1-§O4 contracts and governance state survive unchanged.
G. Static source invariants that keep the defects from reappearing.

SECURITY: synthetic fixture credentials only; no network, no orders, no
governance mutation.
"""

import ast
import hashlib
import hmac
import inspect
import json
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

import pytest

from quantedge.instruments import delta_india_registry, UnknownInstrumentError
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    UnknownOrderStateError,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    OrderRecord,
    PositionRecord,
    PositionStatus,
    LocalStateStore,
    LiveAccountSyncService,
    SyncResult,
)
from quantedge.execution import private_websocket as pws
from quantedge.execution.private_websocket import (
    AUDIT_WS_ORDER_DELETE_STATE_CONFLICT,
    AUDIT_WS_POSITION_DELETE_UNTRACKED,
    AUDIT_WS_SEQUENCE_GAP,
    AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED,
    AUDIT_WS_UNKNOWN_ACTION,
    AUDIT_WS_UNKNOWN_ORDER_STATE,
    DOCUMENTED_STREAM_ACTIONS,
    INTEGRITY_CODE_SEQUENCE_GAP,
    INTEGRITY_CODE_UNKNOWN_ACTION,
    INTEGRITY_CODE_UNKNOWN_ORDER_STATE,
    STREAM_ACTION_DELETE,
    SYMBOL_SCOPED_PRIVATE_CHANNELS,
    UNSCOPED_PRIVATE_CHANNELS,
    DeltaOrderEvent,
    DeltaPositionEvent,
    DeltaPrivateWebSocketClient,
    DeltaStreamIntegrityEvent,
    EventValidator,
    StreamHealth,
    UnknownStreamActionError,
    coerce_auth_timestamp,
    generate_ws_auth_signature,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.single_trade_lock import SingleTradeLockManager

FIXTURE_KEY = "TEST_O5_KEY_FIXTURE_0000000001"
FIXTURE_SECRET = "TEST_O5_SECRET_FIXTURE_00000000000000000000001"
ACCOUNT = "acc_o5_test"
USER = "user_o5_test"


# ── Fixtures & helpers ────────────────────────────────────────────────────────


@pytest.fixture
def store():
    s = LocalStateStore(account_id=ACCOUNT)
    s.account.user_id = USER
    s.account.total_equity = Decimal("10000.00")
    s.account.available_balance = Decimal("10000.00")
    return s


@pytest.fixture
def sync_service():
    svc = MagicMock(spec=LiveAccountSyncService)
    svc.sync = AsyncMock(return_value=SyncResult(
        success=True,
        synced_at=datetime.now(timezone.utc),
        account_id=ACCOUNT,
        equity=Decimal("10000.00"),
        available_balance=Decimal("10000.00"),
        margin_used=Decimal("0.00"),
        positions_synced=0,
        orders_synced=0,
    ))
    return svc


@pytest.fixture
def client(store, sync_service):
    ws = DeltaPrivateWebSocketClient(
        api_key=FIXTURE_KEY,
        api_secret=FIXTURE_SECRET,
        state_store=store,
        sync_service=sync_service,
    )
    ws.observed = []
    ws.register_event_observer(ws.observed.append)
    return ws


def _audits(store, action):
    return [e for e in store.audit_events if e["action"] == action]


def _order_frame(**over):
    payload = {
        "id": 500001,
        "client_order_id": "QE-O5-1",
        "product_symbol": "BTCUSD",
        "side": "buy",
        "order_type": "limit_order",
        "size": "1",
        "unfilled_size": "1",
        "limit_price": "95000",
        "state": "open",
    }
    payload.update(over.pop("payload", {}))
    frame = {"channel": "orders", "payload": payload}
    frame.update(over)
    return json.dumps(frame)


def _position_frame(**over):
    payload = {
        "product_symbol": "BTCUSD",
        "size": "1.5",
        "entry_price": "94000",
        "mark_price": "95000",
        "liquidation_price": "90000",
        "unrealized_pnl": "1500",
        "margin": "1400",
        "leverage": "50",
    }
    payload.update(over.pop("payload", {}))
    frame = {"channel": "positions", "payload": payload}
    frame.update(over)
    return json.dumps(frame)


def _margin_frame(*, seq_no=None, balance="15000"):
    payload = {
        "asset_symbol": "USDT",
        "balance": balance,
        "available_balance": "12000",
        "position_margin": "2000",
        "order_margin": "1000",
    }
    if seq_no is not None:
        payload["seq_no"] = seq_no
    return json.dumps({"channel": "margins", "payload": payload})


def _position_event(**over):
    kwargs = dict(
        symbol="BTCUSD",
        side=PositionSide.LONG,
        size=Decimal("1.5"),
        entry_price=Decimal("94000"),
        mark_price=Decimal("95000"),
        liquidation_price=Decimal("90000"),
        unrealized_pnl=Decimal("1500"),
        realized_pnl=None,
        margin=Decimal("1400"),
        leverage=Decimal("50"),
    )
    kwargs.update(over)
    return DeltaPositionEvent(**kwargs)


def _order_event(**over):
    kwargs = dict(
        order_id="500001",
        client_order_id="QE-O5-1",
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        unfilled_quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        status=OrderStatus.OPEN,
        price=Decimal("95000"),
    )
    kwargs.update(over)
    return DeltaOrderEvent(**kwargs)


# ══ A. `key-auth` timestamp & signature ═══════════════════════════════════════


def test_a01_auth_timestamp_is_coerced_to_numeric_seconds():
    """1. An int stays an int; a digit string becomes the same int."""
    assert coerce_auth_timestamp(1724318400) == 1724318400
    assert coerce_auth_timestamp("1724318400") == 1724318400
    assert coerce_auth_timestamp("  1724318400  ") == 1724318400
    assert isinstance(coerce_auth_timestamp("1724318400"), int)


def test_a02_auth_timestamp_refuses_anything_the_exchange_would_sign_differently():
    """2. §O5: a mis-signed auth frame yields a socket that delivers nothing.

    So a value whose numeric-seconds meaning is not provable is REFUSED rather
    than truncated, rounded or reshaped. `bool` is refused explicitly because
    `True` is an `int` in Python and would sign as the timestamp `1`.
    """
    for bad in (1724318400.7, 1724318400.0, "1724318400.5", "", "   ",
                "not-a-timestamp", "-1", "0x10", True, False, 0, -1,
                None, [], {}, Decimal("1724318400")):
        with pytest.raises(ValueError):
            coerce_auth_timestamp(bad)


def test_a03_signature_is_hmac_over_get_timestamp_live():
    """3. The signing message is 'GET' + str(timestamp) + '/live', verbatim."""
    expected = hmac.new(
        FIXTURE_SECRET.encode("utf-8"),
        b"GET1724318400/live",
        hashlib.sha256,
    ).hexdigest()

    assert generate_ws_auth_signature(FIXTURE_SECRET, 1724318400) == expected
    # The numeric and digit-string forms are the SAME signature: §O5 changed the
    # wire representation only, never the signature vector.
    assert generate_ws_auth_signature(FIXTURE_SECRET, "1724318400") == expected


def test_a04_auth_payload_serializes_timestamp_as_a_json_number(client):
    """4. Round-trip through JSON: a number, never a quoted string."""
    payload = client.build_auth_payload(timestamp=1724318400)
    assert payload["payload"]["timestamp"] == 1724318400
    assert isinstance(payload["payload"]["timestamp"], int)

    wire = json.dumps(payload)
    assert '"timestamp": 1724318400' in wire
    assert '"1724318400"' not in wire
    assert json.loads(wire)["payload"]["timestamp"] == 1724318400
    # The credential never appears; only the key does, and the secret never.
    assert FIXTURE_SECRET not in wire


# ══ B. Private-channel subscription scoping ═══════════════════════════════════


def test_b05_scoped_channels_name_registry_symbols_explicitly(client):
    """5. Delta delivers nothing for a symbol a scoped channel never named."""
    payload = client.build_subscribe_payload()
    by_name = {c["name"]: c for c in payload["payload"]["channels"]}
    registry_symbols = set(delta_india_registry().symbols)

    for name in sorted(SYMBOL_SCOPED_PRIVATE_CHANNELS):
        assert set(by_name[name]["symbols"]) == registry_symbols
        assert "all" not in by_name[name]["symbols"]


def test_b06_unscoped_margins_channel_carries_no_symbols(client):
    """6. `margins` is account-scoped: inventing a product scoping is a guess."""
    payload = client.build_subscribe_payload(["margins"])
    channel = payload["payload"]["channels"][0]
    assert channel == {"name": "margins"}
    assert "symbols" not in channel
    assert "margins" in UNSCOPED_PRIVATE_CHANNELS


def test_b07_explicit_symbols_are_registry_resolved_and_fail_closed(client):
    """7. An unregistered symbol is refused, never substituted (safety rule #15)."""
    payload = client.build_subscribe_payload(["orders"], symbols=["ETHUSD"])
    assert payload["payload"]["channels"][0]["symbols"] == ["ETHUSD"]

    with pytest.raises(UnknownInstrumentError):
        client.build_subscribe_payload(["orders"], symbols=["NOT_A_PRODUCT"])

    # An empty explicit set must not silently become the whole registry or `all`.
    with pytest.raises(ValueError):
        client.build_subscribe_payload(["orders"], symbols=[])


def test_b08_unknown_channel_scoping_is_refused(client):
    """8. §O5: an unknown private channel's scoping is unknown, so refuse it."""
    with pytest.raises(ValueError):
        client.build_subscribe_payload(["orders", "some_future_private_channel"])

    # A refused subscription must not have been recorded as subscribed.
    assert "some_future_private_channel" not in client._subscribed_channels
    # Restoration state is still tracked for the channels that ARE valid.
    client.build_subscribe_payload(["orders", "margins"])
    assert client._subscribed_channels == ["orders", "margins"]


# ══ C. `action` consumption & the single closure definition ════════════════════


def test_c09_action_is_read_from_the_frame_and_normalized(client):
    """9. The documented `action` is consumed; absence stays absence."""
    assert EventValidator.extract_action({}, {}) is None
    assert EventValidator.extract_action({"action": "DELETE"}) == "delete"
    assert EventValidator.extract_action({"action": " update "}) == "update"
    assert DOCUMENTED_STREAM_ACTIONS == {"create", "update", "delete"}

    event = client.validator.parse_and_validate(
        _position_frame(payload={"action": "delete"}))
    assert isinstance(event, DeltaPositionEvent)
    assert event.action == STREAM_ACTION_DELETE


def test_c10_delete_is_a_closure_even_when_the_reported_size_is_non_zero(client):
    """10. §O5 D4: Delta sends the LAST KNOWN size on `action: "delete"`.

    Closure is answered in exactly ONE place, and the reported size is preserved
    exactly as reported -- rewriting it to zero would discard the last observed
    exposure that the audit trail needs.
    """
    event = client.validator.parse_and_validate(
        _position_frame(payload={"action": "delete", "size": "1.5"}))

    assert event.is_closure is True
    assert event.size == Decimal("1.5")          # preserved, never fabricated to 0
    assert _position_event(size=Decimal("0")).is_closure is True
    assert _position_event(size=Decimal("1.5")).is_closure is False
    assert _position_event(size=Decimal("1.5"), action="update").is_closure is False


def test_c11_delete_closes_the_tracked_position(client, store):
    """11. The pre-§O5 defect rewrote a deleted position as OPEN. It must close."""
    assert client.apply_event(_position_event(action="update")) is True
    assert store.positions["BTCUSD"].status == PositionStatus.OPEN

    assert client.apply_event(
        _position_event(size=Decimal("1.5"), action="delete")) is True
    assert "BTCUSD" not in store.positions
    assert store.position_history[-1].status == PositionStatus.CLOSED


def test_c12_delete_of_an_untracked_symbol_is_audited_not_silently_dropped(client, store):
    """12. Still a statement about exchange state, so it must not vanish."""
    applied = client.apply_event(
        _position_event(size=Decimal("2.0"), action="delete"))

    assert applied is False
    assert "BTCUSD" not in store.positions      # nothing fabricated
    audits = _audits(store, AUDIT_WS_POSITION_DELETE_UNTRACKED)
    assert len(audits) == 1
    assert audits[0]["details"]["reported_size"] == "2.0"
    assert audits[0]["details"]["stream_action"] == "delete"


def test_c13_order_delete_never_fabricates_a_terminal_status(client, store):
    """13. §O5: a vanished order may have FILLED or been CANCELLED.

    Choosing either manufactures an exchange fact -- writing `CANCELLED` over an
    order that actually filled hides a live position. So the local record is
    retained verbatim, the contradiction is audited, and authoritative REST
    reconciliation decides.
    """
    assert client.apply_event(_order_event()) is True
    assert store.orders["500001"].status == OrderStatus.OPEN

    applied = client.apply_event(
        _order_event(status=OrderStatus.OPEN, action="delete"))

    assert applied is False
    assert "500001" in store.orders                              # never removed
    assert store.orders["500001"].status == OrderStatus.OPEN     # never rewritten
    assert store.orders["500001"].status != OrderStatus.CANCELLED
    audits = _audits(store, AUDIT_WS_ORDER_DELETE_STATE_CONFLICT)
    assert len(audits) == 1
    assert audits[0]["details"]["resolution"] == "RETAINED_PENDING_REST_RECONCILIATION"
    assert audits[0]["details"]["local_status"] == OrderStatus.OPEN.value


def test_c13b_order_delete_with_a_terminal_status_applies_normally(client, store):
    """13. A delete the exchange itself resolved (`closed`/`cancelled`) applies."""
    assert client.apply_event(_order_event()) is True
    applied = client.apply_event(_order_event(
        status=OrderStatus.CANCELLED,
        unfilled_quantity=Decimal("0"),
        action="delete",
    ))
    assert applied is True
    assert store.orders["500001"].status == OrderStatus.CANCELLED
    assert _audits(store, AUDIT_WS_ORDER_DELETE_STATE_CONFLICT) == []


# ══ D. Unknown / absent order state fails closed ══════════════════════════════


def test_d14_from_exchange_refuses_an_uninterpretable_state():
    """14. §O5 D6: the old `PENDING` default answered a safety question.

    A filled, rejected, liquidated or expired order arriving under a name this
    engine does not know would have been adopted as STILL RESTING.
    """
    # The documented states, and the aliases already relied on, still map.
    assert OrderStatus.from_exchange("open") == OrderStatus.OPEN
    assert OrderStatus.from_exchange("pending") == OrderStatus.PENDING
    assert OrderStatus.from_exchange("closed") == OrderStatus.FILLED
    assert OrderStatus.from_exchange("cancelled") == OrderStatus.CANCELLED

    for unknown in ("liquidated", "settled", "unknown_future_state", "OPENISH"):
        with pytest.raises(UnknownOrderStateError):
            OrderStatus.from_exchange(unknown)
        # The refusal is a ValueError subclass, so existing quarantine guards
        # that already catch ValueError keep working unchanged.
        with pytest.raises(ValueError):
            OrderStatus.from_exchange(unknown)


def test_d15_an_order_frame_with_no_state_and_no_status_fails_closed(client):
    """15. Absence is a different fact from unrecognized, and it is not `OPEN`."""
    with pytest.raises(UnknownOrderStateError):
        client.validator.parse_and_validate(
            _order_frame(payload={"state": None, "status": None}))
    with pytest.raises(UnknownOrderStateError):
        client.validator.parse_and_validate(_order_frame(payload={"state": "   "}))


@pytest.mark.asyncio
async def test_d16_unknown_state_frame_becomes_a_blocking_integrity_failure(client, store):
    """16. Quarantining is fail-closed for the FRAME but silent for the TRADE."""
    applied = await client._handle_message(_order_frame(payload={"state": "liquidated"}))

    assert applied is False
    assert client.integrity_failure_count == 1
    assert client.stream_integrity_ok is False
    assert client.health == StreamHealth.DEGRADED
    audits = _audits(store, AUDIT_WS_UNKNOWN_ORDER_STATE)
    assert len(audits) == 1
    assert audits[0]["details"]["channel"] == "orders"

    integrity = [e for e in client.observed if isinstance(e, DeltaStreamIntegrityEvent)]
    assert len(integrity) == 1
    assert integrity[0].code == INTEGRITY_CODE_UNKNOWN_ORDER_STATE
    assert integrity[0].resynchronized is False


@pytest.mark.asyncio
async def test_d16b_unknown_action_frame_becomes_a_blocking_integrity_failure(client, store):
    """16. An unrecognized `action` MAY be a deletion; reading it as an ordinary
    update is exactly the §O5 D4 defect, so it is escalated, not quarantined."""
    with pytest.raises(UnknownStreamActionError):
        client.validator.extract_action({"action": "purge"})

    applied = await client._handle_message(
        _position_frame(payload={"action": "purge"}))

    assert applied is False
    assert client.integrity_failure_count == 1
    assert client.stream_integrity_ok is False
    audits = _audits(store, AUDIT_WS_UNKNOWN_ACTION)
    assert len(audits) == 1
    assert audits[0]["details"]["channel"] == "positions"

    integrity = [e for e in client.observed if isinstance(e, DeltaStreamIntegrityEvent)]
    assert [e.code for e in integrity] == [INTEGRITY_CODE_UNKNOWN_ACTION]
    # An uninterpretable frame is NOT silently counted as merely malformed.
    assert client.validator.quarantined_events == []


# ══ E. `seq_no` continuity, resynchronization & retained fail-closed state ═════


@pytest.mark.asyncio
async def test_e17_in_order_sequence_advances_without_alarm(client, store, sync_service):
    """17. Consecutive `seq_no` values are the normal case: no gap, no noise."""
    for n, size in ((10, "1.0"), (11, "1.1"), (12, "1.2")):
        await client._handle_message(_position_frame(
            payload={"action": "update", "size": size, "seq_no": n}))

    assert client.sequence_gap_count == 0
    assert client.sequence_replay_count == 0
    assert client.missing_sequence_events_count == 0
    assert client.stream_integrity_ok is True
    assert client._sequence_state == {"positions": 12}
    sync_service.sync.assert_not_called()
    assert _audits(store, AUDIT_WS_SEQUENCE_GAP) == []


@pytest.mark.asyncio
async def test_e18_a_replayed_sequence_is_a_replay_not_a_gap(client, store, sync_service):
    """18. The exchange already delivered that frame; the appliers own it."""
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.0", "seq_no": 10}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.1", "seq_no": 11}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.1", "seq_no": 10}))

    assert client.sequence_replay_count == 1
    assert client.sequence_gap_count == 0
    assert client.stream_integrity_ok is True
    assert client._sequence_state == {"positions": 11}   # never rewound
    sync_service.sync.assert_not_called()


@pytest.mark.asyncio
async def test_e19_a_missing_sequence_makes_no_claim_in_either_direction(client, sync_service):
    """19. Unobserved continuity is counted as a diagnostic only. Inventing
    continuity here would define away the very gap this exists to reveal."""
    for size in ("1.0", "1.1"):
        await client._handle_message(_position_frame(
            payload={"action": "update", "size": size}))

    assert client.missing_sequence_events_count == 2
    assert client.sequence_gap_count == 0
    assert client.sequence_replay_count == 0
    assert client._sequence_state == {}
    assert client.stream_integrity_ok is True
    sync_service.sync.assert_not_called()


@pytest.mark.asyncio
async def test_e20_a_gap_is_audited_and_resynchronized(client, store, sync_service):
    """20. Gap + SUCCESSFUL resync: a recorded diagnostic, not a standing block.

    The state the gap endangered has already been re-derived from the
    authoritative REST snapshot.
    """
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.0", "seq_no": 10}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.1", "seq_no": 14}))

    assert client.sequence_gap_count == 1
    sync_service.sync.assert_awaited_once()

    audits = _audits(store, AUDIT_WS_SEQUENCE_GAP)
    assert len(audits) == 1
    assert audits[0]["details"] == {
        "channel": "positions",
        "expected_seq_no": 11,
        "received_seq_no": 14,
        # Frames 11, 12 and 13 were lost; frame 14 itself arrived.
        "missing_frames": 3,
    }
    assert _audits(store, AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED) == []
    assert client.stream_integrity_ok is True

    integrity = [e for e in client.observed if isinstance(e, DeltaStreamIntegrityEvent)]
    assert len(integrity) == 1
    assert integrity[0].code == INTEGRITY_CODE_SEQUENCE_GAP
    assert integrity[0].channel == "positions"
    assert integrity[0].expected_seq_no == 11
    assert integrity[0].received_seq_no == 14
    assert integrity[0].resynchronized is True


@pytest.mark.asyncio
async def test_e21_a_gap_whose_resync_fails_retains_the_fail_closed_state(client, store, sync_service):
    """21. Gap + FAILED resync: trust was not re-established, so it stays closed."""
    sync_service.sync = AsyncMock(side_effect=RuntimeError("REST unavailable"))

    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.0", "seq_no": 10}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.1", "seq_no": 12}))

    assert client.sequence_gap_count == 1
    assert client.stream_integrity_ok is False
    assert client.health == StreamHealth.DEGRADED
    assert len(_audits(store, AUDIT_WS_SEQUENCE_GAP)) == 1
    failed = _audits(store, AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED)
    assert len(failed) == 1
    assert failed[0]["details"]["missing_frames"] == 1

    integrity = [e for e in client.observed if isinstance(e, DeltaStreamIntegrityEvent)]
    assert [e.resynchronized for e in integrity] == [False]


@pytest.mark.asyncio
async def test_e22_a_reconciliation_hook_failure_also_retains_fail_closed_state(client):
    """21. `trigger_reconciliation` returns trustworthiness ADDITIVELY: a hook
    that failed means the resync did not finish, so trust is not re-established.
    """
    def _broken_hook():
        raise RuntimeError("account reconciliation failed")

    client.register_reconciliation_hook(_broken_hook)
    assert await client.trigger_reconciliation() is False

    # Existing callers may ignore the return value; nothing was raised.
    assert client.health == StreamHealth.DEGRADED


@pytest.mark.asyncio
async def test_e23_continuity_is_tracked_per_channel(client, sync_service):
    """22. Independent numbering per channel: `orders` seq 5 is not an
    `positions` gap, and the granularity decision lives in ONE helper."""
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.0", "seq_no": 100}))
    await client._handle_message(_order_frame(payload={"seq_no": 5}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.1", "seq_no": 101}))

    assert client.sequence_gap_count == 0
    assert client._sequence_state == {"positions": 101, "orders": 5}
    assert client._sequence_key("orders", {"product_symbol": "BTCUSD"}) == "orders"
    sync_service.sync.assert_not_called()


@pytest.mark.asyncio
async def test_e24_a_gap_is_detected_even_when_the_frame_itself_is_dropped(client, store):
    """22. A duplicate or unusable frame still carries the sequence information
    that proves whether anything was lost, so continuity is consumed for every
    data frame -- before application, and independently of it."""
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.0", "seq_no": 10}))
    # An unknown channel: nothing to apply, but the sequence still counts.
    await client._handle_message(json.dumps(
        {"channel": "unknown_future_stream", "payload": {"seq_no": 11}}))
    await client._handle_message(_position_frame(
        payload={"action": "update", "size": "1.2", "seq_no": 20}))

    assert client.validator.unknown_events_count == 1
    assert client.sequence_gap_count == 1
    assert len(_audits(store, AUDIT_WS_SEQUENCE_GAP)) == 1
    assert client._sequence_state["unknown_future_stream"] == 11


def test_e25_sequence_state_resets_on_a_new_session(client):
    """22. A new session restarts the exchange's own numbering, so carrying the
    previous session's last `seq_no` forward would make every reconnect a gap."""
    client._sequence_state = {"positions": 500}
    client._reset_sequence_state(reason="test_reconnect")
    assert client._sequence_state == {}
    assert "self._reset_sequence_state(reason=\"connect\")" in inspect.getsource(
        DeltaPrivateWebSocketClient.connect)


def test_e26_continuity_diagnostics_are_surfaced_in_the_status_summary(client):
    """22. The operator-visible surface reports continuity, not just health."""
    summary = client.get_status_summary()
    for key in ("sequence_gap_count", "sequence_replay_count",
                "missing_sequence_events_count", "integrity_failure_count",
                "stream_integrity_ok", "tracked_sequence_channels"):
        assert key in summary
    assert summary["stream_integrity_ok"] is True
    assert summary["tracked_sequence_channels"] == []
    # Diagnostics never carry credentials.
    assert FIXTURE_SECRET not in json.dumps(summary, default=str)


# ══ F. Lifecycle terminus, §O1-§O4 compatibility & governance ═════════════════


@pytest.fixture
def manager(store):
    exchange = MagicMock(spec=DeltaIndiaClient)
    exchange._api_key = FIXTURE_KEY
    exchange._api_secret = FIXTURE_SECRET
    return TradeLifecycleManager(
        client=exchange,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=SingleTradeLockManager(),
    )


def _decision():
    from quantedge.strategy.models import SetupState, StrategyDecision, StrategyDirection

    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="BTCUSD_1h_MANUAL_SMC_OB-O5_LONG",
        entry=Decimal("95000"),
        stop_loss=Decimal("94000"),
        take_profit=Decimal("98000"),
        risk_distance=Decimal("1000"),
        reward_distance=Decimal("3000"),
        risk_reward=Decimal("3.0"),
        confidence=90.0,
    )


@pytest.mark.asyncio
async def test_f27_a_recovered_gap_does_not_leave_a_standing_block(manager, store):
    """23. Gap + successful resync is audited but must NOT block new entries."""
    raised = manager.handle_stream_integrity_event(DeltaStreamIntegrityEvent(
        code=INTEGRITY_CODE_SEQUENCE_GAP,
        channel="positions",
        symbol="STREAM",
        reason="1 private frame(s) were lost",
        expected_seq_no=11,
        received_seq_no=12,
        resynchronized=True,
    ))

    assert raised is False
    assert manager.reconciliation_alerts == []
    assert len(_audits(store, "PRIVATE_STREAM_INTEGRITY_RECOVERED")) == 1


@pytest.mark.asyncio
async def test_f28_an_unresolved_gap_blocks_new_entries_at_the_existing_terminus(manager, store):
    """23. §O5 D10: the signal terminates in the EXISTING reconciliation-alert
    list, whose presence already blocks `execute_trade_setup`. Degrading
    `StreamHealth` alone blocks nothing, which is why this routing exists."""
    raised = manager.handle_stream_integrity_event(DeltaStreamIntegrityEvent(
        code=INTEGRITY_CODE_SEQUENCE_GAP,
        channel="positions",
        symbol="STREAM",
        reason="4 private frame(s) were lost",
        expected_seq_no=11,
        received_seq_no=14,
        resynchronized=False,
    ))

    assert raised is True
    alerts = manager.reconciliation_alerts
    assert [a["code"] for a in alerts] == [INTEGRITY_CODE_SEQUENCE_GAP]
    assert len(_audits(store, f"RECONCILIATION_ALERT_{INTEGRITY_CODE_SEQUENCE_GAP}")) == 1

    record = await manager.execute_trade_setup(
        decision=_decision(), account_id=ACCOUNT, user_id=USER)
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "RECONCILIATION_REQUIRED"

    # Only an explicit operator action clears it (§M11/§M15 unchanged).
    assert manager.clear_reconciliation_alerts("operator_o5_test") == 1
    assert manager.reconciliation_alerts == []


@pytest.mark.asyncio
async def test_f29_integrity_events_travel_the_existing_observer_channel(manager, client, store, sync_service):
    """23. No parallel lifecycle path: the same `bind_private_stream` wiring the
    typed events already use carries the integrity event to the terminus."""
    sync_service.sync = AsyncMock(side_effect=RuntimeError("REST unavailable"))
    manager.bind_private_stream(client)

    await client._handle_message(_margin_frame(seq_no=10, balance="15000"))
    await client._handle_message(_margin_frame(seq_no=13, balance="15001"))

    assert [a["code"] for a in manager.reconciliation_alerts] == [
        INTEGRITY_CODE_SEQUENCE_GAP]
    # The transport handed the observer no order-placement capability (§M1).
    assert not hasattr(client, "place_order")


def test_f30_a_resting_pending_stop_is_never_erased_by_the_stream(client, store):
    """24. §O1 x §O4: an untriggered stop rests in `pending`, which is why §O4
    widened `GET /v2/orders` to `states=open,pending`. No stream frame may delete
    an `OrderRecord` or invent a terminal state for one, or the SL would vanish
    locally and be re-placed as a duplicate."""
    resting = _order_event(
        order_id="SL-9001",
        order_type=OrderType.STOP_MARKET_ORDER,
        status=OrderStatus.PENDING,
        reduce_only=True,
    )
    assert client.apply_event(resting) is True
    assert store.orders["SL-9001"].status == OrderStatus.PENDING

    # A `delete` frame that still reports `pending` is a contradiction.
    assert client.apply_event(_order_event(
        order_id="SL-9001",
        order_type=OrderType.STOP_MARKET_ORDER,
        status=OrderStatus.PENDING,
        reduce_only=True,
        action="delete",
    )) is False

    assert "SL-9001" in store.orders
    assert store.orders["SL-9001"].status == OrderStatus.PENDING
    # No applier path removes an order record at all.
    assert "del self._state_store.orders[" not in inspect.getsource(
        DeltaPrivateWebSocketClient._apply_order_event)


def test_f31_governance_state_is_untouched():
    """25. §O5 changed no governance value."""
    from quantedge.strategy.manual_smc.backtest import LIVE_EXECUTION_AUTHORIZED
    from quantedge.ai.research.displacement_gated_retest_engine import AI_PROMOTION_STATUS

    assert LIVE_EXECUTION_AUTHORIZED is False
    assert AI_PROMOTION_STATUS == "REJECTED"

    fresh = AccountRecord(account_id=ACCOUNT)
    assert fresh.kill_switch_active is True
    assert fresh.algo_enabled is False


def test_f32_the_transport_still_has_no_order_placement_surface(client):
    """25. The private stream observes; it never acts."""
    for forbidden in ("place_order", "cancel_order", "modify_order",
                      "place_stop_order", "close_position"):
        assert not hasattr(client, forbidden)


# ══ G. Static source invariants (the defects cannot quietly return) ═══════════


def _code(func) -> str:
    """Return `func`'s body as CODE ONLY -- no comments, no docstring.

    §O5 explanatory comments quote the defects they removed (`event.size ==
    Decimal("0")`, the `"OPEN"` default), so a raw-source search would match the
    very comment that documents the fix. `ast.unparse` normalizes string
    literals to single quotes, which the assertions below account for.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    body = getattr(node, "body", [])
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        node.body = body[1:]
    return ast.unparse(tree)


def test_g33_from_exchange_has_no_status_fallback():
    """26. The `PENDING` default is gone and the refusal is explicit."""
    code = _code(OrderStatus.from_exchange)
    assert "raise UnknownOrderStateError" in code
    assert "return cls.PENDING" not in code
    assert "mapping.get(" not in code
    assert "if val_clean in mapping" in code


def test_g34_normalize_order_does_not_default_a_missing_state_to_open():
    """26. The old `data.get("state") or data.get("status", "OPEN")` is gone."""
    code = _code(EventValidator._normalize_order)
    assert "'OPEN'" not in code
    assert '"OPEN"' not in code
    assert "raise UnknownOrderStateError" in code
    assert "OrderStatus.from_exchange" in code


def test_g35_closure_is_decided_only_by_is_closure():
    """26. Both former size-to-zero comparisons are gone; ONE definition remains."""
    from quantedge.execution import trade_lifecycle as tl

    for func in (DeltaPrivateWebSocketClient._apply_position_event,
                 tl.TradeLifecycleManager.handle_position_event):
        code = _code(func)
        assert "event.is_closure" in code
        assert "event.size == Decimal('0')" not in code
        assert "event.size != Decimal('0')" not in code

    is_closure_code = _code(DeltaPositionEvent.is_closure.fget)
    assert "STREAM_ACTION_DELETE" in is_closure_code
    assert "Decimal('0')" in is_closure_code


def test_g36_delete_frames_can_never_be_silently_ignored():
    """26. Every delete outcome is either applied or audited."""
    pos_src = inspect.getsource(DeltaPrivateWebSocketClient._apply_position_event)
    assert "AUDIT_WS_POSITION_DELETE_UNTRACKED" in pos_src

    order_src = inspect.getsource(DeltaPrivateWebSocketClient._apply_order_event)
    assert "AUDIT_WS_ORDER_DELETE_STATE_CONFLICT" in order_src
    assert "event.is_deletion" in order_src
    # A vanished order is never given a status the exchange did not report.
    assert "existing.status = OrderStatus.CANCELLED" not in order_src


def test_g37_seq_no_is_consumed_and_compared_not_merely_parsed():
    """26. Parsing a sequence number and never comparing it would leave the gap
    invisible, which is the whole point of §O5 D9."""
    assert "seq_no" in inspect.getsource(EventValidator.extract_seq_no)
    assert "extract_seq_no" in inspect.getsource(EventValidator.parse_and_validate)

    track_src = inspect.getsource(DeltaPrivateWebSocketClient._track_sequence)
    assert "expected = last + 1" in track_src
    assert "received == expected" in track_src
    assert "received <= last" in track_src

    handle_src = inspect.getsource(DeltaPrivateWebSocketClient._handle_message)
    assert "_track_sequence" in handle_src
    assert "_handle_sequence_gap" in handle_src

    gap_src = inspect.getsource(DeltaPrivateWebSocketClient._handle_sequence_gap)
    # The EXISTING resync path and the EXISTING alert terminus, nothing parallel.
    assert "await self.trigger_reconciliation()" in gap_src
    assert "self._notify_observers(DeltaStreamIntegrityEvent(" in gap_src


def test_g38_the_subscribe_payload_never_falls_back_to_all():
    """26. `symbols: ["all"]` was the defect; the literal must not reappear."""
    code = _code(DeltaPrivateWebSocketClient.build_subscribe_payload)
    assert "'all'" not in code
    assert '"all"' not in code
    assert "delta_india_registry()" in code
    assert "SYMBOL_SCOPED_PRIVATE_CHANNELS" in code
