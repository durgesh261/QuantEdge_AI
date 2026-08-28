"""
Manual SMC — Strategy Orchestration (Phase 1 Step 6).
=====================================================

`ManualSMCStrategy.evaluate_closed_candle()` is the ONE place the approved
Manual SMC building blocks are driven together:

    models.py        identity, config, OB record and its state enum
    geometry.py      OB construction and the wick predicates  (via scanner /
                     lifecycle — this module never calls them directly)
    scanner.py       causal BOS detection                     (via lifecycle)
    lifecycle.py     THE per-candle state machine             (called once)
    portfolio.py     the single globally exclusive trade slot
    sizing.py        leverage, notional, fees, PnL, compounding
    quantization.py  tick-grid snapping, at the OUTPUT boundary only
    state.py         snapshot capture/restore and the candle watermark

This module ADDS NO STRATEGY RULES. It contains no BOS test, no displacement
test, no entry test, no invalidation test, no leverage formula and no lock
rule of its own: every such decision is delegated, unmodified, to the module
that already owns it. What it adds is sequencing, input refusal, cross-checking
between the lifecycle and the lock, and a result object the future adapter can
translate.

CLOSED CANDLES ONLY
-------------------
Every input is a CLOSED candle. There is no intrabar path, no tick handler and
no "current price" argument anywhere in this module. The lifecycle's wick
predicates read `h`/`l` of a finished bar; feeding a forming bar would let a
high that has not happened yet fill an entry.

THE CANDLE ORDER IS LOAD-BEARING — AND IT IS THE LIFECYCLE'S, NOT MINE
----------------------------------------------------------------------
`ManualSMCLifecycle.process_candle` runs, in this fixed order:

    1. resolve the active trade's exit
    2. update every live OB (invalidate / probe / displace / fill)
    3. run the BOS scanner and admit new OBs

Step 3 last is what makes admission break+1 instead of break+2, and step 1
first is what stops a fill and its own exit colliding. This module calls
`process_candle` EXACTLY ONCE per candle and never re-implements or re-orders
those steps. Everything it does itself happens strictly before the call (input
refusal) or strictly after it (lock reconciliation, sizing, quantization,
reporting).

DUPLICATE AND OUT-OF-ORDER CANDLES ARE REFUSED, NOT SKIPPED
-----------------------------------------------------------
A replayed candle would re-run the BOS scan and could re-fill an entry, so it
raises `DuplicateCandleError` BEFORE the lifecycle is touched. The same holds
for a bar index or timestamp that moves backwards within an asset
(`OutOfOrderCandleError`), and for a candle that is older than the last candle
processed for ANY asset (`GlobalOrderError`) — the single trade slot and the
lifecycle's close-timestamp watermark couple the assets, so global order is
part of the result. Equal timestamps across DIFFERENT assets are allowed: four
1h markets close at the same instant.

`CandleWatermark.advance()` remains the authority. The pre-check exists only
because `advance()` mutates, and a mutating check cannot run before the
lifecycle; `advance()` is still called afterwards, so a disagreement between
the two surfaces as an exception rather than as a silently accepted replay.

PERSISTENCE IS NOT ATOMIC AND THIS MODULE DOES NOT PRETEND OTHERWISE
--------------------------------------------------------------------
The lifecycle mutation and the watermark advance are two operations. A crash
between them leaves lifecycle state ahead of the watermark; `state.py` DETECTS
that and refuses to restore it. This module does not add a transaction, a
write-ahead log, a file or a database — `PERSISTENCE_IS_ATOMIC` is `False` and
`unpersisted_strategy_state()` names, explicitly, the strategy-level values the
Step 5 schema does NOT carry (the compounded balance, the sizing captured at
fill, and the process-local lock token). Closing that gap is a later step's
work; hiding it would be worse than the gap.

TAKE PROFIT IS AN ABSOLUTE PRICE — NEVER AN ROE PERCENTAGE
----------------------------------------------------------
Manual SMC's TP is `entry * (1 ∓ 0.006)`, computed by `_make_manual_ob` and
carried as `ManualOBRecord.tp_price`. It must NEVER be re-derived from the
application's `StrategyDecision.take_profit_target_pct`, which is a target
RETURN ON MARGIN (60%), not a price move. The collision is easy to miss
because `ManualSpecConfig.fixed_tp_market_pct` is `0.60` and
`take_profit_target_pct` is `60.0`: the app's number is the Manual SMC number
multiplied by 100x leverage (`gross_tp_return_pct = 0.60 * applied_leverage`).
At any other leverage they disagree, and deriving the price from the ROE figure
would silently move the target. So: no result type in this module has a
percentage-TP field at all, every TP is an absolute price taken from the OB,
and `TP_SOURCE` records that provenance for the adapter.

QUANTIZATION IS AT THE OUTPUT BOUNDARY ONLY
-------------------------------------------
Raw oracle geometry is float and stays float: no `ManualOBRecord` is ever
mutated, and the lifecycle sees only the untouched floats, so oracle
equivalence is unaffected. `quantize_ob_bracket` is applied to a COPY of the
prices when a setup is reported, and its result is carried alongside — never
instead of — the raw values. A tick size must be injected; there is no default
tick and an asset with no product specification yields a NON-executable setup
rather than a guessed grid (safety rules #15, #16).

NO EXECUTION, NO APPLICATION TYPES
----------------------------------
`StrategyDecision` and `SetupState` are deliberately NOT imported: translating
into them is `adapter.py`'s job (Step 7). Nothing here places, amends, cancels
or authorises an order; there is no HTTP, no exchange client, no database, no
SQL, no runtime loop and no file I/O. `ManualSMCFill.cancel_ob_ids` and
`ManualSMCEvaluation.invalidated` merely REPORT that a resting order must be
withdrawn (safety rule #9) — acting on that report belongs to a later step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from quantedge.strategy.manual_smc.lifecycle import (
    ManualActiveTrade,
    ManualLifecycleEvent,
    ManualLifecycleEventType,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.portfolio import (
    LockHolder,
    LockRejection,
    PortfolioLock,
)
from quantedge.strategy.manual_smc.quantization import (
    QuantizationError,
    QuantizedBracket,
    TickSizeSpec,
    quantize_ob_bracket,
)
from quantedge.strategy.manual_smc.sizing import (
    ContractSpecRegistry,
    PositionSizing,
    TradeSettlement,
    settle_trade,
    size_position,
)
from quantedge.strategy.manual_smc.state import (
    CandleWatermark,
    RestoredState,
    StateError,
    capture_state,
    restore_state,
)

#: Provenance marker for every take-profit this module reports. Manual SMC TP
#: is an absolute price from the OB; it is never derived from a target ROE.
TP_SOURCE: str = "ABSOLUTE_OB_TP_PRICE"

#: Stated plainly so no caller can assume otherwise. See the module docstring.
PERSISTENCE_IS_ATOMIC: bool = False

ATOMICITY_NOTE: str = (
    "The lifecycle mutation and the watermark advance are two separate "
    "operations. A crash between them leaves lifecycle state ahead of the "
    "watermark; state.py detects and refuses such a snapshot. This module "
    "provides no transaction, no write-ahead log and no storage."
)

# ---------------------------------------------------------------------------
# Refusals. Every one of them fails closed: no candle is half-processed and no
# non-executable setup is ever reported as executable.
# ---------------------------------------------------------------------------
class StrategyError(RuntimeError):
    """Base class for every Manual SMC orchestration refusal."""


class InvalidCandleError(StrategyError):
    """The candle itself is unusable (bad type, non-finite, or inverted OHLC)."""


class CandleOrderError(StrategyError):
    """The candle arrived out of the order the lifecycle requires."""


class DuplicateCandleError(CandleOrderError):
    """
    This bar has already been processed for this asset.

    Refused rather than skipped: re-running the BOS scan for a consumed origin
    is prevented by the scanner, but the OB update sweep is NOT idempotent — a
    replayed candle can re-touch an entry level and fill a second time.
    """


class OutOfOrderCandleError(CandleOrderError):
    """The bar index or timestamp moved backwards within a single asset."""


class GlobalOrderError(CandleOrderError):
    """
    The candle predates the last candle processed for some OTHER asset.

    The single trade slot and the lifecycle's close-timestamp guard couple the
    assets, so feeding assets out of global order changes which setup wins the
    slot. Equal timestamps on different assets are fine.
    """


class PortfolioLockDesyncError(StrategyError):
    """
    The lifecycle and the `PortfolioLock` disagreed about the trade slot.

    Safety rule #13 — never allow two active trades for one account. Rather
    than choose a winner, this refuses. The watermark is NOT advanced, so the
    candle is replayed on resume instead of being silently accepted.
    """


class TornStateError(StrategyError):
    """
    The lifecycle advanced but the watermark refused to.

    Only reachable if the pre-check and `CandleWatermark.advance()` disagree,
    which is a defect rather than a data condition — hence loud.
    """


class StrategyStateError(StrategyError):
    """Restored or injected strategy-level state is insufficient or unusable."""


# ---------------------------------------------------------------------------
# Input validation. Coercion is never attempted: a caller that hands over the
# wrong type has a bug, and guessing what they meant hides it.
# ---------------------------------------------------------------------------
def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidCandleError(
            f"{where}: expected a non-empty str, got "
            f"{type(value).__name__} {value!r}")
    return value


def _require_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCandleError(
            f"{where}: expected an int, got {type(value).__name__} {value!r}")
    return value


def _require_dt(value: object, where: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidCandleError(
            f"{where}: expected a datetime, got "
            f"{type(value).__name__} {value!r}")
    return value


def _require_price(value: object, where: str) -> float:
    """Finite and strictly positive. NaN would poison every comparison."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidCandleError(
            f"{where}: expected a float price, got "
            f"{type(value).__name__} {value!r}")
    price = float(value)
    if price != price or price in (float("inf"), float("-inf")):
        raise InvalidCandleError(f"{where}: price is not finite ({value!r})")
    if price <= 0.0:
        raise InvalidCandleError(
            f"{where}: price must be strictly positive, got {value!r}")
    return price


