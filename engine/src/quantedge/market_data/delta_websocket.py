"""Delta Exchange India WebSocket client for BTCUSD 1H candles.

This module provides a live WebSocket connection to Delta Exchange India
for real-time BTCUSD 1H candle data. It integrates with the
IncrementalSMCEngine, ensuring the closed-candle contract is respected:
only fully closed candles are passed through for SMC/OB processing.

Forming candles are detected and excluded from engine state updates,
though they may be available for display purposes.
"""

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Callable, Any

import websockets

from quantedge.market_data.ingestion import detect_gaps, _fetch_window

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("delta_ws")

SYMBOL_LOCAL = "BTCUSD.P"
TIMEFRAME = "1h"

CLOSED_CANDLE_THRESHOLD_SECONDS = 3600  # 1 hour old = closed

# Reconnect configuration
MAX_RECONNECT_ATTEMPTS = 10
MAX_BACKOFF_SECONDS = 60
MIN_BACKOFF_SECONDS = 2


class DeltaWebSocketClient:
    """WebSocket client for Delta Exchange India BTCUSD 1H candles.

    Responsibilities:
    - Maintain a live WebSocket connection to Delta Exchange India
    - Parse candle messages, distinguishing forming vs. closed candles
    - Enforce the closed-candle contract: only fully closed candles
      enter the IncrementalSMCEngine
    - Implement bounded exponential backoff reconnect
    - Track last-processed candle timestamp for deduplication
    - Perform REST backfill on reconnect to recover any gaps
    - Integrate with IncrementalSMCEngine via on_candle_closed callback
    """

    def __init__(
        self,
        symbol: str = SYMBOL_LOCAL,
        timeframe: str = TIMEFRAME,
        on_candle_closed: Optional[Callable[[dict], None]] = None,
        engine: Optional["IncrementalSMCEngineWrapper"] = None,
    ):
        """Initialize the WebSocket client.

        Args:
            symbol: Trading symbol (e.g. BTCUSD.P).
            timeframe: Candle timeframe (e.g. 1h).
            on_candle_closed: Callback when a closed candle is received.
                Receives a dict with candle data. The callback should process
                the candle through IncrementalSMCEngine.process_new_candles().
                Must not modify SMC state for forming candles.
            engine: Optional IncrementalSMCEngineWrapper instance for
                direct state integration. If not provided, the callback
                must engine-process candles externally.
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.on_candle_closed = on_candle_closed
        self.engine = engine
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.last_closed_ts: Optional[int] = None
        self.processed_timestamps: Set[int] = set()
        self.reconnect_attempt = 0

    async def connect(self) -> None:
        """Connect to Delta Exchange India WebSocket.

        !!! PLACEHOLDER: Verify actual WebSocket endpoint from Delta Exchange India
        documentation. The following URL is a placeholder.
        """
        # !!! PLACEHOLDER: Verify actual WebSocket endpoint !!!
        # The following URL must be verified from Delta Exchange India documentation.
        # Current placeholder: wss://api.india.delta.exchange/ws/br
        ws_url = "wss://api.india.delta.exchange/ws/br"
        logger.info("Connecting to Delta WebSocket: %s", ws_url)
        self.ws = await websockets.connect(ws_url)
        self.running = True
        self.reconnect_attempt = 0
        logger.info("WebSocket connected")

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        logger.info("Disconnected from Delta WebSocket")

    async def subscribe(self) -> None:
        """Subscribe to the BTCUSD 1H candle channel.

        !!! PLACEHOLDER: Verify actual subscription format from Delta Exchange India
        documentation.
        """
        # !!! PLACEHOLDER: Verify actual subscription format !!!
        # The following subscription message format must be verified.
        # Current placeholder: {"action": "subscribe", "channel": "candle_1h_BTCUSD"}
        subscribe_msg = {"action": "subscribe", "channel": f"candle_1h_{self.symbol}"}
        await self.ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to %s 1H candles", self.symbol)

    async def listen(self) -> None:
        """Listen for incoming WebSocket messages.

        Runs until self.running is False. Handles message parsing,
        forming/closed candle distinction, deduplication, and reconnect
        initiation on connection loss.
        """
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
                    break  # Break to initiate reconnect
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed, initiating reconnect")
        finally:
            if self.running:
                await self._reconnect()

    async def _handle_message(self, data: dict) -> None:
        """Process an incoming WebSocket message.

        !!! PLACEHOLDER: Verify actual message schema from Delta Exchange India
        documentation.

        Distinguishes between forming and closed candles per the
        closed-candle contract: only fully closed candles are passed
        to the engine callback. Deduplication ensures exactly-one SMC
        state transition per candle.
        """
        # !!! PLACEHOLDER: Verify actual message schema !!!
        is_formation = data.get("formation", False)
        candle_data = data.get("candle", {})

        # Validate required OHLCV fields
        required_fields = ["open", "high", "low", "close", "volume", "time"]
        if not all(k in candle_data for k in required_fields):
            logger.warning("Missing required fields in candle data")
            return

        # Parse candle timestamp
        try:
            candle_ts = int(candle_data["time"])
        except (ValueError, TypeError) as e:
            logger.error("Invalid candle timestamp: %s", candle_data.get("time"))
            return

        # Deduplication: skip if we've already processed this timestamp
        if candle_ts in self.processed_timestamps:
            logger.debug("Duplicate candle timestamp: %s, skipping", candle_ts)
            self._log_event("CANDLE_DUPLICATE", candle_ts=candle_ts)
            return

        # Determine if candle is closed per the closed-candle contract:
        # A candle is closed if it is at least 1 hour old (i.e. not the
        # currently forming hour).
        now_ts = int(datetime.now(timezone.utc).timestamp())
        current_hour_start = now_ts - (now_ts % 3600)
        is_closed = candle_ts < current_hour_start - 3600

        self.last_closed_ts = candle_ts if is_closed else self.last_closed_ts

        if is_formation:
            logger.info("Formation candle: %s (still updating, excluded from SMC)", candle_ts)
            # Forming candles MUST NOT enter the IncrementalSMCEngine.
            # Simply skip - do not call on_candle_closed.
            self._log_event("CANDLE_FORMING", candle_ts=candle_ts)
            return

        # Candle is closed and not a duplicate - process it
        logger.info("Closed candle: %s", candle_ts)
        self.processed_timestamps.add(candle_ts)

        candle = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": candle_ts,
            "open": Decimal(str(candle_data["open"])),
            "high": Decimal(str(candle_data["high"])),
            "low": Decimal(str(candle_data["low"])),
            "close": Decimal(str(candle_data["close"])),
            "volume": Decimal(str(candle_data["volume"])),
            "is_closed": True,
        }

        # Log and callback
        self._log_event("CANDLE_CLOSED", candle_ts=candle_ts)
        if self.on_candle_closed:
            self.on_candle_closed(candle)

        # If engine wrapper is provided, process through engine
        if self.engine is not None:
            try:
                result = self.engine.process_new_candles([candle])
                self._log_event("OB_CREATED", ob_count=result.get("new_obs", 0))
            except Exception as e:
                logger.error("Engine process_new_candles error: %s", e)
                self._log_event("OB_INVALIDATED", candle_ts=candle_ts)

    async def _reconnect(self) -> None:
        """Bounded exponential backoff reconnect.

        After a connection loss, wait with exponential backoff up to
        MAX_BACKOFF_SECONDS, then attempt to reconnect. On each attempt,
        try REST backfill to recover any gaps before resuming WebSocket.
        """
        attempt = self.reconnect_attempt + 1
        self.reconnect_attempt = attempt

        if attempt > MAX_RECONNECT_ATTEMPTS:
            logger.error("Max reconnect attempts (%s) reached, giving up", MAX_RECONNECT_ATTEMPTS)
            self._log_event("BACKFILL_COMPLETED", success=False)
            return

        backoff = min(MIN_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        logger.info("Reconnect attempt %s/%s in %s seconds", attempt, MAX_RECONNECT_ATTEMPTS, backoff)
        self._log_event("RECONNECT_ATTEMPT", attempt=attempt, backoff=backoff)

        await asyncio.sleep(backoff)

        # Before resuming WebSocket, try REST backfill to recover gaps
        await self._backfill_gaps()

        # Attempt to reconnect
        try:
            await self.connect()
            await self.subscribe()
            await self.listen()
        except Exception as e:
            logger.error("Reconnect failed: %s, will retry", e)
            # Recurse _reconnect on failure (capped by MAX_RECONNECT_ATTEMPTS)
            await self._reconnect()

    async def _backfill_gaps(self) -> None:
        """REST backfill to recover any gaps in the candle history.

        Uses the ingestion module's _fetch_window to download any missing
        closed candles from the Delta Exchange India REST API, then processes
        them through the engine to ensure state consistency.
        """
        logger.info("Starting REST backfill gap recovery")
        self._log_event("BACKFILL_STARTED")

        # Determine the range of candles to fetch
        # We need candles from the last known processed timestamp up to now
        if self.last_closed_ts is not None:
            start_ts = self.last_closed_ts + 3600  # Next candle after last closed
        else:
            # No prior state; fetch a reasonable window
            start_ts = None

        end_ts = int(datetime.now(timezone.utc).timestamp()) - 3600  # Last completed hour

        try:
            # Fetch missing candles via REST
            candles = _fetch_window(self.symbol, self.timeframe, start_ts, end_ts)
            logger.info("Fetched %s candles via REST backfill", len(candles))

            # Process each candle through the engine (deduplication already handled
            # by processed_timestamps, but we process anyway for state consistency)
            for candle in candles:
                candle_ts = int(candle["timestamp"])
                if candle_ts not in self.processed_timestamps:
                    self.processed_timestamps.add(candle_ts)
                    if self.on_candle_closed:
                        self.on_candle_closed(candle)
                    if self.engine is not None:
                        try:
                            self.engine.process_new_candles([candle])
                        except Exception as e:
                            logger.error("Backfill process_new_candles error: %s", e)

            # Detect and log any gaps that remain
            if self.last_closed_ts is not None:
                gaps = detect_gaps(
                    {"symbol": self.symbol, "timeframe": self.timeframe},
                    start_ts=self.last_closed_ts,
                    end_ts=end_ts,
                )
                if gaps:
                    logger.warning("Gaps detected after backfill: %s", gaps)
                    self._log_event("GAP_DETECTED", gaps=gaps)
                else:
                    logger.info("No gaps detected after backfill")
            else:
                logger.info("No prior state; backfill completed full window")

        except Exception as e:
            logger.error("REST backfill error: %s", e)
            self._log_event("BACKFILL_ERROR", error=str(e))

        self._log_event("BACKFILL_COMPLETED", success=True)

    def _log_event(self, event_type: str, **kwargs) -> None:
        """Log a structured event. Can be extended to write to observability stack."""
        parts = [event_type]
        for k, v in kwargs.items():
            parts.append(f"{k}={v}")
        logger.info(" | ".join(parts))

    async def run(self) -> None:
        """Run the WebSocket client lifecycle.

        The main loop:
        1. Connect
        2. Subscribe
        3. Listen (handles messages, reconnect on loss)
        """
        try:
            await self.connect()
            await self.subscribe()
            await self.listen()
        except Exception as e:
            logger.error("WebSocket run error: %s", e)
        finally:
            await self.disconnect()


async def main() -> None:
    """Example entry point for the Delta WebSocket client."""

    async def on_candle_closed(candle: dict) -> None:
        """Callback when a closed candle is received from WebSocket."""
        logger.info(
            "Closed candle received: timestamp=%s symbol=%s",
            candle["timestamp"],
            candle["symbol"],
        )

    # Example: create engine wrapper if engine available
    engine = None  # TODO: Initialize IncrementalSMCEngine if desired
    client = DeltaWebSocketClient(on_candle_closed=on_candle_closed, engine=engine)
    await client.run()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())