"""
QuantEdge AI — Displacement-Gated OB Lifecycle Retest Engine.
==============================================================
Research-only. Governance invariants preserved:
    live_execution_authorized = False
    AI_PROMOTION_STATUS = REJECTED
    execution_status = BLOCKED_BY_SYSTEM

Reproduces the manual TradingView / LuxAlgo SMC Order Block retest workflow
with a FORMAL STATE MACHINE:

    OB_CREATED
        ↓ (price moves in BOS direction, clears displacement threshold)
    DISPLACEMENT_CONFIRMED  ← NOT an entry trigger
        ↓ (OB is now RETEST_ELIGIBLE)
    RETEST_ELIGIBLE
        ↓ (price SUBSEQUENTLY moves back toward / into OB)
        ↓ (25% entry level touched on a candle AFTER displacement was confirmed)
    TRADE_FILLED
        ↓ (TP / SL / timeout)
    TRADE_CLOSED

Critical invariants:
  1. The displacement candle CANNOT simultaneously be the retest candle.
  2. retest_dt must always be strictly > displacement_confirmed_dt.
  3. Touches to the OB zone before displacement is confirmed are recorded
     as PRE_DISPLACEMENT_TOUCH and do NOT trigger a trade.
  4. An OB has no time-based expiry — it stays active until structural
     invalidation (distal boundary breached).
  5. Global one-trade lock across all 4 assets.
  6. Strictly causal — no future candle information used.

Displacement Modes:
  A — OB-width multiple: price moves ≥ X × OB_width beyond proximal edge
  B — Absolute % move: price moves ≥ Y% beyond proximal edge
  C — Candle count: N completed candles fully outside the OB zone
  D — Structural swing: price creates a new meaningful high/low beyond proximal

Entry level: 25% depth from proximal (configurable).
SL: distal boundary.
TP: fixed +/- 0.60% from actual entry price.
Leverage: min(100, 35 / sl_distance_pct).
Fees: 0.08% round-trip on notional.
Compounding from $10.00 starting capital.
"""

from __future__ import annotations

import collections
import csv
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

IST_TZ = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------
live_execution_authorized: bool = False
AI_PROMOTION_STATUS: str = "REJECTED"
execution_status: str = "BLOCKED_BY_SYSTEM"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class OBState(Enum):
    OB_CREATED = auto()
    RETEST_ELIGIBLE = auto()
    TRADE_ACTIVE = auto()
    TRADE_CLOSED = auto()
    INVALIDATED = auto()


class DisplacementMode(Enum):
    A_OB_WIDTH_MULTIPLE = "A"
    B_ABSOLUTE_PCT = "B"
    C_CANDLE_COUNT = "C"
    D_STRUCTURAL_SWING = "D"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class DisplacementGatedConfig:
    # Core strategy
    entry_depth_pct: float = 0.25          # 25% from proximal into OB
    fixed_tp_market_pct: float = 0.60      # +0.60% from entry price
    max_sl_account_risk_pct: float = 35.0  # 35% account loss at SL
    applied_leverage_cap: float = 100.0    # Exchange cap
    fee_rate: float = 0.0008               # 0.08% roundtrip
    max_holding_bars: int = 72             # 72h horizon
    starting_capital: float = 10.0

    # Displacement gate
    displacement_mode: str = "A"           # A / B / C / D
    # Mode A: minimum MFE in OB-width multiples
    displacement_ob_width_multiple: float = 1.0
    # Mode B: minimum MFE as % of entry price
    displacement_abs_pct: float = 0.60
    # Mode C: minimum completed candles fully outside OB zone
    displacement_candle_count: int = 2
    # Mode D: structural swing (uses mode A with 0.5x as minimal heuristic)
    displacement_structural_min_pct: float = 0.30

    data_timeframe: str = "1h"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class OBRecord:
    """Live state of one order block during the simulation."""
    ob_id: str
    asset: str
    direction: str           # LONG / SHORT
    bos_dt: datetime
    bos_bar_idx: int
    formation_dt: datetime
    ob_high: float
    ob_low: float
    ob_width: float          # ob_high - ob_low
    proximal: float          # entry approach side
    distal: float            # SL side
    entry_25pct: float       # 25% depth level
    sl_price: float
    tp_price: float
    sl_dist_pct: float
    theoretical_leverage: float
    applied_leverage: float

    # State machine
    state: OBState = OBState.OB_CREATED

    # Displacement tracking
    displacement_mode: str = "A"
    displacement_threshold_value: float = 0.0
    displacement_confirmed_dt: Optional[datetime] = None
    displacement_confirmed_bar: Optional[int] = None   # bar index in asset DF
    mfe_from_proximal: float = 0.0        # max favorable excursion from proximal
    mfe_pct: float = 0.0
    mfe_ob_width_multiples: float = 0.0
    candles_fully_outside_ob: int = 0

    # Pre-displacement touch tracking
    pre_displacement_touches: int = 0
    first_touch_dt: Optional[datetime] = None
    first_touch_depth_pct: Optional[float] = None

    # Retest tracking
    retest_number: int = 0
    ob_age_at_entry_hours: float = 0.0
    entry_bar_from_bos: int = 0


@dataclass
class TradeRecord:
    trade_id: int
    asset: str
    direction: str

    # Timestamps (UTC string)
    bos_time: str
    ob_formation_time: str
    displacement_confirmed_time: str
    retest_time: str
    entry_time: str
    exit_time: str

    # IST strings
    bos_time_ist: str
    ob_formation_time_ist: str
    displacement_confirmed_time_ist: str
    retest_time_ist: str
    entry_time_ist: str
    exit_time_ist: str

    # OB geometry
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_pct: float
    proximal: float
    distal: float

    # Trade parameters
    entry_price: float
    sl_price: float
    tp_price: float
    entry_to_sl_distance_pct: float
    theoretical_leverage: float
    leverage: float

    # Displacement diagnostics
    displacement_mode: str
    displacement_threshold_value: float
    mfe_from_proximal: float
    mfe_pct: float
    mfe_ob_width_multiples: float
    candles_fully_outside_ob: int
    pre_displacement_touches: int
    entry_bar_from_bos: int
    ob_age_at_entry_hours: float
    retest_number: int

    # PnL
    gross_sl_return_pct: float
    gross_tp_return_pct: float
    fees_usd: float
    net_return_pct: float
    starting_capital: float
    position_notional: float
    gross_pnl_usd: float
    pnl_usd: float
    ending_capital: float

    # Result
    outcome: str          # FILLED_TP / FILLED_SL / FILLED_TIMEOUT
    reason_for_exit: str
    is_ambiguous: bool
    holding_bars: int
    holding_time_hours: float
    realized_r: float
    cumulative_realized_r: float
    data_timeframe: str
    trade_narrative: str


@dataclass
class LifecycleEvent:
    """One row in the per-OB lifecycle timeline."""
    bar_offset: int         # bars since BOS
    bar_dt: datetime
    candle_o: float
    candle_h: float
    candle_l: float
    candle_c: float
    ob_state: str
    event: str              # human-readable event description
    mfe_from_proximal: float
    candles_outside: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_utc_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _to_ist_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")


def _compute_entry_tp_sl(
    ob_high: float,
    ob_low: float,
    direction: str,
    depth_pct: float,
    tp_market_pct: float,
) -> Tuple[float, float, float, float, float]:
    """Returns (entry_25pct, sl_price, tp_price, proximal, distal)."""
    w = ob_high - ob_low
    if direction == "LONG":
        proximal = ob_high
        distal = ob_low
        entry = ob_high - depth_pct * w
        tp = entry * (1.0 + tp_market_pct / 100.0)
        sl = distal
    else:
        proximal = ob_low
        distal = ob_high
        entry = ob_low + depth_pct * w
        tp = entry * (1.0 - tp_market_pct / 100.0)
        sl = distal
    return entry, sl, tp, proximal, distal


def _displacement_threshold_met(
    ob: OBRecord,
    cfg: DisplacementGatedConfig,
    c_h: float,
    c_l: float,
    c_c: float,
    bar_idx: int,
) -> bool:
    """
    Check whether the current candle satisfies the displacement threshold for
    this OB. Updates ob.mfe_from_proximal, ob.mfe_pct, ob.mfe_ob_width_multiples,
    and ob.candles_fully_outside_ob in-place.

    Returns True if the threshold is now met.
    """
    w = ob.ob_width
    if w <= 1e-9:
        return False

    # Extreme in BOS direction
    if ob.direction == "LONG":
        extreme = c_h   # price moving up = displacement for bullish OB
        # candle fully above OB high counts as "outside"
        fully_outside = c_l > ob.ob_high
    else:
        extreme = c_l   # price moving down = displacement for bearish OB
        fully_outside = c_h < ob.ob_low

    # MFE from proximal
    if ob.direction == "LONG":
        mfe = extreme - ob.proximal
    else:
        mfe = ob.proximal - extreme

    if mfe > ob.mfe_from_proximal:
        ob.mfe_from_proximal = mfe
        ob.mfe_pct = (mfe / ob.proximal) * 100.0
        ob.mfe_ob_width_multiples = mfe / w if w > 1e-9 else 0.0

    if fully_outside:
        ob.candles_fully_outside_ob += 1

    mode = DisplacementMode(ob.displacement_mode)

    if mode == DisplacementMode.A_OB_WIDTH_MULTIPLE:
        return ob.mfe_ob_width_multiples >= cfg.displacement_ob_width_multiple
    elif mode == DisplacementMode.B_ABSOLUTE_PCT:
        return ob.mfe_pct >= cfg.displacement_abs_pct
    elif mode == DisplacementMode.C_CANDLE_COUNT:
        return ob.candles_fully_outside_ob >= cfg.displacement_candle_count
    elif mode == DisplacementMode.D_STRUCTURAL_SWING:
        # Heuristic: price creates new extreme at least structural_min_pct beyond proximal
        return ob.mfe_pct >= cfg.displacement_structural_min_pct
    return False


