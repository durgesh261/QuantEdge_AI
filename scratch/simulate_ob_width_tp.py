"""
Scratch script to run the OB width TP 0.6% research simulation with global 1-trade lock.
"""

from pathlib import Path
import numpy as np
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)

# Sort all 1,670 setups chronologically by decision_timestamp
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

print(f"Total candidate setups in master dataset: {len(df)}")
print(f"Date range: {df['dec_dt'].min()} to {df['dec_dt'].max()}")

# Classify OB Width Regime
# Width percentage
df["ob_width_pct"] = df["feat_ob_width_pct"]
df["regime"] = np.where(df["ob_width_pct"] <= 0.6, "REGIME_A_LE_06", "REGIME_B_GT_06")

print("\n--- OB Width Distribution ---")
print(df["regime"].value_counts())

# Sequential simulation with Global 1-Trade Lock
current_time = None
executed_trades = []

# Account capital tracking ($10 starting)
# We test 3 risk fractions:
# 1. 100% full margin / account allocation (where -1R = -100% or loss of full capital if 1 loss occurs)
# 2. 10% risk per trade (compounding)
# 3. 1.0% risk per trade (conservative compounding)
capital_100 = 10.0
capital_10 = 10.0
capital_1 = 10.0

for i, row in df.iterrows():
    dec_dt = row["dec_dt"]
    if current_time is not None and dec_dt < current_time:
        # Portfolio is locked by active trade on another/same asset
        continue

    # Determine trade geometry
    direction = row["direction"]
    ob_top = float(row["ob_high"])
    ob_bot = float(row["ob_low"])
    width = ob_top - ob_bot
    width_pct = float(row["ob_width_pct"])
    is_narrow = width_pct <= 0.6

    # QuantEdge SMC Entry:
    # Narrow (<= 0.6%): Edge entry
    # Wide (> 0.6%): 25% depth entry
    if direction == "LONG":
        entry_p = ob_top if is_narrow else (ob_top - 0.25 * width)
        sl_p = ob_bot
        risk_dist = entry_p - sl_p
        if is_narrow:
            # Regime A: 60/35 target = 1.7143R
            tp_p = entry_p + (60.0 / 35.0) * risk_dist
            planned_rr = 60.0 / 35.0
        else:
            # Regime B: 1:1 target = 1.0R
            tp_p = entry_p + risk_dist
            planned_rr = 1.0
    else:  # SHORT
        entry_p = ob_bot if is_narrow else (ob_bot + 0.25 * width)
        sl_p = ob_top
        risk_dist = sl_p - entry_p
        if is_narrow:
            tp_p = entry_p - (60.0 / 35.0) * risk_dist
            planned_rr = 60.0 / 35.0
        else:
            tp_p = entry_p - risk_dist
            planned_rr = 1.0

    # Replay outcome
    mfe_r = float(row["mfe_r"])
    mae_r = float(row["mae_r"])
    holding_bars = int(row["holding_bars"])
    
    # Target R multiple
    target_r = planned_rr
    
    # Check if TP hit before SL
    # If Regime A (target = 1.714R):
    # If Regime B (target = 1.0R):
    is_ambiguous = (mfe_r >= target_r and mae_r >= 1.0 and holding_bars <= 1)
    
    if mfe_r >= target_r and mae_r < 1.0:
        outcome = "TP_HIT"
        realized_r = target_r
    elif mae_r >= 1.0 and mfe_r < target_r:
        outcome = "SL_HIT"
        realized_r = -1.0
    elif mfe_r >= target_r and mae_r >= 1.0:
        # Conservative tie break: SL hit first
        outcome = "SL_HIT"
        realized_r = -1.0
    else:
        # Timeout exit
        outcome = "TIMEOUT_EXIT"
        realized_r = min(target_r, max(-1.0, mfe_r - mae_r * 0.5))

    # Duration
    exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)
    current_time = exit_dt

    # Dollar PnL (at 10% risk per trade)
    start_cap = capital_10
    risk_dollars = start_cap * 0.10
    pnl_dollars = risk_dollars * realized_r
    capital_10 = max(0.0, start_cap + pnl_dollars)

    # Dollar PnL (at 1% risk per trade)
    start_cap_1 = capital_1
    risk_dollars_1 = start_cap_1 * 0.01
    pnl_dollars_1 = risk_dollars_1 * realized_r
    capital_1 = max(0.0, start_cap_1 + pnl_dollars_1)

    executed_trades.append({
        "trade_num": len(executed_trades) + 1,
        "timestamp": dec_dt.isoformat(),
        "exit_timestamp": exit_dt.isoformat(),
        "asset": row["asset"],
        "direction": direction,
        "ob_id": row["ob_id"],
        "ob_width_pct": round(width_pct, 4),
        "tp_regime": "REGIME_A_LE_06" if is_narrow else "REGIME_B_GT_06",
        "entry_price": round(entry_p, 4),
        "sl_price": round(sl_p, 4),
        "tp_price": round(tp_p, 4),
        "risk_distance": round(risk_dist, 4),
        "planned_rr": round(planned_rr, 4),
        "outcome": outcome,
        "realized_r": round(realized_r, 4),
        "starting_capital_10pct": round(start_cap, 4),
        "pnl_dollar_10pct": round(pnl_dollars, 4),
        "ending_capital_10pct": round(capital_10, 4),
        "starting_capital_1pct": round(start_cap_1, 4),
        "pnl_dollar_1pct": round(pnl_dollars_1, 4),
        "ending_capital_1pct": round(capital_1, 4),
        "holding_bars": holding_bars,
        "is_ambiguous": is_ambiguous,
    })

