"""
Inspect all candidate Order Blocks in August 2026 (especially the last 5 days: Aug 21-26).
"""

from pathlib import Path
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"

df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

aug_df = df[df["dec_dt"] >= "2026-08-01 00:00:00+00:00"].sort_values("dec_dt").reset_index(drop=True)
last5d_df = df[df["dec_dt"] >= "2026-08-21 00:00:00+00:00"].sort_values("dec_dt").reset_index(drop=True)

print(f"Total August 2026 OBs: {len(aug_df)}")
print(f"Total Last 5 Days OBs (Aug 21–26): {len(last5d_df)}")

print("\n--- ALL LAST 5 DAYS (AUG 21–26) ORDER BLOCKS ---")
for i, r in last5d_df.iterrows():
    top = float(r['ob_high']) if 'ob_high' in r and not pd.isna(r['ob_high']) else float(r['top_price'])
    bot = float(r['ob_low']) if 'ob_low' in r and not pd.isna(r['ob_low']) else float(r['bottom_price'])
    w = top - bot
    dir_ = r['direction']
    if dir_ == "LONG":
        entry = top - 0.25 * w
        sl = bot
    else:
        entry = bot + 0.25 * w
        sl = top
    sl_pct = abs(entry - sl) / entry * 100.0
    lev = 0.35 / (abs(entry - sl) / entry)
    print(f"#{i+1:02d} | {r['asset']} {dir_} | OB Time: {str(r['dec_dt'])[:16]} | OB: [{bot:.2f}, {top:.2f}] | Entry: {entry:.2f} | SL: {sl:.2f} ({sl_pct:.3f}%) | Lev: {lev:.1f}x")
