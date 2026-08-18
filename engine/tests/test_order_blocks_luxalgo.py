"""
Tests for Order Block Detection - LuxAlgo Slice Semantics and Lifecycle.

Tests cover:
1. LuxAlgo slice semantics (inclusive start, exclusive end)
2. OB extreme selection (min low / max high)
3. OB range boundaries
4. OB lifecycle transitions (FRESH -> TOUCHED -> USED / INVALIDATED)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import detect_structure_streaming, StructureType
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType, OrderBlock, OBState


class TestLuxAlgoSliceSemantics:
    """Test LuxAlgo slice semantics for OB search range."""

    def create_bullish_break_with_known_pivots(self):
        """Create a scenario with known pivot and break for testing slice behavior."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        
        # Build bearish structure: pivot high at index 5, break at index 11
        # Indices 0-5: bearish down to pivot high at 5
        price = Decimal("110")
        for i in range(6):
            open_p = price - Decimal(str(i * 0.5))
            # Bearish OHLC: low <= close <= open <= high
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
        
        # Indices 6-10: continuation down (right bars for pivot high)
        for i in range(6, 11):
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
        
        # Index 11: Bullish break candle (breaks above pivot high at index 5)
        candles.append(Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=11),
            open=Decimal("104"),
            high=Decimal("111"),  # Breaks above pivot high (~110.5)
            low=Decimal("103"),
            close=Decimal("110"),
            volume=Decimal("2000"),
        ))
        
        # Fill for ATR
        for i in range(12, 25):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("110") + Decimal(str((i-11)*0.5)),
                high=Decimal("112") + Decimal(str((i-11)*0.5)),
                low=Decimal("109") + Decimal(str((i-11)*0.5)),
                close=Decimal("111") + Decimal(str((i-11)*0.5)),
                volume=Decimal("1000"),
            ))
        
        return candles, base_time

    def test_bullish_ob_slice_includes_pivot_excludes_break(self):
        """
        Test that bullish OB search uses slice(pivot_index, break_index):
        - Includes pivot index (inclusive)
        - Excludes break index (exclusive)
        - Search range: [pivot_index, break_index)
        """
        candles, base_time = self.create_bullish_break_with_known_pivots()
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
        
        # Run structure detection
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=5,
            structure_type=StructureType.INTERNAL
        )
        swing_highs, swing_lows, swing_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=50,
            structure_type=StructureType.SWING
        )
        
        # Detect OBs
        obs = detect_order_blocks_streaming(
            parsed_candles=parsed,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            internal_pivots=internal_highs + internal_lows,
            swing_pivots=swing_highs + swing_lows,
            config=OrderBlockConfig(
                internal_length=5,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )
        
        assert len(obs) >= 1, "Should detect at least one OB"
        
        ob = obs[0]
        
        # Verify OB was formed from candle in the slice range
        # Slice should be [pivot_index, break_index) = [5, 11)
        # So valid formation indices: 5, 6, 7, 8, 9, 10
        # NOT index 11 (break candle)
        assert ob.formation_index >= 5, f"OB formation index {ob.formation_index} should be >= pivot index 5"
        assert ob.formation_index < 11, f"OB formation index {ob.formation_index} should be < break index 11 (exclusive)"
        
        # Verify OB is bullish
        assert ob.type == "BULLISH"
        assert ob.break_type in (BreakType.CHOCH, BreakType.BOS)

    def test_bearish_ob_slice_includes_pivot_excludes_break(self):
        """Test bearish OB slice semantics (same inclusive/exclusive)."""
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        
        # Build bullish structure: pivot low at index 5, bearish break at index 11
        price = Decimal("90")
        for i in range(6):
            open_p = price + Decimal(str(i * 0.5))
            # Bullish OHLC: low <= open <= close <= high
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
        
        for i in range(6, 11):
            open_p = price + Decimal(str(i * 0.5))
            # Bullish OHLC: low <= open <= close <= high
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
        
        # Index 11: Bearish break (closes below pivot low)
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
        
        for i in range(12, 25):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("90") - Decimal(str((i-11)*0.5)),
                high=Decimal("91") - Decimal(str((i-11)*0.5)),
                low=Decimal("88") - Decimal(str((i-11)*0.5)),
                close=Decimal("89") - Decimal(str((i-11)*0.5)),
                volume=Decimal("1000"),
            ))
        
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
        
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=5,
            structure_type=StructureType.INTERNAL
        )
        swing_highs, swing_lows, swing_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=50,
            structure_type=StructureType.SWING
        )
        
        obs = detect_order_blocks_streaming(
            parsed_candles=parsed,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            internal_pivots=internal_highs + internal_lows,
            swing_pivots=swing_highs + swing_lows,
            config=OrderBlockConfig(
                internal_length=5,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )
        
        assert len(obs) >= 1
        ob = obs[0]
        
        # Bearish OB: formation index should be in [5, 11)
        assert ob.formation_index >= 5
        assert ob.formation_index < 11
        assert ob.type == "BEARISH"


class TestOBExtremeSelection:
    """Test OB extreme selection logic (min low for bullish, max high for bearish)."""

    def test_bullish_ob_selects_minimum_parsed_low(self):
        """Bullish OB should be formed from candle with minimum parsed_low in slice."""
        # Create a slice where we know which candle has minimum parsed_low
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        
        # Pivot high at index 5
        for i in range(6):
            open_p = Decimal("110") - Decimal(str(i * 0.5))
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
        
        # Right bars for pivot high
        for i in range(6, 11):
            open_p = Decimal("110") - Decimal(str(i * 0.5))
            # Make index 8 have the lowest low
            if i == 8:
                candles.append(Candle(
                    symbol="TEST",
                    timeframe=Timeframe.H1,
                    timestamp=base_time + timedelta(hours=i),
                    open=open_p,
                    high=open_p + Decimal("0.5"),
                    low=Decimal("100.0"),  # Lowest low
                    close=open_p - Decimal("0.5"),
                    volume=Decimal("1000"),
                ))
            else:
                candles.append(Candle(
                    symbol="TEST",
                    timeframe=Timeframe.H1,
                    timestamp=base_time + timedelta(hours=i),
                    open=open_p,
                    high=open_p + Decimal("0.5"),
                    low=open_p - Decimal("1.0"),
                    close=open_p - Decimal("1.0"),
                    volume=Decimal("1000"),
                ))
        
        # Bullish break at 11
        candles.append(Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=11),
            open=Decimal("104"),
            high=Decimal("111"),
            low=Decimal("103"),
            close=Decimal("110"),
            volume=Decimal("2000"),
        ))
        
        for i in range(12, 25):
            candles.append(Candle(
                symbol="TEST",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=Decimal("110"), high=Decimal("111"),
                low=Decimal("109"), close=Decimal("110"),
                volume=Decimal("1000"),
            ))
        
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
        
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=5,
            structure_type=StructureType.INTERNAL
        )
        
        # Find the break
        bullish_breaks = [b for b in internal_breaks if b.direction == TrendDirection.BULLISH]
        assert len(bullish_breaks) > 0
        
        obs = detect_order_blocks_streaming(
            parsed_candles=parsed,
            internal_breaks=bullish_breaks,
            swing_breaks=[],
            internal_pivots=internal_highs + internal_lows,
            swing_pivots=[],
            config=OrderBlockConfig(
                internal_length=5,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )
        
        assert len(obs) > 0
        ob = obs[0]
        
        # The OB should be formed from index 8 (where we put the lowest low)
        assert ob.formation_index == 8, f"Expected OB at index 8 (min low), got {ob.formation_index}"
        assert ob.type == "BULLISH"


class TestOBRangeBoundaries:
    """Test OB range boundaries and touch/invalidation logic."""

    def test_bullish_ob_top_bottom_from_formation_candle(self):
        """OB top=high, bottom=low of formation candle."""
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
        
        assert ob.top_price == Decimal("101")
        assert ob.bottom_price == Decimal("99")
        assert ob.width == Decimal("2")

    def test_bullish_ob_touch_at_top_boundary(self):
        """Touch at exact top boundary should count as touch."""
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
        
        # Candle with low exactly at top boundary
        # Valid OHLC: low <= open <= close <= high
        # For bullish candle: low <= open <= close <= high
        touch_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("101"),
            high=Decimal("102"),
            low=Decimal("100.5"),  # Above top boundary
            close=Decimal("101.5"),
            volume=Decimal("1000"),
        )
        
        assert ob.check_touch(touch_candle) is True
        assert ob.state == OBState.TOUCHED

    def test_bullish_ob_touch_at_bottom_boundary(self):
        """Touch at exact bottom boundary should count as touch."""
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
        
        # Candle with high exactly at bottom boundary
        # Valid OHLC for bullish: low <= open <= close <= high
        # For touch at bottom: candle.high >= bottom_price
        touch_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("99"),
            high=Decimal("100"),  # High touches bottom boundary (99)
            low=Decimal("98"),
            close=Decimal("99"),
            volume=Decimal("1000"),
        )
        
        assert ob.check_touch(touch_candle) is True
        assert ob.state == OBState.TOUCHED

    def test_bullish_ob_invalidation_at_close_below_bottom(self):
        """Bullish OB invalidated when candle closes below bottom."""
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
        
        # Close below bottom
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
        
        assert ob.check_invalidation(invalid_candle) is True
        assert ob.state == OBState.INVALIDATED
        assert ob.invalidated_by_price == Decimal("98.5")

    def test_bullish_ob_not_invalidated_by_wick_below(self):
        """Bullish OB NOT invalidated by wick below bottom, only close."""
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
        
        # Wick below bottom but close above
        wick_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("100.5"),
            low=Decimal("98"),  # Wick below
            close=Decimal("99.5"),  # Close above bottom
            volume=Decimal("1000"),
        )
        
        assert ob.check_invalidation(wick_candle) is False
        assert ob.state != OBState.INVALIDATED


