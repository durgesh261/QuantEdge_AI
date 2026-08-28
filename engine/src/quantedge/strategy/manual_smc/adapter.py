"""
Manual SMC — Application Boundary (Phase 1 Step 7).
===================================================

THE ONLY translation layer between `ManualSMCStrategy` and the application's
pre-existing `StrategyDecision` / `SetupState` types. Every other module in
this package is deliberately independent of `quantedge.strategy.models`; this
one file is where the two vocabularies meet, so there is exactly one place to
audit when the application representation and the Manual SMC rules disagree.

IT TRANSLATES. IT DECIDES NOTHING.
----------------------------------
No BOS test, no displacement test, no entry test, no invalidation test, no
leverage formula, no fee, no compounding, no lock rule and no tick rounding
appears here. Every value written into a `StrategyDecision` is copied from a
`ManualSMCEvaluation` that `strategy.py` already produced, or is a mapping of
one of its enums. A `ManualSMCAdapter` holds no mutable strategy state, reads
no clock and consults no environment: given the same evaluation it returns the
same decisions.

TAKE PROFIT IS THE ABSOLUTE OB PRICE — `take_profit_target_pct` IS NOT READ
--------------------------------------------------------------------------
`StrategyDecision` carries a `take_profit_target_pct` field whose default,
60.0, is a target RETURN ON MARGIN. Manual SMC's take profit is a 0.60% PRICE
move, and the two coincide only at 100x leverage. This module therefore never
reads that field, never writes it, and never derives a price from it: the
identifier does not occur in the executable source of this file at all. Every
`take_profit` / `take_profit_price` it emits is the OB's absolute price, taken
from the quantized bracket whose raw leg is cross-checked back against
`ManualSMCSetup.tp_price` before the decision is built. `TP_SOURCE` is recorded
in the decision metadata so the provenance survives into the database.

PRICES ARE THE QUANTIZED ONES, VERBATIM — THIS MODULE NEVER ROUNDS
-----------------------------------------------------------------
`entry`, `stop_loss`, `take_profit` and `take_profit_price` are the exact
`Decimal` legs of `ManualSMCSetup.quantized`, copied by reference. There is no
`quantize`, no `ROUND_HALF_UP`, no tick arithmetic and no float price rounding
in this file; `quantization.py` is the only quantizer and it already ran at the
strategy's output boundary. A setup WITHOUT a quantized bracket yields a
decision with NO price fields at all rather than the raw off-grid floats —
fail closed, because an off-grid price is one the exchange must reject and a
`Decimal` in `StrategyDecision.entry` looks executable to every consumer.

TRADE_SETUP_READY IS THE ONLY EXECUTABLE STATE, AND IT IS EARNED
---------------------------------------------------------------
`execution_engine.py`, `validation.py` and `market_orchestrator.py` all gate on
`setup_state == SetupState.TRADE_SETUP_READY`. This module emits it only for an
OB that is LIMIT_RESTING **and** has a quantized bracket **and** whose applied
leverage survives the integer representation check below **and** whose entry the
strategy is not currently refusing:

    * the global trade slot is taken (`evaluation.active_trade` or
      `evaluation.lock_holder` is set) — a second asset's resting limit is
      QUALIFIED, never ready, because exactly one Manual SMC trade may be
      active per account (safety rules #13, #14); and
    * this OB's entry was refused on THIS candle (it appears in
      `evaluation.blocked`) — the boundary must not advertise as ready an entry
      the lifecycle just declined.

Both gates are READ off the evaluation the strategy produced; neither re-derives
the lock rule. Anything that fails any of them maps to a non-executable state,
which is what makes every refusal in this file fail closed at the boundary
rather than downstream.

    ManualOBState.AWAITING_DISPLACEMENT -> SetupState.WATCHING_OB
    ManualOBState.LIMIT_RESTING         -> TRADE_SETUP_READY   (executable)
                                        -> QUALIFIED_LONG / QUALIFIED_SHORT
                                           (bracket, leverage, the trade slot
                                            or this candle's entry refusal)
    an entry the single-trade lock refused
                                        -> QUALIFIED_LONG / QUALIFIED_SHORT
    no live OB at all                   -> SetupState.NO_SETUP

TRADE_ACTIVE, TRADE_CLOSED and INVALIDATED have no mapping and raise: they can
never reach `ManualSMCEvaluation.setups` (the lifecycle's `candidate_obs` emits
only the first two states), and translating one would offer the application a
"setup" for an OB that is already in the market or already dead.

THE INTEGER LEVERAGE GAP — DECLARED, NOT PAPERED OVER
----------------------------------------------------
`StrategyDecision.calculated_leverage` is an `Optional[int]`. Manual SMC's
`applied_leverage` is `min(100, 35 / sl_dist_pct)`, which is virtually never
integral (7.8167x for a 4.478% stop). The field cannot hold it. Leaving it
`None` is NOT the safe choice: `market_orchestrator.py` reads
`decision.calculated_leverage or 10` and `trade_lifecycle.py` reads
`... or 100`, so `None` silently becomes 10x or 100x. This module therefore
writes the FLOOR, matching the application's own `max(1, int(raw_leverage))`
convention, and says so out loud — in `reasons`, in `metadata`
(`applied_leverage` exact, `leverage_truncated_to_int`), and on the adaptation
object (`leverage_truncated_ob_ids`). Flooring only ever lowers the notional,
so the realised risk at the stop stays at or below the 35% budget.

The one case it refuses: an applied leverage below 1x (a stop wider than 35%)
would have to be rounded UP to 1x, which would push the risk ABOVE the budget.
That yields a non-executable decision carrying
`UNREPRESENTABLE_LEVERAGE_REFUSAL`, never a rounded-up 1x.

Choosing a wider representation for leverage is a schema decision for a later
phase, exactly like the `expiresAt` question. This step flags it; it does not
resolve it.

WHAT IT REFUSES TO INVENT
-------------------------
`quantity`, `risk_amount` and `reward_amount` stay `None`: `sizing.py` computes
no order quantity because Delta's contract semantics are unverified (safety
rules #8, #16), and an adapter must not be the place that guesses one.
`order_block` stays `None`: the application's `OrderBlock` is a LuxAlgo
structure object and fabricating one from a `ManualOBRecord` would put invented
fields into `StrategyDecision.ob_zone`. `confidence` stays `None` because
Manual SMC has no confidence model, and `minimum_risk_reward` stays `None`
because it applies no risk/reward threshold — `risk_reward` is emitted as a
diagnostic only. `symbol` is the Manual SMC asset verbatim unless the caller
injects an explicit `symbol_map`; no exchange suffix is ever appended and an
incomplete map fails closed (safety rules #8, #15).

FILLS AND CLOSES PASS THROUGH UNCHANGED
---------------------------------------
`StrategyDecision` is a pre-trade type: it has no filled, closed or settled
state. The application's post-fill vocabulary lives in
`quantedge.execution.trade_lifecycle`, which this package must not import.
So `ManualSMCFill` and `ManualSMCClose` are carried through by reference,
unmodified, for the runtime step that comes later. Invalidated OBs and the
resting orders a fill supersedes are likewise REPORTED (`cancel_ob_ids`,
safety rule #9) — nothing here cancels, amends, places or authorises an order,
and there is no HTTP, exchange client, database, SQL or file I/O in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.quantization import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    InvalidPriceError,
    QuantizedBracket,
    price_from_strategy_float,
)
from quantedge.strategy.manual_smc.strategy import (
    TP_SOURCE,
    ManualSMCBlocked,
    ManualSMCClose,
    ManualSMCEvaluation,
    ManualSMCFill,
    ManualSMCSetup,
)

# The application's own vocabulary. THIS IS THE ONLY MODULE IN THE manual_smc
# PACKAGE PERMITTED TO IMPORT THESE — every sibling stays independent of the
# application, and `test_manual_smc_adapter.py` proves the boundary by AST.
from quantedge.strategy.models import (
    SetupState,
    SetupType,
    StrategyDecision,
    StrategyDirection,
)

#: Recorded in every decision's metadata so the provenance of the take profit
#: survives translation, logging and persistence.
TAKE_PROFIT_SOURCE: str = TP_SOURCE

#: The entry is a resting limit at the 25% level, never a market order. Carried
#: into the metadata because "convert this into a market-entry strategy" is
#: precisely what must not happen at this boundary.
ENTRY_ORDER_TYPE: str = "LIMIT"

#: Approved policy decision: a resting Manual SMC entry has NO time-based
#: expiry. The 72-bar horizon applies to an ACTIVE trade after the fill, not to
#: a resting limit. Recorded so no consumer supplies an expiry by default.
RESTING_ORDER_EXPIRY_POLICY: str = (
    "NO_TIME_BASED_EXPIRY: a resting Manual SMC entry stays valid until it "
    "fills, the OB is invalidated by a distal wick breach, the global trade "
    "lock prevents admission, or an operator cancels it. The 72-bar horizon "
    "applies to an active trade after the fill, never to a resting limit."
)

LEVERAGE_INT_TRUNCATION_NOTE: str = (
    "StrategyDecision.calculated_leverage is an int and Manual SMC's applied "
    "leverage min(100, 35 / sl_dist_pct) is fractional. The floor is written "
    "so no consumer falls back to its own default (10x or 100x); the exact "
    "value is in metadata['applied_leverage']. Flooring lowers the notional, "
    "so risk at the stop stays at or below the 35% budget. Widening the field "
    "is a later schema decision."
)

UNREPRESENTABLE_LEVERAGE_REFUSAL: str = (
    "applied leverage is below 1x, so representing it as an int would mean "
    "rounding UP to 1x and raising the risk at the stop above the 35% budget; "
    "refusing to mark this setup executable"
)

MISSING_BRACKET_REFUSAL: str = (
    "no quantized bracket for this setup, so there are no on-grid prices to "
    "publish; refusing to emit the raw off-grid strategy floats as executable "
    "prices (safety rules #15, #16)"
)

#: The single global trade slot is occupied. Read off `evaluation.active_trade`
#: / `evaluation.lock_holder` — the lock rule itself lives in `portfolio.py` and
#: is not re-derived here.
TRADE_SLOT_TAKEN_REFUSAL: str = (
    "a Manual SMC trade is already active on this account, so the single "
    "global trade slot is taken; this order block stays QUALIFIED and is NOT "
    "marked executable (safety rules #13, #14)"
)

#: This OB appears in `evaluation.blocked`: the lifecycle declined its entry on
#: this very candle. Advertising it as ready on the same candle would contradict
#: the refusal the strategy just issued.
ENTRY_REFUSED_THIS_CANDLE_REFUSAL: str = (
    "the strategy refused this order block's entry on this candle, so the "
    "boundary must not simultaneously advertise it as ready to place"
)

# ---------------------------------------------------------------------------
# Refusals. Translation is pure — nothing has mutated by the time any of these
# raise, so raising is safe here in a way it is not inside `strategy.py`.
# ---------------------------------------------------------------------------
class AdapterError(RuntimeError):
    """Base class for every Manual SMC translation refusal."""


class AdapterConfigError(AdapterError):
    """The adapter itself was constructed without something it must not guess."""


class IdentityMismatchError(AdapterError):
    """
    The payload is not MANUAL_SMC / 1.0.0.

    A LuxAlgo "SMC" / "2.1" evaluation must never be translated by this adapter
    and relabelled as Manual SMC, so identity is checked before anything else.
    """


class UnmappedStateError(AdapterError):
    """A `ManualOBState` this boundary refuses to represent as a setup."""


class UnknownSymbolError(AdapterError):
    """A `symbol_map` was supplied and does not cover this asset."""


class InconsistentEvaluationError(AdapterError):
    """
    The evaluation contradicts itself (or the bracket does not match the setup).

    Cheap to check and load-bearing: a bracket belonging to a different OB would
    put one setup's prices on another setup's decision.
    """


# ---------------------------------------------------------------------------
# Enum mappings. Explicit tables, no fallbacks — an unknown key raises.
# ---------------------------------------------------------------------------
_DIRECTION: Dict[str, StrategyDirection] = {
    DIRECTION_LONG: StrategyDirection.LONG,
    DIRECTION_SHORT: StrategyDirection.SHORT,
}

_SETUP_TYPE: Dict[str, SetupType] = {
    DIRECTION_LONG: SetupType.BULLISH_OB_RETEST,
    DIRECTION_SHORT: SetupType.BEARISH_OB_RETEST,
}

#: The non-executable state a qualified-but-unavailable setup maps to. Both
#: members exist in the application enum and neither is `TRADE_SETUP_READY`,
#: so a consumer gating on readiness refuses them.
_QUALIFIED: Dict[str, SetupState] = {
    DIRECTION_LONG: SetupState.QUALIFIED_LONG,
    DIRECTION_SHORT: SetupState.QUALIFIED_SHORT,
}

#: States that may appear in `ManualSMCEvaluation.setups`. Anything else is a
#: defect upstream, not a translation problem — hence a refusal, not a default.
_MAPPABLE_STATES = (
    ManualOBState.AWAITING_DISPLACEMENT,
    ManualOBState.LIMIT_RESTING,
)

def require_manual_smc_identity(obj: object, where: str) -> None:
    """
    Refuse anything that is not MANUAL_SMC / 1.0.0 (requirement 4).

    Applied to the evaluation and to every setup inside it. `strategy.py` sets
    both fields from `models.py` constants, so this can only fail for an object
    from another strategy — which is exactly what it exists to catch.
    """
    name = getattr(obj, "strategy_name", None)
    version = getattr(obj, "strategy_version", None)
    if name != MANUAL_SMC_STRATEGY_NAME or version != MANUAL_SMC_STRATEGY_VERSION:
        raise IdentityMismatchError(
            f"{where}: expected {MANUAL_SMC_STRATEGY_NAME} / "
            f"{MANUAL_SMC_STRATEGY_VERSION}, got {name!r} / {version!r}; "
            f"refusing to relabel another strategy's output as Manual SMC")


def map_direction(direction: object) -> StrategyDirection:
    """
    "LONG"/"SHORT" -> `StrategyDirection`, preserved exactly (requirement 5).

    Never `StrategyDirection.NONE`: a Manual SMC OB always has a side, and
    erasing it would make a bearish setup indistinguishable from a bullish one
    in the persisted decision. Non-executable states are expressed through
    `setup_state`, which is what every consumer gates on — not through the
    direction.
    """
    try:
        return _DIRECTION[direction]              # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise InconsistentEvaluationError(
            f"direction must be {DIRECTION_LONG!r} or {DIRECTION_SHORT!r}, "
            f"got {direction!r}") from exc


def map_setup_type(direction: str) -> str:
    """The application's setup-type string. Manual SMC is literally an OB retest."""
    try:
        return _SETUP_TYPE[direction].value
    except (KeyError, TypeError) as exc:
        raise InconsistentEvaluationError(
            f"cannot map direction {direction!r} to a setup type") from exc


