"""
QuantEdge AI — Fixed +0.60% TP LuxAlgo Retest Research Engine.
==============================================================
Isolated research experiment reproducing the manual TradingView / LuxAlgo
Smart Money Concepts workflow on canonical 1H cryptocurrency data:
- Reuses verified QuantEdge/LuxAlgo-parity Order Block detector.
- Exact 25% zone penetration depth limit entry.
- Distal OB boundary structural stop loss.
- Fixed +-0.60% price-movement Take Profit.
- Leverage based on SL distance targeting max 35% account risk (applied cap: 100x).
- Full continuous compounding from $10.00 with 0.08% taker fees.
- Global One-Trade-at-a-Time Lock across BTCUSD, ETHUSD, SOLUSD, XRPUSD.
- Detailed Retest Latency tracking:
    * bars_ob_to_entry & hours_ob_to_entry (time from BOS confirmation to fill)
    * bars_retest_to_entry & hours_retest_to_entry (time from zone touch to fill)
- No arbitrary expiration: OB remains active until distal boundary breach.
- Conservative SL-first handling for ambiguous dual-touch candles.
"""

from dataclasses import dataclass, asdict
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
class LuxAlgoRetestConfig:
    fixed_tp_market_pct: float = 0.60    # Fixed 0.60% market price movement TP
    max_sl_account_risk_pct: float = 35.0 # Max 35% account risk at SL
    applied_leverage_cap: float = 100.0   # Applied leverage cap in research environment
    penetration_depth: float = 0.25      # Exactly 25% depth into OB
    fee_rate: float = 0.0008             # 0.08% roundtrip taker fee
    max_holding_bars: int = 72           # 72 hours max holding horizon
    starting_capital: float = 10.0       # Initial portfolio capital
    data_timeframe: str = "1h"           # Canonical 1H data

@dataclass
class LuxAlgoTradeRecord:
    trade_id: int
    asset: str
    direction: str
    ob_formation_time: str               # ISO8601 UTC
    bos_time: str                        # ISO8601 UTC (BOS confirmation candle close)
    entry_time: str                      # ISO8601 UTC
    exit_time: str                       # ISO8601 UTC
    ob_formation_time_ist: str
    bos_time_ist: str
    entry_time_ist: str
    exit_time_ist: str
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_pct: float
    entry_price: float
    sl_price: float
    tp_price: float
    sl_distance_pct: float
    theoretical_leverage: float
    applied_leverage: float
    planned_tp_market_pct: float
    planned_sl_account_pct: float
    planned_tp_account_pct: float
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
    bars_ob_to_entry: int
    hours_ob_to_entry: float
    bars_retest_to_entry: int
    hours_retest_to_entry: float
    realized_r: float
    cumulative_realized_r: float
    data_timeframe: str
    trade_narrative: str

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

