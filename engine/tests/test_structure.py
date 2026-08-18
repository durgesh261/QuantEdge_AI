"""
Tests for SMC Structure Detection - Streaming API
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType, detect_structure_streaming
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType


class TestStructureDetector:
    """Test pivot detection and structure breaks using streaming API."""

    def create_valid_bullish_candles(self, count: int = 50) -> list[Candle]:
        """Create candles with clear bullish trend (higher highs, higher lows)."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        base_price = Decimal("100")

        for i in range(count):
            # Bullish: each candle closes higher, makes higher highs and higher lows
            # Valid OHLC: low <= open <= close <= high
            open_price = base_price + Decimal(str(i * 0.5))
            high_price = open_price + Decimal("1.5")
            low_price = open_price - Decimal("0.3")   # Low below open
            close_price = open_price + Decimal("1.0")  # Close near high

            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=Decimal("1000"),
            ))

        return candles

    def create_valid_bearish_candles(self, count: int = 50) -> list[Candle]:
        """Create candles with clear bearish trend (lower highs, lower lows)."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        base_price = Decimal("150")

        for i in range(count):
            # Bearish: each candle closes lower, makes lower highs and lower lows
            # Valid OHLC: low <= close <= open <= high
            open_price = base_price - Decimal(str(i * 0.5))
            high_price = open_price + Decimal("0.3")   # High above open
            low_price = open_price - Decimal("1.5")    # Low below close
            close_price = open_price - Decimal("1.0")  # Close near low

            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=Decimal("1000"),
            ))

        return candles

    def test_pivot_detection_bullish(self):
        """Test pivot detection in bullish trend - using streaming API."""
        candles = self.create_valid_bullish_candles(50)
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        all_breaks = []
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            all_breaks.extend(breaks)

        # Get pivots from detector state
        highs, lows = detector.get_confirmed_pivots()

        # Should find alternating high/low pivots
        assert len(highs) > 0 or len(lows) > 0

        # Check pivot ordering
        all_pivots = sorted(highs + lows, key=lambda p: p.index)
        for i in range(1, len(all_pivots)):
            assert all_pivots[i].index > all_pivots[i-1].index

    def test_pivot_detection_bearish(self):
        """Test pivot detection in bearish trend - using streaming API."""
        candles = self.create_valid_bearish_candles(50)
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

        highs, lows = detector.get_confirmed_pivots()
        assert len(highs) > 0 or len(lows) > 0

    def test_structure_breaks_bullish(self):
        """Test BOS detection in bullish trend - using streaming API."""
        candles = self.create_valid_bullish_candles(60)
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        all_breaks = []
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            all_breaks.extend(breaks)

        # Should detect BOS (continuation) in bullish trend
        bos_breaks = [b for b in all_breaks if b.break_type == BreakType.BOS]
        choch_breaks = [b for b in all_breaks if b.break_type == BreakType.CHOCH]

        # In pure bullish trend, expect BOS not CHOCH
        assert len(bos_breaks) > 0

    def test_structure_breaks_reversal(self):
        """Test CHOCH detection on trend reversal - using streaming API."""
        # Create bullish then bearish reversal
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        base_price = Decimal("100")

        # First 30: bullish (valid OHLC)
        for i in range(30):
            open_price = base_price + Decimal(str(i * 0.5))
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_price,
                high=open_price + Decimal("1.5"),
                low=open_price - Decimal("0.3"),  # Valid: low < open
                close=open_price + Decimal("1.0"),
                volume=Decimal("1000"),
            ))

        # Next 30: bearish reversal (valid OHLC)
        for i in range(30, 60):
            open_price = base_price + Decimal("15") - Decimal(str((i-30) * 0.5))
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_price,
                high=open_price + Decimal("0.3"),   # High above open
                low=open_price - Decimal("1.5"),    # Low below close
                close=open_price - Decimal("1.0"),  # Close near low
                volume=Decimal("1000"),
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        all_breaks = []
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            all_breaks.extend(breaks)

        # Should detect at least one CHOCH on reversal
        choch_breaks = [b for b in all_breaks if b.break_type == BreakType.CHOCH]
        assert len(choch_breaks) > 0


class TestPivotPoint:
    """Test PivotPoint model."""

    def test_pivot_point_creation(self):
        """Test creating PivotPoint."""
        candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )

        pivot = PivotPoint(
            index=10,
            timestamp=candle.timestamp,
            price=Decimal("101"),
            is_high=True,
            candle=candle,
        )

        assert pivot.index == 10
        assert pivot.price == Decimal("101")
        assert pivot.is_high
        assert pivot.candle == candle


if __name__ == "__main__":
    pytest.main([__file__, "-v"])