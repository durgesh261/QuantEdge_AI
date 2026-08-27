"""
Scratch script to test fetching latest candles up to today from Delta Exchange India API.
"""

import json
import urllib.request
from datetime import datetime, timezone
import pandas as pd

DELTA_HISTORICAL_API = "https://cdn.india.deltaex.org/v2/history/candles"

symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

# Start ts: Aug 21, 2026 (1787320800) or current epoch
now_ts = int(datetime.now(timezone.utc).timestamp())
print(f"Current UTC timestamp: {now_ts} ({datetime.now(timezone.utc).isoformat()})")

# Let's test fetching candles for BTCUSD
url = f"{DELTA_HISTORICAL_API}?resolution=1h&symbol=BTCUSD&start=1787320800&end={now_ts}"
print(f"Testing URL: {url}")

try:
    req = urllib.request.Request(url, headers={"User-Agent": "QuantEdge-AI/2.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        candles = data.get("result", [])
        print(f"Received {len(candles)} candles from Delta API!")
        if candles:
            print("First candle:", candles[0])
            print("Last candle:", candles[-1])
except Exception as e:
    print(f"Error fetching from Delta API: {e}")
