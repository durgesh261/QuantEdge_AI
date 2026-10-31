#!/usr/bin/env python3
"""
5-DAY MANUAL SMC / TRADINGVIEW PARITY DIAGNOSTIC  (Parts 3-7).
==============================================================

READ-ONLY OBSERVER. This script imports the production strategy and drives it
through its OWN public generator `ManualSMCBacktest.iter_run`. It contains no
strategy rule, no threshold, no filter and no sizing arithmetic of its own:
every price, state, leverage and PnL value written to CSV is TRANSCRIBED from
`ManualLifecycleEvent`, `ManualSMCSetup` or `BacktestTrade`.

  * No production file is imported-and-patched, monkeypatched or subclassed.
  * No reward:risk gate is added. R:R is computed for the ledger only.
  * No network call, no credential read, no exchange contact.
  * Writes only under engine/data/research/manual_smc_5day_parity/.

TWO RUNS, BOTH REPORTED
-----------------------
WARM-START (primary) processes the FULL extended history from 2024-06-01 and
CLIPS the ledgers to the 5-day window. This is the TradingView-comparable view:
an order block created weeks earlier is still drawn on the chart and still
tradable, so a run that begins at the window would be structurally unable to
reproduce it.

COLD-START (contrast) passes a window-filtered timeline. `build_timeline` drops
rows without renumbering `bar_idx`, so the lifecycle opens the window with an
EMPTY order-block pool and an EMPTY 10-bar scanner deque. It is reported to
MEASURE how much of the window's behaviour depends on pre-window state.

FIELDS THAT ARE LEFT BLANK ON PURPOSE
-------------------------------------
The task says: "If a field does not exist in the engine, leave it blank. DO NOT
invent values." These have NO engine source and are therefore written empty:

  bos_or_choch / choch_or_bos   the engine has ONE break concept and no
                                BOS-vs-CHOCH classifier
  broken_swing_price            the broken level is a single candle's extreme,
                                not a swing pivot; no swing model exists
  swing_high_timestamp,
  swing_low_timestamp           no swing-pivot detection exists
  structure_state_before/after  the engine holds no market-structure state
  active_bos, active_choch      no BOS/CHOCH object is ever retained
  first_touch_price             the engine records the touch TIMESTAMP only
  entry_trigger_ts              exactly one fill concept exists (ENTRY_FILLED)
  quantity                      PositionSizing is USD/leverage only and its
                                docstring says "NO quantity field"

`modified` and `replaced` are written FALSE rather than blank because they are
MEASURED impossibilities, not unknowns: no code path anywhere in engine/ assigns
to ob_top / ob_bottom / ob_width / proximal / distal / entry_price / sl_price /
tp_price after `_make_manual_ob` returns, and no code path substitutes one order
block for another. That was verified by grep over the whole package.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine" / "src"))

from quantedge.strategy.manual_smc.backtest import (      # noqa: E402
    BacktestResult, BacktestTrade, Candle, ManualSMCBacktest,
    build_timeline, load_canonical_candles,
)
from quantedge.strategy.manual_smc.lifecycle import (     # noqa: E402
    ManualLifecycleEventType,
)
from quantedge.strategy.manual_smc.models import (        # noqa: E402
    ManualOBState,
)

SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
TIMEFRAME = "1h"

WINDOW_START = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
REQUESTED_START = "2026-08-28T00:00:00+00:00"
REQUESTED_END = "2026-09-02T09:00:00+00:00"

OUT_ROOT = (REPO_ROOT / "engine" / "data" / "research"
            / "manual_smc_5day_parity")
EXTENDED_BASE = OUT_ROOT / "canonical_extended" / "delta_exchange_india"
SUMMARY_PATH = (OUT_ROOT
                / "recent_5day_parity_summary_2026-08-28_to-2026-09-02.json")

MODE_WARM = "warm_start"
MODE_COLD = "cold_start"


class DiagnosticError(RuntimeError):
    """Any refusal. Nothing degrades silently."""


# ---------------------------------------------------------------------------
# Column definitions — EXACTLY the fields the task names, in its own order
# ---------------------------------------------------------------------------
LEDGER_COLUMNS: Tuple[str, ...] = (
    # identification
    "pair", "event_id", "event_ts", "event_candle_index", "event_type",
    # structure
    "structure_direction", "bos_or_choch", "structure_reference_price",
    "broken_swing_price", "swing_high_timestamp", "swing_low_timestamp",
    "structure_state_before", "structure_state_after",
    # order block
    "ob_setup_id", "ob_creation_ts", "ob_origin_ts", "ob_type",
    "ob_zone_upper", "ob_zone_lower", "ob_width", "ob_status_before",
    "ob_status_after", "ob_invalidated_ts", "ob_invalidation_reason",
    # lifecycle flags
    "created", "modified", "replaced", "invalidated", "expired", "touched",
    "traded", "cancelled",
    # reason / audit
    "reason", "source_candle", "strategy_state", "notes",
)

TRADE_COLUMNS: Tuple[str, ...] = (
    "pair", "trade_id", "ob_setup_id", "direction", "strategy", "timeframe",
    "ob_creation_ts", "ob_origin_ts", "bos_ts", "choch_or_bos", "ob_type",
    "ob_zone_upper", "ob_zone_lower", "ob_width", "ob_width_pct",
    "first_touch_ts", "first_touch_price", "touch_candle_open",
    "touch_candle_high", "touch_candle_low", "touch_candle_close",
    "entry_trigger_ts", "entry_level", "actual_fill_ts", "actual_fill_price",
    "entry_candle_open", "entry_candle_high", "entry_candle_low",
    "entry_candle_close",
    "sl_price", "tp_price", "risk_distance", "reward_distance",
    "rr_informational", "applied_leverage",
    "balance_before_trade", "margin", "notional", "quantity", "fees",
    "exit_ts", "exit_price", "exit_reason", "outcome", "holding_bars",
    "holding_hours",
    "gross_pnl", "net_pnl", "realized_r", "balance_after_trade",
    "entry_rule_valid", "expiry_rule_valid", "rr_filter_applied",
    "rr_rejection", "notes",
)

SNAPSHOT_COLUMNS: Tuple[str, ...] = (
    "timestamp", "candle_open", "candle_high", "candle_low", "candle_close",
    "active_bos", "active_choch", "latest_structure_direction",
    "active_ob_count", "active_ob_ids", "latest_ob_id", "latest_ob_zone",
    "invalidated_ob_ids", "expired_ob_ids", "touched_ob_ids",
    "active_trade", "trade_id", "pending_entry", "pending_entry_level",
)

#: The literal empty cell. Named so "this field has no engine source" is
#: visually distinct in the code from "this value happened to be zero".
BLANK = ""


def iso(value: Optional[datetime]) -> str:
    """UTC ISO-8601, or BLANK. Never a naive stamp, never a guess."""
    if value is None:
        return BLANK
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DiagnosticError(
            f"{value!r} carries no timezone; refusing to assume UTC for a "
            f"diagnostic timestamp (safety rule #13)")
    return value.astimezone(timezone.utc).isoformat()


def num(value: Optional[float], places: int = 8) -> str:
    """Fixed-precision rendering. BLANK for None; never rounds to hide a zero."""
    if value is None:
        return BLANK
    return f"{float(value):.{places}f}"


def in_window(ts: datetime) -> bool:
    return WINDOW_START <= ts <= WINDOW_END


def ob_type_of(direction: str) -> str:
    """
    The chart-language name for the block, from the engine's own direction.

    A SHORT setup is drawn from a BULLISH origin candle and traded downward,
    which is what every SMC dialect calls a BEARISH order block. This is a
    rename of `direction`, not an inference about structure.
    """
    if direction == "SHORT":
        return "BEARISH_OB"
    if direction == "LONG":
        return "BULLISH_OB"
    raise DiagnosticError(f"unknown direction {direction!r}")


def structure_direction_of(direction: str) -> str:
    """
    The direction of the CLOSE BREAK that created the block.

    scanner.py admits a SHORT when `c < ob_bottom` (a downside close break) and
    a LONG when `c > ob_top` (an upside close break). Transcription of a branch
    condition, not a market-structure trend state — the engine holds none.
    """
    return "BEARISH_BREAK" if direction == "SHORT" else "BULLISH_BREAK"


# ---------------------------------------------------------------------------
# One pair, one mode — driven through the engine's own public generator
# ---------------------------------------------------------------------------
class PairRun:
    """
    Everything one (symbol, mode) run produced. A COLLECTOR, not a model.

    `ob_geo` is filled from `ManualSMCEvaluation.setups`, which is the engine's
    own immutable projection of its live pool. A lifecycle event carries no
    geometry, so the zone printed beside an INVALIDATED event is the zone the
    engine last projected for that block — never a recomputed one.
    """

    def __init__(self, symbol: str, mode: str) -> None:
        self.symbol = symbol
        self.mode = mode
        self.window_events: List[Tuple[int, datetime, Any]] = []
        self.snapshots: List[Dict[str, Any]] = []
        self.ob_geo: "OrderedDict[str, Any]" = OrderedDict()
        self.ob_created_ts: Dict[str, datetime] = {}
        self.ob_first_touch_ts: Dict[str, datetime] = {}
        self.ob_invalidated: Dict[str, Tuple[datetime, str]] = {}
        self.ob_state_seen: Dict[str, str] = {}
        self.candles_by_ts: Dict[datetime, Candle] = {}
        self.candles_by_idx: Dict[int, Candle] = {}
        self.result: Optional[BacktestResult] = None
        self.candles_processed = 0
        self.window_candles = 0
        self.first_ts: Optional[datetime] = None
        self.last_ts: Optional[datetime] = None
        self.balance_at_window_open: Optional[float] = None
        self.event_counts_window: Dict[str, int] = {}
        self.event_counts_run: Dict[str, int] = {}
        self.obs_alive_at_window_open: List[Any] = []
        self.last_break_direction: Optional[str] = None
        self.last_break_ts: Optional[datetime] = None

    # -- derived views -----------------------------------------------------
    def trade_id_for_ob(self) -> Dict[str, int]:
        assert self.result is not None
        return {t.ob_id: t.trade_id for t in self.result.trades}


EV = ManualLifecycleEventType
_TOUCH_EVENTS = frozenset({EV.FIRST_TOUCH_LIMIT_ACTIVATED,
                           EV.PRE_DISPLACEMENT_TOUCH})


def is_window_expiry(detail: str) -> bool:
    """
    Did this INVALIDATED event come from the entry-window expiry branch?

    Matched on the engine's OWN message (lifecycle.py:973-978, "...-candle entry
    window [a..b] expired without an admitted fill at X; order cancelled and
    this OB is permanently invalid"). The other three INVALIDATED branches say
    "distal wick breach ..." or "operator-invalidated ...", so the pair of
    substrings below separates them with no overlap.
    """
    return "entry window" in detail and "expired" in detail


def run_pair(symbol: str, mode: str) -> PairRun:
    """Drive ONE pair through the unmodified engine and collect what it says."""
    run = PairRun(symbol, mode)
    candles = load_canonical_candles(EXTENDED_BASE, symbol)
    if not candles:
        raise DiagnosticError(
            f"{symbol}: no candles under {EXTENDED_BASE}; the extension "
            f"download must run first")
    run.candles_by_ts = {c.ts: c for c in candles}
    run.candles_by_idx = {c.bar_idx: c for c in candles}

    if mode == MODE_WARM:
        timeline = build_timeline({symbol: candles}, symbols=(symbol,))
    elif mode == MODE_COLD:
        timeline = build_timeline({symbol: candles}, symbols=(symbol,),
                                  start_date=WINDOW_START,
                                  end_date=WINDOW_END)
    else:
        raise DiagnosticError(f"unknown mode {mode!r}")
    if not timeline:
        raise DiagnosticError(f"{symbol}/{mode}: empty timeline")

    #: Production defaults on purpose: production config (0.60% TP),
    #: FIRST_TOUCH_WINDOW activation, 3-candle window, one trade at a time.
    driver = ManualSMCBacktest(symbols=(symbol,))

    inv_ids: List[str] = []
    exp_ids: List[str] = []
    touched_ids: List[str] = []
    _collect(run, driver, timeline, candles, inv_ids, exp_ids, touched_ids)
    run.result = driver.result()
    return run


def _bump(counter: Dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _collect(
    run: PairRun,
    driver: ManualSMCBacktest,
    timeline: Sequence[Any],
    candles: Sequence[Candle],
    inv_ids: List[str],
    exp_ids: List[str],
    touched_ids: List[str],
) -> None:
    """
    Walk `iter_run` once and transcribe. Calls nothing that could alter state.

    The cumulative id lists are accumulated FROM THE WINDOW OPEN, not from the
    run's first candle: a warm-start run invalidates thousands of order blocks
    over two years and a snapshot cell holding all of them would be unreadable
    and would not describe the 5-day chart the operator is comparing against.
    """
    for ev in driver.iter_run(timeline, {run.symbol: candles}):
        run.candles_processed += 1
        if run.first_ts is None:
            run.first_ts = ev.ts
        run.last_ts = ev.ts
        inside = in_window(ev.ts)

        if inside and run.balance_at_window_open is None:
            #: `account_balance` on the evaluation is the balance AFTER this
            #: candle settled. Captured on the first in-window candle, which is
            #: the closest observable to "balance entering the window".
            run.balance_at_window_open = ev.account_balance
            run.obs_alive_at_window_open = [
                s for s in ev.setups]

        for event in ev.events:
            name = event.event_type.name
            _bump(run.event_counts_run, name)
            if event.event_type is EV.OB_CREATED:
                run.ob_created_ts[event.ob_id] = event.ts
                run.last_break_direction = structure_direction_of(
                    event.direction)
                run.last_break_ts = event.ts
            if event.event_type in _TOUCH_EVENTS:
                run.ob_first_touch_ts.setdefault(event.ob_id, event.ts)
                if inside and event.ob_id not in touched_ids:
                    touched_ids.append(event.ob_id)
            if event.event_type is EV.INVALIDATED:
                run.ob_invalidated[event.ob_id] = (event.ts, event.detail)
                if inside:
                    if event.ob_id not in inv_ids:
                        inv_ids.append(event.ob_id)
                    if (is_window_expiry(event.detail)
                            and event.ob_id not in exp_ids):
                        exp_ids.append(event.ob_id)
            if inside:
                _bump(run.event_counts_window, name)
                run.window_events.append((ev.bar_idx, ev.ts, event,
                                          run.ob_state_seen.get(event.ob_id,
                                                                BLANK)))
            #: After the row is stamped, so `ob_status_before` is the state the
            #: engine held when the candle opened this transition and
            #: `ob_status_after` is `event.state` (read post-mutation by _emit).
            run.ob_state_seen[event.ob_id] = event.state.name

        # -- geometry registry: the engine's own projection of its live pool --
        for setup in ev.setups:
            run.ob_geo[setup.ob_id] = setup

        if not inside:
            continue
        run.window_candles += 1
        candle = run.candles_by_idx[ev.bar_idx]
        live = list(ev.setups)
        resting = [s for s in live
                   if s.state is ManualOBState.LIMIT_RESTING]
        newest = max(live, key=lambda s: (s.bos_bar_idx, s.ob_id)) if live \
            else None
        active = ev.active_trade
        run.snapshots.append({
            "timestamp": iso(ev.ts),
            "candle_open": num(candle.open), "candle_high": num(candle.high),
            "candle_low": num(candle.low), "candle_close": num(candle.close),
            #: No BOS or CHOCH object is ever retained by the engine.
            "active_bos": BLANK, "active_choch": BLANK,
            "latest_structure_direction": run.last_break_direction or BLANK,
            "active_ob_count": str(len(live)),
            "active_ob_ids": ";".join(s.ob_id for s in live),
            "latest_ob_id": newest.ob_id if newest else BLANK,
            "latest_ob_zone": (f"{newest.ob_bottom:.8f}-{newest.ob_top:.8f}"
                               if newest else BLANK),
            "invalidated_ob_ids": ";".join(inv_ids),
            "expired_ob_ids": ";".join(exp_ids),
            "touched_ob_ids": ";".join(touched_ids),
            "active_trade": "TRUE" if active is not None else "FALSE",
            "_active_trade_ob_id": active.ob.ob_id if active else BLANK,
            "pending_entry": "TRUE" if resting else "FALSE",
            "pending_entry_level": ";".join(num(s.entry_price)
                                            for s in resting),
        })


# ---------------------------------------------------------------------------
# PART 4 — structure / OB event ledger
# ---------------------------------------------------------------------------
#: Event types that ARE a structure event. Only these carry the STRUCTURE
#: block: a touch, a fill or an expiry candle produced no break, and stamping a
#: break direction on such a row would imply structure the engine never saw.
_STRUCTURE_EVENTS = frozenset({EV.OB_CREATED, EV.OB_CREATION_SUPPRESSED})

_NOTE_NO_STRUCTURE_MODEL = (
    "engine has no swing-pivot model and no BOS/CHOCH classifier; the break is "
    "a single close through the most recent opposite-colour candle's extreme "
    "(scanner.py:110-161), so bos_or_choch / broken_swing_price / "
    "swing_*_timestamp / structure_state_* have no engine source"
)


def ledger_rows(run: PairRun) -> List[Dict[str, str]]:
    """One row per lifecycle event inside the window. Pure transcription."""
    by_ts = {s["timestamp"]: s for s in run.snapshots}
    rows: List[Dict[str, str]] = []
    for seq, (bar_idx, ts, event, before) in enumerate(run.window_events, 1):
        geo = run.ob_geo.get(event.ob_id)
        etype = event.event_type
        is_struct = etype in _STRUCTURE_EVENTS
        is_inv = etype is EV.INVALIDATED
        expired = is_inv and is_window_expiry(event.detail)
        candle = run.candles_by_idx[bar_idx]
        snap = by_ts.get(iso(ts), {})
        ref_price = BLANK
        if is_struct and geo is not None:
            ref_price = num(geo.ob_bottom if event.direction == "SHORT"
                            else geo.ob_top)
        notes = [] if is_struct else [""]
        if is_struct:
            notes.append(_NOTE_NO_STRUCTURE_MODEL)
        if geo is None:
            notes.append("no engine projection retained for this ob_id "
                         "(never entered the live pool)")
        rows.append({
            "pair": run.symbol,
            "event_id": f"{run.symbol}_{run.mode.upper()}_{seq:05d}",
            "event_ts": iso(ts),
            "event_candle_index": str(bar_idx),
            "event_type": etype.name,
            "structure_direction": (structure_direction_of(event.direction)
                                    if is_struct else BLANK),
            "bos_or_choch": BLANK,
            "structure_reference_price": ref_price,
            "broken_swing_price": BLANK,
            "swing_high_timestamp": BLANK,
            "swing_low_timestamp": BLANK,
            "structure_state_before": BLANK,
            "structure_state_after": BLANK,
            "ob_setup_id": event.ob_id,
            "ob_creation_ts": iso(run.ob_created_ts.get(event.ob_id)),
            "ob_origin_ts": iso(geo.formation_dt) if geo else BLANK,
            "ob_type": ob_type_of(event.direction),
            "ob_zone_upper": num(geo.ob_top) if geo else BLANK,
            "ob_zone_lower": num(geo.ob_bottom) if geo else BLANK,
            "ob_width": num(geo.ob_width) if geo else BLANK,
            "ob_status_before": before,
            "ob_status_after": event.state.name,
            "ob_invalidated_ts": iso(ts) if is_inv else BLANK,
            "ob_invalidation_reason": event.detail if is_inv else BLANK,
            "created": str(etype is EV.OB_CREATED).upper(),
            #: MEASURED impossibilities, not unknowns — see module docstring.
            "modified": "FALSE",
            "replaced": "FALSE",
            "invalidated": str(is_inv).upper(),
            "expired": str(expired).upper(),
            "touched": str(etype in _TOUCH_EVENTS).upper(),
            "traded": str(etype is EV.ENTRY_FILLED).upper(),
            #: The engine's own expiry message says "order cancelled"; there is
            #: no separate CANCELLED event, so expiry IS the cancellation.
            "cancelled": str(expired).upper(),
            "reason": event.detail,
            "source_candle": (f"bar={bar_idx} O={candle.open} H={candle.high} "
                              f"L={candle.low} C={candle.close}"),
            "strategy_state": (
                f"post_candle: live_obs={snap.get('active_ob_count', '?')} "
                f"active_trade={snap.get('active_trade', '?')} "
                f"pending_entry={snap.get('pending_entry', '?')}"),
            "notes": "; ".join(n for n in notes if n),
        })
    return rows


# ---------------------------------------------------------------------------
# PART 5 — trade ledger
# ---------------------------------------------------------------------------
def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _entry_rule_valid(trade: BacktestTrade) -> Tuple[bool, str]:
    """
    Re-derive the AUTHORISED geometry and compare. A CHECK, not a rule.

    LONG entry = ob_top - 25% of width, SHORT entry = ob_bottom + 25% of width,
    SL = the opposite (distal) edge, TP = entry (1 -/+ 0.60%). Nothing here can
    change what the engine did; a mismatch is reported, never corrected.
    """
    width = trade.ob_width
    if trade.direction == "LONG":
        want_entry = trade.ob_top - 0.25 * width
        want_sl = trade.ob_bottom
        want_tp = want_entry * (1.0 + 0.60 / 100.0)
    else:
        want_entry = trade.ob_bottom + 0.25 * width
        want_sl = trade.ob_top
        want_tp = want_entry * (1.0 - 0.60 / 100.0)
    fails = []
    if not _close(trade.entry_price, want_entry):
        fails.append(f"entry {trade.entry_price} != {want_entry}")
    if not _close(trade.sl_price, want_sl):
        fails.append(f"sl {trade.sl_price} != {want_sl}")
    if not _close(trade.tp_price, want_tp):
        fails.append(f"tp {trade.tp_price} != {want_tp}")
    return (not fails), "; ".join(fails)


def trade_rows(run: PairRun) -> Tuple[List[Dict[str, str]], List[str]]:
    """Every settled trade that OVERLAPS the window, plus any audit failures."""
    assert run.result is not None
    rows: List[Dict[str, str]] = []
    failures: List[str] = []
    for trade in run.result.trades:
        if not (in_window(trade.fill_dt) or in_window(trade.exit_dt)):
            continue
        geo = run.ob_geo.get(trade.ob_id)
        laf = getattr(geo, "limit_active_from_bar", None) if geo else None
        touch_ts = run.ob_first_touch_ts.get(trade.ob_id)
        touch = run.candles_by_ts.get(touch_ts) if touch_ts else None
        fill = run.candles_by_ts.get(trade.fill_dt)
        ok_entry, why = _entry_rule_valid(trade)
        if not ok_entry:
            failures.append(f"{run.symbol} trade {trade.trade_id}: {why}")
        offset = (trade.fill_bar_idx - laf) if laf is not None else None
        ok_expiry = offset is not None and 0 <= offset <= 2
        notes = [f"activation_mode={trade.displacement_mode}",
                 f"window_offset_from_first_touch="
                 f"{offset if offset is not None else 'unavailable'}"]
        if not in_window(trade.fill_dt):
            notes.append("FILLED BEFORE THE WINDOW; row included because the "
                         "exit settled inside it")
        if trade.is_ambiguous:
            notes.append("INTRA_CANDLE_AMBIGUITY: the fill candle touched both "
                         "TP and SL; the engine took SL conservatively")
        if not ok_entry:
            notes.append(f"GEOMETRY AUDIT FAILED: {why}")
        rows.append(_trade_row(run, trade, geo, touch_ts, touch, fill,
                               ok_entry, ok_expiry, notes))
    return rows, failures


def _trade_row(
    run: PairRun,
    t: BacktestTrade,
    geo: Any,
    touch_ts: Optional[datetime],
    touch: Optional[Candle],
    fill: Optional[Candle],
    ok_entry: bool,
    ok_expiry: bool,
    notes: List[str],
) -> Dict[str, str]:
    """Transcribe one `BacktestTrade`. Every number is copied, none derived."""
    sizing = t.sizing_at_fill
    rr = (t.reward_dist / t.risk_dist) if t.risk_dist > 0 else None
    return {
        "pair": t.asset,
        "trade_id": str(t.trade_id),
        "ob_setup_id": t.ob_id,
        "direction": t.direction,
        "strategy": f"{t.strategy_name} v{t.strategy_version}",
        "timeframe": t.data_timeframe,
        "ob_creation_ts": iso(t.bos_dt),
        "ob_origin_ts": iso(t.formation_dt),
        "bos_ts": iso(t.bos_dt),
        #: No BOS-vs-CHOCH classifier exists in the engine.
        "choch_or_bos": BLANK,
        "ob_type": ob_type_of(t.direction),
        "ob_zone_upper": num(t.ob_top),
        "ob_zone_lower": num(t.ob_bottom),
        "ob_width": num(t.ob_width),
        #: width as % of the entry level — the same denominator the engine uses
        #: for its own `sl_dist_pct`. Stated so the formula is not a mystery.
        "ob_width_pct": num(t.ob_width / t.entry_price * 100.0, 6),
        "first_touch_ts": iso(touch_ts),
        #: The engine records the touch TIMESTAMP only, never a touch price.
        "first_touch_price": BLANK,
        "touch_candle_open": num(touch.open) if touch else BLANK,
        "touch_candle_high": num(touch.high) if touch else BLANK,
        "touch_candle_low": num(touch.low) if touch else BLANK,
        "touch_candle_close": num(touch.close) if touch else BLANK,
        #: Exactly one fill concept exists (ENTRY_FILLED); there is no separate
        #: trigger event, so this has no engine source.
        "entry_trigger_ts": BLANK,
        "entry_level": num(t.entry_price),
        "actual_fill_ts": iso(t.fill_dt),
        #: The engine fills AT the limit level, so fill price == entry level.
        "actual_fill_price": num(t.entry_price),
        "entry_candle_open": num(fill.open) if fill else BLANK,
        "entry_candle_high": num(fill.high) if fill else BLANK,
        "entry_candle_low": num(fill.low) if fill else BLANK,
        "entry_candle_close": num(fill.close) if fill else BLANK,
        "sl_price": num(t.sl_price),
        "tp_price": num(t.tp_price),
        "risk_distance": num(t.risk_dist),
        "reward_distance": num(t.reward_dist),
        "rr_informational": num(rr, 6),
        "applied_leverage": num(t.applied_leverage, 4),
        "balance_before_trade": num(t.starting_capital, 10),
        "margin": num(sizing.margin_usd, 10),
        "notional": num(t.position_notional, 10),
        #: PositionSizing is USD/leverage only — its docstring says "NO
        #: quantity field". Contract count is an execution-layer concern.
        "quantity": BLANK,
        "fees": num(t.fees_usd, 10),
        "exit_ts": iso(t.exit_dt),
        "exit_price": num(t.exit_price),
        "exit_reason": t.reason_for_exit,
        "outcome": t.outcome,
        "holding_bars": str(t.holding_bars),
        "holding_hours": num(t.holding_time_hours, 4),
        "gross_pnl": num(t.gross_pnl_usd, 10),
        "net_pnl": num(t.net_pnl_usd, 10),
        "realized_r": num(t.realized_r, 6),
        "balance_after_trade": num(t.ending_capital, 10),
        "entry_rule_valid": str(ok_entry).upper(),
        "expiry_rule_valid": str(ok_expiry).upper(),
        #: VERIFIED FALSE: no reward:risk admission exists anywhere on the
        #: entry path (lifecycle.py:1005-1013 states it in the code itself).
        "rr_filter_applied": "FALSE",
        "rr_rejection": "NONE",
        "notes": "; ".join(notes),
    }


# ---------------------------------------------------------------------------
# PART 6 — hourly state snapshots
# ---------------------------------------------------------------------------
def snapshot_rows(run: PairRun) -> List[Dict[str, str]]:
    """
    Resolve `trade_id` and hand back the collected snapshots.

    `trade_id` is assigned by the driver only when a trade CLOSES, so a candle
    holding a still-open position is matched to its trade through the order
    block that opened it. A position that never closed keeps a BLANK id —
    reported through `open_trade_at_end`, never invented.
    """
    ids = run.trade_id_for_ob()
    rows: List[Dict[str, str]] = []
    for snap in run.snapshots:
        row = dict(snap)
        ob_id = row.pop("_active_trade_ob_id", BLANK)
        row["trade_id"] = str(ids[ob_id]) if ob_id in ids else BLANK
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_csv(path: Path, columns: Sequence[str],
              rows: Sequence[Dict[str, str]]) -> int:
    """Write a CSV whose header is EXACTLY `columns`, in order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns),
                                extrasaction="raise")
        writer.writeheader()
        for row in rows:
            missing = set(columns) - set(row)
            if missing:
                raise DiagnosticError(
                    f"{path.name}: row is missing {sorted(missing)}; refusing "
                    f"to write a ledger with an unexplained hole")
            writer.writerow(row)
    return len(rows)


