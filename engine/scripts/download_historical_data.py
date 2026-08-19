#!/usr/bin/env python3
"""
Download historical 1H OHLCV data for QuantEdge validation symbols.

Uses ccxt to fetch data from exchanges. Defaults to Binance (which has good
perpetual futures data).

Output: data/historical/{SYMBOL}/1h/{YEAR}.csv

CSV Format:
timestamp,open,high,low,close,volume
"""

import asyncio
import ccxt
import pandas as pd
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sys
import os
import argparse

# Symbol mapping: our internal symbol -> exchange symbol
SYMBOL_MAP = {
    "BTCUSD.P": "BTCUSDT",
    "ETHUSD.P": "ETHUSDT",
    "SOLUSD.P": "SOLUSDT",
    "XRPUSD.P": "XRPUSDT",
}

EXCHANGES = {
    "binance": {
        "class": "binance",
        "options": {"defaultType": "future"},
        "symbol_map": SYMBOL_MAP,
    },
}

DATA_ROOT = Path("data/historical")
TIMEFRAME = "1h"
DEFAULT_START = "2024-01-01"
DEFAULT_END = "2024-12-31"


def create_exchange(exchange_name: str):
    """Create and configure ccxt exchange instance."""
    config = EXCHANGES[exchange_name]
    exchange_class = getattr(ccxt, config["class"])
    exchange = exchange_class({
        "enableRateLimit": True,
        "options": config["options"],
    })
    exchange.load_markets()
    return exchange, config["symbol_map"]


def ohlcv_to_dataframe(ohlcv: list) -> pd.DataFrame:
    """Convert ccxt OHLCV to DataFrame."""
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    return df


def validate_dataframe(df: pd.DataFrame, symbol: str) -> dict:
    """Validate OHLCV data quality."""
    issues = []
    
    if df.empty:
        issues.append("Empty dataframe")
        return {"valid": False, "issues": issues, "row_count": 0}
    
    # Check chronological order
    if not df["timestamp"].is_monotonic_increasing:
        issues.append("Timestamps not monotonically increasing")
    
    # Duplicate timestamps
    dup_count = df["timestamp"].duplicated().sum()
    if dup_count > 0:
        issues.append(f"Duplicate timestamps: {dup_count}")
    
    # Missing OHLC
    null_counts = df[["open", "high", "low", "close", "volume"]].isnull().sum()
    if null_counts.any():
        issues.append(f"Missing values: {null_counts[null_counts > 0].to_dict()}")
    
    # Invalid OHLC relationships
    invalid_ohlc = (
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"])
    ).sum()
    if invalid_ohlc > 0:
        issues.append(f"Invalid OHLC relationships: {invalid_ohlc}")
    
    # Zero/negative prices
    zero_prices = (
        (df["open"] <= 0) | (df["high"] <= 0) | 
        (df["low"] <= 0) | (df["close"] <= 0)
    ).sum()
    if zero_prices > 0:
        issues.append(f"Zero/negative prices: {zero_prices}")
    
    # Timeframe consistency (1H intervals)
    expected_interval = pd.Timedelta(hours=1)
    time_diffs = df["timestamp"].diff().dropna()
    off_interval = (abs(time_diffs - pd.Timedelta(hours=1)) > pd.Timedelta(minutes=5)).sum()
    if off_interval > len(df) * 0.05:
        issues.append(f"Timeframe inconsistency: {off_interval} intervals off")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "row_count": len(df),
        "date_range": {
            "start": df["timestamp"].min().isoformat(),
            "end": df["timestamp"].max().isoformat(),
        }
    }


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def save_dataframe(df: pd.DataFrame, symbol: str, year: int, data_root: Path) -> Path:
    """Save DataFrame to CSV with proper format."""
    symbol_dir = data_root / symbol / "1h"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = symbol_dir / f"{year}.csv"
    
    output_df = df.copy()
    output_df["timestamp"] = output_df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    for col in ["open", "high", "low", "close"]:
        output_df[col] = output_df[col].round(8)
    output_df["volume"] = output_df["volume"].round(8)
    
    output_df.to_csv(filepath, index=False, columns=["timestamp", "open", "high", "low", "close", "volume"])
    
    return filepath


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_metadata(symbol: str, year: int, filepath: Path, df: pd.DataFrame, exchange_name: str, symbol_map: dict) -> dict:
    """Create dataset metadata."""
    file_hash = compute_file_hash(filepath)
    
    return {
        "dataset_id": f"{symbol}_1h_{year}_{compute_file_hash(filepath)[:16]}",
        "symbol": symbol,
        "timeframe": "1h",
        "year": year,
        "source": exchange_name,
        "source_symbol": symbol_map.get(symbol, "unknown"),
        "start_time": df["timestamp"].min().isoformat(),
        "end_time": df["timestamp"].max().isoformat(),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "file_hash": compute_file_hash(filepath),
        "schema_version": "1.0",
        "candle_count": len(df),
        "file_path": str(filepath),
    }