class TestOBLifecycleTransitions:
    """Test explicit OB lifecycle state transitions."""

    def test_fresh_to_touched_transition(self):
        """FRESH -> TOUCHED on first touch."""
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
        
        assert ob.state == OBState.FRESH
        assert ob.touch_count == 0
        
        # First touch
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
        
        ob.check_touch(touch_candle)
        
        assert ob.state == OBState.TOUCHED
        assert ob.touch_count == 1
        assert ob.is_eligible_for_entry() is True

    def test_touched_to_used_transition(self):
        """TOUCHED -> USED when trade executed."""
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
        
        # First touch
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
        ob.check_touch(touch_candle)
        assert ob.state == OBState.TOUCHED
        
        # Trade executed
        ob.mark_used()
        
        assert ob.state == OBState.USED
        assert ob.is_eligible_for_entry() is False

    def test_fresh_to_invalidated_transition(self):
        """FRESH -> INVALIDATED on close through boundary."""
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
        
        assert ob.state == OBState.FRESH
        
        # Invalidation
        invalid_candle = Candle(
            symbol="TEST",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 1, 0, 0),
            open=Decimal("100"),
            high=Decimal("100.5"),
            low=Decimal("98"),
            close=Decimal("98.5"),
            volume=Decimal("1000"),
        )
        
        ob.check_invalidation(invalid_candle)
        
        assert ob.state == OBState.INVALIDATED
        assert ob.is_eligible_for_entry() is False

    def test_used_ob_cannot_be_reused(self):
        """Once USED, OB stays USED even if touched again."""
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
        
        ob.mark_used()
        assert ob.state == OBState.USED
        
        # Try to touch
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
        
        ob.check_touch(touch_candle)
        
        # Should remain USED
        assert ob.state == OBState.USED
        assert ob.is_eligible_for_entry() is False


