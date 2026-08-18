"""
Tests for SMC Volatility Parsing (ATR-based)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import calculate_atr, parse_candles_with_volatility, ParsedCandle


class TestATRCalculation:
    """Test ATR calculation with known values."""

    def create_test_candles(self, count: int, base_price: float = 100.0) -> list[Candle]:
        """Create test candles with known OHLC pattern."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        for i in range(count):
            # Create candles with varying ranges
            open_price = base_price + (i * 0.1)
            high_price = open_price + 1.0 + (i % 3) * 0.5
            low_price = open_price - 1.0 - (i % 2) * 0.3
            close_price = open_price + (0.5 if i % 2 == 0 else -0.5)

            candles.append(Candle(
                symbol="BTCUSD.P",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal(str(open_price)),
                high=Decimal(str(high_price)),
                low=Decimal(str(low_price)),
                close=Decimal(str(close_price)),
                volume=Decimal("1000"),
            ))

        return candles

    def test_atr_period_length(self):
        """ATR should return correct length array."""
        candles = self.create_test_candles(250)
        atr_values = calculate_atr(candles, period=200)

        assert len(atr_values) == 250
        # First 199 should be None (not enough data)
        assert atr_values[0] is None
        assert atr_values[198] is None
        # Index 199 (200th candle) should have first ATR
        assert atr_values[199] is not None
        # All subsequent should have values
        assert atr_values[200] is not None
        assert atr_values[249] is not None

    def test_atr_values_positive(self):
        """ATR values should always be positive."""
        candles = self.create_test_candles(250)
        atr_values = calculate_atr(candles, period=200)

        for atr in atr_values[199:]:
            assert atr is not None
            assert atr > Decimal("0")

    def test_atr_wilder_smoothing(self):
        """Test Wilder's smoothing formula with constant TR=2."""
        # Create candles with TR=2: high=101, low=99, prev_close=100
        # TR = max(high-low, high-prev_close, low-prev_close) = max(2, 1, 1) = 2
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        for i in range(210):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("100"),
                high=Decimal("101"),  # Range = 1
                low=Decimal("99"),    # Range = 1
                close=Decimal("100"),
                volume=Decimal("1000"),
            ))

        atr_values = calculate_atr(candles, period=10)

        # First candle TR = high - low = 2
        # Subsequent TR = max(high-low=2, |high-prev_close|=1, |low-prev_close|=1) = 2
        # First ATR at index 9 = average of first 10 TRs = 2.0
        assert atr_values[9] == Decimal("2.0")

        # Subsequent should remain 2.0 (constant TR)
        for i in range(10, 20):
            assert atr_values[i] == Decimal("2.0")


class TestVolatilityParsing:
    """Test LuxAlgo volatility parsing logic."""

    def test_normal_volatility(self):
        """Normal candles should keep high/low as parsed."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        for i in range(210):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5") if i % 2 == 0 else Decimal("99.5"),
                volume=Decimal("1000"),
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=200, atr_multiplier=2.0)

        # After ATR is available, check normal volatility
        for p in parsed[200:]:
            assert not p.is_high_volatility
            assert p.parsed_high == p.original.high
            assert p.parsed_low == p.original.low

    def test_high_volatility_inversion(self):
        """High volatility candles should invert parsed high/low."""
        # Create candles with ATR ~1, then a huge candle with range >= 2*ATR
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        # First 200 normal candles: range=2, so ATR will be ~2
        for i in range(200):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("1000"),
            ))

        # High volatility candle: range=6, ATR~2, 2*ATR=4, 6>=4 -> high volatility
        candles.append(Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=200),
            open=Decimal("100"),
            high=Decimal("106"),  # Range = 6
            low=Decimal("100"),
            close=Decimal("105"),
            volume=Decimal("5000"),
        ))

        parsed = parse_candles_with_volatility(candles, atr_period=200, atr_multiplier=2.0)

        # Last candle should be high volatility
        last_parsed = parsed[-1]
        assert last_parsed.is_high_volatility
        # Inverted: parsed_high = low, parsed_low = high
        assert last_parsed.parsed_high == Decimal("100")  # original low
        assert last_parsed.parsed_low == Decimal("106")   # original high

    def test_insufficient_candles_raises(self):
        """Should raise error for insufficient candles."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        for i in range(100):  # Less than 200+1
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000"),
            ))

        with pytest.raises(ValueError, match="Need at least"):
            parse_candles_with_volatility(candles, atr_period=200)


class TestParsedCandle:
    """Test ParsedCandle dataclass."""

    def test_parsed_candle_creation(self):
        """Test creating ParsedCandle."""
        original = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )

        parsed = ParsedCandle(
            original=original,
            parsed_high=Decimal("101"),
            parsed_low=Decimal("99"),
            is_high_volatility=False,
            atr_value=Decimal("1.5"),
        )

        assert parsed.original == original
        assert parsed.parsed_high == Decimal("101")
        assert parsed.parsed_low == Decimal("99")
        assert not parsed.is_high_volatility
        assert parsed.atr_value == Decimal("1.5")