def validate_candle(
    asset: object,
    bar_idx: object,
    ts: object,
    o: object,
    h: object,
    l: object,
    c: object,
) -> Tuple[str, int, datetime, float, float, float, float]:
    """
    Validate one closed candle and return it normalised.

    The OHLC consistency check (`low <= open, close <= high`) is the definition
    of a candle, not a strategy rule. An inverted bar would make the wick
    predicates nonsense — a `high` below the `close` can both miss a TP and hit
    an SL on the same bar.
    """
    a = _require_str(asset, "asset")
    idx = _require_int(bar_idx, "bar_idx")
    when = _require_dt(ts, "ts")
    o_f = _require_price(o, "open")
    h_f = _require_price(h, "high")
    l_f = _require_price(l, "low")
    c_f = _require_price(c, "close")
    if h_f < l_f:
        raise InvalidCandleError(
            f"{a} bar {idx}: high {h_f} is below low {l_f}")
    if h_f < max(o_f, c_f) or l_f > min(o_f, c_f):
        raise InvalidCandleError(
            f"{a} bar {idx}: OHLC is inconsistent — require "
            f"low <= open,close <= high, got o={o_f} h={h_f} l={l_f} c={c_f}")
    return a, idx, when, o_f, h_f, l_f, c_f

# ---------------------------------------------------------------------------
# Result types. Strategy-level only — the application's `StrategyDecision` and
# `SetupState` are deliberately NOT imported (that translation is Step 7).
#
# NOTE ON WHAT IS ABSENT: none of these carries a percentage take-profit field.
# There is no `take_profit_target_pct`, no `target_roe_pct`, nothing an adapter
# could mistake for one. Every take profit is `tp_price`, absolute, from the OB.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ManualSMCSetup:
    """
    One live OB, reported so the adapter can place or maintain a resting limit.

    `entry_price` / `sl_price` / `tp_price` are the RAW oracle floats, exactly
    as `_make_manual_ob` computed them. `quantized`, when present, carries the
    on-grid Decimal bracket for the same OB; it is additional information and
    never a replacement.
    """
    asset: str
    ob_id: str
    direction: str
    state: ManualOBState
    entry_price: float
    sl_price: float
    tp_price: float
    proximal: float
    distal: float
    ob_top: float
    ob_bottom: float
    ob_width: float
    risk_dist: float
    reward_dist: float
    sl_dist_pct: float
    theoretical_leverage: float
    applied_leverage: float
    origin_bar_idx: int
    bos_bar_idx: int
    bos_dt: datetime
    formation_dt: datetime
    probe_confirmed: bool
    displacement_confirmed_bar: Optional[int]
    limit_active_from_bar: Optional[int]
    pre_displacement_touches: int
    quantized: Optional[QuantizedBracket]
    quantization_refusal: Optional[str]
    tp_source: str = TP_SOURCE
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def risk_reward(self) -> float:
        """Dimensionless. 0.0 on a degenerate risk distance, never a divide."""
        return (self.reward_dist / self.risk_dist
                if self.risk_dist > 1e-9 else 0.0)

    @property
    def limit_is_live(self) -> bool:
        """True once displacement has confirmed and the limit may rest."""
        return self.state is ManualOBState.LIMIT_RESTING

    @property
    def is_executable(self) -> bool:
        """
        A resting limit AND an on-grid bracket. Both are required.

        Without a quantized bracket there is no price that the exchange would
        accept, and this module refuses to invent a tick size.
        """
        return self.limit_is_live and self.quantized is not None


