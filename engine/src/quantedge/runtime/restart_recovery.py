"""Restart and state-recovery safety layer for the STRUCTURE clock.

WHY THIS MODULE EXISTS
----------------------
LuxAlgo internal structure is path-dependent from bar 0: `_PineATR` is Wilder's
recursion (infinite memory), and the leg states, pivot levels, trend biases and
the order-block store are all derived from every candle that came before. The
Phase-2 audit measured what that means in practice - no finite fixed warmup
reproduces the live state exactly, XRPUSD needed the entire available history at
every tested cut, and a live order block survived 18,707 bars. So a restart
cannot be repaired by replaying "enough" recent candles; it has to resume from
the exact state it stopped in.

That exact mechanism already exists and is already proven: `capture_state()` /
`restore_state()` through `ManualSMCRuntime.save_state()` / `load_state()`,
verified by 28/28 exact continuation-parity checks. What did not exist was a
PRODUCTION CALL SITE. This module is that call site and nothing more:

    construct runtime -> locate durable state -> restore -> find the last
    accepted closed candle -> find the current closed-candle boundary -> fetch
    exactly the missing closed candles -> verify completeness and hourly
    contiguity -> replay them chronologically -> persist -> only then subscribe.

WHAT IT DELIBERATELY DOES NOT DO
-------------------------------
No execution. It never builds an orchestrator, never binds the forming-frame
EXECUTION clock, never reads a credential and never calls `runtime.submit()`.
It changes no strategy rule, no LuxAlgo semantics, no entry/SL/TP/window/
leverage arithmetic and no invalidation policy. Replay goes through
`runtime.handle_feed_payload()` - the SAME closed-candle path the live feed
uses - so there is no second ingestion path to keep in sync, and bar indices
come from `bar_index_for` unchanged (absolute epoch hours; never renumbered).

FAIL-CLOSED IN BOTH DIRECTIONS
------------------------------
Two failure classes, deliberately kept apart:

* INERT refusals (`StateLocationError`, `GenesisUnavailableError`) mean the
  operator has not configured a durable location or an origin. Restarting
  cannot fix that, so the process stays up, refuses to subscribe, logs CRITICAL
  and reports NOT healthy on /health. It never silently cold-starts as if
  structure were continuous.
* FATAL failures (unbridgeable gap, truncated/non-contiguous/failed history
  fetch, refused replay, failed persistence, corrupt snapshot) mean the
  structure stream cannot be trusted. They escalate to a non-zero process exit
  so the supervisor sees a failure and Docker's restart policy can act, instead
  of a blind engine warning once an hour while /health answers UP.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import (
    Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple,
)

from quantedge.market_data.ingestion import fetch_closed_candle_range
from quantedge.runtime.manual_smc_runtime import (
    ManualSMCRuntime,
    ManualSMCRuntimeStep,
    SKIPPED_DISABLED,
    bar_index_for,
)
from quantedge.strategy.manual_smc.backtest import (
    PRIMARY_CSV_NAME,
    TIMEFRAME_DIRNAME,
    load_canonical_candles,
)

logger = logging.getLogger(__name__)

HOUR = 3600

#: File name inside the configured durable directory. One file per engine
#: process, written by the EXISTING atomic `ManualSMCRuntime.save_state()`.
STATE_FILE_NAME = "manual_smc_runtime_state.json"

#: Exit status for an unrecoverable structure-continuity failure. Distinct from
#: `__main__.FEED_FAILURE_EXIT_CODE` (1, "the feed stopped") so the two reasons
#: are distinguishable in `docker inspect`/`docker events`.
STRUCTURE_WEDGE_EXIT_CODE = 2

MODE_RESTORED = "RESTORED"
MODE_GENESIS = "GENESIS"

# Fault codes. They appear in the CRITICAL log line and in the /health body.
FAULT_STRUCTURE_GAP = "STRUCTURE_GAP"
FAULT_STATE_PERSISTENCE_FAILED = "STATE_PERSISTENCE_FAILED"
FAULT_BOOTSTRAP_FAILED = "BOOTSTRAP_FAILED"

# Inert codes.
INERT_NO_STATE_DIR = "NO_DURABLE_STATE_DIR"
INERT_NO_GENESIS_SOURCE = "NO_GENESIS_HISTORY_SOURCE"


class RestartRecoveryError(RuntimeError):
    """Base class for every refusal raised by the restart/state layer."""

    #: Short machine-readable reason, surfaced on /health and in logs.
    code = FAULT_BOOTSTRAP_FAILED


class StateLocationError(RestartRecoveryError):
    """No usable durable state directory was configured.

    INERT, not fatal: a restart lands in exactly the same place, so crash-
    looping would only bury the reason in log noise.
    """

    code = INERT_NO_STATE_DIR


class GenesisUnavailableError(RestartRecoveryError):
    """There is no durable snapshot AND no configured full-history origin.

    INERT for the same reason as `StateLocationError`. This is the refusal that
    replaces inventing a warmup: with no snapshot and no declared origin the
    engine cannot know what LuxAlgo's state should be, so it does not pretend.
    """

    code = INERT_NO_GENESIS_SOURCE


class HistoryBootstrapError(RestartRecoveryError):
    """The missing closed candles could not be established beyond doubt.

    Covers a failed fetch, a short/truncated range, a hole in the middle, a
    duplicate, an out-of-order row, a malformed row and a refused replay. FATAL:
    the alternative is replaying an incomplete history into a path-dependent
    detector, which silently produces a different structure forever after.
    """


class StatePersistenceError(RestartRecoveryError):
    """The snapshot could not be written, or did not read back identically."""

    code = FAULT_STATE_PERSISTENCE_FAILED


#: Refusals the composition layer answers by staying inert instead of exiting.
INERT_BOOTSTRAP_REFUSALS: Tuple[type, ...] = (
    StateLocationError, GenesisUnavailableError)


class StructureContinuityMonitor:
    """The one place that knows whether the STRUCTURE clock is still trustworthy.

    Deliberately tiny: two optional strings and a list of stop hooks. It is NOT
    a readiness framework - it exists because `/health` previously answered a
    hardcoded `{"status":"UP"}` and therefore could not distinguish a working
    engine from one that had been wedged for hours.

    Thread-safe because the feed callback runs on the asyncio thread while
    `HealthHandler` runs on the `engine-health` daemon thread.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._fault: Optional[Tuple[str, str]] = None
        self._inert: Optional[Tuple[str, str]] = None
        self._stop_hooks: List[Callable[[], None]] = []

    # -- state -----------------------------------------------------------
    def reset(self) -> None:
        """Return to the pristine state. For tests and for re-arming only."""
        with self._lock:
            self._fault = None
            self._inert = None
            self._stop_hooks = []

    @property
    def fault(self) -> Optional[Tuple[str, str]]:
        with self._lock:
            return self._fault

    @property
    def inert(self) -> Optional[Tuple[str, str]]:
        with self._lock:
            return self._inert

    def is_healthy(self) -> bool:
        with self._lock:
            return self._fault is None and self._inert is None

    def register_stop_hook(self, hook: Callable[[], None]) -> None:
        """Register a callback run once, before a fatal fault raises SystemExit.

        Used by the composition layer to clear the websocket client's `running`
        flag: `listen()` ends with `finally: if self.running: await
        self._reconnect()`, which would otherwise swallow the exit into a
        reconnect + REST-backfill + resubscribe cycle and hide the wedge again.
        """
        with self._lock:
            self._stop_hooks.append(hook)

    # -- transitions -----------------------------------------------------
    def mark_inert(self, code: str, detail: str) -> None:
        """Record a configuration refusal. Does NOT exit: a restart cannot fix it."""
        with self._lock:
            if self._inert is None:
                self._inert = (code, detail)
        logger.critical(
            "STRUCTURE CLOCK INERT [%s]: %s - the live feed will NOT be "
            "subscribed and /health will report NOT healthy", code, detail)

    def mark_fault(self, code: str, detail: str) -> None:
        """Record an unrecoverable structure failure and exit non-zero.

        Always raises `SystemExit(STRUCTURE_WEDGE_EXIT_CODE)`. The CRITICAL line
        is emitted on THIS module's logger, not the entrypoint's, so the
        entrypoint's "the feed stopped" CRITICAL stays the only record there.
        """
        with self._lock:
            first = self._fault is None
            if first:
                self._fault = (code, detail)
            hooks = list(self._stop_hooks)
        if first:
            logger.critical(
                "STRUCTURE CLOCK FAULT [%s]: %s - exiting with status %d so the "
                "supervisor restarts the engine and it resumes from the last "
                "durable snapshot", code, detail, STRUCTURE_WEDGE_EXIT_CODE)
        for hook in hooks:
            try:
                hook()
            except Exception:                                      # noqa: BLE001
                logger.exception("stop hook failed while handling %s", code)
        raise SystemExit(STRUCTURE_WEDGE_EXIT_CODE)

    # -- reporting -------------------------------------------------------
    def health_payload(self) -> Tuple[int, bytes]:
        """`(http_status, body)` for `/health`.

        Healthy keeps the historical body byte-for-byte so nothing downstream
        (the compose healthcheck, dashboards) has to change.
        """
        with self._lock:
            problem = self._fault or self._inert
        if problem is None:
            return 200, b'{"status":"UP","service":"quantedge-engine"}'
        code, detail = problem
        body = json.dumps({"status": "DOWN", "service": "quantedge-engine",
                           "reason": code, "detail": detail[:200]},
                          separators=(",", ":"))
        return 503, body.encode("utf-8")


