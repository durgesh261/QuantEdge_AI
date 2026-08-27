"""
Scratch script to run the Fixed 0.8% TP + 35% SL Leverage Compounding Backtest.
"""

from pathlib import Path
import numpy as np
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

print(f"Total candidate setups: {len(df)}")

# Trackers
executed_trades = []
unfilled_count = 0
skipped_lock_count = 0
current_lock_until = None

capital_gross = 10.0
capital_net = 10.0
peak_capital_gross = 10.0
peak_capital_net = 10.0

fee_rate_roundtrip = 0.0008  # 0.04% per side

sl_buckets = {
    "<0.50%": [],
    "0.50-0.60%": [],
    "0.60-0.70%": [],
    "0.70-0.80%": [],
    "0.80-1.00%": [],
    "1.00-1.50%": [],
    ">1.50%": [],
}

exact_070_count = 0
exact_50x_count = 0

for i, row in df.iterrows():
    dec_dt = row["dec_dt"]
    if current_lock_until is not None and dec_dt < current_lock_until:
        skipped_lock_count += 1
        continue

    direction = row["direction"]
    ob_top = float(row["ob_high"])
    ob_bot = float(row["ob_low"])
    width = ob_top - ob_bot
    if width <= 1e-6:
        continue

    # 1. Entry at 25% penetration inside OB
    if direction == "LONG":
        entry_p = ob_top - 0.25 * width
        sl_p = ob_bot
        risk_dist = entry_p - sl_p  # 0.75 * width
        tp_p = entry_p * 1.008     # Exactly +0.8% price move
        reward_dist = tp_p - entry_p
    else:  # SHORT
        entry_p = ob_bot + 0.25 * width
        sl_p = ob_top
        risk_dist = sl_p - entry_p  # 0.75 * width
        tp_p = entry_p * 0.992     # Exactly -0.8% price move
        reward_dist = entry_p - tp_p

    if risk_dist <= 1e-6:
        continue

    sl_dist_dec = risk_dist / entry_p
    sl_dist_pct = sl_dist_dec * 100.0
    tp_dist_pct = 0.80

    # 2. Leverage = 0.35 / sl_dist_dec
    leverage = 0.35 / sl_dist_dec
    gross_tp_return_pct = 0.008 * leverage * 100.0
    gross_sl_return_pct = -35.0

    # Track 0.70% and 50x frequency
    if 0.65 <= sl_dist_pct <= 0.75:
        exact_070_count += 1
    if 45.0 <= leverage <= 55.0:
        exact_50x_count += 1

    # 3. Check limit order fill (requires MAE >= 0.25 inside zone)
    raw_mae_r = float(row["mae_r"])
    raw_mfe_r = float(row["mfe_r"])
    holding_bars = int(row["holding_bars"])
    exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)

    if raw_mae_r < 0.25:
        unfilled_count += 1
        continue

    # 4. Forward outcome
    eff_mfe = (raw_mfe_r + 0.25) * width
    eff_mae = (raw_mae_r - 0.25) * width

    tp_hit = eff_mfe >= reward_dist
    sl_hit = eff_mae >= risk_dist
    is_ambiguous = (tp_hit and sl_hit and holding_bars <= 1)

    if tp_hit and not sl_hit:
        outcome = "TP_HIT"
        realized_ret_pct = gross_tp_return_pct
        realized_r = reward_dist / risk_dist
        exit_reason = "TP_HIT"
    elif sl_hit and not tp_hit:
        outcome = "SL_HIT"
        realized_ret_pct = gross_sl_return_pct
        realized_r = -1.0
        exit_reason = "SL_HIT"
    elif tp_hit and sl_hit:
        outcome = "SL_HIT"  # Conservative
        realized_ret_pct = gross_sl_return_pct
        realized_r = -1.0
        exit_reason = "SL_HIT"
    else:
        outcome = "TIMEOUT"
        # Prorated exit
        eff_r = (eff_mfe - eff_mae) / risk_dist
        realized_r = min(reward_dist / risk_dist, max(-1.0, eff_r))
        realized_ret_pct = realized_r * 35.0
        exit_reason = "TIMEOUT"

    # Lock portfolio
    current_lock_until = exit_dt

    # 5. Continuous Compounding Ledger
    # Gross
    start_cap_g = capital_gross
    gross_pnl_usd = start_cap_g * (realized_ret_pct / 100.0)
    capital_gross = max(0.0, start_cap_g + gross_pnl_usd)

    # Net
    start_cap_n = capital_net
    notional_usd = start_cap_n * leverage
    fees_usd = notional_usd * fee_rate_roundtrip
    gross_pnl_on_net = start_cap_n * (realized_ret_pct / 100.0)
    net_pnl_usd = gross_pnl_on_net - fees_usd
    capital_net = max(0.0, start_cap_n + net_pnl_usd)

    if capital_gross > peak_capital_gross:
        peak_capital_gross = capital_gross
    if capital_net > peak_capital_net:
        peak_capital_net = capital_net

    # Bucketing
    if sl_dist_pct < 0.50:
        bucket = "<0.50%"
    elif sl_dist_pct < 0.60:
        bucket = "0.50-0.60%"
    elif sl_dist_pct < 0.70:
        bucket = "0.60-0.70%"
    elif sl_dist_pct < 0.80:
        bucket = "0.70-0.80%"
    elif sl_dist_pct < 1.00:
        bucket = "0.80-1.00%"
    elif sl_dist_pct < 1.50:
        bucket = "1.00-1.50%"
    else:
        bucket = ">1.50%"

    trade_info = {
        "trade_id": len(executed_trades) + 1,
        "asset": row["asset"],
        "direction": direction,
        "ob_timestamp": str(row["decision_timestamp"]),
        "entry_timestamp": dec_dt.isoformat(),
        "exit_timestamp": exit_dt.isoformat(),
        "ob_high": ob_top,
        "ob_low": ob_bot,
        "ob_width": width,
        "ob_width_pct": row["feat_ob_width_pct"],
        "entry_price": entry_p,
        "sl_price": sl_p,
        "tp_price": tp_p,
        "entry_to_sl_distance_pct": sl_dist_pct,
        "tp_price_distance_pct": tp_dist_pct,
        "calculated_leverage": leverage,
        "starting_capital_gross": start_cap_g,
        "gross_sl_return_pct": gross_sl_return_pct,
        "gross_tp_return_pct": gross_tp_return_pct,
        "gross_pnl_usd": gross_pnl_usd,
        "starting_capital_net": start_cap_n,
        "fees_usd": fees_usd,
        "net_pnl_usd": net_pnl_usd,
        "ending_capital_gross": capital_gross,
        "ending_capital_net": capital_net,
        "outcome": outcome,
        "realized_r": realized_r,
        "exit_reason": exit_reason,
        "ambiguous_intrabar": is_ambiguous,
        "bucket": bucket,
    }
    executed_trades.append(trade_info)
    sl_buckets[bucket].append(trade_info)

