"""
Delta Exchange India Private Authenticated WebSocket Client.

Provides real-time streaming updates for:
1. Orders channel (`orders`)
2. Margined Positions channel (`positions`)
3. User Trades & Fills channel (`user_trades`)
4. Margins & Wallet Balances channel (`margins`)

Key Architectural Guarantees:
- Server-side HMAC-SHA256 key authentication (`key-auth`).
- Strict event normalization & validation (Decimal precision, zero floats).
- Resilient connection lifecycle: automatic reconnect, exponential backoff, heartbeat watchdog.
- Dual-layer reconciliation: REST synchronization executes on reconnect and periodically, with REST snapshots authoritative on conflict.
- Multi-tenant isolation and strict credential protection (zero secrets in logs/URLs).
- Strictly read-only state synchronization (zero real order side effects, algo_enabled=False, kill_switch_active=True).
"""

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Set, Tuple, Union

import websockets

from quantedge.instruments import delta_india_registry
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    UnknownOrderStateError,
    optional_decimal as _optional_decimal,
    required_decimal as _required_decimal,
)
from quantedge.execution.security import mask_secret
from quantedge.execution.synchronizer import (
    LocalStateStore,
    PositionRecord,
    OrderRecord,
    PositionStatus,
    LiveAccountSyncService,
)

logger = logging.getLogger("delta_private_ws")

WS_ENDPOINT = "wss://socket.india.delta.exchange"
DEFAULT_PING_INTERVAL_SECONDS = 30
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 60
MIN_RECONNECT_BACKOFF_SECONDS = 1.0
MAX_RECONNECT_BACKOFF_SECONDS = 30.0
MAX_RECONNECT_ATTEMPTS = 10

DEFAULT_PRIVATE_CHANNELS = ["orders", "positions", "user_trades", "margins"]

# Task O §O5: private channel subscription scoping.
#
# `symbols: ["all"]` does NOT deliver private position snapshots, so the
# symbol-scoped channels must name the instruments explicitly, and the
# instruments are taken from the provenance-backed registry rather than from a
# literal list here. `margins` is account-scoped and must be subscribed with NO
# `symbols` key at all -- sending one risks the subscription being rejected, and
# a rejected margins subscription means balance and margin state silently never
# update.
#
# A channel in NEITHER set has unknown scoping. Guessing it would mean guessing
# whether the exchange expects symbols for it, so `build_subscribe_payload`
# fails closed instead (§O5 / safety rule #16).
SYMBOL_SCOPED_PRIVATE_CHANNELS = frozenset({"orders", "positions", "user_trades"})
UNSCOPED_PRIVATE_CHANNELS = frozenset({"margins"})

# Task O §O5: the documented private-stream `action` values. `delete` is a
# deletion/closure statement, NOT an ordinary update -- see `DeltaPositionEvent.
# is_closure` and `_apply_order_event`.
STREAM_ACTION_CREATE = "create"
STREAM_ACTION_UPDATE = "update"
STREAM_ACTION_DELETE = "delete"
DOCUMENTED_STREAM_ACTIONS = frozenset(
    {STREAM_ACTION_CREATE, STREAM_ACTION_UPDATE, STREAM_ACTION_DELETE}
)

# Structured transport event names (Task M §M14). Emitted through `logger` with
# the event name as the first field so live logs can be filtered by event.
EVENT_WS_CONNECTING = "PRIVATE_WS_CONNECTING"
EVENT_WS_AUTH_SENT = "PRIVATE_WS_AUTH_SENT"
EVENT_WS_AUTH_ACK = "PRIVATE_WS_AUTH_ACK"
EVENT_WS_SUBSCRIBED = "PRIVATE_WS_SUBSCRIBED"
EVENT_WS_CONNECTED = "PRIVATE_WS_CONNECTED"
EVENT_WS_DISCONNECTED = "PRIVATE_WS_DISCONNECTED"
EVENT_WS_RECONNECT_ATTEMPT = "PRIVATE_WS_RECONNECT_ATTEMPT"
EVENT_WS_RECONNECT_EXHAUSTED = "PRIVATE_WS_RECONNECT_EXHAUSTED"
EVENT_WS_HEARTBEAT = "PRIVATE_WS_HEARTBEAT"
EVENT_WS_HEARTBEAT_TIMEOUT = "PRIVATE_WS_HEARTBEAT_TIMEOUT"
EVENT_WS_EVENT_RECEIVED = "PRIVATE_WS_EVENT_RECEIVED"
EVENT_WS_EVENT_DROPPED = "PRIVATE_WS_EVENT_DROPPED"
EVENT_WS_SERVER_ERROR = "PRIVATE_WS_SERVER_ERROR"
EVENT_WS_OBSERVER_ERROR = "PRIVATE_WS_OBSERVER_ERROR"
EVENT_WS_RECONCILIATION = "PRIVATE_WS_RECONCILIATION"
# Task O §O5: stream-continuity and stream-integrity events.
EVENT_WS_SEQUENCE_GAP = "PRIVATE_WS_SEQUENCE_GAP"
EVENT_WS_SEQUENCE_REPLAY = "PRIVATE_WS_SEQUENCE_REPLAY"
EVENT_WS_SEQUENCE_RESET = "PRIVATE_WS_SEQUENCE_RESET"
EVENT_WS_INTEGRITY_FAILURE = "PRIVATE_WS_INTEGRITY_FAILURE"

# Audit actions recorded on `LocalStateStore` for stream-integrity facts. These
# are the durable record; the blocking decision is taken by the lifecycle
# manager through the existing reconciliation-alert terminus (§O5 D10).
AUDIT_WS_SEQUENCE_GAP = "PRIVATE_WS_SEQUENCE_GAP"
AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED = "PRIVATE_WS_SEQUENCE_GAP_RESYNC_FAILED"
AUDIT_WS_UNKNOWN_ORDER_STATE = "PRIVATE_WS_UNKNOWN_ORDER_STATE"
AUDIT_WS_UNKNOWN_ACTION = "PRIVATE_WS_UNKNOWN_STREAM_ACTION"
AUDIT_WS_POSITION_DELETE_UNTRACKED = "PRIVATE_WS_POSITION_DELETE_UNTRACKED"
AUDIT_WS_ORDER_DELETE_STATE_CONFLICT = "PRIVATE_WS_ORDER_DELETE_STATE_CONFLICT"

# Reconciliation-alert codes handed to the EXISTING
# `TradeLifecycleManager._raise_reconciliation_alert` terminus, whose alert list
# already blocks new entries. No new blocking mechanism is introduced.
INTEGRITY_CODE_SEQUENCE_GAP = "PRIVATE_WS_SEQUENCE_GAP_UNRESOLVED"
INTEGRITY_CODE_UNKNOWN_ORDER_STATE = "PRIVATE_WS_UNKNOWN_ORDER_STATE"
INTEGRITY_CODE_UNKNOWN_ACTION = "PRIVATE_WS_UNKNOWN_STREAM_ACTION"


# ── Connection & Stream Health States ─────────────────────────────────────────


class WSConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"
    ERROR = "ERROR"


class StreamHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    OFFLINE = "OFFLINE"


# ── Typed Normalized WebSocket Event Models ───────────────────────────────────


