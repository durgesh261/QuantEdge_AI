"""Delta Exchange WebSocket client for BTCUSD 1H candlesticks."""

import json
import logging
import time
from collections.abc import Set as AbstractSet
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Callable, Any

import websockets

from quantedge.market_data.ingestion import detect_gaps, _fetch_window

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("delta_ws")

SYMBOL_LOCAL = "BTCUSD.P"
SYMBOL_EXCHANGE = "BTCUSD"
WS_ENDPOINT = "wss://api.india.delta.exchange/ws"
SUBSCRIPTION_CHANNEL = "candlestick_1h"
SUBSCRIPTION_SYMBOL = "BTCUSD"
TIMEFRAME = "1h"
CLOSED_CANDLE_THRESHOLD_SECONDS = 3600
MAX_RECONNECT_ATTEMPTS = 10
MAX_BACKOFF_SECONDS = 60
MIN_BACKOFF_SECONDS = 2
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 60
EVENT_CONNECT = "CONNECT"
EVENT_DISCONNECT = "DISCONNECT"
EVENT_RECONNECT = "RECONNECT"
EVENT_HEARTBEAT = "HEARTBEAT"
EVENT_HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
EVENT_SUBSCRIBE = "SUBSCRIBE"
EVENT_UNSUBSCRIBE = "UNSUBSCRIBE"
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


def _exchange_symbol(internal_symbol):
    if internal_symbol == "BTCUSD.P":
        return SYMBOL_EXCHANGE
    return internal_symbol


def _parse_candle_from_ws(data):
    if data.get("type") != "candlestick_1h":
        return None
    if "error" in data:
        logger.warning("WS error message received: %s", data.get("error"))
        return None
    payload = data.get("payload", {})
    candle_raw = payload.get("candle") or data.get("candle")
    if not candle_raw:
        return None
    try:
        candle_ts = int(candle_raw["ts"])
        candle_open = Decimal(str(candle_raw["o"]))
        candle_high = Decimal(str(candle_raw["h"]))
        candle_low = Decimal(str(candle_raw["l"]))
        candle_close = Decimal(str(candle_raw["c"]))
        candle_volume = Decimal(str(candle_raw["v"]))
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Failed to parse candle fields: %s", e)
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
        "is_closed": False,
    }