def _ob_touching_entry(ob: OBRecord, c_h: float, c_l: float) -> bool:
    """Does this candle's range reach the 25% entry level?"""
    if ob.direction == "LONG":
        return c_l <= ob.entry_25pct
    else:
        return c_h >= ob.entry_25pct


def _distal_breached(ob: OBRecord, c_h: float, c_l: float) -> bool:
    """Did price breach the distal (SL) boundary?"""
    if ob.direction == "LONG":
        return c_l <= ob.distal
    else:
        return c_h >= ob.distal


# ---------------------------------------------------------------------------
# Main simulation engine
# ---------------------------------------------------------------------------
def run_displacement_gated_backtest(
    data_base_dir: Optional[Path] = None,
    config: Optional[DisplacementGatedConfig] = None,
    symbols: Optional[List[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    audit_mode: bool = False,
    max_audit_examples: int = 30,
) -> Dict[str, Any]:
    """
    Run the displacement-gated OB lifecycle backtest.

    Architecture:
      - All assets' candles are merged into a single global timeline.
      - For every new 1H candle (chronological order):
          1. Check whether any live OB on that asset transitions state.
          2. New OBs whose BOS bar has passed are added to the live pool.
          3. The global trade lock is respected.
      - No future information is used.

    Returns a dict with results, trades_df, and (if audit_mode) lifecycle_examples.
    """
    assert not live_execution_authorized, "Governance: live execution not authorized."

    cfg = config or DisplacementGatedConfig()
    syms = symbols or ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    root = data_base_dir or (_find_repo_root() / "data" / "canonical" / "delta_exchange_india")

    # ------------------------------------------------------------------
    # Load canonical candle data and all OBs for each asset
    # ------------------------------------------------------------------
    asset_candles: Dict[str, pd.DataFrame] = {}
    asset_ob_queue: Dict[str, List[OBRecord]] = {}   # OBs sorted by bos_bar_idx

    for sym in syms:
        candles = load_canonical_full_history(root, sym)
        rows = []
        for c in candles:
            rows.append({
                "timestamp": c.timestamp,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            })
        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        asset_candles[sym] = df

        ctx = build_smc_context(candles)
        setups, _ = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)

        ob_queue: List[OBRecord] = []
        for s in setups:
            dec_bar = s.decision_bar
            dec_ts = candles[dec_bar].timestamp
            formation_ts = datetime.fromisoformat(s.creation_time)

            if start_date is not None and dec_ts < start_date:
                continue
            if end_date is not None and dec_ts > end_date:
                continue

            top = float(s.ob_high)
            bot = float(s.ob_low)
            w = top - bot
            if w <= 1e-6:
                continue

            dir_ = s.direction
            entry_25, sl_p, tp_p, proximal, distal = _compute_entry_tp_sl(
                top, bot, dir_, cfg.entry_depth_pct, cfg.fixed_tp_market_pct
            )
            risk_dist = abs(entry_25 - sl_p)
            if risk_dist <= 1e-9:
                continue

            sl_dist_pct = (risk_dist / entry_25) * 100.0
            theo_lev = cfg.max_sl_account_risk_pct / sl_dist_pct
            applied_lev = min(cfg.applied_leverage_cap, theo_lev)

            ob = OBRecord(
                ob_id=f"{sym}_{s.setup_id}",
                asset=sym,
                direction=dir_,
                bos_dt=dec_ts,
                bos_bar_idx=dec_bar,
                formation_dt=formation_ts,
                ob_high=top,
                ob_low=bot,
                ob_width=w,
                proximal=proximal,
                distal=distal,
                entry_25pct=entry_25,
                sl_price=sl_p,
                tp_price=tp_p,
                sl_dist_pct=sl_dist_pct,
                theoretical_leverage=theo_lev,
                applied_leverage=applied_lev,
                displacement_mode=cfg.displacement_mode,
                displacement_threshold_value=(
                    cfg.displacement_ob_width_multiple if cfg.displacement_mode == "A" else
                    cfg.displacement_abs_pct if cfg.displacement_mode == "B" else
                    float(cfg.displacement_candle_count) if cfg.displacement_mode == "C" else
                    cfg.displacement_structural_min_pct
                ),
            )
            ob_queue.append(ob)

        ob_queue.sort(key=lambda x: x.bos_bar_idx)
        asset_ob_queue[sym] = ob_queue

    # ------------------------------------------------------------------
    # Build a global sorted timeline of (timestamp, asset, bar_local_idx)
    # ------------------------------------------------------------------
    timeline_rows = []
    for sym in syms:
        df = asset_candles[sym]
        for i, row in df.iterrows():
            timeline_rows.append((row["timestamp"], sym, i))
    timeline_rows.sort(key=lambda x: (x[0], x[1]))

    # ------------------------------------------------------------------
    # Simulation state
    # ------------------------------------------------------------------
    global_lock_until_dt: Optional[datetime] = None
    capital = cfg.starting_capital
    peak_capital = cfg.starting_capital
    max_dd_pct = 0.0
    cum_r = 0.0
    trade_id_counter = 0

    executed_trades: List[TradeRecord] = []
    audit_examples: List[Dict[str, Any]] = []

    # Live OBs pool: dict[ob_id -> OBRecord]
    live_obs: Dict[str, OBRecord] = {}
    # Track which OBs have been added to pool (by bos_dt passed)
    ob_added_ids: set = set()

    # Pointer into each asset's OB queue
    asset_ob_ptr: Dict[str, int] = {sym: 0 for sym in syms}

    # Track active trade per OB (to handle TP/SL scanning)
    active_trade_ob_id: Optional[str] = None
    active_trade: Optional[Dict[str, Any]] = None   # partial trade info

    # Audit example collection
    audit_ob_ids_collected: set = set()
    audit_ob_timelines: Dict[str, List[LifecycleEvent]] = {}

    # ------------------------------------------------------------------
    # Candle-by-candle simulation
    # ------------------------------------------------------------------
    for (c_ts, sym, bar_local_idx) in timeline_rows:
        df = asset_candles[sym]
        row = df.iloc[bar_local_idx]
        c_o = float(row["open"])
        c_h = float(row["high"])
        c_l = float(row["low"])
        c_c = float(row["close"])

        # ---- 1. Admit new OBs whose BOS candle has now CLOSED ----
        ob_queue = asset_ob_queue[sym]
        ptr = asset_ob_ptr[sym]
        while ptr < len(ob_queue):
            ob = ob_queue[ptr]
            # OB becomes live on the candle AFTER BOS confirmation
            if ob.bos_dt < c_ts:
                if ob.ob_id not in ob_added_ids:
                    live_obs[ob.ob_id] = ob
                    ob_added_ids.add(ob.ob_id)
                    if audit_mode and len(audit_ob_timelines) < max_audit_examples * 3:
                        audit_ob_timelines[ob.ob_id] = []
                ptr += 1
            else:
                break
        asset_ob_ptr[sym] = ptr

        # ---- 2. Handle active trade TP/SL scanning ----
        if active_trade is not None and active_trade["asset"] == sym:
            at = active_trade
            dir_ = at["direction"]
            entry_p = at["entry_price"]
            tp_p = at["tp_price"]
            sl_p = at["sl_price"]

            hit_tp = (c_h >= tp_p) if dir_ == "LONG" else (c_l <= tp_p)
            hit_sl = (c_l <= sl_p) if dir_ == "LONG" else (c_h >= sl_p)

            if hit_tp or hit_sl:
                # Determine outcome
                if hit_tp and hit_sl:
                    outcome = "FILLED_SL"
                    exit_reason = "DUAL_TOUCH_CONSERVATIVE_SL"
                    exit_p = sl_p
                    is_ambiguous = True
                    narrative = "Dual-touch: both TP and SL hit same candle. Conservative SL-first applied."
                elif hit_tp:
                    outcome = "FILLED_TP"
                    exit_reason = "TP_HIT"
                    exit_p = tp_p
                    is_ambiguous = False
                    narrative = f"Fixed +0.60% TP reached at {tp_p:.6f}."
                else:
                    outcome = "FILLED_SL"
                    exit_reason = "SL_HIT"
                    exit_p = sl_p
                    is_ambiguous = False
                    narrative = f"Distal SL breached at {sl_p:.6f}."

                # Finalize trade
                entry_p = at["entry_price"]
                applied_lev = at["applied_leverage"]
                theo_lev = at["theoretical_leverage"]
                risk_dist = at["risk_dist"]
                reward_dist = at["reward_dist"]
                gross_sl_ret = at["gross_sl_return_pct"]
                gross_tp_ret = at["gross_tp_return_pct"]
                start_bal = at["starting_capital"]

                if outcome == "FILLED_TP":
                    ret_pct = gross_tp_ret
                    realized_r = reward_dist / risk_dist
                elif outcome == "FILLED_SL":
                    ret_pct = -gross_sl_ret
                    realized_r = -1.0
                else:
                    realized_r = -1.0
                    ret_pct = -gross_sl_ret

                notional = start_bal * applied_lev
                fees_usd = notional * cfg.fee_rate
                gross_pnl = start_bal * (ret_pct / 100.0)
                net_pnl = gross_pnl - fees_usd
                capital = max(0.0, start_bal + net_pnl)

                if capital > peak_capital:
                    peak_capital = capital
                dd = (peak_capital - capital) / peak_capital * 100.0 if peak_capital > 0 else 0.0
                max_dd_pct = max(max_dd_pct, dd)

                cum_r += realized_r
                fill_dt: datetime = at["fill_dt"]
                holding_secs = (c_ts - fill_dt).total_seconds()
                holding_hrs = max(1.0, holding_secs / 3600.0)

                ob = at["ob"]
                ob.state = OBState.TRADE_CLOSED

                trade_id_counter += 1
                tr = TradeRecord(
                    trade_id=trade_id_counter,
                    asset=sym,
                    direction=dir_,
                    bos_time=_to_utc_str(ob.bos_dt),
                    ob_formation_time=_to_utc_str(ob.formation_dt),
                    displacement_confirmed_time=_to_utc_str(ob.displacement_confirmed_dt),
                    retest_time=_to_utc_str(at["retest_dt"]),
                    entry_time=_to_utc_str(fill_dt),
                    exit_time=_to_utc_str(c_ts),
                    bos_time_ist=_to_ist_str(ob.bos_dt),
                    ob_formation_time_ist=_to_ist_str(ob.formation_dt),
                    displacement_confirmed_time_ist=_to_ist_str(ob.displacement_confirmed_dt),
                    retest_time_ist=_to_ist_str(at["retest_dt"]),
                    entry_time_ist=_to_ist_str(fill_dt),
                    exit_time_ist=_to_ist_str(c_ts),
                    ob_high=round(ob.ob_high, 6),
                    ob_low=round(ob.ob_low, 6),
                    ob_width=round(ob.ob_width, 6),
                    ob_width_pct=round((ob.ob_width / ob.entry_25pct) * 100.0, 4),
                    proximal=round(ob.proximal, 6),
                    distal=round(ob.distal, 6),
                    entry_price=round(entry_p, 6),
                    sl_price=round(sl_p, 6),
                    tp_price=round(tp_p, 6),
                    entry_to_sl_distance_pct=round(ob.sl_dist_pct, 4),
                    theoretical_leverage=round(theo_lev, 2),
                    leverage=round(applied_lev, 2),
                    displacement_mode=ob.displacement_mode,
                    displacement_threshold_value=round(ob.displacement_threshold_value, 4),
                    mfe_from_proximal=round(ob.mfe_from_proximal, 6),
                    mfe_pct=round(ob.mfe_pct, 4),
                    mfe_ob_width_multiples=round(ob.mfe_ob_width_multiples, 4),
                    candles_fully_outside_ob=ob.candles_fully_outside_ob,
                    pre_displacement_touches=ob.pre_displacement_touches,
                    entry_bar_from_bos=ob.entry_bar_from_bos,
                    ob_age_at_entry_hours=round(ob.ob_age_at_entry_hours, 2),
                    retest_number=ob.retest_number,
                    gross_sl_return_pct=round(gross_sl_ret, 2),
                    gross_tp_return_pct=round(gross_tp_ret, 2),
                    fees_usd=fees_usd,
                    net_return_pct=round((net_pnl / start_bal) * 100.0, 2) if start_bal > 0 else 0.0,
                    starting_capital=start_bal,
                    position_notional=notional,
                    gross_pnl_usd=gross_pnl,
                    pnl_usd=net_pnl,
                    ending_capital=capital,
                    outcome=outcome,
                    reason_for_exit=exit_reason,
                    is_ambiguous=is_ambiguous,
                    holding_bars=int(holding_hrs),
                    holding_time_hours=round(holding_hrs, 2),
                    realized_r=round(realized_r, 4),
                    cumulative_realized_r=round(cum_r, 4),
                    data_timeframe=cfg.data_timeframe,
                    trade_narrative=narrative,
                )
                executed_trades.append(tr)
                global_lock_until_dt = c_ts
                active_trade = None
                active_trade_ob_id = None

                # Remove from live pool
                live_obs.pop(ob.ob_id, None)
            else:
                # Check timeout
                fill_dt_at: datetime = at["fill_dt"]
                bars_held = int((c_ts - fill_dt_at).total_seconds() / 3600)
                if bars_held >= cfg.max_holding_bars:
                    exit_p = c_c
                    outcome = "FILLED_TIMEOUT"
                    exit_reason = "TIMEOUT"
                    narrative = f"{cfg.max_holding_bars}h horizon expired. Closed at {c_c:.6f}."
                    is_ambiguous = False

                    entry_p = at["entry_price"]
                    applied_lev = at["applied_leverage"]
                    risk_dist = at["risk_dist"]
                    reward_dist = at["reward_dist"]
                    gross_sl_ret = at["gross_sl_return_pct"]
                    gross_tp_ret = at["gross_tp_return_pct"]
                    start_bal = at["starting_capital"]

                    p_diff = (exit_p - entry_p) if at["direction"] == "LONG" else (entry_p - exit_p)
                    realized_r = p_diff / risk_dist
                    ret_pct = realized_r * gross_sl_ret

                    notional = start_bal * applied_lev
                    fees_usd = notional * cfg.fee_rate
                    gross_pnl = start_bal * (ret_pct / 100.0)
                    net_pnl = gross_pnl - fees_usd
                    capital = max(0.0, start_bal + net_pnl)

                    if capital > peak_capital:
                        peak_capital = capital
                    dd = (peak_capital - capital) / peak_capital * 100.0 if peak_capital > 0 else 0.0
                    max_dd_pct = max(max_dd_pct, dd)

                    cum_r += realized_r
                    fill_dt = at["fill_dt"]
                    holding_secs = (c_ts - fill_dt).total_seconds()
                    holding_hrs = max(1.0, holding_secs / 3600.0)

                    ob = at["ob"]
                    ob.state = OBState.TRADE_CLOSED

                    trade_id_counter += 1
                    tr = TradeRecord(
                        trade_id=trade_id_counter,
                        asset=sym,
                        direction=at["direction"],
                        bos_time=_to_utc_str(ob.bos_dt),
                        ob_formation_time=_to_utc_str(ob.formation_dt),
                        displacement_confirmed_time=_to_utc_str(ob.displacement_confirmed_dt),
                        retest_time=_to_utc_str(at["retest_dt"]),
                        entry_time=_to_utc_str(fill_dt),
                        exit_time=_to_utc_str(c_ts),
                        bos_time_ist=_to_ist_str(ob.bos_dt),
                        ob_formation_time_ist=_to_ist_str(ob.formation_dt),
                        displacement_confirmed_time_ist=_to_ist_str(ob.displacement_confirmed_dt),
                        retest_time_ist=_to_ist_str(at["retest_dt"]),
                        entry_time_ist=_to_ist_str(fill_dt),
                        exit_time_ist=_to_ist_str(c_ts),
                        ob_high=round(ob.ob_high, 6),
                        ob_low=round(ob.ob_low, 6),
                        ob_width=round(ob.ob_width, 6),
                        ob_width_pct=round((ob.ob_width / ob.entry_25pct) * 100.0, 4),
                        proximal=round(ob.proximal, 6),
                        distal=round(ob.distal, 6),
                        entry_price=round(entry_p, 6),
                        sl_price=round(sl_p, 6),
                        tp_price=round(tp_p, 6),
                        entry_to_sl_distance_pct=round(ob.sl_dist_pct, 4),
                        theoretical_leverage=round(at["theoretical_leverage"], 2),
                        leverage=round(applied_lev, 2),
                        displacement_mode=ob.displacement_mode,
                        displacement_threshold_value=round(ob.displacement_threshold_value, 4),
                        mfe_from_proximal=round(ob.mfe_from_proximal, 6),
                        mfe_pct=round(ob.mfe_pct, 4),
                        mfe_ob_width_multiples=round(ob.mfe_ob_width_multiples, 4),
                        candles_fully_outside_ob=ob.candles_fully_outside_ob,
                        pre_displacement_touches=ob.pre_displacement_touches,
                        entry_bar_from_bos=ob.entry_bar_from_bos,
                        ob_age_at_entry_hours=round(ob.ob_age_at_entry_hours, 2),
                        retest_number=ob.retest_number,
                        gross_sl_return_pct=round(gross_sl_ret, 2),
                        gross_tp_return_pct=round(gross_tp_ret, 2),
                        fees_usd=fees_usd,
                        net_return_pct=round((net_pnl / start_bal) * 100.0, 2) if start_bal > 0 else 0.0,
                        starting_capital=start_bal,
                        position_notional=notional,
                        gross_pnl_usd=gross_pnl,
                        pnl_usd=net_pnl,
                        ending_capital=capital,
                        outcome=outcome,
                        reason_for_exit=exit_reason,
                        is_ambiguous=is_ambiguous,
                        holding_bars=int(holding_hrs),
                        holding_time_hours=round(holding_hrs, 2),
                        realized_r=round(realized_r, 4),
                        cumulative_realized_r=round(cum_r, 4),
                        data_timeframe=cfg.data_timeframe,
                        trade_narrative=narrative,
                    )
                    executed_trades.append(tr)
                    global_lock_until_dt = c_ts
                    active_trade = None
                    active_trade_ob_id = None
                    live_obs.pop(ob.ob_id, None)

            # Skip OB lifecycle updates if trade active on this asset
            # (still advance OB states on OTHER assets below)

        # ---- 3. Update OB lifecycle for all live OBs on this asset ----
        obs_to_remove = []
        for ob_id, ob in list(live_obs.items()):
            if ob.asset != sym:
                continue
            if ob.state in (OBState.TRADE_CLOSED, OBState.INVALIDATED):
                obs_to_remove.append(ob_id)
                continue
            if ob.state == OBState.TRADE_ACTIVE:
                continue   # Handled above

            bar_offset = int((c_ts - ob.bos_dt).total_seconds() / 3600)

            # ---- STATE: OB_CREATED — waiting for displacement ----
            if ob.state == OBState.OB_CREATED:
                # Check if distal is breached before displacement (invalidation)
                if _distal_breached(ob, c_h, c_l):
                    ob.state = OBState.INVALIDATED
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="OB_CREATED→INVALIDATED",
                            event="❌ Distal boundary breached before displacement — OB INVALIDATED",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))
                    obs_to_remove.append(ob_id)
                    continue

                # Record pre-displacement touch if entry level is reached
                if _ob_touching_entry(ob, c_h, c_l):
                    ob.pre_displacement_touches += 1
                    if ob.first_touch_dt is None:
                        ob.first_touch_dt = c_ts
                        if ob.direction == "LONG":
                            penetration = ob.proximal - c_l
                        else:
                            penetration = c_h - ob.proximal
                        ob.first_touch_depth_pct = (penetration / ob.ob_width) * 100.0 if ob.ob_width > 0 else 0.0
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="OB_CREATED",
                            event=f"⚠️  PRE-DISPLACEMENT TOUCH #{ob.pre_displacement_touches} — "
                                  f"entry level {ob.entry_25pct:.4f} reached BUT displacement not yet confirmed. NO TRADE.",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))

                # Check displacement threshold
                threshold_met = _displacement_threshold_met(ob, cfg, c_h, c_l, c_c, bar_local_idx)
                if threshold_met:
                    ob.state = OBState.RETEST_ELIGIBLE
                    ob.displacement_confirmed_dt = c_ts
                    ob.displacement_confirmed_bar = bar_local_idx
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="OB_CREATED→RETEST_ELIGIBLE",
                            event=f"✅ DISPLACEMENT CONFIRMED (mode={ob.displacement_mode}, "
                                  f"MFE={ob.mfe_from_proximal:.4f}, {ob.mfe_ob_width_multiples:.2f}× width, "
                                  f"{ob.mfe_pct:.3f}%). OB now RETEST_ELIGIBLE. "
                                  f"NOTE: This candle is NOT a retest trigger.",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))
                    # CRITICAL: Do NOT allow this same candle to be the retest candle.
                    # We skip to next candle by NOT proceeding to RETEST_ELIGIBLE logic here.
                    continue
                else:
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="OB_CREATED",
                            event=f"  Waiting for displacement. MFE={ob.mfe_from_proximal:.4f} "
                                  f"({ob.mfe_ob_width_multiples:.2f}× width, {ob.mfe_pct:.3f}%). "
                                  f"Candles outside: {ob.candles_fully_outside_ob}",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))

            # ---- STATE: RETEST_ELIGIBLE — waiting for a valid return ----
            elif ob.state == OBState.RETEST_ELIGIBLE:
                # Check invalidation (distal breach)
                if _distal_breached(ob, c_h, c_l):
                    ob.state = OBState.INVALIDATED
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="RETEST_ELIGIBLE→INVALIDATED",
                            event="❌ Distal boundary breached after displacement — OB INVALIDATED before retest.",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))
                    obs_to_remove.append(ob_id)
                    continue

                # Check for valid retest: entry 25% level touched
                if _ob_touching_entry(ob, c_h, c_l):
                    # Verify retest_dt > displacement_confirmed_dt (invariant test 22)
                    if ob.displacement_confirmed_dt is not None and c_ts <= ob.displacement_confirmed_dt:
                        # Should never happen given the 'continue' above, but guard explicitly
                        if audit_mode and ob.ob_id in audit_ob_timelines:
                            audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                                bar_offset=bar_offset, bar_dt=c_ts,
                                candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                                ob_state="RETEST_ELIGIBLE",
                                event="⛔ GUARD: retest_dt == displacement_dt — skipped (test-22 invariant).",
                                mfe_from_proximal=ob.mfe_from_proximal,
                                candles_outside=ob.candles_fully_outside_ob,
                            ))
                        continue

                    # Valid retest — check global lock
                    if global_lock_until_dt is not None and c_ts <= global_lock_until_dt:
                        if audit_mode and ob.ob_id in audit_ob_timelines:
                            audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                                bar_offset=bar_offset, bar_dt=c_ts,
                                candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                                ob_state="RETEST_ELIGIBLE",
                                event=f"🔒 Valid retest — but GLOBAL TRADE LOCK active until {global_lock_until_dt}. Skipped.",
                                mfe_from_proximal=ob.mfe_from_proximal,
                                candles_outside=ob.candles_fully_outside_ob,
                            ))
                        continue

                    # Execute trade entry
                    ob.state = OBState.TRADE_ACTIVE
                    ob.retest_number += 1
                    ob.entry_bar_from_bos = bar_offset
                    ob.ob_age_at_entry_hours = float(bar_offset)

                    entry_p = ob.entry_25pct
                    sl_p = ob.sl_price
                    tp_p = ob.tp_price
                    applied_lev = ob.applied_leverage
                    theo_lev = ob.theoretical_leverage
                    risk_dist = abs(entry_p - sl_p)
                    reward_dist = abs(tp_p - entry_p)
                    gross_sl_ret = applied_lev * ob.sl_dist_pct
                    gross_tp_ret = cfg.fixed_tp_market_pct * applied_lev

                    active_trade_ob_id = ob_id
                    active_trade = {
                        "ob": ob,
                        "asset": sym,
                        "direction": ob.direction,
                        "entry_price": entry_p,
                        "sl_price": sl_p,
                        "tp_price": tp_p,
                        "applied_leverage": applied_lev,
                        "theoretical_leverage": theo_lev,
                        "risk_dist": risk_dist,
                        "reward_dist": reward_dist,
                        "gross_sl_return_pct": gross_sl_ret,
                        "gross_tp_return_pct": gross_tp_ret,
                        "starting_capital": capital,
                        "fill_dt": c_ts,
                        "retest_dt": c_ts,
                        "bars_held": 0,
                    }
                    global_lock_until_dt = c_ts  # lock from fill forward

                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="RETEST_ELIGIBLE→TRADE_ACTIVE",
                            event=f"🎯 VALID RETEST → TRADE ENTRY at {entry_p:.6f}. "
                                  f"TP={tp_p:.6f}, SL={sl_p:.6f}. "
                                  f"Bars since BOS: {bar_offset}. "
                                  f"Retest #{ob.retest_number}.",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))
                else:
                    if audit_mode and ob.ob_id in audit_ob_timelines:
                        audit_ob_timelines[ob.ob_id].append(LifecycleEvent(
                            bar_offset=bar_offset, bar_dt=c_ts,
                            candle_o=c_o, candle_h=c_h, candle_l=c_l, candle_c=c_c,
                            ob_state="RETEST_ELIGIBLE",
                            event=f"  Eligible, price not yet returned to OB. "
                                  f"H={c_h:.4f} L={c_l:.4f}. Entry25%={ob.entry_25pct:.4f}",
                            mfe_from_proximal=ob.mfe_from_proximal,
                            candles_outside=ob.candles_fully_outside_ob,
                        ))

        # Remove closed/invalidated OBs
        for ob_id in obs_to_remove:
            live_obs.pop(ob_id, None)

    # ------------------------------------------------------------------
    # Collect audit examples: OBs with at least a displacement or a trade
    # ------------------------------------------------------------------
    if audit_mode:
        collected = 0
        for ob_id, events in audit_ob_timelines.items():
            if collected >= max_audit_examples:
                break
            # Only include OBs that had at least some interesting lifecycle
            states_seen = {e.ob_state for e in events}
            has_displacement = any("RETEST_ELIGIBLE" in s for s in states_seen)
            if not has_displacement:
                continue
            ob_meta = None
            for sym in syms:
                for ob in asset_ob_queue[sym]:
                    if ob.ob_id == ob_id:
                        ob_meta = ob
                        break
                if ob_meta:
                    break
            audit_examples.append({
                "ob_id": ob_id,
                "asset": ob_meta.asset if ob_meta else "?",
                "direction": ob_meta.direction if ob_meta else "?",
                "bos_dt": _to_ist_str(ob_meta.bos_dt) if ob_meta else "?",
                "ob_high": ob_meta.ob_high if ob_meta else 0,
                "ob_low": ob_meta.ob_low if ob_meta else 0,
                "entry_25pct": ob_meta.entry_25pct if ob_meta else 0,
                "final_state": ob_meta.state.name if ob_meta else "?",
                "displacement_mode": cfg.displacement_mode,
                "timeline": [
                    {
                        "bar": e.bar_offset,
                        "dt": _to_ist_str(e.bar_dt),
                        "O": e.candle_o, "H": e.candle_h, "L": e.candle_l, "C": e.candle_c,
                        "state": e.ob_state,
                        "event": e.event,
                        "mfe": round(e.mfe_from_proximal, 6),
                        "candles_outside": e.candles_outside,
                    }
                    for e in events
                ],
            })
            collected += 1

    # ------------------------------------------------------------------
    # Build results
    # ------------------------------------------------------------------
    if executed_trades:
        tdf = pd.DataFrame([asdict(t) for t in executed_trades])
    else:
        tdf = pd.DataFrame()

    def _agg(df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) == 0:
            return {"trades": 0, "wins": 0, "losses": 0, "wr": 0.0,
                    "total_r": 0.0, "exp_r": 0.0, "pf": 0.0}
        n = len(df)
        wins = len(df[df["outcome"] == "FILLED_TP"])
        losses = len(df[df["outcome"] == "FILLED_SL"])
        total_r = float(df["realized_r"].sum())
        gain_r = float(df[df["outcome"] == "FILLED_TP"]["realized_r"].sum()) if wins > 0 else 0.0
        loss_r = abs(float(df[df["outcome"] == "FILLED_SL"]["realized_r"].sum())) if losses > 0 else 1.0
        return {
            "trades": n, "wins": wins, "losses": losses,
            "wr": round(wins / n * 100, 2),
            "total_r": round(total_r, 2),
            "exp_r": round(total_r / n, 4),
            "pf": round(gain_r / loss_r, 2) if loss_r > 0 else 99.0,
        }

    overall = _agg(tdf)

    asset_breakdown: Dict[str, Any] = {}
    for sym in syms:
        sym_df = tdf[tdf["asset"] == sym] if len(tdf) > 0 else pd.DataFrame()
        asset_breakdown[sym] = _agg(sym_df)

    latency_breakdown: Dict[str, Any] = {}
    if len(tdf) > 0:
        bins = [0, 1, 3, 6, 12, 24, 99999]
        labels = ["≤1h (Immediate)", "2-3h", "4-6h", "7-12h", "13-24h", ">24h"]
        tdf2 = tdf.copy()
        tdf2["lat_tier"] = pd.cut(tdf2["entry_bar_from_bos"], bins=bins, labels=labels, right=True)
        for tier, grp in tdf2.groupby("lat_tier", observed=True):
            latency_breakdown[str(tier)] = _agg(grp)

    return {
        "config": asdict(cfg),
        "starting_capital": cfg.starting_capital,
        "ending_capital": capital,
        "total_return_pct": ((capital - cfg.starting_capital) / cfg.starting_capital) * 100.0,
        "total_executed_trades": overall["trades"],
        "wins": overall["wins"],
        "losses": overall["losses"],
        "win_rate_pct": overall["wr"],
        "expectancy_r": overall["exp_r"],
        "total_realized_r": overall["total_r"],
        "profit_factor": overall["pf"],
        "max_drawdown_pct": round(max_dd_pct, 2),
        "asset_breakdown": asset_breakdown,
        "latency_breakdown": latency_breakdown,
        "trades_df": tdf,
        "audit_examples": audit_examples,
    }


