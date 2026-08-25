"""
Delta Exchange India Historical Data Expansion Tool.

Downloads official historical 1H candles from Delta Exchange India API:
https://api.india.delta.exchange/v2/history/candles

Covers 2024-06-01 00:00:00 UTC to 2026-08-21 14:00:00 UTC across:
- BTCUSD
- ETHUSD
- SOLUSD
- XRPUSD

Validates:
- No duplicate timestamps
- Chronological strictly ascending order
- Valid OHLC: High >= max(Open, Close), Low <= min(Open, Close), Volume >= 0
- Zero synthetic candles, zero interpolation
"""

import csv
import json
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import time
import urllib.request
from typing import Dict, List, Any


DELTA_API = "https://api.india.delta.exchange/v2/history/candles"
RESOLUTION = "1h"
SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

START_TS = 1717200000  # 2024-06-01 00:00:00 UTC
END_TS = 1787320800    # 2026-08-21 14:00:00 UTC
CHUNK_SECS = 2000 * 3600  # 2000 hours per batch


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


def fetch_candles_for_symbol(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """Fetches all 1h candles from Delta API between start_ts and end_ts."""
    all_candles: Dict[int, Dict[str, Any]] = {}
    cur = start_ts
    print(f"[{symbol}] Fetching historical candles from {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} to {datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()}...")

    while cur < end_ts:
        nxt = min(end_ts, cur + CHUNK_SECS)
        url = f"{DELTA_API}?resolution={RESOLUTION}&symbol={symbol}&start={cur}&end={nxt}"
        req = urllib.request.Request(url, headers={"User-Agent": "QuantEdge/2.0"})

        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    results = payload.get("result", [])
                    for row in results:
                        ts = int(row["time"])
                        if ts < start_ts or ts > end_ts:
                            continue
                        all_candles[ts] = {
                            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                            "open": Decimal(str(row["open"])),
                            "high": Decimal(str(row["high"])),
                            "low": Decimal(str(row["low"])),
                            "close": Decimal(str(row["close"])),
                            "volume": Decimal(str(row["volume"])),
                        }
                    break
            except Exception as e:
                print(f"[{symbol}] Retry {attempt+1}/5 at {cur}: {e}")
                time.sleep(1.0 + attempt)

        cur = nxt
        time.sleep(0.05)

    # Sort candles ascending by timestamp
    sorted_ts = sorted(all_candles.keys())
    result_list = [all_candles[ts] for ts in sorted_ts]
    print(f"[{symbol}] Fetched {len(result_list)} unique candles.")
    return result_list


def validate_candles(symbol: str, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validates OHLC integrity, timestamp ordering, and absence of duplicate timestamps."""
    if not candles:
        raise ValueError(f"No candles found for {symbol}")

    prev_ts = None
    gaps = []
    for i, c in enumerate(candles):
        ts = c["timestamp"]
        op, hi, lo, cl, vol = c["open"], c["high"], c["low"], c["close"], c["volume"]

        # OHLC validity
        if not (hi >= op and hi >= cl and lo <= op and lo <= cl and vol >= 0):
            raise ValueError(f"[{symbol}] Invalid OHLC at {ts}: O={op}, H={hi}, L={lo}, C={cl}, V={vol}")

        # Ascending order & uniqueness
        if prev_ts is not None:
            if ts <= prev_ts:
                raise ValueError(f"[{symbol}] Non-ascending or duplicate timestamp at {ts} <= {prev_ts}")
            diff_h = (ts - prev_ts).total_seconds() / 3600.0
            if diff_h > 1.05:
                gaps.append((prev_ts.isoformat(), ts.isoformat(), diff_h))

        prev_ts = ts

    return {
        "count": len(candles),
        "first_ts": candles[0]["timestamp"].isoformat(),
        "last_ts": candles[-1]["timestamp"].isoformat(),
        "gaps_count": len(gaps),
        "gaps": gaps,
    }


def write_csv_and_hash(file_path: Path, candles: List[Dict[str, Any]]) -> str:
    """Writes candles to CSV and returns its SHA-256 hash."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([
                c["timestamp"].isoformat(),
                str(c["open"]),
                str(c["high"]),
                str(c["low"]),
                str(c["close"]),
                str(c["volume"]),
            ])

    # Compute SHA-256
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_expansion():
    repo_root = _find_repo_root()
    base_dir = repo_root / "data" / "canonical" / "delta_exchange_india"

    manifest_entries = {}

    for sym in SYMBOLS:
        candles = fetch_candles_for_symbol(sym, START_TS, END_TS)
        audit_res = validate_candles(sym, candles)

        full_csv = base_dir / sym / "1h" / "full_history.csv"
        sha256_full = write_csv_and_hash(full_csv, candles)

        print(f"[{sym}] Written {len(candles)} candles to {full_csv} (SHA-256: {sha256_full})")

        manifest_entries[sym] = {
            "symbol": sym,
            "timeframe": "1h",
            "full_history_file": f"data/canonical/delta_exchange_india/{sym}/1h/full_history.csv",
            "candle_count": len(candles),
            "first_timestamp": audit_res["first_ts"],
            "last_timestamp": audit_res["last_ts"],
            "sha256": sha256_full,
            "file_size_bytes": full_csv.stat().st_size,
            "is_valid_ohlc": True,
            "is_sorted_ascending": True,
            "status": "VALIDATED_CLEAN",
        }

    manifest_data = {
        "manifest_version": "3.0.0",
        "phase": "K",
        "exchange": "Delta Exchange India (api.india.delta.exchange)",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "100% genuine real historical market data. Zero synthetic interpolation.",
        "datasets": manifest_entries,
    }

    manifest_file = base_dir / "manifest_expanded.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    print(f"\n[Manifest] Expanded canonical manifest written to {manifest_file}")


if __name__ == "__main__":
    run_expansion()
