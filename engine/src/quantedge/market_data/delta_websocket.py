"""Delta Exchange WebSocket client for BTCUSD 1H candlesticks.

Connects to wss://api.india.delta.exchange/ws and subscribes to the
candlestick_1h channel for BTCUSD.

Key guarantees:
- Only CLOSED candles are passed to the SMC engine / callback.
- Each closed timestamp is processed exactly once (deduplication).
- On disconnect, REST backfill recovers any missed closed candles.
- Heartbeat detects stale connections and triggers reconnect.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Callable, Any

import websockets

from quantedge.market_data.ingestion import detect_gaps, _fetch_window

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("delta_ws")

SYMBOL_LOCAL = "BTCUSD.P"        # Display / TradingView symbol
SYMBOL_EXCHANGE = "BTCUSD"       # Symbol sent to Delta Exchange API
WS_ENDPOINT = "wss://api.india.delta.exchange/ws"
SUBSCRIPTION_CHANNEL = "candlestick_1h"
SUBSCRIPTION_SYMBOL = "BTCUSD"
TIMEFRAME = "1h"

# Backward-compatibility alias: 1H candle is closed once its hour boundary passes.
# Kept for tests that import this constant.
CLOSED_CANDLE_THRESHOLD_SECONDS = 3600

MAX_RECONNECT_ATTEMPTS = 10
MAX_BACKOFF_SECONDS = 60
MIN_BACKOFF_SECONDS = 2
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 60

# Event type constants
EVENT_CONNECT = "CONNECT"
EVENT_DISCONNECT = "DISCONNECT"
EVENT_RECONNECT = "RECONNECT"
EVENT_HEARTBEAT = "HEARTBEAT"
EVENT_HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
EVENT_SUBSCRIBE = "SUBSCRIBE"
EVENT_CANDLE_CLOSED = "CANDLE_CLOSED"
EVENT_CANDLE_FORMING = "CANDLE_FORMING"
EVENT_CANDLE_DUPLICATE = "CANDLE_DUPLICATE"
EVENT_GAP_DETECTED = "GAP_DETECTED"
EVENT_BACKFILL_STARTED = "BACKFILL_STARTED"
EVENT_BACKFILL_COMPLETED = "BACKFILL_COMPLETED"
EVENT_BACKFILL_ERROR = "BACKFILL_ERROR"
EVENT_OB_CREATED = "OB_CREATED"
EVENT_OB_TOUCHED = "OB_TOUCHED"
EVENT_OB_INVALIDATED = "OB_INVALIDATED"
EVENT_STATE_SAVED = "STATE_SAVED"
EVENT_STATE_RESTORED = "STATE_RESTORED"


def _is_candle_closed(candle_ts: int) -> bool:
    """Return True iff the 1H candle at candle_ts is fully closed.

    Contract:
        A candle beginning at T covers the interval [T, T+3600).
        It is closed only once the T+3600 boundary has passed,
        i.e. when  current_hour_start > candle_ts.

        current_hour_start = floor(now / 3600) * 3600

    Boundary table (candle at 12:00 UTC, current_hour_start shown):
        now=11:59  → chs=11:00 → 11:00 > 12:00? No  → forming   ✓
        now=12:00  → chs=12:00 → 12:00 > 12:00? No  → forming   ✓
        now=12:59  → chs=12:00 → 12:00 > 12:00? No  → forming   ✓
        now=13:00  → chs=13:00 → 13:00 > 12:00? Yes → CLOSED     ✓
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    current_hour_start = now_ts - (now_ts % 3600)
    return candle_ts < current_hour_start


def _parse_candle_from_ws(data: dict) -> Optional[dict]:
    """Parse a raw Delta WebSocket message into a normalised candle dict.

    Delta Exchange candlestick_1h message format:
    {
        "type": "candlestick_1h",
        "ts":   <unix_seconds>,
        "o":    "50000",
        "h":    "50100",
        "l":    "49900",
        "c":    "50050",
        "v":    "1.5",
        "sy":   "BTCUSD"
    }

    Returns None for non-candle messages or parse errors.
    """
    msg_type = data.get("type", "")
    # Accept both the direct flat format and the legacy nested format
    if msg_type == SUBSCRIPTION_CHANNEL:
        # Flat Delta format
        try:
            candle_ts = int(data["ts"])
            candle_open = Decimal(str(data["o"]))
            candle_high = Decimal(str(data["h"]))
            candle_low = Decimal(str(data["l"]))
            candle_close = Decimal(str(data["c"]))
            candle_volume = Decimal(str(data["v"]))
            symbol = data.get("sy", SYMBOL_EXCHANGE)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to parse Delta WS candle fields: %s | msg=%s", e, data)
            return None
    elif msg_type in ("subscriptions", "heartbeat", "ping", "pong", "info"):
        # Control / subscription-ack messages — not candles
        return None
    else:
        # Unknown message type — ignore silently
        return None

    return {
        "symbol": SYMBOL_LOCAL,
        "timeframe": TIMEFRAME,
        "timestamp": candle_ts,
        "open": candle_open,
        "high": candle_high,
        "low": candle_low,
        "close": candle_close,
        "volume": candle_volume,
        "is_closed": False,  # updated by caller based on timestamp
    }