@dataclass(frozen=True)
class ManualSMCFill:
    """An entry that filled on this candle, sized and quantized."""
    asset: str
    ob_id: str
    direction: str
    bar_idx: int
    ts: datetime
    entry_price: float
    sl_price: float
    tp_price: float
    sizing: PositionSizing
    lock_holder: LockHolder
    quantized: Optional[QuantizedBracket]
    quantization_refusal: Optional[str]
    #: Resting orders for these OBs must be withdrawn now that the slot is
    #: taken. Reported only — this module cancels nothing (safety rule #9).
    cancel_ob_ids: Tuple[str, ...] = ()
    tp_source: str = TP_SOURCE
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def is_executable(self) -> bool:
        return self.quantized is not None


@dataclass(frozen=True)
class ManualSMCClose:
    """A trade that closed on this candle, settled against the balance."""
    asset: str
    ob_id: str
    direction: str
    bar_idx: int
    ts: datetime
    exit: ManualTradeExit
    sizing: PositionSizing
    settlement: TradeSettlement
    balance_before: float
    balance_after: float
    lock_released_token: str
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def outcome(self) -> str:
        return self.exit.outcome

    @property
    def is_ambiguous(self) -> bool:
        """Dual-touch: SL was applied conservatively and the bar is ambiguous."""
        return self.exit.is_ambiguous


