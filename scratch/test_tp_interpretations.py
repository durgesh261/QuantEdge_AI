"""
Scratch script to analyze:
1. TP = 0.60R (60% of zone width) vs TP = 1.714R (60/35 ROE target)
2. Raw SMC vs AI Filtered (Phase T Ridge) under the new TP rule
"""

from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

# Test Interpretation 2: TP = 0.60 * Risk Distance (0.60R) for Regime A, and 1.0R for Regime B
current_time = None
trades_interp2 = []
capital = 10.0

for i, row in df.iterrows():
    dec_dt = row["dec_dt"]
    if current_time is not None and dec_dt < current_time:
        continue

    width_pct = float(row["feat_ob_width_pct"])
    is_narrow = width_pct <= 0.6
    
    # Interpretation 2:
    # Regime A: TP = 0.60R
    # Regime B: TP = 1.00R
    planned_rr = 0.60 if is_narrow else 1.00
    
    mfe_r = float(row["mfe_r"])
    mae_r = float(row["mae_r"])
    holding_bars = int(row["holding_bars"])
    
    if mfe_r >= planned_rr and mae_r < 1.0:
        outcome = "TP_HIT"
        realized_r = planned_rr
    elif mae_r >= 1.0 and mfe_r < planned_rr:
        outcome = "SL_HIT"
        realized_r = -1.0
    elif mfe_r >= planned_rr and mae_r >= 1.0:
        outcome = "SL_HIT"
        realized_r = -1.0
    else:
        outcome = "TIMEOUT"
        realized_r = min(planned_rr, max(-1.0, mfe_r - mae_r * 0.5))

    exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)
    current_time = exit_dt

    risk_dollars = capital * 0.10
    pnl = risk_dollars * realized_r
    capital = max(0.0, capital + pnl)

    trades_interp2.append({
        "ob_id": row["ob_id"],
        "asset": row["asset"],
        "width_pct": width_pct,
        "regime": "REGIME_A_LE_06" if is_narrow else "REGIME_B_GT_06",
        "planned_rr": planned_rr,
        "outcome": outcome,
        "realized_r": realized_r,
    })

t2_df = pd.DataFrame(trades_interp2)
print("=" * 80)
print("INTERPRETATION 2: TP = 0.60R for Width <= 0.6%, TP = 1.0R for Width > 0.6%")
print("=" * 80)
print(f"Total Trades: {len(t2_df)}")
wins = t2_df[t2_df["realized_r"] > 0]
print(f"Wins: {len(wins)} | WR: {len(wins)/len(t2_df)*100:.2f}% | Exp: {t2_df['realized_r'].mean():+.4f}R | TotR: {t2_df['realized_r'].sum():+.2f}R")
g_gain = wins["realized_r"].sum()
g_loss = abs(t2_df[t2_df["realized_r"] <= 0]["realized_r"].sum())
print(f"Profit Factor: {g_gain/g_loss:.2f}")

for reg in ["REGIME_A_LE_06", "REGIME_B_GT_06"]:
    rdf = t2_df[t2_df["regime"] == reg]
    rw = rdf[rdf["realized_r"] > 0]
    wr = len(rw) / len(rdf) * 100 if len(rdf) > 0 else 0
    exp = rdf["realized_r"].mean() if len(rdf) > 0 else 0
    tot = rdf["realized_r"].sum() if len(rdf) > 0 else 0
    gg = rw["realized_r"].sum() if len(rw) > 0 else 0
    gl = abs(rdf[rdf["realized_r"] <= 0]["realized_r"].sum())
    pf = gg / gl if gl > 0 else 99.0
    print(f"{reg:<16} -> N: {len(rdf):>4} | WR: {wr:>5.2f}% | Exp: {exp:>+6.4f}R | TotR: {tot:>+6.2f}R | PF: {pf:>4.2f}")
