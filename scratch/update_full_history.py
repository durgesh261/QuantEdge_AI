"""
Update full_history.csv with latest candles from 2026.csv up to today.
"""

from pathlib import Path
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
base_dir = repo_root / "data" / "canonical" / "delta_exchange_india"

for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    full_path = base_dir / sym / "1h" / "full_history.csv"
    c2026_path = base_dir / sym / "1h" / "2026.csv"
    
    full_df = pd.read_csv(full_path)
    c2026_df = pd.read_csv(c2026_path)
    
    full_df["dt"] = pd.to_datetime(full_df["timestamp"], format="mixed", utc=True)
    c2026_df["dt"] = pd.to_datetime(c2026_df["timestamp"], format="mixed", utc=True)
    
    # Combine and drop duplicates
    combined = pd.concat([full_df, c2026_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    
    # Format timestamp
    combined["timestamp"] = combined["dt"].dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    out_df = combined[cols].copy()
    out_df.to_csv(full_path, index=False)
    print(f"{sym}: full_history.csv updated to {out_df['timestamp'].iloc[-1]} ({len(out_df)} candles)")
