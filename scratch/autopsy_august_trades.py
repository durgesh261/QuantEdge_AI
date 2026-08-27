"""
Diagnostic script to trace every single August 2026 trade candle-by-candle.
"""

from pathlib import Path
import pandas as pd
import numpy as np

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

# Filter August 2026
aug_mask = (df["dec_dt"] >= "2026-08-01 00:00:00+00:00") & (df["dec_dt"] <= "2026-08-26 23:59:59+00:00")
aug_obs = df[aug_mask].sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

# Load candle data
candles = {}
for asset in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    c_path = repo_root / "data" / "canonical" / "delta_exchange_india" / asset / "1h" / "2026.csv"
    cdf = pd.read_csv(c_path)
    cdf["dt"] = pd.to_datetime(cdf["timestamp"], utc=True)
    candles[asset] = cdf.sort_values("dt").reset_index(drop=True)

# Execute chronological backtest with global 1-trade lock starting with $10.00
capital = 10.0
current_lock_until = None

executed_trades = []
inventory = {
    "A_NO_FILL": [],
    "B_FILLED_TP": [],
    "C_FILLED_SL": [],
    "D_FILLED_TIMEOUT": [],
    "E_SKIPPED_LOCK": [],
}

fee_rate_roundtrip = 0.0008

print("=" * 120)
print(f"AUGUST 2026 COMPLETE CANDLE-BY-CANDLE TRADE EXECUTION AUDIT (Starting Capital: ${capital:.2f})")
print("=" * 120)

for i, row in aug_obs.iterrows():
    asset = row["asset"]
    dec_dt = row["dec_dt"]
    direction = row["direction"]
    ob_top = float(row["ob_high"])
    ob_bot = float(row["ob_low"])
    width = ob_top - ob_bot
    width_pct = float(row["feat_ob_width_pct"])

    # Geometry
    if direction == "LONG":
        entry_p = ob_top - 0.25 * width
        sl_p = ob_bot
        risk_dist = entry_p - sl_p
        tp_p = entry_p * 1.008
        reward_dist = tp_p - entry_p
    else:  # SHORT
        entry_p = ob_bot + 0.25 * width
        sl_p = ob_top
        risk_dist = sl_p - entry_p
        tp_p = entry_p * 0.992
        reward_dist = entry_p - tp_p

    sl_dist_dec = risk_dist / entry_p
    sl_dist_pct = sl_dist_dec * 100.0
    leverage = 0.35 / sl_dist_dec
    tp_ret_pct = 0.80 * leverage

    # Check lock
    is_locked = (current_lock_until is not None and dec_dt < current_lock_until)

    # Replay on actual raw 1H candles
    c_df = candles[asset]
    future_candles = c_df[c_df["dt"] >= dec_dt].reset_index(drop=True)

    filled = False
    fill_bar_idx = None
    fill_dt = None

    outcome = "NO_FILL"
    exit_dt = None
    exit_p = None
    exit_candle_info = None
    narrative = ""
    is_ambiguous = False

    # Trace candles forward up to 72 hours
    max_bars = min(72, len(future_candles))
    for b in range(max_bars):
        c = future_candles.iloc[b]
        c_dt = c["dt"]
        c_open = float(c["open"])
        c_high = float(c["high"])
        c_low = float(c["low"])
        c_close = float(c["close"])

        if not filled:
            # Check 25% penetration limit fill
            if direction == "LONG":
                if c_low <= entry_p:
                    filled = True
                    fill_bar_idx = b
                    fill_dt = c_dt
            else:  # SHORT
                if c_high >= entry_p:
                    filled = True
                    fill_bar_idx = b
                    fill_dt = c_dt

            if not filled:
                # Check if OB invalidated before fill
                if direction == "LONG" and c_low <= sl_p:
                    narrative = "Invalidated (price breached distal SL before limit fill)."
                    break
                elif direction == "SHORT" and c_high >= sl_p:
                    narrative = "Invalidated (price breached distal SL before limit fill)."
                    break
                continue

        # Trade is filled, evaluate exit
        if filled:
            # Check TP and SL touch
            if direction == "LONG":
                hit_tp = c_high >= tp_p
                hit_sl = c_low <= sl_p
            else:
                hit_tp = c_low <= tp_p
                hit_sl = c_high >= sl_p

            if hit_tp and hit_sl:
                is_ambiguous = True
                outcome = "FILLED_SL"  # conservative tie break
                exit_dt = c_dt
                exit_p = sl_p
                exit_candle_info = {"dt": str(c_dt), "open": c_open, "high": c_high, "low": c_low, "close": c_close}
                narrative = f"Dual-touch ambiguous 1H candle. Both TP ({tp_p:.2f}) and SL ({sl_p:.2f}) touched in candle [{c_low:.2f} - {c_high:.2f}]. Conservative rule resolves to SL."
                break
            elif hit_tp and not hit_sl:
                outcome = "FILLED_TP"
                exit_dt = c_dt
                exit_p = tp_p
                exit_candle_info = {"dt": str(c_dt), "open": c_open, "high": c_high, "low": c_low, "close": c_close}
                narrative = f"Price expanded in trade direction to hit fixed 0.8% TP ({tp_p:.2f}) in candle [{c_low:.2f} - {c_high:.2f}]."
                break
            elif hit_sl and not hit_tp:
                outcome = "FILLED_SL"
                exit_dt = c_dt
                exit_p = sl_p
                exit_candle_info = {"dt": str(c_dt), "open": c_open, "high": c_high, "low": c_low, "close": c_close}
                if b == fill_bar_idx:
                    narrative = f"Instant penetration blowthrough. Candle entered OB, filled limit order at {entry_p:.2f}, and pierced distal SL at {sl_p:.2f} in the same candle."
                else:
                    narrative = f"Filled at {entry_p:.2f}, consolidated for {b - fill_bar_idx} bars, then reversed and breached distal SL at {sl_p:.2f}."
                break

    if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
        outcome = "FILLED_TIMEOUT"
        last_c = future_candles.iloc[max_bars - 1]
        exit_dt = last_c["dt"]
        exit_p = float(last_c["close"])
        exit_candle_info = {"dt": str(exit_dt), "open": float(last_c["open"]), "high": float(last_c["high"]), "low": float(last_c["low"]), "close": exit_p}
        narrative = f"72-hour holding limit expired. Position closed at market {exit_p:.2f}."

    # Update inventory
    if is_locked:
        inventory["E_SKIPPED_LOCK"].append({
            "ob_id": row["ob_id"],
            "asset": asset,
            "dec_dt": str(dec_dt),
            "reason": f"Locked out by active position open until {current_lock_until}",
        })
    elif not filled:
        inventory["A_NO_FILL"].append({
            "ob_id": row["ob_id"],
            "asset": asset,
            "dec_dt": str(dec_dt),
            "entry_p": entry_p,
            "narrative": narrative or "Price never penetrated 25% depth into zone.",
        })
    else:
        # Trade is executed in global portfolio
        if outcome == "FILLED_TP":
            inventory["B_FILLED_TP"].append(row["ob_id"])
            realized_ret_pct = tp_ret_pct
            realized_r = reward_dist / risk_dist
        elif outcome == "FILLED_SL":
            inventory["C_FILLED_SL"].append(row["ob_id"])
            realized_ret_pct = -35.0
            realized_r = -1.0
        else:
            inventory["D_FILLED_TIMEOUT"].append(row["ob_id"])
            p_diff = (exit_p - entry_p) if direction == "LONG" else (entry_p - exit_p)
            realized_r = p_diff / risk_dist
            realized_ret_pct = realized_r * 35.0

        current_lock_until = exit_dt

        # Accounting
        start_cap = capital
        notional = start_cap * leverage
        fees = notional * fee_rate_roundtrip
        gross_pnl = start_cap * (realized_ret_pct / 100.0)
        net_pnl = gross_pnl - fees
        capital = max(0.0, start_cap + net_pnl)

        trade_record = {
            "trade_num": len(executed_trades) + 1,
            "asset": asset,
            "direction": direction,
            "ob_time": str(dec_dt),
            "fill_time": str(fill_dt),
            "exit_time": str(exit_dt),
            "ob_high": ob_top,
            "ob_low": ob_bot,
            "entry_p": entry_p,
            "sl_p": sl_p,
            "tp_p": tp_p,
            "sl_dist_pct": sl_dist_pct,
            "leverage": leverage,
            "tp_ret_pct": tp_ret_pct,
            "outcome": outcome,
            "realized_r": realized_r,
            "starting_capital": start_cap,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "net_pnl": net_pnl,
            "ending_capital": capital,
            "exit_candle": exit_candle_info,
            "narrative": narrative,
            "is_ambiguous": is_ambiguous,
        }
        executed_trades.append(trade_record)

