"""
Extract multi-year compounding capital growth milestones from $10.00 start in June 2024 to August 26, 2026.
"""

from pathlib import Path
import pandas as pd
import json

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

root = _find_repo_root()
base = root / "data" / "canonical" / "delta_exchange_india"
symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

candles_df = {}
all_setups_by_sym = {sym: [] for sym in symbols}
all_setups_combined = []

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
        all_setups_by_sym[sym].append(rec)
        all_setups_combined.append(rec)

all_setups_combined = sorted(all_setups_combined, key=lambda x: (x["decision_dt"], x["ob_id"]))

def run_sim_tracking(setups_list, start_cap=10.0, fee_rate=0.0008, max_leverage=100.0, max_sl_risk_pct=35.0, fixed_tp_pct=0.60):
    active_locks = {sym: None for sym in symbols}
    capital_gross = start_cap
    capital_net = start_cap
    
    trade_records = []
    
    for s in setups_list:
        asset = s["asset"]
        dec_dt = s["decision_dt"]
        dir_ = s["direction"]
        top = s["ob_high"]
        bot = s["ob_low"]
        w = top - bot
        
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
            
            # Net compounding
            start_n = capital_net
            notional = start_n * leverage
            fees = notional * fee_rate
            net_pnl = (start_n * (ret_pct / 100.0)) - fees
            capital_net = max(0.0, start_n + net_pnl)
            
            trade_records.append({
                "asset": asset,
                "exit_dt": exit_dt,
                "year": exit_dt.year,
                "outcome": outcome,
                "realized_r": realized_r,
                "capital_gross": capital_gross,
                "capital_net": capital_net,
            })
            
    tdf = pd.DataFrame(trade_records)
    
    # Yearly milestones
    milestones = {}
    for yr, ydf in tdf.groupby("year"):
        milestones[yr] = {
            "trades": len(ydf),
            "wins": len(ydf[ydf["outcome"] == "FILLED_TP"]),
            "losses": len(ydf[ydf["outcome"] == "FILLED_SL"]),
            "end_gross": ydf.iloc[-1]["capital_gross"],
            "end_net": ydf.iloc[-1]["capital_net"],
        }
        
    return {
        "final_gross": capital_gross,
        "final_net": capital_net,
        "total_trades": len(tdf),
        "total_wins": len(tdf[tdf["outcome"] == "FILLED_TP"]),
        "total_losses": len(tdf[tdf["outcome"] == "FILLED_SL"]),
        "milestones": milestones,
        "trades_df": tdf,
    }

results = {}
for sym in symbols:
    setups = sorted(all_setups_by_sym[sym], key=lambda x: (x["decision_dt"], x["ob_id"]))
    results[sym] = run_sim_tracking(setups)

results["COMBINED"] = run_sim_tracking(all_setups_combined)

output_data = {}
for name, r in results.items():
    output_data[name] = {
        "final_gross": r["final_gross"],
        "final_net": r["final_net"],
        "total_trades": r["total_trades"],
        "win_rate": r["total_wins"] / r["total_trades"] * 100,
        "milestones": r["milestones"],
    }

print(json.dumps(output_data, indent=2, default=str))