def map_ob_state(
    state: object, direction: str, executable: bool
) -> SetupState:
    """
    `ManualOBState` -> `SetupState`. `TRADE_SETUP_READY` only when executable.

    `executable` is decided by the caller from the two things this boundary can
    actually check — a quantized bracket and a representable leverage — and is
    never inferred from the OB state alone.
    """
    if state is ManualOBState.AWAITING_DISPLACEMENT:
        return SetupState.WATCHING_OB
    if state is ManualOBState.LIMIT_RESTING:
        if executable:
            return SetupState.TRADE_SETUP_READY
        return _QUALIFIED[direction]
    raise UnmappedStateError(
        f"{state!r} has no setup representation at this boundary; "
        f"only {[s.name for s in _MAPPABLE_STATES]} can appear in "
        f"ManualSMCEvaluation.setups, and translating an OB that is already "
        f"filled, closed or invalidated would offer the application a setup "
        f"for a trade it must not place")

def represent_leverage(applied_leverage: float) -> Tuple[Optional[int], Optional[str]]:
    """
    Fit Manual SMC's fractional leverage into an int field, or refuse.

    Returns `(leverage, note)`. `leverage` is the FLOOR — the application's own
    `max(1, int(raw_leverage))` convention minus the `max(1, ...)`, because
    rounding a sub-1x leverage UP is the one direction that raises risk above
    the 35% budget. `note` is non-`None` whenever information was lost, so the
    loss is always reported and never silent (safety rule #7).

    This is a representation cast, not a sizing computation: `sizing.py` remains
    the only place a leverage is derived, and `metadata['applied_leverage']`
    carries its exact value alongside.
    """
    floored = int(applied_leverage)          # applied_leverage is always > 0
    if floored < 1:
        return None, (
            f"{UNREPRESENTABLE_LEVERAGE_REFUSAL} "
            f"(applied_leverage={applied_leverage!r})")
    if float(floored) != float(applied_leverage):
        return floored, (
            f"calculated_leverage {floored}x is the floor of the Manual SMC "
            f"applied leverage {applied_leverage!r}x. "
            f"{LEVERAGE_INT_TRUNCATION_NOTE}")
    return floored, None


