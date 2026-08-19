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


@dataclass(frozen=True)
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


@dataclass
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
    SUPPORTED_TIMEFRAMES = [Timeframe.H1]  # Start with 1H

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

    def _get_file_path(self, symbol: str, timeframe: Timeframe) -> Path:
        """Get the CSV file path for a symbol/timeframe."""
        symbol_dir = self.data_root / symbol
        return symbol_dir / f"{timeframe.value}.csv"

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse ISO 8601 timestamp string."""
        # Handle various ISO formats
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
        """Load candles from CSV file."""
        file_path = self._get_file_path(symbol, timeframe)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset not found: {file_path}")
        
        candles = []
        with open(file_path, "r", encoding="utf-8") as f:
            # Read header
            header = f.readline().strip().split(",")
            header = [h.strip().lower() for h in header]
            
            # Validate required columns
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
                    
                    # Filter by time range
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
        
        # Sort by timestamp to ensure ordering
        candles.sort(key=lambda c: c.timestamp)
        
        return candles

    def get_metadata(self, symbol: str, timeframe: Timeframe) -> DatasetMetadata:
        """Get or compute dataset metadata."""
        cache_key = f"{symbol}:{timeframe.value}"
        
        if cache_key in self._metadata_cache:
            return self._metadata_cache[cache_key]
        
        file_path = self._get_file_path(symbol, timeframe)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset not found: {file_path}")
        
        file_hash = self._compute_file_hash(file_path)
        
        # Load candles to compute metadata
        candles = self.load_candles(symbol, timeframe)
        
        if not candles:
            raise ValueError("Dataset contains no candles")
        
        start_time = candles[0].timestamp
        end_time = candles[-1].timestamp
        
        # Check for gaps
        gaps = self._find_gaps(candles, self.default_timeframe)
        
        metadata = DatasetMetadata(
            dataset_id=f"{symbol}_{timeframe.value}_{file_hash[:16]}",
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            source="csv",
            downloaded_at=datetime.fromtimestamp(file_path.stat().st_mtime),
            file_hash=file_hash,
            candle_count=len(candles),
            gaps=gaps,
            quality_report=self._generate_quality_report(candles)
        )
        
        self._metadata_cache[cache_key] = metadata
        return metadata

    def _find_gaps(self, candles: List[Candle], timeframe: Timeframe) -> List[dict]:
        """Find gaps in the candle sequence."""
        gaps = []
        if len(candles) < 2:
            return gaps
        
        # Expected interval in hours for 1H timeframe
        expected_hours = 1
        
        for i in range(1, len(candles)):
            prev = candles[i-1]
            curr = candles[i]
            delta = curr.timestamp - prev.timestamp
            expected_delta = expected_hours * 3600  # seconds
            
            if delta.total_seconds() > expected_delta * 1.5:
                gaps.append({
                    "gap_start": prev.timestamp.isoformat(),
                    "gap_end": curr.timestamp.isoformat(),
                    "missing_candles": int(delta.total_seconds() / expected_delta) - 1,
                    "gap_duration_hours": delta.total_seconds() / 3600
                })
        
        return gaps

    def _generate_quality_report(self, candles: List[Candle]) -> dict:
        """Generate data quality report."""
        if not candles:
            return {"status": "empty"}
        
        issues = []
        
        # Check for duplicate timestamps
        timestamps = [c.timestamp for c in candles]
        duplicates = len(timestamps) - len(set(timestamps))
        if duplicates > 0:
            issues.append(f"duplicate_timestamps: {duplicates}")
        
        # Check for invalid OHLC
        invalid_ohlc = 0
        for c in candles:
            if c.high < c.low or c.high < c.open or c.high < c.close:
                invalid_ohlc += 1
            if c.low > c.open or c.low > c.close:
                invalid_ohlc += 1
        if invalid_ohlc > 0:
            issues.append(f"invalid_ohlc: {invalid_ohlc}")
        
        # Check for zero/negative prices
        zero_prices = sum(1 for c in candles if c.close <= 0)
        if zero_prices > 0:
            issues.append(f"zero_or_negative_prices: {zero_prices}")
        
        # Check volume
        zero_volume = sum(1 for c in candles if c.volume == 0)
        
        return {
            "total_candles": len(candles),
            "issues": issues,
            "zero_volume_candles": zero_volume,
            "date_range": {
                "start": candles[0].timestamp.isoformat(),
                "end": candles[-1].timestamp.isoformat()
            }
        }

    def validate_dataset(self, symbol: str, timeframe: Timeframe) -> dict:
        """Validate dataset quality and return detailed report."""
        metadata = self.get_metadata(symbol, timeframe)
        
        return {
            "dataset_id": metadata.dataset_id,
            "valid": len(metadata.quality_report.get("issues", [])) == 0,
            "candle_count": metadata.candle_count,
            "date_range": metadata.quality_report.get("date_range"),
            "issues": metadata.quality_report.get("issues", []),
            "gaps": metadata.gaps,
            "file_hash": metadata.file_hash
        }


class ParquetHistoricalDataProvider(HistoricalDataProvider):
    """
    Parquet-based historical data provider (placeholder for future implementation).
    """
    
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        raise NotImplementedError("Parquet provider not yet implemented")
    
    def load_candles(self, symbol: str, timeframe: Timeframe, 
                     start_time: Optional[datetime] = None, 
                     end_time: Optional[datetime] = None) -> List[Candle]:
        pass
    
    def get_metadata(self, symbol: str, timeframe: Timeframe) -> DatasetMetadata:
        pass
    
    def validate_dataset(self, symbol: str, timeframe: Timeframe) -> dict:
        pass


def create_provider(provider_type: str, data_root: Path, **kwargs) -> HistoricalDataProvider:
    """Factory function to create historical data providers."""
    if provider_type.lower() == "csv":
        return CsvHistoricalDataProvider(data_root, **kwargs)
    elif provider_type.lower() == "parquet":
        return ParquetHistoricalDataProvider(data_root, **kwargs)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")