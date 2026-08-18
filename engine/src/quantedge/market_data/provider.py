"""
Market data provider abstraction.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Optional

from quantedge.market_data.models import Candle, Timeframe, SymbolInfo, MarketDataSource


class MarketDataProvider(ABC):
    """Abstract base class for market data providers."""

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Candle]:
        """Fetch historical candles."""
        pass

    @abstractmethod
    async def get_latest_candle(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> Candle:
        """Get the most recent completed candle."""
        pass

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Get symbol trading specifications."""
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        """Get current ticker data."""
        pass

    @property
    @abstractmethod
    def source(self) -> MarketDataSource:
        """Return the data source identifier."""
        pass


class DeltaMarketDataProvider(MarketDataProvider):
    """Delta Exchange market data provider."""

    def __init__(self, api_key: str, api_secret: str, base_url: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.testnet = testnet
        self._session = None

    @property
    def source(self) -> MarketDataSource:
        return MarketDataSource.DELTA

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Candle]:
        raise NotImplementedError("Delta provider not yet implemented")

    async def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Candle:
        raise NotImplementedError("Delta provider not yet implemented")

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        raise NotImplementedError("Delta provider not yet implemented")

    async def get_ticker(self, symbol: str) -> dict:
        raise NotImplementedError("Delta provider not yet implemented")


class HistoricalDataProvider(MarketDataProvider):
    """Historical data provider for backtesting."""

    def __init__(self, data_path: str):
        self.data_path = data_path
        self._cache: dict[str, list[Candle]] = {}

    @property
    def source(self) -> MarketDataSource:
        return MarketDataSource.HISTORICAL

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Candle]:
        raise NotImplementedError("Historical provider not yet implemented")

    async def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Candle:
        raise NotImplementedError("Historical provider not yet implemented")

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        raise NotImplementedError("Historical provider not yet implemented")

    async def get_ticker(self, symbol: str) -> dict:
        raise NotImplementedError("Historical provider not yet implemented")