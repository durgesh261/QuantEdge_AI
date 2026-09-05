"""
Live market-update aggregation: the front end of the EXECUTION CLOCK.

WHY THIS MODULE EXISTS
----------------------
Manual SMC is a 1-hour STRUCTURE strategy, and 1h is the timeframe of the
analysis only. It is NOT the rate at which market data arrives. The exchange
pushes updates continuously, and an order block touched at 14:23:17 with its 25%
entry reached at 14:24:02 must be acted on at those instants, not at 15:00.

Two clocks therefore exist, and this module belongs to the second:

  A. the STRUCTURE clock - closed 1h candles drive LuxAlgo ATR / pivots /
     BOS / CHoCH / order-block creation. Unchanged by this module.
  B. the EXECUTION clock - every live market update drives active-OB touch
     detection, entry-price evaluation, and exit resolution.

WHAT THIS MODULE DOES, AND WHAT IT REFUSES TO DO
------------------------------------------------
It normalises one incoming live payload into a `MarketUpdate`, folds it into the
currently-forming candle, and reports both. It performs NO strategy logic, NO
order-block reasoning and NO I/O; it opens no socket and reads no credentials.

The candle it maintains is for OBSERVATION and for the execution clock's bar
index. It is deliberately NOT offered as a substitute for the exchange's own
closed `candlestick_1h` frame: LuxAlgo keeps consuming that authoritative frame,
so 1h structure semantics stay exactly as they are today.

Ambiguity fails closed. Two different prices claimed for the same microsecond,
an update for an hour already finalized, or an update older than the last one
processed are all REFUSED. Nothing here invents or repairs a price.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

#: Delta India reports frame times in microseconds (`candle_start_time`,
#: `timestamp`, `last_updated`). Verified against the frame documented in
#: `market_data.delta_websocket._parse_candle_from_ws`.
MICROSECONDS_PER_SECOND = 1_000_000

#: The execution clock buckets updates into the STRUCTURE timeframe's candles so
#: its bar index is the same integer the closed-candle path uses. 1h only: an
#: unknown timeframe fails closed rather than being guessed.
BUCKET_SECONDS: Mapping[str, int] = {"1h": 3600}

#: `MarketUpdate.source` values. A forming candlestick frame carries the hour's
#: running OHLC as the exchange itself computed it; a price update carries one
#: price and nothing else.
SOURCE_CANDLESTICK_FORMING = "CANDLESTICK_FORMING"
SOURCE_PRICE = "PRICE"
SOURCES: Tuple[str, ...] = (SOURCE_CANDLESTICK_FORMING, SOURCE_PRICE)


class LiveMarketUpdateError(RuntimeError):
    """Base class: a live market update cannot be used as given."""


class MalformedMarketUpdateError(LiveMarketUpdateError):
    """The payload is missing a required field, or a field is unusable."""


class StaleMarketUpdateError(LiveMarketUpdateError):
    """The update is older than one already folded in for this symbol."""


class AmbiguousMarketUpdateError(LiveMarketUpdateError):
    """
    Two different prices are claimed for the same instant.

    Not repaired and not averaged. Which of the two is true decides whether an
    order block was touched, so a guess here would decide a trade.
    """


class FinalizedBucketError(LiveMarketUpdateError):
    """
    An update arrived for an hour this aggregator has already finalized.

    Finalization happens exactly once per bucket. Re-opening a finalized hour
    would let a late frame alter a candle already reported as complete.
    """


# ---------------------------------------------------------------------------
# Field validation. Nothing is coerced from a wrong type: a caller handing over
# a string price has a bug, and guessing what they meant hides it.
# ---------------------------------------------------------------------------
def bucket_seconds(timeframe: object) -> int:
    """Seconds per execution-clock bucket, or refuse."""
    try:
        return BUCKET_SECONDS[timeframe]        # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise MalformedMarketUpdateError(
            f"unsupported timeframe {timeframe!r}; the execution clock buckets "
            f"into {sorted(BUCKET_SECONDS)} only") from exc


def require_price(value: object, where: str) -> float:
    """Finite, strictly positive. A NaN would poison every comparison."""
    if isinstance(value, bool):
        raise MalformedMarketUpdateError(
            f"{where}: expected a price, got bool {value!r}")
    if isinstance(value, Decimal):
        price = float(value)
    elif isinstance(value, (int, float)):
        price = float(value)
    else:
        raise MalformedMarketUpdateError(
            f"{where}: expected a number, got {type(value).__name__} {value!r}")
    if price != price or price in (float("inf"), float("-inf")):
        raise MalformedMarketUpdateError(
            f"{where}: price is not finite ({value!r})")
    if price <= 0.0:
        raise MalformedMarketUpdateError(
            f"{where}: price must be strictly positive, got {value!r}")
    return price


def require_tz_aware(ts: object, where: str) -> datetime:
    """A naive timestamp cannot be placed on the global clock."""
    if not isinstance(ts, datetime):
        raise MalformedMarketUpdateError(
            f"{where}: expected a datetime, got {type(ts).__name__} {ts!r}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise MalformedMarketUpdateError(
            f"{where}: {ts!r} is naive; refusing to assume a timezone")
    return ts


def ts_from_microseconds(raw: object, where: str) -> datetime:
    """Exact UTC datetime from an integer microsecond epoch.

    Built by addition rather than `fromtimestamp(us / 1e6)`: float division
    loses microseconds at present-day epochs, and the microsecond is what
    orders the execution clock.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            raw = int(raw)
        else:
            raise MalformedMarketUpdateError(
                f"{where}: expected an integer microsecond epoch, got "
                f"{type(raw).__name__} {raw!r}")
    seconds, micros = divmod(int(raw), MICROSECONDS_PER_SECOND)
    return (datetime.fromtimestamp(seconds, tz=timezone.utc)
            + timedelta(microseconds=micros))


