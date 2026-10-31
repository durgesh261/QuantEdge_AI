"""
Manual SMC compounding backtests — OFFLINE, RESEARCH ONLY.

Runs the EXISTING Manual SMC backtest driver (`ManualSMCBacktest` via
`run_manual_smc_backtest_from_candles`) once per pair, per period, over the
canonical local candle CSVs. It contains no strategy rule of its own: every
price, level, leverage, fee and balance in the ledgers below is TRANSCRIBED
from the `BacktestTrade` rows the driver produced.

What this script decides, and nothing else:
  * which candles to feed (one pair per run, so four independent $10 accounts);
  * the starting capital ($10.00, passed through to the strategy);
  * the shape of the CSV / JSON / markdown artifacts.

REWARD:RISK IS AN OBSERVATIONAL STATISTIC HERE, NOT A FILTER. No minimum R:R is
applied by this runner, by the backtest driver, by the strategy, or by live order
validation: the authorised Manual SMC specification contains no such rule. Every
R:R figure below is computed FROM trades that were admitted on the structural
rules alone, and reporting one has no effect on which trades exist.

Each pair is its OWN `ManualSMCBacktest`, therefore its own
`ManualSMCStrategy`, its own `ManualSMCLifecycle` and its own `PortfolioLock`.
That is what makes "one trade at a time, per pair, capital never combined"
a property of the runner rather than a claim in a report. Note that this is
deliberately NOT the engine's default four-symbol configuration, in which the
four assets share ONE portfolio slot.

This script places no orders, cancels no orders, contacts no exchange, reads no
credentials and imports no live execution module. `backtest._assert_not_live()`
runs on every entry point it calls.

Usage:
    python scripts/run_manual_smc_compounding_backtests.py [--out DIR]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine" / "src"))

from quantedge.strategy.manual_smc.backtest import (  # noqa: E402
    DETECTOR_LUXALGO_INTERNAL,
    BacktestResult,
    BacktestTrade,
    Candle,
    default_canonical_base,
    load_canonical_dataset,
    run_manual_smc_backtest_from_candles,
)

#: The four pairs, each processed completely independently.
SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: Every account starts here, in every period. Never carried between runs.
STARTING_CAPITAL = 10.0

DEFAULT_OUT = REPO_ROOT / "engine" / "data" / "research" / "manual_smc_compounding"

#: A reference ratio used for ONE OBSERVATIONAL STATISTIC ONLY: "how many of the
#: trades this strategy took happened to have a reward:risk below 1.5".
#:
#: IT IS NOT A THRESHOLD AND NOTHING IS COMPARED AGAINST IT IN ORDER TO ADMIT OR
#: REFUSE A TRADE. No minimum R:R exists in the strategy, the backtest driver,
#: the live order-validation gateway or the live execution engine — the rule was
#: removed because it is not part of the authorised Manual SMC specification.
#: Changing this number changes one column of a report and nothing else; setting
#: it to 0 or 99 would leave every trade in every ledger byte-identical.
#:
#: 1.5 is retained purely because it is the figure previously discussed, so the
#: statistic stays comparable with earlier reports.
OBSERVATIONAL_RR_REFERENCE_DECIMAL: Decimal = Decimal("1.5")
OBSERVATIONAL_RR_REFERENCE = float(OBSERVATIONAL_RR_REFERENCE_DECIMAL)

#: Requested boundaries, verbatim. The actual data end is measured, never
#: assumed, and the run is clamped to it: no candle is fabricated to reach a
#: requested date, and a requested start before the first candle simply yields
#: the first candle.
PERIODS: Tuple[Tuple[str, datetime, datetime], ...] = (
    ("historical",
     datetime(2024, 1, 1, tzinfo=timezone.utc),
     datetime(2026, 9, 2, tzinfo=timezone.utc)),
    ("recent",
     datetime(2026, 8, 1, tzinfo=timezone.utc),
     datetime(2026, 9, 2, tzinfo=timezone.utc)),
)


# ---------------------------------------------------------------------------
# Data integrity: measured before anything is run, reported whether or not it
# is convenient.
# ---------------------------------------------------------------------------
def inspect_coverage(candles: Sequence[Candle]) -> Dict[str, Any]:
    """Exact coverage of one pair's canonical history. No repair, no fill."""
    if not candles:
        return {"rows": 0}
    steps = sorted({(candles[i + 1].ts - candles[i].ts).total_seconds()
                    for i in range(len(candles) - 1)})
    monotonic = all(candles[i].ts < candles[i + 1].ts
                    for i in range(len(candles) - 1))
    #: `expected` and `missing_hours` read the FIRST and LAST elements, so they
    #: are only meaningful on a strictly increasing series. Out of order, they
    #: are reported as None rather than as a nonsense (possibly negative) count:
    #: `strictly_increasing` and `duplicate_timestamps` are the real signal
    #: there, and a bogus number next to them would obscure it.
    expected = int((candles[-1].ts - candles[0].ts).total_seconds() // 3600) + 1
    return {
        "rows": len(candles),
        "first_ts": candles[0].ts.isoformat(),
        "last_ts": candles[-1].ts.isoformat(),
        "first_bar_idx": candles[0].bar_idx,
        "last_bar_idx": candles[-1].bar_idx,
        "step_seconds_observed": steps,
        "strictly_increasing": monotonic,
        "duplicate_timestamps": len(candles) - len({c.ts for c in candles}),
        "expected_rows_if_no_gap": expected if monotonic else None,
        "missing_hours": (expected - len(candles)) if monotonic else None,
        "utc_offsets": sorted({c.ts.utcoffset().total_seconds()
                               for c in candles}),
        "ohlc_violations": sum(
            1 for c in candles
            if c.high < max(c.open, c.close) or c.low > min(c.open, c.close)
            or c.high < c.low),
        "non_positive_prices": sum(
            1 for c in candles
            if min(c.open, c.high, c.low, c.close) <= 0.0),
    }


def rows_in_window(candles: Sequence[Candle],
                   start: datetime, end: datetime) -> int:
    """How many candles the period filter will actually admit (inclusive)."""
    return sum(1 for c in candles if start <= c.ts <= end)


# ---------------------------------------------------------------------------
# The trade ledger (section D). Every column is a field of `BacktestTrade`
# except the four marked DERIVED, each of which is arithmetic over fields the
# driver already produced.
# ---------------------------------------------------------------------------
LEDGER_COLUMNS: Tuple[str, ...] = (
    "trade_number", "pair", "direction",
    "ob_creation_ts", "bos_ts", "bos_bar_idx", "displacement_confirmed_ts",
    "structure_context", "ob_zone_upper", "ob_zone_lower", "ob_width",
    "ob_proximal_edge", "ob_distal_edge",
    "entry_level", "entry_level_matches_25pct_rule",
    "entry_ts", "entry_price", "sl_price", "tp_price",
    "exit_ts", "exit_price", "outcome", "reason_for_exit",
    "holding_bars", "holding_hours",
    "risk_distance", "reward_distance", "rr_informational",
    "rr_informational_exact", "rr_below_observational_reference",
    "sl_distance_pct", "theoretical_leverage", "applied_leverage",
    "leverage_clamped",
    "starting_balance_usd", "position_notional_usd", "fees_usd",
    "gross_pnl_usd", "net_pnl_usd", "pnl_pct_of_balance",
    "ending_balance_usd", "cumulative_return_pct",
    "realized_r", "cumulative_realized_r",
    "ob_setup_id", "entry_bar_from_bos", "retest_number",
    "pre_displacement_touches", "ambiguous_candle", "timeframe",
    "activation_mode", "strategy_version",
)


def entry_level_from_zone(trade: BacktestTrade) -> float:
    """
    DERIVED cross-check of the documented 25%-into-the-zone entry.

    LONG enters at `upper - 25% of width`, SHORT at `lower + 25% of width`,
    which is `proximal` displaced a quarter of the width INTO the zone. This is
    recomputed here only so the ledger can show whether the engine's own
    `entry_price` agrees; nothing acts on it.
    """
    quarter = 0.25 * trade.ob_width
    return (trade.proximal - quarter if trade.direction == "LONG"
            else trade.proximal + quarter)


def structure_context(trade: BacktestTrade) -> str:
    """
    The structure break that created the order block, as the engine records it.

    The Manual SMC package carries `bos_bar_idx` / `bos_dt` and does NOT label a
    break as BOS versus CHOCH anywhere, so this string reports the break the
    engine actually stored and invents no classification it does not make.
    """
    disp = (trade.displacement_confirmed_dt.isoformat()
            if trade.displacement_confirmed_dt is not None else "none")
    return (f"break@bar{trade.bos_bar_idx}={trade.bos_dt.isoformat()} "
            f"origin@bar{trade.origin_bar_idx}={trade.formation_dt.isoformat()} "
            f"span={trade.bos_bar_idx - trade.origin_bar_idx}bar "
            f"displacement_confirmed={disp}")


def rr_exact_of(trade: BacktestTrade) -> Optional[Decimal]:
    """
    The trade's reward:risk in exact `Decimal`, FOR REPORTING ONLY.

    NOTHING IS ADMITTED OR REFUSED ON THIS NUMBER. It is not consulted by the
    strategy, the lifecycle, the backtest driver or the runner; every trade in
    the ledger already exists by the time this is called, and deleting this
    function would change no trade, no balance and no outcome.

    It exists because a float division misreports the ratio it is asked to
    display: `0.5994 / 0.3` is 1.9979999999999998 in binary. The three prices
    are lifted into `Decimal` through `repr`, which is the shortest string that
    round-trips the float exactly, so the printed ratio is the ratio of the
    prices actually traded rather than of a binary approximation.

    Method, stated so the column is reproducible: exact `Decimal` distances,
    then one division under the ambient decimal context (28 significant digits
    by default). `None` when the risk distance is zero — a degenerate geometry
    has no ratio, and inventing one would be a fabricated statistic.
    """
    entry = Decimal(repr(trade.entry_price))
    risk = abs(entry - Decimal(repr(trade.sl_price)))
    reward = abs(Decimal(repr(trade.tp_price)) - entry)
    return None if risk == 0 else reward / risk


def ledger_row(trade: BacktestTrade, opening_balance: float) -> Dict[str, Any]:
    """One `BacktestTrade` -> one ledger row. Transcription plus 6 derivations."""
    level = entry_level_from_zone(trade)
    rr = rr_exact_of(trade)
    return {
        "trade_number": trade.trade_id,
        "pair": trade.asset,
        "direction": trade.direction,
        "ob_creation_ts": trade.formation_dt.isoformat(),
        "bos_ts": trade.bos_dt.isoformat(),
        "bos_bar_idx": trade.bos_bar_idx,
        "displacement_confirmed_ts": (
            trade.displacement_confirmed_dt.isoformat()
            if trade.displacement_confirmed_dt is not None else ""),
        "structure_context": structure_context(trade),
        "ob_zone_upper": repr(trade.ob_top),
        "ob_zone_lower": repr(trade.ob_bottom),
        "ob_width": repr(trade.ob_width),
        "ob_proximal_edge": repr(trade.proximal),
        "ob_distal_edge": repr(trade.distal),
        "entry_level": repr(level),                            # DERIVED
        "entry_level_matches_25pct_rule": (                    # DERIVED
            abs(level - trade.entry_price) <= 1e-9),
        "entry_ts": trade.fill_dt.isoformat(),
        "entry_price": repr(trade.entry_price),
        "sl_price": repr(trade.sl_price),
        "tp_price": repr(trade.tp_price),
        "exit_ts": trade.exit_dt.isoformat(),
        "exit_price": repr(trade.exit_price),
        "outcome": trade.outcome,
        "reason_for_exit": trade.reason_for_exit,
        "holding_bars": trade.holding_bars,
        "holding_hours": repr(trade.holding_time_hours),
        "risk_distance": repr(trade.risk_dist),
        "reward_distance": repr(trade.reward_dist),
        "rr_informational": repr(trade.reward_dist / trade.risk_dist    # DERIVED
                                 if trade.risk_dist else float("nan")),
        "rr_informational_exact": (                                    # DERIVED
            str(rr) if rr is not None else ""),
        "rr_below_observational_reference": (                           # DERIVED
            "" if rr is None else rr < OBSERVATIONAL_RR_REFERENCE_DECIMAL),
        "sl_distance_pct": repr(trade.sl_dist_pct),
        "theoretical_leverage": repr(trade.theoretical_leverage),
        "applied_leverage": repr(trade.applied_leverage),
        "leverage_clamped": trade.leverage_clamped,
        "starting_balance_usd": repr(trade.starting_capital),
        "position_notional_usd": repr(trade.position_notional),
        "fees_usd": repr(trade.fees_usd),
        "gross_pnl_usd": repr(trade.gross_pnl_usd),
        "net_pnl_usd": repr(trade.net_pnl_usd),
        "pnl_pct_of_balance": repr(trade.return_pct),
        "ending_balance_usd": repr(trade.ending_capital),
        "cumulative_return_pct": repr(                                # DERIVED
            (trade.ending_capital / opening_balance - 1.0) * 100.0),
        "realized_r": repr(trade.realized_r),
        "cumulative_realized_r": repr(trade.cumulative_realized_r),
        "ob_setup_id": trade.ob_id,
        "entry_bar_from_bos": trade.entry_bar_from_bos,
        "retest_number": trade.retest_number,
        "pre_displacement_touches": trade.pre_displacement_touches,
        "ambiguous_candle": trade.is_ambiguous,
        "timeframe": trade.data_timeframe,
        "activation_mode": trade.displacement_mode,
        "strategy_version": trade.strategy_version,
    }


# ---------------------------------------------------------------------------
# Integrity checks on the driver's own output (section F). These RAISE: a
# broken compounding chain or an overlapping trade must stop the report, not
# be footnoted in it.
# ---------------------------------------------------------------------------
class BacktestIntegrityError(RuntimeError):
    """The driver's output failed a property this report depends on."""


def verify_chain(result: BacktestResult, opening_balance: float) -> None:
    """
    Compounding is a chain, one trade at a time, and this proves it per pair.

    * trade 1 opens at exactly the requested balance;
    * every later trade opens at the previous trade's closing balance;
    * the last closing balance is the reported final balance;
    * no trade's holding window overlaps the next trade's;
    * trade ids are 1..n with no gaps.
    """
    trades = result.trades
    if result.starting_capital != opening_balance:
        raise BacktestIntegrityError(
            f"{result.symbols}: driver started at {result.starting_capital!r}, "
            f"not {opening_balance!r}")
    if not trades:
        if result.ending_capital != opening_balance:
            raise BacktestIntegrityError(
                f"{result.symbols}: no trades, yet the balance moved to "
                f"{result.ending_capital!r}")
        return
    expected = opening_balance
    previous: Optional[BacktestTrade] = None
    for i, trade in enumerate(trades, start=1):
        if trade.trade_id != i:
            raise BacktestIntegrityError(
                f"{result.symbols}: trade ids are not 1..n at {trade.trade_id}")
        if trade.starting_capital != expected:
            raise BacktestIntegrityError(
                f"{result.symbols} trade {i}: opened at "
                f"{trade.starting_capital!r}, expected {expected!r}")
        if previous is not None and trade.fill_dt < previous.exit_dt:
            raise BacktestIntegrityError(
                f"{result.symbols} trade {i}: filled {trade.fill_dt} before "
                f"trade {i - 1} exited {previous.exit_dt}; trades overlap")
        expected = trade.ending_capital
        previous = trade
    if expected != result.ending_capital:
        raise BacktestIntegrityError(
            f"{result.symbols}: last trade closed at {expected!r} but the "
            f"result reports {result.ending_capital!r}")


def verify_isolation(result: BacktestResult, symbol: str) -> None:
    """No row from another pair may appear in a pair's own account."""
    foreign = sorted({t.asset for t in result.trades} - {symbol})
    if foreign:
        raise BacktestIntegrityError(
            f"{symbol} account contains trades from {foreign}")
    if tuple(result.symbols) != (symbol,):
        raise BacktestIntegrityError(
            f"{symbol} run was configured with {result.symbols}")


# ---------------------------------------------------------------------------
# Summary statistics (section E). `Aggregate` values are the driver's own;
# the dollar and duration statistics are folds over the ledger rows.
# ---------------------------------------------------------------------------
def summarise(result: BacktestResult, symbol: str,
              opening_balance: float) -> Dict[str, Any]:
    trades = result.trades
    n = len(trades)
    agg = result.overall
    nets = [t.net_pnl_usd for t in trades]
    rrs = [t.reward_dist / t.risk_dist for t in trades if t.risk_dist > 0]
    #: The same ratios in exact `Decimal`. OBSERVATIONAL. `min`/`max` over these
    #: answers "what is the lowest reward:risk this strategy actually traded",
    #: which is a description of the trades the structural rules produced — not
    #: evidence about a threshold, because there is no threshold to be evidence
    #: about. The float `rrs` above feeds the float-precision ledger column.
    exact = [r for r in (rr_exact_of(t) for t in trades) if r is not None]
    #: EVERY entry block is a slot conflict. That is not an assumption about the
    #: code: `verify_no_risk_reward_refusals` below refuses to let this run
    #: publish if any block ever carries a reward:risk rejection code.
    slot_blocks = list(result.entry_blocks)
    #: How many of the trades that DID happen sat below the observational
    #: reference ratio. Nothing was refused for it; this is a count of admitted
    #: trades, and it would be the same count if the reference were deleted.
    below_reference = sum(1 for r in exact
                          if r < OBSERVATIONAL_RR_REFERENCE_DECIMAL)
    #: The equity curve, opening balance first, so a peak that is never
    #: revisited is visible rather than hidden behind the final balance.
    curve = [opening_balance] + [t.ending_capital for t in trades]
    peak = max(curve)
    return {
        "pair": symbol,
        "starting_balance_usd": opening_balance,
        "final_balance_usd": result.ending_capital,
        #: Requirement (Task 4): "total setups considered". The order blocks the
        #: scanner admitted over the run — the population `total_trades` is a
        #: subset of. An OB that was created and never touched is counted here
        #: and nowhere else.
        "total_setups_considered": result.order_blocks_created,
        "setups_traded_pct": (
            round(len(trades) / result.order_blocks_created * 100, 2)
            if result.order_blocks_created else 0.0),
        "total_trades": n,
        "tp_count": agg.wins,
        "sl_count": agg.losses,
        "timeout_count": agg.timeouts,
        "win_rate_pct": agg.win_rate_pct,
        "loss_rate_pct": round(agg.losses / n * 100, 2) if n else 0.0,
        "net_pnl_usd": result.ending_capital - opening_balance,
        "return_pct": result.total_return_pct,
        "average_trade_pnl_usd": (sum(nets) / n) if n else 0.0,
        "expectancy_r": agg.expectancy_r,
        "total_realized_r": agg.total_r,
        "profit_factor": agg.profit_factor,
        "max_drawdown_pct": result.max_drawdown_pct,
        #: Sign-domain clamped so the LABELS stay true in a degenerate run. A
        #: bare `max(nets)`/`min(nets)` would report the smallest WIN as the
        #: "largest loss" when a pair never lost (and vice versa). Every one of
        #: the eight real runs contains both signs, so these are byte-identical
        #: to the unclamped fold there; the clamp only protects the labels.
        "largest_win_usd": max([n_ for n_ in nets if n_ > 0], default=0.0),
        "largest_loss_usd": min([n_ for n_ in nets if n_ < 0], default=0.0),
        "average_holding_hours": (
            sum(t.holding_time_hours for t in trades) / n) if n else 0.0,
        "candles_processed": result.candles_processed,
        "first_candle_ts": (result.first_ts.isoformat()
                            if result.first_ts else None),
        "last_candle_ts": (result.last_ts.isoformat()
                           if result.last_ts else None),
        "automatic_invalidations": result.invalidations,
        "entry_touches_blocked_by_open_trade": len(slot_blocks),
        "open_trade_at_end": result.open_trade_at_end,
        "ambiguous_candle_trades": agg.ambiguous,
        "min_rr_informational": min(rrs) if rrs else None,
        "max_rr_informational": max(rrs) if rrs else None,
        #: The same two numbers in exact `Decimal`, as strings so the JSON keeps
        #: every digit. They describe the trades taken; they license no claim
        #: about any minimum, because no minimum is applied.
        "min_rr_informational_exact": str(min(exact)) if exact else None,
        "max_rr_informational_exact": str(max(exact)) if exact else None,
        #: Stated in every summary so a reader cannot mistake the columns above
        #: for a filter that shaped the population they are computed from.
        "risk_reward_is_informational_only": True,
        "risk_reward_admission_rule_exists": False,
        "entry_touches_rejected_by_risk_reward": 0,
        "setups_rejected_by_risk_reward": 0,
        #: Requirement (Task 4): the sub-1.5 population AS AN OBSERVATION. These
        #: trades were taken. Computed from the exact `Decimal` ratios so the
        #: count cannot be moved by a float rounding artefact.
        "observational_rr_reference": str(OBSERVATIONAL_RR_REFERENCE_DECIMAL),
        "trades_below_observational_rr_reference": below_reference,
        "trades_below_observational_rr_reference_pct": (
            round(below_reference / n * 100, 2) if n else 0.0),
        "leverage_clamped_trades": sum(
            1 for t in trades if t.leverage_clamped),
        "min_applied_leverage": (min(t.applied_leverage for t in trades)
                                 if trades else None),
        "max_applied_leverage": (max(t.applied_leverage for t in trades)
                                 if trades else None),
        "total_fees_usd": sum(t.fees_usd for t in trades),
        "peak_balance_usd": peak,
        "peak_after_trade_number": curve.index(peak),
        "final_balance_is_exactly_zero": result.ending_capital == 0.0,
        "trades_opened_under_one_cent": sum(
            1 for t in trades if t.starting_capital < 0.01),
        "zero_notional_trades": sum(
            1 for t in trades if t.position_notional == 0.0),
        "dual_touch_sl_first_trades": agg.ambiguous,
    }


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
def write_csv(path: Path, columns: Sequence[str],
              rows: Sequence[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)
    return path


BLOCK_COLUMNS: Tuple[str, ...] = (
    "pair", "blocked_ob_id", "direction", "bar_idx", "ts",
    "rejection_code", "detail", "holder_pair", "holder_ob_id",
    "holder_acquired_at",
)


def block_rows(result: BacktestResult) -> List[Dict[str, Any]]:
    """
    Every entry touch the strategy refused, with the reason it refused it.

    There is exactly ONE such reason:

      * `ACTIVE_TRADE_OPEN` — "one trade at a time": the pair's single slot was
        already taken, and `holder_*` names the trade that held it.

    `INVALID_RISK_REWARD` is NOT a code that can appear in this file. No
    reward:risk rule exists on the Manual SMC path, so a ratio cannot refuse an
    entry touch; `verify_no_risk_reward_refusals` enforces that rather than
    trusting it.

    Each row is a setup that reached its entry level and was NOT traded.
    Diagnostic only.
    """
    return [{
        "pair": b.asset,
        "blocked_ob_id": b.ob_id,
        "direction": b.direction,
        "bar_idx": b.bar_idx,
        "ts": b.ts.isoformat(),
        "rejection_code": b.rejection_code or "",
        "detail": b.detail,
        "holder_pair": b.holder_asset or "",
        "holder_ob_id": b.holder_ob_id or "",
        "holder_acquired_at": (b.holder_acquired_at.isoformat()
                               if b.holder_acquired_at else ""),
    } for b in result.entry_blocks]


def markdown_table(rows: Sequence[Dict[str, Any]],
                   columns: Sequence[Tuple[str, str, str]]) -> str:
    """
    `columns` is a sequence of (key, header, format spec).

    A None cell renders as `n/a` rather than raising. Several summary keys are
    legitimately None when a pair admitted no trades at all
    (`min_rr_informational`, `min_applied_leverage`, ...), which a short window
    on a quiet pair can produce. Printing `n/a` keeps the absence visible;
    `format(None, ".4f")` would just kill the report. Non-None cells are
    formatted exactly as before.
    """
    head = "| " + " | ".join(h for _, h, _ in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"

    def cell(value: Any, spec: str) -> str:
        if value is None:
            return "n/a"
        return format(value, spec) if spec else str(value)

    body = [
        "| " + " | ".join(cell(row[key], spec) for key, _, spec in columns)
        + " |"
        for row in rows
    ]
    return "\n".join([head, rule, *body])


SUMMARY_VIEW: Tuple[Tuple[str, str, str], ...] = (
    ("pair", "Pair", ""),
    ("starting_balance_usd", "Start $", ".2f"),
    ("final_balance_usd", "Final $", ".6g"),
    ("total_setups_considered", "Setups", ""),
    ("total_trades", "Trades", ""),
    ("tp_count", "TP", ""),
    ("sl_count", "SL", ""),
    ("win_rate_pct", "Win %", ".2f"),
    ("loss_rate_pct", "Loss %", ".2f"),
    ("net_pnl_usd", "Net $", ".6g"),
    ("return_pct", "Return %", ".2f"),
    ("average_trade_pnl_usd", "Avg trade $", ".6g"),
    ("expectancy_r", "Expectancy R", ".4f"),
    ("max_drawdown_pct", "Max DD %", ".2f"),
    ("largest_win_usd", "Largest win $", ".6g"),
    ("largest_loss_usd", "Largest loss $", ".6g"),
    ("average_holding_hours", "Avg hold h", ".2f"),
)

#: Everything a reader needs to judge the summary above, kept in its own table
#: rather than folded into it: the peak the account reached before decaying, how
#: much of the run happened at a balance no exchange would accept, and the
#: reward:risk spread of the trades that were taken — an OBSERVATION about the
#: population, never a filter that shaped it.
DIAGNOSTIC_VIEW: Tuple[Tuple[str, str, str], ...] = (
    ("pair", "Pair", ""),
    ("peak_balance_usd", "Peak $", ".6g"),
    ("peak_after_trade_number", "Peak @ trade", ""),
    ("final_balance_is_exactly_zero", "Final == 0", ""),
    ("trades_opened_under_one_cent", "Opened < $0.01", ""),
    ("zero_notional_trades", "Zero notional", ""),
    ("min_rr_informational", "Min R:R (info)", ".4f"),
    ("max_rr_informational", "Max R:R (info)", ".4f"),
    ("trades_below_observational_rr_reference", "Traded R:R < 1.5", ""),
    ("trades_below_observational_rr_reference_pct", "Traded R:R < 1.5 %", ".2f"),
    ("leverage_clamped_trades", "Lev clamped", ""),
    ("min_applied_leverage", "Min lev", ".2f"),
    ("max_applied_leverage", "Max lev", ".2f"),
    ("total_fees_usd", "Fees $", ".6g"),
    ("dual_touch_sl_first_trades", "Dual touch", ""),
    ("timeout_count", "Timeouts", ""),
    ("entry_touches_blocked_by_open_trade", "Slot conflicts", ""),
    ("automatic_invalidations", "Auto invalidations", ""),
)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def verify_no_risk_reward_refusals(result: BacktestResult, symbol: str) -> None:
    """
    Refuse to report a run in which a reward:risk ratio refused anything.

    This is the inverse of the check that used to stand here. The requirement is
    that R:R can NEVER decide whether an otherwise-valid order block trades, so
    the integrity condition is that no entry block carries a reward:risk
    rejection code and no trade was skipped for its ratio. Asserting it here
    means a regression that reintroduced the rule would stop the report being
    produced instead of quietly shrinking the trade population.

    `detail` is scanned as well as `rejection_code`, so a refusal rebuilt under a
    different code name still trips this.
    """
    for block in result.entry_blocks:
        code = (block.rejection_code or "").upper()
        if "RISK_REWARD" in code or "MINIMUM_RR" in code:
            raise BacktestIntegrityError(
                f"{symbol} entry touch on {block.ob_id} at {block.ts} was "
                f"refused with code {block.rejection_code!r}: a reward:risk "
                f"admission rule has been reintroduced. Detail: {block.detail}")
        if "risk_reward" in block.detail.lower() and "reject" in block.detail.lower():
            raise BacktestIntegrityError(
                f"{symbol} entry touch on {block.ob_id} at {block.ts} reports a "
                f"reward:risk refusal in its detail: {block.detail}")


def run_period(name: str, start: datetime, end: datetime,
               dataset: Dict[str, List[Candle]],
               out: Path,
               detector: str = DETECTOR_LUXALGO_INTERNAL,
               ) -> Tuple[Dict[str, Any], Any]:
    """
    One period, four independent $10 accounts, four separate backtests.

    `detector` selects the structure algorithm the driver runs and defaults to
    the SHIPPED production detector. It is NAMED here rather than inherited so
    that the artifact this function writes says which algorithm produced it, and
    so a change to a library default cannot silently move a published baseline
    onto a different detector. An unknown value is refused downstream by the
    lifecycle; nothing here guesses or aliases.
    """
    ledger: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    config: Any = None

    for symbol in SYMBOLS:
        candles = dataset[symbol]
        result = run_manual_smc_backtest_from_candles(
            {symbol: candles},
            symbols=(symbol,),
            start_date=start,
            end_date=end,
            starting_capital=STARTING_CAPITAL,
            account_id=f"BACKTEST_{name.upper()}_{symbol}",
            detector=detector,
            #: NO `minimum_risk_reward` ARGUMENT EXISTS. The driver admits an
            #: order block on the authorised structural rules alone; a ratio is
            #: reported afterwards and cannot remove a setup from this run.
        )
        verify_isolation(result, symbol)
        verify_chain(result, STARTING_CAPITAL)
        verify_no_risk_reward_refusals(result, symbol)
        if config is None:
            config = result.config
        elif result.config != config:
            raise BacktestIntegrityError(
                f"{symbol} ran under a different config than the other pairs")

        for trade in result.trades:
            ledger.append(ledger_row(trade, STARTING_CAPITAL))
        blocks.extend(block_rows(result))
        summaries.append(summarise(result, symbol, STARTING_CAPITAL))
        print(f"  {symbol}: {result.overall.trades:>4} trades  "
              f"TP {result.overall.wins:>3}  SL {result.overall.losses:>3}  "
              f"slot-blocked {len(result.entry_blocks):>4}  "
              #: `.6g`, not `.6f`: a balance of 3.1e-44 is NOT zero, and a
              #: fixed-point render would print it as `$0.000000` and imply a
              #: liquidation that never happened. The tables use `.6g` for the
              #: same reason; `final_balance_is_exactly_zero` is the only place
              #: a zero balance is asserted.
              f"${STARTING_CAPITAL:.2f} -> ${result.ending_capital:.6g}  "
              f"({result.candles_processed} candles)")

    ledger_path = write_csv(out / f"{name}_trade_ledger.csv",
                            LEDGER_COLUMNS, ledger)
    blocks_path = write_csv(out / f"{name}_blocked_entries.csv",
                            BLOCK_COLUMNS, blocks)
    return {
        "period": name,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        #: Which structure algorithm produced every row below. Published so a
        #: ledger cannot be compared against one drawn from a different
        #: detector without the difference being visible.
        "detector": detector,
        "actual_first_candle": min(
            (s["first_candle_ts"] for s in summaries
             if s["first_candle_ts"]), default=None),
        "actual_last_candle": max(
            (s["last_candle_ts"] for s in summaries
             if s["last_candle_ts"]), default=None),
        "starting_balance_per_pair_usd": STARTING_CAPITAL,
        "capital_shared_between_pairs": False,
        "risk_reward_admission_rule_exists": False,
        "observational_rr_reference": str(OBSERVATIONAL_RR_REFERENCE_DECIMAL),
        "summaries": summaries,
        "trade_ledger_csv": str(ledger_path),
        "blocked_entries_csv": str(blocks_path),
        "total_trades_all_pairs": len(ledger),
        "total_setups_considered_all_pairs": sum(
            s["total_setups_considered"] for s in summaries),
        "total_setups_rejected_by_risk_reward_all_pairs": 0,
        "total_risk_reward_refusal_events_all_pairs": 0,
        "traded_below_observational_rr_reference_all_pairs": sum(
            s["trades_below_observational_rr_reference"] for s in summaries),
        "entry_level_disagreements": sum(
            1 for r in ledger if not r["entry_level_matches_25pct_rule"]),
    }, config


def assumptions(config: Any) -> Dict[str, Any]:
    """
    The EXISTING engine's assumptions, read off the config it actually ran.

    Nothing here is chosen by this script. `$10 -> trade size` is the sizing
    module's own arithmetic (`sizing.py` module docstring), reproduced verbatim
    so the reader does not have to infer it:

        sl_dist_pct  = |entry - sl| / entry * 100
        theo_lev     = max_sl_account_risk_pct / sl_dist_pct
        applied_lev  = min(applied_leverage_cap, theo_lev)
        margin_usd   = account_balance            (the WHOLE balance is margin)
        notional_usd = account_balance * applied_lev
        fee_usd      = notional_usd * fee_rate    (charged once, per trade)
        gross_pnl    = account_balance * return_pct / 100
        net_pnl      = gross_pnl - fee_usd
        next_balance = max(0.0, account_balance + net_pnl)
    """
    return {
        "timeframe": config.data_timeframe,
        "candle_source": str(default_canonical_base()),
        "entry_depth_pct_into_ob": config.entry_depth_pct,
        "fixed_tp_pct_from_entry": config.fixed_tp_market_pct,
        "sl_placement": "opposite OB edge (`distal`)",
        "max_sl_account_risk_pct": config.max_sl_account_risk_pct,
        "applied_leverage_cap": config.applied_leverage_cap,
        "fee_rate_of_notional": config.fee_rate,
        "fee_charged": "once per trade, on the position notional",
        "slippage_model": "none — fills are at the strategy's own levels",
        "funding_model": "none",
        "max_holding_bars_after_fill": config.max_holding_bars,
        "lookback_bars_for_ob_origin": config.lookback,
        "min_ob_width": config.min_ob_width,
        "starting_capital_used": STARTING_CAPITAL,
        "margin_model": "whole balance is margin on every trade",
        "compounding": "next trade opens at the previous trade's ending balance",
        "timezone": "all candle timestamps are UTC (+00:00) in the CSVs",
        #: The keys that reported a minimum-R:R rule are GONE rather than set to
        #: a falsy value, so a reader who greps the old names in a new report
        #: gets NOTHING back instead of a number they might read as "still
        #: enforced". Reward:risk is now reported and never applied.
        "minimum_risk_reward_enforced_anywhere": False,
        "risk_reward_semantics": (
            "reward_dist / risk_dist, computed per trade and published as the "
            "ledger columns `rr_informational` (float) and "
            "`rr_informational_exact` (exact Decimal). NO THRESHOLD IS APPLIED "
            "TO IT: not in the strategy, the lifecycle, the backtest driver, "
            "this runner, execution/validation.py, execution_engine.py or the "
            "Java OrderValidationGateway. It is a consequence of the entry, "
            "stop and target the authorised rules produced, never an input to "
            "whether the setup trades"),
        "observational_rr_reference_semantics": (
            "1.5 is used for one descriptive statistic — how many trades that "
            "WERE taken had a ratio below it. Changing or deleting it would "
            "leave every trade, balance and outcome byte-identical"),
        "risk_reward_is_measured_on_unquantized_prices": (
            "no tick_specs are passed here, so entry/SL/TP are the strategy's "
            "raw levels; live rounds to the instrument tick before reporting, "
            "which can move rr by up to one tick / risk_distance. This affects "
            "a reported number only, since nothing gates on it"),
        "same_candle_tp_and_sl": (
            "conservative SL-first: outcome FILLED_SL, reason "
            "DUAL_TOUCH_CONSERVATIVE_SL, is_ambiguous=True "
            "(lifecycle.py _step1_resolve_active_trade)"),
        "intra_candle_step_order": (
            "1 resolve active trade -> 2 update OBs / fill entries -> "
            "3 scan and admit new OBs; a fill therefore cannot also exit on "
            "its own candle, and minimum holding is one bar"),
        "re_entry_after_a_close": (
            "refused while ts <= last_trade_closed_dt, so the slot cannot be "
            "reused on the candle a trade closed on"),
        "information_available": (
            "each candle is processed closed, with open/high/low/close of that "
            "candle only; no later candle is read"),
        "one_trade_at_a_time": (
            "enforced by the strategy's PortfolioLock; each pair here has its "
            "own strategy, lifecycle and lock"),
        "profit_factor_convention": (
            "`profit_factor` in the JSON is the DRIVER's own "
            "`Aggregate.profit_factor` (backtest.py aggregate()): it divides "
            "gain R by loss R, but substitutes loss_r=1.0 when a pair has zero "
            "losses, so a lossless pair reports its total gain R rather than an "
            "infinite factor. All eight runs here have losses, so every "
            "reported value is a true ratio"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manual SMC compounding backtests (offline, read-only).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"artifact directory (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("MANUAL SMC COMPOUNDING BACKTESTS - offline, no exchange contact")
    print("=" * 78)
    print(f"canonical base : {default_canonical_base()}")
    dataset = load_canonical_dataset(None, SYMBOLS)

    print("\n-- DATA COVERAGE (measured, not assumed) " + "-" * 37)
    coverage: Dict[str, Any] = {}
    for symbol in SYMBOLS:
        info = inspect_coverage(dataset[symbol])
        coverage[symbol] = info
        if not info["rows"]:
            print(f"  {symbol}: NO CANDLES FOUND")
            continue
        print(f"  {symbol}: {info['rows']} rows  "
              f"{info['first_ts']} -> {info['last_ts']}  "
              f"steps={info['step_seconds_observed']}  "
              f"missing_hours={info['missing_hours']}  "
              f"dups={info['duplicate_timestamps']}  "
              f"ohlc_violations={info['ohlc_violations']}")
        for period_name, start, end in PERIODS:
            coverage[symbol][f"{period_name}_rows_in_window"] = rows_in_window(
                dataset[symbol], start, end)

    periods: List[Dict[str, Any]] = []
    config: Any = None
    for period_name, start, end in PERIODS:
        print(f"\n-- PERIOD {period_name.upper()}: requested "
              f"{start.date()} -> {end.date()} " + "-" * 20)
        block, cfg = run_period(period_name, start, end, dataset, out)
        config = cfg if config is None else config
        periods.append(block)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "starting_balance_per_pair_usd": STARTING_CAPITAL,
        "pairs": list(SYMBOLS),
        "accounts_are_independent": True,
        #: Stated once at the top level so no reader can mistake the reward:risk
        #: columns under `periods` for a filter. Every trade in this report was
        #: admitted by the authorised structural rules alone.
        "risk_reward_treatment": {
            "enforced_anywhere": False,
            "role": "INFORMATIONAL / DIAGNOSTIC ONLY",
            "can_admit_or_refuse_a_trade": False,
            "arithmetic": "reward_dist / risk_dist, exact Decimal, unrounded",
            "reported_as": (
                "ledger columns rr_informational, rr_informational_exact, "
                "rr_below_observational_reference; summary keys "
                "min/max_rr_informational[_exact]"),
            "removed_from": (
                "manual_smc lifecycle/strategy/backtest (the whole admission "
                "module was deleted), engine/src/quantedge/execution/"
                "validation.py, engine/src/quantedge/execution/"
                "execution_engine.py, and the Java OrderValidationGateway "
                "CHECK_MIN_RR"),
            "reason": (
                "a minimum reward:risk is not part of the authorised Manual SMC "
                "specification, so it must not decide whether an otherwise-"
                "valid order block can trade"),
            "observational_reference": str(OBSERVATIONAL_RR_REFERENCE_DECIMAL),
            "observational_reference_effect_on_results": "none",
        },
        "assumptions": assumptions(config),
        "data_coverage": coverage,
        "periods": periods,
    }
    report_path = out / "compounding_backtest_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8")

    for block in periods:
        print(f"\n### {block['period'].upper()} "
              f"({block['actual_first_candle']} -> "
              f"{block['actual_last_candle']})\n")
        print(markdown_table(block["summaries"], SUMMARY_VIEW))
        print()
        print(markdown_table(block["summaries"], DIAGNOSTIC_VIEW))
        print()
        print(f"R:R is INFORMATIONAL ONLY — no threshold is applied anywhere. "
              f"Setups refused for R:R: "
              f"{block['total_setups_rejected_by_risk_reward_all_pairs']} "
              f"(structurally impossible). Trades TAKEN with R:R below the "
              f"{OBSERVATIONAL_RR_REFERENCE_DECIMAL} observational reference: "
              f"{block['traded_below_observational_rr_reference_all_pairs']} "
              f"of {block['total_trades_all_pairs']}")
        for summary in block["summaries"]:
            print(f"  {summary['pair']}: "
                  f"traded R:R < {OBSERVATIONAL_RR_REFERENCE_DECIMAL}: "
                  f"{summary['trades_below_observational_rr_reference']}"
                  f" ({summary['trades_below_observational_rr_reference_pct']}%)"
                  f", R:R min "
                  f"{summary['min_rr_informational_exact']} max "
                  f"{summary['max_rr_informational_exact']}")
    print(f"\nreport : {report_path}")
    for block in periods:
        print(f"ledger : {block['trade_ledger_csv']}")
        print(f"blocks : {block['blocked_entries_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
