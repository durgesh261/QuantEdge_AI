"""
Script to fetch real candles up to August 26, 2026 from Delta Exchange India API
and update the canonical CSV files for BTCUSD, ETHUSD, SOLUSD, and XRPUSD.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
DELTA_HISTORICAL_API = "https://cdn.india.deltaex.org/v2/history/candles"

symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
now_ts = int(datetime.now(timezone.utc).timestamp())

print("=" * 80)
print(f"FETCHING LATEST CANDLES UP TO {datetime.now(timezone.utc).isoformat()}")
print("=" * 80)

for sym in symbols:
    csv_path = repo_root / "data" / "canonical" / "delta_exchange_india" / sym / "1h" / "2026.csv"
    existing_df = pd.read_csv(csv_path)
    existing_df["dt"] = pd.to_datetime(existing_df["timestamp"], utc=True)
    existing_df["time_ts"] = existing_df["dt"].astype(int) // 10**9
    
    last_ts = int(existing_df["time_ts"].max())
    print(f"\n{sym}: Existing data ends at {existing_df['timestamp'].iloc[-1]} (ts={last_ts})")
    
    # Fetch from last_ts to now_ts
    url = f"{DELTA_HISTORICAL_API}?resolution=1h&symbol={sym}&start={last_ts}&end={now_ts}"
    req = urllib.request.Request(url, headers={"User-Agent": "QuantEdge-AI/2.0", "Accept": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            new_candles = data.get("result", [])
            print(f"  Fetched {len(new_candles)} new candles from Delta API")
            
            if new_candles:
                # Convert new candles to DataFrame
                # Note: Delta returns candles in reverse chronological order
                records = []
                for c in new_candles:
                    c_ts = c["time"]
                    dt_str = datetime.fromtimestamp(c_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
                    records.append({
                        "timestamp": dt_str,
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c["volume"]),
                        "time_ts": c_ts,
                    })
                
                new_df = pd.DataFrame(records)
                combined = pd.concat([existing_df[["timestamp", "open", "high", "low", "close", "volume", "time_ts"]], new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["time_ts"]).sort_values("time_ts").reset_index(drop=True)
                
                # Exclude incomplete currently running candle if volume is incomplete
                # Drop helper time_ts
                final_df = combined[["timestamp", "open", "high", "low", "close", "volume"]].copy()
                final_df.to_csv(csv_path, index=False)
                print(f"  Successfully updated {csv_path.name}: now has {len(final_df)} candles (ends at {final_df['timestamp'].iloc[-1]})")
    except Exception as e:
        print(f"  Error fetching {sym}: {e}")
