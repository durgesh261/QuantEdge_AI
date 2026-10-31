"""
Per-pair full-lifecycle trade CSVs for the recent window. READ-ONLY REPORTING.

This script contains NO strategy rule of its own. It drives the same production
entry points the compounding runner uses -- `ManualSMCBacktest` over a
`build_timeline` clock, with the production defaults -- and transcribes what
came back. Nothing here can admit, refuse, resize or reprice a trade:

  * There is NO reward:risk threshold. `rr_informational` is divided out of two
    distances that already exist on a closed trade and is written to a column.
    Deleting every R:R line in this file would change no row.
  * TP, SL, OB detection, the 25% entry, the 3-candle window, the one-trade
    lock, leverage and sizing all come from the engine unmodified.
  * No network, no credentials, no exchange contact, no writes outside `--out`.

Why it consumes `iter_run` instead of `run_manual_smc_backtest_from_candles`:
`BacktestTrade` does not carry the OB's first-touch timestamp, but the
lifecycle EMITS it as `FIRST_TOUCH_LIMIT_ACTIVATED`. `iter_run` is the public
generator that `run(...)` itself wraps, so consuming it observes the identical
run while letting the event stream be read. `main` then re-runs the wrapped
entry point and asserts the two trade tuples are identical, so the substitution
is proved rather than assumed.

Usage:
    python scripts/export_manual_smc_recent_trades.py [--out DIR]
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from quantedge.strategy.manual_smc.backtest import (  # noqa: E402
    BacktestTrade,
    Candle,
    ManualSMCBacktest,
    build_timeline,
    default_canonical_base,
    load_canonical_dataset,
    run_manual_smc_backtest_from_candles,
)
from quantedge.strategy.manual_smc.lifecycle import (  # noqa: E402
    ACTIVATION_MODE_FIRST_TOUCH,
    ENTRY_WINDOW_CANDLES,
    OUTCOME_SL,
    OUTCOME_TP,
    ManualLifecycleEventType,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    manual_smc_production_config,
)

#: Reused verbatim so the exact-Decimal ratio strings in these CSVs are
#: byte-identical to the compounding ledger's. It is a reporting function.
from run_manual_smc_compounding_backtests import rr_exact_of  # noqa: E402

SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: Requested window, verbatim. Clamping is measured and reported, never assumed.
REQUESTED_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
REQUESTED_END = datetime(2026, 9, 2, tzinfo=timezone.utc)
LABEL = "2026-08-01_to_2026-09-02"

#: Same figure the recent compounding period uses, per pair, never shared.
STARTING_CAPITAL = 10.0

DEFAULT_OUT = (REPO_ROOT / "engine" / "data" / "research"
               / "manual_smc_recent_trades")

#: Compared against for ONE boolean column. Not a filter: no code path reads
#: `rr_below_1_5` to decide anything, and changing this constant would leave
#: every trade, balance and outcome in every CSV identical.
OBSERVATIONAL_RR_REFERENCE = Decimal("1.5")


class ExportIntegrityError(RuntimeError):
    """The export disagrees with the engine. Fail loudly, write nothing."""


# ---------------------------------------------------------------------------
# Formatting. A missing value is ALWAYS the empty string -- never 0, never
# "N/A", never a plausible-looking number. `repr` is used for floats so the
# CSV carries the shortest string that round-trips the float the engine
# actually computed, rather than a rounded display of it.
# ---------------------------------------------------------------------------
def _iso(dt: Optional[datetime]) -> str:
    return "" if dt is None else dt.isoformat()


def _date(dt: Optional[datetime]) -> str:
    return "" if dt is None else dt.strftime("%Y-%m-%d")


def _time(dt: Optional[datetime]) -> str:
    return "" if dt is None else dt.strftime("%H:%M:%S")


def _num(value: Optional[float]) -> str:
    return "" if value is None else repr(float(value))


def _int(value: Optional[int]) -> str:
    return "" if value is None else str(int(value))


def _bool(value: Optional[bool]) -> str:
    """Lowercase JSON-style booleans so a spreadsheet cannot coerce them."""
    return "" if value is None else ("true" if value else "false")


def _dec(value: Optional[Decimal]) -> str:
    return "" if value is None else format(value, "f")


#: Every column, in the requested order. The CSV writer is driven off this
#: tuple, so a row that omits or invents a key raises instead of shifting a
#: neighbouring column's value into the wrong header.
COLUMNS: Tuple[str, ...] = (
    # A) TRADE / IDENTIFICATION
    "pair", "trade_id", "ob_setup_id", "direction", "strategy_name",
    "timeframe",
    # B) OB CREATION / STRUCTURE
    "ob_creation_ts", "ob_creation_date", "ob_creation_time",
    "bos_ts", "bos_date", "bos_time", "choch_or_bos", "ob_type",
    "ob_zone_upper", "ob_zone_lower", "ob_width", "ob_width_pct",
    "ob_origin_candle_ts", "ob_origin_candle_open", "ob_origin_candle_high",
    "ob_origin_candle_low", "ob_origin_candle_close",
    # C) SETUP / ENTRY
    "setup_created_ts", "setup_status",
    "first_touch_ts", "first_touch_date", "first_touch_time",
    "entry_trigger_ts", "entry_trigger_date", "entry_trigger_time",
    "entry_level", "entry_price",
    "actual_fill_ts", "actual_fill_date", "actual_fill_time",
    "entry_candle_open", "entry_candle_high", "entry_candle_low",
    "entry_candle_close", "entry_candle_volume",
    "candles_from_ob_creation_to_entry", "candles_from_ob_touch_to_entry",
    # D) POSITION PARAMETERS
    "sl_price", "tp_price", "risk_distance", "reward_distance",
    "risk_distance_pct", "reward_distance_pct",
    "rr_informational", "rr_informational_exact",
    "leverage", "applied_leverage",
    "position_margin_usd", "position_notional_usd",
    "quantity", "quantity_step", "minimum_quantity",
    # E) ACCOUNT / MONEY
    "starting_balance_usd", "balance_before_trade_usd", "margin_used_usd",
    "notional_usd", "fees_usd", "gross_pnl_usd", "net_pnl_usd", "realized_r",
    "ending_balance_usd",
    # F) EXIT
    "exit_ts", "exit_date", "exit_time", "exit_price", "exit_reason",
    "outcome", "tp_hit", "sl_hit", "holding_bars", "holding_hours",
    "exit_candle_open", "exit_candle_high", "exit_candle_low",
    "exit_candle_close", "exit_candle_volume",
    # G) TRADE LOCATION / MARKET CONTEXT
    "entry_candle_index", "exit_candle_index", "ob_creation_candle_index",
    "entry_price_vs_ob_position",
    "distance_entry_to_ob_upper", "distance_entry_to_ob_lower",
    "distance_sl_from_entry", "distance_tp_from_entry",
    "entry_position_pct_of_ob",
    "market_price_at_ob_creation", "market_price_at_first_touch",
    "market_price_at_entry",
    # H) VALIDATION / AUDIT
    "rr_below_1_5", "rr_filter_applied", "rr_rejection",
    "entry_rule_valid", "expiry_rule_valid", "one_trade_lock_valid",
    "data_source", "data_timezone", "notes",
)

#: Columns the engine genuinely does not produce. Written EMPTY in every row
#: and reported by name. Nothing here is guessed, defaulted or back-filled.
UNAVAILABLE_COLUMNS: Dict[str, str] = {
    "choch_or_bos":
        "the Manual SMC package has ONE break concept (bos_bar_idx/bos_dt) and "
        "no CHOCH classifier anywhere, so BOS cannot be distinguished from "
        "CHOCH without inventing a rule",
    "entry_trigger_ts": "the lifecycle has exactly one fill concept "
                        "(ENTRY_FILLED); there is no separate trigger event",
    "entry_trigger_date": "see entry_trigger_ts",
    "entry_trigger_time": "see entry_trigger_ts",
    "quantity": "the backtest sizes in USD notional only "
                "(sizing.py: 'a verified contract value is NOT required to "
                "size in USD'); PositionSizing has no quantity field",
    "quantity_step": "Delta publishes no size-side increment field; recorded "
                     "UNVERIFIED in product_specs_snapshot.json",
    "minimum_quantity": "Delta publishes no minimum_order_size field; recorded "
                        "UNVERIFIED in product_specs_snapshot.json",
}

# ---------------------------------------------------------------------------
# Driving the engine. One pair, one driver, one balance.
# ---------------------------------------------------------------------------
class PairRun:
    """One pair's run plus the event-derived lookups the CSV needs."""

    def __init__(self, symbol: str, candles: Sequence[Candle]) -> None:
        self.symbol = symbol
        self.candles: Tuple[Candle, ...] = tuple(candles)
        self.by_bar: Dict[int, Candle] = {c.bar_idx: c for c in self.candles}
        self.timeline = build_timeline(
            {symbol: list(candles)}, (symbol,), REQUESTED_START, REQUESTED_END)
        #: ob_id -> (bar_idx, ts) for the events that carry a lifecycle instant.
        self.created: Dict[str, Tuple[int, datetime]] = {}
        self.first_touch: Dict[str, Tuple[int, datetime]] = {}
        self.filled: Dict[str, Tuple[int, datetime]] = {}
        self.final_state: Dict[str, str] = {}
        self.event_counts: Dict[str, int] = {}
        self.result = self._drive()

    def _drive(self):
        """
        `iter_run` -> harvest events -> `result()`. Identical to `run()`.

        The driver's constructor calls `_assert_not_live()` itself, so this
        path carries the same live-execution refusal as the wrapped entry
        point; it is not being bypassed.
        """
        driver = ManualSMCBacktest(
            symbols=(self.symbol,),
            starting_capital=STARTING_CAPITAL,
            account_id=f"BACKTEST_RECENT_{self.symbol}",
            activation_mode=ACTIVATION_MODE_FIRST_TOUCH,
            entry_window_candles=ENTRY_WINDOW_CANDLES,
        )
        if driver.cfg != manual_smc_production_config():
            raise ExportIntegrityError(
                f"{self.symbol}: driver config is not the production config")
        for evaluation in driver.iter_run(
                self.timeline, {self.symbol: list(self.candles)}):
            for event in evaluation.events:
                self._harvest(event)
        return driver.result()

    def _harvest(self, event) -> None:
        name = event.event_type.name
        self.event_counts[name] = self.event_counts.get(name, 0) + 1
        stamp = (event.bar_idx, event.ts)
        if name == "OB_CREATED":
            self.created.setdefault(event.ob_id, stamp)
        elif name == "FIRST_TOUCH_LIMIT_ACTIVATED":
            self.first_touch.setdefault(event.ob_id, stamp)
        elif name == "ENTRY_FILLED":
            self.filled.setdefault(event.ob_id, stamp)
        self.final_state[event.ob_id] = event.state.value

