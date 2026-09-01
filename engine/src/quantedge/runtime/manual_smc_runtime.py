"""Manual SMC production wiring.

WHAT THIS MODULE IS
-------------------
The single composition layer that makes the already-proven Manual SMC
strategy reachable from the running application. It owns no trading logic of
its own. Read it as a router with four jobs:

    1. accept a *closed* candle from the live feed and refuse anything else;
    2. derive the absolute bar index from that candle's own clock;
    3. hand the candle to the one shared `ManualSMCStrategy` instance and
       translate the result with the one shared `ManualSMCAdapter`;
    4. hand the resulting ready decisions to the existing market
       orchestration boundary, unchanged.

FLOW
----
    live closed candle (`delta_websocket` payload dict)
        -> `ManualSMCRuntime.closed_candle_from_payload`   (validate, gate)
        -> `ManualSMCRuntime.on_closed_candle`             (route, index)
        -> `ManualSMCStrategy.evaluate_closed_candle`      (frozen strategy)
        -> `ManualSMCAdapter.adapt`                        (frozen translator)
        -> `MarketScannerOrchestrator.scan_and_execute`    (frozen Path A)
        -> frozen allocator -> validation -> lifecycle -> exchange

WHAT THIS MODULE MUST NEVER DO
------------------------------
It never sizes a position, never converts prices into exchange contracts,
never touches the exchange transport, and never opens a second execution
path. Manual SMC publishes leverage intent and a bracket; everything past
the orchestration boundary belongs to the frozen execution engine.

STATE
-----
One process-wide strategy instance covers every configured pair: separate
scanner, order-block pool and candle watermark per pair, one shared
portfolio slot. Disabling a pair is *routing only* -- its candles stop being
delivered, and not one byte of its state is destroyed. `save_state` wraps
`ManualSMCStrategy.capture_state()` in a small envelope and writes it
atomically; `load_state` rebuilds a runtime that behaves exactly like one
that never stopped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.instruments.registry import delta_india_registry
from quantedge.strategy.manual_smc.adapter import ManualSMCAdapter
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_FIRST_TOUCH,
    ENTRY_WINDOW_CANDLES,
    ManualLifecycleEventType,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    ManualSpecConfig,
    manual_smc_production_config,
)
from quantedge.strategy.manual_smc.sizing import MANUAL_SMC_SYMBOLS
from quantedge.strategy.manual_smc.strategy import ManualSMCStrategy

logger = logging.getLogger(__name__)


class ManualSMCWiringError(RuntimeError):
    """Base class for every refusal raised by the wiring layer."""


class TimeframeError(ManualSMCWiringError):
    """An unsupported candle timeframe was requested."""


class CandleBoundaryError(ManualSMCWiringError):
    """A candle timestamp is not a timezone-aware timeframe boundary."""


class FormingCandleError(ManualSMCWiringError):
    """A candle that is not yet closed was offered to the strategy."""


class MalformedCandleError(ManualSMCWiringError):
    """A feed payload is missing fields or is internally inconsistent."""


class UnknownFeedSymbolError(ManualSMCWiringError):
    """A candle arrived for a symbol this runtime does not drive."""


class SymbolNotRegisteredError(ManualSMCWiringError):
    """A symbol is absent from the provenance-verified instrument registry."""


class RuntimeStateError(ManualSMCWiringError):
    """A persisted runtime snapshot is absent, foreign or unreadable."""


# The live feed labels the local product `BTCUSD.P` and the exchange product
# `BTCUSD` (see `market_data.delta_websocket.SYMBOL_LOCAL` / `SYMBOL_EXCHANGE`).
# This is the only symbol rewriting the wiring layer performs.
LOCAL_SYMBOL_SUFFIX = ".P"

SKIPPED_DISABLED = "DISABLED"

# Manual SMC is a 1H strategy (`data_timeframe`) and the feed subscribes to
# `candlestick_1h`. Any other timeframe fails closed rather than guessing.
SUPPORTED_TIMEFRAMES: Mapping[str, int] = {"1h": 3600}

WIRING_STATE_SCHEMA = "MANUAL_SMC_RUNTIME_STATE"
WIRING_STATE_SCHEMA_VERSION = 1

REQUIRED_CANDLE_KEYS: Tuple[str, ...] = (
    "symbol", "timestamp", "open", "high", "low", "close")
PRICE_KEYS: Tuple[str, ...] = ("open", "high", "low", "close")
STATE_KEYS: Tuple[str, ...] = (
    "account_balance", "account_id", "balance_at_fill", "disabled",
    "strategy", "symbols", "timeframe", "wiring_schema",
    "wiring_schema_version")

def timeframe_seconds(timeframe: object) -> int:
    """Seconds in one candle of `timeframe`, or refuse."""
    try:
        seconds = SUPPORTED_TIMEFRAMES[timeframe]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise TimeframeError(
            f"unsupported timeframe {timeframe!r}; Manual SMC production "
            f"wiring supports {sorted(SUPPORTED_TIMEFRAMES)} only") from exc
    return seconds


def bar_index_for(ts: object, timeframe: str = "1h") -> int:
    """The absolute bar index of the candle that opens at `ts`.

    The index is derived from the candle's own clock, so a restart, a gap in
    the feed or a re-subscription can never renumber history.
    """
    seconds = timeframe_seconds(timeframe)
    if not isinstance(ts, datetime):
        raise CandleBoundaryError(
            f"a candle timestamp must be a datetime, got {type(ts).__name__}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise CandleBoundaryError(
            "a candle timestamp must be timezone-aware; a naive timestamp "
            "cannot be placed on the global clock")
    epoch = ts.timestamp()
    if epoch != int(epoch) or int(epoch) % seconds:
        raise CandleBoundaryError(
            f"{ts.isoformat()} is not a {timeframe} boundary")
    return int(epoch) // seconds


def is_candle_closed(candle_ts: object, timeframe: str = "1h") -> bool:
    """Mirror of `market_data.delta_websocket._is_candle_closed`.

    A candle opening at T covers [T, T + timeframe); it is closed only once
    the current period start has moved past T.
    """
    seconds = timeframe_seconds(timeframe)
    if isinstance(candle_ts, datetime):
        if candle_ts.tzinfo is None or candle_ts.utcoffset() is None:
            raise CandleBoundaryError(
                "a naive candle timestamp cannot be tested for closure")
        epoch = int(candle_ts.timestamp())
    else:
        try:
            epoch = int(candle_ts)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise MalformedCandleError(
                f"unusable candle timestamp {candle_ts!r}") from exc
    now_ts = int(datetime.now(timezone.utc).timestamp())
    current_period_start = now_ts - (now_ts % seconds)
    return epoch < current_period_start


def ob_distance(ob: Any, price: float) -> float:
    """Distance from `price` to an order block's zone; zero when inside it."""
    reference = float(price)
    return max(float(ob.ob_bottom) - reference,
               reference - float(ob.ob_top), 0.0)

