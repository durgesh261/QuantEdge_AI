"""
Tests for Order Block Detection (LuxAlgo Logic)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility, ParsedCandle
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType, OrderBlock


class TestOrderBlockDetector:
    """Test LuxAlgo-style Order Block detection."""

    def create_bullish_break_scenario(self) -> tuple[list[Candle], list[ParsedCandle], StructureBreak]:
        """Create a scenario with clear bullish break for OB testing."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        # Build structure: bearish trend then bullish CHOCH
        # Indices 0-10: bearish (lower highs, lower lows)
        price = Decimal("110")
        for i in range(11):
            open_p = price - Decimal(str(i * 0.5))
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p,
                high=open_p + Decimal("0.5"),
                low=open_p - Decimal("1.5"),
                close=open_p - Decimal("1.0"),
                volume=Decimal("1000"),
            ))

        # Index 11: Bullish break candle (breaks above previous high)
        # Previous high was at index 0: ~110.5
        candles.append(Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=11),
            open=Decimal("104"),
            high=Decimal("111"),  # Breaks above
            low=Decimal("103"),
            close=Decimal("110"),
            volume=Decimal("2000"),
        ))

        # Indices 12-15: continuation
        for i in range(12, 16):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("110") + Decimal(str((i-12)*0.5)),
                high=Decimal("112") + Decimal(str((i-12)*0.5)),
                low=Decimal("109") + Decimal(str((i-12)*0.5)),
                close=Decimal("111") + Decimal(str((i-12)*0.5)),
                volume=Decimal("1000"),
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        # Manually create the break event (bullish CHOCH at index 11)
        break_event = StructureBreak(
            index=11,
            timestamp=candles[11].timestamp,
            price=candles[11].close,
            break_type=BreakType.CHOCH,
            direction=TrendDirection.BULLISH,
            previous_trend=TrendDirection.BEARISH,
            structure_type=StructureType.INTERNAL,
            confirmation_candle=candles[11],
        )

        return candles, parsed, break_event

    def test_bullish_ob_creation(self):
        """Test bullish OB created from bullish break."""
        candles, parsed, break_event = self.create_bullish_break_scenario()

        # Create internal pivots (simplified)
        internal_pivots = [
            PivotPoint(index=0, timestamp=candles[0].timestamp, price=parsed[0].parsed_high, is_high=True, candle=candles[0]),
            PivotPoint(index=5, timestamp=candles[5].timestamp, price=parsed[5].parsed_low, is_high=False, candle=candles[5]),
        ]
        swing_pivots = []

        detector = OrderBlockDetector(OrderBlockConfig(
            internal_length=5,
            swing_length=50,
            atr_period=10,
            atr_multiplier=2.0,
        ))

        obs = detector.detect_order_blocks(
            parsed_candles=parsed,
            internal_breaks=[break_event],
            swing_breaks=[],
            internal_pivots=internal_pivots,
            swing_pivots=swing_pivots,
        )

        assert len(obs) == 1
        ob = obs[0]

        # Verify OB properties
        assert ob.type == "BULLISH"
        assert ob.break_type == BreakType.CHOCH
        assert ob.trend_before_break == TrendDirection.BEARISH
        assert ob.formation_index <= break_event.index  # Formation before break
        assert ob.top_price > ob.bottom_price

    def test_bearish_ob_creation(self):
        """Test bearish OB created from bearish break."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        # Bullish trend then bearish CHOCH
        price = Decimal("90")
        for i in range(11):
            open_p = price + Decimal(str(i * 0.5))
            # Valid bullish OHLC: low <= open <= close <= high
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p,
                high=open_p + Decimal("1.5"),
                low=open_p - Decimal("0.5"),
                close=open_p + Decimal("1.0"),
                volume=Decimal("1000"),
            ))

        # Bearish break at index 11
        candles.append(Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=11),
            open=Decimal("96"),
            high=Decimal("97"),
            low=Decimal("89"),  # Breaks below
            close=Decimal("90"),
            volume=Decimal("2000"),
        ))

        for i in range(12, 16):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("90") - Decimal(str((i-12)*0.5)),
                high=Decimal("91") - Decimal(str((i-12)*0.5)),
                low=Decimal("88") - Decimal(str((i-12)*0.5)),
                close=Decimal("89") - Decimal(str((i-12)*0.5)),
                volume=Decimal("1000"),
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        break_event = StructureBreak(
            index=11,
            timestamp=candles[11].timestamp,
            price=candles[11].close,
            break_type=BreakType.CHOCH,
            direction=TrendDirection.BEARISH,
            previous_trend=TrendDirection.BULLISH,
            structure_type=StructureType.INTERNAL,
            confirmation_candle=candles[11],
        )

        internal_pivots = [
            PivotPoint(index=0, timestamp=candles[0].timestamp, price=parsed[0].parsed_low, is_high=False, candle=candles[0]),
            PivotPoint(index=5, timestamp=candles[5].timestamp, price=parsed[5].parsed_high, is_high=True, candle=candles[5]),
        ]

        detector = OrderBlockDetector(OrderBlockConfig(
            internal_length=5,
            swing_length=50,
            atr_period=10,
            atr_multiplier=2.0,
        ))

        obs = detector.detect_order_blocks(
            parsed_candles=parsed,
            internal_breaks=[break_event],
            swing_breaks=[],
            internal_pivots=internal_pivots,
            swing_pivots=[],
        )

        assert len(obs) == 1
        ob = obs[0]

        assert ob.type == "BEARISH"
        assert ob.break_type == BreakType.CHOCH
        assert ob.trend_before_break == TrendDirection.BULLISH


class TestOrderBlockProperties:
    """Test OrderBlock calculated properties."""

    def test_width_calculation(self):
        """Test OB width calculation."""
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

        ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("101"),
            bottom_price=Decimal("99"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        # Width = 101 - 99 = 2
        assert ob.width == Decimal("2")
        # Width % = (2 / 99) * 100 = ~2.02%
        expected_pct = (Decimal("2") / Decimal("99")) * Decimal("100")
        assert abs(ob.width_percent - expected_pct) < Decimal("0.01")

    def test_entry_price_narrow_ob(self):
        """Test entry price for narrow OB (<= 0.6%)."""
        candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            open=Decimal("100"),
            high=Decimal("100.3"),
            low=Decimal("99.7"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )

        # Width = 0.6, width% = 0.6/99.7 * 100 = ~0.6018% (> 0.6%)
        # So this is actually a WIDE OB, not narrow
        # For narrow test, we need width% <= 0.6
        # 0.6% of 99.7 = 0.5982, so width <= 0.5982
        # Let's use top=100.3, bottom=99.75 -> width=0.55, width%=0.55/99.75*100=0.551% (narrow)
        
        ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("100.3"),
            bottom_price=Decimal("99.75"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        # Narrow bullish: entry = top
        assert ob.calculate_entry_price() == Decimal("100.3")

    def test_entry_price_wide_ob(self):
        """Test entry price for wide OB (> 0.6%)."""
        candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )

        # Width = 4, width% = 4/98 * 100 = ~4.08% (wide)
        ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("102"),
            bottom_price=Decimal("98"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        # Wide bullish: entry = top - 0.25 * width = 102 - 1 = 101
        expected = Decimal("102") - (Decimal("4") * Decimal("0.25"))
        assert ob.calculate_entry_price() == expected

    def test_stop_loss(self):
        """Test stop loss at opposite boundary."""
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

        bullish_ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("101"),
            bottom_price=Decimal("99"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        bearish_ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BEARISH",
            top_price=Decimal("101"),
            bottom_price=Decimal("99"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BEARISH,
        )

        # Bullish SL = bottom
        assert bullish_ob.calculate_stop_loss() == Decimal("99")
        # Bearish SL = top
        assert bearish_ob.calculate_stop_loss() == Decimal("101")

    def test_touch_detection(self):
        """Test OB touch detection."""
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

        ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("101"),
            bottom_price=Decimal("99"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        # Candle that touches OB range (low=100 is within 99-101)
        touch_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("101.5"),
            low=Decimal("100"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
        )
        assert ob.check_touch(touch_candle)

        # Candle that doesn't touch (low=101.5, high=102, both above OB top=101)
        no_touch_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("101.8"),
            high=Decimal("102"),
            low=Decimal("101.5"),
            close=Decimal("101.8"),
            volume=Decimal("1000"),
        )
        assert not ob.check_touch(no_touch_candle)

    def test_invalidation(self):
        """Test OB invalidation."""
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

        bullish_ob = OrderBlock(
            index=10,
            symbol="TEST",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("101"),
            bottom_price=Decimal("99"),
            formation_candle=candle,
            formation_index=10,
            break_index=11,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BULLISH,
        )

        # Close below bottom -> invalidated
        invalid_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("100.5"),
            low=Decimal("98"),
            close=Decimal("98.5"),  # Below bottom (99)
            volume=Decimal("1000"),
        )
        assert bullish_ob.check_invalidation(invalid_candle)

        # Close above bottom -> not invalidated
        valid_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("100.5"),
            low=Decimal("99.5"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        assert not bullish_ob.check_invalidation(valid_candle)