def _check_bracket_matches(setup: ManualSMCSetup, bracket: QuantizedBracket) -> None:
    """
    Prove the bracket belongs to this setup before its prices are published.

    The raw legs are compared through `price_from_strategy_float`, the package's
    single sanctioned float -> Decimal crossing, which converts and rounds
    nothing. The take-profit comparison is the load-bearing one: it establishes
    that the published `take_profit` descends from `ManualOBRecord.tp_price` —
    the absolute OB price — and from nothing else.
    """
    if bracket.asset != setup.asset or bracket.direction != setup.direction:
        raise InconsistentEvaluationError(
            f"{setup.ob_id}: bracket is for {bracket.asset} {bracket.direction} "
            f"but the setup is {setup.asset} {setup.direction}")
    for role, raw_leg, strategy_price in (
            ("entry", bracket.raw_entry_price, setup.entry_price),
            ("stop_loss", bracket.raw_sl_price, setup.sl_price),
            ("take_profit", bracket.raw_tp_price, setup.tp_price)):
        if raw_leg != price_from_strategy_float(strategy_price):
            raise InconsistentEvaluationError(
                f"{setup.ob_id}: the bracket's raw {role} {raw_leg} does not "
                f"match the strategy's {strategy_price!r}; refusing to publish "
                f"prices that do not descend from this setup's own geometry")

