"""
Complete analysis script for August 2026 Diagnostic.
"""

from pathlib import Path
import pandas as pd
import numpy as np

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

aug_mask = (df["dec_dt"] >= "2026-08-01 00:00:00+00:00") & (df["dec_dt"] <= "2026-08-26 23:59:59+00:00")
aug_obs = df[aug_mask].sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

# Load candles
candles = {}
for asset in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    c_path = repo_root / "data" / "canonical" / "delta_exchange_india" / asset / "1h" / "2026.csv"
    cdf = pd.read_csv(c_path)
    cdf["dt"] = pd.to_datetime(cdf["timestamp"], utc=True)
    candles[asset] = cdf.sort_values("dt").reset_index(drop=True)

# Run full August simulation
capital_gross = 10.0
capital_net = 10.0
peak_gross = 10.0
peak_net = 10.0
fee_rate = 0.0008

current_lock_until = None
executed_trades = []
daily_stats = {}
inventory = {
    "A_NO_FILL": [],
    "B_FILLED_TP": [],
    "C_FILLED_SL": [],
    "D_FILLED_TIMEOUT": [],
    "E_SKIPPED_LOCK": [],
}

sl_buckets = {
    "<0.30%": [],
    "0.30-0.50%": [],
    "0.50-0.70%": [],
    "0.70-1.00%": [],
    "1.00-1.50%": [],
    ">1.50%": [],
}

for i, row in aug_obs.iterrows():
    asset = row["asset"]
    dec_dt = row["dec_dt"]
    dir_ = row["direction"]
    top = float(row["ob_high"])
    bot = float(row["ob_low"])
    w = top - bot
    w_pct = float(row["feat_ob_width_pct"])

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

    is_locked = (current_lock_until is not None and dec_dt < current_lock_until)

    c_df = candles[asset]
    future_candles = c_df[c_df["dt"] >= dec_dt].reset_index(drop=True)

    filled = False
    fill_bar = None
    fill_dt = None
    outcome = "NO_FILL"
    exit_dt = None
    exit_p = None
    exit_candle_dict = {}
    narrative = ""
    is_ambiguous = False

    max_b = min(72, len(future_candles))
    for b in range(max_b):
        c = future_candles.iloc[b]
        c_dt = c["dt"]
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
                is_ambiguous = True
                outcome = "FILLED_SL"
                exit_dt = c_dt
                exit_p = sl_p
                exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                narrative = f"Dual-touch ambiguous 1H candle. Both TP ({tp_p:.2f}) and SL ({sl_p:.2f}) touched in candle [{c_l:.2f} - {c_h:.2f}]. Conservative rule resolves to SL."
                break
            elif hit_tp and not hit_sl:
                outcome = "FILLED_TP"
                exit_dt = c_dt
                exit_p = tp_p
                exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                narrative = f"Price expanded in trade direction to hit fixed 0.8% TP ({tp_p:.2f}) in candle [{c_l:.2f} - {c_h:.2f}]."
                break
            elif hit_sl and not hit_tp:
                outcome = "FILLED_SL"
                exit_dt = c_dt
                exit_p = sl_p
                exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                if b == fill_bar:
                    narrative = f"Instant penetration blowthrough. Candle entered OB, filled limit order at {entry_p:.2f}, and pierced distal SL at {sl_p:.2f} in the same candle."
                else:
                    narrative = f"Filled at {entry_p:.2f}, consolidated for {b - fill_bar} bars, then reversed and breached distal SL at {sl_p:.2f}."
                break

    if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
        outcome = "FILLED_TIMEOUT"
        last_c = future_candles.iloc[max_b - 1]
        exit_dt = last_c["dt"]
        exit_p = float(last_c["close"])
        exit_candle_dict = {"dt": str(exit_dt), "open": float(last_c["open"]), "high": float(last_c["high"]), "low": float(last_c["low"]), "close": exit_p}
        narrative = f"72-hour holding limit expired. Closed at market {exit_p:.2f}."

    # Bucket name
    if sl_dist_pct < 0.30:
        b_name = "<0.30%"
    elif sl_dist_pct < 0.50:
        b_name = "0.30-0.50%"
    elif sl_dist_pct < 0.70:
        b_name = "0.50-0.70%"
    elif sl_dist_pct < 1.00:
        b_name = "0.70-1.00%"
    elif sl_dist_pct < 1.50:
        b_name = "1.00-1.50%"
    else:
        b_name = ">1.50%"

    if is_locked:
        inventory["E_SKIPPED_LOCK"].append(row["ob_id"])
    elif not filled:
        inventory["A_NO_FILL"].append(row["ob_id"])
    else:
        if outcome == "FILLED_TP":
            inventory["B_FILLED_TP"].append(row["ob_id"])
            ret_pct = tp_ret_pct
            realized_r = reward_dist / risk_dist
        elif outcome == "FILLED_SL":
            inventory["C_FILLED_SL"].append(row["ob_id"])
            ret_pct = -35.0
            realized_r = -1.0
        else:
            inventory["D_FILLED_TIMEOUT"].append(row["ob_id"])
            p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
            realized_r = p_diff / risk_dist
            ret_pct = realized_r * 35.0

        current_lock_until = exit_dt

        # Compounding
        start_g = capital_gross
        gross_pnl = start_g * (ret_pct / 100.0)
        capital_gross = max(0.0, start_g + gross_pnl)

        start_n = capital_net
        notional = start_n * leverage
        fees = notional * fee_rate
        net_pnl = (start_n * (ret_pct / 100.0)) - fees
        capital_net = max(0.0, start_n + net_pnl)

        rec = {
            "trade_num": len(executed_trades) + 1,
            "asset": asset,
            "direction": dir_,
            "ob_creation_time": str(dec_dt),
            "entry_time": str(fill_dt),
            "exit_time": str(exit_dt),
            "entry_price": entry_p,
            "ob_high": top,
            "ob_low": bot,
            "sl_price": sl_p,
            "tp_price": tp_p,
            "sl_distance_pct": sl_dist_pct,
            "leverage": leverage,
            "tp_return_pct": tp_ret_pct,
            "outcome": outcome,
            "realized_r": realized_r,
            "starting_capital_net": start_n,
            "gross_pnl_net": start_n * (ret_pct / 100.0),
            "fees_net": fees,
            "net_pnl_usd": net_pnl,
            "ending_capital_net": capital_net,
            "starting_capital_gross": start_g,
            "gross_pnl_usd": gross_pnl,
            "ending_capital_gross": capital_gross,
            "exit_candle": exit_candle_dict,
            "narrative": narrative,
            "is_ambiguous": is_ambiguous,
            "sl_bucket": b_name,
        }
        executed_trades.append(rec)
        sl_buckets[b_name].append(rec)

t_df = pd.DataFrame(executed_trades)
print(f"Total August Setups: {len(aug_obs)}")
print(f"Executed Trades: {len(t_df)}")
print(f"Wins: {len(t_df[t_df['outcome'] == 'FILLED_TP'])} | Losses: {len(t_df[t_df['outcome'] == 'FILLED_SL'])}")
print(f"Win Rate: {len(t_df[t_df['outcome'] == 'FILLED_TP'])/len(t_df)*100:.2f}%")
print(f"Mean Leverage: {t_df['leverage'].mean():.2f}x | Median: {t_df['leverage'].median():.2f}x")
print(f"Gross Ending Capital: ${capital_gross:.4f}")
print(f"Net Ending Capital: ${capital_net:.4f}")
