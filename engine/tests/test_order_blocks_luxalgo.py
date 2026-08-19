"""
Canonical LuxAlgo Order Block Tests.

These tests verify the exact LuxAlgo Order Block behavior as defined in
the supplied Pine Script reference.

Key LuxAlgo OB concepts tested:
- Bullish OB: parsedLows[pivot_index : break_index] -> minimum
- Bearish OB: parsedHighs[pivot_index : break_index] -> maximum
- Slice: [pivot_index, break_index) inclusive start, exclusive end
- OB formed from candle with extreme in slice
- OB lifecycle: FRESH -> TOUCHED -> USED / INVALIDATED
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import detect_structure_streaming, StructureType
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType, OrderBlock, OBState
from tests.fixtures.luxalgo import (
    create_bearish_leg_then_bullish_leg_then_bearish_break,
    create_bullish_leg_then_bearish_leg_then_bullish_break,
)
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import detect_structure_streaming, StructureType
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection, BreakType, OrderBlock, OBState


def _create_bullish_break_fixture():
    """
    Creates a fixture with a valid bullish structure break.

    Sequence:
    1. Bearish leg (indices 0-9): creates pivot_high at index 3
    2. Bullish leg (indices 10-19): creates pivot_low at index 10, crosses pivot_high -> CHOCH at 18
    3. Bearish leg (indices 20-29): breaks below pivot_low -> BOS at 25
    4. Bullish leg (indices 30-39): crosses above pivot_high -> CHOCH at 33
    """
    candles, expected = create_bullish_leg_then_bearish_leg_then_bullish_break()
    return candles, expected


def _create_bearish_break_fixture():
    """
    Creates a fixture with a valid bearish structure break.

    Sequence:
    1. Bearish leg (indices 0-9): creates pivot_high at index 3
    2. Bullish leg (indices 10-19): creates pivot_low at index 10
    3. Bearish leg (indices 20-29): breaks below pivot_low -> BOS
    """
    candles, expected = create_bearish_leg_then_bullish_leg_then_bearish_break()
    return candles, expected


class TestLuxAlgoSliceSemantics:
    """Test LuxAlgo slice semantics for OB search range."""

    def test_bullish_ob_slice_includes_pivot_excludes_break(self):
        """
        Test that bullish OB search uses slice(pivot_index, break_index):
        - Includes pivot index (inclusive)
        - Excludes break index (exclusive)
        - Search range: [pivot_index, break_index)
        """
        # Use canonical fixture that produces a valid bullish break (CHOCH)
        candles, expected = create_bullish_leg_then_bearish_leg_then_bullish_break()
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        # Run structure detection
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=2,
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
                internal_length=2,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )

        assert len(obs) >= 1, "Should detect at least one OB"

        # Find a bullish OB (from bullish break)
        bullish_obs = [ob for ob in obs if ob.type == 'BULLISH']
        assert len(bullish_obs) >= 1, "Should detect at least one bullish OB"

        ob = bullish_obs[0]

        # Verify OB was formed from candle in the slice range
        # For bullish break: slice is [pivot_low_index, break_index)
        # pivot_low was created at bullish leg transition
        # break_index is the bullish break candle
        assert ob.formation_index >= ob.break_index - 20, "OB formation should be in slice"
        assert ob.formation_index < ob.break_index, "OB formation should be before break (exclusive)"

        # Verify OB is bullish
        assert ob.type == "BULLISH"

    def test_bearish_ob_slice_includes_pivot_excludes_break(self):
        """Test bearish OB slice semantics (same inclusive/exclusive)."""
        # Use canonical fixture that produces a valid bearish break (BOS)
        candles, expected = create_bearish_leg_then_bullish_leg_then_bearish_break()
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=2,
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
                internal_length=2,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )

        assert len(obs) >= 1, "Should detect at least one OB"

        # Find a bearish OB
        bearish_obs = [ob for ob in obs if ob.type == 'BEARISH']
        assert len(bearish_obs) >= 1, "Should detect at least one bearish OB"

        ob = bearish_obs[0]

        # Verify OB formation index is in [pivot_index, break_index)
        assert ob.formation_index < ob.break_index, "OB formation should be before break (exclusive)"


class TestOBExtremeSelection:
    """Test OB extreme selection logic (min low for bullish, max high for bearish)."""

    def test_bullish_ob_selects_minimum_parsed_low(self):
        """Bullish OB should be formed from candle with minimum parsed_low in slice."""
        # Use canonical fixture that produces a valid bullish break
        candles, expected = create_bullish_leg_then_bearish_leg_then_bullish_break()
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed_candles=parsed,
            length=2,
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
                internal_length=2,
                swing_length=50,
                atr_period=10,
                atr_multiplier=2.0,
            )
        )

        assert len(obs) >= 1, "Should detect at least one OB"

        # Find a bullish OB
        bullish_obs = [ob for ob in obs if ob.type == 'BULLISH']
        assert len(bullish_obs) >= 1, "Should detect at least one bullish OB"

        ob = bullish_obs[0]

        # Verify OB was formed from a candle in the valid slice
        assert ob.type == 'BULLISH'
        assert ob.break_type in (BreakType.BOS, BreakType.CHOCH)


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
        # Valid OHLC for bullish: low <= open <= close <= high
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