#: Process-wide singleton. `start_health_server()` must stay zero-argument, so
#: the health handler reads this rather than receiving a monitor.
STRUCTURE_MONITOR = StructureContinuityMonitor()


# ---------------------------------------------------------------------------
# Durable location
# ---------------------------------------------------------------------------
def resolve_state_file(state_dir: Optional[str]) -> Path:
    """`<state_dir>/manual_smc_runtime_state.json`, proven writable right now.

    The write probe matters: a read-only bind mount, a volume owned by root and
    a typo in the path all look identical at construction time and only surface
    at the first `save_state()` - an hour into live operation, after the first
    closed candle has already mutated in-memory structure. Failing here instead
    keeps the refusal in front of the subscription.
    """
    if state_dir is None or not str(state_dir).strip():
        raise StateLocationError(
            "no durable state directory is configured (ENGINE_STATE_DIR). "
            "LuxAlgo structure is path-dependent from bar 0, so the engine "
            "refuses to subscribe rather than cold-start as if structure were "
            "continuous")
    directory = Path(str(state_dir).strip())
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StateLocationError(
            f"durable state directory {directory} is not writable by this "
            f"process ({exc.__class__.__name__}: {exc}); mount a persistent "
            f"location owned by the engine user") from exc
    return directory / STATE_FILE_NAME


