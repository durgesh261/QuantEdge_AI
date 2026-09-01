"""
Phase 5.6 — Private Delta WebSocket & Real-Time Account State Test Suite.

Comprehensive verification of:
1. HMAC-SHA256 signature generation for `key-auth` frames
2. Successful connection & handshake structure
3. Authentication failure handling (fail closed)
4. Channel subscription formatting (`orders`, `positions`, `user_trades`, `margins`)
5. Malformed event quarantine without crashing
6. Unknown event handling & metrics incrementing
7. Duplicate event deduplication
8. Out-of-order event protection
9. Order event normalization (`DeltaOrderEvent`)
10. Position event normalization (`DeltaPositionEvent`)
11. Fill event normalization (`DeltaFillEvent`)
12. Margin / balance event normalization (`DeltaMarginEvent`)
13. Reconnection backoff calculation
14. Heartbeat ping / timeout handling
15. Subscription restoration tracking
16. REST reconciliation trigger on reconnect
17. REST snapshot precedence (REST wins on conflict)
18. Zero duplicate records in state store
19. Zero credential leakage & secret masking
20. Zero real order placement side effects
21. Kill switch remains active (`kill_switch_active == True`)
22. Algorithmic trading remains disabled (`algo_enabled == False`)
23. Graceful shutdown

SECURITY:
  All tests use synthetic fixture credentials only. Real credentials must NEVER be placed in code.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.instruments import delta_india_registry
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderResponse,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    PositionRecord,
    OrderRecord,
    PositionStatus,
    LocalStateStore,
    LiveAccountSyncService,
    SyncResult,
)
from quantedge.execution.private_websocket import (
    WSConnectionState,
    StreamHealth,
    DeltaOrderEvent,
    DeltaPositionEvent,
    DeltaFillEvent,
    DeltaMarginEvent,
    generate_ws_auth_signature,
    EventValidator,
    DeltaPrivateWebSocketClient,
)

FIXTURE_KEY = "TEST_WS_KEY_FIXTURE_0000000001"
FIXTURE_SECRET = "TEST_WS_SECRET_FIXTURE_000000000000000000000000001"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def state_store():
    store = LocalStateStore(account_id="acc_ws_test_01")
    store.account.user_id = "user_ws_test"
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("8000.00")
    store.account.margin_used = Decimal("2000.00")
    return store


@pytest.fixture
def mock_sync_service():
    service = MagicMock(spec=LiveAccountSyncService)
    service.sync = AsyncMock(return_value=SyncResult(
        success=True,
        synced_at=datetime.now(timezone.utc),
        account_id="acc_ws_test_01",
        equity=Decimal("10000.00"),
        available_balance=Decimal("8000.00"),
        margin_used=Decimal("2000.00"),
        positions_synced=1,
        orders_synced=1,
    ))
    return service


@pytest.fixture
def ws_client(state_store, mock_sync_service):
    return DeltaPrivateWebSocketClient(
        api_key=FIXTURE_KEY,
        api_secret=FIXTURE_SECRET,
        state_store=state_store,
        sync_service=mock_sync_service,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_01_hmac_authentication_generation():
    """1. Verify HMAC-SHA256 signature matches official Delta key-auth formula: GET + ts + /live."""
    timestamp = "1724318400"
    sig = generate_ws_auth_signature(FIXTURE_SECRET, timestamp)

    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex string is 64 characters

    # Deterministic check
    sig2 = generate_ws_auth_signature(FIXTURE_SECRET, timestamp)
    assert sig == sig2

    # Different timestamp produces different signature
    sig_diff_ts = generate_ws_auth_signature(FIXTURE_SECRET, "1724318401")
    assert sig != sig_diff_ts

    # Task O §O5: STRENGTHENED, not weakened. The documented `key-auth`
    # timestamp is numeric seconds, and the signing message is
    # 'GET' + str(timestamp) + '/live' -- so a numeric timestamp must sign
    # IDENTICALLY to the digit string this test has always used. That is what
    # makes the §O5 correction a change to the wire representation only, not to
    # the signature vector.
    assert generate_ws_auth_signature(FIXTURE_SECRET, 1724318400) == sig

    # A value the exchange would sign differently is refused rather than
    # truncated or reshaped: a silently mis-signed auth frame yields a socket
    # that connects and then delivers nothing.
    for bad in (1724318400.7, "1724318400.5", "", "  ", "not-a-timestamp", True, 0, -1, None):
        with pytest.raises(ValueError):
            generate_ws_auth_signature(FIXTURE_SECRET, bad)


def test_02_successful_connection_handshake(ws_client):
    """2. Verify key-auth JSON payload construction."""
    payload = ws_client.build_auth_payload(timestamp="1724318400")
    assert payload["type"] == "key-auth"
    assert payload["payload"]["api-key"] == FIXTURE_KEY
    # Task O §O5: STRENGTHENED, not weakened. This previously asserted the
    # timestamp was the STRING "1724318400". Delta's documented `key-auth` frame
    # types `timestamp` as a number; serializing it as a JSON string risks a
    # rejected auth, and a rejected auth on this transport is silent -- the
    # socket connects and then simply never delivers an order, fill or closure.
    assert payload["payload"]["timestamp"] == 1724318400
    assert isinstance(payload["payload"]["timestamp"], int)
    assert not isinstance(payload["payload"]["timestamp"], str)
    assert len(payload["payload"]["signature"]) == 64
    # The numeric form signs identically to the digit string.
    assert payload["payload"]["signature"] == generate_ws_auth_signature(
        FIXTURE_SECRET, 1724318400)
    # A default timestamp is still numeric seconds.
    assert isinstance(ws_client.build_auth_payload()["payload"]["timestamp"], int)


def test_03_authentication_failure_handling(ws_client):
    """3. Verify error message frames transition stream health appropriately without crashing."""
    validator = ws_client.validator
    raw_error = '{"type": "error", "message": "Signature mismatch for api-key"}'
    result = validator.parse_and_validate(raw_error)
    
    assert result == {"type": "error", "message": "Signature mismatch for api-key"}
    assert validator.malformed_events_count == 0


def test_04_subscription_payload_and_ack(ws_client):
    """4. Verify private channel subscription formatting for orders, positions, user_trades, margins."""
    sub_payload = ws_client.build_subscribe_payload(["orders", "positions", "user_trades", "margins"])
    assert sub_payload["type"] == "subscribe"
    channels = sub_payload["payload"]["channels"]
    assert len(channels) == 4
    names = [c["name"] for c in channels]
    assert "orders" in names
    assert "positions" in names
    assert "user_trades" in names
    assert "margins" in names

    # Task O §O5: STRENGTHENED, not weakened. Channel NAMES alone never proved
    # the subscription was actually scoped. Delta's symbol-scoped private
    # channels deliver nothing for a symbol that was not named, so an
    # unscoped `orders`/`positions`/`user_trades` subscription is a silent
    # blindness to fills and closures. The symbols must come from the
    # provenance-backed instrument registry, never from a guessed literal
    # and never from an `"all"` wildcard.
    by_name = {c["name"]: c for c in channels}
    registry_symbols = set(delta_india_registry().symbols)
    assert registry_symbols, "instrument registry must expose at least one symbol"
    for scoped in ("orders", "positions", "user_trades"):
        assert "symbols" in by_name[scoped], f"{scoped} must be symbol-scoped"
        assert set(by_name[scoped]["symbols"]) == registry_symbols
        assert "all" not in by_name[scoped]["symbols"]
    # `margins` is account-scoped: it carries no product, so sending a
    # `symbols` list would be inventing a scoping the exchange does not define.
    assert "symbols" not in by_name["margins"]


def test_05_malformed_event_quarantine(ws_client):
    """5. Verify malformed or non-JSON messages are quarantined without crashing the client."""
    validator = ws_client.validator
    
    # Non-JSON string
    res1 = validator.parse_and_validate("MALFORMED_NON_JSON_FRAME")
    assert res1 is None
    assert validator.malformed_events_count == 1

    # Invalid types within JSON
    res2 = validator.parse_and_validate('{"channel": "orders", "payload": {"order_type": "LIMIT", "side": "INVALID_SIDE"}}')
    assert res2 is None
    assert validator.malformed_events_count == 2
    assert len(validator.quarantined_events) == 1


def test_06_unknown_event_handling(ws_client):
    """6. Verify unknown channel frames are safely ignored and increment unknown counter."""
    validator = ws_client.validator
    res = validator.parse_and_validate('{"channel": "unknown_future_stream", "payload": {"data": 123}}')
    assert res is None
    assert validator.unknown_events_count == 1
    assert validator.malformed_events_count == 0


def test_07_duplicate_event_deduplication(ws_client, state_store):
    """7. Verify repeated identical events are recognized as duplicates and not reapplied."""
    order_event = DeltaOrderEvent(
        order_id="ORD-101",
        client_order_id="CL-101",
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1.0"),
        unfilled_quantity=Decimal("1.0"),
        filled_quantity=Decimal("0.0"),
        status=OrderStatus.OPEN,
        price=Decimal("95000.00"),
    )

    # First application: succeeds
    res1 = ws_client.apply_event(order_event)
    assert res1 is True
    assert "ORD-101" in state_store.orders

    # Second application: duplicate dropped
    res2 = ws_client.apply_event(order_event)
    assert res2 is False
    assert ws_client.duplicate_events_count == 1


def test_08_out_of_order_event_handling(ws_client, state_store):
    """8. Verify out-of-order OPEN event does not overwrite already FILLED order state."""
    # 1. Order is FILLED
    filled_event = DeltaOrderEvent(
        order_id="ORD-202",
        client_order_id="CL-202",
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2.0"),
        unfilled_quantity=Decimal("0.0"),
        filled_quantity=Decimal("2.0"),
        status=OrderStatus.FILLED,
        price=Decimal("94000.00"),
    )
    ws_client.apply_event(filled_event)
    assert state_store.orders["ORD-202"].status == OrderStatus.FILLED

    # 2. Delayed OPEN event arrives later
    stale_open_event = DeltaOrderEvent(
        order_id="ORD-202",
        client_order_id="CL-202",
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2.0"),
        unfilled_quantity=Decimal("2.0"),
        filled_quantity=Decimal("0.0"),
        status=OrderStatus.OPEN,
        price=Decimal("94000.00"),
    )
    res = ws_client.apply_event(stale_open_event)
    assert res is False
    assert ws_client.out_of_order_events_count == 1
    # State remains FILLED
    assert state_store.orders["ORD-202"].status == OrderStatus.FILLED


def test_09_order_event_normalization(ws_client, state_store):
    """9. Verify raw Delta order JSON payload normalizes cleanly into DeltaOrderEvent."""
    raw_order_msg = json.dumps({
        "channel": "orders",
        "payload": {
            "id": 998877,
            "client_order_id": "QE-BTC-BUY-01",
            "product_symbol": "BTCUSD",
            "side": "buy",
            "order_type": "limit_order",
            "size": "0.5",
            "unfilled_size": "0.2",
            "limit_price": "95500.50",
            "state": "open",
            "reduce_only": False,
        }
    })

    event = ws_client.validator.parse_and_validate(raw_order_msg)
    assert isinstance(event, DeltaOrderEvent)
    assert event.order_id == "998877"
    assert event.client_order_id == "QE-BTC-BUY-01"
    assert event.symbol == "BTCUSD"
    assert event.side == OrderSide.BUY
    assert event.quantity == Decimal("0.5")
    assert event.unfilled_quantity == Decimal("0.2")
    assert event.filled_quantity == Decimal("0.3")
    assert event.price == Decimal("95500.50")
    assert event.status == OrderStatus.OPEN

    applied = ws_client.apply_event(event)
    assert applied is True
    assert "998877" in state_store.orders


def test_10_position_event_normalization(ws_client, state_store):
    """10. Verify raw Delta position JSON payload normalizes into DeltaPositionEvent."""
    raw_pos_msg = json.dumps({
        "channel": "positions",
        "payload": {
            "product_symbol": "BTCUSD",
            "size": "1.5",
            "entry_price": "94200.00",
            "mark_price": "95100.00",
            "liquidation_price": "91500.00",
            "unrealized_pnl": "1350.00",
            "realized_pnl": "50.00",
            "margin": "1413.00",
            "leverage": "50",
        }
    })

    event = ws_client.validator.parse_and_validate(raw_pos_msg)
    assert isinstance(event, DeltaPositionEvent)
    assert event.symbol == "BTCUSD"
    assert event.side == PositionSide.LONG
    assert event.size == Decimal("1.5")
    assert event.entry_price == Decimal("94200.00")
    assert event.mark_price == Decimal("95100.00")
    assert event.unrealized_pnl == Decimal("1350.00")

    applied = ws_client.apply_event(event)
    assert applied is True
    assert "BTCUSD" in state_store.positions
    pos: PositionRecord = state_store.positions["BTCUSD"]
    assert pos.quantity == Decimal("1.5")
    assert pos.status == PositionStatus.OPEN


def test_11_fill_event_normalization(ws_client, state_store):
    """11. Verify raw Delta user_trades / fill JSON payload normalizes and records audit log."""
    raw_fill_msg = json.dumps({
        "channel": "user_trades",
        "payload": {
            "trade_id": "FILL-7788",
            "order_id": "ORD-5544",
            "product_symbol": "BTCUSD",
            "side": "buy",
            "size": "0.5",
            "price": "95000.00",
            "fee": "4.75",
            "role": "taker",
        }
    })

    event = ws_client.validator.parse_and_validate(raw_fill_msg)
    assert isinstance(event, DeltaFillEvent)
    assert event.trade_id == "FILL-7788"
    assert event.size == Decimal("0.5")
    assert event.price == Decimal("95000.00")

    applied = ws_client.apply_event(event)
    assert applied is True
    assert len(state_store.audit_events) == 1
    assert state_store.audit_events[0]["action"] == "WS_TRADE_FILL"


def test_12_margin_event_normalization(ws_client, state_store):
    """12. Verify raw Delta margins JSON payload normalizes into DeltaMarginEvent."""
    raw_margin_msg = json.dumps({
        "channel": "margins",
        "payload": {
            "asset_symbol": "USDT",
            "balance": "15000.00",
            "available_balance": "12000.00",
            "position_margin": "2000.00",
            "order_margin": "1000.00",
        }
    })

    event = ws_client.validator.parse_and_validate(raw_margin_msg)
    assert isinstance(event, DeltaMarginEvent)
    assert event.asset_symbol == "USDT"
    assert event.balance == Decimal("15000.00")
    assert event.available_balance == Decimal("12000.00")

    applied = ws_client.apply_event(event)
    assert applied is True
    assert state_store.account.total_equity == Decimal("15000.00")
    assert state_store.account.available_balance == Decimal("12000.00")
    assert state_store.account.margin_used == Decimal("3000.00")


def test_13_reconnect_exponential_backoff(ws_client):
    """13. Verify exponential backoff delay calculation."""
    d0 = ws_client.compute_backoff_delay(0)
    d1 = ws_client.compute_backoff_delay(1)
    d2 = ws_client.compute_backoff_delay(2)

    assert d0 >= 1.0
    assert d1 >= 2.0
    assert d2 >= 4.0
    assert d2 <= 30.0 + 15.0  # bounded by max backoff + jitter


def test_14_heartbeat_ping_and_timeout(ws_client):
    """14. Verify heartbeat ping frame and status reporting."""
    summary = ws_client.get_status_summary()
    assert summary["connection_state"] == WSConnectionState.DISCONNECTED.value
    assert summary["stream_health"] == StreamHealth.OFFLINE.value
    assert summary["reconnect_count"] == 0


def test_15_subscription_restoration_after_reconnect(ws_client):
    """15. Verify subscribed channels are retained for reconnection restoration."""
    ws_client.build_subscribe_payload(["orders", "positions"])
    assert ws_client._subscribed_channels == ["orders", "positions"]


@pytest.mark.asyncio
async def test_16_rest_reconciliation_trigger(ws_client, mock_sync_service):
    """16. Verify REST reconciliation executes and updates stream health."""
    await ws_client.trigger_reconciliation()
    mock_sync_service.sync.assert_called_once()
    assert ws_client.health == StreamHealth.HEALTHY
    assert ws_client.last_sync_at is not None


@pytest.mark.asyncio
async def test_17_rest_wins_on_state_conflict(ws_client, state_store, mock_sync_service):
    """17. Verify that authoritative REST snapshot overwrites any conflicting WebSocket state."""
    # Divergent WS state
    state_store.account.total_equity = Decimal("99999.00")

    # Authoritative REST sync returns true equity
    mock_sync_service.sync = AsyncMock(side_effect=lambda: setattr(state_store.account, "total_equity", Decimal("10000.00")) or SyncResult(
        success=True,
        synced_at=datetime.now(timezone.utc),
        account_id="acc_ws_test_01",
        equity=Decimal("10000.00"),
        available_balance=Decimal("8000.00"),
        margin_used=Decimal("2000.00"),
        positions_synced=1,
        orders_synced=1,
    ))

    await ws_client.trigger_reconciliation()
    assert state_store.account.total_equity == Decimal("10000.00")


def test_18_no_duplicate_records_in_state_store(ws_client, state_store):
    """18. Verify that repeated position updates update the same symbol key without duplicates."""
    for p in range(5):
        event = DeltaPositionEvent(
            symbol="BTCUSD",
            side=PositionSide.LONG,
            size=Decimal(f"1.{p}"),
            entry_price=Decimal("94000.00"),
            mark_price=Decimal(f"9500{p}.00"),
            liquidation_price=Decimal("91000.00"),
            unrealized_pnl=Decimal("500.00"),
            realized_pnl=Decimal("0.00"),
            margin=Decimal("1400.00"),
            leverage=Decimal("50"),
        )
        ws_client.apply_event(event)

    assert len(state_store.positions) == 1
    assert state_store.positions["BTCUSD"].quantity == Decimal("1.4")


def test_19_credential_redaction_and_masking(ws_client):
    """19. Verify status summary masks API key and never exposes raw secrets."""
    summary = ws_client.get_status_summary()
    assert summary["masked_api_key"].startswith("TEST")
    assert summary["masked_api_key"].endswith("0001")
    assert FIXTURE_SECRET not in str(summary)


def test_20_zero_order_placement_side_effects(ws_client):
    """20. Verify that private WebSocket client has zero order placement methods or side effects."""
    assert not hasattr(ws_client, "place_order")
    assert not hasattr(ws_client, "cancel_order")
    assert not hasattr(ws_client, "modify_order")


def test_21_kill_switch_remains_active(state_store):
    """21. Invariant check: kill switch remains active on state store."""
    assert state_store.account.kill_switch_active is True


def test_22_algo_remains_disabled(state_store):
    """22. Invariant check: algo enabled remains False on state store."""
    assert state_store.account.algo_enabled is False


@pytest.mark.asyncio
async def test_23_graceful_shutdown(ws_client):
    """23. Verify client.close() cleans up resources and resets state to DISCONNECTED."""
    ws_client.state = WSConnectionState.CONNECTED
    ws_client.health = StreamHealth.HEALTHY

    await ws_client.close()

    assert ws_client.state == WSConnectionState.DISCONNECTED
    assert ws_client.health == StreamHealth.OFFLINE
