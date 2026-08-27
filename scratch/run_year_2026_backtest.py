"""
Year 2026 Full-Year Backtest (Jan 1, 2026 – Aug 26, 2026):
Fixed 0.60% TP, Max 100x Leverage, Max 35% SL, Starting Capital $10.00.
Evaluates Isolated Pairs and Unified Multi-Pair Portfolio.
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

candles_df = {}
all_2026_setups_by_sym = {sym: [] for sym in symbols}
all_2026_setups_combined = []

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
    candles_df[sym] = c_df
    
    ctx = build_smc_context(candles)
    setups, _ = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)
    
    for s in setups:
        dec_ts = candles[s.decision_bar].timestamp
        if dec_ts >= datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc):
            rec = {
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
            }
            all_2026_setups_by_sym[sym].append(rec)
            all_2026_setups_combined.append(rec)

all_2026_setups_combined = sorted(all_2026_setups_combined, key=lambda x: (x["decision_dt"], x["ob_id"]))

def run_simulation(setups_list, start_cap=10.0, fee_rate=0.0008, max_leverage=100.0, max_sl_risk_pct=35.0, fixed_tp_pct=0.60):
    active_locks = {sym: None for sym in symbols}
    capital_gross = start_cap
    capital_net = start_cap
    peak_gross = start_cap
    peak_net = start_cap
    max_dd_gross = 0.0
    max_dd_net = 0.0
    
    executed_trades = []
    
    for s in setups_list:
        asset = s["asset"]
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
            tp_p = entry_p * (1.0 + fixed_tp_pct / 100.0)
            reward_dist = tp_p - entry_p
        else:
            entry_p = bot + 0.25 * w
            sl_p = top
            risk_dist = sl_p - entry_p
            tp_p = entry_p * (1.0 - fixed_tp_pct / 100.0)
            reward_dist = entry_p - tp_p
            
        if risk_dist <= 1e-6:
            continue
            
        sl_dist_dec = risk_dist / entry_p
        sl_dist_pct = sl_dist_dec * 100.0
        
        uncapped_lev = max_sl_risk_pct / sl_dist_pct
        leverage = min(max_leverage, uncapped_lev)
        actual_sl_risk_pct = leverage * sl_dist_pct
        actual_sl_ret_pct = -1.0 * actual_sl_risk_pct
        tp_ret_pct = fixed_tp_pct * leverage
        
        # Check active lock
        if active_locks[asset] is not None and dec_dt < active_locks[asset]:
            continue
            
        c_df = candles_df[asset]
        future_candles = c_df[c_df["timestamp"] >= dec_dt].reset_index(drop=True)
        
        filled = False
        fill_bar = None
        fill_dt = None
        outcome = "NO_FILL"
        exit_dt = None
        exit_p = None
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
                        break
                    elif dir_ == "SHORT" and c_h >= sl_p:
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
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_dt = c_dt
                    exit_p = tp_p
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    loss_mech = "INSTANT_BLOWTHROUGH" if b == fill_bar else "CONSOLIDATION_REVERSAL"
                    break
                    
        if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
            outcome = "FILLED_TIMEOUT"
            last_c = future_candles.iloc[max_b - 1]
            exit_dt = last_c["timestamp"]
            exit_p = float(last_c["close"])
            
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
                
            active_locks[asset] = exit_dt
            
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
                "asset": asset,
                "direction": dir_,
                "ob_zone": f"[{bot:.2f}, {top:.2f}]",
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
            }
            executed_trades.append(trade_rec)
            
    wins = [t for t in executed_trades if t["outcome"] == "FILLED_TP"]
    losses = [t for t in executed_trades if t["outcome"] == "FILLED_SL"]
    total_exec = len(executed_trades)
    wr = (len(wins) / total_exec * 100.0) if total_exec > 0 else 0.0
    tot_r = sum(t["realized_r"] for t in executed_trades)
    exp_r = tot_r / total_exec if total_exec > 0 else 0.0
    gain_r = sum(t["realized_r"] for t in wins)
    loss_r = abs(sum(t["realized_r"] for t in losses))
    pf = (gain_r / loss_r) if loss_r > 0 else 99.0
    
    return {
        "starting_capital": start_cap,
        "ending_capital_gross": round(capital_gross, 4),
        "gross_return_pct": round((capital_gross - start_cap) / start_cap * 100.0, 2),
        "ending_capital_net": round(capital_net, 4),
        "net_return_pct": round((capital_net - start_cap) / start_cap * 100.0, 2),
        "total_setups": len(setups_list),
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

# 1. Run Isolated Pairs (2026 Full Year)
isolated_2026_results = {}
for sym in symbols:
    setups = sorted(all_2026_setups_by_sym[sym], key=lambda x: (x["decision_dt"], x["ob_id"]))
    res = run_simulation(setups, start_cap=10.0, fixed_tp_pct=0.60)
    isolated_2026_results[sym] = res

# 2. Run Combined Multi-Pair Portfolio (2026 Full Year)
combined_2026_res = run_simulation(all_2026_setups_combined, start_cap=10.0, fixed_tp_pct=0.60)

# Summary DataFrame
summary_rows = []
for sym, r in isolated_2026_results.items():
    summary_rows.append({
        "Configuration": f"Isolated {sym}",
        "Start Capital ($)": r["starting_capital"],
        "Gross End ($)": r["ending_capital_gross"],
        "Gross Return (%)": f"{r['gross_return_pct']:+.2f}%",
        "Net End ($)": r["ending_capital_net"],
        "Net Return (%)": f"{r['net_return_pct']:+.2f}%",
        "Trades": r["executed_trades"],
        "Wins": r["wins"],
        "Losses": r["losses"],
        "Win Rate (%)": f"{r['win_rate_pct']:.2f}%",
        "Expectancy (R)": f"{r['expectancy_r']:+.4f}R",
        "Total Realized R": f"{r['total_realized_r']:+.2f}R",
        "Profit Factor": r["profit_factor"],
        "Max DD (Net %)": f"{r['max_drawdown_net_pct']:.2f}%",
    })

summary_rows.append({
    "Configuration": "Unified Multi-Pair Portfolio",
    "Start Capital ($)": combined_2026_res["starting_capital"],
    "Gross End ($)": combined_2026_res["ending_capital_gross"],
    "Gross Return (%)": f"{combined_2026_res['gross_return_pct']:+.2f}%",
    "Net End ($)": combined_2026_res["ending_capital_net"],
    "Net Return (%)": f"{combined_2026_res['net_return_pct']:+.2f}%",
    "Trades": combined_2026_res["executed_trades"],
    "Wins": combined_2026_res["wins"],
    "Losses": combined_2026_res["losses"],
    "Win Rate (%)": f"{combined_2026_res['win_rate_pct']:.2f}%",
    "Expectancy (R)": f"{combined_2026_res['expectancy_r']:+.4f}R",
    "Total Realized R": f"{combined_2026_res['total_realized_r']:+.2f}R",
    "Profit Factor": combined_2026_res["profit_factor"],
    "Max DD (Net %)": f"{combined_2026_res['max_drawdown_net_pct']:.2f}%",
})

sum_df = pd.DataFrame(summary_rows)

print("\n" + "=" * 110)
print("YEAR 2026 FULL-YEAR BACKTEST SUMMARY (FIXED 0.60% TP | MAX 100x LEVERAGE | $10.00 BASE)")
print("=" * 110)
print(sum_df.to_string(index=False))

# Monthly Breakdown for BTCUSD & Combined Portfolio
trades_btc = pd.DataFrame(isolated_2026_results["BTCUSD"]["trades"])
trades_btc["month"] = trades_btc["entry_time_ist"].str.slice(0, 7)
monthly_btc = []
for m, mdf in trades_btc.groupby("month"):
    mw = mdf[mdf["outcome"] == "FILLED_TP"]
    ml = mdf[mdf["outcome"] == "FILLED_SL"]
    monthly_btc.append({
        "Month": m,
        "Trades": len(mdf),
        "Wins": len(mw),
        "Losses": len(ml),
        "Win Rate (%)": f"{len(mw)/len(mdf)*100:.2f}%",
        "Total Realized R": f"{mdf['realized_r'].sum():+.2f}R",
        "Net Ending Balance ($)": f"${mdf.iloc[-1]['ending_capital_net']:.2f}",
    })
print("\n" + "=" * 80)
print("BTCUSD 2026 MONTHLY BREAKDOWN")
print("=" * 80)
print(pd.DataFrame(monthly_btc).to_string(index=False))

# Save CSVs
csv_summary_path = docs_ai_dir / "year_2026_fixed_06_tp_summary.csv"
sum_df.to_csv(csv_summary_path, index=False)
print(f"\nWritten: {csv_summary_path}")

csv_btc_trades_path = docs_ai_dir / "year_2026_fixed_06_tp_btc_trades.csv"
trades_btc.to_csv(csv_btc_trades_path, index=False)
print(f"Written: {csv_btc_trades_path}")
