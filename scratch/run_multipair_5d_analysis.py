"""
Multi-Pair Concurrent Execution Backtest for the last 5 days (Aug 21-26, 2026)
and full month of August 2026.
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import json

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

root = _find_repo_root()
base = root / "data" / "canonical" / "delta_exchange_india"
symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

# Load all candles as DataFrames for fast slicing
candles_df = {}
candles_objs = {}
all_aug_setups = []

for sym in symbols:
    candles = load_canonical_full_history(base, sym)
    candles_objs[sym] = candles
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
        dt = datetime.fromisoformat(s.creation_time)
        if dt >= datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc):
            all_aug_setups.append({
                "ob_id": s.setup_id,
                "asset": sym,
                "direction": s.direction,
                "creation_time": s.creation_time,
                "creation_dt": dt,
                "entry_price": float(s.entry_price),
                "sl_price": float(s.sl_price),
                "tp_price": float(s.tp_price),
                "ob_high": float(s.ob_high),
                "ob_low": float(s.ob_low),
                "decision_bar": s.decision_bar,
            })

all_aug_setups = sorted(all_aug_setups, key=lambda x: (x["creation_dt"], x["ob_id"]))

print(f"Total August 2026 Setups Detected: {len(all_aug_setups)}")
last5d_setups = [s for s in all_aug_setups if s["creation_dt"] >= datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)]
print(f"Total Last 5 Days Setups (Aug 21–26): {len(last5d_setups)}")

# Replay function with Per-Asset Lock (Multi-Pair Concurrent Execution)
def run_multipair_backtest(setups_list, start_cap=10.0, fee_rate=0.0008):
    # active_locks maps asset -> lock_until_dt
    active_locks = {sym: None for sym in symbols}
    
    capital_gross = start_cap
    capital_net = start_cap
    
    executed_trades = []
    skipped_asset_lock = []
    unfilled = []
    
    for s in setups_list:
        asset = s["asset"]
        dec_dt = s["creation_dt"]
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
        leverage = 0.35 / sl_dist_dec
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
        fill_candle = {}
        outcome = "NO_FILL"
        exit_dt = None
        exit_p = None
        exit_candle = {}
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
                    fill_candle = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                elif dir_ == "SHORT" and c_h >= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                    fill_candle = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                    
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
                    exit_candle = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                    loss_mech = "DUAL_TOUCH_AMBIGUITY"
                    narrative = f"Dual-touch ambiguous 1H candle. Both TP ({tp_p:.2f}) and SL ({sl_p:.2f}) touched. Conservative rule resolves to SL."
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_dt = c_dt
                    exit_p = tp_p
                    exit_candle = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                    narrative = f"Price expanded in trade direction to hit fixed 0.8% TP ({tp_p:.2f}) in candle [{c_l:.2f} - {c_h:.2f}]."
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    exit_candle = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
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
            exit_candle = {"dt": str(exit_dt), "open": float(last_c["open"]), "high": float(last_c["high"]), "low": float(last_c["low"]), "close": exit_p}
            narrative = f"Holding horizon expired (or currently active candle reached). Market exit at {exit_p:.2f}."
            
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
                ret_pct = -35.0
                realized_r = -1.0
            else:
                p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
                realized_r = p_diff / risk_dist
                ret_pct = realized_r * 35.0
                
            active_locks[asset] = exit_dt
            
            # Accounting
            start_g = capital_gross
            gross_pnl = start_g * (ret_pct / 100.0)
            capital_gross = max(0.0, start_g + gross_pnl)
            
            start_n = capital_net
            notional = start_n * leverage
            fees = notional * fee_rate
            net_pnl = (start_n * (ret_pct / 100.0)) - fees
            capital_net = max(0.0, start_n + net_pnl)
            
            trade_rec = {
                "trade_num": len(executed_trades) + 1,
                "asset": asset,
                "direction": dir_,
                "ob_time": str(dec_dt),
                "entry_time": str(fill_dt),
                "exit_time": str(exit_dt),
                "entry_price": entry_p,
                "ob_high": top,
                "ob_low": bot,
                "sl_price": sl_p,
                "tp_price": tp_p,
                "sl_dist_pct": sl_dist_pct,
                "leverage": leverage,
                "tp_ret_pct": tp_ret_pct,
                "outcome": outcome,
                "realized_r": realized_r,
                "loss_mechanism": loss_mech,
                "starting_capital_gross": start_g,
                "gross_pnl": gross_pnl,
                "ending_capital_gross": capital_gross,
                "starting_capital_net": start_n,
                "fees": fees,
                "net_pnl": net_pnl,
                "ending_capital_net": capital_net,
                "narrative": narrative,
                "exit_candle": exit_candle,
            }
            executed_trades.append(trade_rec)
            
    return {
        "executed_trades": executed_trades,
        "skipped_asset_lock": skipped_asset_lock,
        "unfilled": unfilled,
        "ending_capital_gross": capital_gross,
        "ending_capital_net": capital_net,
    }

# Run for Last 5 Days (Aug 21–26)
res_5d = run_multipair_backtest(last5d_setups, start_cap=10.0)
t5d = res_5d["executed_trades"]

print("\n" + "=" * 100)
print(f"LAST 5 DAYS (AUG 21–26, 2026) MULTI-PAIR CONCURRENT TRADES (Starting Capital: $10.00)")
print("=" * 100)
print(f"Total Setups: {len(last5d_setups)} | Executed Trades: {len(t5d)}")
for t in t5d:
    print(f"\nTrade #{t['trade_num']:02d} | {t['asset']} {t['direction']} | Fill: {t['entry_time'][:16]} -> Exit: {t['exit_time'][:16]}")
    print(f"  Entry: {t['entry_price']:.2f} | SL: {t['sl_price']:.2f} ({t['sl_dist_pct']:.3f}%) | TP: {t['tp_price']:.2f} (+0.80%)")
    print(f"  Leverage: {t['leverage']:.1f}x | TP Return: +{t['tp_ret_pct']:.1f}% | Outcome: {t['outcome']} ({t['realized_r']:+.2f}R)")
    print(f"  Capital (Net): ${t['starting_capital_net']:.2f} -> Fees: ${t['fees']:.2f} -> Net PnL: ${t['net_pnl']:+.2f} -> Ending: ${t['ending_capital_net']:.2f}")
    print(f"  Autopsy: {t['narrative']}")

print(f"\nEnding Capital (Gross): ${res_5d['ending_capital_gross']:.4f}")
print(f"Ending Capital (Net):   ${res_5d['ending_capital_net']:.4f}")
