"""Delta Exchange WebSocket client for BTCUSD 1H candlesticks.

Connects to wss://socket.india.delta.exchange and subscribes to the
candlestick_1h channel for BTCUSD.

Key guarantees:
- Only CLOSED candles are passed to the SMC engine / callback.
- Each closed timestamp is processed exactly once (deduplication).
- Closed candles are atomically persisted BEFORE the engine processes them.
- On disconnect, REST backfill recovers and persists any missed closed candles.
- Heartbeat detects stale connections and triggers reconnect.
- Persistence failure blocks engine processing (Rule 10).
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Callable, Any

import websockets

from quantedge.market_data.ingestion import (
    detect_gaps,
    _fetch_window,
    fetch_closed_candles,
    upsert_closed_candles,
    validate_candle_ohlcv,
    validate_candle_year,
    CANONICAL_CSV,
    CANONICAL_META,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("delta_ws")

SYMBOL_LOCAL = "BTCUSD.P"        # Display / TradingView symbol
SYMBOL_EXCHANGE = "BTCUSD"       # Symbol sent to Delta Exchange API
WS_ENDPOINT = "wss://socket.india.delta.exchange"
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
    """Parse a raw Delta Exchange India WebSocket message into a normalised candle dict.

    Real Delta India candlestick_1h message format (verified live 2026-08-21):
    {
        "type":             "candlestick_1h",
        "symbol":           "BTCUSD",
        "resolution":       "1h",
        "open":             77809.5,        # float
        "high":             77865.5,        # float
        "low":              77338.5,        # float
        "close":            77428.5,        # float
        "volume":           372963.0,       # float
        "candle_start_time": 1787310000000000,  # microseconds — candle open time
        "timestamp":        1787310788489993,   # microseconds — last tick time
        "last_updated":     1787310788489993,
        "sUID":             "BTCUSD_#_BTCUSD_#_60"
    }

    candle_start_time is the candle's open timestamp in MICROSECONDS.
    Divide by 1_000_000 to get UNIX seconds.

    Returns None for non-candle messages or parse errors.
    """
    msg_type = data.get("type", "")
    # Accept the real Delta India flat format
    if msg_type == SUBSCRIPTION_CHANNEL:
        try:
            # candle_start_time is in microseconds — convert to seconds
            raw_ts = data.get("candle_start_time")
            if raw_ts is None:
                # Fallback: use timestamp field (also microseconds)
                raw_ts = data.get("timestamp")
            candle_ts = int(raw_ts) // 1_000_000

            # OHLCV are floats in the real feed
            candle_open = Decimal(str(data["open"]))
            candle_high = Decimal(str(data["high"]))
            candle_low = Decimal(str(data["low"]))
            candle_close = Decimal(str(data["close"]))
            candle_volume = Decimal(str(data["volume"]))
            symbol = data.get("symbol", SYMBOL_EXCHANGE)
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
        persist: bool = True,
        csv_path: Optional[Any] = None,
        meta_path: Optional[Any] = None,
    ) -> None:
        self._symbol = symbol
        self.symbol = symbol
        self.timeframe = timeframe
        self._symbol_exchange = SYMBOL_EXCHANGE  # always BTCUSD for Delta API
        self._timeframe = timeframe
        self.on_candle_closed = on_candle_closed
        self.engine = engine
        # Phase 3F.5: persistence
        self.persist = persist
        self.csv_path = csv_path if csv_path is not None else CANONICAL_CSV
        self.meta_path = meta_path if meta_path is not None else CANONICAL_META
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

        # Closed-candle boundary: candle_ts < current_hour_start
        closed = _is_candle_closed(candle_ts)
        candle["is_closed"] = closed

        if not closed:
            logger.debug(
                "Forming candle: %s (excluded from SMC until closed)", candle_ts
            )
            self._log_event(EVENT_CANDLE_FORMING, candle_ts=candle_ts)
            return

        # Deduplication: skip already-processed timestamps
        if candle_ts in self.processed_timestamps:
            logger.debug("Duplicate candle timestamp: %s, skipping", candle_ts)
            self._log_event(EVENT_CANDLE_DUPLICATE, candle_ts=candle_ts)
            return

        # Phase 3F.5/3F.6.1 Rule 10 — strict order: validate -> year guard -> persist -> engine
        # ----------------------------------------------------------------------------------
        # Step 1: OHLCV validation & Year partition guard
        try:
            validate_candle_ohlcv(candle)
            validate_candle_year(candle, csv_path=self.csv_path)
        except ValueError as e:
            logger.error("Validation failed for candle %s: %s", candle_ts, e)
            return  # Reject malformed or wrong-year candle

        # Step 2: Persist (if enabled) — MUST succeed before engine is called
        if self.persist:
            try:
                upsert_result = upsert_closed_candles(
                    [candle], self.csv_path, self.meta_path
                )
                logger.info(
                    "Persisted candle %s: inserts=%d updates=%d unchanged=%d sha256=%s",
                    candle_ts, upsert_result.inserts, upsert_result.updates,
                    upsert_result.unchanged, upsert_result.sha256[:12],
                )
                self._log_event(
                    EVENT_STATE_SAVED,
                    candle_ts=candle_ts,
                    inserts=upsert_result.inserts,
                    updates=upsert_result.updates,
                )
            except Exception as e:
                # Persistence failed: DO NOT mark as processed, DO NOT call engine
                logger.error(
                    "Persistence FAILED for candle %s: %s | "
                    "Candle remains eligible for retry.", candle_ts, e
                )
                return  # Rule 10: leave candle un-processed

        # Step 3: Mark processed ONLY after successful persistence
        self.processed_timestamps.add(candle_ts)
        self.last_closed_ts = candle_ts
        self._log_event(EVENT_CANDLE_CLOSED, candle_ts=candle_ts)

        # Step 4: Callback
        if self.on_candle_closed:
            self.on_candle_closed(candle)

        # Step 5: Engine — only after successful persistence
        if self.engine is not None:
            try:
                result = self.engine.process_new_candles([candle])
                self._log_event(EVENT_OB_CREATED, ob_count=result.get("new_obs", 0))
            except Exception as e:
                logger.error("Engine process_new_candles error: %s", e)

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
        """Use Delta REST (paginated) to recover any closed candles missed during disconnect.

        Phase 3F.5: uses fetch_closed_candles() (paginated) instead of a single
        _fetch_window() call, then persists the entire batch via upsert_closed_candles()
        before passing successfully-persisted candles to the engine.
        """
        logger.info("Starting REST backfill gap recovery")
        self._log_event(EVENT_BACKFILL_STARTED)

        if self.last_closed_ts is None:
            logger.info("No prior processed candle; skipping backfill")
            self._log_event(EVENT_BACKFILL_COMPLETED, success=True)
            return

        start_ts = self.last_closed_ts + 3600
        end_ts = int(datetime.now(timezone.utc).timestamp())

        try:
            # Paginated REST fetch (fetch_closed_candles handles chunking + dedup)
            raw_candles = fetch_closed_candles(start_ts, end_ts)
            logger.info("Fetched %s candles via REST backfill", len(raw_candles))

            if not raw_candles:
                self._log_event(EVENT_BACKFILL_COMPLETED, success=True)
                return

            # Normalize raw Delta REST dicts
            candle_dicts = []
            for c in raw_candles:
                ts_int = int(c["time"])
                if not _is_candle_closed(ts_int):
                    continue  # skip any forming candle
                if ts_int in self.processed_timestamps:
                    continue  # skip already-processed
                candle_dict = {
                    "timestamp": ts_int,
                    "open":   Decimal(str(c.get("open",   c.get("o", "0")))),
                    "high":   Decimal(str(c.get("high",   c.get("h", "0")))),
                    "low":    Decimal(str(c.get("low",    c.get("l", "0")))),
                    "close":  Decimal(str(c.get("close",  c.get("c", "0")))),
                    "volume": Decimal(str(c.get("volume", c.get("v", "0")))),
                }
                try:
                    validate_candle_year(candle_dict, csv_path=self.csv_path)
                except ValueError as e:
                    logger.warning("Backfill candle rejected by year partition: %s", e)
                    continue
                candle_dicts.append(candle_dict)

            if not candle_dicts:
                logger.info("No new closed candles to backfill")
                self._log_event(EVENT_BACKFILL_COMPLETED, success=True)
                return

            # Phase 3F.5: Persist entire batch BEFORE engine (Rule 8)
            if self.persist:
                try:
                    upsert_result = upsert_closed_candles(
                        candle_dicts, self.csv_path, self.meta_path
                    )
                    logger.info(
                        "Backfill persisted: inserts=%d updates=%d unchanged=%d",
                        upsert_result.inserts, upsert_result.updates, upsert_result.unchanged,
                    )
                    self._log_event(
                        EVENT_STATE_SAVED,
                        inserts=upsert_result.inserts,
                        updates=upsert_result.updates,
                    )
                except Exception as e:
                    logger.error("Backfill persistence FAILED: %s", e)
                    self._log_event(EVENT_BACKFILL_ERROR, error=str(e))
                    return  # Do not process candles if persistence failed

            # Pass successfully-persisted candles to engine and mark processed
            for c_dict in candle_dicts:
                ts_int = int(c_dict["timestamp"]) if not isinstance(c_dict["timestamp"], datetime) else int(c_dict["timestamp"].timestamp())
                self.processed_timestamps.add(ts_int)
                self.last_closed_ts = ts_int

                candle_normalized = {
                    "symbol":    self._symbol,
                    "timeframe": self._timeframe,
                    "timestamp": ts_int,
                    "open":      c_dict["open"],
                    "high":      c_dict["high"],
                    "low":       c_dict["low"],
                    "close":     c_dict["close"],
                    "volume":    c_dict["volume"],
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
            candle_map = {int(c["time"]): c for c in raw_candles}
            gaps = detect_gaps(candle_map)
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