# ---------------------------------------------------------------------------
# Lifecycle Audit printer
# ---------------------------------------------------------------------------
def print_lifecycle_audit(
    audit_examples: List[Dict[str, Any]],
    output_path: Optional[Path] = None,
) -> str:
    """
    Render a human-readable lifecycle audit for the collected OB examples.
    Returns the text. Optionally writes to output_path.
    """
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("DISPLACEMENT-GATED OB LIFECYCLE AUDIT")
    lines.append("=" * 72)

    for idx, ex in enumerate(audit_examples, 1):
        lines.append(f"\n{'─' * 72}")
        lines.append(f"OB #{idx:02d}  |  {ex['asset']} {ex['direction']}")
        lines.append(f"BOS confirmed at: {ex['bos_dt']}")
        lines.append(f"OB zone: [{ex['ob_low']:.6f}, {ex['ob_high']:.6f}]  "
                     f"25%-entry: {ex['entry_25pct']:.6f}")
        lines.append(f"Displacement mode: {ex['displacement_mode']}  |  "
                     f"Final state: {ex['final_state']}")
        lines.append("")
        lines.append("  Bar  | Timestamp (IST)               | State                         | Event")
        lines.append("  -----|-------------------------------|-------------------------------|" + "-" * 50)

        for ev in ex["timeline"]:
            bar_str = f"{ev['bar']:>5}"
            dt_str = f"{ev['dt']:<29}"
            state_str = f"{ev['state']:<29}"
            lines.append(f"  {bar_str} | {dt_str} | {state_str} | {ev['event']}")

        lines.append("")

    text = "\n".join(lines)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return text