def trade_key(trade: BacktestTrade) -> Tuple[Any, ...]:
    """
    The identity tuple used to prove `iter_run` observed the SAME run.

    Every price, balance and outcome a CSV row transcribes is in here, so an
    equal tuple means the substituted loop produced an equal ledger.
    """
    return (
        trade.trade_id, trade.asset, trade.direction, trade.ob_id,
        trade.origin_bar_idx, trade.bos_bar_idx, trade.fill_bar_idx,
        trade.exit_bar_idx, trade.bos_dt, trade.formation_dt,
        trade.displacement_confirmed_dt, trade.fill_dt, trade.exit_dt,
        trade.ob_top, trade.ob_bottom, trade.ob_width, trade.proximal,
        trade.distal, trade.entry_price, trade.sl_price, trade.tp_price,
        trade.exit_price, trade.risk_dist, trade.reward_dist,
        trade.sl_dist_pct, trade.theoretical_leverage, trade.applied_leverage,
        trade.starting_capital, trade.position_notional, trade.fees_usd,
        trade.gross_pnl_usd, trade.net_pnl_usd, trade.ending_capital,
        trade.return_pct, trade.realized_r, trade.outcome,
        trade.reason_for_exit, trade.holding_bars, trade.holding_time_hours,
    )


def prove_same_run(pair: PairRun) -> None:
    """
    Re-run the WRAPPED production entry point and require an identical ledger.

    This is the whole justification for consuming `iter_run`: if the two
    ledgers ever diverged, the CSVs would describe a run the production entry
    point never produced. Divergence raises and nothing is written.
    """
    reference = run_manual_smc_backtest_from_candles(
        {pair.symbol: list(pair.candles)},
        symbols=(pair.symbol,),
        start_date=REQUESTED_START,
        end_date=REQUESTED_END,
        starting_capital=STARTING_CAPITAL,
        account_id=f"BACKTEST_RECENT_{pair.symbol}",
    )
    mine = tuple(trade_key(t) for t in pair.result.trades)
    theirs = tuple(trade_key(t) for t in reference.trades)
    if mine != theirs:
        raise ExportIntegrityError(
            f"{pair.symbol}: iter_run ledger differs from "
            f"run_manual_smc_backtest_from_candles "
            f"({len(mine)} vs {len(theirs)} trades)")
    if (pair.result.ending_capital != reference.ending_capital
            or pair.result.candles_processed != reference.candles_processed):
        raise ExportIntegrityError(
            f"{pair.symbol}: aggregate mismatch against the wrapped entry point")