class DeltaWebSocketClient:
    def __init__(self, symbol=SYMBOL_LOCAL, timeframe=TIMEFRAME,
                 on_candle_closed=None, engine=None):
        self._symbol = symbol
        self.symbol = symbol
        self.timeframe = timeframe
        self._symbol_exchange = _exchange_symbol(symbol)
        self._timeframe = timeframe
        self.on_candle_closed = on_candle_closed
        self.engine = engine
        self.ws = None
        self.running = False
        self.last_closed_ts = None
        self.processed_timestamps = set()
        self.reconnect_attempt = 0
        self._heartbeat_task = None
        self._last_pong_ts = None

    async def connect(self):
        logger.info("Connecting to Delta WebSocket: %s", WS_ENDPOINT)
        self.ws = await websockets.connect(WS_ENDPOINT)
        self.running = True
        self.reconnect_attempt = 0
        await self._start_heartbeat()

    async def disconnect(self):
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
        logger.info("Disconnected from Delta WebSocket")

    async def subscribe(self):
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": SUBSCRIPTION_CHANNEL, "symbols": [SYMBOL_EXCHANGE]}
                ]
            }
        }
        await self.ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to %s %s candles (exchange symbol: %s)",
                    SYMBOL_EXCHANGE, self._timeframe, SYMBOL_EXCHANGE)

    async def _start_heartbeat(self):
        async def _heartbeat_loop():
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

    async def _check_heartbeat_timeout(self):
        if self._last_pong_ts is not None:
            elapsed = time.monotonic() - self._last_pong_ts
            if elapsed > HEARTBEAT_TIMEOUT_SECONDS:
                logger.warning("Heartbeat timeout: no PONG received for %.1f seconds", elapsed)
                self._log_event(EVENT_HEARTBEAT_TIMEOUT)
                await self.disconnect()
                await self.connect()
                await self.subscribe()

    async def listen(self):
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

    async def _handle_message(self, data):
        candle = _parse_candle_from_ws(data)
        if candle is None:
            return
        candle_ts = candle["timestamp"]
        if candle_ts in self.processed_timestamps:
            logger.debug("Duplicate candle timestamp: %s, skipping", candle_ts)
            self._log_event(EVENT_CANDLE_DUPLICATE, candle_ts=candle_ts)
            return
        now_ts = int(datetime.now(timezone.utc).timestamp())
        current_hour_start = now_ts - (now_ts % 3600)
        is_closed = candle["timestamp"] < current_hour_start - 3600
        self.last_closed_ts = candle["timestamp"] if is_closed else self.last_closed_ts
        if is_closed:
            logger.info("Closed candle: %s", candle_ts)
            self.processed_timestamps.add(candle_ts)
            self._log_event(EVENT_CANDLE_CLOSED, candle_ts=candle_ts)
            if self.on_candle_closed:
                self.on_candle_closed(candle)
            if self.engine is not None:
                try:
                    result = self.engine.process_new_candles([candle])
                    self._log_event(EVENT_OB_CREATED, ob_count=result.get("new_obs", 0))
                except Exception as e:
                    logger.error("Engine process_new_candles error: %s", e)
                    self._log_event(EVENT_OB_INVALIDATED, candle_ts=candle_ts)
        else:
            logger.info("Formation candle: %s (still updating, excluded from SMC)", candle_ts)
            self._log_event(EVENT_CANDLE_FORMING, candle_ts=candle_ts)

    async def _reconnect(self):
        attempt = self.reconnect_attempt + 1
        self.reconnect_attempt = attempt
        if attempt > MAX_RECONNECT_ATTEMPTS:
            logger.error("Max reconnect attempts (%s) reached, giving up", MAX_RECONNECT_ATTEMPTS)
            self._log_event(EVENT_BACKFILL_COMPLETED, success=False)
            return
        backoff = min(MIN_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        logger.info("Reconnect attempt %s/%s in %s seconds", attempt, MAX_RECONNECT_ATTEMPTS, backoff)
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

    async def _backfill_gaps(self):
        logger.info("Starting REST backfill gap recovery")
        self._log_event(EVENT_BACKFILL_STARTED)
        if self.last_closed_ts is not None:
            start_ts = self.last_closed_ts + 3600
        else:
            start_ts = None
        end_ts = int(datetime.now(timezone.utc).timestamp()) - 3600
        try:
            candles = _fetch_window(self._symbol_exchange, self._timeframe, start_ts, end_ts)
            logger.info("Fetched %s candles via REST backfill", len(candles))
            for c in candles:
                candle_ts = int(c["timestamp"])
                if candle_ts not in self.processed_timestamps:
                    self.processed_timestamps.add(candle_ts)
                    candle_normalized = {
                        "symbol": self._symbol,
                        "timeframe": self._timeframe,
                        "timestamp": candle_ts,
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"],
                        "is_closed": True,
                    }
                    if self.on_candle_closed:
                        self.on_candle_closed(candle_normalized)
                    if self.engine is not None:
                        try:
                            self.engine.process_new_candles([candle_normalized])
                        except Exception as e:
                            logger.error("Backfill process_new_candles error: %s", e)
            if self.last_closed_ts is not None:
                gaps = detect_gaps(
                    {"symbol": self._symbol_exchange, "timeframe": self._timeframe},
                    start_ts=self.last_closed_ts,
                    end_ts=end_ts,
                )
                if gaps:
                    logger.warning("Gaps detected after backfill: %s", gaps)
                    self._log_event(EVENT_GAP_DETECTED, gaps=gaps)
                else:
                    logger.info("No gaps detected after backfill")
            else:
                logger.info("No prior state; backfill completed full window")
        except Exception as e:
            logger.error("REST backfill error: %s", e)
            self._log_event(EVENT_BACKFILL_ERROR, error=str(e))
        self._log_event(EVENT_BACKFILL_COMPLETED, success=True)

    def _log_event(self, event_type, **kwargs):
        parts = [event_type]
        for k, v in kwargs.items():
            parts.append("{}={}".format(k, v))
        logger.info(" | ".join(parts))


async def main():
    async def on_candle_closed(candle):
        logger.info("Closed candle received: timestamp=%s symbol=%s",
                    candle["timestamp"], candle["symbol"])

    engine = None
    client = DeltaWebSocketClient(on_candle_closed=on_candle_closed, engine=engine)
    await client.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())