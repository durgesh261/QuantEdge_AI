"""
Full Month August 2026 Backtest with $10.00 Starting Capital, Max 100x Leverage, Max 35% SL, and Multi-Pair Concurrent Execution.
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

candles_df = {}
all_aug_setups = []

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
    cdf = pd.DataFrame(c_records).sort_values("timestamp").reset_index(drop=True)
    candles_df[sym] = cdf
    
    ctx = build_smc_context(candles)
    setups, _ = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)
    for s in setups:
        dec_ts = candles[s.decision_bar].timestamp
        if dec_ts >= datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc):
            all_aug_setups.append({
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

all_aug_setups = sorted(all_aug_setups, key=lambda x: (x["decision_dt"], x["ob_id"]))

def to_ist_str(dt):
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_dt = dt.astimezone(ist_tz)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")

def run_august_10usd_simulation(setups_list, start_cap=10.0, fee_rate=0.0008, max_leverage=100.0, max_sl_risk_pct=35.0):
    active_locks = {sym: None for sym in symbols}
    capital_gross = start_cap
    capital_net = start_cap
    peak_gross = start_cap
    peak_net = start_cap
    max_dd_gross = 0.0
    max_dd_net = 0.0
    
    executed_trades = []
    skipped_asset_lock = []
    unfilled = []
    
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
        
        # CAPPED LEVERAGE: Target 35% risk, but capped at 100x
        uncapped_lev = max_sl_risk_pct / sl_dist_pct
        leverage = min(max_leverage, uncapped_lev)
        
        # Actual SL risk % (if leverage capped at 100x, SL risk is < 35%)
        actual_sl_risk_pct = leverage * sl_dist_pct
        actual_sl_ret_pct = -1.0 * actual_sl_risk_pct
        
        # Actual TP return % (+0.80% price move * leverage)
        tp_ret_pct = 0.80 * leverage
        
        # Check Per-Asset Lock (Allow concurrent trades in DIFFERENT assets!)
        if active_locks[asset] is not None and dec_dt < active_locks[asset]:
            skipped_asset_lock.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "dt": str(dec_dt),
                "reason": f"Active {asset} position open until {active_locks[asset]}",
            })
            continue
            
        # Replay candles for this asset
        c_df = candles_df[asset]
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
                    loss_mech = "DUAL_TOUCH_AMBIGUITY"
                    narrative = f"Dual-touch ambiguous 1H candle. Both TP ({tp_p:.2f}) and SL ({sl_p:.2f}) touched. Conservative rule resolves to SL."
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_dt = c_dt
                    exit_p = tp_p
                    narrative = f"Price expanded in trade direction to hit fixed 0.8% TP ({tp_p:.2f}) in candle [{c_l:.2f} - {c_h:.2f}]."
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    if b == fill_bar:
                        loss_mech = "INSTANT_BLOWTHROUGH"
                        narrative = f"Instant penetration blowthrough. Candle entered OB, filled limit order at {entry_p:.2f}, and pierced distal SL at {sl_p:.2f} in the same candle."
                    else:
                        loss_mech = "CONSOLIDATION_REVERSAL"
                        narrative = f"Filled at {entry_p:.2f}, moved inside zone for {b - fill_bar} bars, then reversed and breached distal SL at {sl_p:.2f}."
                    break
                    
        if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
            outcome = "FILLED_TIMEOUT"
            last_c = future_candles.iloc[max_b - 1]
            exit_dt = last_c["timestamp"]
            exit_p = float(last_c["close"])
            narrative = f"Holding horizon expired. Closed at market {exit_p:.2f}."
            
        if not filled:
            unfilled.append({
                "ob_id": s["ob_id"],
                "asset": asset,
                "dt": str(dec_dt),
                "narrative": narrative or "Price never reached 25% penetration depth.",
            })
        else:
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
            
    return {
        "executed_trades": executed_trades,
        "skipped_asset_lock": skipped_asset_lock,
        "unfilled": unfilled,
        "ending_capital_gross": capital_gross,
        "ending_capital_net": capital_net,
        "max_dd_gross": max_dd_gross,
        "max_dd_net": max_dd_net,
    }

res = run_august_10usd_simulation(all_aug_setups, start_cap=10.0, max_leverage=100.0, max_sl_risk_pct=35.0)
trades = res["executed_trades"]

# Write to CSV
out_csv = docs_ai_dir / "august_2026_10usd_multipair_capped_trades.csv"
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
    writer.writeheader()
    writer.writerows(trades)

print(f"Total August 2026 Executed Trades: {len(trades)}")
wins = [t for t in trades if t["outcome"] == "FILLED_TP"]
losses = [t for t in trades if t["outcome"] == "FILLED_SL"]
print(f"Wins: {len(wins)} ({len(wins)/len(trades)*100:.2f}%) | Losses: {len(losses)}")
print(f"Starting Capital: $10.00")
print(f"Gross Ending Capital: ${res['ending_capital_gross']:.4f}")
print(f"Net Ending Capital:   ${res['ending_capital_net']:.4f}")
print(f"Max Drawdown (Gross): {res['max_dd_gross']:.2f}% | Max Drawdown (Net): {res['max_dd_net']:.2f}%")