# =============================================================================
# ▼▼▼  MANUAL-SPEC SMC ENGINE  ▼▼▼
# =============================================================================
# Authoritative implementation of the proven manual TradingView SMC strategy.
# Runs INDEPENDENTLY of the LuxAlgo pipeline (run_displacement_gated_backtest).
#
# Key rules (forensic report, BTC screenshot confirmed):
#   BOS:          close beyond last opposing candle's boundary (no pivots)
#   OB boundary:  BEARISH: top=origin.close, bottom=origin.low
#                 BULLISH: top=origin.high,  bottom=origin.close
#   Displacement: Mode C — probe-then-pullback (close-based, not wick-based)
#   Invalidation: wick-based  (candle.high >= distal for SHORT)
#   Entry:        25% from proximal into OB
#   SL:           distal = OB top for SHORT = origin.close (NOT origin.high)
#   TP:           entry × (1 ∓ 0.006)
#   Global lock:  preserved (same 1-trade-at-a-time constraint)
#
# GOVERNANCE:
#   live_execution_authorized = False  (inherited from module level)
#   AI_PROMOTION_STATUS = "REJECTED"
# =============================================================================


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class ManualOBState(Enum):
    """State machine for the manual-spec OB lifecycle."""
    AWAITING_DISPLACEMENT = "AWAITING_DISPLACEMENT"
    LIMIT_RESTING         = "LIMIT_RESTING"
    TRADE_ACTIVE          = "TRADE_ACTIVE"
    TRADE_CLOSED          = "TRADE_CLOSED"
    INVALIDATED           = "INVALIDATED"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ManualOBRecord:
    """
    Live OB record for the manual-spec strategy engine.

    OB boundary rules (direction-specific, proved by TradingView screenshot):
        SHORT (bearish OB from bullish origin candle):
            ob_top    = origin.CLOSE  (CRITICAL — NOT origin.high)
            ob_bottom = origin.LOW
            distal    = ob_top        SL = ob_top = origin.CLOSE
            proximal  = ob_bottom

        LONG (bullish OB from bearish origin candle):
            ob_top    = origin.HIGH
            ob_bottom = origin.CLOSE  (CRITICAL — NOT origin.low)
            distal    = ob_bottom     SL = ob_bottom = origin.CLOSE
            proximal  = ob_top
    """
    # Identity
    ob_id:                      str
    asset:                      str
    direction:                  str            # "SHORT" | "LONG"

    # Bar references (absolute index in asset DataFrame)
    origin_bar_idx:             int
    bos_bar_idx:                int
    bos_dt:                     datetime
    formation_dt:               datetime       # timestamp of origin candle

    # OB geometry (manual spec)
    ob_top:                     float
    ob_bottom:                  float
    ob_width:                   float
    proximal:                   float          # SHORT: ob_bottom  LONG: ob_top
    distal:                     float          # SHORT: ob_top     LONG: ob_bottom

    # Trade parameters
    entry_price:                float          # 25% from proximal
    sl_price:                   float          # = distal
    tp_price:                   float          # entry × (1 ∓ 0.006)
    sl_dist_pct:                float
    theoretical_leverage:       float
    applied_leverage:           float

    # Mode C displacement state
    state:                      ManualOBState  = field(default=ManualOBState.AWAITING_DISPLACEMENT)
    probe_confirmed:            bool           = False
    displacement_confirmed_dt:  Optional[datetime] = None
    displacement_confirmed_bar: Optional[int]  = None
    # Limit is active starting at this bar index (= displacement_bar + 1)
    limit_active_from_bar:      Optional[int]  = None

    # Diagnostics
    pre_displacement_touches:   int            = 0
    first_touch_dt:             Optional[datetime] = None
    entry_bar_from_bos:         int            = 0
    ob_age_at_entry_hours:      float          = 0.0
    retest_number:              int            = 0
    mfe_from_proximal:          float          = 0.0


