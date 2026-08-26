"""
QuantEdge AI — First-Touch 3-Candle Qualification / OB Expiry Research Engine.
==============================================================================
Pre-registered research experiment evaluating the hypothesis:
"If an OB is touched but price does not penetrate to the 25% entry within the
first 3 candles, that OB has already shown rejection behavior and should be
permanently invalidated rather than traded on a much later retest."

Reuses canonical deterministic QuantEdge SMC Order Block detection unchanged.
Implements:
1. Canonical SMC OB detection (no same-candle entry on BOS confirmation).
2. 25% penetration depth limit entry & distal OB stop-loss.
3. First-touch detection: first candle where candle range overlaps [OB_low, OB_high].
4. 3-candle qualification window: Touch Candle 0, 1, 2.
5. Failure to reach 25% within 3 candles -> Permanent invalidation (FIRST_TOUCH_NO_25PCT_WITHIN_3_BARS).
6. Distal breach before 25% -> Immediate invalidation (INVALIDATED_BEFORE_FILL).
7. Global 1-Trade Lock across BTCUSD, ETHUSD, SOLUSD, XRPUSD.
8. $10.00 starting capital with continuous compounding and 0.08% taker fees.
9. Side-by-side comparison: Baseline vs New Strategy + Removed Trades Analysis.
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
import pandas as pd
import numpy as np
import json
import csv

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups, Candle

IST_TZ = timezone(timedelta(hours=5, minutes=30))

@dataclass
class FirstTouchConfig:
    fixed_tp_pct: float = 0.60          # Fixed +0.60% price move target
    max_sl_risk_pct: float = 35.0        # Target capital risk on SL (35%)
    max_leverage: float = 100.0          # Strict leverage cap (100x)
    penetration_depth: float = 0.25      # 25% depth into OB
    fee_rate: float = 0.0008             # 0.08% roundtrip taker fee
    max_holding_bars: int = 72           # 72 hours max holding horizon
    qualification_window_bars: int = 3   # 3-candle qualification window from first touch
    starting_capital: float = 10.0       # Initial portfolio capital
    data_timeframe: str = "1h"           # Canonical 1H data

@dataclass
class FirstTouchTradeRecord:
    trade_id: int
    asset: str
    direction: str
    setup_time: str
    setup_time_ist: str
    first_touch_time: str
    first_touch_time_ist: str
    entry_time: str
    entry_time_ist: str
    exit_time: str
    exit_time_ist: str
    touch_to_fill_bars: int              # 0, 1, or 2
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_pct: float
    entry_price: float
    sl_price: float
    tp_price: float
    sl_distance_pct: float
    leverage: float
    actual_sl_risk_pct: float
    tp_target_return_pct: float
    starting_capital: float
    position_notional: float
    gross_pnl: float
    fees: float
    net_pnl: float
    ending_capital: float
    outcome: str                         # FILLED_TP, FILLED_SL, FILLED_TIMEOUT
    exit_reason: str                     # TP_HIT, SL_HIT, DUAL_TOUCH_CONSERVATIVE_SL, TIMEOUT
    is_ambiguous: bool
    holding_bars: int
    holding_time_hours: float
    realized_r: float
    cumulative_realized_r: float
    data_timeframe: str
    trade_narrative: str

@dataclass
class RemovedTradeRecord:
    ob_id: str
    asset: str
    direction: str
    setup_time: str
    setup_time_ist: str
    first_touch_time: str
    first_touch_time_ist: str
    bars_from_touch_to_old_entry: int
    old_entry_time: str
    old_entry_time_ist: str
    old_exit_time: str
    old_exit_time_ist: str
    entry_price: float
    sl_price: float
    tp_price: float
    leverage: float
    old_outcome: str                     # FILLED_TP, FILLED_SL, FILLED_TIMEOUT
    old_realized_r: float
    old_net_pnl_pct: float
    removal_reason: str
    capital_impact_type: str             # "SAVED_LOSS" or "MISSED_WIN"

def to_utc_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")

def to_ist_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")

def run_first_touch_3_candle_backtest(
    data_base_dir: Optional[Path] = None,
    config: Optional[FirstTouchConfig] = None,
    symbols: Optional[List[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    enforce_3_candle_rule: bool = True,
) -> Dict[str, Any]:
    """
    Runs deterministic backtest across canonical data.
    enforce_3_candle_rule=True: New Strategy (First-Touch 3-Candle Expiry)
    enforce_3_candle_rule=False: Baseline Strategy (No 3-Candle Expiry)
    """
    cfg = config or FirstTouchConfig()
    syms = symbols or ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    root = data_base_dir or (_find_repo_root() / "data" / "canonical" / "delta_exchange_india")
    
    candles_df_dict: Dict[str, pd.DataFrame] = {}
    all_candidate_setups: List[Dict[str, Any]] = []
    
    for sym in syms:
        candles = load_canonical_full_history(root, sym)
        c_records = []
        for c in candles:
            c_records.append({
                "timestamp": c.timestamp,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            })
        c_df = pd.DataFrame(c_records).sort_values("timestamp").reset_index(drop=True)
        candles_df_dict[sym] = c_df
        
        ctx = build_smc_context(candles)
        setups, _ = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)
        
        for s in setups:
            dec_bar = s.decision_bar
            dec_ts = candles[dec_bar].timestamp
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
            if dir_ == "LONG":
                entry_p = top - cfg.penetration_depth * w
                sl_p = bot
                risk_dist = entry_p - sl_p
                tp_p = entry_p * (1.0 + cfg.fixed_tp_pct / 100.0)
                reward_dist = tp_p - entry_p
            else:
                entry_p = bot + cfg.penetration_depth * w
                sl_p = top
                risk_dist = sl_p - entry_p
                tp_p = entry_p * (1.0 - cfg.fixed_tp_pct / 100.0)
                reward_dist = entry_p - tp_p
                
            if risk_dist <= 1e-6:
                continue
                
            sl_dist_dec = risk_dist / entry_p
            sl_dist_pct = sl_dist_dec * 100.0
            
            uncapped_lev = cfg.max_sl_risk_pct / sl_dist_pct
            leverage = min(cfg.max_leverage, uncapped_lev)
            actual_sl_risk_pct = leverage * sl_dist_pct
            tp_ret_pct = cfg.fixed_tp_pct * leverage
            
            all_candidate_setups.append({
                "ob_id": s.setup_id,
                "asset": sym,
                "direction": dir_,
                "decision_bar": dec_bar,
                "decision_dt": dec_ts,
                "entry_price": entry_p,
                "sl_price": sl_p,
                "tp_price": tp_p,
                "ob_high": top,
                "ob_low": bot,
                "ob_width": w,
                "ob_width_pct": (w / entry_p) * 100.0,
                "sl_distance_pct": sl_dist_pct,
                "leverage": leverage,
                "actual_sl_risk_pct": actual_sl_risk_pct,
                "tp_ret_pct": tp_ret_pct,
                "risk_dist": risk_dist,
                "reward_dist": reward_dist,
            })
            
    all_candidate_setups = sorted(all_candidate_setups, key=lambda x: (x["decision_dt"], x["asset"], x["ob_id"]))
    
    global_lock_until_dt: Optional[datetime] = None
    capital_net = cfg.starting_capital
    peak_net = cfg.starting_capital
    max_dd_net = 0.0
    
    executed_trades: List[FirstTouchTradeRecord] = []
    skipped_global_lock = []
    no_fill_setups = []
    first_touch_expirations = []
    invalidations_before_fill = []
    first_touch_events = []
    
    cum_realized_r = 0.0
    
    for s in all_candidate_setups:
        asset = s["asset"]
        dec_dt = s["decision_dt"]
        dir_ = s["direction"]
        top = s["ob_high"]
        bot = s["ob_low"]
        entry_p = s["entry_price"]
        sl_p = s["sl_price"]
        tp_p = s["tp_price"]
        leverage = s["leverage"]
        actual_sl_risk_pct = s["actual_sl_risk_pct"]
        tp_ret_pct = s["tp_ret_pct"]
        risk_dist = s["risk_dist"]
        reward_dist = s["reward_dist"]
        
        if global_lock_until_dt is not None and dec_dt < global_lock_until_dt:
            skipped_global_lock.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "decision_dt": dec_dt,
                "locked_until": global_lock_until_dt,
            })
            continue
            
        c_df = candles_df_dict[asset]
        future_candles = c_df[c_df["timestamp"] > dec_dt].reset_index(drop=True)
        
        first_touched = False
        first_touch_bar = None
        first_touch_dt = None
        
        filled = False
        fill_bar = None
        fill_dt = None
        touch_to_fill_bars = None
        
        outcome = "NO_FILL"
        exit_reason = "N/A"
        exit_dt = None
        exit_p = None
        is_ambiguous = False
        narrative = ""
        
        max_b = min(cfg.max_holding_bars, len(future_candles))
        for b in range(max_b):
            c = future_candles.iloc[b]
            c_dt = c["timestamp"]
            c_o, c_h, c_l, c_c = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            
            # Step 1: Detect First Touch (zone overlap)
            is_zone_overlap = (c_h >= bot and c_l <= top)
            if not first_touched and is_zone_overlap:
                first_touched = True
                first_touch_bar = b
                first_touch_dt = c_dt
                first_touch_events.append({
                    "ob_id": s["ob_id"],
                    "asset": asset,
                    "first_touch_dt": first_touch_dt,
                })
                
            # If 3-candle rule is enforced, check if qualification window expired
            if enforce_3_candle_rule and first_touched and not filled:
                bars_since_touch = b - first_touch_bar
                if bars_since_touch >= cfg.qualification_window_bars:
                    # 3 full bars elapsed without reaching 25% entry -> Permanent Invalidation
                    outcome = "FIRST_TOUCH_NO_25PCT_WITHIN_3_BARS"
                    exit_reason = "EXPIRED_AFTER_3_BARS"
                    narrative = f"OB touched at {to_ist_str(first_touch_dt)} but failed to reach 25% entry within 3 candles. Permanently expired."
                    first_touch_expirations.append(s["ob_id"])
                    break
                    
            # Step 2: Check 25% Penetration Entry Fill
            if not filled:
                if dir_ == "LONG" and c_l <= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                    touch_to_fill_bars = (b - first_touch_bar) if first_touch_bar is not None else 0
                elif dir_ == "SHORT" and c_h >= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                    touch_to_fill_bars = (b - first_touch_bar) if first_touch_bar is not None else 0
                    
                if not filled:
                    # Distal boundary invalidation check before fill
                    if dir_ == "LONG" and c_l <= sl_p:
                        outcome = "INVALIDATED_BEFORE_FILL"
                        exit_reason = "SL_BREACHED_BEFORE_FILL"
                        narrative = "Setup invalidated: price pierced distal SL before limit entry was touched."
                        invalidations_before_fill.append(s["ob_id"])
                        break
                    elif dir_ == "SHORT" and c_h >= sl_p:
                        outcome = "INVALIDATED_BEFORE_FILL"
                        exit_reason = "SL_BREACHED_BEFORE_FILL"
                        narrative = "Setup invalidated: price pierced distal SL before limit entry was touched."
                        invalidations_before_fill.append(s["ob_id"])
                        break
                    continue
                    
            # Step 3: Once Filled, Evaluate TP/SL
            if filled:
                hit_tp = (c_h >= tp_p) if dir_ == "LONG" else (c_l <= tp_p)
                hit_sl = (c_l <= sl_p) if dir_ == "LONG" else (c_h >= sl_p)
                
                if hit_tp and hit_sl:
                    outcome = "FILLED_SL"
                    exit_reason = "DUAL_TOUCH_CONSERVATIVE_SL"
                    is_ambiguous = True
                    exit_dt = c_dt
                    exit_p = sl_p
                    narrative = "Dual-touch ambiguous candle: Both TP and SL touched in same 1H candle. Conservative standard resolves to SL."
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_reason = "TP_HIT"
                    exit_dt = c_dt
                    exit_p = tp_p
                    narrative = f"Price reached fixed +0.60% TP target at {tp_p:.4f}."
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_reason = "SL_HIT"
                    exit_dt = c_dt
                    exit_p = sl_p
                    narrative = f"Price breached distal stop loss at {sl_p:.4f}."
                    break
                    
        if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
            outcome = "FILLED_TIMEOUT"
            exit_reason = "TIMEOUT"
            last_c = future_candles.iloc[max_b - 1]
            exit_dt = last_c["timestamp"]
            exit_p = float(last_c["close"])
            narrative = f"{cfg.max_holding_bars}-hour max horizon expired. Closed at market {exit_p:.4f}."
            
        if not filled:
            no_fill_setups.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "decision_dt": dec_dt,
                "reason": outcome if outcome != "NO_FILL" else "Price never touched OB zone.",
            })
            continue
            
        # PnL & Compounding
        if outcome == "FILLED_TP":
            ret_pct = tp_ret_pct
            realized_r = reward_dist / risk_dist
        elif outcome == "FILLED_SL":
            ret_pct = -1.0 * actual_sl_risk_pct
            realized_r = -1.0
        else:
            p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
            realized_r = p_diff / risk_dist
            ret_pct = realized_r * actual_sl_risk_pct
            
        global_lock_until_dt = exit_dt
        cum_realized_r += realized_r
        
        start_bal = capital_net
        notional = start_bal * leverage
        fees_usd = notional * cfg.fee_rate
        gross_pnl_usd = start_bal * (ret_pct / 100.0)
        net_pnl_usd = gross_pnl_usd - fees_usd
        capital_net = max(0.0, start_bal + net_pnl_usd)
        
        if capital_net > peak_net:
            peak_net = capital_net
        dd = (peak_net - capital_net) / peak_net * 100.0 if peak_net > 0 else 0.0
        max_dd_net = max(max_dd_net, dd)
        
        holding_hours = float(max(1, int((exit_dt - fill_dt).total_seconds() / 3600))) if (exit_dt and fill_dt) else 1.0
        
        trade = FirstTouchTradeRecord(
            trade_id=len(executed_trades) + 1,
            asset=asset,
            direction=dir_,
            setup_time=to_utc_str(dec_dt),
            setup_time_ist=to_ist_str(dec_dt),
            first_touch_time=to_utc_str(first_touch_dt),
            first_touch_time_ist=to_ist_str(first_touch_dt),
            entry_time=to_utc_str(fill_dt),
            entry_time_ist=to_ist_str(fill_dt),
            exit_time=to_utc_str(exit_dt),
            exit_time_ist=to_ist_str(exit_dt),
            touch_to_fill_bars=touch_to_fill_bars if touch_to_fill_bars is not None else 0,
            ob_high=round(s["ob_high"], 4),
            ob_low=round(s["ob_low"], 4),
            ob_width=round(s["ob_width"], 4),
            ob_width_pct=round(s["ob_width_pct"], 4),
            entry_price=round(entry_p, 4),
            sl_price=round(sl_p, 4),
            tp_price=round(tp_p, 4),
            sl_distance_pct=round(s["sl_distance_pct"], 4),
            leverage=round(leverage, 2),
            actual_sl_risk_pct=round(actual_sl_risk_pct, 2),
            tp_target_return_pct=round(tp_ret_pct, 2),
            starting_capital=start_bal,
            position_notional=notional,
            gross_pnl=gross_pnl_usd,
            fees=fees_usd,
            net_pnl=net_pnl_usd,
            ending_capital=capital_net,
            outcome=outcome,
            exit_reason=exit_reason,
            is_ambiguous=is_ambiguous,
            holding_bars=int(holding_hours),
            holding_time_hours=holding_hours,
            realized_r=round(realized_r, 4),
            cumulative_realized_r=round(cum_realized_r, 4),
            data_timeframe=cfg.data_timeframe,
            trade_narrative=narrative,
        )
        executed_trades.append(trade)
        
    tdf = pd.DataFrame([asdict(t) for t in executed_trades])
    wins = tdf[tdf["outcome"] == "FILLED_TP"] if len(tdf) > 0 else pd.DataFrame()
    losses = tdf[tdf["outcome"] == "FILLED_SL"] if len(tdf) > 0 else pd.DataFrame()
    ambiguous = tdf[tdf["is_ambiguous"] == True] if len(tdf) > 0 else pd.DataFrame()
    
    total_exec = len(executed_trades)
    wr = (len(wins) / total_exec * 100.0) if total_exec > 0 else 0.0
    exp_r = (cum_realized_r / total_exec) if total_exec > 0 else 0.0
    gain_r = float(wins["realized_r"].sum()) if len(wins) > 0 else 0.0
    loss_r = abs(float(losses["realized_r"].sum())) if len(losses) > 0 else 1.0
    pf = (gain_r / loss_r) if loss_r > 0 else 99.0
    
    max_losing_streak = 0
    cur_streak = 0
    if len(tdf) > 0:
        for outcome in tdf["outcome"]:
            if outcome == "FILLED_SL":
                cur_streak += 1
                max_losing_streak = max(max_losing_streak, cur_streak)
            else:
                cur_streak = 0
                
    asset_breakdown = {}
    for sym in syms:
        sym_trades = tdf[tdf["asset"] == sym] if len(tdf) > 0 else pd.DataFrame()
        sym_setups_count = len([s for s in all_candidate_setups if s["asset"] == sym])
        sym_wins = sym_trades[sym_trades["outcome"] == "FILLED_TP"] if len(sym_trades) > 0 else pd.DataFrame()
        sym_losses = sym_trades[sym_trades["outcome"] == "FILLED_SL"] if len(sym_trades) > 0 else pd.DataFrame()
        
        sym_n = len(sym_trades)
        sym_wr = (len(sym_wins) / sym_n * 100.0) if sym_n > 0 else 0.0
        sym_tot_r = float(sym_trades["realized_r"].sum()) if sym_n > 0 else 0.0
        sym_exp_r = (sym_tot_r / sym_n) if sym_n > 0 else 0.0
        sym_gain_r = float(sym_wins["realized_r"].sum()) if len(sym_wins) > 0 else 0.0
        sym_loss_r = abs(float(sym_losses["realized_r"].sum())) if len(sym_losses) > 0 else 1.0
        sym_pf = (sym_gain_r / sym_loss_r) if sym_loss_r > 0 else 99.0
        
        asset_breakdown[sym] = {
            "asset": sym,
            "total_setups": sym_setups_count,
            "filled_trades": sym_n,
            "wins": len(sym_wins),
            "losses": len(sym_losses),
            "win_rate_pct": round(sym_wr, 2),
            "total_realized_r": round(sym_tot_r, 2),
            "expectancy_r": round(sym_exp_r, 4),
            "profit_factor": round(sym_pf, 2),
        }
        
    return {
        "enforce_3_candle_rule": enforce_3_candle_rule,
        "config": asdict(cfg),
        "starting_capital": cfg.starting_capital,
        "ending_capital_net": capital_net,
        "total_return_pct": ((capital_net - cfg.starting_capital) / cfg.starting_capital) * 100.0,
        "total_candidate_setups": len(all_candidate_setups),
        "total_first_touch_events": len(first_touch_events),
        "total_executed_trades": total_exec,
        "total_no_fill_setups": len(no_fill_setups),
        "total_first_touch_expirations": len(first_touch_expirations),
        "total_invalidations_before_fill": len(invalidations_before_fill),
        "total_skipped_global_lock": len(skipped_global_lock),
        "wins": len(wins),
        "losses": len(losses),
        "ambiguous_trades": len(ambiguous),
        "win_rate_pct": round(wr, 2),
        "expectancy_r": round(exp_r, 4),
        "total_realized_r": round(cum_realized_r, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(max_dd_net, 2),
        "max_losing_streak": max_losing_streak,
        "asset_breakdown": asset_breakdown,
        "trades": executed_trades,
        "trades_df": tdf,
    }