tdf = pd.DataFrame(executed_trades)

print(f"\n--- SIMULATION RESULTS: FIXED 0.8% TP + 35% SL LEVERAGE ---")
print(f"Total Candidate Setups: {len(df)}")
print(f"Unfilled (did not reach 25% depth): {unfilled_count}")
print(f"Skipped due to Global 1-Trade Lock: {skipped_lock_count}")
print(f"Total Executed Trades: {len(tdf)}")
wins = tdf[tdf["realized_r"] > 0]
losses = tdf[tdf["realized_r"] <= 0]
print(f"Wins: {len(wins)} ({len(wins)/len(tdf)*100:.2f}%) | Losses: {len(losses)} ({len(losses)/len(tdf)*100:.2f}%)")
print(f"Mean Realized R: {tdf['realized_r'].mean():+.4f}R | Total R: {tdf['realized_r'].sum():+.2f}R")
gg = wins["realized_r"].sum()
gl = abs(losses["realized_r"].sum())
print(f"Profit Factor (R): {gg/gl:.2f}")

print(f"\n--- Compounding Results ($10.00 Starting) ---")
print(f"Gross Ending Capital: ${capital_gross:.6f} (Return: {(capital_gross - 10.0)/10.0*100:+.2f}%)")
print(f"Net Ending Capital (After Fees): ${capital_net:.6f} (Return: {(capital_net - 10.0)/10.0*100:+.2f}%)")

print(f"\n--- Leverage & Return Statistics ---")
print(f"Mean Leverage: {tdf['calculated_leverage'].mean():.2f}x | Median: {tdf['calculated_leverage'].median():.2f}x")
print(f"Min Leverage: {tdf['calculated_leverage'].min():.2f}x | Max Leverage: {tdf['calculated_leverage'].max():.2f}x")
print(f"Mean Gross TP Return: {tdf['gross_tp_return_pct'].mean():.2f}% | Median: {tdf['gross_tp_return_pct'].median():.2f}%")
print(f"Trades with Entry->SL ~ 0.70% (0.65%-0.75%): {exact_070_count} setups")
print(f"Trades with Leverage ~ 50x (45x-55x): {exact_50x_count} setups")

print(f"\n--- SL Distance Bucket Breakdown ---")
for b_name, b_trades in sl_buckets.items():
    b_df = pd.DataFrame(b_trades)
    if len(b_df) > 0:
        b_w = len(b_df[b_df["realized_r"] > 0])
        b_wr = b_w / len(b_df) * 100
        b_exp = b_df["realized_r"].mean()
        b_lev = b_df["calculated_leverage"].mean()
        b_tp_ret = b_df["gross_tp_return_pct"].mean()
        b_gg = b_df[b_df["realized_r"] > 0]["realized_r"].sum()
        b_gl = abs(b_df[b_df["realized_r"] <= 0]["realized_r"].sum())
        b_pf = b_gg / b_gl if b_gl > 0 else 99.0
        print(f"{b_name:<12} -> N: {len(b_df):>4} | WR: {b_wr:>5.2f}% | AvgLev: {b_lev:>6.1f}x | AvgTPRet: {b_tp_ret:>6.1f}% | Exp: {b_exp:>+6.4f}R | PF: {b_pf:>4.2f}")
    else:
        print(f"{b_name:<12} -> N:    0")