# ---------------------------------------------------------------------------
# Row derivations. Every one of these is either a transcription or a stated
# formula over values the engine already produced.
# ---------------------------------------------------------------------------
def ob_type_of(trade: BacktestTrade) -> str:
    """
    BULLISH_OB / BEARISH_OB, per the DOCUMENTED direction mapping in models.py.

    A LONG setup is a bullish OB drawn from a bearish origin candle
    (`ob_top=origin.HIGH`, `ob_bottom=origin.CLOSE`); a SHORT setup is a
    bearish OB drawn from a bullish origin (`ob_top=origin.CLOSE`,
    `ob_bottom=origin.LOW`). This reads that mapping; it invents no taxonomy.
    """
    return "BULLISH_OB" if trade.direction == "LONG" else "BEARISH_OB"


def entry_level_of(trade: BacktestTrade, depth_pct: float) -> float:
    """
    The strategy's 25%-into-the-zone level, recomputed from the zone.

    LONG  = upper edge - depth * width; SHORT = lower edge + depth * width,
    with `depth` read off the live config rather than hardcoded. Recomputed
    ONLY so `entry_rule_valid` can compare it against the engine's own
    `entry_price`; nothing acts on the result.
    """
    step = depth_pct * trade.ob_width
    return (trade.ob_top - step if trade.direction == "LONG"
            else trade.ob_bottom + step)


def zone_position_of(trade: BacktestTrade) -> Tuple[str, Optional[float]]:
    """
    Where the entry sits in the zone: a MEASURED label plus the percentage.

    The percentage is measured from the lower edge upwards for both
    directions, so the column has one meaning across every row. A zero-width
    zone has no percentage and yields `None` rather than a fabricated 0.
    """
    if trade.entry_price >= trade.ob_top:
        label = "AT_OR_ABOVE_UPPER_EDGE"
    elif trade.entry_price <= trade.ob_bottom:
        label = "AT_OR_BELOW_LOWER_EDGE"
    else:
        label = "INSIDE_ZONE"
    pct = (None if trade.ob_width == 0.0 else
           (trade.entry_price - trade.ob_bottom) / trade.ob_width * 100.0)
    return label, pct


def pct_of_entry(distance: float, entry: float) -> Optional[float]:
    """`distance / entry * 100`, the engine's own percentage convention."""
    return None if entry <= 0.0 else distance / entry * 100.0

def lock_respected(trades: Sequence[BacktestTrade]) -> Dict[int, bool]:
    """
    Per-trade: did this fill start only after every earlier trade had closed?

    MEASURED over the pair's own ledger by bar index, not asserted. The run is
    single-symbol, so the portfolio-wide one-trade lock reduces exactly to
    "no two [fill_bar, exit_bar] intervals overlap".
    """
    out: Dict[int, bool] = {}
    previous_exit: Optional[int] = None
    for trade in sorted(trades, key=lambda t: (t.fill_bar_idx, t.trade_id)):
        out[trade.trade_id] = (previous_exit is None
                               or trade.fill_bar_idx >= previous_exit)
        previous_exit = (trade.exit_bar_idx if previous_exit is None
                         else max(previous_exit, trade.exit_bar_idx))
    return out


def candle_fields(candle: Optional[Candle], prefix: str) -> Dict[str, str]:
    """OHLCV for one bar, or five empty cells when the bar is not loaded."""
    if candle is None:
        return {f"{prefix}_{k}": "" for k in
                ("open", "high", "low", "close", "volume")}
    return {
        f"{prefix}_open": _num(candle.open),
        f"{prefix}_high": _num(candle.high),
        f"{prefix}_low": _num(candle.low),
        f"{prefix}_close": _num(candle.close),
        f"{prefix}_volume": _num(candle.volume),
    }


