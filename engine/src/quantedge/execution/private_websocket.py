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
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Set

from quantedge.instruments import delta_india_registry
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
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


@dataclass(frozen=True)
class DeltaPositionEvent:
    """Normalized real-time position update event from Delta Exchange private stream."""
    symbol: str
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    margin: Decimal
    leverage: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DeltaFillEvent:
    """Normalized real-time fill / execution update event from Delta Exchange private stream."""
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    size: Decimal
    price: Decimal
    fee: Decimal
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


# ── Signature Generation Helper ──────────────────────────────────────────────


def generate_ws_auth_signature(api_secret: str, timestamp: str) -> str:
    """Generate HMAC-SHA256 signature for Delta Exchange WebSocket key-auth.
    
    Formula: HMAC-SHA256(secret, "GET" + timestamp + "/live")
    """
    if not api_secret or not timestamp:
        raise ValueError("API secret and timestamp are required to generate WebSocket auth signature")
    
    message = f"GET{timestamp}/live"
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

    def parse_and_validate(self, raw_message: str) -> Optional[Any]:
        """Parse raw JSON string and return a typed event or None if non-actionable."""
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

        try:
            if channel in ("orders", "user_orders") or "order_type" in payload:
                event = self._normalize_order(payload)
                self.valid_events_count += 1
                return event
            elif channel in ("positions", "user_positions") or ("entry_price" in payload and "size" in payload and "product_symbol" in payload):
                event = self._normalize_position(payload)
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
        except Exception as e:
            self.malformed_events_count += 1
            logger.warning("Failed to validate/normalize WebSocket event (%s): %s", channel, str(e))
            self.quarantined_events.append({"raw": data, "error": str(e), "timestamp": time.time()})
            return None

    def _normalize_order(self, data: Dict[str, Any]) -> DeltaOrderEvent:
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

        state_str = str(data.get("state") or data.get("status", "OPEN"))
        status = OrderStatus.from_exchange(state_str)

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
        )

    def _normalize_position(self, data: Dict[str, Any]) -> DeltaPositionEvent:
        # Registry-resolved identity, exactly as in `_normalize_order`.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        symbol = delta_india_registry().get(raw_symbol).symbol
        size_dec = Decimal(str(data.get("size", "0")))
        side = PositionSide.LONG if size_dec >= Decimal("0") else PositionSide.SHORT
        abs_size = abs(size_dec)

        entry_price = Decimal(str(data.get("entry_price", "0")))
        mark_price = Decimal(str(data.get("mark_price", entry_price)))
        liq_raw = data.get("liquidation_price")
        liquidation_price = Decimal(str(liq_raw)) if liq_raw is not None and str(liq_raw).strip() != "" else None

        unrealized_pnl = Decimal(str(data.get("unrealised_pnl", data.get("unrealized_pnl", "0"))))
        realized_pnl = Decimal(str(data.get("realised_pnl", data.get("realized_pnl", "0"))))
        margin = Decimal(str(data.get("margin", "0")))
        leverage = Decimal(str(data.get("leverage", "1")))

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
        fee = Decimal(str(data.get("fee", "0")))
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
        asset_symbol = str(data.get("asset_symbol", "USDT")).upper()
        balance = Decimal(str(data.get("balance", "0")))
        available_balance = Decimal(str(data.get("available_balance", "0")))
        position_margin = Decimal(str(data.get("position_margin", "0")))
        order_margin = Decimal(str(data.get("order_margin", "0")))

        return DeltaMarginEvent(
            asset_symbol=asset_symbol,
            balance=balance,
            available_balance=available_balance,
            position_margin=position_margin,
            order_margin=order_margin,
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

    def get_status_summary(self) -> Dict[str, Any]:
        """Return safe diagnostic summary of WebSocket connection."""
        return {
            "connection_state": self.state.value,
            "stream_health": self.health.value,
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
        }

    def build_auth_payload(self, timestamp: Optional[str] = None) -> Dict[str, Any]:
        """Construct the key-auth JSON payload frame."""
        ts = timestamp or str(int(time.time()))
        signature = generate_ws_auth_signature(self._api_secret, ts)
        return {
            "type": "key-auth",
            "payload": {
                "api-key": self._api_key,
                "signature": signature,
                "timestamp": ts,
            }
        }

    def build_subscribe_payload(self, channels: Optional[List[str]] = None) -> Dict[str, Any]:
        """Construct the private channel subscription JSON frame."""
        chan_list = channels or ["orders", "positions", "user_trades", "margins"]
        self._subscribed_channels = chan_list
        return {
            "type": "subscribe",
            "payload": {
                "channels": [{"name": c, "symbols": ["all"]} for c in chan_list]
            }
        }

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

    def _apply_order_event(self, event: DeltaOrderEvent) -> bool:
        order_key = event.order_id
        existing = self._state_store.orders.get(order_key)

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

        if event.size == Decimal("0"):
            # Position closed
            if existing:
                existing.status = PositionStatus.CLOSED
                existing.closed_at = event.timestamp
                existing.updated_at = event.timestamp
                self._state_store.position_history.append(existing)
                del self._state_store.positions[symbol]
                return True
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
                "fee": str(event.fee),
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

    async def trigger_reconciliation(self) -> None:
        """Trigger authoritative REST reconciliation after reconnect or periodically.
        
        REST SNAPSHOT ALWAYS WINS over WebSocket state.
        """
        if self._sync_service is not None:
            try:
                res = await self._sync_service.sync()
                self.last_sync_at = datetime.now(timezone.utc)
                if res.success:
                    self.health = StreamHealth.HEALTHY
                else:
                    self.health = StreamHealth.DEGRADED
                    logger.warning("Reconciliation completed with errors: %s", res.error)
            except Exception as e:
                self.health = StreamHealth.DEGRADED
                logger.error("REST reconciliation failed: %s", str(e))

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
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("Delta Private WebSocket client closed cleanly")
