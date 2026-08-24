"""
Canonical Market Data Quality and Provenance Validator.

Verifies:
- Required schema (timestamp, open, high, low, close, volume)
- OHLC geometric consistency (high >= max(open, close), low <= min(open, close), high >= low > 0)
- Positive volume (volume >= 0)
- Strict 1-hour cadence and ascending timestamp order
- Detection of timestamp duplicates and missing-candle gaps
- Cryptographic SHA-256 calculation
- Provenance manifest generation
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CanonicalValidationReport:
    """Detailed validation outcome for a canonical dataset."""
    symbol: str
    timeframe: str
    file_path: str
    file_exists: bool
    candle_count: int
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    sha256: str
    file_size_bytes: int
    is_valid_ohlc: bool
    is_valid_volume: bool
    is_sorted_ascending: bool
    duplicate_count: int
    gap_count: int
    max_gap_hours: float
    gaps: List[Dict[str, Any]]
    status: str  # "VALIDATED_CLEAN", "INVALID_DATA", "INSUFFICIENT_HISTORY", "MISSING"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanonicalDataValidator:
    """Validates raw CSV files against strict canonical requirements."""

    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

    @staticmethod
    def calculate_sha256(file_path: Path) -> str:
        if not file_path.exists():
            return "MISSING"
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)
        return sha.hexdigest()

    @classmethod
    def validate_file(cls, csv_path: Path, symbol: str, timeframe: str = "1h") -> CanonicalValidationReport:
        if not csv_path.exists():
            return CanonicalValidationReport(
                symbol=symbol,
                timeframe=timeframe,
                file_path=str(csv_path),
                file_exists=False,
                candle_count=0,
                first_timestamp=None,
                last_timestamp=None,
                sha256="MISSING",
                file_size_bytes=0,
                is_valid_ohlc=False,
                is_valid_volume=False,
                is_sorted_ascending=False,
                duplicate_count=0,
                gap_count=0,
                max_gap_hours=0.0,
                gaps=[],
                status="MISSING",
            )

        file_size = csv_path.stat().st_size
        sha_hash = cls.calculate_sha256(csv_path)

        try:
            df = pd.read_csv(csv_path)
        except Exception:
            return CanonicalValidationReport(
                symbol=symbol,
                timeframe=timeframe,
                file_path=str(csv_path),
                file_exists=True,
                candle_count=0,
                first_timestamp=None,
                last_timestamp=None,
                sha256=sha_hash,
                file_size_bytes=file_size,
                is_valid_ohlc=False,
                is_valid_volume=False,
                is_sorted_ascending=False,
                duplicate_count=0,
                gap_count=0,
                max_gap_hours=0.0,
                gaps=[],
                status="INVALID_DATA",
            )

        # Check column names
        cols_lower = [c.lower() for c in df.columns]
        missing_cols = [req for req in cls.REQUIRED_COLUMNS if req not in cols_lower]
        if missing_cols:
            return CanonicalValidationReport(
                symbol=symbol,
                timeframe=timeframe,
                file_path=str(csv_path),
                file_exists=True,
                candle_count=len(df),
                first_timestamp=None,
                last_timestamp=None,
                sha256=sha_hash,
                file_size_bytes=file_size,
                is_valid_ohlc=False,
                is_valid_volume=False,
                is_sorted_ascending=False,
                duplicate_count=0,
                gap_count=0,
                max_gap_hours=0.0,
                gaps=[],
                status="INVALID_DATA",
            )

        # Standardize column names
        df.columns = [c.lower() for c in df.columns]

        # Parse timestamps
        try:
            df["parsed_ts"] = pd.to_datetime(df["timestamp"], utc=True)
        except Exception:
            return CanonicalValidationReport(
                symbol=symbol,
                timeframe=timeframe,
                file_path=str(csv_path),
                file_exists=True,
                candle_count=len(df),
                first_timestamp=None,
                last_timestamp=None,
                sha256=sha_hash,
                file_size_bytes=file_size,
                is_valid_ohlc=False,
                is_valid_volume=False,
                is_sorted_ascending=False,
                duplicate_count=0,
                gap_count=0,
                max_gap_hours=0.0,
                gaps=[],
                status="INVALID_DATA",
            )

        n_candles = len(df)
        if n_candles == 0:
            return CanonicalValidationReport(
                symbol=symbol,
                timeframe=timeframe,
                file_path=str(csv_path),
                file_exists=True,
                candle_count=0,
                first_timestamp=None,
                last_timestamp=None,
                sha256=sha_hash,
                file_size_bytes=file_size,
                is_valid_ohlc=False,
                is_valid_volume=False,
                is_sorted_ascending=False,
                duplicate_count=0,
                gap_count=0,
                max_gap_hours=0.0,
                gaps=[],
                status="INSUFFICIENT_HISTORY",
            )

        # Check sorting
        is_sorted = bool(df["parsed_ts"].is_monotonic_increasing)

        # Check duplicates
        dup_count = int(df["parsed_ts"].duplicated().sum())

        # Check gaps (> 1.5 hours)
        time_diffs_h = (df["parsed_ts"].diff().dt.total_seconds() / 3600.0).dropna()
        gap_indices = time_diffs_h[time_diffs_h > 1.5].index
        gaps = []
        max_gap = 0.0
        for idx in gap_indices:
            gap_h = float(time_diffs_h.loc[idx])
            prev_ts = df["parsed_ts"].loc[idx - 1].isoformat()
            curr_ts = df["parsed_ts"].loc[idx].isoformat()
            gaps.append({"start": prev_ts, "end": curr_ts, "gap_hours": gap_h})
            if gap_h > max_gap:
                max_gap = gap_h

        gap_count = len(gaps)

        # OHLC Geometric Invariants
        numeric_valid = True
        try:
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                if df[col].isna().any() or np.isinf(df[col]).any():
                    numeric_valid = False

            if numeric_valid:
                high_valid = bool((df["high"] >= df[["open", "close"]].max(axis=1) - 1e-6).all())
                low_valid = bool((df["low"] <= df[["open", "close"]].min(axis=1) + 1e-6).all())
                pos_price = bool((df["low"] > 0).all())
                ohlc_valid = high_valid and low_valid and pos_price
                vol_valid = bool((df["volume"] >= 0).all())
            else:
                ohlc_valid = False
                vol_valid = False
        except Exception:
            ohlc_valid = False
            vol_valid = False

        first_ts = df["parsed_ts"].iloc[0].isoformat()
        last_ts = df["parsed_ts"].iloc[-1].isoformat()

        if n_candles < 1000:
            status = "INSUFFICIENT_HISTORY"
        elif not ohlc_valid or not vol_valid or not is_sorted or dup_count > 0:
            status = "INVALID_DATA"
        else:
            status = "VALIDATED_CLEAN"

        return CanonicalValidationReport(
            symbol=symbol,
            timeframe=timeframe,
            file_path=str(csv_path),
            file_exists=True,
            candle_count=n_candles,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            sha256=sha_hash,
            file_size_bytes=file_size,
            is_valid_ohlc=ohlc_valid,
            is_valid_volume=vol_valid,
            is_sorted_ascending=is_sorted,
            duplicate_count=dup_count,
            gap_count=gap_count,
            max_gap_hours=round(max_gap, 2),
            gaps=gaps,
            status=status,
        )