def _iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _num(value: Optional[float]) -> Optional[str]:
    """
    Numbers go into metadata as strings.

    `repr` of a float round-trips exactly, and a string cannot be mistaken for
    an executable price the way a `Decimal` in a price field can. Nothing
    downstream is expected to trade on metadata; it is audit information.
    """
    return None if value is None else repr(value)


def _setup_metadata(setup: ManualSMCSetup) -> Dict[str, Any]:
    """
    Audit trail for one setup. Strings, ints and bools only — JSON-safe.

    Everything here is copied from the evaluation. The raw float prices are
    retained (as strings) because `QuantizedBracket` retains its raw legs for
    the same reason: an on-grid price and the geometry it came from must both be
    reconstructable from the record.
    """
    bracket = setup.quantized
    return {
        "strategy_name": MANUAL_SMC_STRATEGY_NAME,
        "strategy_version": MANUAL_SMC_STRATEGY_VERSION,
        "manual_ob_id": setup.ob_id,
        "manual_ob_state": setup.state.value,
        "manual_direction": setup.direction,
        "tp_source": TAKE_PROFIT_SOURCE,
        "take_profit_is_absolute_price": True,
        "entry_order_type": ENTRY_ORDER_TYPE,
        "entry_is_resting_limit": True,
        "resting_order_expiry_policy": RESTING_ORDER_EXPIRY_POLICY,
        "raw_entry_price": _num(setup.entry_price),
        "raw_sl_price": _num(setup.sl_price),
        "raw_tp_price": _num(setup.tp_price),
        "raw_proximal": _num(setup.proximal),
        "raw_distal": _num(setup.distal),
        "raw_ob_top": _num(setup.ob_top),
        "raw_ob_bottom": _num(setup.ob_bottom),
        "raw_ob_width": _num(setup.ob_width),
        "sl_dist_pct": _num(setup.sl_dist_pct),
        "applied_leverage": _num(setup.applied_leverage),
        "theoretical_leverage": _num(setup.theoretical_leverage),
        "origin_bar_idx": setup.origin_bar_idx,
        "bos_bar_idx": setup.bos_bar_idx,
        "bos_dt": _iso(setup.bos_dt),
        "formation_dt": _iso(setup.formation_dt),
        "probe_confirmed": setup.probe_confirmed,
        "displacement_confirmed_bar": setup.displacement_confirmed_bar,
        "limit_active_from_bar": setup.limit_active_from_bar,
        "pre_displacement_touches": setup.pre_displacement_touches,
        "tick_size": None if bracket is None else str(bracket.tick_size),
        "quantization_refusal": setup.quantization_refusal,
        "manual_smc_is_executable": setup.is_executable,
    }