@dataclass
class ManualSpecConfig:
    """Configuration for the manual-spec backtest engine."""
    lookback:                   int   = 10      # bars to scan backward for origin
    entry_depth_pct:            float = 0.25    # 25% from proximal into OB
    fixed_tp_market_pct:        float = 0.60    # 0.60% from entry
    max_sl_account_risk_pct:    float = 35.0    # SL risk % of account
    applied_leverage_cap:       float = 100.0   # exchange cap
    fee_rate:                   float = 0.0008  # 0.08% round-trip
    max_holding_bars:           int   = 72      # timeout horizon
    starting_capital:           float = 10.0
    min_ob_width:               float = 1e-6    # reject zero-width OBs
    data_timeframe:             str   = "1h"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def _make_manual_ob(
    asset: str,
    bos_bar_idx: int,
    bos_dt: datetime,
    origin_bar_idx: int,
    origin_dt: datetime,
    direction: str,
    ob_top: float,
    ob_bottom: float,
    cfg: ManualSpecConfig,
) -> ManualOBRecord:
    """Construct a ManualOBRecord from scanner-detected BOS event."""
    width = ob_top - ob_bottom
    if direction == "SHORT":
        proximal  = ob_bottom
        distal    = ob_top           # = origin.close (critical)
        entry     = ob_bottom + cfg.entry_depth_pct * width
        tp        = entry * (1.0 - cfg.fixed_tp_market_pct / 100.0)
    else:   # LONG
        proximal  = ob_top
        distal    = ob_bottom        # = origin.close (critical)
        entry     = ob_top - cfg.entry_depth_pct * width
        tp        = entry * (1.0 + cfg.fixed_tp_market_pct / 100.0)

    sl          = distal
    risk_dist   = abs(entry - sl)
    sl_dist_pct = (risk_dist / entry) * 100.0 if entry > 1e-9 else 0.0
    theo_lev    = cfg.max_sl_account_risk_pct / sl_dist_pct if sl_dist_pct > 1e-9 else 1.0
    applied_lev = min(cfg.applied_leverage_cap, theo_lev)

    ob_id = f"MANUAL_{asset}_{direction}_{origin_bar_idx}_{bos_bar_idx}"
    return ManualOBRecord(
        ob_id=ob_id,
        asset=asset,
        direction=direction,
        origin_bar_idx=origin_bar_idx,
        bos_bar_idx=bos_bar_idx,
        bos_dt=bos_dt,
        formation_dt=origin_dt,
        ob_top=ob_top,
        ob_bottom=ob_bottom,
        ob_width=width,
        proximal=proximal,
        distal=distal,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        sl_dist_pct=sl_dist_pct,
        theoretical_leverage=theo_lev,
        applied_leverage=applied_lev,
    )


