"""
Download recent 2026 Binance 1H data for Phase 3B TradingView comparison.

TradingView Free shows ~5000 candles. At 1H that is ~208 days.
We download 2026-01-01 to today for BTCUSDT.

Run from repo root:
    python engine/download_2026.py
"""

import sys
import csv
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

ENGINE = Path(__file__).parent
DATA_ROOT = ENGINE / "data" / "historical"

SYMBOLS = {
    "BTCUSD.P": "BTCUSDT",
    "ETHUSD.P": "ETHUSDT",
    "SOLUSD.P": "SOLUSDT",
    "XRPUSD.P": "XRPUSDT",
}

START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
# Today (approx)
END   = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
INTERVAL = "1h"
LIMIT = 1000   # max per Binance request


def fetch_klines(binance_sym: str, start_ms: int, end_ms: int) -> list:
    all_rows = []
    cursor = start_ms
    while cursor < end_ms:
        params = urllib.parse.urlencode({
            "symbol": binance_sym,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": LIMIT,
        })
        url = f"https://api.binance.com/api/v3/klines?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"    [WARN] fetch error: {e}")
            time.sleep(2)
            continue
        if not data:
            break
        all_rows.extend(data)
        last_open_ms = data[-1][0]
        if last_open_ms == cursor:
            break
        cursor = last_open_ms + 1
        time.sleep(0.15)   # rate limit courtesy
    return all_rows


def save_csv(out_path: Path, rows: list, symbol: str) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for r in rows:
            ts = datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc)
            w.writerow([
                ts.isoformat(),
                r[1], r[2], r[3], r[4], r[5],
            ])
    meta = {
        "symbol": symbol,
        "timeframe": "1h",
        "source": "Binance",
        "start": rows[0][0] if rows else None,
        "end": rows[-1][0] if rows else None,
        "candle_count": len(rows),
        "note": "Binance USDT perpetual proxy — NOT Delta Exchange",
    }
    meta_path = out_path.with_name(out_path.stem + "_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return len(rows)


def main():
    start_ms = int(START.timestamp() * 1000)
    end_ms   = int(END.timestamp() * 1000)

    print(f"Phase 3B: Downloading 2026 Binance 1H data")
    print(f"Period: {START.date()} to {END.date()}")
    print(f"Expected candles per symbol: ~{(end_ms - start_ms) // 3_600_000}")
    print()

    for q_sym, b_sym in SYMBOLS.items():
        print(f"  {q_sym} ({b_sym})...")
        rows = fetch_klines(b_sym, start_ms, end_ms)
        if not rows:
            print(f"    [ERROR] no data returned")
            continue
        out_path = DATA_ROOT / q_sym / "1h" / "2026.csv"
        n = save_csv(out_path, rows, q_sym)
        first_ts = datetime.fromtimestamp(rows[0][0]  / 1000, tz=timezone.utc)
        last_ts  = datetime.fromtimestamp(rows[-1][0] / 1000, tz=timezone.utc)
        print(f"    Saved {n} candles: {first_ts.date()} to {last_ts.date()} -> {out_path.name}")

    print("\nDone.")


if __name__ == "__main__":
    main()