def _magnitude(value: float) -> Optional[Decimal]:
    """
    Exact float -> Decimal for a non-price magnitude (a percentage, a leverage).

    `price_from_strategy_float` is the package's single sanctioned crossing: it
    converts through `Decimal(str(value))` and performs NO rounding. Its
    positive-and-finite check is exactly the fail-closed behaviour wanted here,
    so a degenerate magnitude becomes `None` — an absent field — rather than a
    misleading zero.
    """
    try:
        return price_from_strategy_float(value)
    except InvalidPriceError:
        return None


def _setup_id(symbol: str, timeframe: str, setup: ManualSMCSetup) -> str:
    """
    Deterministic, traceable, and unmistakably Manual SMC.

    The application's `generate_setup_id` needs a LuxAlgo `OrderBlock` and
    cannot be reused. `ob_id` already encodes asset, direction, origin bar and
    BOS bar, so this only adds the symbol, the timeframe and the strategy name —
    which is what makes a Manual SMC setup id distinguishable from an "SMC" one
    in the database (approved identity decision).
    """
    return (f"{symbol}_{timeframe}_{MANUAL_SMC_STRATEGY_NAME}_"
            f"{setup.ob_id}_{setup.direction}")


def _setup_reasons(setup: ManualSMCSetup, state: SetupState) -> List[str]:
    """Factual reasons, every one of them read off the evaluation."""
    reasons = [
        f"{MANUAL_SMC_STRATEGY_NAME} {setup.direction} order block "
        f"{setup.ob_id}",
        f"BOS confirmed at bar {setup.bos_bar_idx} "
        f"({_iso(setup.bos_dt)}); origin bar {setup.origin_bar_idx}",
        f"order block zone [{setup.ob_bottom!r}, {setup.ob_top!r}], "
        f"proximal {setup.proximal!r}, distal {setup.distal!r}",
        f"entry is a resting {ENTRY_ORDER_TYPE} at the 25% level "
        f"{setup.entry_price!r}; stop loss is the distal edge "
        f"{setup.sl_price!r}",
        f"take profit {setup.tp_price!r} is the absolute order-block price "
        f"({TAKE_PROFIT_SOURCE}); no target-return percentage is used",
    ]
    if setup.state is ManualOBState.AWAITING_DISPLACEMENT:
        reasons.append(
            "displacement not yet confirmed, so no entry limit may rest"
            + (" (probe confirmed)" if setup.probe_confirmed else ""))
    else:
        reasons.append(
            f"displacement confirmed at bar "
            f"{setup.displacement_confirmed_bar}; entry limit active from bar "
            f"{setup.limit_active_from_bar} after "
            f"{setup.pre_displacement_touches} pre-displacement touch(es)")
    reasons.append(f"setup_state={state.value}")
    return reasons

def _risk_budget(config: Optional[ManualSpecConfig]) -> Dict[str, Decimal]:
    """
    Carry the config's own risk budget instead of trusting a coinciding default.

    `StrategyDecision.max_loss_pct` defaults to `Decimal("35.0")`, which happens
    to equal `ManualSpecConfig.max_sl_account_risk_pct`. Relying on that
    coincidence would silently misreport a config that changed, so the value is
    copied when a config is supplied and the application default is left alone
    when one is not.
    """
    if config is None:
        return {}
    budget = _magnitude(config.max_sl_account_risk_pct)
    if budget is None:
        raise AdapterConfigError(
            f"config.max_sl_account_risk_pct is "
            f"{config.max_sl_account_risk_pct!r}, which is not a usable risk "
            f"budget; refusing to fall back to the application default")
    return {"max_loss_pct": budget}