def export_pair(run: PairRun) -> Dict[str, Any]:
    """Write the three CSVs for one (pair, mode) and report what was written."""
    out_dir = OUT_ROOT / run.mode
    sym = run.symbol
    led_path = out_dir / (f"{sym}_5day_structure_ob_ledger_"
                          f"2026-08-28_to_2026-09-02.csv")
    trd_path = out_dir / f"{sym}_5day_trades_2026-08-28_to-2026-09-02.csv"
    snp_path = out_dir / (f"{sym}_5day_state_snapshots_"
                          f"2026-08-28_to-2026-09-02.csv")
    led = ledger_rows(run)
    trd, failures = trade_rows(run)
    snp = snapshot_rows(run)
    n_led = write_csv(led_path, LEDGER_COLUMNS, led)
    n_trd = write_csv(trd_path, TRADE_COLUMNS, trd)
    n_snp = write_csv(snp_path, SNAPSHOT_COLUMNS, snp)
    return {
        "structure_ob_ledger_csv": str(led_path.relative_to(REPO_ROOT)
                                       ).replace("\\", "/"),
        "structure_ob_ledger_rows": n_led,
        "trades_csv": str(trd_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "trades_rows": n_trd,
        "state_snapshots_csv": str(snp_path.relative_to(REPO_ROOT)
                                   ).replace("\\", "/"),
        "state_snapshots_rows": n_snp,
        "geometry_audit_failures": failures,
    }


# ---------------------------------------------------------------------------
# PART 7 — summary
# ---------------------------------------------------------------------------
def _drawdown(trades: Sequence[BacktestTrade], opening: float) -> float:
    """Peak-to-trough on the in-window equity path. Reporting only."""
    peak = opening
    worst = 0.0
    for t in trades:
        peak = max(peak, t.ending_capital)
        if peak > 0:
            worst = max(worst, (peak - t.ending_capital) / peak * 100.0)
    return worst


def pair_summary(run: PairRun, export: Dict[str, Any]) -> Dict[str, Any]:
    """The DATA / STRUCTURE / TRADES / R:R blocks for one pair, one mode."""
    assert run.result is not None
    wc = run.event_counts_window
    trades = [t for t in run.result.trades
              if in_window(t.fill_dt) or in_window(t.exit_dt)]
    rrs = [t.reward_dist / t.risk_dist for t in trades if t.risk_dist > 0]
    tp = sum(1 for t in trades if t.outcome == "FILLED_TP")
    sl = sum(1 for t in trades if t.outcome == "FILLED_SL")
    to = sum(1 for t in trades if t.outcome == "FILLED_TIMEOUT")
    opening = run.balance_at_window_open
    net = sum(t.net_pnl_usd for t in trades)
    return {
        "data": {
            "requested_start": REQUESTED_START,
            "requested_end": REQUESTED_END,
            "actual_start": iso(WINDOW_START) if run.window_candles else None,
            "actual_end": iso(WINDOW_END) if run.window_candles else None,
            "candle_count": run.window_candles,
            "candles_processed_by_engine": run.candles_processed,
            "engine_run_first_ts": iso(run.first_ts),
            "engine_run_last_ts": iso(run.last_ts),
            "data_source": ("Delta Exchange India public candles endpoint "
                            "https://api.india.delta.exchange/v2/history/"
                            "candles (unauthenticated GET), appended to the "
                            "repository's canonical 1h history"),
            "downloaded_or_existing": ("EXISTING through 2026-08-26T12:00Z; "
                                       "DOWNLOADED 2026-08-26T13:00Z through "
                                       "2026-09-02T09:00Z (165 candles)"),
            "timeframe": TIMEFRAME,
        },
        "structure": {
            "bos_count": wc.get("OB_CREATED", 0),
            "bos_count_note": ("one OB_CREATED event == one close-break the "
                               "scanner admitted; the engine retains no BOS "
                               "object, so this is a count of events, not of "
                               "surviving structure"),
            "choch_count": None,
            "choch_count_note": ("NOT IMPLEMENTED — the engine contains no "
                                 "CHOCH concept, so this is UNKNOWN rather "
                                 "than zero"),
            "ob_created": wc.get("OB_CREATED", 0),
            "ob_modified": 0,
            "ob_modified_note": ("VERIFIED impossible: no code path assigns to "
                                 "OB geometry after creation"),
            "ob_invalidated": wc.get("INVALIDATED", 0),
            "ob_expired": sum(
                1 for _, _, e, _ in run.window_events
                if e.event_type is EV.INVALIDATED
                and is_window_expiry(e.detail)),
            "ob_touched": (wc.get("FIRST_TOUCH_LIMIT_ACTIVATED", 0)
                           + wc.get("PRE_DISPLACEMENT_TOUCH", 0)),
            "ob_traded": wc.get("ENTRY_FILLED", 0),
            "entries_blocked_by_active_trade": wc.get(
                "ENTRY_BLOCKED_BY_ACTIVE_TRADE", 0),
            "all_window_event_counts": dict(sorted(wc.items())),
            #: The WHOLE run, not the window. Present so "the live pool is
            #: unbounded" is a measured ratio (blocks still alive / blocks ever
            #: created) rather than a claim about missing eviction code.
            "full_run_event_counts": dict(sorted(run.event_counts_run.items())),
            "live_pool_at_window_open": len(run.obs_alive_at_window_open),
            "live_pool_at_window_close": (
                int(run.snapshots[-1]["active_ob_count"])
                if run.snapshots else 0),
        },
        "trades": {
            "total": len(trades),
            "tp_hits": tp, "sl_hits": sl, "timeouts": to,
            "win_rate_pct": (tp / len(trades) * 100.0) if trades else None,
            "total_pl_usd": net,
            "balance_at_window_open": opening,
            "final_balance": (trades[-1].ending_capital if trades
                              else opening),
            "max_drawdown_pct_in_window": (
                _drawdown(trades, opening) if trades and opening else None),
            "average_hold_hours": (
                sum(t.holding_time_hours for t in trades) / len(trades)
                if trades else None),
            "average_leverage": (
                sum(t.applied_leverage for t in trades) / len(trades)
                if trades else None),
            "open_trade_at_end_of_full_run": run.result.open_trade_at_end,
        },
        "risk_reward": {
            "min_rr_informational": min(rrs) if rrs else None,
            "max_rr_informational": max(rrs) if rrs else None,
            "trades_below_1_5_rr": sum(1 for r in rrs if r < 1.5),
            "rr_ever_filtered_a_trade": False,
            "rr_confirmation": ("CONFIRMED: no reward:risk admission exists on "
                                "the entry path. lifecycle.py:1013-1016 states "
                                "it in the code; every trade above filled "
                                "regardless of its ratio"),
        },
        "artifacts": export,
    }


def _window_open_pool(run: PairRun) -> List[Dict[str, Any]]:
    """
    The order blocks the engine was already carrying when the window opened.

    This is the single most parity-relevant measurement in the run: an OB whose
    BOS is weeks old is still live, still resting and still tradable, and it is
    exactly the kind of object a dynamic chart indicator may have dropped.
    """
    return [{
        "ob_id": s.ob_id,
        "direction": s.direction,
        "state": s.state.name,
        "bos_ts": iso(s.bos_dt),
        "origin_ts": iso(s.formation_dt),
        "age_hours_at_window_open": round(
            (WINDOW_START - s.bos_dt).total_seconds() / 3600.0, 2),
        "zone_lower": s.ob_bottom,
        "zone_upper": s.ob_top,
        "entry_price": s.entry_price,
    } for s in sorted(run.obs_alive_at_window_open, key=lambda x: x.bos_dt)]


def main(argv: Sequence[str]) -> int:
    started = datetime.now(timezone.utc)
    print(f"5-day Manual SMC parity diagnostic  started {started.isoformat()}")
    print(f"window   {REQUESTED_START} .. {REQUESTED_END}")
    print(f"data     {EXTENDED_BASE}")
    print("engine   production config, FIRST_TOUCH_WINDOW, 3-candle window, "
          "one trade at a time, NO R:R gate")

    summary: Dict[str, Any] = {
        "purpose": ("5-day Manual SMC vs TradingView/LuxAlgo parity "
                    "diagnostic — DIAGNOSTIC ONLY, no strategy rule changed"),
        "generated_utc": started.isoformat(),
        "timeframe": TIMEFRAME,
        "symbols": list(SYMBOLS),
        "pairs_run_separately": True,
        "primary_variant": MODE_WARM,
        "variant_meaning": {
            MODE_WARM: ("PRIMARY. Full extended history processed from "
                        "2024-06-01; ledgers clipped to the 5-day window. The "
                        "TradingView-comparable view: order blocks created "
                        "before the window are still live and still tradable."),
            MODE_COLD: ("CONTRAST. Timeline filtered to the window, so the "
                        "lifecycle opens with an EMPTY order-block pool and an "
                        "EMPTY 10-bar scanner deque. Measures how much of the "
                        "window's behaviour depends on pre-window state."),
        },
        "variants": {},
    }

    for mode in (MODE_WARM, MODE_COLD):
        print(f"\n=== {mode} ===")
        per_pair: Dict[str, Any] = {}
        for symbol in SYMBOLS:
            run = run_pair(symbol, mode)
            export = export_pair(run)
            block = pair_summary(run, export)
            block["order_blocks_alive_at_window_open"] = _window_open_pool(run)
            per_pair[symbol] = block
            st, tr = block["structure"], block["trades"]
            print(f"  {symbol:8s} candles={run.window_candles:>4} "
                  f"events={len(run.window_events):>4} "
                  f"OB_created={st['ob_created']:>3} "
                  f"invalidated={st['ob_invalidated']:>3} "
                  f"touched={st['ob_touched']:>3} "
                  f"trades={tr['total']:>2} "
                  f"(TP={tr['tp_hits']} SL={tr['sl_hits']} TO={tr['timeouts']}) "
                  f"carried_in={len(block['order_blocks_alive_at_window_open'])}")
            if export["geometry_audit_failures"]:
                raise DiagnosticError(
                    f"{symbol}/{mode}: geometry audit failed: "
                    f"{export['geometry_audit_failures']}")
        summary["variants"][mode] = per_pair

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str) + "\n",
                            encoding="utf-8")
    print(f"\nsummary  {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
