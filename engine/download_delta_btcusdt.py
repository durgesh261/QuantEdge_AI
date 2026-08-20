"""
Phase 3C: Download Delta Exchange BTCUSDT 1H OHLCV data.

Source: api.delta.exchange/v2/history/candles
Symbol: BTCUSDT (Bitcoin Perpetual futures, USDT-margined)
Exchange: Delta Exchange (global, NOT India endpoint)
Resolution: 1h

Delta API quirks:
- resolution must be string '1h', not integer 3600
- returns candles in DESCENDING order within each response
- max 2000 candles per request
- timestamps are Unix seconds (not milliseconds)

Run from repo root:
    python engine/download_delta_btcusdt.py
"""

import sys
import csv
import json
import time
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

ENGINE = Path(__file__).parent
DATA_ROOT = ENGINE / "data" / "historical"

# ── Target ─────────────────────────────────────────────────────────────────────
DELTA_BASE = "https://api.delta.exchange/v2/history/candles"
SYMBOL_DELTA = "BTCUSDT"         # Delta symbol
SYMBOL_LOCAL  = "BTCUSDT.P"      # Our local storage key

START_DT = datetime(2026, 1, 1,  0, 0, 0, tzinfo=timezone.utc)
END_DT   = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

# Delta max 2000 candles per request; 2000 1H candles = 83.3 days
CHUNK_CANDLES = 2000
CHUNK_SECS    = CHUNK_CANDLES * 3600


def fetch_window(sym: str, start_ts: int, end_ts: int) -> list:
    """Fetch one window of candles from Delta Exchange."""
    params = urllib.parse.urlencode({
        "symbol":     sym,
        "resolution": "1h",
        "start":      str(start_ts),
        "end":        str(end_ts),
    })
    url = DELTA_BASE + "?" + params
    req = urllib.request.Request(url, headers={
        "Accept":     "application/json",
        "User-Agent": "QuantEdge-Validator/1.0",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if not data.get("success"):
        raise RuntimeError(f"Delta API error: {data}")
    return data.get("result", [])


def fetch_all(sym: str, start_ts: int, end_ts: int) -> list:
    """Page through Delta API to collect all 1H candles in [start_ts, end_ts]."""
    all_candles = []
    cursor_end = end_ts

    print(f"  Downloading {sym} 1H: "
          f"{datetime.fromtimestamp(start_ts, tz=timezone.utc).date()} "
          f"to {datetime.fromtimestamp(end_ts, tz=timezone.utc).date()}")

    while cursor_end > start_ts:
        cursor_start = max(start_ts, cursor_end - CHUNK_SECS)
        try:
            candles = fetch_window(sym, cursor_start, cursor_end)
        except urllib.error.HTTPError as e:
            body = e.read()
            print(f"    [ERROR] HTTP {e.code}: {body[:200]}")
            break
        except Exception as e:
            print(f"    [ERROR] {e}")
            time.sleep(3)
            continue

        if not candles:
            print(f"    [WARN] No candles for window {cursor_start}–{cursor_end}")
            cursor_end = cursor_start
            continue

        all_candles.extend(candles)
        oldest_ts = min(c["time"] for c in candles)
        oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
        print(f"    +{len(candles):4d} candles | oldest: {oldest_dt.isoformat()}")
        cursor_end = oldest_ts - 1
        time.sleep(0.25)   # courtesy rate limit

    # Sort ascending, deduplicate
    all_candles.sort(key=lambda c: c["time"])
    seen = set()
    deduped = []
    for c in all_candles:
        ts = c["time"]
        if ts not in seen:
            seen.add(ts)
            deduped.append(c)

    return deduped


def validate_candles(candles: list) -> dict:
    """Run data quality checks and return a report dict."""
    report = {
        "candle_count":      len(candles),
        "first_timestamp":   None,
        "last_timestamp":    None,
        "gap_count":         0,
        "duplicate_count":   0,      # already removed before calling
        "invalid_ohlc":      0,
        "max_gap_hours":     0,
    }
    if not candles:
        return report

    report["first_timestamp"] = datetime.fromtimestamp(
        candles[0]["time"], tz=timezone.utc).isoformat()
    report["last_timestamp"]  = datetime.fromtimestamp(
        candles[-1]["time"], tz=timezone.utc).isoformat()

    prev_ts = None
    for c in candles:
        ts = c["time"]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])

        # OHLC sanity
        if h < l or h < o or h < cl or l > o or l > cl:
            report["invalid_ohlc"] += 1

        # Gap detection (missing 1H bars)
        if prev_ts is not None:
            gap_h = (ts - prev_ts) / 3600
            if gap_h > 1:
                report["gap_count"] += 1
                if gap_h > report["max_gap_hours"]:
                    report["max_gap_hours"] = gap_h
        prev_ts = ts

    return report


