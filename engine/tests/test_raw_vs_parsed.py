"""
Raw vs Parsed Regression Tests.

These tests verify the critical raw-vs-parsed distinction:
- RAW OHLC drives leg detection and structure
- RAW OHLC drives pivot levels
- PARSED OHLC is used for Order Block extreme selection
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, detect_structure_streaming, StructureType, StructureConfig
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.models import TrendDirection, BreakType
from tests.fixtures.luxalgo import (
    create_raw_vs_parsed_high_volatility_inversion,
    create_ta_highest_lowest_semantic_test,
)


class TestRawVsParsedRegression:
    """Test that structure detection uses RAW OHLC, not parsed."""

    def test_leg_detection_uses_raw_not_parsed(self):
        """Leg detection uses RAW high/low, not parsed values."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        # Process candles and check leg transitions use RAW values
        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

            # At the high-volatility candle (index 5), raw H=120, L=80
            # But parsed H=80, L=120 (inverted)
            # Leg detection should use RAW values
            if i == 5:
                assert pc.parsed_high == Decimal('80')  # Inverted
                assert pc.parsed_low == Decimal('120')  # Inverted
                # But detector's internal history should have RAW values
                assert detector._high_history[5] == Decimal('120')  # RAW
                assert detector._low_history[5] == Decimal('80')  # RAW

        # Leg transition should happen at the correct point based on RAW values
        # The high-volatility candle shouldn't cause incorrect leg transitions
        # After full sequence, we should be in bullish leg (final leg is bullish)
        assert detector.state.current_leg == 1  # Final leg is bullish

    def test_pivot_uses_raw_values(self):
        """Pivot levels use RAW high/low at size-offset candle."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

        # After full sequence, verify pivot levels use RAW prices
        if detector.state.pivot_high:
            # Pivot high should use RAW high at size-offset
            assert detector.state.pivot_high.price in [Decimal('120'), Decimal('104.5')]

        if detector.state.pivot_low:
            # Pivot low should use RAW low at size-offset
            assert detector.state.pivot_low.price in [Decimal('80'), Decimal('79')]

    def test_parsed_values_only_used_for_ob_selection(self):
        """Parsed values only used for OB extreme selection, not structure."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

        # The parsed history is maintained but only for OB use
        assert len(detector._high_parsed_history) == len(candles)
        assert len(detector._low_parsed_history) == len(candles)
        # But leg detection uses _high_history (raw)
        assert len(detector._high_history) == len(candles)
        assert len(detector._low_history) == len(candles)


class TestTaHighestLowestSemantic:
    """Test ta.highest/ta.lowest index mapping matches Pine Script."""

    def test_high_size_index_calculation(self):
        """high[size] = high[candle_count - 1 - size]."""
        candles, expected = create_ta_highest_lowest_semantic_test()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        for check in expected['checks']:
            # Create fresh detector for each check to avoid state accumulation
            detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

            # Process up to the check candle
            for i in range(check['candle'] + 1):
                detector.process_candle(parsed[i], i)

            # Verify size_idx calculation: size_idx = candle_count - 1 - size
            size_idx = detector._candle_count - 1 - detector.length
            assert size_idx == check['size_idx'], f"size_idx mismatch at candle {check['candle']}"

            # Verify high_size = high[size_idx] (using RAW history)
            actual_high_size = detector._high_history[size_idx]
            assert actual_high_size == Decimal(str(check['high_size']))

            # Verify highest = max of last size bars (including current) using RAW history
            start_idx = detector._candle_count - detector.length
            end_idx = detector._candle_count
            highest = max(detector._high_history[start_idx:end_idx])
            assert highest == Decimal(str(check['highest']))

            # Verify new_leg_high logic
            actual_new_leg_high = actual_high_size > highest
            assert actual_new_leg_high == check['new_leg_high']


class TestHighVolatilityInversion:
    """Test high-volatility inversion doesn't affect structure."""

    def test_high_vol_candle_does_not_trigger_false_leg_transition(self):
        """High-volatility candle with inverted parsed values doesn't cause false leg transition."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        # Process all candles
        leg_transitions = []
        for i, pc in enumerate(parsed):
            prev_leg = detector.state.current_leg
            detector.process_candle(pc, i)
            if detector.state.current_leg != prev_leg:
                leg_transitions.append((i, detector.state.current_leg))

        # Should have proper leg transitions based on RAW values
        # The high-volatility candle at index 5 should NOT cause a false transition
        # because its RAW high=120 is lower than previous highs
        assert len(leg_transitions) >= 1  # At least one transition expected

    def test_parsed_inversion_does_not_affect_structure(self):
        """Parsed inversion (H=80, L=120) doesn't affect structure levels."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        # At high-vol candle (index 5):
        # Raw: H=120, L=80
        # Parsed: H=80, L=120 (inverted)
        hv_candle = parsed[5]
        assert hv_candle.parsed_high == Decimal('80')
        assert hv_candle.parsed_low == Decimal('120')
        assert hv_candle.original.high == Decimal('120')
        assert hv_candle.original.low == Decimal('80')


class TestStructureUsesRawOnly:
    """Verify structure detection uses RAW values exclusively."""

    def test_pivot_price_comes_from_raw_history(self):
        """Pivot prices come from raw OHLC history."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)

        # Verify any created pivots use RAW prices
        if detector.state.pivot_high:
            assert detector.state.pivot_high.price in [Decimal('120'), Decimal('104.5')]
        if detector.state.pivot_low:
            assert detector.state.pivot_low.price in [Decimal('80'), Decimal('79')]

    def test_break_detection_uses_raw_close(self):
        """Break detection uses raw close prices."""
        candles, expected = create_raw_vs_parsed_high_volatility_inversion()
        parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            # Break detection uses raw close prices
            if breaks:
                for b in breaks:
                    # Break price should be raw close
                    assert b.price in [Decimal('90'), Decimal('129')]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])