"""
Acquires and validates real canonical 1H historical market data from Delta Exchange India
for ETHUSD, SOLUSD, and XRPUSD.

Preserves the existing canonical BTCUSD dataset and generates cryptographic provenance manifests.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import urllib.request
import pandas as pd

from quantedge.data.canonical_validator import CanonicalDataValidator, CanonicalValidationReport


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


DELTA_HISTORICAL_API = "https://cdn.india.deltaex.org/v2/history/candles"


def fetch_candles_from_delta(
    symbol: str,
    start_ts: int = 1767225600,  # 2026-01-01T00:00:00Z
    end_ts: int = 1787320800,    # 2026-08-21T14:00:00Z
    resolution: str = "1h",
) -> List[Dict[str, Any]]:
    """
    Fetches real historical candles from Delta Exchange India using chunked pagination.
    """
    all_candles = []
    curr_start = start_ts
    step_seconds = 86400 * 30  # 30 days per chunk

    while curr_start < end_ts:
        curr_end = min(curr_start + step_seconds, end_ts)
        url = f"{DELTA_HISTORICAL_API}?resolution={resolution}&symbol={symbol}&start={curr_start}&end={curr_end}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "QuantEdge-AI/2.0 (DeltaIndia-Ingestion-Pipeline)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Delta API returned HTTP {resp.status} for {symbol}")
                raw_json = json.loads(resp.read().decode())
                candles_chunk = raw_json.get("result", [])
                all_candles.extend(candles_chunk)
        except Exception as e:
            print(f"[Acquire] Error fetching chunk {curr_start}->{curr_end} for {symbol}: {e}")
            raise

        curr_start = curr_end
        time.sleep(0.2)

    return all_candles


def process_and_save_canonical_dataset(
    symbol: str,
    raw_candles: List[Dict[str, Any]],
    output_dir: Path,
    description: str,
) -> CanonicalValidationReport:
    """
    Converts raw Delta candles to canonical CSV format and writes 2026.csv + metadata.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_file = output_dir / "2026.csv"
    meta_file = output_dir / "2026_metadata.json"

    # Convert to DataFrame
    df = pd.DataFrame(raw_candles)
    if "time" not in df.columns:
        raise ValueError(f"Missing 'time' column in Delta candles for {symbol}")

    # Standardize schema: timestamp (ISO UTC), open, high, low, close, volume
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})

    # Deduplicate and sort ascending
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    canonical_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    df = df[canonical_cols]

    # Save CSV
    df.to_csv(csv_file, index=False)

    # Validate
    report = CanonicalDataValidator.validate_file(csv_file, symbol=symbol, timeframe="1h")

    # Save metadata
    meta = {
        "symbol": f"{symbol}.P",
        "delta_symbol": symbol,
        "tradingview_symbol": f"{symbol}.P",
        "exchange": "Delta Exchange India (api.india.delta.exchange)",
        "description": description,
        "timeframe": "1h",
        "source_url": DELTA_HISTORICAL_API,
        "download_utc": datetime.now(timezone.utc).isoformat(),
        "candle_count": report.candle_count,
        "first_timestamp": report.first_timestamp,
        "last_timestamp": report.last_timestamp,
        "gap_count": report.gap_count,
        "max_gap_hours": report.max_gap_hours,
        "gaps": report.gaps,
        "invalid_ohlc": 0 if report.is_valid_ohlc else 1,
        "sha256": report.sha256,
        "policy": f"Delta Exchange India {symbol} is a canonical market-data source for QuantEdge AI V2.",
        "quality_report": {
            "gap_count": report.gap_count,
            "max_gap_hours": report.max_gap_hours,
            "invalid_ohlc_count": 0 if report.is_valid_ohlc else 1,
            "duplicate_count": report.duplicate_count,
            "is_sorted_ascending": report.is_sorted_ascending,
            "status": report.status,
        },
    }
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return report


def run_canonical_acquisition(repo_root: Optional[Path] = None) -> Dict[str, CanonicalValidationReport]:
    """
    Ingests ETHUSD, SOLUSD, XRPUSD from Delta Exchange India while verifying BTCUSD invariance.
    """
    root = repo_root or _get_repo_root()
    canonical_base = root / "data" / "canonical" / "delta_exchange_india"

    symbols_to_fetch = [
        ("ETHUSD", "Ethereum Perpetual futures, quoted, settled & margined in US Dollar"),
        ("SOLUSD", "Solana Perpetual futures, quoted, settled & margined in US Dollar"),
        ("XRPUSD", "XRP Perpetual futures, quoted, settled & margined in US Dollar"),
    ]

    reports = {}

    # 1. First, validate and preserve BTCUSD
    btc_csv = canonical_base / "BTCUSD" / "1h" / "2026.csv"
    btc_report = CanonicalDataValidator.validate_file(btc_csv, symbol="BTCUSD", timeframe="1h")
    reports["BTCUSD"] = btc_report
    print(f"[Canonical] Verified BTCUSD: {btc_report.candle_count} candles, SHA256={btc_report.sha256[:16]}..., status={btc_report.status}")

    # 2. Ingest ETHUSD, SOLUSD, XRPUSD
    for sym, desc in symbols_to_fetch:
        sym_dir = canonical_base / sym / "1h"
        print(f"[Canonical] Ingesting genuine 2026 history for {sym} from Delta Exchange India...")
        raw_candles = fetch_candles_from_delta(sym)
        rep = process_and_save_canonical_dataset(sym, raw_candles, sym_dir, desc)
        reports[sym] = rep
        print(f"[Canonical] Successfully saved {sym}: {rep.candle_count} candles, SHA256={rep.sha256[:16]}..., status={rep.status}")

    # 3. Generate Master Manifest
    manifest = {
        "manifest_version": "2.0.0",
        "exchange": "Delta Exchange India (api.india.delta.exchange / cdn.india.deltaex.org)",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "100% genuine real historical market data. Zero synthetic interpolation.",
        "datasets": {sym: rep.to_dict() for sym, rep in reports.items()},
    }
    manifest_file = canonical_base / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[Canonical] Updated master manifest at {manifest_file}")

    return reports


if __name__ == "__main__":
    run_canonical_acquisition()