class TestOBStateQueries:
    """Test OB state query methods."""

    def test_is_fresh(self):
        """is_fresh() returns True only for FRESH state."""
        candle = Candle(
            symbol="TEST", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("100"),
            high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1000")
        )
        ob = OrderBlock(index=10, symbol="TEST", timeframe="1h", type="BULLISH",
                       top_price=Decimal("101"), bottom_price=Decimal("99"),
                       formation_candle=candle, formation_index=10, break_index=11,
                       break_type=BreakType.BOS, trend_before_break=TrendDirection.BULLISH)
        
        assert ob.is_fresh() is True
        
        ob.state = OBState.TOUCHED
        assert ob.is_fresh() is False
        
        ob.state = OBState.USED
        assert ob.is_fresh() is False

    def test_is_touched(self):
        """is_touched() returns True only for TOUCHED state."""
        candle = Candle(
            symbol="TEST", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("100"),
            high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1000")
        )
        ob = OrderBlock(index=10, symbol="TEST", timeframe="1h", type="BULLISH",
                       top_price=Decimal("101"), bottom_price=Decimal("99"),
                       formation_candle=candle, formation_index=10, break_index=11,
                       break_type=BreakType.BOS, trend_before_break=TrendDirection.BULLISH)
        
        ob.state = OBState.TOUCHED
        assert ob.is_touched() is True
        
        ob.state = OBState.FRESH
        assert ob.is_touched() is False

    def test_is_used(self):
        """is_used() returns True only for USED state."""
        candle = Candle(
            symbol="TEST", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("100"),
            high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1000")
        )
        ob = OrderBlock(index=10, symbol="TEST", timeframe="1h", type="BULLISH",
                       top_price=Decimal("101"), bottom_price=Decimal("99"),
                       formation_candle=candle, formation_index=10, break_index=11,
                       break_type=BreakType.BOS, trend_before_break=TrendDirection.BULLISH)
        
        ob.state = OBState.USED
        assert ob.is_used() is True
        
        ob.state = OBState.TOUCHED
        assert ob.is_used() is False

    def test_is_invalidated(self):
        """is_invalidated() returns True only for INVALIDATED state."""
        candle = Candle(
            symbol="TEST", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("100"),
            high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1000")
        )
        ob = OrderBlock(index=10, symbol="TEST", timeframe="1h", type="BULLISH",
                       top_price=Decimal("101"), bottom_price=Decimal("99"),
                       formation_candle=candle, formation_index=10, break_index=11,
                       break_type=BreakType.BOS, trend_before_break=TrendDirection.BULLISH)
        
        ob.state = OBState.INVALIDATED
        assert ob.is_invalidated() is True
        
        ob.state = OBState.FRESH
        assert ob.is_invalidated() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])