class DeltaWebSocketClient:
    """Delta Exchange WebSocket client — BTCUSD 1H candlesticks."""

    def __init__(
        self,
        symbol: str = SYMBOL_LOCAL,
        timeframe: str = TIMEFRAME,
        on_candle_closed: Optional[Callable[[dict], None]] = None,
        engine: Any = None,
    ) -> None:
        self._symbol = symbol
        self.symbol = symbol
        self.timeframe = timeframe
        self._symbol_exchange = SYMBOL_EXCHANGE  # always BTCUSD for Delta API
        self._timeframe = timeframe
        self.on_candle_closed = on_candle_closed
        self.engine = engine
        self.ws = None
        self.running = False
        self.last_closed_ts: Optional[int] = None
        self.processed_timestamps: set = set()
        self.reconnect_attempt = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_pong_ts: Optional[float] = None

    async def connect(self) -> None:
        logger.info("Connecting to Delta WebSocket: %s", WS_ENDPOINT)
        self.ws = await websockets.connect(WS_ENDPOINT)
        self.running = True
        self.reconnect_attempt = 0
        self._log_event(EVENT_CONNECT)
        await self._start_heartbeat()

    async def disconnect(self) -> None:
        self.running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._log_event(EVENT_DISCONNECT)
        logger.info("Disconnected from Delta WebSocket")

    async def subscribe(self) -> None:
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": SUBSCRIPTION_CHANNEL, "symbols": [SYMBOL_EXCHANGE]}
                ]
            },
        }
        await self.ws.send(json.dumps(subscribe_msg))
        self._log_event(EVENT_SUBSCRIBE)
        logger.info(
            "Subscribed to %s %s candles (exchange symbol: %s)",
            SYMBOL_EXCHANGE,
            self._timeframe,
            SYMBOL_EXCHANGE,
        )

    async def _start_heartbeat(self) -> None:
        async def _heartbeat_loop() -> None:
            while self.running:
                try:
                    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                    if not self.running:
                        break
                    if self.ws:
                        await self.ws.ping()
                        self._last_pong_ts = time.monotonic()
                        self._log_event(EVENT_HEARTBEAT)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.warning("Heartbeat error: %s", e)
                    self._log_event(EVENT_HEARTBEAT_TIMEOUT)
                    break

        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())

    async def _check_heartbeat_timeout(self) -> None:
        if self._last_pong_ts is not None:
            elapsed = time.monotonic() - self._last_pong_ts
            if elapsed > HEARTBEAT_TIMEOUT_SECONDS:
                logger.warning(
                    "Heartbeat timeout: no PONG received for %.1f seconds", elapsed
                )
                self._log_event(EVENT_HEARTBEAT_TIMEOUT)
                await self.disconnect()
                await self.connect()
                await self.subscribe()

    async def listen(self) -> None:
        assert self.ws is not None, "WebSocket not connected"
        try:
            async for message in self.ws:
                if not self.running:
                    break
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.error("Invalid JSON message: %s", message[:100])
                except Exception as e:
                    logger.error("Error handling message: %s", e)
                    break
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed, initiating reconnect")
        finally:
            if self.running:
                await self._reconnect()

    async def _handle_message(self, data: dict) -> None:
        candle = _parse_candle_from_ws(data)
        if candle is None:
            return

        candle_ts = candle["timestamp"]

        # Deduplication: skip already-processed timestamps
        if candle_ts in self.processed_timestamps:
            logger.debug("Duplicate candle timestamp: %s, skipping", candle_ts)
            self._log_event(EVENT_CANDLE_DUPLICATE, candle_ts=candle_ts)
            return

        # Closed-candle boundary: candle_ts < current_hour_start
        closed = _is_candle_closed(candle_ts)
        candle["is_closed"] = closed

        if closed:
            logger.info("Closed candle: %s", candle_ts)
            self.processed_timestamps.add(candle_ts)
            self.last_closed_ts = candle_ts
            self._log_event(EVENT_CANDLE_CLOSED, candle_ts=candle_ts)
            if self.on_candle_closed:
                self.on_candle_closed(candle)
            if self.engine is not None:
                try:
                    result = self.engine.process_new_candles([candle])
                    self._log_event(EVENT_OB_CREATED, ob_count=result.get("new_obs", 0))
                except Exception as e:
                    logger.error("Engine process_new_candles error: %s", e)
        else:
            logger.debug(
                "Forming candle: %s (excluded from SMC until closed)", candle_ts
            )
            self._log_event(EVENT_CANDLE_FORMING, candle_ts=candle_ts)

    async def _reconnect(self) -> None:
        attempt = self.reconnect_attempt + 1
        self.reconnect_attempt = attempt
        if attempt > MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "Max reconnect attempts (%s) reached, giving up", MAX_RECONNECT_ATTEMPTS
            )
            return
        backoff = min(MIN_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        logger.info(
            "Reconnect attempt %s/%s in %s seconds",
            attempt,
            MAX_RECONNECT_ATTEMPTS,
            backoff,
        )
        self._log_event("RECONNECT_ATTEMPT", attempt=attempt, backoff=backoff)
        await asyncio.sleep(backoff)
        await self._backfill_gaps()
        try:
            await self.connect()
            await self.subscribe()
            await self.listen()
        except Exception as e:
            logger.error("Reconnect failed: %s, will retry", e)
            await self._reconnect()

    async def _backfill_gaps(self) -> None:
        """Use Delta REST to recover any closed candles missed during disconnect."""
        logger.info("Starting REST backfill gap recovery")
        self._log_event(EVENT_BACKFILL_STARTED)

        if self.last_closed_ts is None:
            logger.info("No prior processed candle; skipping backfill")
            self._log_event(EVENT_BACKFILL_COMPLETED, success=True)
            return

        start_ts = self.last_closed_ts + 3600
        end_ts = int(datetime.now(timezone.utc).timestamp())

        try:
            # Use the canonical _fetch_window(start_ts, end_ts) interface
            raw_candles = _fetch_window(start_ts, end_ts)
            logger.info("Fetched %s candles via REST backfill", len(raw_candles))

            # Filter to closed candles, sort chronologically, deduplicate
            raw_candles.sort(key=lambda c: c["time"])
            for c in raw_candles:
                candle_ts = int(c["time"])
                if not _is_candle_closed(candle_ts):
                    continue  # Skip forming candle
                if candle_ts in self.processed_timestamps:
                    continue  # Skip already processed
                self.processed_timestamps.add(candle_ts)
                self.last_closed_ts = candle_ts
                candle_normalized = {
                    "symbol": self._symbol,
                    "timeframe": self._timeframe,
                    "timestamp": candle_ts,
                    "open": Decimal(str(c.get("open", c.get("o", "0")))),
                    "high": Decimal(str(c.get("high", c.get("h", "0")))),
                    "low": Decimal(str(c.get("low", c.get("l", "0")))),
                    "close": Decimal(str(c.get("close", c.get("c", "0")))),
                    "volume": Decimal(str(c.get("volume", c.get("v", "0")))),
                    "is_closed": True,
                }
                if self.on_candle_closed:
                    self.on_candle_closed(candle_normalized)
                if self.engine is not None:
                    try:
                        self.engine.process_new_candles([candle_normalized])
                    except Exception as e:
                        logger.error("Backfill process_new_candles error: %s", e)

            # Detect remaining gaps for logging
            candle_dict = {c["time"]: c for c in raw_candles}
            gaps = detect_gaps(candle_dict)
            if gaps:
                logger.warning("Gaps detected after backfill: %s", gaps)
                self._log_event(EVENT_GAP_DETECTED, gaps=gaps)
            else:
                logger.info("No gaps detected after backfill")

        except Exception as e:
            logger.error("REST backfill error: %s", e)
            self._log_event(EVENT_BACKFILL_ERROR, error=str(e))

        self._log_event(EVENT_BACKFILL_COMPLETED, success=True)

    async def run(self) -> None:
        """Main entry point: connect, subscribe, listen, reconnect on failure."""
        self.running = True
        await self.connect()
        await self.subscribe()
        await self.listen()

    def _log_event(self, event_type: str, **kwargs: Any) -> None:
        parts = [event_type]
        for k, v in kwargs.items():
            parts.append(f"{k}={v}")
        logger.info(" | ".join(parts))


async def main() -> None:
    async def on_candle_closed(candle: dict) -> None:
        logger.info(
            "Closed candle received: timestamp=%s symbol=%s",
            candle["timestamp"],
            candle["symbol"],
        )

    client = DeltaWebSocketClient(on_candle_closed=on_candle_closed, engine=None)
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())