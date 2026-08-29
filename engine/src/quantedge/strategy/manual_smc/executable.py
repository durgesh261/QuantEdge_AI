"""
Manual SMC — Exchange-Executable Baseline (Phase 2, quantization half).
=======================================================================

Step 8 section 11 required that "the ideal strategy baseline and the
exchange-executable baseline must remain distinguishable", and delivered the
first half of that: `backtest.py` REPORTS whether each fill had a valid on-grid
bracket without ever moving a strategy price. What it did not deliver was the
measurement — how far the executable bracket actually sits from the ideal one,
and what that costs in R. This module is that measurement and nothing else.

It is a READER of the Step 8 ledger. It re-runs no candle, re-quantizes no
price, and owns no rule:

    tick grid ...................... quantization.quantize_ob_bracket, called
                                     ONCE, by strategy.py, at fill time. The
                                     resulting `QuantizedBracket` is retained
                                     on `BacktestTrade.quantized_bracket` and
                                     is simply read here.
    risk % / leverage ............. sizing.compute_sl_dist_pct and
                                     sizing.compute_leverage, called with the
                                     on-grid legs instead of the ideal ones.
    R for an outcome .............. sizing.realized_r_for_outcome, called
                                     unchanged on the trade's OWN
                                     `PositionSizing` with only its five
                                     bracket/distance fields re-expressed.
    ledger aggregation ............ backtest.aggregate, called unchanged.
                                     `ExecutableTrade` deliberately exposes
                                     `realized_r` / `is_win` / `is_loss` /
                                     `is_timeout` / `asset`, so the ONE
                                     aggregation function serves both
                                     baselines and the two summaries cannot
                                     drift apart.

WHAT THIS IS A COUNTERFACTUAL ABOUT — AND WHAT IT IS NOT
--------------------------------------------------------
This is a PRICE-LEVEL counterfactual. It holds the realised history fixed —
same order blocks, same fills, same exits, same outcomes, same bar indices —
and asks only: what if the three bracket legs had been the on-grid ones?

It is therefore NOT an executable-mode re-simulation. Moving the entry onto the
tick grid would move the price at which a candle's wick counts as a touch, so a
true executable run could fill on a different bar, or not at all, and exit
somewhere else. Producing that would require a second lifecycle (forbidden — a
second implementation of the shared state machine is exactly the defect class
Step 8 removed) or changed geometry predicates (forbidden — they are frozen and
oracle-equivalent). `TIMING_IS_RESIMULATED` is False and is asserted, so the
boundary of this measurement is stated in code rather than in a caveat nobody
reads. Whether an executable-mode baseline may change fill/exit TIMING is an
open decision, and it is reported as one, not silently resolved.

CAPITAL IS DELIBERATELY NOT RE-COMPOUNDED
-----------------------------------------
The executable leverage differs from the ideal one, so an executable equity
curve would differ from trade one onward — every subsequent notional, fee and
balance would change. That is a sequential re-run, not a post-hoc measurement,
and it would need the driver. R is the comparable metric (and the only
non-degenerate one: both baselines commit the whole balance as margin at up to
100x). So this module reports prices, distances, risk percentages, leverage and
R. It reports no notional, no fee, no PnL, no balance and no return percentage.

ORDER QUANTITY IS STILL ABSENT
------------------------------
`ORDER_QUANTITY_IS_COMPUTED` is False. Delta's contract semantics remain
unverified — `ProductSpecification.contract_value` is an un-overridden
`Decimal("1.0")` default for every symbol — and safety rules #8 and #16 forbid
guessing it. `sizing.resolve_order_quantity` already refuses without a verified
contract value AND an injected converter; this module does not reopen that
door, and it has no quantity field to reopen it with.

DELIBERATELY ABSENT
-------------------
No product-specification table (there are no symbol constants and no default
tick in this file — the tick size arrives inside the recorded bracket, which
came from an injected spec), no exchange call, no HTTP, no database, no order
placement, no amendment, no cancellation, no Java/backend call, no WebSocket,
no runtime composition, no CLI side effect on import.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from quantedge.strategy.manual_smc.backtest import (
    Aggregate,
    BacktestResult,
    BacktestTrade,
    aggregate,
)
from quantedge.strategy.manual_smc.lifecycle import (
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
)
from quantedge.strategy.manual_smc.models import ManualSpecConfig
from quantedge.strategy.manual_smc.quantization import (
    PriceRole,
    QuantizedBracket,
    TickRounding,
)
from quantedge.strategy.manual_smc.sizing import (
    EPS,
    PositionSizing,
    compute_leverage,
    compute_sl_dist_pct,
    realized_r_for_outcome,
)

# ---------------------------------------------------------------------------
# Governance. Stated as code so it cannot be lost in a docstring.
# ---------------------------------------------------------------------------
#: This module measures. It never writes a quantized price back into strategy
#: geometry, an OB record, a trade row or a decision. Flipping this is a
#: behavioural change to a frozen-equivalent strategy and is refused.
APPLIES_TO_STRATEGY_GEOMETRY: bool = False

#: The counterfactual holds fills and exits FIXED. An executable-mode re-run
#: that may fill on a different bar is an unresolved design decision, not a
#: switch to flip here.
TIMING_IS_RESIMULATED: bool = False

#: Contract value is unverified, so quantity is absent (safety rules #8, #16).
ORDER_QUANTITY_IS_COMPUTED: bool = False


class ExecutableBaselineError(RuntimeError):
    """Base class for refusals from the executable-baseline measurement."""


class ExecutableGovernanceError(ExecutableBaselineError):
    """A governance flag was flipped; the measurement refuses to run."""


class ExecutableDataError(ExecutableBaselineError):
    """A ledger row and its retained bracket disagree. Fails closed."""


def _assert_reporting_only() -> None:
    """Every public entry point asserts this, as `backtest.py` does its own."""
    if APPLIES_TO_STRATEGY_GEOMETRY:
        raise ExecutableGovernanceError(
            "governance: APPLIES_TO_STRATEGY_GEOMETRY is set; quantization "
            "must never be applied to Manual SMC geometry (Step 8 section 11)")
    if TIMING_IS_RESIMULATED:
        raise ExecutableGovernanceError(
            "governance: TIMING_IS_RESIMULATED is set, but this module owns no "
            "lifecycle and cannot re-simulate fills; an executable-mode run is "
            "an unresolved design decision")
    if ORDER_QUANTITY_IS_COMPUTED:
        raise ExecutableGovernanceError(
            "governance: ORDER_QUANTITY_IS_COMPUTED is set, but Delta's "
            "contract value is unverified (safety rules #8, #16)")


def _as_float(value: Decimal) -> float:
    """
    The ONE sanctioned Decimal -> float crossing in this package.

    `quantization.price_from_strategy_float` is the crossing in the other
    direction and is representation-only; this is its mirror, and it exists for
    exactly one reason: `sizing`'s R and leverage functions are the shared
    implementations and they are float. Converting here means the R numbers of
    the two baselines are computed by the SAME code and are directly
    comparable. Every PRICE-level fact below stays in exact `Decimal`.
    """
    if not isinstance(value, Decimal):
        raise ExecutableDataError(
            f"expected a Decimal price from the recorded bracket, got "
            f"{type(value).__name__}")
    return float(value)


# ---------------------------------------------------------------------------
# One bracket leg
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LegDivergence:
    """
    One bracket leg, ideal vs on-grid, in exact `Decimal`.

    `rounding` is the direction `quantization.conservative_rounding` chose for
    this (role, direction) pair. It is copied, never re-derived: this module
    must not be able to disagree with the quantizer about which way a leg goes.
    """
    role: PriceRole
    ideal: Decimal
    executable: Decimal
    tick_size: Decimal
    rounding: TickRounding

    @property
    def delta(self) -> Decimal:
        """Signed `executable - ideal`. Zero when the leg was already on-grid."""
        return self.executable - self.ideal

    @property
    def ticks_moved(self) -> Decimal:
        """`|delta| / tick`, exact. Strictly less than 1 for a single snap."""
        return abs(self.delta) / self.tick_size

    @property
    def moved(self) -> bool:
        return self.delta != 0

    @property
    def already_on_grid(self) -> bool:
        return self.delta == 0


# ---------------------------------------------------------------------------
# One trade, re-expressed on the grid
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutableTrade:
    """
    One closed trade with its bracket re-expressed on the exchange tick grid.

    Structurally compatible with `backtest.aggregate`: `realized_r`, `is_win`,
    `is_loss`, `is_timeout` and `asset` are present with the same meanings, so
    the ONE aggregation function summarises both baselines and the ideal and
    executable summaries cannot drift apart in a second implementation.

    `realized_r` is the EXECUTABLE R and is what `aggregate` will read. The
    ideal one is kept beside it under its own name so a reader of a single row
    can never mistake which baseline they are looking at.
    """
    trade_id: int
    asset: str
    direction: str
    ob_id: str
    outcome: str
    is_ambiguous: bool
    tick_size: Decimal

    entry: LegDivergence
    sl: LegDivergence
    tp: LegDivergence

    ideal_risk_dist: Decimal
    ideal_reward_dist: Decimal
    executable_risk_dist: Decimal
    executable_reward_dist: Decimal

    ideal_sl_dist_pct: float
    executable_sl_dist_pct: float
    ideal_theoretical_leverage: float
    executable_theoretical_leverage: float
    ideal_applied_leverage: float
    executable_applied_leverage: float

    ideal_realized_r: float
    #: The executable R. Named plainly so `aggregate` finds it.
    realized_r: float

    #: `ideal_applied_leverage * executable_sl_dist_pct` — what the position
    #: the strategy ACTUALLY sized would risk if its stop sat on the grid.
    budget_used_pct: float
    budget_limit_pct: float

    @property
    def legs(self) -> Tuple[LegDivergence, LegDivergence, LegDivergence]:
        return (self.entry, self.sl, self.tp)

    @property
    def any_leg_moved(self) -> bool:
        return any(leg.moved for leg in self.legs)

    @property
    def legs_moved(self) -> int:
        return sum(1 for leg in self.legs if leg.moved)

    @property
    def max_ticks_moved(self) -> Decimal:
        return max(leg.ticks_moved for leg in self.legs)

    @property
    def risk_shrank(self) -> bool:
        """The Step 4 conservative-rounding guarantee, checked per trade."""
        return self.executable_risk_dist <= self.ideal_risk_dist

    @property
    def budget_respected(self) -> bool:
        """Would the stop still cost at most the configured risk budget?"""
        return self.budget_used_pct <= self.budget_limit_pct + EPS

    @property
    def realized_r_delta(self) -> float:
        """Executable minus ideal. Positive means the grid helped this trade."""
        return self.realized_r - self.ideal_realized_r

    @property
    def is_win(self) -> bool:
        return self.outcome == OUTCOME_TP

    @property
    def is_loss(self) -> bool:
        return self.outcome == OUTCOME_SL

    @property
    def is_timeout(self) -> bool:
        return self.outcome == OUTCOME_TIMEOUT


# ---------------------------------------------------------------------------
# The two baselines, side by side
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutableBaseline:
    """
    The ideal and executable baselines, plus what separates them.

    `ideal_overall` covers the WHOLE closed-trade ledger. `ideal_measured`
    covers only the subset that had a retained on-grid bracket, and is the only
    fair comparand for `executable_measured`: a trade whose quantization was
    refused has no executable form at all, and averaging it into one side but
    not the other would manufacture a divergence that is really a coverage gap.
    """
    strategy_name: str
    strategy_version: str
    config: ManualSpecConfig
    symbols: Tuple[str, ...]

    trades_total: int
    trades_measured: int
    #: Closed trades with no on-grid bracket: no product spec was injected, or
    #: the snap was refused. Named so a thin measurement cannot look complete.
    trades_unmeasurable: int
    unmeasurable_reasons: Tuple[str, ...]

    ideal_overall: Aggregate
    ideal_measured: Aggregate
    executable_measured: Aggregate
    ideal_by_asset: Dict[str, Aggregate]
    executable_by_asset: Dict[str, Aggregate]

    trades_with_any_leg_moved: int
    legs_moved: int
    legs_already_on_grid: int
    max_ticks_moved: Optional[Decimal]
    #: MUST be 0. Conservative rounding guarantees |entry - SL| can only
    #: shrink, and that guarantee is what keeps the leverage `sizing.py`
    #: derived from the UNQUANTIZED distance inside the risk budget.
    risk_grew_count: int
    #: MUST be 0. A breach means a position sized on the ideal stop distance
    #: would risk more than `max_sl_account_risk_pct` with an on-grid stop.
    budget_breach_count: int
    worst_budget_used_pct: float

    trades: Tuple[ExecutableTrade, ...]

    @property
    def total_r_delta(self) -> float:
        """
        Executable total R minus ideal total R, on the measured subset.

        Both operands come from `backtest.aggregate`, which rounds `total_r` to
        2dp exactly as the oracle's `_agg` does. On a small ledger this
        difference-of-roundings can therefore differ from the sum of the
        per-trade `realized_r_delta`s; `expectancy_r_delta` (4dp inputs) is the
        finer figure, and the per-trade deltas are the exact ones.
        """
        return round(self.executable_measured.total_r
                     - self.ideal_measured.total_r, 4)

    @property
    def expectancy_r_delta(self) -> float:
        return round(self.executable_measured.expectancy_r
                     - self.ideal_measured.expectancy_r, 6)

    @property
    def coverage_pct(self) -> float:
        """Share of the closed ledger that has an exchange-valid bracket."""
        return (round(self.trades_measured / self.trades_total * 100.0, 2)
                if self.trades_total else 0.0)

    @property
    def baselines_are_distinguishable(self) -> bool:
        """
        True when the two baselines are actually reported as two things.

        Step 8 section 11's requirement, expressed as something a test can
        assert rather than something a docstring claims.
        """
        return (self.ideal_measured is not self.executable_measured
                and self.trades_measured == self.executable_measured.trades)

    @property
    def guarantees_hold(self) -> bool:
        """The two invariants that must never be violated by a grid snap."""
        return self.risk_grew_count == 0 and self.budget_breach_count == 0


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def _regrid_sizing(
    ideal: PositionSizing,
    bracket: QuantizedBracket,
) -> PositionSizing:
    """
    The trade's OWN `PositionSizing` with its bracket re-expressed on the grid.

    Exactly five fields change — the three prices and the two distances — and
    they are precisely the fields `sizing.realized_r_for_outcome` reads. Every
    other field is the real, recorded one: nothing is fabricated, and no
    capital field is touched, so the returned object cannot be mistaken for a
    sizing decision that was ever made. It is PRIVATE and is never returned
    from this module; it exists so the R rule stays in `sizing.py`.
    """
    if ideal.direction != bracket.direction:
        raise ExecutableDataError(
            f"{ideal.asset}: recorded bracket direction "
            f"{bracket.direction!r} disagrees with the sizing's "
            f"{ideal.direction!r}")
    if ideal.asset != bracket.asset:
        raise ExecutableDataError(
            f"recorded bracket is for {bracket.asset!r} but the sizing is for "
            f"{ideal.asset!r}")
    return replace(
        ideal,
        entry_price=_as_float(bracket.entry_price),
        sl_price=_as_float(bracket.sl_price),
        tp_price=_as_float(bracket.tp_price),
        risk_dist=_as_float(bracket.risk_dist),
        reward_dist=_as_float(bracket.reward_dist),
    )


def _leg(
    role: PriceRole,
    ideal: float,
    executable: Decimal,
    raw: Decimal,
    tick: Decimal,
    rounding: TickRounding,
    asset: str,
) -> LegDivergence:
    """
    Build one leg, cross-checking the bracket's raw price against the ledger.

    `QuantizedBracket` retains the raw Decimal it was given. If that disagrees
    with the float the ledger recorded for the same leg, the two are not
    describing the same trade and the measurement refuses rather than reporting
    a divergence that is really a bookkeeping error.
    """
    if _as_float(raw) != ideal:
        raise ExecutableDataError(
            f"{asset} {role.value}: the recorded bracket's raw price {raw} "
            f"disagrees with the ledger's {ideal!r}; refusing to measure a "
            f"divergence between two different trades")
    return LegDivergence(role=role, ideal=raw, executable=executable,
                         tick_size=tick, rounding=rounding)


def measure_trade(
    trade: BacktestTrade,
    config: ManualSpecConfig,
) -> Optional[ExecutableTrade]:
    """
    One closed trade -> its on-grid form, or None when it has no grid form.

    None means the trade carries no `quantized_bracket`: either no product
    specification was injected for its asset, or the snap was refused. That is
    reported as missing coverage, never filled in with a guessed tick size.
    """
    _assert_reporting_only()
    bracket = trade.quantized_bracket
    if bracket is None:
        return None
    ideal_sizing = trade.sizing_at_fill
    tick = bracket.tick_size

    entry = _leg(PriceRole.ENTRY, trade.entry_price, bracket.entry_price,
                 bracket.raw_entry_price, tick, bracket.entry_rounding,
                 trade.asset)
    sl = _leg(PriceRole.STOP_LOSS, trade.sl_price, bracket.sl_price,
              bracket.raw_sl_price, tick, bracket.sl_rounding, trade.asset)
    tp = _leg(PriceRole.TAKE_PROFIT, trade.tp_price, bracket.tp_price,
              bracket.raw_tp_price, tick, bracket.tp_rounding, trade.asset)

    exec_sizing = _regrid_sizing(ideal_sizing, bracket)
    exec_sl_pct = compute_sl_dist_pct(exec_sizing.entry_price,
                                      exec_sizing.sl_price)
    exec_theo, exec_applied = compute_leverage(exec_sl_pct, config)

    ideal_r = realized_r_for_outcome(trade.outcome, ideal_sizing,
                                    trade.exit_price)
    if abs(ideal_r - trade.realized_r) > EPS * max(1.0, abs(trade.realized_r)):
        raise ExecutableDataError(
            f"{trade.asset} {trade.ob_id}: reconstructing the IDEAL R from the "
            f"recorded sizing gives {ideal_r!r} but the ledger recorded "
            f"{trade.realized_r!r}; the executable comparison would not be "
            f"measuring the same trade")
    exec_r = realized_r_for_outcome(trade.outcome, exec_sizing,
                                    trade.exit_price)

    return ExecutableTrade(
        trade_id=trade.trade_id,
        asset=trade.asset,
        direction=trade.direction,
        ob_id=trade.ob_id,
        outcome=trade.outcome,
        is_ambiguous=trade.is_ambiguous,
        tick_size=tick,
        entry=entry,
        sl=sl,
        tp=tp,
        ideal_risk_dist=abs(entry.ideal - sl.ideal),
        ideal_reward_dist=abs(tp.ideal - entry.ideal),
        executable_risk_dist=bracket.risk_dist,
        executable_reward_dist=bracket.reward_dist,
        ideal_sl_dist_pct=ideal_sizing.sl_dist_pct,
        executable_sl_dist_pct=exec_sl_pct,
        ideal_theoretical_leverage=ideal_sizing.theoretical_leverage,
        executable_theoretical_leverage=exec_theo,
        ideal_applied_leverage=ideal_sizing.applied_leverage,
        executable_applied_leverage=exec_applied,
        ideal_realized_r=ideal_r,
        realized_r=exec_r,
        budget_used_pct=ideal_sizing.applied_leverage * exec_sl_pct,
        budget_limit_pct=config.max_sl_account_risk_pct,
    )


def measure_executable_divergence(
    result: BacktestResult,
) -> ExecutableBaseline:
    """
    Read a Step 8 `BacktestResult` and report both baselines side by side.

    The input is not modified in any way, and neither is anything it points at:
    `BacktestResult`, `BacktestTrade` and `QuantizedBracket` are all frozen, and
    this function only constructs new objects from them. Re-running it on the
    same result yields the same answer, and the ideal numbers it reports are
    literally `result.overall` — asserted, not assumed.
    """
    _assert_reporting_only()
    measured: List[ExecutableTrade] = []
    measured_ideal: List[BacktestTrade] = []
    unmeasurable: List[str] = []

    for trade in result.trades:
        executable = measure_trade(trade, result.config)
        if executable is None:
            unmeasurable.append(
                f"{trade.asset} {trade.ob_id}: "
                f"{trade.quantization_refusal or 'no product specification'}")
            continue
        measured.append(executable)
        measured_ideal.append(trade)

    legs_moved = sum(t.legs_moved for t in measured)
    return ExecutableBaseline(
        strategy_name=result.strategy_name,
        strategy_version=result.strategy_version,
        config=result.config,
        symbols=result.symbols,
        trades_total=len(result.trades),
        trades_measured=len(measured),
        trades_unmeasurable=len(unmeasurable),
        unmeasurable_reasons=tuple(unmeasurable),
        ideal_overall=result.overall,
        ideal_measured=aggregate(measured_ideal),
        executable_measured=aggregate(measured),
        ideal_by_asset={
            symbol: aggregate([t for t in measured_ideal if t.asset == symbol])
            for symbol in result.symbols
        },
        executable_by_asset={
            symbol: aggregate([t for t in measured if t.asset == symbol])
            for symbol in result.symbols
        },
        trades_with_any_leg_moved=sum(1 for t in measured if t.any_leg_moved),
        legs_moved=legs_moved,
        legs_already_on_grid=len(measured) * 3 - legs_moved,
        max_ticks_moved=(max(t.max_ticks_moved for t in measured)
                         if measured else None),
        risk_grew_count=sum(1 for t in measured if not t.risk_shrank),
        budget_breach_count=sum(1 for t in measured if not t.budget_respected),
        worst_budget_used_pct=(max(t.budget_used_pct for t in measured)
                               if measured else 0.0),
        trades=tuple(measured),
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
def _row(cells: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(c.rjust(w) if i else c.ljust(w)
                     for i, (c, w) in enumerate(zip(cells, widths)))


def format_baseline_comparison(baseline: ExecutableBaseline) -> str:
    """
    A plain-text side-by-side of the two baselines. Presentation only.

    It labels both columns explicitly, because a single unlabelled table of
    Manual SMC numbers is exactly how an ideal baseline gets mistaken for an
    executable one. The header carries the counterfactual's boundary so a
    pasted table cannot lose it.
    """
    _assert_reporting_only()
    widths = (10, 8, 8, 9, 10, 8, 10)
    head = ("asset", "trades", "wins", "wr%", "total_r", "pf", "exp_r")
    lines: List[str] = [
        f"MANUAL SMC BASELINES - {baseline.strategy_name} / "
        f"{baseline.strategy_version}",
        f"measured {baseline.trades_measured}/{baseline.trades_total} closed "
        f"trades ({baseline.coverage_pct}% have an exchange-valid bracket)",
        "prices only: fills, exits, outcomes and bar indices are the IDEAL "
        "run's; timing is NOT re-simulated",
        "",
    ]
    for label, per_asset, overall in (
        ("IDEAL (unquantized strategy geometry)",
         baseline.ideal_by_asset, baseline.ideal_measured),
        ("EXCHANGE-EXECUTABLE (bracket legs on the tick grid)",
         baseline.executable_by_asset, baseline.executable_measured),
    ):
        lines.append(label)
        lines.append(_row(head, widths))
        for symbol in baseline.symbols:
            agg = per_asset[symbol]
            lines.append(_row((symbol, str(agg.trades), str(agg.wins),
                               f"{agg.win_rate_pct:.2f}", f"{agg.total_r:+.2f}",
                               f"{agg.profit_factor:.2f}",
                               f"{agg.expectancy_r:+.4f}"), widths))
        lines.append(_row(("TOTAL", str(overall.trades), str(overall.wins),
                           f"{overall.win_rate_pct:.2f}",
                           f"{overall.total_r:+.2f}",
                           f"{overall.profit_factor:.2f}",
                           f"{overall.expectancy_r:+.4f}"), widths))
        lines.append("")
    lines.extend([
        "DIVERGENCE",
        f"  total R          {baseline.total_r_delta:+.4f}",
        f"  expectancy R     {baseline.expectancy_r_delta:+.6f}",
        f"  legs moved       {baseline.legs_moved} of "
        f"{baseline.trades_measured * 3} "
        f"({baseline.trades_with_any_leg_moved} trades affected)",
        f"  max ticks moved  {baseline.max_ticks_moved}",
        f"  risk grew        {baseline.risk_grew_count}  (must be 0)",
        f"  budget breaches  {baseline.budget_breach_count}  (must be 0; "
        f"worst {baseline.worst_budget_used_pct:.4f}% of "
        f"{baseline.config.max_sl_account_risk_pct}%)",
        "",
        "ORDER QUANTITY IS ABSENT: Delta's contract value is unverified "
        "(safety rules #8, #16).",
    ])
    return "\n".join(lines)


__all__ = [
    "APPLIES_TO_STRATEGY_GEOMETRY",
    "TIMING_IS_RESIMULATED",
    "ORDER_QUANTITY_IS_COMPUTED",
    "ExecutableBaselineError",
    "ExecutableGovernanceError",
    "ExecutableDataError",
    "LegDivergence",
    "ExecutableTrade",
    "ExecutableBaseline",
    "measure_trade",
    "measure_executable_divergence",
    "format_baseline_comparison",
]