@dataclass(frozen=True)
class DeltaOrderEvent:
    """Normalized real-time order update event from Delta Exchange private stream."""
    order_id: str
    client_order_id: Optional[str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    unfilled_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    average_fill_price: Optional[Decimal] = None
    reduce_only: bool = False
    cancellation_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Task O §O5: the documented private-stream `action` (`create` / `update` /
    # `delete`), lower-cased, or None when the frame did not state one. Appended
    # last so existing positional construction is unaffected. `delete` means the
    # order left the book; it is NOT an ordinary update, and it is deliberately
    # NOT translated into a status here -- a vanished order may have filled or
    # been cancelled, and inventing either would manufacture an exchange fact.
    action: Optional[str] = None

    @property
    def is_deletion(self) -> bool:
        """True when the exchange stated this order was deleted from the book."""
        return self.action == STREAM_ACTION_DELETE


@dataclass(frozen=True)
class DeltaPositionEvent:
    """Normalized real-time position update event from Delta Exchange private stream."""
    symbol: str
    side: PositionSide
    size: Decimal
    # Task O §O6: five optional numerics, mirroring `DeltaPosition`. The stream
    # may not carry them, and `_normalize_position` used to fabricate an
    # observation for each one -- `entry_price` and `unrealized_pnl` and `margin`
    # defaulted to `"0"`, `leverage` to `"1"`, and `mark_price` fell back to
    # whatever `entry_price` had become. `size` stays mandatory here: unlike the
    # REST snapshot boundary, an absent size is NOT refused on this path (a
    # documented `delete` frame may omit it and §O5 decides closure from
    # `is_closure`), so it keeps its parsed value.
    entry_price: Optional[Decimal]
    mark_price: Optional[Decimal]
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    # Task O §O3: `realized_pnl` is documented on the REST position object but is
    # NOT present on the `positions` stream update. `None` means "the exchange
    # did not tell us", which is a different fact from an observed flat result;
    # collapsing the two books a real closure at exactly zero P&L.
    realized_pnl: Optional[Decimal]
    margin: Optional[Decimal]
    leverage: Optional[Decimal]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Task O §O5: the documented private-stream `action`, lower-cased, or None
    # when the frame did not state one. Appended last so existing positional
    # construction is unaffected.
    action: Optional[str] = None

    @property
    def is_closure(self) -> bool:
        """The SINGLE definition of "the exchange says this position is gone".

        Task O §O5. Closure used to be inferred only from `size == 0`, in two
        independent places (`_apply_position_event` and
        `TradeLifecycleManager.handle_position_event`). Delta sends the LAST
        KNOWN size on an `action: "delete"` frame, so a real closure arriving
        with a non-zero size took the update branch instead: the local position
        was rewritten as `PositionStatus.OPEN` while the exchange was flat, the
        closure/rescan flow never ran, and a symbol with no local trade raised a
        false `ORPHAN_EXCHANGE_POSITION` alert against a position that no longer
        existed.

        The exchange-reported `size` is deliberately preserved as reported --
        rewriting it to zero would discard the last observed exposure. Closure
        is expressed here instead, once, so every consumer asks the same
        question.
        """
        return self.action == STREAM_ACTION_DELETE or self.size == Decimal("0")


@dataclass(frozen=True)
class DeltaFillEvent:
    """Normalized real-time fill / execution update event from Delta Exchange private stream."""
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    size: Decimal
    price: Decimal
    # Task O §O2: the documented fill field is `commission` (a negative value
    # means commission was EARNED in a maker role), and Delta documents that
    # `v2/user_trades` "doesn't contain commission data" at all. `None` therefore
    # means unobserved -- never zero, which would be an observed free execution.
    fee: Optional[Decimal]
    role: str  # "maker" | "taker"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DeltaMarginEvent:
    """Normalized real-time margin & balance update event from Delta Exchange private stream."""
    asset_symbol: str
    balance: Decimal
    available_balance: Decimal
    position_margin: Decimal
    order_margin: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Stream integrity (Task O §O5) ─────────────────────────────────────────────


class UnknownStreamActionError(ValueError):
    """The frame named an `action` this engine cannot interpret.

    Documented values are `create`, `update` and `delete`. An unrecognized
    action cannot be treated as an update, because it may be a deletion: reading
    a closure as a size change leaves a locally OPEN position against a flat
    exchange. A `ValueError` subclass so the existing normalization guards keep
    treating it as a refusal rather than a value.
    """


@dataclass(frozen=True)
class FrameContinuity:
    """The continuity facts of one parsed frame (§O5).

    `seq_no` is `None` when the frame did not carry one. That is "unobserved",
    not "continuous": it makes no gap claim in either direction.
    """
    channel: str
    seq_no: Optional[int]


@dataclass(frozen=True)
class DeltaStreamIntegrityEvent:
    """A provable break in private-stream trustworthiness (§O5 D10).

    This is NOT market state and never touches `LocalStateStore.positions` or
    `.orders`; it is fanned out through the EXISTING observer channel so it
    lands in `TradeLifecycleManager.observe_private_event` and terminates in the
    EXISTING `_raise_reconciliation_alert`, whose alert list already blocks new
    entries. No second state model and no new registry are introduced.

    `resynchronized` carries the whole blocking decision:
      * True  -- a gap was detected and the existing REST resync re-established
                 trust, so this is an audit/diagnostic fact and must NOT
                 permanently block new entries.
      * False -- trust could not be re-established, so the fail-closed state is
                 retained and new entries stay blocked until an operator clears
                 the alert.
    """
    code: str
    channel: str
    symbol: str
    reason: str
    expected_seq_no: Optional[int] = None
    received_seq_no: Optional[int] = None
    resynchronized: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Signature Generation Helper ──────────────────────────────────────────────


def coerce_auth_timestamp(timestamp: Union[int, str]) -> int:
    """Return the documented NUMERIC-seconds auth timestamp, or refuse (§O5).

    Delta's `key-auth` frame types `timestamp` as a number and signs
    `'GET' + str(timestamp) + '/live'`. Accepts an `int` or an all-digit `str`
    (a digit string produces a byte-identical signing message, so the existing
    signature vector is unchanged).

    A `float` is refused rather than truncated, and `bool` is refused outright:
    `int(1756000000.7)` would silently drop the fraction and produce a signing
    message that no longer matches what the exchange computed, which fails as a
    *silent* auth rejection -- a socket that connects and then delivers nothing.
    """
    if isinstance(timestamp, bool):
        raise ValueError(
            f"{timestamp!r} is not a usable auth timestamp; "
            f"a boolean is not a number of seconds"
        )
    if isinstance(timestamp, int):
        value = timestamp
    elif isinstance(timestamp, str):
        text = timestamp.strip()
        if not text.isdigit():
            raise ValueError(
                f"{timestamp!r} is not a usable auth timestamp; Delta signs "
                f"whole seconds and this engine refuses to truncate or reshape "
                f"a value the exchange would sign differently"
            )
        value = int(text)
    else:
        raise ValueError(
            f"{timestamp!r} ({type(timestamp).__name__}) is not a usable auth "
            f"timestamp; whole seconds as an int are required"
        )
    if value <= 0:
        raise ValueError(
            f"{timestamp!r} is not a usable auth timestamp; "
            f"seconds since the epoch must be positive"
        )
    return value


def generate_ws_auth_signature(api_secret: str, timestamp: Union[int, str]) -> str:
    """Generate HMAC-SHA256 signature for Delta Exchange WebSocket key-auth.

    Formula: HMAC-SHA256(secret, "GET" + str(timestamp) + "/live")

    Task O §O5: `timestamp` is numeric seconds. An all-digit string is still
    accepted and signs identically, so this is a strengthening of the input
    contract, not a change to the signing message.
    """
    if not api_secret:
        raise ValueError("API secret and timestamp are required to generate WebSocket auth signature")
    if timestamp is None or (isinstance(timestamp, str) and timestamp.strip() == ""):
        raise ValueError("API secret and timestamp are required to generate WebSocket auth signature")

    ts = coerce_auth_timestamp(timestamp)
    message = f"GET{ts}/live"
    return hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


# ── Event Validator & Normalizer ──────────────────────────────────────────────


class EventValidator:
    """Validates raw WebSocket JSON messages and converts them into typed events.
    
    Guarantees:
    - Never crashes the WebSocket loop on malformed or unexpected data.
    - Quarantines invalid events and increments diagnostic metrics.
    - Strictly uses Decimal for monetary and numeric fields.
    """

    def __init__(self):
        self.malformed_events_count = 0
        self.unknown_events_count = 0
        self.valid_events_count = 0
        self.quarantined_events: List[Dict[str, Any]] = []
        # Task O §O5: the continuity facts of the frame parsed by the most
        # recent `parse_and_validate` call, or None when that frame carried none
        # (a system frame, or a malformed frame that never reached routing).
        # `_handle_message` is the only production caller and reads this
        # immediately after its own call, on the single-threaded receive loop.
        # It is cleared at the top of every call so a stale value can never be
        # mistaken for the current frame's continuity.
        self.last_frame_continuity: Optional[FrameContinuity] = None

    @staticmethod
    def extract_seq_no(*sources: Any) -> Optional[int]:
        """Read the documented `seq_no` from the first source that states one.

        Returns None when no source carries it. Absence is unobserved
        continuity, never assumed continuity (§O5). A present-but-uninterpretable
        `seq_no` is also reported as absent rather than coerced, because a
        fabricated sequence number would silently define away the gap it was
        supposed to reveal.
        """
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw = source.get("seq_no")
            if raw is None:
                continue
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int):
                return raw
            if isinstance(raw, str) and raw.strip().isdigit():
                return int(raw.strip())
            logger.warning(
                "Ignoring uninterpretable seq_no %r; treating continuity as "
                "unobserved rather than inventing a sequence number", raw,
            )
        return None

    @staticmethod
    def extract_action(*sources: Any) -> Optional[str]:
        """Read the documented `action` from the first source that states one.

        Returns None when no source states one (every pre-§O5 fixture and any
        snapshot frame), and raises `UnknownStreamActionError` for a stated value
        outside `create` / `update` / `delete` -- an unrecognized action may be a
        deletion, and reading a deletion as an update is the §O5 D4 defect.
        """
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw = source.get("action")
            if raw is None:
                continue
            action = str(raw).strip().lower()
            if action == "":
                continue
            if action not in DOCUMENTED_STREAM_ACTIONS:
                raise UnknownStreamActionError(
                    f"{raw!r} is not a documented private-stream action "
                    f"(create, update, delete); refusing to treat an "
                    f"uninterpretable action as an ordinary update"
                )
            return action
        return None

    def parse_and_validate(self, raw_message: str) -> Optional[Any]:
        """Parse raw JSON string and return a typed event or None if non-actionable."""
        self.last_frame_continuity = None
        try:
            data = json.loads(raw_message)
        except Exception as e:
            self.malformed_events_count += 1
            logger.warning("Malformed non-JSON WebSocket frame received: %s", str(e))
            return None

        if not isinstance(data, dict):
            self.malformed_events_count += 1
            return None

        # System frames (heartbeats, acks, errors)
        msg_type = data.get("type", "")
        if msg_type in ("pong", "ping", "subscriptions", "key-auth"):
            return data

        if msg_type == "error":
            logger.error("Delta WebSocket server returned error: %s", data.get("message", data))
            return data

        # Data events: identify channel or event payload
        channel = data.get("channel") or msg_type
        payload = data.get("payload") or data.get("data") or data

        # §O5: continuity is recorded for every data frame, including one whose
        # normalization then fails -- a dropped frame must still advance/compare
        # the sequence, or a gap would be masked by the very frame that revealed
        # a problem.
        self.last_frame_continuity = FrameContinuity(
            channel=str(channel),
            seq_no=self.extract_seq_no(payload, data),
        )

        try:
            # §O5: the action is read ONCE, here, for every data frame. An
            # unrecognized action is an integrity failure, not a quarantine: it
            # propagates so the client can alert rather than dropping the frame
            # with a log line nobody reads.
            action = self.extract_action(payload, data)

            if channel in ("orders", "user_orders") or "order_type" in payload:
                event = self._normalize_order(payload, action=action)
                self.valid_events_count += 1
                return event
            elif channel in ("positions", "user_positions") or ("entry_price" in payload and "size" in payload and "product_symbol" in payload):
                event = self._normalize_position(payload, action=action)
                self.valid_events_count += 1
                return event
            elif channel in ("user_trades", "fills") or "trade_id" in payload or "fill_id" in payload:
                event = self._normalize_fill(payload)
                self.valid_events_count += 1
                return event
            elif channel in ("margins", "wallet_balances") or ("asset_symbol" in payload and "available_balance" in payload):
                event = self._normalize_margin(payload)
                self.valid_events_count += 1
                return event
            else:
                self.unknown_events_count += 1
                logger.debug("Received unknown/unsupported WebSocket channel frame: %s", channel)
                return None
        except (UnknownOrderStateError, UnknownStreamActionError):
            # §O5 D8: an uninterpretable order state or action is a fact about
            # exchange state this engine could not read. Quarantining it here
            # would be fail-closed for the FRAME but silent for the TRADE, so it
            # is raised to `_handle_message`, which owns alerting, auditing and
            # the fail-closed reconciliation requirement.
            raise
        except Exception as e:
            self.malformed_events_count += 1
            logger.warning("Failed to validate/normalize WebSocket event (%s): %s", channel, str(e))
            self.quarantined_events.append({"raw": data, "error": str(e), "timestamp": time.time()})
            return None

    def _normalize_order(
        self,
        data: Dict[str, Any],
        action: Optional[str] = None,
    ) -> DeltaOrderEvent:
        order_id = str(data.get("id") or data.get("order_id", ""))
        if not order_id:
            raise ValueError("Order event missing order_id")

        client_order_id = data.get("client_order_id")
        # A stream frame without a usable symbol must not be handed a fabricated
        # identity (this used to default to "BTCUSD" and `.upper()` its way into
        # a registered symbol). `event.symbol` becomes an `OrderRecord.symbol`
        # and a `state_store.positions` key downstream, so a fabricated value
        # would attribute a live order to an instrument the exchange never
        # named. The registry is the single source of verified symbols and
        # performs the same exact, fail-closed lookup used at the REST parse
        # boundaries; anything it cannot resolve raises `UnknownInstrumentError`,
        # which `parse_and_validate` already quarantines like any other
        # normalization failure.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        symbol = delta_india_registry().get(raw_symbol).symbol
        side = OrderSide.from_str(str(data.get("side", "BUY")))
        order_type = OrderType.from_str(str(data.get("order_type", "LIMIT_ORDER")))
        
        size = Decimal(str(data.get("size", "0")))
        unfilled_size = Decimal(str(data.get("unfilled_size", size)))
        filled_size = Decimal(str(data.get("filled_size", size - unfilled_size)))
        if filled_size < Decimal("0"):
            filled_size = Decimal("0")

        # §O5: the order state is the exchange's own statement about where this
        # order sits in its lifecycle, so it is never manufactured. This used to
        # read `data.get("state") or data.get("status", "OPEN")` and then fall
        # back to `PENDING` inside `from_exchange`, which meant BOTH an absent
        # state and an unrecognized one were answered with a fabricated
        # lifecycle position. Absence and unrecognizability are now separate
        # refusals, and neither produces a status.
        raw_state = data.get("state")
        if raw_state is None or str(raw_state).strip() == "":
            raw_state = data.get("status")
        if raw_state is None or str(raw_state).strip() == "":
            raise UnknownOrderStateError(
                f"order {order_id} arrived with no state and no status; "
                f"refusing to assume it is open"
            )
        status = OrderStatus.from_exchange(str(raw_state))

        price = Decimal(str(data["limit_price"])) if data.get("limit_price") is not None and str(data.get("limit_price")).strip() != "" else None
        stop_price = Decimal(str(data["stop_price"])) if data.get("stop_price") is not None and str(data.get("stop_price")).strip() != "" else None
        avg_price = Decimal(str(data["average_fill_price"])) if data.get("average_fill_price") is not None and str(data.get("average_fill_price")).strip() != "" else None

        return DeltaOrderEvent(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=size,
            unfilled_quantity=unfilled_size,
            filled_quantity=filled_size,
            status=status,
            price=price,
            stop_price=stop_price,
            average_fill_price=avg_price,
            reduce_only=bool(data.get("reduce_only", False)),
            cancellation_reason=data.get("cancellation_reason"),
            timestamp=datetime.now(timezone.utc),
            action=action,
        )

    def _normalize_position(
        self,
        data: Dict[str, Any],
        action: Optional[str] = None,
    ) -> DeltaPositionEvent:
        # Registry-resolved identity, exactly as in `_normalize_order`.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        symbol = delta_india_registry().get(raw_symbol).symbol
        size_dec = Decimal(str(data.get("size", "0")))
        side = PositionSide.LONG if size_dec >= Decimal("0") else PositionSide.SHORT
        abs_size = abs(size_dec)

        # Task O §O6: the same five optional numerics the REST parse now reports
        # honestly. Four of them defaulted to a fabricated observation (`"0"` for
        # entry price, unrealized PnL and margin; `"1"` for leverage), and
        # `mark_price` fell back to `entry_price` -- so a frame that reported
        # neither price produced a position marked at zero and "unchanged" from a
        # zero entry, which is a mark this engine never received. Absence is now
        # `None` on both boundaries, so a `PositionRecord` field cannot be `None`
        # from REST and `Decimal("0")` from the stream for the same missing fact.
        #
        # `size` above is deliberately NOT refused here: a documented `delete`
        # frame may omit it, and §O5 decides closure from `is_closure`.
        entry_price = _optional_decimal(data, "entry_price")
        mark_price = _optional_decimal(data, "mark_price")
        liq_raw = data.get("liquidation_price")
        liquidation_price = Decimal(str(liq_raw)) if liq_raw is not None and str(liq_raw).strip() != "" else None

        unrealized_pnl = _optional_decimal(data, "unrealised_pnl", "unrealized_pnl")
        # §O3: absent realized PnL stays absent. The documented `positions`
        # update payload carries no `realized_pnl`, so a "0" default would turn
        # every streamed closure into an observed break-even result.
        realized_pnl = _optional_decimal(data, "realised_pnl", "realized_pnl")
        margin = _optional_decimal(data, "margin")
        leverage = _optional_decimal(data, "leverage")

        return DeltaPositionEvent(
            symbol=symbol,
            side=side,
            size=abs_size,
            entry_price=entry_price,
            mark_price=mark_price,
            liquidation_price=liquidation_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            margin=margin,
            leverage=leverage,
            timestamp=datetime.now(timezone.utc),
            action=action,
        )

    def _normalize_fill(self, data: Dict[str, Any]) -> DeltaFillEvent:
        trade_id = str(data.get("id") or data.get("trade_id") or data.get("fill_id", ""))
        order_id = str(data.get("order_id", ""))
        # Registry-resolved identity, exactly as in `_normalize_order`.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        symbol = delta_india_registry().get(raw_symbol).symbol
        side = OrderSide.from_str(str(data.get("side", "BUY")))
        size = Decimal(str(data.get("size", "0")))
        price = Decimal(str(data.get("price", "0")))
        # §O2: `commission` is the ONLY documented fee field on a fill, and it is
        # read verbatim -- sign included, because a negative commission is a
        # maker rebate. It is never synthesized from the pinned maker/taker
        # rates, from `role`, or from size x price: an unobserved commission is
        # reported as unobserved.
        fee = _optional_decimal(data, "commission")
        role = str(data.get("role", "taker")).lower()

        return DeltaFillEvent(
            trade_id=trade_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            fee=fee,
            role=role,
            timestamp=datetime.now(timezone.utc),
        )

    def _normalize_margin(self, data: Dict[str, Any]) -> DeltaMarginEvent:
        """Parse a `margins`/`wallet_balances` frame, or refuse it (Task O §O7).

        The same five fabrications §O7 removed from `DeltaWalletBalance.from_dict`
        lived here, and this is the path that matters more in a live session:
        `_handle_margin_event` writes `available_balance` and
        `position_margin + order_margin` straight onto
        `LocalStateStore.account` on every frame. Fixing only the REST parser
        would have relocated the defect rather than closed it -- the same
        `AccountRecord` field would be refused from one boundary and fabricated
        from the other, exactly the disagreement §O6 C7 outlawed for positions.

        `asset_symbol` defaulted to `"USDT"`, which is worse than the REST `""`:
        an unnamed frame asserted itself to be the collateral wallet and was
        written to the account record on that basis. Rule #15 -- an unidentified
        product or asset fails closed.

        Deliberately NOT changed: this is the margin frame only. §O5 owns
        sequence continuity, action handling and closure detection, and none of
        them are touched here.

        Raises:
            DeltaResponseError: the asset is unnamed, or any of the four
                numerics is absent, blank, non-numeric or non-finite.
        """
        from quantedge.execution.delta_client import DeltaResponseError

        raw_asset = data.get("asset_symbol")
        if raw_asset is None or str(raw_asset).strip() == "":
            raise DeltaResponseError(
                "Margin frame carries no asset_symbol; refusing to assume it "
                "describes the collateral wallet"
            )
        asset_symbol = str(raw_asset).strip().upper()
        context = f"Margin frame {asset_symbol}"

        return DeltaMarginEvent(
            asset_symbol=asset_symbol,
            balance=_required_decimal(
                data, "balance", field_name="balance", context=context),
            available_balance=_required_decimal(
                data, "available_balance", field_name="available_balance",
                context=context),
            position_margin=_required_decimal(
                data, "position_margin", field_name="position_margin",
                context=context),
            order_margin=_required_decimal(
                data, "order_margin", field_name="order_margin",
                context=context),
            timestamp=datetime.now(timezone.utc),
        )


# ── Private WebSocket Client ──────────────────────────────────────────────────


class DeltaPrivateWebSocketClient:
    """Production-grade private authenticated WebSocket client for Delta Exchange India."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        state_store: LocalStateStore,
        sync_service: Optional[LiveAccountSyncService] = None,
        endpoint: str = WS_ENDPOINT,
        ping_interval: int = DEFAULT_PING_INTERVAL_SECONDS,
        heartbeat_timeout: int = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._state_store = state_store
        self._sync_service = sync_service
        self._endpoint = endpoint
        self._ping_interval = ping_interval
        self._heartbeat_timeout = heartbeat_timeout

        self.validator = EventValidator()
        self.state = WSConnectionState.DISCONNECTED
        self.health = StreamHealth.OFFLINE
        
        self.last_event_at: Optional[datetime] = None
        self.last_heartbeat_at: Optional[datetime] = None
        self.last_sync_at: Optional[datetime] = None
        
        self.reconnect_count = 0
        self.duplicate_events_count = 0
        self.out_of_order_events_count = 0
        
        self._running = False
        self._ws = None
        self._processed_fill_ids: Set[str] = set()
        self._subscribed_channels: List[str] = []

        # Task M §M1: observers receive every event that actually changed local
        # state (`apply_event` returned True). This is the ONLY outbound hook of
        # the transport: it deliberately carries no order-placement capability,
        # so bracket placement / cancellation stay in `TradeLifecycleManager`
        # (pinned by `test_20_zero_order_placement_side_effects`, which asserts
        # this client never grows `place_order` / `cancel_order` / `modify_order`).
        self._event_observers: List[Callable[[Any], Any]] = []
        # Task M §M4: callables invoked on every successful (re)connection so
        # authoritative REST reconciliation is reachable from the live session.
        self._reconciliation_hooks: List[Callable[[], Any]] = []
        self._authenticated = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_pong_monotonic: Optional[float] = None

        # Task O §O5: per-channel sequence continuity. One dict, consulted and
        # advanced inside the existing `_handle_message` funnel -- not a second
        # state model: it holds no market state, only the last `seq_no` seen per
        # continuity key.
        self._sequence_state: Dict[str, int] = {}
        self.sequence_gap_count = 0
        self.sequence_replay_count = 0
        self.missing_sequence_events_count = 0
        self.integrity_failure_count = 0
        # Retained fail-closed marker: set when a detected gap could NOT be
        # repaired by the existing REST resync, cleared only by a later
        # successful resync. Surfaced through `get_status_summary`; the entry
        # block itself is enforced by the lifecycle manager's existing
        # reconciliation-alert list (§O5 D10).
        self.stream_integrity_ok = True

    def get_status_summary(self) -> Dict[str, Any]:
        """Return safe diagnostic summary of WebSocket connection."""
        return {
            "connection_state": self.state.value,
            "stream_health": self.health.value,
            "authenticated": self._authenticated,
            "subscribed_channels": list(self._subscribed_channels),
            "masked_api_key": mask_secret(self._api_key, 4, 4),
            "endpoint": self._endpoint,
            "reconnect_count": self.reconnect_count,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "valid_events_count": self.validator.valid_events_count,
            "malformed_events_count": self.validator.malformed_events_count,
            "unknown_events_count": self.validator.unknown_events_count,
            "duplicate_events_count": self.duplicate_events_count,
            "out_of_order_events_count": self.out_of_order_events_count,
            # Task O §O5 continuity diagnostics.
            "sequence_gap_count": self.sequence_gap_count,
            "sequence_replay_count": self.sequence_replay_count,
            "missing_sequence_events_count": self.missing_sequence_events_count,
            "integrity_failure_count": self.integrity_failure_count,
            "stream_integrity_ok": self.stream_integrity_ok,
            "tracked_sequence_channels": sorted(self._sequence_state),
        }

    def build_auth_payload(
        self,
        timestamp: Optional[Union[int, str]] = None,
    ) -> Dict[str, Any]:
        """Construct the key-auth JSON payload frame.

        Task O §O5: `timestamp` is emitted as NUMERIC seconds. It used to be
        serialized as a JSON string (`str(int(time.time()))`), which the
        documented `key-auth` contract does not describe; an auth frame the
        exchange rejects yields a socket that connects and then delivers nothing,
        which is the worst failure mode on this transport because the lifecycle
        manager sees no fills, no closures and no order transitions while the
        connection looks healthy.
        """
        ts = coerce_auth_timestamp(timestamp) if timestamp is not None else int(time.time())
        signature = generate_ws_auth_signature(self._api_secret, ts)
        return {
            "type": "key-auth",
            "payload": {
                "api-key": self._api_key,
                "signature": signature,
                "timestamp": ts,
            }
        }

    def build_subscribe_payload(
        self,
        channels: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Construct the private channel subscription JSON frame.

        Task O §O5: symbol-scoped channels name their instruments EXPLICITLY.
        This used to send `symbols: ["all"]` for every channel, which does not
        deliver private position snapshots -- a live position would simply never
        appear on the stream -- and which also attached `symbols` to the
        account-scoped `margins` channel, risking a rejected subscription and
        therefore balance/margin state that silently never updates.

        Symbols come from the provenance-backed instrument registry, not from a
        literal list here, and an explicitly supplied symbol is verified through
        the same registry so an unregistered instrument fails closed instead of
        being subscribed.
        """
        chan_list = list(channels) if channels else list(DEFAULT_PRIVATE_CHANNELS)

        unknown = [
            c for c in chan_list
            if c not in SYMBOL_SCOPED_PRIVATE_CHANNELS and c not in UNSCOPED_PRIVATE_CHANNELS
        ]
        if unknown:
            raise ValueError(
                f"cannot subscribe to {unknown!r}: this engine has no documented "
                f"scoping for these private channels and refuses to guess "
                f"whether the exchange expects `symbols` for them "
                f"(symbol-scoped: {sorted(SYMBOL_SCOPED_PRIVATE_CHANNELS)}, "
                f"account-scoped: {sorted(UNSCOPED_PRIVATE_CHANNELS)})"
            )

        registry = delta_india_registry()
        if symbols is None:
            scoped_symbols = list(registry.symbols)
        else:
            # Verified, exact, fail-closed -- the same lookup used at every other
            # symbol boundary. Nothing is upper-cased, stripped or defaulted.
            scoped_symbols = [registry.get(s).symbol for s in symbols]
            if not scoped_symbols:
                raise ValueError(
                    "a symbol-scoped private subscription needs at least one "
                    "verified symbol; refusing to fall back to `all`"
                )

        self._subscribed_channels = chan_list
        built: List[Dict[str, Any]] = []
        for name in chan_list:
            if name in SYMBOL_SCOPED_PRIVATE_CHANNELS:
                built.append({"name": name, "symbols": list(scoped_symbols)})
            else:
                # Account-scoped: NO `symbols` key at all.
                built.append({"name": name})
        return {"type": "subscribe", "payload": {"channels": built}}

    # ── Event observer registration (Task M §M1) ─────────────────────────────

    def register_event_observer(self, observer: Callable[[Any], Any]) -> None:
        """Register a consumer of applied private-stream events.

        The observer is invoked with the typed event (`DeltaOrderEvent`,
        `DeltaPositionEvent`, `DeltaFillEvent`, `DeltaMarginEvent`) ONLY after
        `apply_event` reports that the event changed local state, so the
        existing duplicate / out-of-order guards in the appliers are also the
        de-duplication guards for the observers. Sync and async callables are
        both accepted. An observer raising is logged and isolated: a consumer
        defect must never tear down the event transport.
        """
        if observer not in self._event_observers:
            self._event_observers.append(observer)

    def register_reconciliation_hook(self, hook: Callable[[], Any]) -> None:
        """Register work to run on every successful (re)connection (§M4 case B).

        The hook takes no arguments and may be sync or async. This is how
        account-level reconciliation and active-trade convergence become
        reachable from the live session without this transport importing or
        driving execution itself. This module registers hooks; it never calls
        the reconciliation service.
        """
        if hook not in self._reconciliation_hooks:
            self._reconciliation_hooks.append(hook)

    def _log_event(self, event_name: str, **fields: Any) -> None:
        """Emit one structured transport log line (never includes credentials).

        The label parameter is `event_name`, not `event`: `_describe_event`
        returns a descriptor whose own `"event"` key names the event TYPE, and
        those descriptors are splatted in here. With a parameter called `event`
        every applied order, position, fill and margin frame raised
        `TypeError: got multiple values for argument 'event'` inside
        `_handle_message`, BEFORE `_notify_observers` -- so on the real transport
        the lifecycle manager would have observed no fills, no closures and no
        order transitions while the socket looked healthy. All call sites pass
        the label positionally, so the rename is confined to this signature.
        """
        if fields:
            logger.info("%s %s", event_name, json.dumps(fields, default=str, sort_keys=True))
        else:
            logger.info("%s", event_name)

    async def _notify_observers(self, event: Any) -> None:
        for observer in list(self._event_observers):
            try:
                result = observer(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.error(
                    "%s observer=%s error=%s",
                    EVENT_WS_OBSERVER_ERROR, getattr(observer, "__qualname__", repr(observer)), e,
                )

    # ── Connection transport (Task M §M1) ────────────────────────────────────

    async def connect(self) -> None:
        """Open the private WebSocket, authenticate, and subscribe.

        Ordering is mandated by Delta: `key-auth` must be accepted before the
        private channels will deliver anything, so a subscribe frame sent first
        silently yields an empty stream. The endpoint is logged; the API key is
        never placed in the URL and only ever appears masked in diagnostics.
        """
        self._log_event(EVENT_WS_CONNECTING, endpoint=self._endpoint)
        self.state = WSConnectionState.CONNECTING
        self._authenticated = False
        # §O5: a new session restarts the exchange's own numbering, so carrying
        # the previous session's last `seq_no` forward would make every reconnect
        # look like a gap. Resetting loses nothing: `run()` drives a full
        # authoritative REST resync on every successful (re)connection before the
        # first frame of the new session is consumed.
        self._reset_sequence_state(reason="connect")
        self._ws = await websockets.connect(self._endpoint)
        self._running = True
        await self.authenticate()
        await self.subscribe()
        self.state = WSConnectionState.CONNECTED
        self.health = StreamHealth.HEALTHY
        self._last_pong_monotonic = time.monotonic()
        self.last_heartbeat_at = datetime.now(timezone.utc)
        self._log_event(EVENT_WS_CONNECTED, channels=list(self._subscribed_channels))
        await self._start_heartbeat()

    async def authenticate(self) -> None:
        """Send the HMAC `key-auth` frame for this connection."""
        if self._ws is None:
            raise RuntimeError("Cannot authenticate: private WebSocket is not connected")
        self.state = WSConnectionState.AUTHENTICATING
        await self._ws.send(json.dumps(self.build_auth_payload()))
        self._log_event(EVENT_WS_AUTH_SENT, masked_api_key=mask_secret(self._api_key, 4, 4))

    async def subscribe(
        self,
        channels: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
    ) -> None:
        """Subscribe to the private order/position/fill/margin channels."""
        if self._ws is None:
            raise RuntimeError("Cannot subscribe: private WebSocket is not connected")
        payload = self.build_subscribe_payload(channels, symbols=symbols)
        await self._ws.send(json.dumps(payload))
        self._log_event(EVENT_WS_SUBSCRIBED, channels=list(self._subscribed_channels))

    async def _start_heartbeat(self) -> None:
        """Start the liveness watchdog for this connection."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Ping periodically and mark the stream STALE when the peer goes quiet.

        The watchdog never reconnects by itself: it closes the socket so the
        `listen()` loop unwinds and `run()`'s single reconnect path handles
        recovery (including the reconciliation that must follow every
        reconnect). Two competing reconnect drivers could double-subscribe the
        same account.
        """
        while self._running:
            try:
                await asyncio.sleep(self._ping_interval)
            except asyncio.CancelledError:
                return
            if not self._running or self._ws is None:
                return
            try:
                await self._ws.ping()
                self.last_heartbeat_at = datetime.now(timezone.utc)
                self._log_event(EVENT_WS_HEARTBEAT)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("%s reason=%s", EVENT_WS_HEARTBEAT_TIMEOUT, e)
                self.health = StreamHealth.STALE
                await self._drop_socket()
                return
            if self.is_stream_stale():
                logger.warning(
                    "%s idle_seconds=%.1f timeout=%s",
                    EVENT_WS_HEARTBEAT_TIMEOUT,
                    self.seconds_since_last_frame(),
                    self._heartbeat_timeout,
                )
                self.state = WSConnectionState.STALE
                self.health = StreamHealth.STALE
                await self._drop_socket()
                return

    def seconds_since_last_frame(self) -> float:
        """Monotonic seconds since the last inbound frame was observed."""
        if self._last_pong_monotonic is None:
            return 0.0
        return time.monotonic() - self._last_pong_monotonic

    def is_stream_stale(self) -> bool:
        """True when no inbound frame arrived within the heartbeat timeout."""
        if self._last_pong_monotonic is None:
            return False
        return self.seconds_since_last_frame() > self._heartbeat_timeout

    async def _drop_socket(self) -> None:
        """Close the underlying socket WITHOUT ending the client run loop."""
        ws, self._ws = self._ws, None
        self._authenticated = False
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self._log_event(EVENT_WS_DISCONNECTED, state=self.state.value)

    def apply_event(self, event: Any) -> bool:
        """Apply a validated typed event to the LocalStateStore idempotently.
        
        Returns True if state was updated, False if dropped (duplicate/stale).
        """
        now = datetime.now(timezone.utc)
        self.last_event_at = now
        self.health = StreamHealth.HEALTHY

        if isinstance(event, DeltaOrderEvent):
            return self._apply_order_event(event)
        elif isinstance(event, DeltaPositionEvent):
            return self._apply_position_event(event)
        elif isinstance(event, DeltaFillEvent):
            return self._apply_fill_event(event)
        elif isinstance(event, DeltaMarginEvent):
            return self._apply_margin_event(event)
        return False

    # §O5: statuses that are themselves a statement that the order is finished.
    # A `delete` frame carrying one of these is self-consistent and is applied
    # normally; a `delete` carrying anything else contradicts itself.
    TERMINAL_ORDER_STATUSES = (
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    )

    def _apply_order_event(self, event: DeltaOrderEvent) -> bool:
        order_key = event.order_id
        existing = self._state_store.orders.get(order_key)

        # §O5: `action: "delete"` says the order left the book. It is
        # deliberately NOT translated into a terminal status here: a vanished
        # order may have filled OR been cancelled, and choosing either would
        # manufacture an exchange fact -- writing `CANCELLED` over an order that
        # actually filled would hide a live position, and writing `FILLED` over a
        # cancelled one would invent an entry that never happened.
        #
        # So a delete whose own reported status is non-terminal is a
        # contradiction the exchange has to resolve: the local record is left
        # exactly as it stands, the conflict is audited, and the authoritative
        # REST reconciliation path decides. No `OrderRecord` is ever removed by a
        # stream frame, which is also what keeps a resting §O1 stop-loss -- which
        # rests in `pending`, and which §O4 widened `GET /v2/orders` to report --
        # from being erased by the stream and re-placed as a duplicate.
        if event.is_deletion and event.status not in self.TERMINAL_ORDER_STATUSES:
            logger.warning(
                "%s order=%s symbol=%s reported_status=%s: a deleted order "
                "cannot also be resting; local state retained for REST "
                "reconciliation",
                AUDIT_WS_ORDER_DELETE_STATE_CONFLICT, order_key, event.symbol,
                event.status.value,
            )
            self._state_store.record_audit(
                action=AUDIT_WS_ORDER_DELETE_STATE_CONFLICT,
                details={
                    "order_id": order_key,
                    "client_order_id": event.client_order_id,
                    "symbol": event.symbol,
                    "reported_status": event.status.value,
                    "locally_tracked": existing is not None,
                    "local_status": existing.status.value if existing else None,
                    "resolution": "RETAINED_PENDING_REST_RECONCILIATION",
                },
            )
            return False

        if existing:
            # Idempotency & Out-of-order protection: check if status/sequence is already ahead or duplicate
            if existing.status == event.status and existing.filled_quantity == event.filled_quantity:
                self.duplicate_events_count += 1
                return False

            if existing.status in (OrderStatus.FILLED, OrderStatus.CANCELLED) and event.status == OrderStatus.OPEN:
                self.out_of_order_events_count += 1
                logger.debug("Ignored out-of-order OPEN order event for already finalized order %s", order_key)
                return False

            existing.status = event.status
            existing.filled_quantity = event.filled_quantity
            existing.price = event.price or existing.price
            existing.average_fill_price = event.average_fill_price or existing.average_fill_price
            existing.updated_at = event.timestamp
            if event.status == OrderStatus.FILLED:
                existing.filled_at = event.timestamp
            elif event.status == OrderStatus.CANCELLED:
                existing.cancelled_at = event.timestamp
        else:
            record = OrderRecord(
                delta_order_id=event.order_id,
                client_order_id=event.client_order_id,
                symbol=event.symbol,
                side=event.side,
                order_type=event.order_type,
                quantity=event.quantity,
                filled_quantity=event.filled_quantity,
                status=event.status,
                price=event.price,
                stop_price=event.stop_price,
                average_fill_price=event.average_fill_price,
                reduce_only=event.reduce_only,
                placed_at=event.timestamp,
                updated_at=event.timestamp,
            )
            self._state_store.orders[order_key] = record

        return True

    def _apply_position_event(self, event: DeltaPositionEvent) -> bool:
        symbol = event.symbol
        existing = self._state_store.positions.get(symbol)

        # §O5: ONE closure question, answered by `DeltaPositionEvent.is_closure`
        # (`action == "delete"` OR a reported size of zero). This used to test
        # `event.size == Decimal("0")` directly, so a deletion carrying the last
        # known non-zero size fell through to the update branch below and was
        # rewritten as `PositionStatus.OPEN` against a flat exchange.
        if event.is_closure:
            if existing:
                existing.status = PositionStatus.CLOSED
                existing.closed_at = event.timestamp
                existing.updated_at = event.timestamp
                self._state_store.position_history.append(existing)
                del self._state_store.positions[symbol]
                return True
            # No local position to close. That is still a statement about
            # exchange state and must not vanish, so an explicit deletion of an
            # untracked symbol is recorded rather than silently dropped.
            if event.action == STREAM_ACTION_DELETE:
                logger.warning(
                    "%s symbol=%s reported_size=%s has no locally tracked position",
                    AUDIT_WS_POSITION_DELETE_UNTRACKED, symbol, event.size,
                )
                self._state_store.record_audit(
                    action=AUDIT_WS_POSITION_DELETE_UNTRACKED,
                    details={
                        "symbol": symbol,
                        "reported_size": str(event.size),
                        "stream_action": event.action,
                    },
                )
            return False

        if existing:
            if existing.quantity == event.size and existing.current_price == event.mark_price and existing.unrealized_pnl == event.unrealized_pnl:
                self.duplicate_events_count += 1
                return False

            existing.side = event.side
            existing.quantity = event.size
            existing.entry_price = event.entry_price
            existing.current_price = event.mark_price
            existing.liquidation_price = event.liquidation_price
            existing.unrealized_pnl = event.unrealized_pnl
            existing.realized_pnl = event.realized_pnl
            existing.margin_used = event.margin
            existing.leverage = event.leverage
            existing.status = PositionStatus.OPEN
            existing.updated_at = event.timestamp
        else:
            record = PositionRecord(
                symbol=symbol,
                side=event.side,
                quantity=event.size,
                entry_price=event.entry_price,
                current_price=event.mark_price,
                unrealized_pnl=event.unrealized_pnl,
                realized_pnl=event.realized_pnl,
                leverage=event.leverage,
                margin_used=event.margin,
                liquidation_price=event.liquidation_price,
                status=PositionStatus.OPEN,
                opened_at=event.timestamp,
                updated_at=event.timestamp,
            )
            self._state_store.positions[symbol] = record

        return True

    def _apply_fill_event(self, event: DeltaFillEvent) -> bool:
        if event.trade_id in self._processed_fill_ids:
            self.duplicate_events_count += 1
            return False

        self._processed_fill_ids.add(event.trade_id)
        self._state_store.record_audit(
            action="WS_TRADE_FILL",
            details={
                "trade_id": event.trade_id,
                "order_id": event.order_id,
                "symbol": event.symbol,
                "side": event.side.value,
                "size": str(event.size),
                "price": str(event.price),
                # §O2: an unobserved commission is recorded as null, never "0".
                "fee": str(event.fee) if event.fee is not None else None,
                "fee_source": "EXCHANGE_COMMISSION" if event.fee is not None else "UNOBSERVED",
                "role": event.role,
            }
        )
        return True

    def _apply_margin_event(self, event: DeltaMarginEvent) -> bool:
        if event.asset_symbol in ("USDT", "USD"):
            self._state_store.account.total_equity = event.balance
            self._state_store.account.current_balance = event.balance
            self._state_store.account.available_balance = event.available_balance
            self._state_store.account.margin_used = event.position_margin + event.order_margin
            self._state_store.account.last_synced_at = event.timestamp
            return True
        return False

    # ── Receive loop & frame routing (Task M §M1) ────────────────────────────

    @staticmethod
    def _describe_event(event: Any) -> Dict[str, Any]:
        """Safe, credential-free structured descriptor for a typed event."""
        if isinstance(event, DeltaOrderEvent):
            return {
                "event": "order", "order_id": event.order_id,
                "client_order_id": event.client_order_id, "symbol": event.symbol,
                "status": event.status.value, "filled": str(event.filled_quantity),
                "quantity": str(event.quantity), "action": event.action,
            }
        if isinstance(event, DeltaPositionEvent):
            return {"event": "position", "symbol": event.symbol, "size": str(event.size),
                    "side": event.side.value, "action": event.action,
                    "is_closure": event.is_closure}
        if isinstance(event, DeltaFillEvent):
            return {"event": "fill", "trade_id": event.trade_id, "order_id": event.order_id,
                    "symbol": event.symbol, "size": str(event.size), "price": str(event.price)}
        if isinstance(event, DeltaMarginEvent):
            return {"event": "margin", "asset": event.asset_symbol}
        return {"event": type(event).__name__}

    def _handle_system_frame(self, data: Dict[str, Any]) -> None:
        """Route a non-data control frame (auth ack, heartbeat, subscriptions, error)."""
        msg_type = str(data.get("type", ""))
        if msg_type in ("pong", "ping"):
            self.last_heartbeat_at = datetime.now(timezone.utc)
            return
        if msg_type == "key-auth":
            # Delta answers the auth frame on the same `key-auth` type. Anything
            # carrying an error marker leaves the connection unauthenticated so
            # the run loop treats it as a failed attempt rather than assuming a
            # working private stream that would silently deliver nothing.
            if data.get("success") is False or data.get("error"):
                self._authenticated = False
                logger.error("%s stage=key-auth detail=%s", EVENT_WS_SERVER_ERROR, data.get("error") or data)
                self.health = StreamHealth.DEGRADED
                return
            self._authenticated = True
            self._log_event(EVENT_WS_AUTH_ACK)
            return
        if msg_type == "subscriptions":
            self._log_event(EVENT_WS_SUBSCRIBED, ack=True, channels=list(self._subscribed_channels))
            return
        if msg_type == "error":
            # `parse_and_validate` already logged the server text; record the
            # degraded health so `get_status_summary` surfaces it.
            self.health = StreamHealth.DEGRADED
            self._log_event(EVENT_WS_SERVER_ERROR, code=data.get("code"))
            return

    # ── Sequence continuity (Task O §O5) ─────────────────────────────────────

    @staticmethod
    def _sequence_key(channel: str, payload: Any) -> str:
        """Return the continuity key a frame's `seq_no` belongs to.

        Continuity is tracked PER CHANNEL, per the documented contract, and this
        helper is the single place that decides the granularity. `payload` is
        accepted (and deliberately unused) so that if a Phase-2 testnet probe
        shows the exchange numbers per (channel, symbol) instead, exactly one
        function changes and nothing else in the funnel has to move.
        """
        return str(channel)

    def _reset_sequence_state(self, reason: str) -> None:
        """Forget per-channel continuity (new session ⇒ new numbering)."""
        if self._sequence_state:
            self._log_event(
                EVENT_WS_SEQUENCE_RESET, reason=reason,
                channels=sorted(self._sequence_state),
            )
        self._sequence_state = {}

    def _track_sequence(self, continuity: FrameContinuity) -> Optional[Tuple[int, int]]:
        """Compare and advance continuity for one frame.

        Returns `(expected, received)` when a GAP is proven, otherwise None.

        * `received == expected`  -- in order, advance.
        * `received <= last`      -- a replay/duplicate, counted as such. It is
          explicitly NOT a gap: the exchange has already delivered that frame,
          and the de-duplication guards in the appliers own it.
        * `received > expected`   -- frames were lost. This is the only gap.
        * no `seq_no` at all      -- unobserved continuity. Counted, and no claim
          is made in either direction; inventing continuity here would define
          away the very gap this exists to reveal.
        """
        if continuity.seq_no is None:
            self.missing_sequence_events_count += 1
            return None

        key = self._sequence_key(continuity.channel, None)
        received = continuity.seq_no
        last = self._sequence_state.get(key)

        if last is None:
            # First observation on this channel establishes the baseline; there
            # is nothing to compare it against.
            self._sequence_state[key] = received
            return None

        expected = last + 1
        if received == expected:
            self._sequence_state[key] = received
            return None

        if received <= last:
            self.sequence_replay_count += 1
            self._log_event(
                EVENT_WS_SEQUENCE_REPLAY, channel=continuity.channel,
                last_seq_no=last, received_seq_no=received,
            )
            return None

        # received > expected: frames were lost.
        self.sequence_gap_count += 1
        self._sequence_state[key] = received
        return (expected, received)

    async def _handle_sequence_gap(
        self,
        continuity: FrameContinuity,
        expected: int,
        received: int,
    ) -> None:
        """Alert, resynchronize, and retain the fail-closed state on failure.

        Reuses the EXISTING resync path (`trigger_reconciliation`, which drives
        `_sync_service.sync()` and every registered reconciliation hook) and the
        EXISTING alert terminus (the lifecycle manager's reconciliation-alert
        list, reached through the existing observer channel). Nothing parallel is
        introduced.
        """
        # Frames `expected .. received - 1` were lost; `received` itself arrived,
        # so it is NOT counted as missing.
        missing = received - expected
        logger.critical(
            "%s channel=%s expected=%s received=%s missing=%s",
            EVENT_WS_SEQUENCE_GAP, continuity.channel, expected, received, missing,
        )
        self.health = StreamHealth.DEGRADED
        self._state_store.record_audit(
            action=AUDIT_WS_SEQUENCE_GAP,
            details={
                "channel": continuity.channel,
                "expected_seq_no": expected,
                "received_seq_no": received,
                "missing_frames": missing,
            },
        )

        resynchronized = await self.trigger_reconciliation()

        if resynchronized:
            # Trust re-established from the authoritative REST snapshot. The gap
            # is a recorded diagnostic, NOT a standing block on new entries.
            self.stream_integrity_ok = True
        else:
            self.stream_integrity_ok = False
            self.health = StreamHealth.DEGRADED
            logger.critical(
                "%s channel=%s expected=%s received=%s: REST resynchronization "
                "did not re-establish trustworthy state; new entries must stay "
                "blocked", AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED,
                continuity.channel, expected, received,
            )
            self._state_store.record_audit(
                action=AUDIT_WS_SEQUENCE_GAP_RESYNC_FAILED,
                details={
                    "channel": continuity.channel,
                    "expected_seq_no": expected,
                    "received_seq_no": received,
                    "missing_frames": missing,
                },
            )

        await self._notify_observers(DeltaStreamIntegrityEvent(
            code=INTEGRITY_CODE_SEQUENCE_GAP,
            channel=continuity.channel,
            symbol="STREAM",
            reason=(
                f"{missing} private frame(s) were lost on channel "
                f"{continuity.channel} (expected seq_no {expected}, "
                f"received {received})"
            ),
            expected_seq_no=expected,
            received_seq_no=received,
            resynchronized=resynchronized,
        ))

    async def _handle_integrity_failure(
        self,
        code: str,
        channel: str,
        audit_action: str,
        error: Exception,
    ) -> None:
        """Report an uninterpretable frame as a blocking integrity failure.

        §O5 D8: an unknown order state or an unknown `action` is a fact about
        exchange state this engine could not read. Quarantining it is fail-closed
        for the FRAME but silent for the TRADE, so it is escalated here: audited,
        health-degraded, and routed through the existing observer channel to the
        existing reconciliation-alert terminus. `resynchronized=False` because
        nothing was resynchronized -- the frame's meaning is simply unknown, and
        that is exactly the condition new entries must not be admitted on top of.
        """
        self.integrity_failure_count += 1
        self.stream_integrity_ok = False
        self.health = StreamHealth.DEGRADED
        logger.critical("%s code=%s channel=%s detail=%s",
                        EVENT_WS_INTEGRITY_FAILURE, code, channel, error)
        self._state_store.record_audit(
            action=audit_action,
            details={"channel": channel, "code": code, "detail": str(error)},
        )
        await self._notify_observers(DeltaStreamIntegrityEvent(
            code=code,
            channel=channel,
            symbol="STREAM",
            reason=str(error),
            resynchronized=False,
        ))

    async def _handle_message(self, raw_message: Any) -> bool:
        """Parse one raw frame, apply it, and fan it out to observers.

        Returns True only when the frame changed local state. Never raises: a
        malformed or unexpected frame must not tear down the receive loop.
        """
        if isinstance(raw_message, (bytes, bytearray)):
            raw_message = raw_message.decode("utf-8", errors="replace")
        elif not isinstance(raw_message, str):
            raw_message = json.dumps(raw_message, default=str)

        # Any inbound frame is proof of liveness for the staleness watchdog.
        self._last_pong_monotonic = time.monotonic()

        try:
            parsed = self.validator.parse_and_validate(raw_message)
        except UnknownOrderStateError as e:
            await self._handle_integrity_failure(
                INTEGRITY_CODE_UNKNOWN_ORDER_STATE,
                self._failed_frame_channel(), AUDIT_WS_UNKNOWN_ORDER_STATE, e,
            )
            return False
        except UnknownStreamActionError as e:
            await self._handle_integrity_failure(
                INTEGRITY_CODE_UNKNOWN_ACTION,
                self._failed_frame_channel(), AUDIT_WS_UNKNOWN_ACTION, e,
            )
            return False

        # §O5: continuity is consumed for every data frame, BEFORE the frame is
        # applied, and independently of whether it was applied -- a duplicate or
        # dropped frame still carries the sequence information that proves
        # whether anything was lost.
        continuity = self.validator.last_frame_continuity
        gap: Optional[Tuple[int, int]] = None
        if continuity is not None:
            gap = self._track_sequence(continuity)

        if parsed is None:
            if gap is not None:
                await self._handle_sequence_gap(continuity, gap[0], gap[1])
            return False
        if isinstance(parsed, dict):
            self._handle_system_frame(parsed)
            return False

        applied = self.apply_event(parsed)
        descriptor = self._describe_event(parsed)
        if not applied:
            self._log_event(EVENT_WS_EVENT_DROPPED, **descriptor)
        else:
            self._log_event(EVENT_WS_EVENT_RECEIVED, **descriptor)
            await self._notify_observers(parsed)

        if gap is not None:
            await self._handle_sequence_gap(continuity, gap[0], gap[1])
        return applied

    def _failed_frame_channel(self) -> str:
        """Best available channel label for a frame that failed to normalize."""
        continuity = self.validator.last_frame_continuity
        return continuity.channel if continuity is not None else "unknown"

    async def listen(self) -> None:
        """Consume frames until the socket closes or `close()` is called."""
        ws = self._ws
        if ws is None:
            raise RuntimeError("Cannot listen: private WebSocket is not connected")
        try:
            async for message in ws:
                if not self._running:
                    break
                try:
                    await self._handle_message(message)
                except Exception as e:  # defensive: observers/appliers already guard
                    logger.error("Unhandled error while processing private WS frame: %s", e)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("%s reason=connection_closed detail=%s", EVENT_WS_DISCONNECTED, e)
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                self._heartbeat_task = None

    async def run(self, max_attempts: int = MAX_RECONNECT_ATTEMPTS) -> None:
        """Connect, reconcile, listen, and reconnect with bounded backoff.

        Reconciliation runs on EVERY successful (re)connection, before the first
        frame is consumed, because anything that happened on the exchange while
        the socket was down is only recoverable from the REST snapshot
        (Task M §M4 cases A and B). Attempts are bounded: after `max_attempts`
        consecutive failures the client stops OFFLINE rather than spinning, so
        the operator-visible state is an explicit outage instead of a stream
        that appears alive.
        """
        self._running = True
        attempt = 0
        while self._running:
            try:
                await self.connect()
                attempt = 0
                await self.trigger_reconciliation()
                await self.listen()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state = WSConnectionState.ERROR
                logger.error("Private WebSocket session failed: %s", e)

            if not self._running:
                break

            await self._drop_socket()
            attempt += 1
            self.reconnect_count += 1
            if attempt > max_attempts:
                logger.critical(
                    "%s attempts=%s: private order/position stream is OFFLINE",
                    EVENT_WS_RECONNECT_EXHAUSTED, max_attempts,
                )
                self.state = WSConnectionState.ERROR
                self.health = StreamHealth.OFFLINE
                self._running = False
                break

            delay = self.compute_backoff_delay(attempt - 1)
            self.state = WSConnectionState.RECONNECTING
            self.health = StreamHealth.DEGRADED
            self._log_event(EVENT_WS_RECONNECT_ATTEMPT, attempt=attempt, delay_seconds=round(delay, 3))
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def trigger_reconciliation(self) -> bool:
        """Trigger authoritative REST reconciliation after reconnect or periodically.

        REST SNAPSHOT ALWAYS WINS over WebSocket state.

        Task O §O5: returns whether trustworthy state was re-established -- True
        only when the sync service reported success (or none is configured) AND
        every registered reconciliation hook completed. The return value is
        ADDITIVE: `run()` and every existing caller ignore it, and the swallowing
        behaviour below is unchanged so a reconciliation failure still cannot
        tear down the transport. The gap path in `_handle_sequence_gap` is the
        first caller that needs the answer, because "the resync failed" is what
        turns a repaired gap into a retained fail-closed condition.
        """
        trustworthy = True

        if self._sync_service is not None:
            try:
                self._log_event(EVENT_WS_RECONCILIATION, stage="started")
                res = await self._sync_service.sync()
                self.last_sync_at = datetime.now(timezone.utc)
                if res.success:
                    self.health = StreamHealth.HEALTHY
                    self._log_event(EVENT_WS_RECONCILIATION, stage="completed", success=True)
                else:
                    self.health = StreamHealth.DEGRADED
                    trustworthy = False
                    logger.warning("Reconciliation completed with errors: %s", res.error)
            except Exception as e:
                self.health = StreamHealth.DEGRADED
                trustworthy = False
                logger.error("REST reconciliation failed: %s", str(e))

        # Task M §M4 case B: whatever the deployment wires in here (account
        # reconciliation, active-trade convergence) runs on every successful
        # (re)connection, before the first frame of the new session is consumed.
        # Hooks are read-only from this client's point of view: it neither
        # inspects nor depends on what they do.
        for hook in list(self._reconciliation_hooks):
            try:
                result = hook()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.health = StreamHealth.DEGRADED
                trustworthy = False
                logger.error("Reconciliation hook %r failed: %s", getattr(hook, "__name__", hook), e)

        return trustworthy

    def compute_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        delay = min(MAX_RECONNECT_BACKOFF_SECONDS, MIN_RECONNECT_BACKOFF_SECONDS * (2 ** attempt))
        jitter = random.uniform(0, 0.5 * delay)
        return delay + jitter

    async def close(self) -> None:
        """Gracefully disconnect the WebSocket client."""
        self._running = False
        self.state = WSConnectionState.DISCONNECTED
        self.health = StreamHealth.OFFLINE
        self._authenticated = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._log_event(EVENT_WS_DISCONNECTED, graceful=True)
        logger.info("Delta Private WebSocket client closed cleanly")