async def download_symbol_data(exchange_name: str, symbol: str, start_date: str, end_date: str, data_root: Path):
    """Download and save historical data for a single symbol."""
    print(f"\n{'='*60}")
    print(f"Downloading {symbol} from {exchange_name}")
    print(f"{'='*60}")
    
    exchange, symbol_map = create_exchange(exchange_name)
    ccxt_symbol = symbol_map.get(symbol)
    
    if not ccxt_symbol:
        raise ValueError(f"Symbol not found in mapping: {symbol}")
    
    print(f"Exchange symbol: {ccxt_symbol}")
    
    # Parse dates
    since = int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ts = int(datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc).timestamp() * 1000)
    
    print(f"Fetching from {start_date} to {end_date}")
    
    all_ohlcv = []
    current_since = since
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    exchange.load_markets()
    
    while current_since < end_ts:
        try:
            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, "1h", since=current_since, limit=1000)
            if not ohlcv:
                break
            
            all_ohlcv.extend(ohlcv)
            last_ts = ohlcv[-1][0]
            current_since = last_ts + 1
            
            if len(ohlcv) < 1000:
                break
            
            await asyncio.sleep(exchange.rateLimit / 1000)
            
            if ohlcv[-1][0] >= end_ts:
                break
                
        except Exception as e:
            print(f"Error fetching: {e}")
            break
    
    if not all_ohlcv:
        print(f"No data returned")
        return
    
    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    
    # Filter to exact date range
    start_ts = pd.Timestamp(start_date, tz="UTC")
    end_ts = pd.Timestamp(end_date, tz="UTC")
    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
    
    print(f"Fetched {len(df)} candles")
    
    # Validate
    quality = validate_dataframe(df, symbol)
    print(f"Validation: {'PASS' if quality['valid'] else 'FAIL'}")
    for issue in quality["issues"]:
        print(f"  ISSUE: {issue}")
    
    # Split by year and save
    for year, group in df.groupby(df["timestamp"].dt.year):
        symbol_dir = data_root / symbol / "1h"
        symbol_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = symbol_dir / f"{year}.csv"
        
        output_df = group.copy()
        output_df["timestamp"] = output_df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S")
        
        for col in ["open", "high", "low", "close"]:
            output_df[col] = output_df[col].round(8)
        output_df["volume"] = output_df["volume"].round(8)
        
        output_df.to_csv(filepath, index=False, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        metadata = create_metadata(symbol, year, filepath, group, exchange_name, symbol_map)
        
        metadata_path = data_root / symbol / "1h" / f"{year}_metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        
        print(f"Saved {len(group)} candles to {filepath}")


async def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data")
    parser.add_argument("--exchange", default="binance", choices=["binance"], help="Exchange to use")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"], help="Symbols to download")
    parser.add_argument("--start", default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--data-root", default="data/historical", help="Data root directory")
    
    args = parser.parse_args()
    
    print(f"Downloading {len(args.symbols)} symbols")
    print(f"Date range: {args.start} to {args.end}")
    print(f"Data root: {args.data_root}")
    
    for symbol in args.symbols:
        await download_symbol_data(args.exchange, symbol, args.start, args.end, Path(args.data_root))
    
    print("\nDone!")


if __name__ == "__main__":
    import argparse
    asyncio.run(main())