@dataclass(frozen=True)
class ManualSMCBlocked:
    """An entry the lifecycle refused because the one trade slot was taken."""
    asset: str
    ob_id: str
    direction: str
    bar_idx: int
    ts: datetime
    detail: str
    lock_rejection: Optional[LockRejection]

@dataclass(frozen=True)
class ManualSMCEvaluation:
    """
    Everything one closed candle produced. Enough for the adapter, and no more.

    `events` is the lifecycle's own diagnostic stream, unfiltered and in order.
    `setups` is the live candidate pool FOR THIS ASSET after the candle. The
    remaining fields are the things an adapter must act on: a fill, a close,
    refused entries, and OBs whose resting orders must be withdrawn.
    """
    asset: str
    bar_idx: int
    ts: datetime
    events: Tuple[ManualLifecycleEvent, ...]
    setups: Tuple[ManualSMCSetup, ...]
    filled: Optional[ManualSMCFill]
    closed: Optional[ManualSMCClose]
    blocked: Tuple[ManualSMCBlocked, ...]
    #: OB ids invalidated on this candle by a distal wick breach. Any resting
    #: order for them must be cancelled (safety rule #9). Reported, not acted on.
    invalidated: Tuple[str, ...]
    active_trade: Optional[ManualActiveTrade]
    lock_holder: Optional[LockHolder]
    account_balance: float
    watermark_advanced: bool
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def has_active_trade(self) -> bool:
        return self.active_trade is not None

    @property
    def executable_setups(self) -> Tuple[ManualSMCSetup, ...]:
        """Resting limits with an on-grid bracket. The rest are informational."""
        return tuple(s for s in self.setups if s.is_executable)