def decision_from_setup(
    setup: ManualSMCSetup,
    symbol: str,
    timeframe: str,
    ts: datetime,
    config: Optional[ManualSpecConfig] = None,
    *,
    trade_slot_taken: bool,
    entry_refused_this_candle: bool,
) -> StrategyDecision:
    """
    Translate ONE live OB into the application's `StrategyDecision`.

    Prices are published only when a quantized bracket exists, and they are the
    bracket's `Decimal` legs verbatim. `TRADE_SETUP_READY` additionally requires
    a representable leverage, a free trade slot and no entry refusal on this
    candle. Every field this boundary refuses to invent is left `None` and the
    reason is recorded.

    `trade_slot_taken` and `entry_refused_this_candle` are REQUIRED keyword
    arguments with no defaults: both are facts only the caller can read off the
    evaluation, and a default would decide a safety question (rule #13) on the
    caller's behalf.
    """
    require_manual_smc_identity(setup, f"setup {setup.ob_id}")
    if setup.state not in _MAPPABLE_STATES:
        # Raised by `map_ob_state` below, but checked here too so the refusal
        # message names the state before any field is derived from it.
        map_ob_state(setup.state, setup.direction, False)

    direction = map_direction(setup.direction)
    bracket = setup.quantized
    metadata = _setup_metadata(setup)
    notes: List[str] = []

    leverage: Optional[int] = None
    if bracket is None:
        notes.append(MISSING_BRACKET_REFUSAL)
    else:
        _check_bracket_matches(setup, bracket)
        if setup.state is ManualOBState.LIMIT_RESTING:
            leverage, note = represent_leverage(setup.applied_leverage)
            if note is not None:
                notes.append(note)
    if setup.state is ManualOBState.LIMIT_RESTING:
        if trade_slot_taken:
            notes.append(TRADE_SLOT_TAKEN_REFUSAL)
        if entry_refused_this_candle:
            notes.append(ENTRY_REFUSED_THIS_CANDLE_REFUSAL)

    executable = (bracket is not None and leverage is not None
                  and not trade_slot_taken and not entry_refused_this_candle)
    metadata["leverage_truncated_to_int"] = (
        leverage is not None
        and float(leverage) != float(setup.applied_leverage))
    metadata["trade_slot_taken"] = bool(trade_slot_taken)
    metadata["entry_refused_this_candle"] = bool(entry_refused_this_candle)
    metadata["representation_notes"] = list(notes)
    state = map_ob_state(setup.state, setup.direction, executable)
    reasons = _setup_reasons(setup, state) + list(notes)

    return StrategyDecision(
        timestamp=ts,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        setup_state=state,
        setup_id=_setup_id(symbol, timeframe, setup),
        setup_type=map_setup_type(setup.direction),
        # -- prices: the quantized legs, copied. Absent without a bracket. ----
        entry=None if bracket is None else bracket.entry_price,
        stop_loss=None if bracket is None else bracket.sl_price,
        take_profit=None if bracket is None else bracket.tp_price,
        take_profit_price=None if bracket is None else bracket.tp_price,
        risk_distance=None if bracket is None else bracket.risk_dist,
        reward_distance=None if bracket is None else bracket.reward_dist,
        # Diagnostic only. Manual SMC applies NO risk/reward threshold, so
        # `minimum_risk_reward` stays None and nothing gates on this number.
        risk_reward=(None if bracket is None
                     else bracket.reward_dist / bracket.risk_dist),
        minimum_risk_reward=None,
        order_block_upper_edge=_magnitude(setup.ob_top),
        order_block_lower_edge=_magnitude(setup.ob_bottom),
        stop_distance_pct=_magnitude(setup.sl_dist_pct),
        calculated_leverage=leverage,
        reasons=reasons,
        strategy_name=MANUAL_SMC_STRATEGY_NAME,
        strategy_version=MANUAL_SMC_STRATEGY_VERSION,
        metadata=metadata,
        **_risk_budget(config),
    )

def decision_from_blocked(
    blocked: ManualSMCBlocked,
    symbol: str,
    timeframe: str,
    config: Optional[ManualSpecConfig] = None,
) -> StrategyDecision:
    """
    Translate an entry the single-trade lock refused (requirement 7).

    It maps to `QUALIFIED_LONG` / `QUALIFIED_SHORT` — qualified, and pointedly
    NOT ready — and carries NO price fields at all, because `ManualSMCBlocked`
    carries none: the lifecycle refused the fill, so there is nothing to place.
    Both the lifecycle's own detail and the `PortfolioLock`'s rejection code are
    preserved verbatim so the refusal survives translation (safety rules #13,
    #14).
    """
    direction = map_direction(blocked.direction)
    rejection = blocked.lock_rejection
    code = None if rejection is None else rejection.code.value
    held_by = None if rejection is None or rejection.held_by is None else (
        rejection.held_by)
    reasons = [
        f"{MANUAL_SMC_STRATEGY_NAME} {blocked.direction} order block "
        f"{blocked.ob_id} reached its entry level",
        f"entry refused: {blocked.detail}",
        "exactly one Manual SMC trade may be active per account "
        "(safety rule #13)",
    ]
    if rejection is not None:
        reasons.append(f"portfolio lock rejection {code}: {rejection.detail}")
    return StrategyDecision(
        timestamp=blocked.ts,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        setup_state=_QUALIFIED[blocked.direction],
        setup_id=(f"{symbol}_{timeframe}_{MANUAL_SMC_STRATEGY_NAME}_"
                  f"{blocked.ob_id}_{blocked.direction}"),
        setup_type=map_setup_type(blocked.direction),
        reasons=reasons,
        risk_validation_status=(
            "REJECTED_ONE_TRADE_ACTIVE" if code is None
            else f"REJECTED_{code}"),
        strategy_name=MANUAL_SMC_STRATEGY_NAME,
        strategy_version=MANUAL_SMC_STRATEGY_VERSION,
        metadata={
            "strategy_name": MANUAL_SMC_STRATEGY_NAME,
            "strategy_version": MANUAL_SMC_STRATEGY_VERSION,
            "manual_ob_id": blocked.ob_id,
            "manual_direction": blocked.direction,
            "blocked_by_single_trade_lock": True,
            "lifecycle_detail": blocked.detail,
            "lock_rejection_code": code,
            "lock_rejection_detail":
                None if rejection is None else rejection.detail,
            "lock_held_by_ob_id": None if held_by is None else held_by.ob_id,
            "lock_held_by_asset": None if held_by is None else held_by.asset,
            "lock_held_since":
                None if held_by is None else _iso(held_by.acquired_at),
            "tp_source": TAKE_PROFIT_SOURCE,
        },
        **_risk_budget(config),
    )

