"""
Historical Data Provider Abstraction for QuantEdge Engine.

Provides a pluggable interface for loading historical market data
from various sources (CSV, Parquet, database, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Iterator
import hashlib
import json

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource


@dataclass
class DatasetMetadata:
    """Metadata for a historical dataset."""
    dataset_id: str
    symbol: str
    timeframe: Timeframe
    start_time: datetime
    end_time: datetime
    source: str
    downloaded_at: datetime
    file_hash: str
    schema_version: str = "1.0"
    candle_count: int = 0
    gaps: List[dict] = field(default_factory=list)
    quality_report: dict = field(default_factory=dict)


class HistoricalDataProvider(ABC):
    """Abstract base class for historical data providers."""

    @abstractmethod
    def load_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[Candle]:
        """Load candles for a symbol/timeframe within a time range."""
        pass

    @abstractmethod
    def get_metadata(self, symbol: str, timeframe: Timeframe) -> DatasetMetadata:
        """Get metadata for a dataset."""
        pass

    @abstractmethod
    def validate_dataset(self, symbol: str, timeframe: Timeframe) -> dict:
        """Validate dataset quality and return report."""
        pass


class CsvHistoricalDataProvider(HistoricalDataProvider):
    """
    CSV-based historical data provider.
    
    Expected CSV format:
    timestamp,open,high,low,close,volume
    2024-01-01T00:00:00,50000,50100,49900,50050,1000
    ...
    
    Timestamps should be ISO 8601 format (UTC).
    """

    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
    SUPPORTED_TIMEFRAMES = [Timeframe.H1]

    def __init__(
        self,
        data_root: Path,
        timeframe: Timeframe = Timeframe.H1,
        timezone: str = "UTC"
    ):
        self.data_root = Path(data_root)
        self.default_timeframe = timeframe
        self.timezone = timezone
        self._metadata_cache: dict[str, DatasetMetadata] = {}

    def _get_file_paths(self, symbol: str, timeframe: Timeframe) -> List[Path]:
        """Get the CSV file paths for a symbol/timeframe."""
        symbol_dir = self.data_root / symbol / timeframe.value
        yearly_files = sorted(symbol_dir.glob("*.csv"))
        if yearly_files:
            return yearly_files
        return [symbol_dir / f"{timeframe.value}.csv"]

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse ISO 8601 timestamp string."""
        ts_str = ts_str.strip()
        if "Z" in ts_str:
            ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)

    def _parse_decimal(self, val: str) -> Decimal:
        """Parse decimal value, handling various formats."""
        return Decimal(val.strip())

    def load_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[Candle]:
        """Load candles from CSV files (yearly files)."""
        file_paths = self._get_file_paths(symbol, timeframe)
        
        if not file_paths:
            raise FileNotFoundError(f"No dataset found for {symbol} {timeframe.value}")
        
        candles = []
        for file_path in file_paths:
            if not file_path.exists():
                continue
            
            with open(file_path, "r", encoding="utf-8") as f:
                header = f.readline().strip().split(",")
                header = [h.strip().lower() for h in header]
                
                for col in self.REQUIRED_COLUMNS:
                    if col not in header:
                        raise ValueError(f"Missing required column: {col}")
                
                col_indices = {col: header.index(col) for col in self.REQUIRED_COLUMNS}
                
                for line_num, line in enumerate(f, start=2):
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split(",")
                    if len(parts) < len(self.REQUIRED_COLUMNS):
                        raise ValueError(f"Line {line_num}: insufficient columns")
                    
                    try:
                        timestamp = self._parse_timestamp(parts[col_indices["timestamp"]])
                        
                        if start_time and timestamp < start_time:
                            continue
                        if end_time and timestamp > end_time:
                            continue
                        
                        candle = Candle(
                            symbol=symbol,
                            timeframe=timeframe,
                            timestamp=timestamp,
                            open=self._parse_decimal(parts[col_indices["open"]]),
                            high=self._parse_decimal(parts[col_indices["high"]]),
                            low=self._parse_decimal(parts[col_indices["low"]]),
                            close=self._parse_decimal(parts[col_indices["close"]]),
                            volume=self._parse_decimal(parts[col_indices["volume"]]),
                            source=MarketDataSource.HISTORICAL
                        )
                        candles.append(candle)
                        
                    except Exception as e:
                        raise ValueError(f"Line {line_num}: {e}")
            
        candles.sort(key=lambda c: c.timestamp)
        return candles

    def get_metadata(self, symbol: str, timeframe: Timeframe) -> DatasetMetadata:
        cache_key = f"{symbol}:{timeframe.value}"
        
        if cache_key in self._metadata_cache:
            return self._metadata_cache[cache_key]
        
        file_paths = self._get_file_paths(symbol, timeframe)
        
        if not file_paths:
            raise FileNotFoundError(f"No dataset found for {symbol} {timeframe.value}")
        
        combined_hash = hashlib.sha256()
        for fp in file_paths:
            if fp.exists():
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        combined_hash.update(chunk)
        file_hash = combined_hash.hexdigest()
        
        candles = self.load_candles(symbol, timeframe)
        
        if not candles:
            raise ValueError("Dataset contains no candles")
        
        start_time = candles[0].timestamp
        end_time = candles[-1].timestamp
        
        gaps = self._find_gaps(candles, self.default_timeframe)
        
        metadata = DatasetMetadata(
            dataset_id=f"{symbol}_{timeframe.value}_{file_hash[:16]}",
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            source="csv",
            downloaded_at=datetime.fromtimestamp(file_paths[0].stat().st_mtime),
            file_hash=file_hash,
            candle_count=len(candles),
            gaps=gaps,
            quality_report=self._generate_quality_report(candles)
        )
        
        self._metadata_cache[cache_key] = metadata
        return metadata

    def _find_gaps(self, candles: List[Candle], timeframe: Timeframe) -> List[dict]:
        gaps = []
        if len(candles) < 2:
            return gaps
        
        expected_hours = 1
        
        for i in range(1, len(candles)):
            prev = candles[i-1]
            curr = candles[i]
            delta = curr.timestamp - prev.timestamp
            expected_delta = expected_hours * 3600
            
            if delta.total_seconds() > expected_delta * 1.5:
                gaps.append({
                    "gap_start": prev.timestamp.isoformat(),
                    "gap_end": curr.timestamp.isoformat(),
                    "missing_candles": int(delta.total_seconds() / expected_delta) - 1,
                    "gap_duration_hours": delta.total_seconds() / 3600
                })
        
        return gaps

    def _generate_quality_report(self, candles: List[Candle]) -> dict:
        if not candles:
            return {"status": "empty"}
        
        issues = []
        
        if not candles[0].timestamp < candles[-1].timestamp:
            issues.append("Timestamps not in chronological order")
        
        dup_count = len([c for i, c in enumerate(candles) if i > 0 and c.timestamp == candles[i-1].timestamp])
        if dup_count > 0:
            issues.append(f"Duplicate timestamps: {dup_count}")
        
        return {
            "status": "clean" if not issues else "issues_found",
            "issues": issues,
            "candle_count": len(candles),
            "date_range": {
                "start": candles[0].timestamp.isoformat(),
                "end": candles[-1].timestamp.isoformat()
            }
        }

    def validate_dataset(self, symbol: str, timeframe: Timeframe) -> dict:
        """Validate dataset quality and return report."""
        candles = self.load_candles(symbol, timeframe)
        return self._generate_quality_report(candles)