# ---------------------------------------------------------------------------
# The orchestrator.
# ---------------------------------------------------------------------------
class ManualSMCStrategy:
    """
    Drives the approved Manual SMC modules over a stream of closed candles.

    One instance per account. Deterministic: given the same construction
    arguments and the same candle sequence it produces byte-identical results,
    because every value it derives comes from a pure function of the modules
    below it and nothing consults a clock, a random source or the environment.

    Construction arguments:
        config          the Manual SMC config (defaults to `ManualSpecConfig()`)
        assets          pre-create scanners for these assets (order-neutral)
        account_id      identifies the lock, not an exchange account
        account_balance starting balance; defaults to `config.starting_capital`
        tick_specs      asset -> product specification, read ONLY for
                        `tick_size`. Omit it and setups are reported but not
                        executable — there is no default tick (safety rule #15).
        registry        contract registry, consulted solely to fail closed on an
                        unknown symbol. `ContractSpecRegistry.default()` has
                        every contract value UNVERIFIED and is still valid here
                        because USD sizing does not need one.
        lock            an existing `PortfolioLock`, for a caller that owns it.

    NOT thread-safe, exactly like `PortfolioLock`: the authoritative
    cross-process lock is the Postgres lock of a later phase, and a local mutex
    here would imply a distributed guarantee that does not exist.
    """

    def __init__(
        self,
        config: Optional[ManualSpecConfig] = None,
        assets: Optional[List[str]] = None,
        account_id: str = "DEFAULT",
        account_balance: Optional[float] = None,
        tick_specs: Optional[Mapping[str, TickSizeSpec]] = None,
        registry: Optional[ContractSpecRegistry] = None,
        lock: Optional[PortfolioLock] = None,
        lifecycle: Optional[ManualSMCLifecycle] = None,
        watermark: Optional[CandleWatermark] = None,
    ) -> None:
        self.cfg: ManualSpecConfig = config or ManualSpecConfig()
        self.lifecycle: ManualSMCLifecycle = lifecycle or ManualSMCLifecycle(
            config=self.cfg, assets=assets)
        if self.lifecycle.cfg is not self.cfg:
            # A lifecycle built elsewhere must not silently run under a
            # different config than the one this strategy reports.
            if self.lifecycle.cfg != self.cfg:
                raise StrategyStateError(
                    "lifecycle config does not match the strategy config; "
                    "refusing to run two configurations at once")
            self.cfg = self.lifecycle.cfg
        self.watermark: CandleWatermark = (
            watermark if watermark is not None else CandleWatermark())
        self.lock: PortfolioLock = lock or PortfolioLock(account_id=account_id)
        self.account_id: str = self.lock.account_id
        self.tick_specs: Dict[str, TickSizeSpec] = dict(tick_specs or {})
        self.registry: Optional[ContractSpecRegistry] = registry

        balance = (self.cfg.starting_capital if account_balance is None
                   else account_balance)
        self._balance: float = _require_price(balance, "account_balance")
        #: Sizing captured at fill. Needed to settle the trade on close, and
        #: NOT part of the Step 5 snapshot — see `unpersisted_strategy_state`.
        self._open_sizing: Optional[PositionSizing] = None
        self._last_global_ts: Optional[datetime] = None

    # -- introspection ----------------------------------------------------
    @property
    def account_balance(self) -> float:
        """Compounded balance. Updated only by a settled close."""
        return self._balance

    @property
    def open_sizing(self) -> Optional[PositionSizing]:
        return self._open_sizing

    @property
    def strategy_name(self) -> str:
        return MANUAL_SMC_STRATEGY_NAME

    @property
    def strategy_version(self) -> str:
        return MANUAL_SMC_STRATEGY_VERSION

    def has_active_trade(self) -> bool:
        """Delegated. The lifecycle owns the answer; this never re-derives it."""
        return self.lifecycle.has_active_trade()

    def unpersisted_strategy_state(self) -> Dict[str, Any]:
        """
        The strategy-level values the Step 5 snapshot does NOT carry.

        `state.capture_state` persists the lifecycle and the watermark. It does
        not persist the compounded balance, the sizing captured at fill, or the
        lock token — those live here. A caller resuming after a crash must hand
        them back through `from_state`, which REFUSES to guess (see
        `restored_balance_at_fill`). Named rather than hidden, because a silent
        reset to `starting_capital` after a crash would corrupt compounding.
        """
        holder = self.lock.active_trade
        return {
            "account_balance": self._balance,
            "open_sizing_present": self._open_sizing is not None,
            "lock_token": None if holder is None else holder.token,
            "last_global_ts": self._last_global_ts,
            "persistence_is_atomic": PERSISTENCE_IS_ATOMIC,
            "note": ATOMICITY_NOTE,
        }

    # -- candle admission (runs BEFORE the lifecycle is touched) -----------
    def _precheck_candle_order(
        self, asset: str, bar_idx: int, ts: datetime
    ) -> None:
        """
        Refuse a replayed or out-of-order candle without mutating anything.

        This mirrors `CandleWatermark.advance()` deliberately and minimally: a
        mutating check cannot run before the lifecycle, and the lifecycle must
        not be touched by a candle that will then be rejected. `advance()` runs
        afterwards and stays the authority — if the two ever disagreed the
        result would be `TornStateError`, never a silently accepted replay.
        """
        mark = self.watermark.last(asset)
        if mark is not None:
            if bar_idx == mark.bar_idx and ts == mark.ts:
                raise DuplicateCandleError(
                    f"{asset} bar {bar_idx} at {ts.isoformat()} has already "
                    f"been processed; replaying it would re-run the OB update "
                    f"sweep and could fill an entry twice")
            if bar_idx <= mark.bar_idx:
                raise OutOfOrderCandleError(
                    f"{asset} bar {bar_idx} is not after the processed "
                    f"watermark {mark.bar_idx}")
            try:
                regressed = ts <= mark.ts
            except TypeError as exc:       # naive vs tz-aware feed mix
                raise InvalidCandleError(
                    f"{asset}: cannot compare candle timestamp {ts!r} with "
                    f"watermark {mark.ts!r}") from exc
            if regressed:
                raise OutOfOrderCandleError(
                    f"{asset} bar {bar_idx} timestamp {ts.isoformat()} is not "
                    f"after the watermark {mark.ts.isoformat()}")

        if self._last_global_ts is not None:
            try:
                stale = ts < self._last_global_ts
            except TypeError as exc:
                raise InvalidCandleError(
                    f"{asset}: cannot compare candle timestamp {ts!r} with the "
                    f"last processed timestamp {self._last_global_ts!r}") from exc
            if stale:
                raise GlobalOrderError(
                    f"{asset} bar {bar_idx} at {ts.isoformat()} predates the "
                    f"last processed candle {self._last_global_ts.isoformat()}; "
                    f"the single trade slot couples the assets, so global "
                    f"candle order changes which setup wins it")

    # -- quantization (OUTPUT boundary only; never mutates an OB) ----------
    def _quantize(
        self, ob: ManualOBRecord
    ) -> Tuple[Optional[QuantizedBracket], Optional[str]]:
        """
        Snap one OB's bracket onto the exchange grid, or explain the refusal.

        Returns `(bracket, None)` on success and `(None, reason)` otherwise. A
        refusal is returned rather than raised on purpose: this runs AFTER the
        lifecycle has already mutated, and raising here would discard a
        correctly-processed candle. A `None` bracket makes the setup
        non-executable, so nothing can reach an exchange either way.

        The OB is not modified — `quantize_ob_bracket` reads its floats and
        returns a separate `QuantizedBracket`, so raw oracle geometry survives
        untouched.
        """
        spec = self.tick_specs.get(ob.asset)
        if spec is None:
            return None, (
                f"no product specification for {ob.asset}; there is no default "
                f"tick size and refusing to guess one (safety rule #15)")
        try:
            return quantize_ob_bracket(ob, spec), None
        except QuantizationError as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _setup_from_ob(self, ob: ManualOBRecord) -> ManualSMCSetup:
        """Project one live OB into an immutable report. Reads only."""
        bracket, refusal = self._quantize(ob)
        return ManualSMCSetup(
            asset=ob.asset,
            ob_id=ob.ob_id,
            direction=ob.direction,
            state=ob.state,
            entry_price=ob.entry_price,
            sl_price=ob.sl_price,
            tp_price=ob.tp_price,
            proximal=ob.proximal,
            distal=ob.distal,
            ob_top=ob.ob_top,
            ob_bottom=ob.ob_bottom,
            ob_width=ob.ob_width,
            risk_dist=abs(ob.entry_price - ob.sl_price),
            reward_dist=abs(ob.tp_price - ob.entry_price),
            sl_dist_pct=ob.sl_dist_pct,
            theoretical_leverage=ob.theoretical_leverage,
            applied_leverage=ob.applied_leverage,
            origin_bar_idx=ob.origin_bar_idx,
            bos_bar_idx=ob.bos_bar_idx,
            bos_dt=ob.bos_dt,
            formation_dt=ob.formation_dt,
            probe_confirmed=ob.probe_confirmed,
            displacement_confirmed_bar=ob.displacement_confirmed_bar,
            limit_active_from_bar=ob.limit_active_from_bar,
            pre_displacement_touches=ob.pre_displacement_touches,
            quantized=bracket,
            quantization_refusal=refusal,
        )

    # -- the single public entry point -------------------------------------
    def evaluate_closed_candle(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
    ) -> ManualSMCEvaluation:
        """
        Evaluate ONE closed candle and report what it produced.

        Sequence — and nothing in it may be reordered:

            1. validate the candle (types, finiteness, OHLC consistency);
            2. refuse a duplicate / out-of-order / globally stale candle;
            3. `lifecycle.process_candle()` — called exactly once, which is
               where the load-bearing resolve → update → scan order lives;
            4. reconcile the `PortfolioLock`, size the fill, settle the close,
               in the lifecycle's own event order (a release always precedes
               the acquire it enables);
            5. advance the watermark — a SEPARATE operation, deliberately not
               presented as atomic with step 3;
            6. project the live pool into an immutable report, quantizing at
               that boundary only.

        Raises rather than returning a degraded result for anything that would
        make an order unsafe: a bad candle, a replay, or a lock that disagrees
        with the lifecycle. A quantization refusal is the one exception — it is
        reported per setup, because it cannot make an order unsafe, only absent.
        """
        asset, bar_idx, ts, o, h, l, c = validate_candle(
            asset, bar_idx, ts, o, h, l, c)
        self._precheck_candle_order(asset, bar_idx, ts)

        events = tuple(
            self.lifecycle.process_candle(asset, bar_idx, ts, o, h, l, c))

        filled: Optional[ManualSMCFill] = None
        closed: Optional[ManualSMCClose] = None
        blocked: List[ManualSMCBlocked] = []
        invalidated: List[str] = []

        for event in events:
            kind = event.event_type
            if kind is ManualLifecycleEventType.TRADE_CLOSED:
                closed = self._on_trade_closed(event, asset, bar_idx, ts)
            elif kind is ManualLifecycleEventType.ENTRY_FILLED:
                filled = self._on_entry_filled(event, asset, bar_idx, ts)
            elif kind is ManualLifecycleEventType.ENTRY_BLOCKED_BY_ACTIVE_TRADE:
                blocked.append(self._on_entry_blocked(event, bar_idx, ts))
            elif kind is ManualLifecycleEventType.INVALIDATED:
                invalidated.append(event.ob_id)

        watermark_advanced = self._advance_watermark(asset, bar_idx, ts)
        self._last_global_ts = ts

        setups = tuple(self._setup_from_ob(ob)
                       for ob in self.lifecycle.candidate_obs(asset))
        return ManualSMCEvaluation(
            asset=asset,
            bar_idx=bar_idx,
            ts=ts,
            events=events,
            setups=setups,
            filled=filled,
            closed=closed,
            blocked=tuple(blocked),
            invalidated=tuple(invalidated),
            active_trade=self.lifecycle.active_trade,
            lock_holder=self.lock.active_trade,
            account_balance=self._balance,
            watermark_advanced=watermark_advanced,
        )

    # -- per-event reconciliation ------------------------------------------
    def _on_trade_closed(
        self,
        event: ManualLifecycleEvent,
        asset: str,
        bar_idx: int,
        ts: datetime,
    ) -> ManualSMCClose:
        """
        Settle the closed trade and release the slot.

        The lock is released with the holder's own token AND the exit's terminal
        outcome, which is what `PortfolioLock.release` demands (safety rule
        #14). The exit record is the lifecycle's, unmodified; the capital
        arithmetic is `sizing.settle_trade`'s, unmodified.
        """
        exit_record = self.lifecycle.exits[-1] if self.lifecycle.exits else None
        if exit_record is None or exit_record.ob_id != event.ob_id:
            raise StrategyStateError(
                f"{asset} bar {bar_idx}: lifecycle reported TRADE_CLOSED for "
                f"{event.ob_id!r} but the last exit record is "
                f"{None if exit_record is None else exit_record.ob_id!r}")
        sizing = self._open_sizing
        if sizing is None:
            raise StrategyStateError(
                f"{asset} bar {bar_idx}: {event.ob_id} closed but no sizing was "
                f"captured at fill, so the trade cannot be settled. After a "
                f"restore, pass restored_balance_at_fill to from_state().")

        holder = self.lock.active_trade
        if holder is None or holder.ob_id != event.ob_id:
            raise PortfolioLockDesyncError(
                f"{asset} bar {bar_idx}: {event.ob_id} closed but the lock is "
                f"held by {None if holder is None else holder.ob_id!r}")

        settlement = settle_trade(sizing, exit_record.outcome,
                                  exit_record.exit_price)
        balance_before = self._balance
        self._balance = settlement.ending_balance
        self._open_sizing = None
        self.lock.release(holder.token, ts, exit_record.outcome)
        return ManualSMCClose(
            asset=asset,
            ob_id=event.ob_id,
            direction=event.direction,
            bar_idx=bar_idx,
            ts=ts,
            exit=exit_record,
            sizing=sizing,
            settlement=settlement,
            balance_before=balance_before,
            balance_after=self._balance,
            lock_released_token=holder.token,
        )

    def _on_entry_filled(
        self,
        event: ManualLifecycleEvent,
        asset: str,
        bar_idx: int,
        ts: datetime,
    ) -> ManualSMCFill:
        """
        Size the fill, take the lock, and quantize the bracket.

        The lock MUST grant here: the lifecycle only fills when its own gate
        allows it, and `PortfolioLock.evaluate` mirrors that gate expression for
        expression. A rejection therefore means the two views of the account
        have diverged — most plausibly because something outside this strategy
        touched the lock — and that is refused rather than reconciled
        (safety rule #13).
        """
        trade = self.lifecycle.active_trade
        if trade is None or trade.ob.ob_id != event.ob_id:
            raise StrategyStateError(
                f"{asset} bar {bar_idx}: lifecycle reported ENTRY_FILLED for "
                f"{event.ob_id!r} but active_trade is "
                f"{None if trade is None else trade.ob.ob_id!r}")

        decision = self.lock.try_acquire(
            asset=asset, ob_id=event.ob_id, direction=trade.direction,
            ts=ts, bar_idx=bar_idx)
        if isinstance(decision, LockRejection):
            raise PortfolioLockDesyncError(
                f"{asset} bar {bar_idx}: the lifecycle filled {event.ob_id} but "
                f"the portfolio lock refused it "
                f"({decision.code.value}: {decision.detail}). Refusing to run "
                f"with two disagreeing views of the single trade slot.")

        sizing = size_position(trade.ob, self._balance, self.cfg, self.registry)
        self._open_sizing = sizing
        bracket, refusal = self._quantize(trade.ob)

        cancel_ids = tuple(
            ob.ob_id for ob in self.lifecycle.candidate_obs()
            if ob.state is ManualOBState.LIMIT_RESTING
            and ob.ob_id != event.ob_id)
        return ManualSMCFill(
            asset=asset,
            ob_id=event.ob_id,
            direction=trade.direction,
            bar_idx=bar_idx,
            ts=ts,
            entry_price=trade.entry_price,
            sl_price=trade.sl_price,
            tp_price=trade.tp_price,
            sizing=sizing,
            lock_holder=decision,
            quantized=bracket,
            quantization_refusal=refusal,
            cancel_ob_ids=cancel_ids,
        )

    def _on_entry_blocked(
        self, event: ManualLifecycleEvent, bar_idx: int, ts: datetime
    ) -> ManualSMCBlocked:
        """
        Record an entry the lifecycle refused, and confirm the lock agrees.

        If the lock reports the slot as FREE while the lifecycle just refused an
        entry for lack of it, the two have diverged and continuing could place a
        second position. Refused (safety rule #13).
        """
        rejection = self.lock.evaluate(ts)
        if rejection is None:
            raise PortfolioLockDesyncError(
                f"{event.asset} bar {bar_idx}: the lifecycle blocked "
                f"{event.ob_id} for want of the trade slot, but the portfolio "
                f"lock reports the slot as available")
        return ManualSMCBlocked(
            asset=event.asset,
            ob_id=event.ob_id,
            direction=event.direction,
            bar_idx=bar_idx,
            ts=ts,
            detail=event.detail,
            lock_rejection=rejection,
        )

    def _advance_watermark(
        self, asset: str, bar_idx: int, ts: datetime
    ) -> bool:
        """
        Advance the processed-candle marker. A SEPARATE step 3, on purpose.

        `_precheck_candle_order` has already established that this candle is
        admissible, so `advance()` can only refuse if the two disagree — a
        defect, not a data condition. It is reported as `TornStateError` because
        at that point the lifecycle HAS mutated while the watermark has not, and
        that is exactly the non-atomic window this module refuses to paper over.
        """
        try:
            self.watermark.advance(asset, bar_idx, ts)
        except StateError as exc:
            raise TornStateError(
                f"{asset} bar {bar_idx}: the lifecycle processed this candle "
                f"but the watermark refused to advance ({exc}). State is now "
                f"ahead of the watermark; a snapshot taken here is torn and "
                f"state.py will refuse it. {ATOMICITY_NOTE}") from exc
        return True

    # -- snapshot / resume -------------------------------------------------
    def capture_state(self) -> Dict[str, Any]:
        """
        Delegate to `state.capture_state`. Valid only BETWEEN candles.

        This returns a dict; it writes nothing. It also does NOT include the
        strategy-level values listed by `unpersisted_strategy_state()` — the
        Step 5 schema does not define fields for them and this step must not
        invent any. A caller that stores this snapshot must store those values
        alongside it and hand them back to `from_state`.
        """
        return capture_state(self.lifecycle, self.watermark)

    @classmethod
    def from_state(
        cls,
        payload: object,
        account_balance: float,
        expected_config: Optional[ManualSpecConfig] = None,
        tick_specs: Optional[Mapping[str, TickSizeSpec]] = None,
        registry: Optional[ContractSpecRegistry] = None,
        account_id: str = "DEFAULT",
        restored_balance_at_fill: Optional[float] = None,
    ) -> "ManualSMCStrategy":
        """
        Rebuild a strategy from a Step 5 snapshot and resume evaluating.

        `state.restore_state` does the reconstruction and all of the fail-closed
        validation; this adds only the strategy-level values the schema does not
        carry:

          * `account_balance` — the compounded balance. Required: defaulting to
            `starting_capital` after a crash would silently reset compounding.
          * `restored_balance_at_fill` — REQUIRED when the snapshot contains an
            active trade, and rejected when it does not. Sizing is a pure
            function of (OB, balance-at-fill, config), so supplying the balance
            as of the fill reproduces the original `PositionSizing` exactly;
            guessing it would misstate the PnL of a trade already in the market.

        The lock is re-acquired for the restored active trade so the slot is
        occupied from the first candle after resume (safety rule #13). The new
        token differs from the pre-crash one: tokens are process-local, and the
        authoritative cross-process lock is a later phase's Postgres lock.
        """
        restored: RestoredState = restore_state(
            payload, expected_config=expected_config)
        strategy = cls(
            config=restored.config,
            account_id=account_id,
            account_balance=account_balance,
            tick_specs=tick_specs,
            registry=registry,
            lifecycle=restored.lifecycle,
            watermark=restored.watermark,
        )

        trade = restored.lifecycle.active_trade
        if trade is None:
            if restored_balance_at_fill is not None:
                raise StrategyStateError(
                    "restored_balance_at_fill was supplied but the snapshot "
                    "holds no active trade; refusing to size a trade that does "
                    "not exist")
        else:
            if restored_balance_at_fill is None:
                raise StrategyStateError(
                    f"the snapshot holds an active trade on {trade.asset} "
                    f"({trade.ob.ob_id}) filled at "
                    f"{trade.fill_dt.isoformat()}, so the balance AS OF THAT "
                    f"FILL is required to reproduce its sizing. Refusing to "
                    f"guess it — the trade's PnL would be wrong.")
            strategy._open_sizing = size_position(
                trade.ob,
                _require_price(restored_balance_at_fill,
                               "restored_balance_at_fill"),
                restored.config,
                registry,
            )
            strategy.lock.acquire(
                asset=trade.asset, ob_id=trade.ob.ob_id,
                direction=trade.direction, ts=trade.fill_dt,
                bar_idx=trade.fill_bar_idx)

        marks = [restored.watermark.last(a)
                 for a in restored.watermark.assets()]
        timestamps = [m.ts for m in marks if m is not None]
        strategy._last_global_ts = max(timestamps) if timestamps else None
        return strategy


__all__ = [
    "TP_SOURCE",
    "PERSISTENCE_IS_ATOMIC",
    "ATOMICITY_NOTE",
    "StrategyError",
    "InvalidCandleError",
    "CandleOrderError",
    "DuplicateCandleError",
    "OutOfOrderCandleError",
    "GlobalOrderError",
    "PortfolioLockDesyncError",
    "TornStateError",
    "StrategyStateError",
    "validate_candle",
    "ManualSMCSetup",
    "ManualSMCFill",
    "ManualSMCClose",
    "ManualSMCBlocked",
    "ManualSMCEvaluation",
    "ManualSMCStrategy",
]