def no_setup_decision(
    symbol: str,
    timeframe: str,
    ts: datetime,
    config: Optional[ManualSpecConfig] = None,
) -> StrategyDecision:
    """
    The `NO_SETUP` decision for a candle with no live OB.

    The application's own engine returns a `NO_SETUP` decision rather than
    nothing, so every candle produces a record; this mirrors that. Direction is
    `NONE` because there is no order block to have a side.
    """
    return StrategyDecision(
        timestamp=ts,
        symbol=symbol,
        timeframe=timeframe,
        direction=StrategyDirection.NONE,
        setup_state=SetupState.NO_SETUP,
        setup_type=None,
        reasons=[f"no live {MANUAL_SMC_STRATEGY_NAME} order block for {symbol}"],
        strategy_name=MANUAL_SMC_STRATEGY_NAME,
        strategy_version=MANUAL_SMC_STRATEGY_VERSION,
        metadata={
            "strategy_name": MANUAL_SMC_STRATEGY_NAME,
            "strategy_version": MANUAL_SMC_STRATEGY_VERSION,
            "tp_source": TAKE_PROFIT_SOURCE,
        },
        **_risk_budget(config),
    )


@dataclass(frozen=True)
class ManualSMCAdaptation:
    """
    One candle's evaluation, expressed in the application's vocabulary.

    `decisions` holds one `StrategyDecision` per live OB, in the lifecycle's
    insertion order, or a single `NO_SETUP` decision when there is none.
    `blocked_decisions` is kept SEPARATE so a refused entry can never be
    iterated as if it were a pending setup.

    `filled` and `closed` are the strategy's own objects, unmodified: a
    `StrategyDecision` is a pre-trade type with no filled/closed/settled state,
    and the application's post-fill vocabulary lives in
    `quantedge.execution.trade_lifecycle`, which this package must not import.

    `cancel_ob_ids` is a REPORT (safety rule #9): resting orders for these OBs
    must be withdrawn, either because a distal wick breach invalidated the OB or
    because another OB just took the single trade slot. Nothing here cancels.
    """
    asset: str
    symbol: str
    timeframe: str
    bar_idx: int
    ts: datetime
    decisions: Tuple[StrategyDecision, ...]
    blocked_decisions: Tuple[StrategyDecision, ...]
    invalidated_ob_ids: Tuple[str, ...]
    cancel_ob_ids: Tuple[str, ...]
    filled: Optional[ManualSMCFill]
    closed: Optional[ManualSMCClose]
    leverage_truncated_ob_ids: Tuple[str, ...]
    non_executable_ob_ids: Tuple[str, ...]
    #: The single global trade slot was occupied when this candle was
    #: translated, so nothing here may be marked executable (safety rule #13).
    trade_slot_taken: bool
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def ready_decisions(self) -> Tuple[StrategyDecision, ...]:
        """The only decisions any consumer may act on."""
        return tuple(d for d in self.decisions
                     if d.setup_state is SetupState.TRADE_SETUP_READY)

    @property
    def has_ready_decision(self) -> bool:
        return bool(self.ready_decisions)