def note_for(trade: BacktestTrade, level_matches: bool,
             created_is_bos: bool) -> str:
    """
    Provenance for the two coincidences a reader would otherwise suspect.

    Both are properties of the engine, not of this export, and both are
    MEASURED per row rather than asserted once in a docstring.
    """
    parts = [
        "entry_level==entry_price" if level_matches
        else "ENTRY_LEVEL_MISMATCH",
        "ob_creation_ts==bos_ts (OB admitted on its BOS candle)"
        if created_is_bos else "OB_CREATED_TS_DIFFERS_FROM_BOS",
        f"activation={ACTIVATION_MODE_FIRST_TOUCH}",
        f"entry_window={ENTRY_WINDOW_CANDLES}",
        f"displacement_mode={trade.displacement_mode}",
        f"strategy_version={trade.strategy_version}",
        f"ambiguous_bar={'true' if trade.is_ambiguous else 'false'}",
        f"quantized_at_fill={'true' if trade.quantized_at_fill else 'false'}",
    ]
    return "; ".join(parts)

def row_for_trade(
    pair: PairRun,
    trade: BacktestTrade,
    lock_ok: bool,
    source: str,
) -> Dict[str, str]:
    """One closed trade -> one fully populated CSV row."""
    created_bar, created_ts = pair.created.get(trade.ob_id, (None, None))
    touch_bar, touch_ts = pair.first_touch.get(trade.ob_id, (None, None))
    fill_bar, fill_ts = pair.filled.get(trade.ob_id, (None, None))
    if fill_ts is not None and fill_ts != trade.fill_dt:
        raise ExportIntegrityError(
            f"{trade.ob_id}: ENTRY_FILLED at {fill_ts.isoformat()} disagrees "
            f"with the ledger's fill_dt {trade.fill_dt.isoformat()}")

    origin_candle = pair.by_bar.get(trade.origin_bar_idx)
    entry_candle = pair.by_bar.get(trade.fill_bar_idx)
    exit_candle = pair.by_bar.get(trade.exit_bar_idx)
    creation_candle = (None if created_bar is None
                       else pair.by_bar.get(created_bar))
    touch_candle = None if touch_bar is None else pair.by_bar.get(touch_bar)

    level = entry_level_of(trade, pair.result.config.entry_depth_pct)
    level_matches = level == trade.entry_price
    rr_exact = rr_exact_of(trade)
    zone_label, zone_pct = zone_position_of(trade)
    sizing = trade.sizing_at_fill

    window_last = (None if touch_bar is None
                   else touch_bar + ENTRY_WINDOW_CANDLES - 1)
    in_window = (None if window_last is None else
                 touch_bar <= trade.fill_bar_idx <= window_last)

    row: Dict[str, str] = {
        # A) TRADE / IDENTIFICATION
        "pair": trade.asset,
        "trade_id": _int(trade.trade_id),
        "ob_setup_id": trade.ob_id,
        "direction": trade.direction,
        "strategy_name": trade.strategy_name,
        "timeframe": trade.data_timeframe,
        # B) OB CREATION / STRUCTURE
        "ob_creation_ts": _iso(created_ts),
        "ob_creation_date": _date(created_ts),
        "ob_creation_time": _time(created_ts),
        "bos_ts": _iso(trade.bos_dt),
        "bos_date": _date(trade.bos_dt),
        "bos_time": _time(trade.bos_dt),
        "ob_type": ob_type_of(trade),
        "ob_zone_upper": _num(trade.ob_top),
        "ob_zone_lower": _num(trade.ob_bottom),
        "ob_width": _num(trade.ob_width),
        "ob_width_pct": _num(pct_of_entry(trade.ob_width, trade.entry_price)),
        "ob_origin_candle_ts": _iso(trade.formation_dt),
        # C) SETUP / ENTRY
        "setup_created_ts": _iso(trade.displacement_confirmed_dt),
        "setup_status": pair.final_state.get(trade.ob_id, ""),
        "first_touch_ts": _iso(touch_ts),
        "first_touch_date": _date(touch_ts),
        "first_touch_time": _time(touch_ts),
        "entry_level": _num(level),
        "entry_price": _num(trade.entry_price),
        "actual_fill_ts": _iso(trade.fill_dt),
        "actual_fill_date": _date(trade.fill_dt),
        "actual_fill_time": _time(trade.fill_dt),
        "candles_from_ob_creation_to_entry": _int(trade.entry_bar_from_bos),
        "candles_from_ob_touch_to_entry": (
            "" if touch_bar is None else _int(trade.fill_bar_idx - touch_bar)),
        # D) POSITION PARAMETERS
        "sl_price": _num(trade.sl_price),
        "tp_price": _num(trade.tp_price),
        "risk_distance": _num(trade.risk_dist),
        "reward_distance": _num(trade.reward_dist),
        "risk_distance_pct": _num(trade.sl_dist_pct),
        "reward_distance_pct": _num(
            pct_of_entry(trade.reward_dist, trade.entry_price)),
        "rr_informational": _num(
            None if trade.risk_dist == 0.0
            else trade.reward_dist / trade.risk_dist),
        "rr_informational_exact": _dec(rr_exact),
        "leverage": _num(trade.theoretical_leverage),
        "applied_leverage": _num(trade.applied_leverage),
        "position_margin_usd": _num(sizing.margin_usd),
        "position_notional_usd": _num(trade.position_notional),
        # E) ACCOUNT / MONEY
        "starting_balance_usd": _num(STARTING_CAPITAL),
        "balance_before_trade_usd": _num(trade.starting_capital),
        "margin_used_usd": _num(sizing.margin_usd),
        "notional_usd": _num(trade.position_notional),
        "fees_usd": _num(trade.fees_usd),
        "gross_pnl_usd": _num(trade.gross_pnl_usd),
        "net_pnl_usd": _num(trade.net_pnl_usd),
        "realized_r": _num(trade.realized_r),
        "ending_balance_usd": _num(trade.ending_capital),
        # F) EXIT
        "exit_ts": _iso(trade.exit_dt),
        "exit_date": _date(trade.exit_dt),
        "exit_time": _time(trade.exit_dt),
        "exit_price": _num(trade.exit_price),
        "exit_reason": trade.reason_for_exit,
        "outcome": trade.outcome,
        "tp_hit": _bool(trade.is_win),
        "sl_hit": _bool(trade.is_loss),
        "holding_bars": _int(trade.holding_bars),
        "holding_hours": _num(trade.holding_time_hours),
        # G) TRADE LOCATION / MARKET CONTEXT
        "entry_candle_index": _int(trade.fill_bar_idx),
        "exit_candle_index": _int(trade.exit_bar_idx),
        "ob_creation_candle_index": _int(created_bar),
        "entry_price_vs_ob_position": zone_label,
        "distance_entry_to_ob_upper": _num(trade.ob_top - trade.entry_price),
        "distance_entry_to_ob_lower": _num(trade.entry_price - trade.ob_bottom),
        "distance_sl_from_entry": _num(abs(trade.sl_price - trade.entry_price)),
        "distance_tp_from_entry": _num(abs(trade.tp_price - trade.entry_price)),
        "entry_position_pct_of_ob": _num(zone_pct),
        "market_price_at_ob_creation": _num(
            None if creation_candle is None else creation_candle.close),
        "market_price_at_first_touch": _num(
            None if touch_candle is None else touch_candle.close),
        "market_price_at_entry": _num(
            None if entry_candle is None else entry_candle.close),
        # H) VALIDATION / AUDIT
        "rr_below_1_5": (
            "" if rr_exact is None
            else _bool(rr_exact < OBSERVATIONAL_RR_REFERENCE)),
        #: No reward:risk rule exists in the strategy, the lifecycle, the
        #: backtest driver or this export, so these two are structurally false.
        "rr_filter_applied": _bool(False),
        "rr_rejection": _bool(False),
        "entry_rule_valid": _bool(level_matches and in_window is True),
        "expiry_rule_valid": _bool(in_window),
        "one_trade_lock_valid": _bool(lock_ok),
        "data_source": source,
        "data_timezone": "UTC",
        "notes": note_for(trade, level_matches, created_ts == trade.bos_dt),
    }
    row.update(candle_fields(origin_candle, "ob_origin_candle"))
    row.pop("ob_origin_candle_volume", None)   # not a requested column
    row.update(candle_fields(entry_candle, "entry_candle"))
    row.update(candle_fields(exit_candle, "exit_candle"))
    for name in UNAVAILABLE_COLUMNS:
        row[name] = ""
    missing = [c for c in COLUMNS if c not in row]
    extra = [c for c in row if c not in COLUMNS]
    if missing or extra:
        raise ExportIntegrityError(
            f"row schema drift: missing={missing} extra={extra}")
    return row