def sha256_of_candles(candles: list) -> str:
    """Deterministic SHA-256 of the entire dataset."""
    h = hashlib.sha256()
    for c in candles:
        row = f"{c['time']},{c['open']},{c['high']},{c['low']},{c['close']},{c['volume']}\n"
        h.update(row.encode())
    return h.hexdigest()


def save_csv(out_path: Path, candles: list):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            dt = datetime.fromtimestamp(c["time"], tz=timezone.utc).isoformat()
            w.writerow([dt, c["open"], c["high"], c["low"], c["close"], c["volume"]])
    print(f"    Saved CSV -> {out_path}")


def save_metadata(meta_path: Path, sym: str, candles: list, report: dict):
    sha = sha256_of_candles(candles)
    meta = {
        "symbol":           sym,
        "delta_symbol":     SYMBOL_DELTA,
        "exchange":         "Delta Exchange (global, api.delta.exchange)",
        "description":      "Bitcoin Perpetual futures, quoted, settled & margined in Tether (USDT)",
        "timeframe":        "1h",
        "source_url":       "https://api.delta.exchange/v2/history/candles",
        "download_utc":     datetime.now(tz=timezone.utc).isoformat(),
        "candle_count":     report["candle_count"],
        "first_timestamp":  report["first_timestamp"],
        "last_timestamp":   report["last_timestamp"],
        "gap_count":        report["gap_count"],
        "max_gap_hours":    report["max_gap_hours"],
        "invalid_ohlc":     report["invalid_ohlc"],
        "sha256":           sha,
        "note":             "Phase 3C: Exact Delta Exchange BTCUSDT data for same-market validation",
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"    Saved metadata -> {meta_path.name}")
    return sha


def main():
    start_ts = int(START_DT.timestamp())
    end_ts   = int(END_DT.timestamp())

    print("=" * 60)
    print("Phase 3C: Delta Exchange BTCUSDT 1H Download")
    print("=" * 60)
    print(f"Symbol   : {SYMBOL_DELTA} (Delta Exchange global)")
    print(f"Period   : {START_DT.date()} to {END_DT.date()}")
    print(f"Expected : ~{(end_ts - start_ts) // 3600} candles")
    print()

    candles = fetch_all(SYMBOL_DELTA, start_ts, end_ts)

    if not candles:
        print("[FATAL] No candles downloaded. Check API access.")
        sys.exit(1)

    report = validate_candles(candles)
    sha = sha256_of_candles(candles)

    print()
    print("Data Quality Report:")
    print(f"  Candles       : {report['candle_count']}")
    print(f"  First         : {report['first_timestamp']}")
    print(f"  Last          : {report['last_timestamp']}")
    print(f"  Gaps (>1H)    : {report['gap_count']}  (max gap: {report['max_gap_hours']:.1f}H)")
    print(f"  Invalid OHLC  : {report['invalid_ohlc']}")
    print(f"  SHA-256       : {sha}")
    print()

    out_csv  = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta.csv"
    out_meta = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta_metadata.json"

    save_csv(out_csv, candles)
    sha2 = save_metadata(out_meta, SYMBOL_LOCAL, candles, report)

    print()
    print("Done.")
    print(f"Output: {out_csv}")


if __name__ == "__main__":
    main()
