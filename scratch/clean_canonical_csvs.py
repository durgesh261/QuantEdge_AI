"""
Ensure all canonical CSV files are strictly sorted ascending and formatted with standard timestamp string.
"""

from pathlib import Path
import pandas as pd

base_dir = Path("data/canonical/delta_exchange_india")
symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

for sym in symbols:
    # 1. 2026.csv
    p_2026 = base_dir / sym / "1h" / "2026.csv"
    if p_2026.exists():
        df = pd.read_csv(p_2026)
        df["dt"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True)
        df = df.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
        df["timestamp"] = df["dt"].dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df.to_csv(p_2026, index=False)
        print(f"Cleaned and sorted: {p_2026} ({len(df)} rows)")

    # 2. full_history.csv
    p_full = base_dir / sym / "1h" / "full_history.csv"
    if p_full.exists():
        df = pd.read_csv(p_full)
        df["dt"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True)
        df = df.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
        df["timestamp"] = df["dt"].dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df.to_csv(p_full, index=False)
        print(f"Cleaned and sorted: {p_full} ({len(df)} rows)")
