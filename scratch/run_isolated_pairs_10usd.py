"""
Isolated Pair Backtest: $10.00 dedicated capital per pair for August 2026.
Compares BTCUSD, ETHUSD, SOLUSD, and XRPUSD performance.
"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import json
import csv

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

root = _find_repo_root()
base = root / "data" / "canonical" / "delta_exchange_india"
docs_ai_dir = root / "docs" / "ai"
symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

def to_ist_str(dt):
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_dt = dt.astimezone(ist_tz)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")

pair_results = {}
all_isolated_trades = []

for sym in symbols:
    candles = load_canonical_full_history(base, sym)
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
    
    ctx = build_smc_context(candles)
    setups, _ = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)
    
    sym_setups = []
    for s in setups:
        dec_ts = candles[s.decision_bar].timestamp
        if dec_ts >= datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc):
            sym_setups.append({
                "ob_id": s.setup_id,
                "asset": sym,
                "direction": s.direction,
                "decision_time": str(dec_ts),
                "decision_dt": dec_ts,
                "creation_time": s.creation_time,
                "entry_price": float(s.entry_price),
                "sl_price": float(s.sl_price),
                "tp_price": float(s.tp_price),
                "ob_high": float(s.ob_high),
                "ob_low": float(s.ob_low),
                "decision_bar": s.decision_bar,
            })
            
    sym_setups = sorted(sym_setups, key=lambda x: (x["decision_dt"], x["ob_id"]))
    
    # Run isolated simulation with $10.00 initial capital
    capital_gross = 10.0
    capital_net = 10.0
    peak_gross = 10.0
    peak_net = 10.0
    max_dd_gross = 0.0
    max_dd_net = 0.0
    
    executed_trades = []
    active_lock_until = None
    fee_rate = 0.0008
    max_leverage = 100.0
    max_sl_risk_pct = 35.0
    
    for s in sym_setups:
        dec_dt = s["decision_dt"]
        dir_ = s["direction"]
        top = s["ob_high"]
        bot = s["ob_low"]
        w = top - bot
        
        # 25% penetration limit entry
        if dir_ == "LONG":
            entry_p = top - 0.25 * w
            sl_p = bot
            risk_dist = entry_p - sl_p
            tp_p = entry_p * 1.008
            reward_dist = tp_p - entry_p
        else:
            entry_p = bot + 0.25 * w
            sl_p = top
            risk_dist = sl_p - entry_p
            tp_p = entry_p * 0.992
            reward_dist = entry_p - tp_p
            
        sl_dist_dec = risk_dist / entry_p
        sl_dist_pct = sl_dist_dec * 100.0
        
        # Capped leverage & actual SL risk
        uncapped_lev = max_sl_risk_pct / sl_dist_pct
        leverage = min(max_leverage, uncapped_lev)
        actual_sl_risk_pct = leverage * sl_dist_pct
        actual_sl_ret_pct = -1.0 * actual_sl_risk_pct
        tp_ret_pct = 0.80 * leverage
        
        # Check active lock for this pair
        if active_lock_until is not None and dec_dt < active_lock_until:
            continue
            
        future_candles = c_df[c_df["timestamp"] >= dec_dt].reset_index(drop=True)
        
        filled = False
        fill_bar = None
        fill_dt = None
        outcome = "NO_FILL"
        exit_dt = None
        exit_p = None
        narrative = ""
        loss_mech = "N/A"
        
        max_b = min(72, len(future_candles))
        for b in range(max_b):
            c = future_candles.iloc[b]
            c_dt = c["timestamp"]
            c_o, c_h, c_l, c_c = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            
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
                    if dir_ == "LONG" and c_l <= sl_p:
                        narrative = "Invalidated before entry"
                        break
                    elif dir_ == "SHORT" and c_h >= sl_p:
                        narrative = "Invalidated before entry"
                        break
                    continue
                    
            if filled:
                hit_tp = (c_h >= tp_p) if dir_ == "LONG" else (c_l <= tp_p)
                hit_sl = (c_l <= sl_p) if dir_ == "LONG" else (c_h >= sl_p)
                
                if hit_tp and hit_sl:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    loss_mech = "DUAL_TOUCH_AMBIGUITY"
                    narrative = "Dual-touch ambiguous candle. SL conservative tie-break."
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_dt = c_dt
                    exit_p = tp_p
                    narrative = f"Price expanded to hit +0.80% TP at {tp_p:.2f}."
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    if b == fill_bar:
                        loss_mech = "INSTANT_BLOWTHROUGH"
                        narrative = f"Instant penetration blowthrough. Breached distal SL at {sl_p:.2f}."
                    else:
                        loss_mech = "CONSOLIDATION_REVERSAL"
                        narrative = f"Consolidated for {b - fill_bar} bars, then reversed to breach distal SL at {sl_p:.2f}."
                    break
                    
        if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
            outcome = "FILLED_TIMEOUT"
            last_c = future_candles.iloc[max_b - 1]
            exit_dt = last_c["timestamp"]
            exit_p = float(last_c["close"])
            narrative = f"72-hour horizon expired. Closed at {exit_p:.2f}."
            
        if filled:
            if outcome == "FILLED_TP":
                ret_pct = tp_ret_pct
                realized_r = reward_dist / risk_dist
            elif outcome == "FILLED_SL":
                ret_pct = actual_sl_ret_pct
                realized_r = -1.0
            else:
                p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
                realized_r = p_diff / risk_dist
                ret_pct = realized_r * actual_sl_risk_pct
                
            active_lock_until = exit_dt
            
            # Gross compounding
            start_g = capital_gross
            gross_pnl = start_g * (ret_pct / 100.0)
            capital_gross = max(0.0, start_g + gross_pnl)
            if capital_gross > peak_gross:
                peak_gross = capital_gross
            dd_g = (peak_gross - capital_gross) / peak_gross * 100.0 if peak_gross > 0 else 0.0
            max_dd_gross = max(max_dd_gross, dd_g)
            
            # Net compounding
            start_n = capital_net
            notional = start_n * leverage
            fees = notional * fee_rate
            net_pnl = (start_n * (ret_pct / 100.0)) - fees
            capital_net = max(0.0, start_n + net_pnl)
            if capital_net > peak_net:
                peak_net = capital_net
            dd_n = (peak_net - capital_net) / peak_net * 100.0 if peak_net > 0 else 0.0
            max_dd_net = max(max_dd_net, dd_n)
            
            trade_rec = {
                "trade_num": len(executed_trades) + 1,
                "asset": sym,
                "direction": dir_,
                "ob_zone": f"[{bot:.2f}, {top:.2f}]",
                "ob_low": bot,
                "ob_high": top,
                "decision_time_ist": to_ist_str(dec_dt),
                "entry_time_ist": to_ist_str(fill_dt),
                "exit_time_ist": to_ist_str(exit_dt),
                "entry_price": round(entry_p, 4),
                "sl_price": round(sl_p, 4),
                "tp_price": round(tp_p, 4),
                "sl_dist_pct": round(sl_dist_pct, 4),
                "leverage": round(leverage, 2),
                "actual_sl_risk_pct": round(actual_sl_risk_pct, 2),
                "tp_ret_pct": round(tp_ret_pct, 2),
                "outcome": outcome,
                "realized_r": round(realized_r, 4),
                "loss_mechanism": loss_mech,
                "starting_capital_gross": round(start_g, 4),
                "gross_pnl_usd": round(gross_pnl, 4),
                "ending_capital_gross": round(capital_gross, 4),
                "starting_capital_net": round(start_n, 4),
                "fees_usd": round(fees, 4),
                "net_pnl_usd": round(net_pnl, 4),
                "ending_capital_net": round(capital_net, 4),
                "narrative": narrative,
            }
            executed_trades.append(trade_rec)
            all_isolated_trades.append(trade_rec)
            
    wins = [t for t in executed_trades if t["outcome"] == "FILLED_TP"]
    losses = [t for t in executed_trades if t["outcome"] == "FILLED_SL"]
    total_exec = len(executed_trades)
    wr = (len(wins) / total_exec * 100.0) if total_exec > 0 else 0.0
    tot_r = sum(t["realized_r"] for t in executed_trades)
    exp_r = tot_r / total_exec if total_exec > 0 else 0.0
    gain_r = sum(t["realized_r"] for t in wins)
    loss_r = abs(sum(t["realized_r"] for t in losses))
    pf = (gain_r / loss_r) if loss_r > 0 else 99.0
    
    pair_results[sym] = {
        "asset": sym,
        "starting_capital": 10.0,
        "ending_capital_gross": round(capital_gross, 4),
        "gross_return_pct": round((capital_gross - 10.0) / 10.0 * 100.0, 2),
        "ending_capital_net": round(capital_net, 4),
        "net_return_pct": round((capital_net - 10.0) / 10.0 * 100.0, 2),
        "total_setups": len(sym_setups),
        "executed_trades": total_exec,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(wr, 2),
        "expectancy_r": round(exp_r, 4),
        "total_realized_r": round(tot_r, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_gross_pct": round(max_dd_gross, 2),
        "max_drawdown_net_pct": round(max_dd_net, 2),
        "trades": executed_trades,
    }

# Write summary and trades CSV
summary_df = pd.DataFrame([
    {
        "Asset": r["asset"],
        "Start Capital ($)": r["starting_capital"],
        "Gross End Capital ($)": r["ending_capital_gross"],
        "Gross Return (%)": f"{r['gross_return_pct']:+.2f}%",
        "Net End Capital ($)": r["ending_capital_net"],
        "Net Return (%)": f"{r['net_return_pct']:+.2f}%",
        "Executed Trades": r["executed_trades"],
        "Wins": r["wins"],
        "Losses": r["losses"],
        "Win Rate (%)": f"{r['win_rate_pct']:.2f}%",
        "Expectancy (R)": f"{r['expectancy_r']:+.4f}R",
        "Total Realized R": f"{r['total_realized_r']:+.2f}R",
        "Profit Factor": r["profit_factor"],
        "Max Drawdown (Gross %)": f"{r['max_drawdown_gross_pct']:.2f}%",
        "Max Drawdown (Net %)": f"{r['max_drawdown_net_pct']:.2f}%",
    }
    for r in sorted(pair_results.values(), key=lambda x: x["ending_capital_net"], reverse=True)
])

print("\n" + "=" * 110)
print("ISOLATED PAIR PERFORMANCE COMPARISON ($10.00 DEDICATED PER PAIR — AUGUST 1–26, 2026)")
print("=" * 110)
print(summary_df.to_string(index=False))

# Write CSVs
csv_summary_path = docs_ai_dir / "august_2026_isolated_pairs_summary.csv"
summary_df.to_csv(csv_summary_path, index=False)
print(f"\nWritten: {csv_summary_path}")

csv_all_trades_path = docs_ai_dir / "august_2026_isolated_pairs_all_trades.csv"
with open(csv_all_trades_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(all_isolated_trades[0].keys()))
    writer.writeheader()
    writer.writerows(all_isolated_trades)
print(f"Written: {csv_all_trades_path}")