def run_luxalgo_retest_backtest(
    data_base_dir: Optional[Path] = None,
    config: Optional[LuxAlgoRetestConfig] = None,
    symbols: Optional[List[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Executes the LuxAlgo Retest Research Backtest with deterministic trade lifecycle.
    """
    cfg = config or LuxAlgoRetestConfig()
    syms = symbols or ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    root = data_base_dir or (_find_repo_root() / "data" / "canonical" / "delta_exchange_india")
    
    candles_df_dict: Dict[str, pd.DataFrame] = {}
    all_candidate_setups: List[Dict[str, Any]] = []
    
    total_obs_detected = 0
    
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
            total_obs_detected += 1
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
            # 25% Penetration Depth Formula
            if dir_ == "LONG":
                entry_p = top - cfg.penetration_depth * w
                sl_p = bot
                risk_dist = entry_p - sl_p
                tp_p = entry_p * (1.0 + cfg.fixed_tp_market_pct / 100.0)
                reward_dist = tp_p - entry_p
            else:
                entry_p = bot + cfg.penetration_depth * w
                sl_p = top
                risk_dist = sl_p - entry_p
                tp_p = entry_p * (1.0 - cfg.fixed_tp_market_pct / 100.0)
                reward_dist = entry_p - tp_p
                
            if risk_dist <= 1e-6:
                continue
                
            sl_dist_dec = risk_dist / entry_p
            sl_dist_pct = sl_dist_dec * 100.0
            
            # Theoretical vs Applied Leverage
            theoretical_lev = cfg.max_sl_account_risk_pct / sl_dist_pct
            applied_lev = min(cfg.applied_leverage_cap, theoretical_lev)
            
            # Account Risk and Return
            planned_sl_account_pct = applied_lev * sl_dist_pct
            planned_tp_account_pct = cfg.fixed_tp_market_pct * applied_lev
            
            all_candidate_setups.append({
                "ob_id": s.setup_id,
                "asset": sym,
                "direction": dir_,
                "decision_bar": dec_bar,
                "decision_dt": dec_ts,
                "formation_dt": formation_ts,
                "entry_price": entry_p,
                "sl_price": sl_p,
                "tp_price": tp_p,
                "ob_high": top,
                "ob_low": bot,
                "ob_width": w,
                "ob_width_pct": (w / entry_p) * 100.0,
                "sl_distance_pct": sl_dist_pct,
                "theoretical_leverage": theoretical_lev,
                "applied_leverage": applied_lev,
                "planned_sl_account_pct": planned_sl_account_pct,
                "planned_tp_account_pct": planned_tp_account_pct,
                "risk_dist": risk_dist,
                "reward_dist": reward_dist,
            })
            
    # Sort all setups chronologically across all assets
    all_candidate_setups = sorted(all_candidate_setups, key=lambda x: (x["decision_dt"], x["asset"], x["ob_id"]))
    
    global_lock_until_dt: Optional[datetime] = None
    capital_net = cfg.starting_capital
    peak_net = cfg.starting_capital
    max_dd_net = 0.0
    
    executed_trades: List[LuxAlgoTradeRecord] = []
    skipped_global_lock = []
    no_fill_setups = []
    touches_without_25pct = []
    invalidations_before_fill = []
    
    cum_realized_r = 0.0
    
    for s in all_candidate_setups:
        asset = s["asset"]
        dec_dt = s["decision_dt"]
        formation_dt = s["formation_dt"]
        dir_ = s["direction"]
        top = s["ob_high"]
        bot = s["ob_low"]
        entry_p = s["entry_price"]
        sl_p = s["sl_price"]
        tp_p = s["tp_price"]
        applied_lev = s["applied_leverage"]
        theoretical_lev = s["theoretical_leverage"]
        planned_sl_account_pct = s["planned_sl_account_pct"]
        planned_tp_account_pct = s["planned_tp_account_pct"]
        risk_dist = s["risk_dist"]
        reward_dist = s["reward_dist"]
        
        # Check Global 1-Trade Lock
        if global_lock_until_dt is not None and dec_dt < global_lock_until_dt:
            skipped_global_lock.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "decision_dt": dec_dt,
                "locked_until": global_lock_until_dt,
            })
            continue
            
        c_df = candles_df_dict[asset]
        # Zero lookahead: Future candles start strictly AFTER confirmation candle close
        future_candles = c_df[c_df["timestamp"] > dec_dt].reset_index(drop=True)
        
        first_touched = False
        first_touch_bar = None
        first_touch_dt = None
        
        filled = False
        fill_bar = None
        fill_dt = None
        
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
            
            # Step 1: Detect Initial Zone Touch
            is_zone_overlap = (c_h >= bot and c_l <= top)
            if not first_touched and is_zone_overlap:
                first_touched = True
                first_touch_bar = b
                first_touch_dt = c_dt
                
            # Step 2: Check 25% Penetration Fill
            if not filled:
                if dir_ == "LONG" and c_l <= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                elif dir_ == "SHORT" and c_h >= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                    
                if not filled:
                    # Invalidation check: distal boundary breached before 25% fill
                    if dir_ == "LONG" and c_l <= sl_p:
                        outcome = "INVALIDATED_BEFORE_FILL"
                        exit_reason = "DISTAL_SL_BREACHED_BEFORE_FILL"
                        narrative = "Setup cancelled: price breached distal SL before 25% entry was filled."
                        invalidations_before_fill.append(s["ob_id"])
                        break
                    elif dir_ == "SHORT" and c_h >= sl_p:
                        outcome = "INVALIDATED_BEFORE_FILL"
                        exit_reason = "DISTAL_SL_BREACHED_BEFORE_FILL"
                        narrative = "Setup cancelled: price breached distal SL before 25% entry was filled."
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
                    narrative = "Dual-touch candle: Both TP and SL touched in same 1H candle. Conservative SL-first execution applied."
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
            if first_touched:
                touches_without_25pct.append(s["ob_id"])
            no_fill_setups.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "decision_dt": dec_dt,
                "reason": outcome if outcome != "NO_FILL" else "Price never reached 25% penetration depth.",
            })
            continue
            
        # PnL & Compounding
        if outcome == "FILLED_TP":
            ret_pct = planned_tp_account_pct
            realized_r = reward_dist / risk_dist
        elif outcome == "FILLED_SL":
            ret_pct = -1.0 * planned_sl_account_pct
            realized_r = -1.0
        else:
            p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
            realized_r = p_diff / risk_dist
            ret_pct = realized_r * planned_sl_account_pct
            
        global_lock_until_dt = exit_dt
        cum_realized_r += realized_r
        
        start_bal = capital_net
        notional = start_bal * applied_lev
        fees_usd = notional * cfg.fee_rate
        gross_pnl_usd = start_bal * (ret_pct / 100.0)
        net_pnl_usd = gross_pnl_usd - fees_usd
        capital_net = max(0.0, start_bal + net_pnl_usd)
        
        if capital_net > peak_net:
            peak_net = capital_net
        dd = (peak_net - capital_net) / peak_net * 100.0 if peak_net > 0 else 0.0
        max_dd_net = max(max_dd_net, dd)
        
        holding_hours = float(max(1, int((exit_dt - fill_dt).total_seconds() / 3600))) if (exit_dt and fill_dt) else 1.0
        bars_ob_to_entry = fill_bar + 1 if fill_bar is not None else 1
        hours_ob_to_entry = float(bars_ob_to_entry)
        
        bars_retest_to_entry = (fill_bar - first_touch_bar) if (fill_bar is not None and first_touch_bar is not None) else 0
        hours_retest_to_entry = float(bars_retest_to_entry)
        
        trade = LuxAlgoTradeRecord(
            trade_id=len(executed_trades) + 1,
            asset=asset,
            direction=dir_,
            ob_formation_time=to_utc_str(formation_dt),
            bos_time=to_utc_str(dec_dt),
            entry_time=to_utc_str(fill_dt),
            exit_time=to_utc_str(exit_dt),
            ob_formation_time_ist=to_ist_str(formation_dt),
            bos_time_ist=to_ist_str(dec_dt),
            entry_time_ist=to_ist_str(fill_dt),
            exit_time_ist=to_ist_str(exit_dt),
            ob_high=round(top, 4),
            ob_low=round(bot, 4),
            ob_width=round(w, 4),
            ob_width_pct=round(s["ob_width_pct"], 4),
            entry_price=round(entry_p, 4),
            sl_price=round(sl_p, 4),
            tp_price=round(tp_p, 4),
            sl_distance_pct=round(s["sl_distance_pct"], 4),
            theoretical_leverage=round(theoretical_lev, 2),
            applied_leverage=round(applied_lev, 2),
            planned_tp_market_pct=round(cfg.fixed_tp_market_pct, 2),
            planned_sl_account_pct=round(planned_sl_account_pct, 2),
            planned_tp_account_pct=round(planned_tp_account_pct, 2),
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
            bars_ob_to_entry=bars_ob_to_entry,
            hours_ob_to_entry=hours_ob_to_entry,
            bars_retest_to_entry=bars_retest_to_entry,
            hours_retest_to_entry=hours_retest_to_entry,
            realized_r=round(realized_r, 4),
            cumulative_realized_r=round(cum_realized_r, 4),
            data_timeframe=cfg.data_timeframe,
            trade_narrative=narrative,
        )
        executed_trades.append(trade)
        
    tdf = pd.DataFrame([asdict(t) for t in executed_trades])
    wins = tdf[tdf["outcome"] == "FILLED_TP"] if len(tdf) > 0 else pd.DataFrame()
    losses = tdf[tdf["outcome"] == "FILLED_SL"] if len(tdf) > 0 else pd.DataFrame()
    timeouts = tdf[tdf["outcome"] == "FILLED_TIMEOUT"] if len(tdf) > 0 else pd.DataFrame()
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
                
    holding_series = tdf["holding_time_hours"] if len(tdf) > 0 else pd.Series([0.0])
    latency_series = tdf["hours_ob_to_entry"] if len(tdf) > 0 else pd.Series([0.0])
    
    # Asset breakdown
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
        
    # Retest Latency Segmentation (Section 20)
    latency_breakdown = {}
    if len(tdf) > 0:
        tdf_copy = tdf.copy()
        bins = [0, 1, 3, 6, 12, 24, 10000]
        labels = ["A. Immediate (1h)", "B. 2-3h Retest", "C. 4-6h Retest", "D. 7-12h Retest", "E. 13-24h Retest", "F. >24h Retest"]
        tdf_copy["latency_tier"] = pd.cut(tdf_copy["hours_ob_to_entry"], bins=bins, labels=labels, right=True)
        
        for tier_name, grp in tdf_copy.groupby("latency_tier", observed=True):
            g_n = len(grp)
            g_w = len(grp[grp["outcome"] == "FILLED_TP"])
            g_l = len(grp[grp["outcome"] == "FILLED_SL"])
            g_wr = (g_w / g_n * 100.0) if g_n > 0 else 0.0
            g_r = float(grp["realized_r"].sum()) if g_n > 0 else 0.0
            g_exp = (g_r / g_n) if g_n > 0 else 0.0
            g_gain = float(grp[grp["outcome"] == "FILLED_TP"]["realized_r"].sum()) if g_w > 0 else 0.0
            g_loss = abs(float(grp[grp["outcome"] == "FILLED_SL"]["realized_r"].sum())) if g_l > 0 else 1.0
            g_pf = (g_gain / g_loss) if g_loss > 0 else 99.0
            
            latency_breakdown[str(tier_name)] = {
                "trades": g_n,
                "wins": g_w,
                "losses": g_l,
                "win_rate_pct": round(g_wr, 2),
                "total_realized_r": round(g_r, 2),
                "expectancy_r": round(g_exp, 4),
                "profit_factor": round(g_pf, 2),
            }
            
    return {
        "config": asdict(cfg),
        "starting_capital": cfg.starting_capital,
        "ending_capital_net": capital_net,
        "total_return_pct": ((capital_net - cfg.starting_capital) / cfg.starting_capital) * 100.0,
        "total_obs_detected": total_obs_detected,
        "total_candidate_setups": len(all_candidate_setups),
        "total_executed_trades": total_exec,
        "total_no_fill_setups": len(no_fill_setups),
        "total_touches_without_25pct": len(touches_without_25pct),
        "total_invalidations_before_fill": len(invalidations_before_fill),
        "total_skipped_global_lock": len(skipped_global_lock),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "ambiguous_trades": len(ambiguous),
        "win_rate_pct": round(wr, 2),
        "expectancy_r": round(exp_r, 4),
        "total_realized_r": round(cum_realized_r, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(max_dd_net, 2),
        "max_losing_streak": max_losing_streak,
        "holding_time_stats": {
            "average_hours": round(float(holding_series.mean()), 2),
            "median_hours": round(float(holding_series.median()), 2),
            "min_hours": round(float(holding_series.min()), 2),
            "max_hours": round(float(holding_series.max()), 2),
        },
        "latency_stats": {
            "average_hours_to_fill": round(float(latency_series.mean()), 2),
            "median_hours_to_fill": round(float(latency_series.median()), 2),
        },
        "asset_breakdown": asset_breakdown,
        "latency_breakdown": latency_breakdown,
        "trades": executed_trades,
        "trades_df": tdf,
    }