def _manual_distal_breached(ob: ManualOBRecord, c_h: float, c_l: float) -> bool:
    """
    Wick-based distal boundary check (pre-entry invalidation).

    SHORT: candle.high >= ob_top  (ob_top = origin.close)
    LONG:  candle.low  <= ob_bottom (ob_bottom = origin.close)
    """
    if ob.direction == "SHORT":
        return c_h >= ob.distal
    return c_l <= ob.distal


def _manual_entry_touched(ob: ManualOBRecord, c_h: float, c_l: float) -> bool:
    """Check if the 25%-depth entry level is touched by the candle wick."""
    if ob.direction == "SHORT":
        return c_h >= ob.entry_price
    return c_l <= ob.entry_price


def _manual_sl_hit(direction: str, c_h: float, c_l: float, sl: float) -> bool:
    """Post-entry stop-loss check (wick-based)."""
    if direction == "SHORT":
        return c_h >= sl
    return c_l <= sl


def _manual_tp_hit(direction: str, c_h: float, c_l: float, tp: float) -> bool:
    """Post-entry take-profit check (wick-based)."""
    if direction == "SHORT":
        return c_l <= tp
    return c_h >= tp


# ---------------------------------------------------------------------------
# ManualSpecBOSScanner
# ---------------------------------------------------------------------------
class ManualSpecBOSScanner:
    """
    Streaming, causal BOS scanner implementing the proven manual TradingView SMC rule.
    One instance per asset; call scan() bar-by-bar in chronological order.

    SHORT setup rules (bearish OB):
        origin  = most recent bullish candle (close > open) within last N bars
        ob_top  = origin.close   ← CRITICAL: NOT origin.high
        ob_bot  = origin.low
        BOS     = current_close < ob_bottom  (strict; close-only)

    LONG setup rules (bullish OB):
        origin  = most recent bearish candle (close < open) within last N bars
        ob_top  = origin.high
        ob_bot  = origin.close   ← CRITICAL: NOT origin.low
        BOS     = current_close > ob_top  (strict; close-only)

    Deduplication:
        consumed_origins prevents the same origin bar from generating
        multiple BOS events (e.g. as price continues past the same boundary).
        One origin → one setup, ever.

    Admission timing:
        OBs returned by scan() at bar B are added to the live pool AFTER
        bar B is fully processed, so displacement monitoring starts at B+1.
        This correctly implements break+1 (not break+2) admission.
    """

    def __init__(self, lookback: int = 10, min_width: float = 1e-6) -> None:
        self.lookback  = lookback
        self.min_width = min_width
        # Circular history: (bar_idx, open, high, low, close, timestamp)
        self._history: collections.deque = collections.deque(maxlen=lookback + 1)
        # Consumed origin keys: (asset, origin_bar_idx) → prevents duplicates
        self._consumed: set = set()

    def reset(self) -> None:
        """Reset scanner state. Call when switching assets or re-running."""
        self._history.clear()
        self._consumed.clear()

    def scan(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        cfg: ManualSpecConfig,
    ) -> List[ManualOBRecord]:
        """
        Process one candle. Returns newly created ManualOBRecords (may be empty).

        The BOS candle's CLOSE is the causal trigger. The OB origin and BOS
        are identified simultaneously at the BOS candle close — no future
        information is required.

        Invariant: the BOS candle itself is never the OB origin (origin must
        be a candle BEFORE the current bar).
        """
        self._history.append((bar_idx, o, h, l, c, ts))

        # Need at least 2 bars: one origin candidate + one BOS candle
        if len(self._history) < 2:
            return []

        new_obs: List[ManualOBRecord] = []

        # ── SHORT setup ─────────────────────────────────────────────────────
        # Scan backward through history[:-1] (exclude current bar as origin)
        bull_origin = None
        for i in range(len(self._history) - 2, -1, -1):
            bi, eo, eh, el, ec, ets = self._history[i]
            if ec > eo:                   # bullish: close > open
                bull_origin = self._history[i]
                break

        if bull_origin is not None:
            bi, eo, eh, el, ec, ets = bull_origin
            ob_top_s = ec              # CLOSE (critical — not HIGH)
            ob_bot_s = el              # LOW
            width_s  = ob_top_s - ob_bot_s
            # BOS: strict close below origin low
            if width_s >= self.min_width and c < ob_bot_s:
                key = (asset, bi)
                if key not in self._consumed:
                    self._consumed.add(key)
                    new_obs.append(_make_manual_ob(
                        asset=asset, bos_bar_idx=bar_idx, bos_dt=ts,
                        origin_bar_idx=bi, origin_dt=ets,
                        direction="SHORT",
                        ob_top=ob_top_s, ob_bottom=ob_bot_s,
                        cfg=cfg,
                    ))

        # ── LONG setup ──────────────────────────────────────────────────────
        bear_origin = None
        for i in range(len(self._history) - 2, -1, -1):
            bi, eo, eh, el, ec, ets = self._history[i]
            if ec < eo:                   # bearish: close < open
                bear_origin = self._history[i]
                break

        if bear_origin is not None:
            bi, eo, eh, el, ec, ets = bear_origin
            ob_top_l = eh              # HIGH
            ob_bot_l = ec              # CLOSE (critical — not LOW)
            width_l  = ob_top_l - ob_bot_l
            # BOS: strict close above origin high
            if width_l >= self.min_width and c > ob_top_l:
                key = (asset, bi)
                if key not in self._consumed:
                    self._consumed.add(key)
                    new_obs.append(_make_manual_ob(
                        asset=asset, bos_bar_idx=bar_idx, bos_dt=ts,
                        origin_bar_idx=bi, origin_dt=ets,
                        direction="LONG",
                        ob_top=ob_top_l, ob_bottom=ob_bot_l,
                        cfg=cfg,
                    ))

        return new_obs