print(f"Total August Setups: {len(aug_obs)}")
print(f"  NO_FILL (Price never reached 25%):        {len(inventory['A_NO_FILL'])}")
print(f"  SKIPPED by Global 1-Trade Lock:          {len(inventory['E_SKIPPED_LOCK'])}")
print(f"  EXECUTED TRADES:                          {len(executed_trades)}")
print(f"    - FILLED -> TP:                         {len(inventory['B_FILLED_TP'])}")
print(f"    - FILLED -> SL:                         {len(inventory['C_FILLED_SL'])}")
print(f"    - FILLED -> TIMEOUT:                    {len(inventory['D_FILLED_TIMEOUT'])}")

print("\n" + "=" * 120)
print("EXECUTED AUGUST 2026 TRADES LEDGER & AUTOPSY")
print("=" * 120)
for t in executed_trades:
    print(f"\nTrade #{t['trade_num']:02d} | {t['asset']} {t['direction']} | Fill: {t['fill_time'][:16]} -> Exit: {t['exit_time'][:16]}")
    print(f"  OB: [{t['ob_low']:.2f}, {t['ob_high']:.2f}] | Entry: {t['entry_p']:.2f} | SL: {t['sl_p']:.2f} ({t['sl_dist_pct']:.3f}%) | TP: {t['tp_p']:.2f} (+0.80%)")
    print(f"  Leverage: {t['leverage']:.1f}x | TP Target Return: +{t['tp_ret_pct']:.1f}% | SL Risk Return: -35.0%")
    print(f"  Outcome: {t['outcome']} (Realized R: {t['realized_r']:+.2f}R) | Ambiguous: {t['is_ambiguous']}")
    print(f"  Capital: ${t['starting_capital']:.4f} -> Gross PnL: ${t['gross_pnl']:+.4f} -> Fees: ${t['fees']:.4f} -> Net PnL: ${t['net_pnl']:+.4f} -> Ending: ${t['ending_capital']:.4f}")
    print(f"  Exit Candle: {t['exit_candle']}")
    print(f"  Autopsy Narrative: {t['narrative']}")
