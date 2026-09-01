"""Delta Exchange WebSocket client for 1H candlesticks.

Connects to wss://socket.india.delta.exchange and subscribes to the
candlestick_1h channel for every configured pair.

Key guarantees:
- Only CLOSED candles are passed to the SMC engine / callback.
- Each closed timestamp is processed exactly once PER SYMBOL (deduplication).
- Closed candles are atomically persisted BEFORE the engine processes them.
- On disconnect, REST backfill recovers and persists any missed closed candles.
- Heartbeat detects stale connections and triggers reconnect.
- Persistence failure blocks engine processing (Rule 10).
- Symbol identity is exact: every subscribed and every received symbol is
  resolved through the provenance-verified instrument registry. There is no
  case folding, no fuzzy matching, and no fallback of an unknown symbol onto
  BTCUSD -- an unrecognised symbol is refused.

Single-symbol construction (`DeltaWebSocketClient()`) behaves exactly as it
always did: BTCUSD.P, the canonical 2026 CSV, one flat `processed_timestamps`
set. Passing `symbols=[...]` adds pairs, each with its own watermark, its own
processed-timestamp set and its own persistence files, so no pair can mask,
overwrite or contaminate another.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Callable, Any, Dict, Sequence, Set, Tuple

import websockets

from quantedge.instruments.registry import delta_india_registry
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

# The one symbol rewriting this module performs: the feed labels the local
# product `BTCUSD.P` and the exchange product `BTCUSD`. Applied by suffix, and
# always confirmed against the registry -- never used to invent a product.
LOCAL_SYMBOL_SUFFIX = ".P"


class UnsupportedFeedSymbolError(ValueError):
    """A symbol is not a registered Delta India instrument."""


def exchange_symbol(symbol: str) -> str:
    """The exchange symbol for `symbol`, which may carry the local suffix.

    Exact: the only transformation is removing a trailing `.P`. The result must
    be in the provenance-verified registry or this refuses (safety rules #8,
    #15, #16). No case folding, no stripping, no substitution.
    """
    if not isinstance(symbol, str) or not symbol:
        raise UnsupportedFeedSymbolError(
            f"{symbol!r} is not a usable feed symbol")
    exchange = (symbol[:-len(LOCAL_SYMBOL_SUFFIX)]
                if symbol.endswith(LOCAL_SYMBOL_SUFFIX) else symbol)
    try:
        delta_india_registry().get(exchange)
    except Exception as exc:                                   # noqa: BLE001
        raise UnsupportedFeedSymbolError(
            f"{symbol!r} resolves to {exchange!r}, which is not a registered "
            f"Delta India instrument; refusing to subscribe to or accept "
            f"candles for an unknown product") from exc
    return exchange


def local_symbol(symbol: str) -> str:
    """The local (`.P`) label for a registry-verified exchange symbol."""
    return exchange_symbol(symbol) + LOCAL_SYMBOL_SUFFIX


def canonical_paths(symbol: str,
                    csv_path: Optional[Any] = None,
                    meta_path: Optional[Any] = None) -> Tuple[Path, Path]:
    """The persistence files for one symbol.

    BTCUSD keeps the canonical 2026 partition byte-for-byte -- it is the
    dataset the frozen baseline was measured on. Every other pair gets the
    sibling partition its own symbol names, following the same
    `<symbol>/<timeframe>/<year>` layout already on disk. When an explicit path
    is supplied (tests, alternative deployments), non-BTCUSD pairs are given a
    suffixed sibling of that path so one file can never hold two products.
    """
    exchange = exchange_symbol(symbol)
    if csv_path is None and meta_path is None:
        if exchange == SYMBOL_EXCHANGE:
            return Path(CANONICAL_CSV), Path(CANONICAL_META)
        root = Path(CANONICAL_CSV).parent.parent.parent
        base = root / exchange / TIMEFRAME
        return base / "2026.csv", base / "2026_metadata.json"
    csv_given = Path(CANONICAL_CSV) if csv_path is None else Path(csv_path)
    meta_given = Path(CANONICAL_META) if meta_path is None else Path(meta_path)
    if exchange == SYMBOL_EXCHANGE:
        return csv_given, meta_given
    return (csv_given.with_name(f"{csv_given.stem}_{exchange}{csv_given.suffix}"),
            meta_given.with_name(
                f"{meta_given.stem}_{exchange}{meta_given.suffix}"))


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


def _parse_candle_from_ws(
    data: dict,
    accepted: Optional[Set[str]] = None,
) -> Optional[dict]:
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

    The candle is labelled with the symbol the FRAME carries, resolved through
    the instrument registry. A frame without a symbol, with an unregistered
    symbol, or with a symbol outside `accepted` (when given) is refused: the
    old behaviour of labelling every frame BTCUSD.P could have fed one pair's
    prices into another pair's strategy.

    Returns None for non-candle messages, parse errors and refused symbols.
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
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to parse Delta WS candle fields: %s | msg=%s", e, data)
            return None
        try:
            frame_exchange = exchange_symbol(data.get("symbol"))
        except UnsupportedFeedSymbolError as e:
            logger.warning(
                "Refusing candle frame with unusable symbol: %s | msg=%s", e, data)
            return None
        if accepted is not None and frame_exchange not in accepted:
            logger.warning(
                "Refusing candle for %s: not a subscribed symbol (%s)",
                frame_exchange, sorted(accepted))
            return None
    elif msg_type in ("subscriptions", "heartbeat", "ping", "pong", "info"):
        # Control / subscription-ack messages — not candles
        return None
    else:
        # Unknown message type — ignore silently
        return None

    return {
        "symbol": frame_exchange + LOCAL_SYMBOL_SUFFIX,
        "exchange_symbol": frame_exchange,
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
    """Delta Exchange WebSocket client — 1H candlesticks, one or many pairs."""

    def __init__(
        self,
        symbol: str = SYMBOL_LOCAL,
        timeframe: str = TIMEFRAME,
        on_candle_closed: Optional[Callable[[dict], None]] = None,
        engine: Any = None,
        persist: bool = True,
        csv_path: Optional[Any] = None,
        meta_path: Optional[Any] = None,
        symbols: Optional[Sequence[str]] = None,
    ) -> None:
        self._symbol = symbol
        self.symbol = symbol
        self.timeframe = timeframe
        self._timeframe = timeframe
        self.on_candle_closed = on_candle_closed
        self.engine = engine
        # Phase 3F.5: persistence
        self.persist = persist
        self.csv_path = csv_path if csv_path is not None else CANONICAL_CSV
        self.meta_path = meta_path if meta_path is not None else CANONICAL_META

        # §M7: every configured pair, registry-verified, order preserved. The
        # first is the primary: it keeps the legacy attributes so a
        # single-symbol client is byte-for-byte the client that existed before.
        requested = (symbol,) if symbols is None else tuple(symbols)
        if not requested:
            raise UnsupportedFeedSymbolError(
                "at least one symbol must be configured; an empty feed "
                "subscription would silently starve the strategy")
        self._exchange_symbols: Tuple[str, ...] = tuple(
            dict.fromkeys(exchange_symbol(s) for s in requested))
        self.symbols: Tuple[str, ...] = tuple(
            s + LOCAL_SYMBOL_SUFFIX for s in self._exchange_symbols)
        self._accepted: Set[str] = set(self._exchange_symbols)
        self._primary = self._exchange_symbols[0]
        self._symbol_exchange = self._primary

        # Per-symbol watermarks and persistence targets. `processed_timestamps`
        # and `last_closed_ts` remain the primary pair's own state, exposed as
        # properties so both mutation (`.add(ts)`) and rebinding (`= {...}`)
        # keep working exactly as before, while another pair can never mark a
        # timestamp processed on the primary's behalf.
        self._processed: Dict[str, Set[int]] = {}
        self._last_closed: Dict[str, Optional[int]] = {}
        self._paths: Dict[str, Tuple[Path, Path]] = {
            self._primary: (Path(self.csv_path), Path(self.meta_path))}
        for exchange in self._exchange_symbols:
            self._processed[exchange] = set()
            self._last_closed[exchange] = None
            if exchange != self._primary:
                self._paths[exchange] = canonical_paths(
                    exchange, csv_path=csv_path, meta_path=meta_path)

        self.ws = None
        self.running = False
        self.reconnect_attempt = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_pong_ts: Optional[float] = None

    # ------------------------------------------------------- per-symbol state
    @property
    def processed_timestamps(self) -> Set[int]:
        """The primary pair's processed timestamps (legacy attribute)."""
        return self._processed[self._primary]

    @processed_timestamps.setter
    def processed_timestamps(self, value: Any) -> None:
        self._processed[self._primary] = set(value)

    @property
    def last_closed_ts(self) -> Optional[int]:
        """The primary pair's watermark (legacy attribute, unchanged)."""
        return self._last_closed[self._primary]

    @last_closed_ts.setter
    def last_closed_ts(self, value: Optional[int]) -> None:
        self._last_closed[self._primary] = value

    def processed_for(self, symbol: str) -> Set[int]:
        """The processed-timestamp set owned by `symbol`."""
        return self._processed[exchange_symbol(symbol)]

    def last_closed_for(self, symbol: str) -> Optional[int]:
        """The closed-candle watermark owned by `symbol`."""
        return self._last_closed[exchange_symbol(symbol)]

    def paths_for(self, symbol: str) -> Tuple[Path, Path]:
        """The (csv, meta) persistence targets owned by `symbol`."""
        return self._paths[exchange_symbol(symbol)]

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
        """Subscribe to `candlestick_1h` for every configured pair.

        One channel entry carrying every exchange symbol, exactly as the
        registry spells them. Previously only BTCUSD was ever sent, so any
        other configured pair received no candles at all and its strategy state
        simply never advanced.
        """
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": SUBSCRIPTION_CHANNEL,
                     "symbols": list(self._exchange_symbols)}
                ]
            },
        }
        await self.ws.send(json.dumps(subscribe_msg))
        self._log_event(EVENT_SUBSCRIBE)
        logger.info(
            "Subscribed to %s %s candles (exchange symbols: %s)",
            SUBSCRIPTION_CHANNEL,
            self._timeframe,
            ",".join(self._exchange_symbols),
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
        candle = _parse_candle_from_ws(data, accepted=self._accepted)
        if candle is None:
            return

        candle_ts = candle["timestamp"]
        # Every decision below is scoped to the pair that sent the frame.
        exchange = candle["exchange_symbol"]
        processed = self._processed[exchange]
        csv_path, meta_path = self._paths[exchange]

        # Closed-candle boundary: candle_ts < current_hour_start
        closed = _is_candle_closed(candle_ts)
        candle["is_closed"] = closed

        if not closed:
            logger.debug(
                "Forming candle: %s %s (excluded from SMC until closed)",
                exchange, candle_ts
            )
            self._log_event(EVENT_CANDLE_FORMING, symbol=exchange,
                            candle_ts=candle_ts)
            return

        # Deduplication: skip already-processed timestamps for THIS pair
        if candle_ts in processed:
            logger.debug("Duplicate candle timestamp: %s %s, skipping",
                         exchange, candle_ts)
            self._log_event(EVENT_CANDLE_DUPLICATE, symbol=exchange,
                            candle_ts=candle_ts)
            return

        # Phase 3F.5/3F.6.1 Rule 10 — strict order: validate -> year guard -> persist -> engine
        # ----------------------------------------------------------------------------------
        # Step 1: OHLCV validation & Year partition guard
        try:
            validate_candle_ohlcv(candle)
            validate_candle_year(candle, csv_path=csv_path)
        except ValueError as e:
            logger.error("Validation failed for candle %s %s: %s",
                         exchange, candle_ts, e)
            return  # Reject malformed or wrong-year candle

        # Step 2: Persist (if enabled) — MUST succeed before engine is called
        if self.persist:
            try:
                upsert_result = upsert_closed_candles(
                    [candle], csv_path, meta_path
                )
                logger.info(
                    "Persisted candle %s %s: inserts=%d updates=%d unchanged=%d sha256=%s",
                    exchange, candle_ts, upsert_result.inserts,
                    upsert_result.updates,
                    upsert_result.unchanged, upsert_result.sha256[:12],
                )
                self._log_event(
                    EVENT_STATE_SAVED,
                    symbol=exchange,
                    candle_ts=candle_ts,
                    inserts=upsert_result.inserts,
                    updates=upsert_result.updates,
                )
            except Exception as e:
                # Persistence failed: DO NOT mark as processed, DO NOT call engine
                logger.error(
                    "Persistence FAILED for candle %s %s: %s | "
                    "Candle remains eligible for retry.", exchange, candle_ts, e
                )
                return  # Rule 10: leave candle un-processed

        # Step 3: Mark processed ONLY after successful persistence
        processed.add(candle_ts)
        self._last_closed[exchange] = candle_ts
        self._log_event(EVENT_CANDLE_CLOSED, symbol=exchange,
                        candle_ts=candle_ts)

        # Step 4: Callback
        if self.on_candle_closed:
            self.on_candle_closed(candle)

        # Step 5: Engine — only after successful persistence
        if self.engine is not None:
            try:
                result = self.engine.process_new_candles([candle])
                self._log_event(EVENT_OB_CREATED, symbol=exchange,
                                ob_count=result.get("new_obs", 0))
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
        """Recover missed closed candles for every configured pair.

        Phase 3F.5: uses fetch_closed_candles() (paginated) instead of a single
        _fetch_window() call, then persists the entire batch via
        upsert_closed_candles() before passing successfully-persisted candles to
        the engine. Each pair is backfilled from its OWN watermark into its OWN
        partition, so a gap is never filled with another product's prices.
        """
        for exchange in self._exchange_symbols:
            await self._backfill_symbol(exchange)

    async def _backfill_symbol(self, exchange: str) -> None:
        logger.info("Starting REST backfill gap recovery for %s", exchange)
        self._log_event(EVENT_BACKFILL_STARTED, symbol=exchange)

        last_closed = self._last_closed[exchange]
        processed = self._processed[exchange]
        csv_path, meta_path = self._paths[exchange]

        if last_closed is None:
            logger.info("No prior processed candle for %s; skipping backfill",
                        exchange)
            self._log_event(EVENT_BACKFILL_COMPLETED, symbol=exchange,
                            success=True)
            return

        start_ts = last_closed + 3600
        end_ts = int(datetime.now(timezone.utc).timestamp())

        try:
            # Paginated REST fetch (fetch_closed_candles handles chunking + dedup)
            raw_candles = fetch_closed_candles(start_ts, end_ts, exchange)
            logger.info("Fetched %s %s candles via REST backfill",
                        len(raw_candles), exchange)

            if not raw_candles:
                self._log_event(EVENT_BACKFILL_COMPLETED, symbol=exchange,
                                success=True)
                return

            # Normalize raw Delta REST dicts
            candle_dicts = []
            for c in raw_candles:
                ts_int = int(c["time"])
                if not _is_candle_closed(ts_int):
                    continue  # skip any forming candle
                if ts_int in processed:
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
                    validate_candle_year(candle_dict, csv_path=csv_path)
                except ValueError as e:
                    logger.warning("Backfill candle rejected by year partition: %s", e)
                    continue
                candle_dicts.append(candle_dict)

            if not candle_dicts:
                logger.info("No new closed candles to backfill for %s", exchange)
                self._log_event(EVENT_BACKFILL_COMPLETED, symbol=exchange,
                                success=True)
                return

            # Phase 3F.5: Persist entire batch BEFORE engine (Rule 8)
            if self.persist:
                try:
                    upsert_result = upsert_closed_candles(
                        candle_dicts, csv_path, meta_path
                    )
                    logger.info(
                        "Backfill persisted %s: inserts=%d updates=%d unchanged=%d",
                        exchange, upsert_result.inserts, upsert_result.updates,
                        upsert_result.unchanged,
                    )
                    self._log_event(
                        EVENT_STATE_SAVED,
                        symbol=exchange,
                        inserts=upsert_result.inserts,
                        updates=upsert_result.updates,
                    )
                except Exception as e:
                    logger.error("Backfill persistence FAILED for %s: %s",
                                 exchange, e)
                    self._log_event(EVENT_BACKFILL_ERROR, symbol=exchange,
                                    error=str(e))
                    return  # Do not process candles if persistence failed

            # Pass successfully-persisted candles to engine and mark processed
            for c_dict in candle_dicts:
                ts_int = int(c_dict["timestamp"]) if not isinstance(c_dict["timestamp"], datetime) else int(c_dict["timestamp"].timestamp())
                processed.add(ts_int)
                self._last_closed[exchange] = ts_int

                candle_normalized = {
                    "symbol":    exchange + LOCAL_SYMBOL_SUFFIX,
                    "exchange_symbol": exchange,
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
                logger.warning("Gaps detected after %s backfill: %s",
                               exchange, gaps)
                self._log_event(EVENT_GAP_DETECTED, symbol=exchange, gaps=gaps)
            else:
                logger.info("No gaps detected after %s backfill", exchange)

        except Exception as e:
            logger.error("REST backfill error for %s: %s", exchange, e)
            self._log_event(EVENT_BACKFILL_ERROR, symbol=exchange, error=str(e))

        self._log_event(EVENT_BACKFILL_COMPLETED, symbol=exchange, success=True)

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