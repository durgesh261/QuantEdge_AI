"""
Scratch script to run the fixed 0.7% TP research simulation.
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

current_lock_until = None
executed_trades = []
unfilled_setups = 0

cap_35pct = 10.0  # 35% margin risk per trade
cap_10pct = 10.0  # 10% risk per trade
cap_1pct = 10.0   # 1.0% risk per trade

rr_distribution = {"A_smaller_than_sl (< 0.9R)": 0, "B_approx_equal (0.9R-1.1R)": 0, "C_larger_than_sl (> 1.1R)": 0}
r_values = []

for i, row in df.iterrows():
    dec_dt = row["dec_dt"]
    if current_lock_until is not None and dec_dt < current_lock_until:
        continue

    direction = row["direction"]
    ob_top = float(row["ob_high"])
    ob_bot = float(row["ob_low"])
    width = ob_top - ob_bot
    if width <= 1e-6:
        continue

    # Entry is 25% penetration inside OB
    if direction == "LONG":
        entry_p = ob_top - 0.25 * width
        sl_p = ob_bot
        risk_dist = entry_p - sl_p  # 0.75 * width
        tp_p = entry_p * 1.007
        reward_dist = tp_p - entry_p  # 0.007 * entry_p
    else:  # SHORT
        entry_p = ob_bot + 0.25 * width
        sl_p = ob_top
        risk_dist = sl_p - entry_p  # 0.75 * width
        tp_p = entry_p * 0.993
        reward_dist = entry_p - tp_p  # 0.007 * entry_p

    if risk_dist <= 1e-6:
        continue

    planned_rr = reward_dist / risk_dist
    r_values.append(planned_rr)

    if planned_rr < 0.90:
        rr_distribution["A_smaller_than_sl (< 0.9R)"] += 1
    elif planned_rr <= 1.10:
        rr_distribution["B_approx_equal (0.9R-1.1R)"] += 1
    else:
        rr_distribution["C_larger_than_sl (> 1.1R)"] += 1

    # Check limit order fill: Price must reach 25% penetration (mae relative to zone width >= 0.25)
    raw_mae_r = float(row["mae_r"])  # MAE in full OB width
    raw_mfe_r = float(row["mfe_r"])  # MFE in full OB width
    holding_bars = int(row["holding_bars"])
    exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)

    if raw_mae_r < 0.25:
        # Price bounced before reaching 25% penetration
        unfilled_setups += 1
        continue

    # Setup is filled!
    # Normalize MFE and MAE from entry price in terms of planned risk_dist (0.75 * width)
    # Price distance to SL is risk_dist
    # Price distance to TP is reward_dist = planned_rr * risk_dist
    effective_mfe_price = (raw_mfe_r + 0.25) * width if direction == "LONG" else (raw_mfe_r + 0.25) * width
    effective_mae_price = (raw_mae_r - 0.25) * width

    tp_hit = effective_mfe_price >= reward_dist
    sl_hit = effective_mae_price >= risk_dist
    is_ambiguous = (tp_hit and sl_hit and holding_bars <= 1)

    if tp_hit and not sl_hit:
        outcome = "TP_HIT"
        realized_r = planned_rr
    elif sl_hit and not tp_hit:
        outcome = "SL_HIT"
        realized_r = -1.0
    elif tp_hit and sl_hit:
        # Conservative tie break
        outcome = "SL_HIT"
        realized_r = -1.0
    else:
        outcome = "TIMEOUT"
        realized_r = min(planned_rr, max(-1.0, (effective_mfe_price - effective_mae_price) / risk_dist))

    # Apply global lock
    current_lock_until = exit_dt

    # 35% margin risk compounding
    start_35 = cap_35pct
    pnl_35 = start_35 * 0.35 * realized_r
    cap_35pct = max(0.0, start_35 + pnl_35)

    # 10% risk compounding
    start_10 = cap_10pct
    pnl_10 = start_10 * 0.10 * realized_r
    cap_10pct = max(0.0, start_10 + pnl_10)

    # 1.0% risk compounding
    start_1 = cap_1pct
    pnl_1 = start_1 * 0.01 * realized_r
    cap_1pct = max(0.0, start_1 + pnl_1)

    # Stop distance percent
    stop_pct = (risk_dist / entry_p) * 100.0
    leverage = min(100, max(1, int(35.0 / stop_pct))) if stop_pct > 0 else 10

    executed_trades.append({
        "trade_number": len(executed_trades) + 1,
        "timestamp": dec_dt.isoformat(),
        "asset": row["asset"],
        "direction": direction,
        "ob_id": row["ob_id"],
        "ob_width_pct": row["feat_ob_width_pct"],
        "entry_price": round(entry_p, 4),
        "sl_price": round(sl_p, 4),
        "tp_price": round(tp_p, 4),
        "risk_distance": round(risk_dist, 4),
        "reward_distance": round(reward_dist, 4),
        "planned_rr": round(planned_rr, 4),
        "leverage": leverage,
        "outcome": outcome,
        "realized_r": round(realized_r, 4),
        "starting_capital_35pct": round(start_35, 4),
        "pnl_dollar_35pct": round(pnl_35, 4),
        "ending_capital_35pct": round(cap_35pct, 4),
        "starting_capital_10pct": round(start_10, 4),
        "pnl_dollar_10pct": round(pnl_10, 4),
        "ending_capital_10pct": round(cap_10pct, 4),
        "starting_capital_1pct": round(start_1, 4),
        "pnl_dollar_1pct": round(pnl_1, 4),
        "ending_capital_1pct": round(cap_1pct, 4),
        "holding_time_hours": holding_bars,
        "is_ambiguous": is_ambiguous,
    })

t_df = pd.DataFrame(executed_trades)
print(f"\n--- FIXED 0.7% TP SIMULATION SUMMARY ---")
print(f"Total Candidate Setups: 1670")
print(f"Unfilled (price didn't reach 25% depth): {unfilled_setups}")
print(f"Executed Trades (with Global 1-Trade Lock): {len(t_df)}")
wins = t_df[t_df["realized_r"] > 0]
losses = t_df[t_df["realized_r"] <= 0]
print(f"Wins: {len(wins)} ({len(wins)/len(t_df)*100:.2f}%) | Losses: {len(losses)} ({len(losses)/len(t_df)*100:.2f}%)")
print(f"Mean Realized R: {t_df['realized_r'].mean():+.4f}R")
print(f"Total Realized R: {t_df['realized_r'].sum():+.2f}R")
gg = wins["realized_r"].sum()
gl = abs(losses["realized_r"].sum())
print(f"Profit Factor: {gg/gl:.2f}")
print(f"\n--- Planned RR (TP/SL Ratio) Distribution ---")
print(f"Mean Planned RR: {np.mean(r_values):.2f}R | Median: {np.median(r_values):.2f}R | Min: {np.min(r_values):.2f}R | Max: {np.max(r_values):.2f}R")
for k, v in rr_distribution.items():
    print(f"  {k}: {v} setups ({v/len(r_values)*100:.1f}%)")

print(f"\n--- Compounded Account Balances ($10.00 Base) ---")
print(f"35% Margin-Risk Compounding: Ending Capital = ${cap_35pct:.4f} (Return: {(cap_35pct - 10.0)/10.0*100:+.2f}%)")
print(f"10% Risk Compounding: Ending Capital = ${cap_10pct:.4f} (Return: {(cap_10pct - 10.0)/10.0*100:+.2f}%)")
print(f"1.0% Risk Compounding: Ending Capital = ${cap_1pct:.4f} (Return: {(cap_1pct - 10.0)/10.0*100:+.2f}%)")