def newest_closed_bar_idx(now_ts: Optional[int] = None) -> int:
    """Index of the newest CLOSED 1h bar - the candle before the forming one.

    Same arithmetic as `market_data.candles.is_candle_closed`, expressed as a
    bar index so it can be compared with `bar_index_for` directly (both are
    absolute epoch hours; neither is ever renumbered).
    """
    if now_ts is None:
        now_ts = int(datetime.now(timezone.utc).timestamp())
    return int(now_ts) // HOUR - 1


# ---------------------------------------------------------------------------
# Closed bars
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClosedBar:
    """One verified closed candle, addressed by absolute epoch hour."""

    symbol: str
    bar_idx: int
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def payload(self, timeframe: str) -> Dict[str, Any]:
        """The same payload shape the live feed hands to `on_candle_closed`.

        Going through `handle_feed_payload` rather than calling
        `on_closed_candle` directly keeps replay on ONE ingestion path: every
        validation the live feed is subject to (Mapping shape, required keys,
        finite positive prices, OHLC consistency, timeframe match, closed-candle
        wall-clock check) applies to replayed candles too.
        """
        return {
            "symbol": self.symbol,
            "timeframe": timeframe,
            "timestamp": self.bar_idx * HOUR,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": True,
        }


def verify_bar_series(
    symbol: str,
    bars: Sequence[ClosedBar],
    *,
    expect_first_bar_idx: Optional[int] = None,
    expect_last_bar_idx: Optional[int] = None,
) -> None:
    """Refuse anything that is not a complete, contiguous, ordered hourly run.

    Every check here answers a specific way an incomplete history silently
    corrupts a path-dependent detector forever:

    * a missing hour in the middle -> the ATR recursion and every leg/pivot
      after it diverge, and the live feed's next candle looks contiguous;
    * a missing hour at either END -> `_position_for` refuses the first live
      candle and the process wedges;
    * a duplicate -> the same bar is folded into the ATR recursion twice;
    * an out-of-order row -> `GlobalOrderError`/`CandleOrderError` at replay,
      or a silently wrong structure if the rows land in a single window.
    """
    if not bars:
        raise HistoryBootstrapError(
            f"{symbol}: expected closed candles but received none; refusing to "
            f"treat an empty result as an up-to-date history")
    seen: set = set()
    previous: Optional[ClosedBar] = None
    for bar in bars:
        if bar.symbol != symbol:
            raise HistoryBootstrapError(
                f"{symbol}: history contains a row for {bar.symbol}")
        if bar.bar_idx in seen:
            raise HistoryBootstrapError(
                f"{symbol}: duplicate closed candle at bar {bar.bar_idx} "
                f"({bar.ts.isoformat()})")
        if previous is not None:
            if bar.bar_idx <= previous.bar_idx:
                raise HistoryBootstrapError(
                    f"{symbol}: closed candles are not in chronological order "
                    f"({previous.bar_idx} then {bar.bar_idx})")
            if bar.bar_idx != previous.bar_idx + 1:
                missing = bar.bar_idx - previous.bar_idx - 1
                raise HistoryBootstrapError(
                    f"{symbol}: {missing} closed candle(s) missing between bar "
                    f"{previous.bar_idx} ({previous.ts.isoformat()}) and bar "
                    f"{bar.bar_idx} ({bar.ts.isoformat()}); the hourly series "
                    f"is not contiguous")
        seen.add(bar.bar_idx)
        previous = bar
    if expect_first_bar_idx is not None and bars[0].bar_idx != expect_first_bar_idx:
        raise HistoryBootstrapError(
            f"{symbol}: history starts at bar {bars[0].bar_idx} but the gap "
            f"starts at bar {expect_first_bar_idx}; the range is truncated at "
            f"the oldest end")
    if expect_last_bar_idx is not None and bars[-1].bar_idx != expect_last_bar_idx:
        raise HistoryBootstrapError(
            f"{symbol}: history ends at bar {bars[-1].bar_idx} but the newest "
            f"closed bar is {expect_last_bar_idx}; the range is truncated at "
            f"the newest end")