# ---------------------------------------------------------------------------
# run_manual_spec_backtest
# ---------------------------------------------------------------------------
def run_manual_spec_backtest(
    data_base_dir: Optional[Path] = None,
    config: Optional[ManualSpecConfig] = None,
    symbols: Optional[List[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Run the manual-spec SMC backtest across the canonical 4-asset dataset.

    Uses ManualSpecBOSScanner (pivot-free, close-based BOS) and Mode C
    (probe → pullback) displacement.  All portfolio-level invariants preserved:
        - Global one-trade lock across all 4 assets
        - Wick-based invalidation (wick reaches distal → killed)
        - Strictly causal (no future candle information used)
        - Displacement candle cannot be the retest candle
        - No time-based expiry on resting limit order

    Returns the same dict structure as run_displacement_gated_backtest()
    for compatibility.
    """
    assert not live_execution_authorized, "Governance: live execution not authorised."

    cfg  = config or ManualSpecConfig()
    syms = symbols or ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    root = data_base_dir or (
        _find_repo_root() / "data" / "canonical" / "delta_exchange_india"
    )

    from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history

    # ------------------------------------------------------------------
    # Load candles
    # ------------------------------------------------------------------
    asset_candles: Dict[str, pd.DataFrame] = {}
    for sym in syms:
        try:
            candles = load_canonical_full_history(root, sym)
        except Exception:
            asset_candles[sym] = pd.DataFrame()
            continue
        rows = [
            {
                "timestamp": c.timestamp,
                "open":      float(c.open),
                "high":      float(c.high),
                "low":       float(c.low),
                "close":     float(c.close),
                "volume":    float(c.volume),
            }
            for c in candles
        ]
        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        asset_candles[sym] = df

    # ------------------------------------------------------------------
    # Build global sorted timeline
    # ------------------------------------------------------------------
    timeline_rows: List[Tuple[datetime, str, int]] = []
    for sym in syms:
        df = asset_candles.get(sym)
        if df is None or len(df) == 0:
            continue
        for i, row in df.iterrows():
            ts = row["timestamp"]
            if start_date is not None and ts < start_date:
                continue
            if end_date is not None and ts > end_date:
                continue
            timeline_rows.append((ts, sym, int(i)))
    timeline_rows.sort(key=lambda x: (x[0], x[1]))

    # ------------------------------------------------------------------
    # Per-asset scanners (independent history/consumed-origins)
    # ------------------------------------------------------------------
    scanners: Dict[str, ManualSpecBOSScanner] = {
        sym: ManualSpecBOSScanner(lookback=cfg.lookback, min_width=cfg.min_ob_width)
        for sym in syms
    }

    # ------------------------------------------------------------------
    # Simulation state
    # ------------------------------------------------------------------
    live_obs: Dict[str, ManualOBRecord]  = {}
    global_lock_until_dt: Optional[datetime] = None
    capital      = cfg.starting_capital
    peak_capital = cfg.starting_capital
    max_dd_pct   = 0.0
    cum_r        = 0.0
    trade_id_counter = 0
    executed_trades: List[TradeRecord] = []
    active_trade: Optional[Dict] = None     # one active trade at most (global lock)

    # ------------------------------------------------------------------
    # Candle-by-candle simulation
    # ------------------------------------------------------------------
    for (c_ts, sym, bar_local_idx) in timeline_rows:
        df  = asset_candles[sym]
        row = df.iloc[bar_local_idx]
        c_o = float(row["open"])
        c_h = float(row["high"])
        c_l = float(row["low"])
        c_c = float(row["close"])

        # ── 1. Handle active trade TP / SL ──────────────────────────
        if active_trade is not None and active_trade["asset"] == sym:
            at   = active_trade
            dir_ = at["direction"]
            tp_p = at["tp_price"]
            sl_p = at["sl_price"]

            hit_tp = _manual_tp_hit(dir_, c_h, c_l, tp_p)
            hit_sl = _manual_sl_hit(dir_, c_h, c_l, sl_p)

            if hit_tp or hit_sl:
                if hit_tp and hit_sl:
                    # Conservative: SL first (same-candle ambiguity)
                    outcome     = "FILLED_SL"
                    exit_reason = "DUAL_TOUCH_CONSERVATIVE_SL"
                    exit_p      = sl_p
                    is_ambiguous = True
                    narrative   = (
                        "Dual-touch: both TP and SL hit same candle. "
                        "Conservative SL-first applied."
                    )
                elif hit_tp:
                    outcome     = "FILLED_TP"
                    exit_reason = "TP_HIT"
                    exit_p      = tp_p
                    is_ambiguous = False
                    narrative   = f"Fixed +0.60% TP reached at {tp_p:.6f}."
                else:
                    outcome     = "FILLED_SL"
                    exit_reason = "SL_HIT"
                    exit_p      = sl_p
                    is_ambiguous = False
                    narrative   = f"SL breached at {sl_p:.6f}."

                entry_p      = at["entry_price"]
                applied_lev  = at["applied_leverage"]
                theo_lev     = at["theoretical_leverage"]
                risk_dist    = at["risk_dist"]
                reward_dist  = at["reward_dist"]
                gross_sl_ret = at["gross_sl_return_pct"]
                gross_tp_ret = at["gross_tp_return_pct"]
                start_bal    = at["starting_capital"]

                if outcome == "FILLED_TP":
                    ret_pct    = gross_tp_ret
                    realized_r = reward_dist / risk_dist if risk_dist > 1e-9 else 0.0
                else:
                    ret_pct    = -gross_sl_ret
                    realized_r = -1.0

                notional  = start_bal * applied_lev
                fees_usd  = notional * cfg.fee_rate
                gross_pnl = start_bal * (ret_pct / 100.0)
                net_pnl   = gross_pnl - fees_usd
                capital   = max(0.0, start_bal + net_pnl)

                if capital > peak_capital:
                    peak_capital = capital
                dd = ((peak_capital - capital) / peak_capital * 100.0
                      if peak_capital > 0 else 0.0)
                max_dd_pct = max(max_dd_pct, dd)
                cum_r += realized_r

                fill_dt      = at["fill_dt"]
                holding_secs = (c_ts - fill_dt).total_seconds()
                holding_hrs  = max(1.0, holding_secs / 3600.0)

                ob = at["ob"]
                ob.state = ManualOBState.TRADE_CLOSED

                trade_id_counter += 1
                tr = TradeRecord(
                    trade_id=trade_id_counter,
                    asset=sym,
                    direction=dir_,
                    bos_time=_to_utc_str(ob.bos_dt),
                    ob_formation_time=_to_utc_str(ob.formation_dt),
                    displacement_confirmed_time=_to_utc_str(ob.displacement_confirmed_dt),
                    retest_time=_to_utc_str(fill_dt),
                    entry_time=_to_utc_str(fill_dt),
                    exit_time=_to_utc_str(c_ts),
                    bos_time_ist=_to_ist_str(ob.bos_dt),
                    ob_formation_time_ist=_to_ist_str(ob.formation_dt),
                    displacement_confirmed_time_ist=_to_ist_str(ob.displacement_confirmed_dt),
                    retest_time_ist=_to_ist_str(fill_dt),
                    entry_time_ist=_to_ist_str(fill_dt),
                    exit_time_ist=_to_ist_str(c_ts),
                    ob_high=round(ob.ob_top, 6),
                    ob_low=round(ob.ob_bottom, 6),
                    ob_width=round(ob.ob_width, 6),
                    ob_width_pct=round(
                        (ob.ob_width / ob.entry_price) * 100.0, 4
                    ) if ob.entry_price > 0 else 0.0,
                    proximal=round(ob.proximal, 6),
                    distal=round(ob.distal, 6),
                    entry_price=round(entry_p, 6),
                    sl_price=round(sl_p, 6),
                    tp_price=round(tp_p, 6),
                    entry_to_sl_distance_pct=round(ob.sl_dist_pct, 4),
                    theoretical_leverage=round(theo_lev, 2),
                    leverage=round(applied_lev, 2),
                    displacement_mode="C_PROBE_PULLBACK",
                    displacement_threshold_value=0.0,
                    mfe_from_proximal=round(ob.mfe_from_proximal, 6),
                    mfe_pct=0.0,
                    mfe_ob_width_multiples=0.0,
                    candles_fully_outside_ob=0,
                    pre_displacement_touches=ob.pre_displacement_touches,
                    entry_bar_from_bos=ob.entry_bar_from_bos,
                    ob_age_at_entry_hours=round(ob.ob_age_at_entry_hours, 2),
                    retest_number=ob.retest_number,
                    gross_sl_return_pct=round(gross_sl_ret, 2),
                    gross_tp_return_pct=round(gross_tp_ret, 2),
                    fees_usd=fees_usd,
                    net_return_pct=(
                        round((net_pnl / start_bal) * 100.0, 2)
                        if start_bal > 0 else 0.0
                    ),
                    starting_capital=start_bal,
                    position_notional=notional,
                    gross_pnl_usd=gross_pnl,
                    pnl_usd=net_pnl,
                    ending_capital=capital,
                    outcome=outcome,
                    reason_for_exit=exit_reason,
                    is_ambiguous=is_ambiguous,
                    holding_bars=int(holding_hrs),
                    holding_time_hours=round(holding_hrs, 2),
                    realized_r=round(realized_r, 4),
                    cumulative_realized_r=round(cum_r, 4),
                    data_timeframe=cfg.data_timeframe,
                    trade_narrative=narrative,
                )
                executed_trades.append(tr)
                global_lock_until_dt = c_ts
                active_trade = None
                live_obs.pop(ob.ob_id, None)

            else:
                # Timeout check
                bars_held = int(
                    (c_ts - at["fill_dt"]).total_seconds() / 3600
                )
                if bars_held >= cfg.max_holding_bars:
                    ob       = at["ob"]
                    ob.state = ManualOBState.TRADE_CLOSED
                    dir_     = at["direction"]
                    p_diff   = ((c_c - at["entry_price"]) if dir_ == "LONG"
                                else (at["entry_price"] - c_c))
                    risk_d   = at["risk_dist"]
                    realized_r = p_diff / risk_d if risk_d > 1e-9 else 0.0
                    start_bal  = at["starting_capital"]
                    applied_lev = at["applied_leverage"]
                    theo_lev   = at["theoretical_leverage"]
                    ret_pct    = realized_r * at["gross_sl_return_pct"]
                    notional   = start_bal * applied_lev
                    fees_usd   = notional * cfg.fee_rate
                    gross_pnl  = start_bal * (ret_pct / 100.0)
                    net_pnl    = gross_pnl - fees_usd
                    capital    = max(0.0, start_bal + net_pnl)
                    if capital > peak_capital:
                        peak_capital = capital
                    dd = ((peak_capital - capital) / peak_capital * 100.0
                          if peak_capital > 0 else 0.0)
                    max_dd_pct = max(max_dd_pct, dd)
                    cum_r += realized_r
                    holding_hrs = max(1.0,
                        (c_ts - at["fill_dt"]).total_seconds() / 3600)
                    fill_dt = at["fill_dt"]
                    trade_id_counter += 1
                    tr = TradeRecord(
                        trade_id=trade_id_counter,
                        asset=sym, direction=dir_,
                        bos_time=_to_utc_str(ob.bos_dt),
                        ob_formation_time=_to_utc_str(ob.formation_dt),
                        displacement_confirmed_time=_to_utc_str(
                            ob.displacement_confirmed_dt),
                        retest_time=_to_utc_str(fill_dt),
                        entry_time=_to_utc_str(fill_dt),
                        exit_time=_to_utc_str(c_ts),
                        bos_time_ist=_to_ist_str(ob.bos_dt),
                        ob_formation_time_ist=_to_ist_str(ob.formation_dt),
                        displacement_confirmed_time_ist=_to_ist_str(
                            ob.displacement_confirmed_dt),
                        retest_time_ist=_to_ist_str(fill_dt),
                        entry_time_ist=_to_ist_str(fill_dt),
                        exit_time_ist=_to_ist_str(c_ts),
                        ob_high=round(ob.ob_top, 6),
                        ob_low=round(ob.ob_bottom, 6),
                        ob_width=round(ob.ob_width, 6),
                        ob_width_pct=round(
                            (ob.ob_width / ob.entry_price) * 100.0, 4
                        ) if ob.entry_price > 0 else 0.0,
                        proximal=round(ob.proximal, 6),
                        distal=round(ob.distal, 6),
                        entry_price=round(at["entry_price"], 6),
                        sl_price=round(ob.sl_price, 6),
                        tp_price=round(ob.tp_price, 6),
                        entry_to_sl_distance_pct=round(ob.sl_dist_pct, 4),
                        theoretical_leverage=round(theo_lev, 2),
                        leverage=round(applied_lev, 2),
                        displacement_mode="C_PROBE_PULLBACK",
                        displacement_threshold_value=0.0,
                        mfe_from_proximal=round(ob.mfe_from_proximal, 6),
                        mfe_pct=0.0,
                        mfe_ob_width_multiples=0.0,
                        candles_fully_outside_ob=0,
                        pre_displacement_touches=ob.pre_displacement_touches,
                        entry_bar_from_bos=ob.entry_bar_from_bos,
                        ob_age_at_entry_hours=round(ob.ob_age_at_entry_hours, 2),
                        retest_number=ob.retest_number,
                        gross_sl_return_pct=round(
                            at["gross_sl_return_pct"], 2),
                        gross_tp_return_pct=round(
                            at["gross_tp_return_pct"], 2),
                        fees_usd=fees_usd,
                        net_return_pct=(
                            round((net_pnl / start_bal) * 100.0, 2)
                            if start_bal > 0 else 0.0
                        ),
                        starting_capital=start_bal,
                        position_notional=notional,
                        gross_pnl_usd=gross_pnl,
                        pnl_usd=net_pnl,
                        ending_capital=capital,
                        outcome="FILLED_TIMEOUT",
                        reason_for_exit="TIMEOUT",
                        is_ambiguous=False,
                        holding_bars=int(holding_hrs),
                        holding_time_hours=round(holding_hrs, 2),
                        realized_r=round(realized_r, 4),
                        cumulative_realized_r=round(cum_r, 4),
                        data_timeframe=cfg.data_timeframe,
                        trade_narrative=(
                            f"{cfg.max_holding_bars}h horizon expired. "
                            f"Closed at {c_c:.6f}."
                        ),
                    )
                    executed_trades.append(tr)
                    global_lock_until_dt = c_ts
                    active_trade = None
                    live_obs.pop(ob.ob_id, None)

        # ── 2. Update OB lifecycle for all live OBs on this asset ───
        obs_to_remove: List[str] = []

        for ob_id, ob in list(live_obs.items()):
            if ob.asset != sym:
                continue
            if ob.state in (ManualOBState.TRADE_CLOSED,
                             ManualOBState.INVALIDATED):
                obs_to_remove.append(ob_id)
                continue
            if ob.state == ManualOBState.TRADE_ACTIVE:
                continue      # handled in step 1

            # Update MFE tracking (useful for diagnostics)
            mfe_this = (max(0.0, ob.proximal - c_l) if ob.direction == "SHORT"
                        else max(0.0, c_h - ob.proximal))
            if mfe_this > ob.mfe_from_proximal:
                ob.mfe_from_proximal = mfe_this

            # ── State: AWAITING_DISPLACEMENT ────────────────────────
            if ob.state == ManualOBState.AWAITING_DISPLACEMENT:

                # 1. Wick-based distal invalidation
                if _manual_distal_breached(ob, c_h, c_l):
                    ob.state = ManualOBState.INVALIDATED
                    obs_to_remove.append(ob_id)
                    continue

                # 2. Track pre-displacement touches (no fill allowed)
                if _manual_entry_touched(ob, c_h, c_l):
                    ob.pre_displacement_touches += 1
                    if ob.first_touch_dt is None:
                        ob.first_touch_dt = c_ts

                # 3. Mode C probe → pullback detection (close-based)
                if not ob.probe_confirmed:
                    # Waiting for first close on the proximal side of OB
                    # SHORT: probe = close ABOVE ob_bottom (price returns up)
                    # LONG:  probe = close BELOW ob_top    (price returns down)
                    if ob.direction == "SHORT" and c_c > ob.proximal:
                        ob.probe_confirmed = True
                    elif ob.direction == "LONG" and c_c < ob.proximal:
                        ob.probe_confirmed = True
                else:
                    # Probe confirmed; wait for close back through proximal
                    # SHORT: pullback = close BELOW ob_bottom
                    # LONG:  pullback = close ABOVE ob_top
                    if ob.direction == "SHORT" and c_c < ob.proximal:
                        ob.state = ManualOBState.LIMIT_RESTING
                        ob.displacement_confirmed_dt  = c_ts
                        ob.displacement_confirmed_bar = bar_local_idx
                        ob.limit_active_from_bar      = bar_local_idx + 1
                        # Displacement candle cannot simultaneously be the
                        # entry candle (invariant from forensic spec §4)
                        continue
                    elif ob.direction == "LONG" and c_c > ob.proximal:
                        ob.state = ManualOBState.LIMIT_RESTING
                        ob.displacement_confirmed_dt  = c_ts
                        ob.displacement_confirmed_bar = bar_local_idx
                        ob.limit_active_from_bar      = bar_local_idx + 1
                        continue

            # ── State: LIMIT_RESTING ─────────────────────────────────
            elif ob.state == ManualOBState.LIMIT_RESTING:

                # 1. Wick-based distal invalidation (post-displacement,
                #    pre-entry)
                if _manual_distal_breached(ob, c_h, c_l):
                    ob.state = ManualOBState.INVALIDATED
                    obs_to_remove.append(ob_id)
                    continue

                # 2. Entry check — only from limit_active_from_bar onwards
                #    (the displacement candle itself cannot be the entry bar)
                if (ob.limit_active_from_bar is not None
                        and bar_local_idx >= ob.limit_active_from_bar
                        and _manual_entry_touched(ob, c_h, c_l)):

                    # Global lock prevents concurrent trades across assets
                    if (global_lock_until_dt is not None
                            and c_ts <= global_lock_until_dt):
                        continue

                    # ─── ENTRY FILLED ───────────────────────────────
                    ob.state = ManualOBState.TRADE_ACTIVE
                    ob.retest_number       += 1
                    ob.entry_bar_from_bos   = bar_local_idx - ob.bos_bar_idx
                    ob.ob_age_at_entry_hours = (
                        (c_ts - ob.bos_dt).total_seconds() / 3600
                    )

                    risk_dist   = abs(ob.entry_price - ob.sl_price)
                    reward_dist = abs(ob.tp_price - ob.entry_price)
                    gross_sl    = ob.applied_leverage * ob.sl_dist_pct
                    gross_tp    = cfg.fixed_tp_market_pct * ob.applied_leverage

                    active_trade = {
                        "ob":                   ob,
                        "asset":                sym,
                        "direction":            ob.direction,
                        "entry_price":          ob.entry_price,
                        "sl_price":             ob.sl_price,
                        "tp_price":             ob.tp_price,
                        "applied_leverage":     ob.applied_leverage,
                        "theoretical_leverage": ob.theoretical_leverage,
                        "risk_dist":            risk_dist,
                        "reward_dist":          reward_dist,
                        "gross_sl_return_pct":  gross_sl,
                        "gross_tp_return_pct":  gross_tp,
                        "starting_capital":     capital,
                        "fill_dt":              c_ts,
                        "retest_dt":            c_ts,
                    }
                    global_lock_until_dt = c_ts

        for ob_id in obs_to_remove:
            live_obs.pop(ob_id, None)

        # ── 3. Run scanner → add new OBs from this bar ──────────────
        # New OBs are added AFTER this bar's lifecycle update, so they
        # start being monitored from the NEXT bar (break+1 admission).
        # This prevents the BOS candle from acting as its own displacement.
        new_obs = scanners[sym].scan(
            sym, bar_local_idx, c_ts, c_o, c_h, c_l, c_c, cfg
        )
        for ob in new_obs:
            live_obs[ob.ob_id] = ob

    # ------------------------------------------------------------------
    # Build results (compatible with run_displacement_gated_backtest)
    # ------------------------------------------------------------------
    if executed_trades:
        tdf = pd.DataFrame([asdict(t) for t in executed_trades])
    else:
        tdf = pd.DataFrame()

    def _agg(df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) == 0:
            return {
                "trades": 0, "wins": 0, "losses": 0,
                "wr": 0.0, "total_r": 0.0, "exp_r": 0.0, "pf": 0.0,
            }
        n      = len(df)
        wins   = len(df[df["outcome"] == "FILLED_TP"])
        losses = len(df[df["outcome"] == "FILLED_SL"])
        total_r = float(df["realized_r"].sum())
        gain_r  = (float(df[df["outcome"] == "FILLED_TP"]["realized_r"].sum())
                   if wins > 0 else 0.0)
        loss_r  = (abs(float(df[df["outcome"] == "FILLED_SL"]["realized_r"].sum()))
                   if losses > 0 else 1.0)
        return {
            "trades":   n,
            "wins":     wins,
            "losses":   losses,
            "wr":       round(wins / n * 100, 2),
            "total_r":  round(total_r, 2),
            "exp_r":    round(total_r / n, 4),
            "pf":       round(gain_r / loss_r, 2) if loss_r > 0 else 99.0,
        }

    overall = _agg(tdf)
    asset_breakdown: Dict[str, Any] = {}
    for sym in syms:
        sym_df = tdf[tdf["asset"] == sym] if len(tdf) > 0 else pd.DataFrame()
        asset_breakdown[sym] = _agg(sym_df)

    return {
        "config":                 vars(cfg),
        "starting_capital":       cfg.starting_capital,
        "ending_capital":         capital,
        "total_return_pct":       (
            (capital - cfg.starting_capital) / cfg.starting_capital * 100.0
        ),
        "total_executed_trades":  overall["trades"],
        "wins":                   overall["wins"],
        "losses":                 overall["losses"],
        "win_rate_pct":           overall["wr"],
        "expectancy_r":           overall["exp_r"],
        "total_realized_r":       overall["total_r"],
        "profit_factor":          overall["pf"],
        "max_drawdown_pct":       round(max_dd_pct, 2),
        "asset_breakdown":        asset_breakdown,
        "trades_df":              tdf,
    }