class ManualSMCAdapter:
    """
    Stateless translator from `ManualSMCEvaluation` to `StrategyDecision`.

    Holds only translation policy — the timeframe label, the optional
    symbol map and the Manual SMC config whose risk budget is copied into each
    decision. It keeps no candle state, no balance, no lock and no watermark:
    those belong to `ManualSMCStrategy`, and duplicating any of them here would
    create a second, divergent view of the account.

    Construction:
        config      the Manual SMC config. Supplying it copies
                    `max_sl_account_risk_pct` into `max_loss_pct` and supplies
                    the timeframe from `data_timeframe`.
        timeframe   overrides `config.data_timeframe`. One of the two is
                    REQUIRED — the label ends up in the persisted decision and
                    this boundary does not assume "1h".
        symbol_map  asset -> application symbol. Omit it and the Manual SMC
                    asset is used verbatim; supply it and an asset it does not
                    cover fails closed. No exchange suffix, product id or
                    contract semantic is ever inferred (safety rules #8, #15).
    """

    def __init__(
        self,
        config: Optional[ManualSpecConfig] = None,
        timeframe: Optional[str] = None,
        symbol_map: Optional[Mapping[str, str]] = None,
    ) -> None:
        resolved = (timeframe if timeframe is not None
                    else (None if config is None else config.data_timeframe))
        if not isinstance(resolved, str) or not resolved:
            raise AdapterConfigError(
                "a timeframe is required: pass timeframe= or a config carrying "
                "data_timeframe. Refusing to assume one, because the label is "
                "persisted with the decision and identifies the series.")
        self.config: Optional[ManualSpecConfig] = config
        self.timeframe: str = resolved
        self.symbol_map: Optional[Dict[str, str]] = (
            None if symbol_map is None else dict(symbol_map))

    def symbol_for(self, asset: str) -> str:
        """The application symbol for a Manual SMC asset. Never guessed."""
        if self.symbol_map is None:
            return asset
        try:
            return self.symbol_map[asset]
        except KeyError as exc:
            raise UnknownSymbolError(
                f"symbol_map covers {sorted(self.symbol_map)} and not "
                f"{asset!r}; refusing to guess an exchange symbol for it "
                f"(safety rules #8, #15)") from exc

    def adapt(self, evaluation: ManualSMCEvaluation) -> ManualSMCAdaptation:
        """
        Translate one candle's evaluation. Pure: it mutates nothing, anywhere.

        Identity is checked first, on the evaluation and on every setup, fill and
        close inside it, so a payload from another strategy cannot be relabelled
        MANUAL_SMC / 1.0.0 by passing through this method.
        """
        require_manual_smc_identity(evaluation, "evaluation")
        symbol = self.symbol_for(evaluation.asset)

        # Both facts are READ off the evaluation, never re-derived: the lock
        # rule lives in `portfolio.py` and the entry refusal in `lifecycle.py`.
        trade_slot_taken = (evaluation.active_trade is not None
                            or evaluation.lock_holder is not None)
        refused_now = {b.ob_id for b in evaluation.blocked}

        decisions: List[StrategyDecision] = []
        truncated: List[str] = []
        non_executable: List[str] = []
        for setup in evaluation.setups:
            if setup.asset != evaluation.asset:
                raise InconsistentEvaluationError(
                    f"evaluation is for {evaluation.asset} but setup "
                    f"{setup.ob_id} is for {setup.asset}")
            decision = decision_from_setup(
                setup, symbol, self.timeframe, evaluation.ts, self.config,
                trade_slot_taken=trade_slot_taken,
                entry_refused_this_candle=setup.ob_id in refused_now)
            decisions.append(decision)
            if decision.metadata.get("leverage_truncated_to_int"):
                truncated.append(setup.ob_id)
            if (setup.state is ManualOBState.LIMIT_RESTING
                    and decision.setup_state is not SetupState.TRADE_SETUP_READY):
                non_executable.append(setup.ob_id)
        if not decisions:
            decisions.append(no_setup_decision(
                symbol, self.timeframe, evaluation.ts, self.config))

        blocked = tuple(
            decision_from_blocked(b, symbol, self.timeframe, self.config)
            for b in evaluation.blocked)

        if evaluation.filled is not None:
            require_manual_smc_identity(evaluation.filled, "fill")
        if evaluation.closed is not None:
            require_manual_smc_identity(evaluation.closed, "close")

        # Withdrawal report, deduplicated but order-preserving: an invalidated
        # OB and an OB superseded by the fill can be the same OB.
        cancel: List[str] = []
        for ob_id in (tuple(evaluation.invalidated)
                      + (() if evaluation.filled is None
                         else tuple(evaluation.filled.cancel_ob_ids))):
            if ob_id not in cancel:
                cancel.append(ob_id)

        return ManualSMCAdaptation(
            asset=evaluation.asset,
            symbol=symbol,
            timeframe=self.timeframe,
            bar_idx=evaluation.bar_idx,
            ts=evaluation.ts,
            decisions=tuple(decisions),
            blocked_decisions=blocked,
            invalidated_ob_ids=tuple(evaluation.invalidated),
            cancel_ob_ids=tuple(cancel),
            filled=evaluation.filled,
            closed=evaluation.closed,
            leverage_truncated_ob_ids=tuple(truncated),
            non_executable_ob_ids=tuple(non_executable),
            trade_slot_taken=trade_slot_taken,
        )


def to_strategy_decisions(
    evaluation: ManualSMCEvaluation,
    timeframe: Optional[str] = None,
    config: Optional[ManualSpecConfig] = None,
    symbol_map: Optional[Mapping[str, str]] = None,
) -> ManualSMCAdaptation:
    """One-shot convenience wrapper around `ManualSMCAdapter.adapt`."""
    return ManualSMCAdapter(
        config=config, timeframe=timeframe, symbol_map=symbol_map,
    ).adapt(evaluation)


__all__ = [
    "TAKE_PROFIT_SOURCE",
    "ENTRY_ORDER_TYPE",
    "RESTING_ORDER_EXPIRY_POLICY",
    "LEVERAGE_INT_TRUNCATION_NOTE",
    "UNREPRESENTABLE_LEVERAGE_REFUSAL",
    "MISSING_BRACKET_REFUSAL",
    "TRADE_SLOT_TAKEN_REFUSAL",
    "ENTRY_REFUSED_THIS_CANDLE_REFUSAL",
    "AdapterError",
    "AdapterConfigError",
    "IdentityMismatchError",
    "UnmappedStateError",
    "UnknownSymbolError",
    "InconsistentEvaluationError",
    "require_manual_smc_identity",
    "map_direction",
    "map_setup_type",
    "map_ob_state",
    "represent_leverage",
    "decision_from_setup",
    "decision_from_blocked",
    "no_setup_decision",
    "ManualSMCAdaptation",
    "ManualSMCAdapter",
    "to_strategy_decisions",
]
