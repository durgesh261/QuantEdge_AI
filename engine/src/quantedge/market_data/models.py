"""
Market data models for QuantEdge Engine.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from enum import Enum


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class MarketDataSource(str, Enum):
    DELTA = "delta"
    CCXT = "ccxt"
    HISTORICAL = "historical"


@dataclass(frozen=True)
class Candle:
    """Immutable candle/OHLCV data."""
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: MarketDataSource = MarketDataSource.DELTA

    def __post_init__(self):
        # Validate OHLC relationships
        if self.high < self.low:
            raise ValueError("High cannot be less than low")
        if self.high < self.open or self.high < self.close:
            raise ValueError("High must be >= open and close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("Low must be <= open and close")
        if self.volume < 0:
            raise ValueError("Volume cannot be negative")

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def is_doji(self) -> bool:
        return self.open == self.close

    @property
    def body_size(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> Decimal:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> Decimal:
        return min(self.open, self.close) - self.low

    @property
    def range_size(self) -> Decimal:
        return self.high - self.low


@dataclass(frozen=True)
class SymbolInfo:
    """Trading symbol metadata."""
    symbol: str
    base_asset: str
    quote_asset: str
    min_quantity: Decimal
    max_quantity: Decimal
    quantity_step: Decimal
    min_price: Decimal
    max_price: Decimal
    price_step: Decimal
    min_notional: Decimal
    is_active: bool = True