def bucket_index_for(ts: object, timeframe: str = "1h") -> int:
    """The bucket (candle) index containing `ts`.

    THE SAME INTEGER the closed-candle path uses. `runtime.bar_index_for`
    demands an exact boundary timestamp and returns `epoch // seconds` for the
    candle opening at it; a live update falls INSIDE a candle, so this floors
    instead. For any `ts` in `[T, T + seconds)` the two agree on the candle
    opening at `T`, which is what makes an intra-hour update and its own closed
    candle share one bar index.
    """
    seconds = bucket_seconds(timeframe)
    when = require_tz_aware(ts, "ts")
    epoch = when.timestamp()
    return int(epoch // seconds)


def bucket_open_ts(bucket_idx: int, timeframe: str = "1h") -> datetime:
    """The UTC instant at which `bucket_idx` opens."""
    seconds = bucket_seconds(timeframe)
    if isinstance(bucket_idx, bool) or not isinstance(bucket_idx, int):
        raise MalformedMarketUpdateError(
            f"bucket_idx: expected an int, got {type(bucket_idx).__name__}")
    return datetime.fromtimestamp(bucket_idx * seconds, tz=timezone.utc)


# ---------------------------------------------------------------------------
# One live market update.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketUpdate:
    """
    ONE live market update: a price, the instant it was true, and its range.

    `high` / `low` describe the range THIS update spans. A single trade print
    spans nothing, so both equal `price`; a forming-candlestick frame carries the
    hour's running extremes as the exchange computed them, so they are the
    frame's own high and low. The degenerate `high == low == price` pair is what
    lets the authorised lifecycle predicates - all of them functions of
    `(high, low)` - apply to a tick without being restated or relaxed.

    `bucket_idx` is the STRUCTURE candle containing `ts`, and it is the same
    integer the closed-candle path calls `bar_idx`.
    """

    symbol: str
    ts: datetime
    price: float
    high: float
    low: float
    bucket_idx: int
    source: str
    timeframe: str = "1h"
    exchange_symbol: Optional[str] = None
    #: The candle open the FRAME declared, when it declared one. Cross-checked
    #: against `ts`; never used to replace it.
    declared_bucket_ts: Optional[datetime] = None
    #: The hour's opening price as the exchange reports it, when supplied. The
    #: aggregator prefers it over the first price it happens to observe, because
    #: a subscriber that connects mid-hour never saw the real first trade.
    exchange_open: Optional[float] = None
    #: The raw microsecond epoch this update's `ts` came from, for audit.
    raw_ts_us: Optional[int] = None


def _require_symbol(payload: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise MalformedMarketUpdateError(
            f"symbol: expected a non-empty str, got {symbol!r}")
    exchange = payload.get("exchange_symbol")
    if exchange is not None and (not isinstance(exchange, str) or not exchange):
        raise MalformedMarketUpdateError(
            f"exchange_symbol: expected a non-empty str or None, got "
            f"{exchange!r}")
    return symbol, exchange


def _resolve_source(payload: Mapping[str, Any]) -> str:
    """The payload's shape, from its declared `source` or from its keys.

    Inference is a fixed rule, not a guess: all four OHLC keys present means a
    forming candlestick frame; a `price` key means a price update. Anything else
    is refused rather than interpreted.
    """
    declared = payload.get("source")
    if declared is not None:
        if declared not in SOURCES:
            raise MalformedMarketUpdateError(
                f"source: {declared!r} is not one of {list(SOURCES)}")
        return declared
    if all(k in payload for k in ("open", "high", "low", "close")):
        return SOURCE_CANDLESTICK_FORMING
    if "price" in payload:
        return SOURCE_PRICE
    raise MalformedMarketUpdateError(
        "cannot tell what this payload is: it carries neither a full OHLC set "
        f"nor a 'price' key. Keys present: {sorted(payload)}")


def _update_ts(payload: Mapping[str, Any]) -> Tuple[datetime, Optional[int]]:
    """The instant the price was true, and the raw microsecond epoch behind it.

    Accepts an explicit `ts` datetime, or a microsecond epoch under
    `update_ts_us` / `ts_us`. `timestamp` is deliberately NOT accepted as the
    update instant: in a Delta candlestick frame it means the candle's START in
    one place and the last tick in another, and resolving that ambiguity by
    preference would silently mis-time an entry.
    """
    if "ts" in payload:
        return require_tz_aware(payload["ts"], "ts"), None
    for key in ("update_ts_us", "ts_us"):
        if key in payload:
            raw = payload[key]
            return ts_from_microseconds(raw, key), int(raw)
    raise MalformedMarketUpdateError(
        "no update instant: supply 'ts' (tz-aware datetime) or "
        f"'update_ts_us' (integer microseconds). Keys present: {sorted(payload)}")


def _declared_bucket_ts(
    payload: Mapping[str, Any], symbol: str
) -> Optional[datetime]:
    """The candle start the frame declares, in seconds or microseconds.

    `candle_start_ts` is UNIX SECONDS - the unit
    `delta_websocket._parse_candle_from_ws` already normalises to.
    `candle_start_ts_us` is the raw microsecond field. Neither is required; a
    frame that declares no candle start is still usable, it simply cannot be
    cross-checked.
    """
    if "candle_start_ts_us" in payload:
        return ts_from_microseconds(payload["candle_start_ts_us"],
                                    "candle_start_ts_us")
    raw = payload.get("candle_start_ts")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return require_tz_aware(raw, "candle_start_ts")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise MalformedMarketUpdateError(
            f"{symbol}: candle_start_ts must be an int (UNIX seconds) or a "
            f"tz-aware datetime, got {type(raw).__name__} {raw!r}")
    return datetime.fromtimestamp(raw, tz=timezone.utc)


def normalize_market_update(
    payload: Any, timeframe: str = "1h"
) -> MarketUpdate:
    """
    Normalise one live payload into a `MarketUpdate`, or refuse it.

    TWO ACCEPTED SHAPES, and no others.

    1. A FORMING CANDLESTICK FRAME - `open`, `high`, `low`, `close` (float or
       Decimal), the candle's own start under `candle_start_ts` (UNIX seconds) or
       `candle_start_ts_us` (microseconds), and the last-tick instant under
       `update_ts_us`. `is_closed` must be absent or False: a CLOSED candle
       belongs to the structure clock and is refused here so one frame can never
       be processed by both clocks.

    2. A PRICE UPDATE - `price`, plus `ts` or `update_ts_us`. `high` / `low` are
       optional and default to `price`.

    INTEGRITY, NOT REPAIR. When a frame declares its candle start, that window
    must contain the update instant. If it does not, one of the two fields is
    wrong, and which one decides when an order block was touched - so the frame
    is refused instead of being reconciled (safety rule #15).
    """
    if not isinstance(payload, Mapping):
        raise MalformedMarketUpdateError(
            f"a market update payload must be a mapping, got "
            f"{type(payload).__name__}")
    seconds = bucket_seconds(timeframe)
    symbol, exchange = _require_symbol(payload)
    source = _resolve_source(payload)
    ts, raw_us = _update_ts(payload)

    declared: Optional[datetime] = None
    exchange_open: Optional[float] = None
    if source == SOURCE_CANDLESTICK_FORMING:
        if payload.get("is_closed"):
            raise MalformedMarketUpdateError(
                f"{symbol}: this frame is marked closed, and a closed candle "
                f"belongs to the STRUCTURE clock. Route it to "
                f"`evaluate_closed_candle`, not to the execution clock.")
        price = require_price(payload["close"], "close")
        high = require_price(payload["high"], "high")
        low = require_price(payload["low"], "low")
        exchange_open = require_price(payload["open"], "open")
        declared = _declared_bucket_ts(payload, symbol)
    else:
        price = require_price(payload["price"], "price")
        high = price if payload.get("high") is None else require_price(
            payload["high"], "high")
        low = price if payload.get("low") is None else require_price(
            payload["low"], "low")

    if high < low:
        raise MalformedMarketUpdateError(
            f"{symbol}: high {high} is below low {low}")
    if not (low <= price <= high):
        raise MalformedMarketUpdateError(
            f"{symbol}: price {price} lies outside its own range [{low}, {high}]")
    if exchange_open is not None and not (low <= exchange_open <= high):
        raise MalformedMarketUpdateError(
            f"{symbol}: open {exchange_open} lies outside the frame's range "
            f"[{low}, {high}]; the frame contradicts itself")

    bucket_idx = bucket_index_for(ts, timeframe)
    if declared is not None:
        expected = bucket_open_ts(bucket_idx, timeframe)
        if declared != expected:
            raise MalformedMarketUpdateError(
                f"{symbol}: the frame declares candle start "
                f"{declared.isoformat()} but its update instant "
                f"{ts.isoformat()} falls in the candle opening "
                f"{expected.isoformat()} ({seconds}s buckets). Refusing to "
                f"choose which field is wrong.")
    return MarketUpdate(
        symbol=symbol,
        ts=ts,
        price=price,
        high=high,
        low=low,
        bucket_idx=bucket_idx,
        source=source,
        timeframe=timeframe,
        exchange_symbol=exchange,
        declared_bucket_ts=declared,
        exchange_open=exchange_open,
        raw_ts_us=raw_us,
    )


# ---------------------------------------------------------------------------
# The forming candle and the per-update result.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FormingCandle:
    """
    The candle currently being built for one symbol, as of the last update.

    A SNAPSHOT, not a live handle: `LiveCandleAggregator` replaces it rather
    than mutating it, so a caller that keeps one keeps what it saw.

    `open` is the hour's first price - the exchange's own `open` when a frame
    supplied one (a subscriber that connects mid-hour never saw the real first
    trade, and inventing one from the first frame it happens to receive would
    misstate the candle), otherwise the first price observed locally.
    """

    symbol: str
    bucket_idx: int
    bucket_ts: datetime
    open: float
    high: float
    low: float
    close: float
    first_update_ts: datetime
    last_update_ts: datetime
    update_count: int
    #: True when `open` came from the exchange rather than from the first price
    #: this process happened to observe. Reported, never assumed.
    open_from_exchange: bool = False
    sources: Tuple[str, ...] = ()

    @property
    def is_ohlc_consistent(self) -> bool:
        """`low <= open, close <= high`, the definition of a candle."""
        return (self.low <= self.open <= self.high
                and self.low <= self.close <= self.high
                and self.low <= self.high)


@dataclass(frozen=True)
class AggregatorStep:
    """What one call to `LiveCandleAggregator.update` produced."""

    symbol: str
    update: Optional[MarketUpdate]
    forming: Optional[FormingCandle]
    #: The candle finalized BY this update, i.e. the previous bucket, once and
    #: only once. `None` on every other update.
    finalized: Optional[FormingCandle] = None
    #: True when this payload carried nothing new and was folded in as a no-op.
    duplicate: bool = False
    #: Buckets the feed skipped entirely. NEVER fabricated as candles - a gap is
    #: reported, not filled.
    skipped_buckets: Tuple[int, ...] = ()
    #: True when this update opened a new bucket.
    rolled_over: bool = False


class LiveCandleAggregator:
    """
    Folds live market updates into the currently-forming candle, per symbol.

    OPEN is the hour's first price - the exchange's own when a frame reports one,
    otherwise the first price observed. HIGH is the running maximum, LOW the
    running minimum, CLOSE the latest price. Buckets are exact UTC hours, so a
    bucket index here is the same integer the closed-candle path uses.

    EXTREMES ONLY WIDEN. A running high is never lowered, even if a later frame
    reports a smaller one. Lowering it could retroactively un-touch an order
    block that has already fired an order, and no arithmetic here may rewrite
    what the execution clock has already acted on.

    FINALIZATION IS EXACTLY ONCE, and it is triggered by DATA - the first update
    belonging to a later bucket - never by a wall clock. A gap in the feed
    finalizes only the bucket actually observed; the skipped hours are reported
    and never fabricated.

    This class holds no socket, performs no I/O and knows nothing about orders.
    """

    def __init__(self, timeframe: str = "1h") -> None:
        self.timeframe: str = timeframe
        self.seconds: int = bucket_seconds(timeframe)
        self._forming: Dict[str, FormingCandle] = {}
        self._last: Dict[str, MarketUpdate] = {}
        self._finalized: Dict[str, set] = {}
        self.updates_applied: int = 0
        self.duplicates_ignored: int = 0
        self.candles_finalized: int = 0

    # -- introspection ----------------------------------------------------
    def symbols(self) -> Tuple[str, ...]:
        return tuple(sorted(self._forming))

    def forming(self, symbol: str) -> Optional[FormingCandle]:
        """The candle currently being built, or None before the first update."""
        return self._forming.get(symbol)

    def last_update(self, symbol: str) -> Optional[MarketUpdate]:
        return self._last.get(symbol)

    def finalized_buckets(self, symbol: str) -> Tuple[int, ...]:
        return tuple(sorted(self._finalized.get(symbol, ())))

    def is_finalized(self, symbol: str, bucket_idx: int) -> bool:
        return bucket_idx in self._finalized.get(symbol, ())

    # -- the single mutating entry point -----------------------------------
    def update(self, payload: Any) -> AggregatorStep:
        """Normalise a raw payload and fold it in. Refuses, never repairs."""
        return self.apply(normalize_market_update(payload, self.timeframe))

    def apply(self, update: MarketUpdate) -> AggregatorStep:
        """Fold one already-normalised `MarketUpdate` in."""
        if not isinstance(update, MarketUpdate):
            raise MalformedMarketUpdateError(
                f"apply() takes a MarketUpdate, got {type(update).__name__}")
        if update.timeframe != self.timeframe:
            raise MalformedMarketUpdateError(
                f"{update.symbol}: update is on {update.timeframe} but this "
                f"aggregator buckets {self.timeframe}")

        symbol = update.symbol
        previous = self._last.get(symbol)
        if previous is not None:
            duplicate = self._classify_repeat(previous, update)
            if duplicate:
                self.duplicates_ignored += 1
                return AggregatorStep(
                    symbol=symbol,
                    update=None,
                    forming=self._forming.get(symbol),
                    duplicate=True,
                )

        if self.is_finalized(symbol, update.bucket_idx):
            raise FinalizedBucketError(
                f"{symbol}: bucket {update.bucket_idx} "
                f"({bucket_open_ts(update.bucket_idx, self.timeframe).isoformat()}) "
                f"has already been finalized; refusing to re-open a completed "
                f"candle for an update at {update.ts.isoformat()}")

        forming = self._forming.get(symbol)
        finalized: Optional[FormingCandle] = None
        skipped: Tuple[int, ...] = ()
        rolled = False
        if forming is None:
            current = self._open_bucket(update)
            rolled = True
        elif forming.bucket_idx == update.bucket_idx:
            current = self._fold(forming, update)
        elif update.bucket_idx > forming.bucket_idx:
            finalized = forming
            self._finalized.setdefault(symbol, set()).add(forming.bucket_idx)
            self.candles_finalized += 1
            skipped = tuple(range(forming.bucket_idx + 1, update.bucket_idx))
            current = self._open_bucket(update)
            rolled = True
        else:
            raise StaleMarketUpdateError(
                f"{symbol}: update at {update.ts.isoformat()} belongs to bucket "
                f"{update.bucket_idx}, behind the forming bucket "
                f"{forming.bucket_idx}")

        self._forming[symbol] = current
        self._last[symbol] = update
        self.updates_applied += 1
        return AggregatorStep(
            symbol=symbol,
            update=update,
            forming=current,
            finalized=finalized,
            skipped_buckets=skipped,
            rolled_over=rolled,
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _classify_repeat(previous: MarketUpdate, update: MarketUpdate) -> bool:
        """True when `update` repeats `previous` exactly; raise when it clashes.

        A forming-candlestick frame repeats its last-tick timestamp whenever no
        new trade has printed, so an exact repeat is expected and is absorbed as
        a no-op. A DIFFERENT price at the same instant is a contradiction the
        feed must resolve, not this module (safety rule #15).
        """
        try:
            older = update.ts < previous.ts
            same_instant = update.ts == previous.ts
        except TypeError as exc:                # naive vs tz-aware mix
            raise MalformedMarketUpdateError(
                f"{update.symbol}: cannot compare update instant {update.ts!r} "
                f"with the previous {previous.ts!r}") from exc
        if older:
            raise StaleMarketUpdateError(
                f"{update.symbol}: update at {update.ts.isoformat()} predates "
                f"the last update processed at {previous.ts.isoformat()}; "
                f"refusing to re-order the execution clock")
        if not same_instant:
            return False
        same = (update.price == previous.price
                and update.high == previous.high
                and update.low == previous.low
                and update.bucket_idx == previous.bucket_idx)
        if same:
            return True
        raise AmbiguousMarketUpdateError(
            f"{update.symbol}: two different prices are claimed for "
            f"{update.ts.isoformat()} - was ({previous.price}, {previous.high}, "
            f"{previous.low}) in bucket {previous.bucket_idx}, now "
            f"({update.price}, {update.high}, {update.low}) in bucket "
            f"{update.bucket_idx}. Refusing to choose one.")

    def _open_bucket(self, update: MarketUpdate) -> FormingCandle:
        """Start a new forming candle from the first update seen inside it."""
        opening = (update.price if update.exchange_open is None
                   else update.exchange_open)
        return FormingCandle(
            symbol=update.symbol,
            bucket_idx=update.bucket_idx,
            bucket_ts=bucket_open_ts(update.bucket_idx, self.timeframe),
            open=opening,
            high=max(update.high, opening),
            low=min(update.low, opening),
            close=update.price,
            first_update_ts=update.ts,
            last_update_ts=update.ts,
            update_count=1,
            open_from_exchange=update.exchange_open is not None,
            sources=(update.source,),
        )

    def _fold(
        self, forming: FormingCandle, update: MarketUpdate
    ) -> FormingCandle:
        """Fold an update into the bucket it belongs to, widening the extremes."""
        opening = forming.open
        from_exchange = forming.open_from_exchange
        if update.exchange_open is not None:
            if not from_exchange:
                # The exchange knows the hour's true first trade; this process
                # may have connected after it. Adopt it once.
                opening = update.exchange_open
                from_exchange = True
            elif update.exchange_open != opening:
                raise AmbiguousMarketUpdateError(
                    f"{forming.symbol}: bucket {forming.bucket_idx} was opened "
                    f"at {opening} by the exchange and this frame reports "
                    f"{update.exchange_open}. A candle has one open; refusing "
                    f"to pick.")
        sources = (forming.sources if update.source in forming.sources
                   else forming.sources + (update.source,))
        return replace(
            forming,
            open=opening,
            high=max(forming.high, update.high, opening),
            low=min(forming.low, update.low, opening),
            close=update.price,
            last_update_ts=update.ts,
            update_count=forming.update_count + 1,
            open_from_exchange=from_exchange,
            sources=sources,
        )


# ---------------------------------------------------------------------------
# Wiring helper. Imports nothing from `runtime` or `strategy`, so the feed can
# depend on this module without a cycle.
# ---------------------------------------------------------------------------
def make_execution_clock_callback(
    aggregator: LiveCandleAggregator,
    on_update: Callable[[MarketUpdate, AggregatorStep], Any],
    on_finalized: Optional[Callable[[FormingCandle], Any]] = None,
    on_refused: Optional[Callable[[Exception, Any], Any]] = None,
) -> Callable[[Any], Optional[AggregatorStep]]:
    """
    Build a `callback(payload)` that folds a live payload in and dispatches it.

    `on_update` is called ONCE per applied update, with the update and the step,
    and is where the caller runs the execution clock. It is NOT called for a
    duplicate: a payload carrying no new trade must not re-run the OB sweep.

    `on_finalized` is called at most once per bucket, when a rollover completes
    the previous candle. It is offered for observation and logging. It is
    deliberately NOT wired to LuxAlgo: the structure clock consumes the
    exchange's own closed candle, so 1h structure semantics are unchanged.

    `on_refused`, when given, receives `(exception, payload)` for a refused
    payload and the exception is then swallowed; without it the exception
    propagates. A feed loop that must survive one bad frame supplies it - and a
    refusal is still a refusal, never a repair.
    """
    def callback(payload: Any) -> Optional[AggregatorStep]:
        try:
            step = aggregator.update(payload)
        except LiveMarketUpdateError as exc:
            if on_refused is None:
                raise
            on_refused(exc, payload)
            return None
        if step.finalized is not None and on_finalized is not None:
            on_finalized(step.finalized)
        if step.update is not None:
            on_update(step.update, step)
        return step

    return callback


__all__ = [
    "MICROSECONDS_PER_SECOND",
    "BUCKET_SECONDS",
    "SOURCE_CANDLESTICK_FORMING",
    "SOURCE_PRICE",
    "SOURCES",
    "LiveMarketUpdateError",
    "MalformedMarketUpdateError",
    "StaleMarketUpdateError",
    "AmbiguousMarketUpdateError",
    "FinalizedBucketError",
    "bucket_seconds",
    "require_price",
    "require_tz_aware",
    "ts_from_microseconds",
    "bucket_index_for",
    "bucket_open_ts",
    "MarketUpdate",
    "normalize_market_update",
    "FormingCandle",
    "AggregatorStep",
    "LiveCandleAggregator",
    "make_execution_clock_callback",
]