@dataclass(frozen=True)
class ClosedCandle:
    """One closed candle, already normalised onto the exchange symbol."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class ManualSMCRuntimeStep:
    """Everything one closed candle produced, for logs and for callers.

    `evaluation` and `adaptation` are the frozen strategy's own objects,
    passed through untouched. `scan_result` and `submitted` are populated
    only when the step was pushed through the orchestration boundary.
    """

    symbol: str
    ts: datetime
    bar_idx: Optional[int] = None
    evaluation: Any = None
    adaptation: Any = None
    skipped: Optional[str] = None
    scan_result: Any = None
    submitted: Tuple[str, ...] = ()
    # Task M: the exchange's own answer for every entry order this candle's
    # invalidations asked to cancel. Observability, not a decision input.
    expiries: Tuple[Dict[str, Any], ...] = ()

    @property
    def events(self) -> Tuple[Any, ...]:
        if self.evaluation is None:
            return ()
        return tuple(self.evaluation.events)

    @property
    def ready_decisions(self) -> Tuple[Any, ...]:
        if self.adaptation is None:
            return ()
        return tuple(self.adaptation.ready_decisions)

class ManualSMCRuntime:
    """The production driver for Manual SMC. One instance per process."""

    def __init__(
        self,
        symbols: Optional[Sequence[str]] = None,
        timeframe: Optional[str] = None,
        account_balance: Optional[float] = None,
        orchestrator: Optional[MarketScannerOrchestrator] = None,
        state_path: Optional[Any] = None,
        config: Optional[ManualSpecConfig] = None,
        account_id: str = "DEFAULT",
        strategy: Optional[ManualSMCStrategy] = None,
        disabled: Optional[Iterable[str]] = None,
    ) -> None:
        self.config = config if config is not None else (
            manual_smc_production_config())
        self.timeframe = (timeframe if timeframe is not None
                          else self.config.data_timeframe)
        self.timeframe_secs = timeframe_seconds(self.timeframe)

        resolved = tuple(MANUAL_SMC_SYMBOLS if symbols is None else symbols)
        if not resolved:
            raise SymbolNotRegisteredError(
                "at least one registered instrument must be configured; "
                "an empty pair list cannot be wired")
        registry = delta_india_registry()
        specs: Dict[str, Any] = {}
        for symbol in resolved:
            try:
                specs[symbol] = registry.get(symbol)
            except Exception as exc:                       # noqa: BLE001
                raise SymbolNotRegisteredError(
                    f"{symbol!r} is not a registered Delta India instrument; "
                    f"refusing to invent a tick size") from exc
        self.symbols = resolved
        self.tick_specs = specs

        self.account_id = account_id
        self.state_path = None if state_path is None else Path(state_path)
        self.orchestrator = orchestrator
        self.candles_processed = 0
        self.candles_refused = 0
        self.candles_skipped = 0
        self._disabled = {self._checked(s, resolved)
                          for s in (disabled or ())}

        if strategy is None:
            balance = (self.config.starting_capital
                       if account_balance is None else float(account_balance))
            strategy = ManualSMCStrategy(
                config=self.config,
                assets=list(resolved),
                account_id=account_id,
                account_balance=balance,
                tick_specs=specs,
                activation_mode=ACTIVATION_MODE_FIRST_TOUCH,
                entry_window_candles=ENTRY_WINDOW_CANDLES,
            )
        scanned = set(getattr(strategy.lifecycle, "_scanners", {}))
        if scanned and scanned != set(resolved):
            raise RuntimeStateError(
                f"strategy covers {sorted(scanned)} but the runtime was asked "
                f"to drive {sorted(resolved)}")
        self.strategy = strategy
        self.adapter = ManualSMCAdapter(config=self.config,
                                        timeframe=self.timeframe)
        # Task M: the execution-side feeds this runtime coordinates. None of
        # them influence a strategy decision; they exist so the exchange's own
        # order/position state is observed, protected and reconciled.
        self.private_stream: Any = None
        self.reconciliation_service: Any = None
        self.expiries_cancelled = 0
        self.reconciliations_run = 0

    # ------------------------------------------------ execution-side wiring
    @property
    def lifecycle_manager(self) -> Any:
        """The existing lifecycle manager behind the orchestration boundary."""
        return getattr(self.orchestrator, "lifecycle_manager", None)

    def bind_execution_feeds(
        self,
        private_stream: Any = None,
        reconciliation_service: Any = None,
    ) -> None:
        """Connect the private event stream and REST reconciliation (Task M).

        Wiring only -- every behaviour it enables already exists:

        * the private stream's applied order/fill/position events reach the
          existing trade-lifecycle handlers, so a resting LIMIT that
          fills later gets real exchange-side protection;
        * the closure/rescan flow the orchestrator already owns is what runs
          when the exchange closes a position;
        * reconciliation runs on every successful (re)connection, and can be
          called directly at startup and after a restart.
        """
        manager = self.lifecycle_manager
        if manager is None:
            raise ManualSMCWiringError(
                "execution feeds cannot be bound before an orchestrator with a "
                "lifecycle manager is wired")

        if private_stream is not None:
            self.private_stream = private_stream
            manager.bind_private_stream(private_stream)
            if hasattr(private_stream, "register_reconciliation_hook"):
                private_stream.register_reconciliation_hook(
                    self._reconciliation_hook)

        if reconciliation_service is not None:
            self.reconciliation_service = reconciliation_service

        closure_handler = getattr(
            self.orchestrator, "handle_trade_closure_and_rescan", None)
        if closure_handler is not None:
            manager.register_closure_handler(closure_handler)

        logger.info(
            "MANUAL_SMC execution feeds bound | private_stream=%s "
            "reconciliation_service=%s closure_handler=%s",
            private_stream is not None, reconciliation_service is not None,
            closure_handler is not None)

    async def _reconciliation_hook(self) -> None:
        """Invoked by the private stream on every successful (re)connection."""
        await self.reconcile(account_id=self.account_id)

    async def reconcile(self, account_id: Optional[str] = None,
                        user_id: Optional[str] = None) -> Dict[str, Any]:
        """Run authoritative exchange reconciliation (§M4 A/B/C/D/E/F).

        Safe to call at startup, after a restart, on reconnect, and
        periodically. Returns what was found; unresolved conditions block new
        entries through the lifecycle manager's own fail-closed gate rather
        than through anything this layer decides.
        """
        manager = self.lifecycle_manager
        if manager is None:
            logger.warning("MANUAL_SMC reconcile skipped | reason=NO_LIFECYCLE_MANAGER")
            return {"skipped": "NO_LIFECYCLE_MANAGER"}

        acct = account_id or self.account_id
        report: Dict[str, Any] = {}
        if self.reconciliation_service is not None:
            try:
                account_report = await self.reconciliation_service.reconcile_account(
                    account_id=acct, user_id=user_id)
                report["account"] = account_report
            except Exception as exc:                          # noqa: BLE001
                logger.error("MANUAL_SMC account reconciliation failed | "
                             "account=%s error=%s", acct, exc)
                report["account_error"] = str(exc)

        report["trades"] = await manager.reconcile_active_trades_with_exchange(
            account_id=acct, user_id=user_id)
        self.reconciliations_run += 1
        return report

    def expired_setup_ids(self, step: "ManualSMCRuntimeStep") -> Tuple[str, ...]:
        """Setup ids whose order block the strategy has just invalidated.

        The strategy has no dedicated expiry event: a 3-candle entry window that
        closes without an admitted fill is reported as `INVALIDATED`, exactly as
        a distal breach is. Either way the setup is dead, so any order still
        resting for it must be cancelled on the exchange. The window
        calculation, the invalidation rules and the event stream are read here
        and not altered.

        The id is rebuilt with the adapter's own formula -- application symbol
        (through the adapter's own `symbol_for`, never a guess), timeframe,
        strategy name, `ob_id`, direction -- which is why it is the same id the
        execution engine is tracking for that setup.
        """
        evaluation = step.evaluation
        if evaluation is None or not getattr(evaluation, "events", ()):
            return ()
        ids = []
        for event in evaluation.events:
            if event.event_type is not ManualLifecycleEventType.INVALIDATED:
                continue
            symbol = self.adapter.symbol_for(event.asset)
            ids.append(
                f"{symbol}_{self.timeframe}_{MANUAL_SMC_STRATEGY_NAME}_"
                f"{event.ob_id}_{event.direction}")
        return tuple(ids)

    async def cancel_expired_entries(
        self, step: "ManualSMCRuntimeStep") -> Tuple[Dict[str, Any], ...]:
        """Cancel the REAL exchange order behind every invalidated setup (§M3).

        The exchange decides the outcome, not the expiry: a cancel that races a
        fill and loses is reconciled as a fill by the lifecycle manager, which
        then protects the position. Nothing here treats a requested cancel as a
        completed one.
        """
        manager = self.lifecycle_manager
        if manager is None:
            return ()
        outcomes = []
        for setup_id in self.expired_setup_ids(step):
            if manager.get_active_trade(setup_id) is None:
                continue
            outcome = await manager.expire_resting_entry(
                setup_id, reason="MANUAL_SMC_OB_INVALIDATED")
            outcomes.append(outcome)
            if outcome.get("cancelled"):
                self.expiries_cancelled += 1
            logger.info(
                "MANUAL_SMC ENTRY_EXPIRY | symbol=%s bar=%s setup_id=%s "
                "outcome=%s cancelled=%s",
                step.symbol, step.bar_idx, setup_id,
                outcome.get("outcome"), outcome.get("cancelled"))
        return tuple(outcomes)

    @staticmethod
    def _checked(symbol: str, universe: Sequence[str]) -> str:
        if symbol not in universe:
            raise SymbolNotRegisteredError(
                f"{symbol!r} is not one of this runtime's pairs "
                f"{sorted(universe)}")
        return symbol

    # ------------------------------------------------------------- identity
    @property
    def strategy_name(self) -> str:
        return MANUAL_SMC_STRATEGY_NAME

    @property
    def strategy_version(self) -> str:
        return str(self.strategy.strategy_version)

    def __repr__(self) -> str:                             # pragma: no cover
        return (f"<ManualSMCRuntime {self.timeframe} "
                f"{'/'.join(self.symbols)} "
                f"enabled={'/'.join(self.enabled_symbols)}>")

    # ------------------------------------------- per-pair enable / disable
    # Section 19: disabling a pair is routing only. Its scanner, its order
    # blocks, its consumed origins and its watermark are all left exactly as
    # they were, so re-enabling resumes rather than restarts.
    @property
    def enabled_symbols(self) -> Tuple[str, ...]:
        return tuple(s for s in self.symbols if s not in self._disabled)

    def is_enabled(self, symbol: str) -> bool:
        return self._checked(symbol, self.symbols) not in self._disabled

    def enable(self, symbol: str) -> None:
        self._disabled.discard(self._checked(symbol, self.symbols))
        logger.info("MANUAL_SMC pair enabled | symbol=%s timeframe=%s",
                    symbol, self.timeframe)

    def disable(self, symbol: str) -> None:
        self._disabled.add(self._checked(symbol, self.symbols))
        logger.info("MANUAL_SMC pair disabled (state retained) | symbol=%s "
                    "timeframe=%s live_obs=%d", symbol, self.timeframe,
                    len(self.active_obs(symbol)))

    # ------------------------------------------------- order-block queries
    def active_obs(self, symbol: Optional[str] = None) -> Tuple[Any, ...]:
        """Every retained order block. Never truncated, never evicted."""
        obs: Iterable[Any] = self.strategy.lifecycle.live_obs.values()
        if symbol is not None:
            self._checked(symbol, self.symbols)
            obs = [ob for ob in obs if ob.asset == symbol]
        return tuple(sorted(obs, key=lambda ob: ob.ob_id))

    def nearest_obs(self, symbol: str, price: float,
                    limit: int = 10) -> Tuple[Any, ...]:
        """A *display* projection. Section 16: a view, never a deletion."""
        candidates = self.active_obs(symbol)
        ranked = sorted(candidates,
                        key=lambda ob: (ob_distance(ob, price), ob.ob_id))
        return tuple(ranked if limit is None else ranked[:limit])

    # --------------------------------------------------- the candle boundary
    def resolve_feed_symbol(self, raw: object) -> str:
        """Map a feed symbol onto an exchange symbol this runtime drives.

        No case folding, no fuzzy matching, no substitution: an unrecognised
        symbol is refused (safety rule 15).
        """
        if not isinstance(raw, str) or not raw:
            raise UnknownFeedSymbolError(f"unusable feed symbol {raw!r}")
        symbol = (raw[:-len(LOCAL_SYMBOL_SUFFIX)]
                  if raw.endswith(LOCAL_SYMBOL_SUFFIX) else raw)
        if symbol not in self.symbols:
            raise UnknownFeedSymbolError(
                f"{raw!r} resolves to {symbol!r}, which this runtime does not "
                f"drive; configured pairs are {list(self.symbols)}")
        return symbol

    def closed_candle_from_payload(self, payload: object) -> ClosedCandle:
        """Validate a live feed payload and gate it on closure."""
        if not isinstance(payload, Mapping):
            raise MalformedCandleError(
                f"a candle payload must be a mapping, got "
                f"{type(payload).__name__}")
        missing = [k for k in REQUIRED_CANDLE_KEYS if k not in payload]
        if missing:
            raise MalformedCandleError(
                f"candle payload is missing required fields {missing}")

        prices: Dict[str, float] = {}
        for key in PRICE_KEYS:
            raw = payload[key]
            if raw is None or isinstance(raw, bool):
                raise MalformedCandleError(f"candle field {key!r} is {raw!r}")
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise MalformedCandleError(
                    f"candle field {key!r} is not numeric: {raw!r}") from exc
            if not math.isfinite(value) or value <= 0.0:
                raise MalformedCandleError(
                    f"candle field {key!r} is not a usable price: {raw!r}")
            prices[key] = value
        if (prices["high"] < max(prices["open"], prices["close"])
                or prices["low"] > min(prices["open"], prices["close"])
                or prices["high"] < prices["low"]):
            raise MalformedCandleError(
                f"candle OHLC is internally inconsistent: {prices}")

        declared_tf = payload.get("timeframe")
        if declared_tf is not None and declared_tf != self.timeframe:
            raise MalformedCandleError(
                f"candle timeframe {declared_tf!r} is not this runtime's "
                f"{self.timeframe!r}")

        raw_ts = payload["timestamp"]
        if raw_ts is None or isinstance(raw_ts, bool):
            raise MalformedCandleError(f"candle timestamp is {raw_ts!r}")
        try:
            epoch = int(raw_ts)
        except (TypeError, ValueError) as exc:
            raise MalformedCandleError(
                f"candle timestamp is not an epoch second: {raw_ts!r}"
            ) from exc

        # Section 10: closed candles only. The feed's own flag is necessary
        # but never sufficient -- the wall clock has to agree.
        declared_closed = payload.get("is_closed")
        if declared_closed is not None and not bool(declared_closed):
            raise FormingCandleError(
                f"candle at {epoch} is still forming per the feed")
        if not is_candle_closed(epoch, self.timeframe):
            raise FormingCandleError(
                f"candle at {epoch} has not closed on the wall clock; "
                f"Manual SMC evaluates closed candles only")

        symbol = self.resolve_feed_symbol(payload["symbol"])
        return ClosedCandle(
            symbol=symbol,
            ts=datetime.fromtimestamp(epoch, tz=timezone.utc),
            open=prices["open"], high=prices["high"],
            low=prices["low"], close=prices["close"])

    # ------------------------------------------------------ the strategy call
    def on_closed_candle(self, candle: ClosedCandle) -> ManualSMCRuntimeStep:
        """Route one closed candle into the one shared strategy instance.

        Raises on refusal so no caller can mistake a dropped candle for a
        processed one. Duplicate and out-of-order refusals come straight from
        the strategy's own watermark -- this layer adds no second guarantee
        and weakens none.
        """
        symbol = self.resolve_feed_symbol(candle.symbol)
        bar_idx = bar_index_for(candle.ts, self.timeframe)
        if not is_candle_closed(candle.ts, self.timeframe):
            raise FormingCandleError(
                f"{symbol} candle at {candle.ts.isoformat()} has not closed")

        if symbol in self._disabled:
            self.candles_skipped += 1
            logger.debug("MANUAL_SMC candle skipped | symbol=%s bar=%s "
                         "reason=%s", symbol, bar_idx, SKIPPED_DISABLED)
            return ManualSMCRuntimeStep(symbol=symbol, ts=candle.ts,
                                        bar_idx=bar_idx,
                                        skipped=SKIPPED_DISABLED)

        evaluation = self.strategy.evaluate_closed_candle(
            symbol, bar_idx, candle.ts,
            float(candle.open), float(candle.high),
            float(candle.low), float(candle.close))
        adaptation = self.adapter.adapt(evaluation)
        self.candles_processed += 1
        step = ManualSMCRuntimeStep(
            symbol=symbol, ts=candle.ts, bar_idx=bar_idx,
            evaluation=evaluation, adaptation=adaptation)
        self._log_step(step)
        return step

    def handle_feed_payload(self, payload: object) -> ManualSMCRuntimeStep:
        """Sync entry point matching `delta_websocket.on_candle_closed`."""
        return self.on_closed_candle(self.closed_candle_from_payload(payload))

    # ------------------------------------------- the orchestration boundary
    async def submit(self, step: ManualSMCRuntimeStep, user_id: str,
                     account_id: str) -> ManualSMCRuntimeStep:
        """Hand ready decisions to the existing Path A boundary, unchanged.

        Manual SMC publishes a bracket and a leverage intent and stops there.
        Sizing, contract conversion, validation, the single-trade lock and
        the exchange call all belong to the frozen execution engine beyond
        this call.
        """
        if step.skipped is not None or step.adaptation is None:
            return step
        # Task M §M3: an order block the strategy just invalidated may still
        # have a real order resting on the exchange. Cancel it *before* asking
        # for a new entry, so a confirmed cancellation frees the portfolio slot
        # on this same candle -- and so a cancel that loses the race to a fill
        # is reconciled as a fill (with real protection) instead of being
        # papered over by a new submission.
        step.expiries = await self.cancel_expired_entries(step)
        ready = list(step.adaptation.ready_decisions)
        if not ready:
            return step
        if self.orchestrator is None:
            logger.warning(
                "MANUAL_SMC ready setup not submitted | symbol=%s bar=%s "
                "setups=%s reason=NO_ORCHESTRATION_BOUND",
                step.symbol, step.bar_idx, [d.setup_id for d in ready])
            return step
        result = await self.orchestrator.scan_and_execute(
            account_id=account_id, user_id=user_id, candidate_decisions=ready)
        step.scan_result = result
        if result.rejection_reason is None and result.decision is not None:
            step.submitted = (result.decision.setup_id,)
            logger.info(
                "MANUAL_SMC ENTRY_SUBMITTED | symbol=%s timeframe=%s "
                "setup_id=%s leverage=%s",
                result.qualifying_symbol, self.timeframe,
                result.decision.setup_id, result.decision.calculated_leverage)
        else:
            logger.info(
                "MANUAL_SMC entry refused by execution engine | symbol=%s "
                "bar=%s reason=%s", step.symbol, step.bar_idx,
                result.rejection_reason)
        return step

    async def process_closed_candle(self, candle: ClosedCandle, user_id: str,
                                    account_id: str) -> ManualSMCRuntimeStep:
        step = self.on_closed_candle(candle)
        return await self.submit(step, user_id=user_id, account_id=account_id)

    async def process_feed_payload(self, payload: object, user_id: str,
                                   account_id: str) -> ManualSMCRuntimeStep:
        return await self.process_closed_candle(
            self.closed_candle_from_payload(payload),
            user_id=user_id, account_id=account_id)

    # ---------------------------------------------------------- observability
    def _log_step(self, step: ManualSMCRuntimeStep) -> None:
        """Section 28: one structured line per *eventful* candle, no spam."""
        evaluation = step.evaluation
        if evaluation is None or not evaluation.events:
            return
        for event in evaluation.events:
            logger.info(
                "MANUAL_SMC %s | strategy=%s symbol=%s timeframe=%s bar=%s "
                "ts=%s setup_id=%s",
                event.event_type.name, self.strategy_name, step.symbol,
                self.timeframe, step.bar_idx, step.ts.isoformat(),
                getattr(event, "ob_id", None))
        closed = evaluation.closed
        if closed is not None:
            logger.info(
                "MANUAL_SMC %s | strategy=%s symbol=%s timeframe=%s "
                "setup_id=%s realized_r=%s balance=%s",
                closed.exit.outcome, self.strategy_name, step.symbol,
                self.timeframe, closed.exit.ob_id, closed.exit.realized_r,
                closed.balance_after)

    # -------------------------------------------------------------- snapshots
    def snapshot(self) -> Dict[str, Any]:
        """The minimum envelope around the strategy's own state schema."""
        sizing = self.strategy.open_sizing
        return {
            "wiring_schema": WIRING_STATE_SCHEMA,
            "wiring_schema_version": WIRING_STATE_SCHEMA_VERSION,
            "timeframe": self.timeframe,
            "symbols": list(self.symbols),
            "disabled": sorted(self._disabled),
            "account_id": self.account_id,
            "account_balance": float(self.strategy.account_balance),
            "balance_at_fill": (None if sizing is None
                                else float(sizing.account_balance)),
            "strategy": self.strategy.capture_state(),
        }

    def save_state(self, path: Optional[Any] = None) -> Path:
        """Write the snapshot through a temporary file and one rename.

        `manual_smc.state.PERSISTENCE_IS_ATOMIC` is False for the schema
        itself, so atomicity is this adapter's job: a crash between the write
        and the rename leaves the previous good snapshot untouched.
        """
        target = Path(path) if path is not None else self.state_path
        if target is None:
            raise RuntimeStateError(
                "no snapshot path configured for this runtime")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        body = json.dumps(self.snapshot(), indent=2, sort_keys=True)
        try:
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(target)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:                                # pragma: no cover
                pass
            raise
        logger.info("MANUAL_SMC state saved | path=%s live_obs=%d "
                    "active_trade=%s", target, len(self.active_obs()),
                    self.strategy.has_active_trade())
        return target

    @classmethod
    def load_state(cls, path: Any,
                   orchestrator: Optional[MarketScannerOrchestrator] = None,
                   config: Optional[ManualSpecConfig] = None
                   ) -> "ManualSMCRuntime":
        """Rebuild a runtime that behaves exactly like one that never stopped.

        Every refusal is fail-closed: an unreadable file, a foreign envelope,
        a foreign strategy identity or a config that no longer matches the
        production config all raise rather than silently resuming with the
        wrong behaviour.

        Called by the operator/composition layer, never from inside `src/`:
        `tests/test_final_execution_path_parity_audit.py` pins that no
        production module contains a call site of this name spelled as an
        attribute access, because that is how it proves the execution engine's
        own lock is never restored across a restart. Do not add a production
        caller or a convenience alias here.
        """
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeStateError(f"cannot read snapshot {target}") from exc
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise RuntimeStateError(
                f"snapshot root must be an object, got {type(body).__name__}")
        if sorted(body) != sorted(STATE_KEYS):
            raise RuntimeStateError(
                f"snapshot fields {sorted(body)} do not match the expected "
                f"{sorted(STATE_KEYS)}")
        if body["wiring_schema"] != WIRING_STATE_SCHEMA:
            raise RuntimeStateError(
                f"{body['wiring_schema']!r} is not a Manual SMC runtime "
                f"snapshot")
        if body["wiring_schema_version"] != WIRING_STATE_SCHEMA_VERSION:
            raise RuntimeStateError(
                f"unsupported snapshot version "
                f"{body['wiring_schema_version']!r}")

        expected = config if config is not None else (
            manual_smc_production_config())
        symbols = tuple(body["symbols"])
        registry = delta_india_registry()
        specs: Dict[str, Any] = {}
        for symbol in symbols:
            try:
                specs[symbol] = registry.get(symbol)
            except Exception as exc:                       # noqa: BLE001
                raise SymbolNotRegisteredError(
                    f"snapshot names unregistered instrument {symbol!r}"
                ) from exc
        strategy = ManualSMCStrategy.from_state(
            body["strategy"],
            account_balance=float(body["account_balance"]),
            expected_config=expected,
            tick_specs=specs,
            account_id=str(body["account_id"]),
            restored_balance_at_fill=body["balance_at_fill"])
        runtime = cls(symbols=list(symbols), timeframe=body["timeframe"],
                      orchestrator=orchestrator, state_path=target,
                      config=expected, account_id=str(body["account_id"]),
                      strategy=strategy, disabled=body["disabled"])
        logger.info("MANUAL_SMC state restored | path=%s live_obs=%d "
                    "active_trade=%s disabled=%s", target,
                    len(runtime.active_obs()), strategy.has_active_trade(),
                    sorted(runtime._disabled))
        return runtime


def build_manual_smc_runtime(
    orchestrator: Optional[MarketScannerOrchestrator] = None,
    symbols: Optional[Sequence[str]] = None,
    timeframe: Optional[str] = None,
    account_balance: Optional[float] = None,
    state_path: Optional[Any] = None,
    account_id: str = "DEFAULT",
) -> ManualSMCRuntime:
    """The application's registration point for Manual SMC.

    Building a runtime has no exchange side effects: no socket is opened, no
    order is placed and nothing is scanned until a caller starts feeding it
    closed candles. Pass `orchestrator` to arm the execution boundary; leave
    it out for a read-only observer.
    """
    runtime = ManualSMCRuntime(
        symbols=list(MANUAL_SMC_SYMBOLS) if symbols is None else list(symbols),
        timeframe=timeframe,
        account_balance=account_balance,
        orchestrator=orchestrator,
        state_path=state_path,
        account_id=account_id,
    )
    logger.info(
        "MANUAL_SMC runtime registered | strategy=%s version=%s timeframe=%s "
        "pairs=%s execution_bound=%s",
        runtime.strategy_name, runtime.strategy_version, runtime.timeframe,
        list(runtime.symbols), orchestrator is not None)
    return runtime


def make_feed_callback(runtime: ManualSMCRuntime,
                       submit_to: Optional[Tuple[str, str]] = None):
    """Wrap a runtime as a resilient `on_candle_closed` callback.

    The core is strict -- `on_closed_candle` raises on every refusal. The
    transport edge has to be resilient instead: a refused candle must not
    tear down the websocket loop, so it is counted and logged here. Refusals
    are data, never a reason to abandon the feed.

    `submit_to` is an optional `(user_id, account_id)` pair. When present,
    and only when a running event loop is available, each eventful candle is
    pushed on to the existing orchestration boundary.
    """
    def on_candle_closed(payload: object) -> Optional[ManualSMCRuntimeStep]:
        try:
            step = runtime.handle_feed_payload(payload)
        except Exception as exc:                           # noqa: BLE001
            runtime.candles_refused += 1
            logger.warning(
                "MANUAL_SMC candle refused | strategy=%s error=%s detail=%s",
                runtime.strategy_name, type(exc).__name__, exc)
            return None
        if submit_to is None or step.skipped is not None:
            return step
        user_id, account_id = submit_to
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error(
                "MANUAL_SMC ready setup not submitted | symbol=%s bar=%s "
                "reason=NO_RUNNING_EVENT_LOOP", step.symbol, step.bar_idx)
            return step
        loop.create_task(runtime.submit(step, user_id=user_id,
                                        account_id=account_id))
        return step

    return on_candle_closed


__all__ = [
    "ClosedCandle",
    "ManualSMCRuntime",
    "ManualSMCRuntimeStep",
    "ManualSMCWiringError",
    "CandleBoundaryError",
    "FormingCandleError",
    "MalformedCandleError",
    "RuntimeStateError",
    "SymbolNotRegisteredError",
    "TimeframeError",
    "UnknownFeedSymbolError",
    "LOCAL_SYMBOL_SUFFIX",
    "SKIPPED_DISABLED",
    "SUPPORTED_TIMEFRAMES",
    "WIRING_STATE_SCHEMA",
    "WIRING_STATE_SCHEMA_VERSION",
    "bar_index_for",
    "build_manual_smc_runtime",
    "is_candle_closed",
    "make_feed_callback",
    "ob_distance",
    "timeframe_seconds",
]