# ---------------------------------------------------------------------------
# Data integrity, MEASURED over the candles the window actually contained.
# ---------------------------------------------------------------------------
def integrity_of(candles: Sequence[Candle]) -> Dict[str, Any]:
    """Gaps, duplicates, OHLC violations and timezone, counted not assumed."""
    stamps = [c.ts for c in candles]
    dups = sorted({s.isoformat() for s in stamps if stamps.count(s) > 1})
    gaps: List[Dict[str, Any]] = []
    for previous, current in zip(candles, candles[1:]):
        seconds = (current.ts - previous.ts).total_seconds()
        if seconds != 3600.0:
            gaps.append({"after": previous.ts.isoformat(),
                         "before": current.ts.isoformat(),
                         "delta_seconds": seconds,
                         "missing_hours": int(seconds // 3600) - 1})
    violations = [
        {"ts": c.ts.isoformat(), "open": c.open, "high": c.high,
         "low": c.low, "close": c.close}
        for c in candles
        if not (c.high >= c.low
                and c.high >= max(c.open, c.close)
                and c.low <= min(c.open, c.close))
    ]
    # `utcoffset()` returns timedelta(0) for UTC, which is FALSEY -- an
    # `if c.ts.utcoffset()` test would report genuine UTC as "no offset". The
    # comparison is against None explicitly for that reason.
    offsets = sorted({
        (None if c.ts.utcoffset() is None
         else c.ts.utcoffset().total_seconds())
        for c in candles}, key=lambda v: (v is None, v))
    naive = any(o is None for o in offsets)
    return {
        "candle_count": len(candles),
        "missing_candle_gaps": gaps,
        "missing_candles_total": sum(g["missing_hours"] for g in gaps),
        "duplicate_timestamps": dups,
        "ohlc_violations": violations,
        "timezone": ("UTC" if offsets == [0.0]
                     else f"{'NAIVE' if naive else 'NON_UTC'}:{offsets}"),
        "utc_offsets_seconds": offsets,
        "first_candle": (None if not candles else {
            "ts": candles[0].ts.isoformat(), "bar_idx": candles[0].bar_idx,
            "open": candles[0].open, "high": candles[0].high,
            "low": candles[0].low, "close": candles[0].close,
            "volume": candles[0].volume}),
        "last_candle": (None if not candles else {
            "ts": candles[-1].ts.isoformat(), "bar_idx": candles[-1].bar_idx,
            "open": candles[-1].open, "high": candles[-1].high,
            "low": candles[-1].low, "close": candles[-1].close,
            "volume": candles[-1].volume}),
    }

def summary_of(pair: PairRun, window: Sequence[Candle],
               rows: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """Per-pair aggregates. Every figure is computed off this pair alone."""
    trades = pair.result.trades
    ratios = [rr_exact_of(t) for t in trades]
    known = [r for r in ratios if r is not None]
    below = [r for r in known if r < OBSERVATIONAL_RR_REFERENCE]
    equity = [STARTING_CAPITAL] + [t.ending_capital for t in trades]
    n = len(trades)
    return {
        "pair": pair.symbol,
        "requested_start": REQUESTED_START.isoformat(),
        "requested_end": REQUESTED_END.isoformat(),
        "actual_start": (None if not window else window[0].ts.isoformat()),
        "actual_end": (None if not window else window[-1].ts.isoformat()),
        "coverage_clamped": bool(window) and window[-1].ts < REQUESTED_END,
        "hours_short_of_requested_end": (
            None if not window
            else (REQUESTED_END - window[-1].ts).total_seconds() / 3600.0),
        "candle_count": len(window),
        "candles_processed_by_engine": pair.result.candles_processed,
        "order_blocks_created": pair.result.order_blocks_created,
        "entry_blocks_active_trade": len(pair.result.entry_blocks),
        "open_trade_at_end": pair.result.open_trade_at_end,
        "csv_rows": len(rows),
        "total_trades": n,
        "TP_count": sum(1 for t in trades if t.is_win),
        "SL_count": sum(1 for t in trades if t.is_loss),
        "timeout_count": sum(1 for t in trades if t.is_timeout),
        "win_rate": (None if n == 0
                     else sum(1 for t in trades if t.is_win) / n * 100.0),
        "starting_balance": STARTING_CAPITAL,
        "final_balance": pair.result.ending_capital,
        "total_net_pnl": sum(t.net_pnl_usd for t in trades),
        "total_gross_pnl": sum(t.gross_pnl_usd for t in trades),
        "total_fees": sum(t.fees_usd for t in trades),
        "max_drawdown_pct": pair.result.max_drawdown_pct,
        "peak_balance": max(equity),
        "trough_balance": min(equity),
        "average_hold_hours": (None if n == 0
                               else sum(t.holding_time_hours
                                        for t in trades) / n),
        "average_leverage": (None if n == 0
                             else sum(t.applied_leverage for t in trades) / n),
        "minimum_rr": (None if not known else format(min(known), "f")),
        "maximum_rr": (None if not known else format(max(known), "f")),
        "trades_below_1_5_rr": len(below),
        "percentage_below_1_5_rr": (None if n == 0 else len(below) / n * 100.0),
        "trades_with_undefined_rr": len(ratios) - len(known),
        "rr_filter_applied": False,
        "rr_rejections": 0,
        "lifecycle_event_counts": dict(sorted(pair.event_counts.items())),
        "data_integrity": integrity_of(window),
    }

# ---------------------------------------------------------------------------
# The 13 requested verification steps. Every one re-reads what was WRITTEN.
# ---------------------------------------------------------------------------
class Check:
    """One verification step: a measured pass/fail plus its evidence."""

    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.failures: List[str] = []
        self.evidence: List[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def note(self, message: str) -> None:
        self.evidence.append(message)

    @property
    def passed(self) -> bool:
        return not self.failures

    def render(self) -> str:
        head = (f"  [{'PASS' if self.passed else 'FAIL'}] "
                f"{self.number:>2}. {self.title}")
        lines = [head]
        lines.extend(f"        {e}" for e in self.evidence)
        lines.extend(f"        !! {f}" for f in self.failures)
        return "\n".join(lines)


def read_back(path: Path) -> List[Dict[str, str]]:
    """Re-open a written CSV and return its rows. Step 12's actual evidence."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ExportIntegrityError(
                f"{path}: header does not match the requested column order")
        return list(reader)

def verify(
    runs: Dict[str, PairRun],
    paths: Dict[str, Path],
    on_disk: Dict[str, List[Dict[str, str]]],
) -> List[Check]:
    """Run all 13 steps against the engine ledgers and the written files."""
    checks: List[Check] = []

    c1 = Check(1, "Row count per CSV")
    for symbol in SYMBOLS:
        c1.note(f"{symbol}: {len(on_disk[symbol])} data rows "
                f"(+1 header) in {paths[symbol].name}")
    checks.append(c1)

    c2 = Check(2, "Every executed trade has exactly ONE CSV row")
    for symbol in SYMBOLS:
        ledger = runs[symbol].result.trades
        counts: Dict[str, int] = {}
        for row in on_disk[symbol]:
            counts[row["trade_id"]] = counts.get(row["trade_id"], 0) + 1
        c2.require(len(on_disk[symbol]) == len(ledger),
                   f"{symbol}: {len(on_disk[symbol])} rows vs "
                   f"{len(ledger)} executed trades")
        for trade in ledger:
            c2.require(counts.get(str(trade.trade_id)) == 1,
                       f"{symbol}: trade {trade.trade_id} has "
                       f"{counts.get(str(trade.trade_id), 0)} rows")
        c2.note(f"{symbol}: {len(ledger)} executed -> "
                f"{len(on_disk[symbol])} rows, 1:1")
    checks.append(c2)

    c3 = Check(3, "No executed trade is missing")
    for symbol in SYMBOLS:
        expected = {str(t.trade_id) for t in runs[symbol].result.trades}
        written = {r["trade_id"] for r in on_disk[symbol]}
        c3.require(expected == written,
                   f"{symbol}: missing={sorted(expected - written)} "
                   f"unexpected={sorted(written - expected)}")
        span = sorted(int(v) for v in expected)
        c3.note(f"{symbol}: trade-id sets identical ({len(expected)} ids, "
                f"{span[0] if span else '-'}..{span[-1] if span else '-'})")
    checks.append(c3)

    c4 = Check(4, "No duplicate trade IDs")
    for symbol in SYMBOLS:
        ids = [r["trade_id"] for r in on_disk[symbol]]
        c4.require(len(ids) == len(set(ids)),
                   f"{symbol}: {len(ids) - len(set(ids))} duplicate id(s)")
        c4.note(f"{symbol}: {len(set(ids))} unique of {len(ids)}")
    checks.append(c4)

    c5 = Check(5, "Chronological ordering by actual fill timestamp")
    for symbol in SYMBOLS:
        stamps = [r["actual_fill_ts"] for r in on_disk[symbol]]
        c5.require(stamps == sorted(stamps),
                   f"{symbol}: fill timestamps are not ascending")
        ids = [int(r["trade_id"]) for r in on_disk[symbol]]
        c5.require(ids == sorted(ids),
                   f"{symbol}: engine trade order not preserved")
        c5.note(f"{symbol}: {len(stamps)} rows ascending by actual_fill_ts; "
                f"trade_id order preserved")
    checks.append(c5)

    c6 = Check(6, "Entry timestamp strictly precedes exit timestamp")
    for symbol in SYMBOLS:
        for row in on_disk[symbol]:
            c6.require(row["actual_fill_ts"] < row["exit_ts"],
                       f"{symbol} trade {row['trade_id']}: "
                       f"{row['actual_fill_ts']} !< {row['exit_ts']}")
            c6.require(int(row["exit_candle_index"])
                       > int(row["entry_candle_index"]),
                       f"{symbol} trade {row['trade_id']}: exit bar not after "
                       f"entry bar")
        c6.note(f"{symbol}: all {len(on_disk[symbol])} rows have fill < exit")
    checks.append(c6)

    c7 = Check(7, "TP / SL / exit-reason consistency")
    for symbol in SYMBOLS:
        seen: Dict[str, int] = {}
        for row in on_disk[symbol]:
            outcome, reason = row["outcome"], row["exit_reason"]
            seen[f"{outcome}/{reason}"] = seen.get(f"{outcome}/{reason}", 0) + 1
            c7.require(row["tp_hit"] == _bool(outcome == OUTCOME_TP),
                       f"{symbol} trade {row['trade_id']}: tp_hit disagrees "
                       f"with outcome {outcome}")
            c7.require(row["sl_hit"] == _bool(outcome == OUTCOME_SL),
                       f"{symbol} trade {row['trade_id']}: sl_hit disagrees "
                       f"with outcome {outcome}")
            c7.require(not (row["tp_hit"] == "true" and row["sl_hit"] == "true"),
                       f"{symbol} trade {row['trade_id']}: both TP and SL")
            price = float(row["exit_price"])
            expected = (float(row["tp_price"]) if outcome == OUTCOME_TP
                        else float(row["sl_price"]) if outcome == OUTCOME_SL
                        else None)
            c7.require(expected is None or price == expected,
                       f"{symbol} trade {row['trade_id']}: exit price {price} "
                       f"is not the {outcome} price {expected}")
        c7.note(f"{symbol}: " + ", ".join(f"{k}={v}"
                                          for k, v in sorted(seen.items())))
    checks.append(c7)

    c8 = Check(8, "entry / SL / TP equal the strategy's own calculation")
    for symbol in SYMBOLS:
        cfg = runs[symbol].result.config
        for trade in runs[symbol].result.trades:
            level = entry_level_of(trade, cfg.entry_depth_pct)
            sl = (trade.ob_bottom if trade.direction == "LONG"
                  else trade.ob_top)
            factor = (1.0 + cfg.fixed_tp_market_pct / 100.0
                      if trade.direction == "LONG"
                      else 1.0 - cfg.fixed_tp_market_pct / 100.0)
            c8.require(trade.entry_price == level,
                       f"{symbol} trade {trade.trade_id}: entry "
                       f"{trade.entry_price!r} != {cfg.entry_depth_pct:.0%} "
                       f"level {level!r}")
            c8.require(trade.sl_price == sl,
                       f"{symbol} trade {trade.trade_id}: SL "
                       f"{trade.sl_price!r} is not the distal edge {sl!r}")
            c8.require(trade.tp_price == trade.entry_price * factor,
                       f"{symbol} trade {trade.trade_id}: TP "
                       f"{trade.tp_price!r} != entry * {factor!r}")
        c8.note(f"{symbol}: entry=={cfg.entry_depth_pct:.0%} of zone, "
                f"SL==opposite edge, TP==entry*(1±"
                f"{cfg.fixed_tp_market_pct}%) for all "
                f"{len(runs[symbol].result.trades)} trades")
    checks.append(c8)

    c9 = Check(9, "R:R is informational only")
    for symbol in SYMBOLS:
        rows = on_disk[symbol]
        c9.require(all(r["rr_filter_applied"] == "false" for r in rows),
                   f"{symbol}: rr_filter_applied is not false in every row")
        c9.require(all(r["rr_rejection"] == "false" for r in rows),
                   f"{symbol}: rr_rejection is not false in every row")
        populated = sum(1 for r in rows if r["rr_informational_exact"])
        c9.require(populated == len(rows),
                   f"{symbol}: {len(rows) - populated} row(s) carry no ratio")
        below = sum(1 for r in rows if r["rr_below_1_5"] == "true")
        c9.note(f"{symbol}: ratio recorded on {populated}/{len(rows)} rows; "
                f"{below} below 1.5 and ALL of them present in the CSV")
    checks.append(c9)

    c10 = Check(10, "Zero trades were rejected because of R:R")
    for symbol in SYMBOLS:
        codes: Dict[str, int] = {}
        for block in runs[symbol].result.entry_blocks:
            key = block.rejection_code or "UNCODED"
            codes[key] = codes.get(key, 0) + 1
        for code in codes:
            c10.require("RISK_REWARD" not in code.upper(),
                        f"{symbol}: refusal code {code} mentions risk/reward")
        offenders = [e for e in runs[symbol].event_counts
                     if "RISK" in e.upper() or "REWARD" in e.upper()]
        c10.require(not offenders,
                    f"{symbol}: lifecycle emitted {offenders}")
        c10.note(f"{symbol}: entry-refusal codes {codes or '{}'}; none is a "
                 f"reward:risk refusal")
    checks.append(c10)

    c11 = Check(11, "The four pairs keep SEPARATE balances")
    finals: Dict[str, float] = {}
    for symbol in SYMBOLS:
        result = runs[symbol].result
        c11.require(result.starting_capital == STARTING_CAPITAL,
                    f"{symbol}: starts at {result.starting_capital}, "
                    f"not {STARTING_CAPITAL}")
        c11.require(result.symbols == (symbol,),
                    f"{symbol}: driver also carried {result.symbols}")
        balance = STARTING_CAPITAL
        for trade in result.trades:
            c11.require(trade.starting_capital == balance,
                        f"{symbol} trade {trade.trade_id}: opens at "
                        f"{trade.starting_capital!r}, chain says {balance!r}")
            c11.require(trade.asset == symbol,
                        f"{symbol}: ledger contains {trade.asset}")
            balance = trade.ending_capital
        c11.require(balance == result.ending_capital,
                    f"{symbol}: chain ends {balance!r} vs reported "
                    f"{result.ending_capital!r}")
        finals[symbol] = result.ending_capital
        c11.note(f"{symbol}: {STARTING_CAPITAL} -> {result.ending_capital!r} "
                 f"over {len(result.trades)} trades, own chain")
    c11.require(len(set(map(repr, finals.values()))) == len(finals),
                f"two pairs share a final balance: {finals}")
    checks.append(c11)

    c12 = Check(12, "Every CSV re-opens and parses")
    for symbol in SYMBOLS:
        path = paths[symbol]
        c12.require(path.exists(), f"{symbol}: {path} does not exist")
        c12.require(len(on_disk[symbol]) > 0, f"{symbol}: no data rows")
        widths = {len(r) for r in on_disk[symbol]}
        c12.require(widths == {len(COLUMNS)},
                    f"{symbol}: row widths {widths} != {len(COLUMNS)}")
        c12.note(f"{symbol}: parsed {len(on_disk[symbol])} rows x "
                 f"{len(COLUMNS)} columns, {path.stat().st_size} bytes")
    checks.append(c12)

    c13 = Check(13, "Exact file paths and row counts")
    for symbol in SYMBOLS:
        c13.note(f"{paths[symbol]}  ->  {len(on_disk[symbol])} rows")
    checks.append(c13)
    return checks

def window_candles(candles: Sequence[Candle]) -> List[Candle]:
    """The candles the requested window actually contained. No fabrication."""
    return [c for c in candles
            if REQUESTED_START <= c.ts <= REQUESTED_END]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output directory (default {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    base = default_canonical_base()
    dataset = load_canonical_dataset(base, SYMBOLS)

    runs: Dict[str, PairRun] = {}
    paths: Dict[str, Path] = {}
    summaries: List[Dict[str, Any]] = []
    windows: Dict[str, List[Candle]] = {}

    for symbol in SYMBOLS:
        candles = dataset[symbol]
        if not candles:
            raise ExportIntegrityError(
                f"{symbol}: no canonical candles under {base}")
        print(f"[{symbol}] driving the engine over the requested window ...")
        pair = PairRun(symbol, candles)
        prove_same_run(pair)
        runs[symbol] = pair
        windows[symbol] = window_candles(candles)

        source = str((base / symbol / "1h" / "full_history.csv"))
        locks = lock_respected(pair.result.trades)
        rows = [row_for_trade(pair, trade, locks[trade.trade_id], source)
                for trade in sorted(pair.result.trades,
                                    key=lambda t: (t.fill_dt, t.trade_id))]

        path = out_dir / f"{symbol}_recent_trades_{LABEL}.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS,
                                    extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
        paths[symbol] = path
        summaries.append(summary_of(pair, windows[symbol], rows))
        print(f"[{symbol}] {len(rows)} trades -> {path.name}")

    on_disk = {s: read_back(paths[s]) for s in SYMBOLS}
    checks = verify(runs, paths, on_disk)

    summary_path = out_dir / f"recent_trade_summary_{LABEL}.json"
    payload = {
        "report": "manual_smc_recent_trades",
        "generated_for_window": LABEL,
        "requested_start": REQUESTED_START.isoformat(),
        "requested_end": REQUESTED_END.isoformat(),
        "starting_capital_per_pair": STARTING_CAPITAL,
        "balances_are_shared_between_pairs": False,
        "strategy": {
            "name": runs[SYMBOLS[0]].result.strategy_name,
            "version": runs[SYMBOLS[0]].result.strategy_version,
            "activation_mode": ACTIVATION_MODE_FIRST_TOUCH,
            "entry_window_candles": ENTRY_WINDOW_CANDLES,
            "config": vars(runs[SYMBOLS[0]].result.config),
        },
        "risk_reward": {
            "admission_rule_exists": False,
            "filter_applied": False,
            "rejections": 0,
            "role": "informational metric only; recorded, never consulted",
            "observational_reference_only": str(OBSERVATIONAL_RR_REFERENCE),
        },
        "unavailable_columns": UNAVAILABLE_COLUMNS,
        "csv_files": {s: str(paths[s]) for s in SYMBOLS},
        "csv_row_counts": {s: len(on_disk[s]) for s in SYMBOLS},
        "verification": [
            {"step": c.number, "title": c.title,
             "passed": c.passed, "evidence": c.evidence,
             "failures": c.failures}
            for c in checks
        ],
        "all_verification_passed": all(c.passed for c in checks),
        "pairs": summaries,
    }
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")

    print("\n=== VERIFICATION ===")
    for check in checks:
        print(check.render())
    failed = [c.number for c in checks if not c.passed]
    print(f"\nsummary json: {summary_path}")
    print(f"steps passed: {len(checks) - len(failed)}/{len(checks)}"
          + ("" if not failed else f"   FAILED: {failed}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