def _finite_price(symbol: str, bar_idx: int, field: str, raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HistoryBootstrapError(
            f"{symbol}: bar {bar_idx} has a non-numeric {field} ({raw!r})"
        ) from exc
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        raise HistoryBootstrapError(
            f"{symbol}: bar {bar_idx} has a non-finite or non-positive {field} "
            f"({raw!r})")
    return value


def bars_from_delta_rows(
    symbol: str, rows: Sequence[Mapping[str, Any]],
) -> List[ClosedBar]:
    """Convert raw Delta history rows into verified `ClosedBar`s.

    Malformed rows are REFUSED, never skipped: a skipped row is a hole, and a
    hole is exactly what this whole module exists to prevent.
    """
    bars: List[ClosedBar] = []
    for raw in rows:
        if not isinstance(raw, Mapping) or "time" not in raw:
            raise HistoryBootstrapError(
                f"{symbol}: history row is not a candle mapping ({raw!r})")
        try:
            ts_int = int(raw["time"])
        except (TypeError, ValueError) as exc:
            raise HistoryBootstrapError(
                f"{symbol}: history row has a non-integer time ({raw!r})"
            ) from exc
        if ts_int % HOUR:
            raise HistoryBootstrapError(
                f"{symbol}: history row at epoch {ts_int} is not on a 1h "
                f"boundary")
        bar_idx = ts_int // HOUR
        o = _finite_price(symbol, bar_idx, "open", raw.get("open", raw.get("o")))
        h = _finite_price(symbol, bar_idx, "high", raw.get("high", raw.get("h")))
        low = _finite_price(symbol, bar_idx, "low", raw.get("low", raw.get("l")))
        c = _finite_price(symbol, bar_idx, "close", raw.get("close", raw.get("c")))
        try:
            volume = float(raw.get("volume", raw.get("v", 0.0)) or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        bars.append(ClosedBar(
            symbol=symbol, bar_idx=bar_idx,
            ts=datetime.fromtimestamp(ts_int, tz=timezone.utc),
            open=o, high=h, low=low, close=c, volume=volume))
    bars.sort(key=lambda bar: bar.bar_idx)
    return bars


# ---------------------------------------------------------------------------
# Genesis: the declared origin, used only when no snapshot exists
# ---------------------------------------------------------------------------
def load_genesis_bars(
    genesis_dir: Optional[str],
    symbols: Sequence[str],
    timeframe: str,
    *,
    newest_bar_idx: int,
) -> Dict[str, List[ClosedBar]]:
    """Read the full canonical 1h history that declares where structure begins.

    POLICY DECISION, reported rather than invented (mandate §3): genesis is
    "replay the operator's declared full history from its first row", NOT "warm
    up over the last N candles". The audit showed no finite warmup is exact, so
    a warmup would be a guess presented as continuity. This is instead an
    explicit, auditable origin: whatever the operator mounted, from row 0.

    Two deliberate refusals:

    * `full_history.csv` must exist. `backtest.canonical_csv_path` silently
      falls back to `2026.csv`, and a 2026-01-01 origin is a truncated history
      dressed up as a complete one, so the primary file is checked FIRST and the
      fallback is never accepted here.
    * a symbol with no rows is refused, not treated as "nothing to replay".
    """
    if genesis_dir is None or not str(genesis_dir).strip():
        raise GenesisUnavailableError(
            "no durable snapshot exists and no genesis history directory is "
            "configured (ENGINE_GENESIS_HISTORY_DIR); refusing to invent a "
            "finite warmup for a detector that is path-dependent from bar 0")
    base = Path(str(genesis_dir).strip())
    per_symbol: Dict[str, List[ClosedBar]] = {}
    for symbol in symbols:
        primary = base / symbol / TIMEFRAME_DIRNAME / PRIMARY_CSV_NAME
        if not primary.is_file():
            raise GenesisUnavailableError(
                f"{symbol}: genesis requires {primary} and it does not exist; "
                f"the {TIMEFRAME_DIRNAME} fallback CSV is deliberately NOT "
                f"accepted as an origin")
        candles = load_canonical_candles(base, symbol)
        bars: List[ClosedBar] = []
        for candle in candles:
            try:
                bar_idx = bar_index_for(candle.ts, timeframe)
            except Exception as exc:                               # noqa: BLE001
                raise HistoryBootstrapError(
                    f"{symbol}: genesis row {candle.ts!r} is not on a "
                    f"{timeframe} boundary ({exc})") from exc
            if bar_idx > newest_bar_idx:
                continue                     # not closed yet; never replay it
            bars.append(ClosedBar(
                symbol=symbol, bar_idx=bar_idx, ts=candle.ts,
                open=candle.open, high=candle.high, low=candle.low,
                close=candle.close, volume=candle.volume))
        if not bars:
            raise GenesisUnavailableError(
                f"{symbol}: {primary} contains no closed {timeframe} candles; "
                f"refusing to start structure from an empty origin")
        verify_bar_series(symbol, bars)
        per_symbol[symbol] = bars
        logger.info(
            "genesis history for %s: %d closed candles, %s -> %s (bars %d..%d)",
            symbol, len(bars), bars[0].ts.isoformat(), bars[-1].ts.isoformat(),
            bars[0].bar_idx, bars[-1].bar_idx)
    return per_symbol


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay_bars(
    runtime: ManualSMCRuntime, bars: Sequence[ClosedBar],
) -> Dict[str, int]:
    """Feed bars through the NORMAL closed-candle path, globally ordered.

    Global order across symbols is not cosmetic. `ManualSMCStrategy` refuses a
    candle older than the last one processed for ANY asset (`GlobalOrderError`)
    because the single trade slot and the lifecycle's close-timestamp watermark
    couple the four markets together. Replaying symbol-by-symbol would either
    be refused outright or - worse, if it were permitted - produce a different
    interleaving than live operation and therefore a different result. Equal
    timestamps across different assets are allowed, so the sort key breaks ties
    by symbol for determinism.
    """
    ordered = sorted(bars, key=lambda bar: (bar.bar_idx, bar.symbol))
    replayed: Dict[str, int] = {}
    for bar in ordered:
        try:
            step: ManualSMCRuntimeStep = runtime.handle_feed_payload(
                bar.payload(runtime.timeframe))
        except Exception as exc:                                   # noqa: BLE001
            raise HistoryBootstrapError(
                f"{bar.symbol}: replay of bar {bar.bar_idx} "
                f"({bar.ts.isoformat()}) was refused by the closed-candle path "
                f"({exc.__class__.__name__}: {exc}); the structure stream "
                f"cannot be bridged") from exc
        if step.skipped is not None and step.skipped != SKIPPED_DISABLED:
            raise HistoryBootstrapError(
                f"{bar.symbol}: replay of bar {bar.bar_idx} was skipped "
                f"({step.skipped}); a skipped candle leaves a hole in a "
                f"path-dependent series")
        if step.skipped is None:
            replayed[bar.symbol] = replayed.get(bar.symbol, 0) + 1
    return replayed


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persist_state(runtime: ManualSMCRuntime, state_path: Path) -> None:
    """Write the snapshot atomically and prove it reads back identically.

    Uses the EXISTING `save_state()` (temporary file + one rename) - no second
    persistence mechanism. The read-back is the part that makes a silent
    half-write impossible to mistake for durability: `save_state()` returning
    normally only proves the bytes left this process.
    """
    try:
        written = runtime.save_state(state_path)
    except Exception as exc:                                       # noqa: BLE001
        raise StatePersistenceError(
            f"could not write the durable snapshot to {state_path} "
            f"({exc.__class__.__name__}: {exc}); refusing to continue as if "
            f"durable state existed") from exc
    expected = runtime.snapshot()
    try:
        readback = json.loads(Path(written).read_text(encoding="utf-8"))
    except Exception as exc:                                       # noqa: BLE001
        raise StatePersistenceError(
            f"the durable snapshot at {written} could not be read back "
            f"({exc.__class__.__name__}: {exc})") from exc
    if readback != expected:
        raise StatePersistenceError(
            f"the durable snapshot at {written} does not match the in-memory "
            f"state it was written from")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BootstrapReport:
    """What the startup sequence actually did. Logged, and used by tests."""

    mode: str
    runtime: ManualSMCRuntime
    state_path: Path
    newest_bar_idx: int
    replayed: Mapping[str, int]
    watermarks: Mapping[str, int]
    genesis_first_bar: Mapping[str, int]

    @property
    def total_replayed(self) -> int:
        return sum(self.replayed.values())


#: `fetcher(start_ts, end_ts, symbol) -> raw rows`. Defaults to the real REST
#: range fetch; deterministic tests inject a fake so nothing touches Delta.
HistoryFetcher = Callable[[int, int, str], Sequence[Mapping[str, Any]]]


def bootstrap_structure_state(
    runtime: ManualSMCRuntime,
    *,
    state_dir: Optional[str],
    genesis_dir: Optional[str],
    fetcher: Optional[HistoryFetcher] = None,
    now_ts: Optional[int] = None,
) -> BootstrapReport:
    """Bring the STRUCTURE clock to the current closed-candle boundary.

    The mandate's sequence, in order and with nothing between the last step and
    the caller's subscription:

        locate durable state -> restore -> last accepted closed candle ->
        current closed-candle boundary -> fetch exactly the missing closed
        candles -> verify completeness + hourly contiguity -> replay
        chronologically -> persist.

    Genesis bars and bridge bars are merged into ONE globally ordered replay
    rather than two passes, because two passes could feed symbol A's older
    bridge candle after symbol B's newer genesis candle and be refused by the
    cross-asset order rule.

    Raises `RestartRecoveryError`. The caller decides inert vs fatal;
    `bootstrap_or_fail` implements that decision for production.
    """
    state_path = resolve_state_file(state_dir)
    newest = newest_closed_bar_idx(now_ts)
    fetch = fetcher if fetcher is not None else fetch_closed_candle_range

    pending: List[ClosedBar] = []
    origin: Dict[str, int] = {}
    genesis_first: Dict[str, int] = {}

    if state_path.is_file():
        mode = MODE_RESTORED
        logger.info("restoring the Manual SMC runtime from %s", state_path)
        runtime = ManualSMCRuntime.load_state(state_path)
        for symbol in runtime.enabled_symbols:
            mark = runtime.strategy.watermark.last(symbol)
            if mark is None:
                raise HistoryBootstrapError(
                    f"{symbol}: the restored snapshot has no processed-candle "
                    f"watermark, so there is no origin to be contiguous with; "
                    f"clear the durable state to re-run genesis deliberately")
            origin[symbol] = mark.bar_idx
    else:
        mode = MODE_GENESIS
        logger.info(
            "no durable snapshot at %s; starting from the declared genesis "
            "history", state_path)
        per_symbol = load_genesis_bars(
            genesis_dir, runtime.enabled_symbols, runtime.timeframe,
            newest_bar_idx=newest)
        for symbol, bars in per_symbol.items():
            pending.extend(bars)
            origin[symbol] = bars[-1].bar_idx
            genesis_first[symbol] = bars[0].bar_idx

    for symbol in sorted(origin):
        first_missing = origin[symbol] + 1
        if first_missing > newest:
            logger.info("%s: structure is already at the newest closed bar %d",
                        symbol, newest)
            continue
        logger.info("%s: bridging bars %d..%d (%d closed candles) by REST",
                    symbol, first_missing, newest, newest - first_missing + 1)
        try:
            rows = fetch(first_missing * HOUR, newest * HOUR, symbol)
        except Exception as exc:                                   # noqa: BLE001
            raise HistoryBootstrapError(
                f"{symbol}: the historical fetch for bars {first_missing}.."
                f"{newest} failed ({exc.__class__.__name__}: {exc}); a failed "
                f"fetch is never a complete history") from exc
        bars = bars_from_delta_rows(symbol, rows)
        verify_bar_series(symbol, bars,
                          expect_first_bar_idx=first_missing,
                          expect_last_bar_idx=newest)
        pending.extend(bars)

    replayed = replay_bars(runtime, pending)
    persist_state(runtime, state_path)

    watermarks: Dict[str, int] = {}
    for symbol in runtime.symbols:
        mark = runtime.strategy.watermark.last(symbol)
        if mark is not None:
            watermarks[symbol] = mark.bar_idx
    report = BootstrapReport(
        mode=mode, runtime=runtime, state_path=state_path, newest_bar_idx=newest,
        replayed=dict(replayed), watermarks=watermarks,
        genesis_first_bar=genesis_first)
    logger.info(
        "STRUCTURE bootstrap %s | replayed=%d newest_closed_bar=%d "
        "watermarks=%s live_obs=%d state=%s", mode, report.total_replayed,
        newest, watermarks, len(runtime.active_obs()), state_path)
    return report


def bootstrap_or_fail(
    runtime: ManualSMCRuntime,
    *,
    monitor: StructureContinuityMonitor,
    state_dir: Optional[str],
    genesis_dir: Optional[str],
    fetcher: Optional[HistoryFetcher] = None,
    now_ts: Optional[int] = None,
) -> BootstrapReport:
    """`bootstrap_structure_state` with the production failure policy applied.

    Configuration refusals propagate for the caller to answer with an inert
    process. Everything else is a structure-continuity failure: mark the fault,
    which logs CRITICAL, runs the stop hooks and raises SystemExit(2).
    """
    try:
        return bootstrap_structure_state(
            runtime, state_dir=state_dir, genesis_dir=genesis_dir,
            fetcher=fetcher, now_ts=now_ts)
    except INERT_BOOTSTRAP_REFUSALS:
        raise
    except RestartRecoveryError as exc:
        monitor.mark_fault(exc.code, str(exc))
        raise                                          # pragma: no cover
    except Exception as exc:                                       # noqa: BLE001
        monitor.mark_fault(
            FAULT_BOOTSTRAP_FAILED,
            f"{exc.__class__.__name__}: {exc}")
        raise                                          # pragma: no cover


# ---------------------------------------------------------------------------
# Live callback wrapper
# ---------------------------------------------------------------------------
def make_durable_feed_callback(
    runtime: ManualSMCRuntime,
    inner: Callable[[Any], Optional[ManualSMCRuntimeStep]],
    *,
    state_path: Path,
    monitor: StructureContinuityMonitor = STRUCTURE_MONITOR,
) -> Callable[[Any], Optional[ManualSMCRuntimeStep]]:
    """Wrap the existing feed callback with wedge detection and persistence.

    `inner` stays exactly what it was - `make_feed_callback(runtime,
    submit_to=None)`, which still owns candle handling and still counts refused
    updates. This wrapper adds only the two things that were missing:

    1. BEFORE handing the payload over, ask the runtime whether this candle
       would leave a hole. A positive gap means LuxAlgo's `_position_for` is
       about to raise and `make_feed_callback` is about to swallow it as one
       warning per hour, forever, while /health answers UP. That is escalated to
       a fault instead. A negative gap (duplicate/out-of-order) and an
       unparseable payload are NOT escalated: they are ordinary refusals and
       stay ordinary refusals, counted as before.
    2. AFTER a candle is accepted, persist. This is the safest point: the
       lifecycle and the watermark have both advanced, so the snapshot is whole
       rather than torn, and it is the first instant at which the in-memory
       state is worth more than the file. Persistence failure is a fault, not a
       warning - continuing would mean reporting successful structure
       processing that no restart could reproduce.
    """
    def on_closed_candle(payload: Any) -> Optional[ManualSMCRuntimeStep]:
        gap = runtime.continuity_gap_for_payload(payload)
        if gap is not None and gap > 0:
            monitor.mark_fault(
                FAULT_STRUCTURE_GAP,
                f"{gap} closed 1h candle(s) are missing before the candle just "
                f"received; LuxAlgo structure is path-dependent from bar 0 and "
                f"cannot skip a bar")
        step = inner(payload)
        if step is None or step.skipped is not None:
            return step
        try:
            runtime.save_state(state_path)
        except Exception as exc:                                   # noqa: BLE001
            monitor.mark_fault(
                FAULT_STATE_PERSISTENCE_FAILED,
                f"could not persist the accepted closed candle to "
                f"{state_path} ({exc.__class__.__name__}: {exc})")
        return step

    return on_closed_candle





