"""
Phase 3D: Download Delta Exchange India BTCUSD 1H OHLCV data.

Source: api.india.delta.exchange/v2/history/candles
Symbol: BTCUSD (Bitcoin Perpetual, USD-margined)
Exchange: Delta Exchange India (exact same feed as TradingView BTCUSD.P)
Resolution: 1h

This is the EXACT same market visible in TradingView under:
    Exchange: Delta Exchange India
    Symbol: BTCUSD.P
    Timeframe: 1H

Run from repo root:
    python engine/download_delta_india_btcusd.py
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
DELTA_INDIA_BASE = "https://api.india.delta.exchange/v2/history/candles"
SYMBOL_DELTA     = "BTCUSD"      # Delta India native symbol
SYMBOL_LOCAL     = "BTCUSD.P"   # Our QuantEdge local storage key

START_DT = datetime(2026, 1, 1,  0, 0, 0, tzinfo=timezone.utc)
END_DT   = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

# Delta API supports up to 2000 candles per request
CHUNK_CANDLES = 2000
CHUNK_SECS    = CHUNK_CANDLES * 3600  # 2000 hours = 83.3 days


def fetch_window(start_ts: int, end_ts: int) -> list:
    """Fetch one window of BTCUSD 1H candles from Delta India API."""
    params = urllib.parse.urlencode({
        "symbol":     SYMBOL_DELTA,
        "resolution": "1h",
        "start":      str(start_ts),
        "end":        str(end_ts),
    })
    url = DELTA_INDIA_BASE + "?" + params
    req = urllib.request.Request(url, headers={
        "Accept":     "application/json",
        "User-Agent": "QuantEdge-Phase3D-Validator/1.0",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if not data.get("success"):
        raise RuntimeError(f"Delta India API error: {data}")
    return data.get("result", [])


def fetch_all(start_ts: int, end_ts: int) -> list:
    """Page through Delta India API to collect all 1H candles."""
    all_candles = []
    cursor_end = end_ts

    print(f"  Symbol   : {SYMBOL_DELTA} (Delta Exchange India)")
    print(f"  Period   : {datetime.fromtimestamp(start_ts, tz=timezone.utc).date()} "
          f"to {datetime.fromtimestamp(end_ts, tz=timezone.utc).date()}")

    while cursor_end > start_ts:
        cursor_start = max(start_ts, cursor_end - CHUNK_SECS)
        try:
            candles = fetch_window(cursor_start, cursor_end)
        except urllib.error.HTTPError as e:
            body = e.read()
            print(f"    [ERROR] HTTP {e.code}: {body[:300]}")
            break
        except Exception as e:
            print(f"    [WARN] {type(e).__name__}: {e} — retrying in 3s")
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
        time.sleep(0.25)

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
    """Run data quality checks. Returns quality report dict."""
    report = {
        "candle_count":    len(candles),
        "first_timestamp": None,
        "last_timestamp":  None,
        "gap_count":       0,
        "max_gap_hours":   0.0,
        "invalid_ohlc":    0,
        "gaps":            [],   # list of (timestamp_utc, gap_hours)
    }
    if not candles:
        return report

    report["first_timestamp"] = datetime.fromtimestamp(
        candles[0]["time"], tz=timezone.utc).isoformat()
    report["last_timestamp"]  = datetime.fromtimestamp(
        candles[-1]["time"], tz=timezone.utc).isoformat()

    prev_ts = None
    for c in candles:
        ts  = c["time"]
        o   = float(c["open"])
        h   = float(c["high"])
        l   = float(c["low"])
        cl  = float(c["close"])

        # OHLC sanity: H >= max(O, C) and L <= min(O, C)
        if h < l or h < o or h < cl or l > o or l > cl:
            report["invalid_ohlc"] += 1

        if prev_ts is not None:
            gap_h = (ts - prev_ts) / 3600
            if gap_h > 1.0:
                report["gap_count"] += 1
                gap_dt = datetime.fromtimestamp(prev_ts + 3600, tz=timezone.utc).isoformat()
                report["gaps"].append({"at": gap_dt, "gap_hours": round(gap_h, 2)})
                if gap_h > report["max_gap_hours"]:
                    report["max_gap_hours"] = gap_h
        prev_ts = ts

    return report


def sha256_candles(candles: list) -> str:
    """Deterministic SHA-256 over all candle data."""
    h = hashlib.sha256()
    for c in candles:
        row = (f"{c['time']},{c['open']},{c['high']},"
               f"{c['low']},{c['close']},{c['volume']}\n")
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


def save_metadata(meta_path: Path, report: dict, sha: str):
    meta = {
        "symbol":           SYMBOL_LOCAL,
        "delta_symbol":     SYMBOL_DELTA,
        "exchange":         "Delta Exchange India (api.india.delta.exchange)",
        "description":      "Bitcoin Perpetual futures, quoted, settled & margined in US Dollar",
        "timeframe":        "1h",
        "source_url":       DELTA_INDIA_BASE,
        "download_utc":     datetime.now(tz=timezone.utc).isoformat(),
        "candle_count":     report["candle_count"],
        "first_timestamp":  report["first_timestamp"],
        "last_timestamp":   report["last_timestamp"],
        "gap_count":        report["gap_count"],
        "max_gap_hours":    report["max_gap_hours"],
        "gaps":             report["gaps"],
        "invalid_ohlc":     report["invalid_ohlc"],
        "sha256":           sha,
        "phase":            "3D",
        "note":             (
            "Phase 3D: Exact Delta Exchange India BTCUSD data. "
            "This is the EXACT same feed as TradingView BTCUSD.P / Delta Exchange India."
        ),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def main():
    start_ts = int(START_DT.timestamp())
    end_ts   = int(END_DT.timestamp())

    print("=" * 60)
    print("Phase 3D: Delta Exchange India BTCUSD 1H Download")
    print("=" * 60)

    candles = fetch_all(start_ts, end_ts)

    if not candles:
        print("[FATAL] No candles downloaded.")
        sys.exit(1)

    report = validate_candles(candles)
    sha    = sha256_candles(candles)

    print()
    print("Data Quality Report:")
    print(f"  Candles       : {report['candle_count']}")
    print(f"  First         : {report['first_timestamp']}")
    print(f"  Last          : {report['last_timestamp']}")
    print(f"  Gaps (>1H)    : {report['gap_count']}"
          f"  (max: {report['max_gap_hours']:.1f}H)")
    if report["gaps"]:
        for g in report["gaps"][:5]:
            print(f"    Gap at {g['at']} : {g['gap_hours']:.1f}H")
    print(f"  Invalid OHLC  : {report['invalid_ohlc']}")
    print(f"  SHA-256       : {sha}")

    # Save to BTCUSD.P/1h/2026_delta_india.csv  (distinct from global BTCUSDT)
    out_csv  = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta_india.csv"
    out_meta = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta_india_metadata.json"

    save_csv(out_csv, candles)
    save_metadata(out_meta, report, sha)

    print()
    print(f"  CSV      -> {out_csv}")
    print(f"  Metadata -> {out_meta.name}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