trades_df = pd.DataFrame(executed_trades)
print(f"\n--- SIMULATION RESULTS (Global 1-Trade Lock) ---")
print(f"Total Executed Trades: {len(trades_df)} (from {len(df)} candidate setups)")
print(f"Wins: {len(trades_df[trades_df['realized_r'] > 0])} | Losses: {len(trades_df[trades_df['realized_r'] <= 0])}")
win_rate = len(trades_df[trades_df['realized_r'] > 0]) / len(trades_df) * 100
print(f"Win Rate: {win_rate:.2f}%")
print(f"Expectancy: {trades_df['realized_r'].mean():+.4f}R")
print(f"Total Realized R: {trades_df['realized_r'].sum():+.2f}R")
g_gain = trades_df[trades_df['realized_r'] > 0]['realized_r'].sum()
g_loss = abs(trades_df[trades_df['realized_r'] <= 0]['realized_r'].sum())
pf = g_gain / g_loss if g_loss > 0 else 99.0
print(f"Profit Factor: {pf:.2f}")

print(f"\n--- Compounded Capital Results ($10.00 Base) ---")
print(f"10% Risk Compounding: Ending Capital = ${capital_10:.4f} (Return: {(capital_10 - 10.0)/10.0*100:+.2f}%)")
print(f"1.0% Risk Compounding: Ending Capital = ${capital_1:.4f} (Return: {(capital_1 - 10.0)/10.0*100:+.2f}%)")

# Regime Breakdown
print("\n--- REGIME BREAKDOWN ---")
for reg in ["REGIME_A_LE_06", "REGIME_B_GT_06"]:
    rdf = trades_df[trades_df["tp_regime"] == reg]
    rwins = rdf[rdf["realized_r"] > 0]
    r_wr = len(rwins) / len(rdf) * 100 if len(rdf) > 0 else 0
    r_exp = rdf["realized_r"].mean() if len(rdf) > 0 else 0
    r_tot = rdf["realized_r"].sum() if len(rdf) > 0 else 0
    rg_gain = rwins["realized_r"].sum() if len(rwins) > 0 else 0
    rg_loss = abs(rdf[rdf["realized_r"] <= 0]["realized_r"].sum())
    r_pf = rg_gain / rg_loss if rg_loss > 0 else 99.0
    print(f"{reg:<16} -> N: {len(rdf):>4} | WR: {r_wr:>5.2f}% | Exp: {r_exp:>+6.4f}R | TotR: {r_tot:>+6.2f}R | PF: {r_pf:>4.2f}")
