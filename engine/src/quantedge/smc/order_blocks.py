"""
Order Block detection per LuxAlgo SMC specification.

Reference implementation logic:
1. Detect structure (internal/swing)
2. Detect structural break (BOS/CHOCH)
3. Determine bullish/bearish bias from break
4. Search relevant parsed range for extreme
5. Create OB from selected candle

For bullish structure: search parsed lows, select minimum
For bearish structure: search parsed highs, select maximum
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from quantedge.market_data.models import Candle
from quantedge.smc.models import (
    OrderBlock, StructureBreak, TrendDirection, BreakType, PivotPoint
)
from quantedge.smc.volatility import ParsedCandle


@dataclass
class OrderBlockConfig:
    internal_length: int = 5
    swing_length: int = 50
    atr_period: int = 200
    atr_multiplier: float = 2.0


class OrderBlockDetector:
    """
    Detects Order Blocks following LuxAlgo methodology.

    Key differences from naive OB detection:
    - Uses volatility-parsed candles (ATR-based)
    - Searches parsed range after structural break
    - Selects extreme (min low for bullish, max high for bearish)
    - Creates OB from the extreme candle, not just "last opposite candle"
    """

    def __init__(self, config: OrderBlockConfig):
        self.config = config

    def detect_order_blocks(
        self,
        parsed_candles: list[ParsedCandle],
        internal_breaks: list[StructureBreak],
        swing_breaks: list[StructureBreak],
        internal_pivots: list[PivotPoint],
        swing_pivots: list[PivotPoint],
    ) -> list[OrderBlock]:
        """
        Detect all order blocks from both internal and swing structures.

        Returns combined list of order blocks with metadata.
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
        parsed_candles: list[ParsedCandle],
        break_event: StructureBreak,
        structure_type: str,
        internal_pivots: list[PivotPoint],
        swing_pivots: list[PivotPoint],
    ) -> Optional[OrderBlock]:
        """
        Create Order Block from a structural break.

        LuxAlgo logic:
        1. Determine search range: from break candle back to relevant pivot
        2. For bullish break: find minimum parsed low in range
        3. For bearish break: find maximum parsed high in range
        4. Create OB from that extreme candle
        """
        break_idx = break_event.index
        is_bullish_break = break_event.direction == TrendDirection.BULLISH

        # Determine search range
        # Search from break back to the pivot that was broken
        search_start = self._find_search_start(break_event, internal_pivots, swing_pivots, structure_type)
        search_end = break_idx

        if search_start >= search_end or search_start < 0:
            return None

        # Search for extreme in parsed range
        if is_bullish_break:
            # Bullish: find minimum parsed low
            extreme_idx = search_start
            extreme_value = parsed_candles[search_start].parsed_low

            for i in range(search_start + 1, search_end + 1):
                if parsed_candles[i].parsed_low < extreme_value:
                    extreme_value = parsed_candles[i].parsed_low
                    extreme_idx = i
        else:
            # Bearish: find maximum parsed high
            extreme_idx = search_start
            extreme_value = parsed_candles[search_start].parsed_high

            for i in range(search_start + 1, search_end + 1):
                if parsed_candles[i].parsed_high > extreme_value:
                    extreme_value = parsed_candles[i].parsed_high
                    extreme_idx = i

        # Create OB from extreme candle
        extreme_candle = parsed_candles[extreme_idx].original

        if is_bullish_break:
            # Bullish OB: from extreme low to extreme high of that candle
            top_price = extreme_candle.high
            bottom_price = extreme_candle.low
            ob_type = "BULLISH"
        else:
            # Bearish OB: from extreme low to extreme high of that candle
            top_price = extreme_candle.high
            bottom_price = extreme_candle.low
            ob_type = "BEARISH"

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

    def _find_search_start(
        self,
        break_event: StructureBreak,
        internal_pivots: list[PivotPoint],
        swing_pivots: list[PivotPoint],
        structure_type: str,
    ) -> int:
        """
        Find the start index for OB search range.

        Searches back from break to the pivot that was broken.
        """
        pivots = swing_pivots if structure_type == "swing" else internal_pivots
        break_idx = break_event.index

        # Find the pivot that was broken
        if break_event.direction == TrendDirection.BULLISH:
            # Bullish break: broke above a pivot high
            # Find the pivot high that was broken
            for pivot in reversed(pivots):
                if pivot.is_high and pivot.index < break_idx:
                    return pivot.index + 1
        else:
            # Bearish break: broke below a pivot low
            for pivot in reversed(pivots):
                if not pivot.is_high and pivot.index < break_idx:
                    return pivot.index + 1

        # Fallback: search from break - length
        length = self.config.swing_length if structure_type == "swing" else self.config.internal_length
        return max(0, break_idx - length)

    def _get_trend_at_index(self, pivots: list[PivotPoint], index: int) -> TrendDirection:
        """Determine trend direction at a given index based on pivot sequence."""
        # Find last two pivots before index
        prior_pivots = [p for p in pivots if p.index < index]
        if len(prior_pivots) < 2:
            return TrendDirection.RANGING

        last_two = prior_pivots[-2:]
        if last_two[0].is_high and not last_two[1].is_high:
            return TrendDirection.BULLISH  # Low then High
        elif not last_two[0].is_high and last_two[1].is_high:
            return TrendDirection.BEARISH  # High then Low

        return TrendDirection.RANGING
