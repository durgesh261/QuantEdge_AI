"""
Order Block detection per LuxAlgo SMC specification.

Reference implementation logic (matching LuxAlgo Pine Script):
1. Detect structure (internal/swing) with confirmed pivots
2. Detect structural break (BOS/CHOCH)
3. Determine bullish/bearish bias from break
4. Search relevant parsed range for extreme using LuxAlgo slice semantics:
   - Bullish break: parsedLows.slice(pivot_index, break_index) -> find minimum
   - Bearish break: parsedHighs.slice(pivot_index, break_index) -> find maximum
5. Create OB from selected candle's full range (high to low)

LuxAlgo slice behavior (Pine Script):
- array.slice(from, to) includes 'from', excludes 'to'
- pivot.barIndex is the pivot candle index
- bar_index is the current/break candle index
- So slice(pivot_index, break_index) includes pivot candle, excludes break candle

For OB formation:
- Search range: from broken pivot (inclusive) to break candle (exclusive)
- Bullish OB: find minimum parsed_low in range, OB = that candle's [low, high]
- Bearish OB: find maximum parsed_high in range, OB = that candle's [low, high]
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List
from quantedge.market_data.models import Candle
from quantedge.smc.models import (
    OrderBlock, StructureBreak, TrendDirection, BreakType, PivotPoint, StructureType
)
from quantedge.smc.volatility import ParsedCandle
from quantedge.smc.models import LegState


@dataclass
class OrderBlockConfig:
    internal_length: int = 5
    swing_length: int = 50
    atr_period: int = 200
    atr_multiplier: float = 2.0


class OrderBlockDetector:
    """
    Detects Order Blocks following LuxAlgo methodology exactly.

    Key LuxAlgo behaviors reproduced:
    - Uses volatility-parsed candles (ATR-based)
    - Searches parsed range from broken pivot (inclusive) to break candle (exclusive)
    - Selects extreme: min parsed_low for bullish, max parsed_high for bearish
    - Creates OB from extreme candle's full OHLC range
    - OB top = candle.high, OB bottom = candle.low (always)
    """

    def __init__(self, config: OrderBlockConfig):
        self.config = config

    def detect_order_blocks(
        self,
        parsed_candles: List[ParsedCandle],
        internal_breaks: List[StructureBreak],
        swing_breaks: List[StructureBreak],
        internal_pivots: List[PivotPoint],
        swing_pivots: List[PivotPoint],
    ) -> List[OrderBlock]:
        """
        Detect all order blocks from both internal and swing structures.
        """
        order_blocks = []

        # Process internal structure breaks
        for brk in internal_breaks:
            ob = self._create_order_block_from_break(
                parsed_candles=parsed_candles,
                break_event=brk,
                structure_type="internal",
                internal_pivots=internal_pivots,
                swing_pivots=swing_pivots,
            )
            if ob:
                order_blocks.append(ob)

        # Process swing structure breaks
        for brk in swing_breaks:
            ob = self._create_order_block_from_break(
                parsed_candles=parsed_candles,
                break_event=brk,
                structure_type="swing",
                internal_pivots=internal_pivots,
                swing_pivots=swing_pivots,
            )
            if ob:
                order_blocks.append(ob)

        return order_blocks

    def _create_order_block_from_break(
        self,
        parsed_candles: List[ParsedCandle],
        break_event: StructureBreak,
        structure_type: str,
        internal_pivots: List[PivotPoint],
        swing_pivots: List[PivotPoint],
    ) -> Optional[OrderBlock]:
        """
        Create Order Block from a structural break using LuxAlgo slice semantics.

        LuxAlgo logic:
        1. Determine search range: slice(pivot_index, break_index)
           - pivot_index: the pivot that was broken (inclusive)
           - break_index: the break candle index (exclusive)
        2. For bullish break: find minimum parsed_low in range
        3. For bearish break: find maximum parsed_high in range
        4. Create OB from that extreme candle's full range
        """
        break_idx = break_event.index
        is_bullish_break = break_event.direction == TrendDirection.BULLISH

        # Find the pivot that was broken (matching LuxAlgo: pivot that gave way)
        search_start = self._find_broken_pivot_index(
            break_event, internal_pivots, swing_pivots, structure_type
        )
        search_end = break_idx  # Exclusive per LuxAlgo slice

        if search_start >= search_end or search_start < 0:
            return None

        # Search for extreme in parsed range [search_start, search_end)
        # This matches Pine Script: array.slice(from, to) where 'from' is inclusive, 'to' is exclusive
        if is_bullish_break:
            # Bullish: find minimum parsed_low in [search_start, search_end)
            extreme_idx = search_start
            extreme_value = parsed_candles[search_start].parsed_low

            for i in range(search_start + 1, search_end):
                if parsed_candles[i].parsed_low < extreme_value:
                    extreme_value = parsed_candles[i].parsed_low
                    extreme_idx = i
        else:
            # Bearish: find maximum parsed_high in [search_start, search_end)
            extreme_idx = search_start
            extreme_value = parsed_candles[search_start].parsed_high

            for i in range(search_start + 1, search_end):
                if parsed_candles[i].parsed_high > extreme_value:
                    extreme_value = parsed_candles[i].parsed_high
                    extreme_idx = i

        # Create OB from extreme candle
        extreme_candle = parsed_candles[extreme_idx].original

        # OB always spans the full candle range: [low, high]
        top_price = extreme_candle.high
        bottom_price = extreme_candle.low
        ob_type = "BULLISH" if is_bullish_break else "BEARISH"

        # Determine swing and internal trends at formation
        swing_trend = self._get_trend_at_index(swing_pivots, extreme_idx)
        internal_trend = self._get_trend_at_index(internal_pivots, extreme_idx)

        return OrderBlock(
            index=extreme_idx,
            symbol=extreme_candle.symbol,
            timeframe=extreme_candle.timeframe.value,
            type=ob_type,
            top_price=top_price,
            bottom_price=bottom_price,
            formation_candle=extreme_candle,
            formation_index=extreme_idx,
            break_index=break_idx,
            break_type=break_event.break_type,
            trend_before_break=break_event.previous_trend,
            swing_trend=swing_trend,
            internal_trend=internal_trend,
        )

    def _find_broken_pivot_index(
        self,
        break_event: StructureBreak,
        internal_pivots: List[PivotPoint],
        swing_pivots: List[PivotPoint],
        structure_type: str,
    ) -> int:
        """
        Find the index of the pivot that was broken.

        Matches LuxAlgo: the pivot that gave way to the break.
        For bullish break: the pivot high that was broken
        For bearish break: the pivot low that was broken

        Returns the pivot index (inclusive start for slice).
        """
        pivots = swing_pivots if structure_type == "swing" else internal_pivots
        break_idx = break_event.index

        if break_event.direction == TrendDirection.BULLISH:
            # Bullish break: broke above a pivot high
            # Find the most recent pivot high before break that was exceeded
            for pivot in reversed(pivots):
                if pivot.is_high and pivot.index < break_idx:
                    # Verify this pivot high was actually broken
                    if break_event.price > pivot.price:
                        return pivot.index
        else:
            # Bearish break: broke below a pivot low
            for pivot in reversed(pivots):
                if not pivot.is_high and pivot.index < break_idx:
                    if break_event.price < pivot.price:
                        return pivot.index

        # Fallback: search from break - length
        length = self.config.swing_length if structure_type == "swing" else self.config.internal_length
        return max(0, break_idx - length)

    def _get_trend_at_index(self, pivots: List[PivotPoint], index: int) -> TrendDirection:
        """Determine trend direction at a given index based on pivot sequence."""
        prior_pivots = [p for p in pivots if p.index < index]
        if len(prior_pivots) < 2:
            return TrendDirection.RANGING

        last_two = prior_pivots[-2:]
        if last_two[0].is_high and not last_two[1].is_high:
            return TrendDirection.BULLISH  # Low then High
        elif not last_two[0].is_high and last_two[1].is_high:
            return TrendDirection.BEARISH  # High then Low

        return TrendDirection.RANGING


def detect_order_blocks_streaming(
    parsed_candles: List[ParsedCandle],
    internal_breaks: List[StructureBreak],
    swing_breaks: List[StructureBreak],
    internal_pivots: List[PivotPoint],
    swing_pivots: List[PivotPoint],
    config: OrderBlockConfig
) -> List[OrderBlock]:
    """Convenience function for full OB detection."""
    detector = OrderBlockDetector(config)
    return detector.detect_order_blocks(
        parsed_candles=parsed_candles,
        internal_breaks=internal_breaks,
        swing_breaks=swing_breaks,
        internal_pivots=internal_pivots,
        swing_pivots=swing_pivots,
    )