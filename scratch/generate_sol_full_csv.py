"""
Generate full, complete CSV ledger for SOLUSD (2024–2026) under Fixed 0.60% TP / Max 100x Leverage / Max 35% SL.
Includes exact minute and second timestamps (YYYY-MM-DD HH:MM:SS IST).
Starting capital: $10.00.
Deducts 0.08% exchange taker fees per trade.
"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import csv

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

root = _find_repo_root()
base = root / "data" / "canonical" / "delta_exchange_india"
docs_ai_dir = root / "docs" / "ai"
docs_ai_dir.mkdir(parents=True, exist_ok=True)

sym = "SOLUSD"
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

def to_ist_str_exact(dt):
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_dt = dt.astimezone(ist_tz)
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")

start_capital = 10.0
capital_net = start_capital
active_lock_until = None
fee_rate = 0.0008  # 0.08% roundtrip fee (0.04% entry + 0.04% exit)
max_leverage = 100.0
max_sl_risk_pct = 35.0
fixed_tp_pct = 0.60

executed_trades = []
cum_realized_r = 0.0

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
    
    # Capped leverage & actual SL risk
    uncapped_lev = max_sl_risk_pct / sl_dist_pct
    leverage = min(max_leverage, uncapped_lev)
    actual_sl_risk_pct = leverage * sl_dist_pct
    actual_sl_ret_pct = -1.0 * actual_sl_risk_pct
    tp_ret_pct = fixed_tp_pct * leverage
    
    # Check active lock
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
                    narrative = "Invalidated (price breached distal SL before limit fill)."
                    break
                elif dir_ == "SHORT" and c_h >= sl_p:
                    narrative = "Invalidated (price breached distal SL before limit fill)."
                    break
                continue
                
        if filled:
            hit_tp = (c_h >= tp_p) if dir_ == "LONG" else (c_l <= tp_p)
            hit_sl = (c_l <= sl_p) if dir_ == "LONG" else (c_h >= sl_p)
            
            if hit_tp and hit_sl:
                outcome = "FILLED_SL"
                exit_dt = c_dt
                exit_p = sl_p
                narrative = "Dual-touch ambiguous 1H candle. Both TP and SL touched; resolved to SL conservatively."
                break
            elif hit_tp and not hit_sl:
                outcome = "FILLED_TP"
                exit_dt = c_dt
                exit_p = tp_p
                narrative = f"Price expanded in trade direction to hit fixed +0.60% TP at {tp_p:.4f}."
                break
            elif hit_sl and not hit_tp:
                outcome = "FILLED_SL"
                exit_dt = c_dt
                exit_p = sl_p
                if b == fill_bar:
                    narrative = f"Instant penetration blowthrough. Filled limit at {entry_p:.4f} and pierced distal SL at {sl_p:.4f} in same candle."
                else:
                    narrative = f"Filled at {entry_p:.4f}, consolidated for {b - fill_bar} bars, then reversed to breach distal SL at {sl_p:.4f}."
                break
                
    if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
        outcome = "FILLED_TIMEOUT"
        last_c = future_candles.iloc[max_b - 1]
        exit_dt = last_c["timestamp"]
        exit_p = float(last_c["close"])
        narrative = f"72-hour holding horizon expired. Closed at market {exit_p:.4f}."
        
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
        cum_realized_r += realized_r
        
        # Net compounding calculation with fee deduction
        start_bal = capital_net
        notional = start_bal * leverage
        fees_usd = notional * fee_rate
        gross_pnl_usd = start_bal * (ret_pct / 100.0)
        net_pnl_usd = gross_pnl_usd - fees_usd
        capital_net = max(0.0, start_bal + net_pnl_usd)
        
        executed_trades.append({
            "Trade_Number": len(executed_trades) + 1,
            "Asset": sym,
            "Direction": dir_,
            "OB_Zone_Low": round(bot, 4),
            "OB_Zone_High": round(top, 4),
            "OB_Width": round(w, 4),
            "Setup_Time_IST": to_ist_str_exact(dec_dt),
            "Entry_Time_IST": to_ist_str_exact(fill_dt),
            "Exit_Time_IST": to_ist_str_exact(exit_dt),
            "Entry_Price": round(entry_p, 4),
            "Stop_Loss_Price": round(sl_p, 4),
            "Take_Profit_Price": round(tp_p, 4),
            "Stop_Loss_Distance_Pct": round(sl_dist_pct, 4),
            "Leverage": round(leverage, 2),
            "Actual_SL_Risk_Pct": round(actual_sl_risk_pct, 2),
            "TP_Target_Return_Pct": round(tp_ret_pct, 2),
            "Outcome": outcome,
            "Realized_R": round(realized_r, 4),
            "Cumulative_Realized_R": round(cum_realized_r, 4),
            "Starting_Account_Balance_USD": f"{start_bal:.6f}" if start_bal < 1000000 else f"{start_bal:.6e}",
            "Position_Notional_USD": f"{notional:.6f}" if notional < 1000000 else f"{notional:.6e}",
            "Exchange_Fees_USD": f"{fees_usd:.6f}" if fees_usd < 1000000 else f"{fees_usd:.6e}",
            "Gross_PnL_USD": f"{gross_pnl_usd:.6f}" if abs(gross_pnl_usd) < 1000000 else f"{gross_pnl_usd:.6e}",
            "Net_PnL_USD": f"{net_pnl_usd:.6f}" if abs(net_pnl_usd) < 1000000 else f"{net_pnl_usd:.6e}",
            "Ending_Account_Balance_USD": f"{capital_net:.6f}" if capital_net < 1000000 else f"{capital_net:.6e}",
            "Trade_Narrative": narrative,
        })

out_csv_path = docs_ai_dir / "SOLUSD_2024_2026_fixed_06_tp_complete_ledger.csv"
with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(executed_trades[0].keys()))
    writer.writeheader()
    writer.writerows(executed_trades)

print(f"Total SOLUSD Trades Executed (2024–2026): {len(executed_trades)}")
print(f"First Trade Setup Time (IST): {executed_trades[0]['Setup_Time_IST']}")
print(f"First Trade Entry Time (IST): {executed_trades[0]['Entry_Time_IST']}")
print(f"First Trade Exit Time (IST):  {executed_trades[0]['Exit_Time_IST']}")
print(f"CSV Successfully Written to: {out_csv_path}")
