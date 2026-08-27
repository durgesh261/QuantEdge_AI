"""
Scratch script to inspect August 2026 candles and Order Blocks.
"""

from pathlib import Path
import pandas as pd
import numpy as np

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

# Filter for August 2026
aug_mask = (df["dec_dt"] >= "2026-08-01 00:00:00+00:00") & (df["dec_dt"] <= "2026-08-26 23:59:59+00:00")
aug_df = df[aug_mask].sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

print(f"Total August 2026 candidate setups in master dataset: {len(aug_df)}")
print(f"Date range of August setups: {aug_df['dec_dt'].min()} to {aug_df['dec_dt'].max()}")
print("\n--- August 2026 Setups by Asset ---")
print(aug_df["asset"].value_counts())

# Also inspect available raw 1H candle data for August 2026
raw_data_dir = repo_root / "data"
print("\n--- Checking raw candle files in data/ ---")
for f in raw_data_dir.glob("*.csv"):
    try:
        cdf = pd.read_csv(f)
        ts_col = [c for c in cdf.columns if "time" in c.lower() or "date" in c.lower()][0]
        cdf["dt"] = pd.to_datetime(cdf[ts_col], utc=True)
        aug_candles = cdf[(cdf["dt"] >= "2026-08-01") & (cdf["dt"] <= "2026-08-26 23:59:59")]
        print(f"File {f.name}: total {len(cdf)} candles, August candles: {len(aug_candles)} (latest: {cdf['dt'].max()})")
    except Exception as e:
        print(f"File {f.name}: error reading ({e})")
