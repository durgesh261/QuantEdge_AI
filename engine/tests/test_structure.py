"""
Canonical LuxAlgo SMC Structure Tests - Basic Structure API.

These tests verify the basic StructureDetector API using the
canonical LuxAlgo implementation.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType, detect_structure_streaming
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType, StructureType
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType


class TestStructureDetector:
    """Test StructureDetector API with canonical LuxAlgo behavior."""

    def test_detector_initialization(self):
        """Test StructureDetector can be created with different configs."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        assert detector.length == 5
        assert detector.structure_type == StructureType.INTERNAL

        detector2 = StructureDetector(10, StructureType.SWING)
        assert detector2.length == 10
        assert detector2.structure_type == StructureType.SWING

    def test_detector_reset(self):
        """Test detector reset clears all state."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        # Process some candles
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(10):
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
                open=Decimal('100'), high=Decimal('101'),
                low=Decimal('99'), close=Decimal('100'),
                volume=Decimal('1000')
            ))
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)
        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

        detector.reset()
        assert detector.state.current_leg == 0
        assert detector.state.trend == TrendDirection.RANGING
        assert detector.state.pivot_high is None
        assert detector.state.pivot_low is None
        assert detector._candle_count == 0

    def test_get_current_trend(self):
        """Test get_current_trend returns current trend state."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        assert detector.get_current_trend() == TrendDirection.RANGING

    def test_get_last_break(self):
        """Test get_last_break returns last structure break."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        assert detector.get_last_break() is None

    def test_get_confirmed_pivots(self):
        """Test get_confirmed_pivots returns current pivot levels."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        highs, lows = detector.get_confirmed_pivots()
        assert highs == []
        assert lows == []

    def test_get_legs(self):
        """Test get_legs returns empty list (not implemented)."""
        detector = StructureDetector(StructureConfig(length=5, structure_type=StructureType.INTERNAL))
        assert detector.get_legs() == []


class TestStructureDetectorStreaming:
    """Test detect_structure_streaming convenience function."""

    def test_detect_structure_streaming_returns_tuple(self):
        """detect_structure_streaming returns (highs, lows, breaks, trend)."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(20):
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
                open=Decimal('100'), high=Decimal('101'),
                low=Decimal('99'), close=Decimal('100'),
                volume=Decimal('1000')
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)
        highs, lows, breaks, trend = detect_structure_streaming(
            parsed_candles=parsed,
            length=5,
            structure_type=StructureType.INTERNAL
        )

        assert isinstance(highs, list)
        assert isinstance(lows, list)
        assert isinstance(breaks, list)
        assert isinstance(trend, TrendDirection)

    def test_detect_structure_streaming_swing(self):
        """detect_structure_streaming works with SWING structure type."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(60):
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
                open=Decimal('100'), high=Decimal('101'),
                low=Decimal('99'), close=Decimal('100'),
                volume=Decimal('1000')
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
        highs, lows, breaks, trend = detect_structure_streaming(
            parsed_candles=parsed,
            length=50,
            structure_type=StructureType.SWING
        )

        assert isinstance(highs, list)
        assert isinstance(lows, list)
        assert isinstance(breaks, list)
        assert isinstance(trend, TrendDirection)


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


class TestStructureBreak:
    """Test StructureBreak model."""

    def test_structure_break_creation(self):
        """Test creating StructureBreak."""
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

        brk = StructureBreak(
            index=10,
            timestamp=candle.timestamp,
            price=Decimal("101"),
            break_type=BreakType.BOS,
            direction=TrendDirection.BULLISH,
            previous_trend=TrendDirection.RANGING,
            structure_type=StructureType.INTERNAL,
            confirmation_candle=candle,
        )

        assert brk.index == 10
        assert brk.price == Decimal("101")
        assert brk.break_type == BreakType.BOS
        assert brk.direction == TrendDirection.BULLISH
        assert brk.previous_trend == TrendDirection.RANGING
        assert brk.confirmation_candle == candle


class TestTrendDirection:
    """Test TrendDirection enum."""

    def test_trend_direction_values(self):
        assert TrendDirection.BULLISH == "bullish"
        assert TrendDirection.BEARISH == "bearish"
        assert TrendDirection.RANGING == "ranging"


class TestBreakType:
    """Test BreakType enum."""

    def test_break_type_values(self):
        assert BreakType.BOS == "bos"
        assert BreakType.CHOCH == "choch"


class TestStructureType:
    """Test StructureType enum."""

    def test_structure_type_values(self):
        assert StructureType.INTERNAL == "internal"
        assert StructureType.SWING == "swing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])