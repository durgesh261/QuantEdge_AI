"""
Swing and Internal Structure detection per LuxAlgo SMC.

Reference: LuxAlgo uses pivot-based structure with configurable lengths.
Internal structure length = 5, Swing structure length = 50 (defaults).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import numpy as np

from quantedge.market_data.models import Candle
from quantedge.smc.models import (
    PivotPoint, StructureBreak, TrendDirection, BreakType, StructureType
)
from quantedge.smc.volatility import ParsedCandle


@dataclass
class StructureConfig:
    length: int
    structure_type: StructureType


class StructureDetector:
    """
    Detects market structure (swing/internal) using pivot points.

    LuxAlgo approach:
    - Find pivot highs/lows using left/right lookback
    - Track trend direction based on pivot sequence
    - Detect BOS/CHOCH when price breaks opposite pivot
    """

    def __init__(self, config: StructureConfig):
        self.config = config
        self.length = config.length

    def find_pivots(self, parsed_candles: list[ParsedCandle]) -> list[PivotPoint]:
        """
        Find pivot highs and lows using parsed high/low values.

        A pivot high at index i requires:
        - parsed_high[i] > parsed_high[i-left...i-1] and > parsed_high[i+1...i+right]
        
        A pivot low at index i requires:
        - parsed_low[i] < parsed_low[i-left...i-1] and < parsed_low[i+1...i+right]
        """
        pivots = []
        n = len(parsed_candles)

        for i in range(self.length, n - self.length):
            candle = parsed_candles[i].original

            # Check for pivot high
            is_pivot_high = True
            current_high = parsed_candles[i].parsed_high

            for j in range(i - self.length, i + self.length + 1):
                if j == i:
                    continue
                if parsed_candles[j].parsed_high >= current_high:
                    is_pivot_high = False
                    break

            if is_pivot_high:
                pivots.append(PivotPoint(
                    index=i,
                    timestamp=candle.timestamp,
                    price=current_high,
                    is_high=True,
                    candle=candle
                ))
                continue

            # Check for pivot low
            is_pivot_low = True
            current_low = parsed_candles[i].parsed_low

            for j in range(i - self.length, i + self.length + 1):
                if j == i:
                    continue
                if parsed_candles[j].parsed_low <= current_low:
                    is_pivot_low = False
                    break

            if is_pivot_low:
                pivots.append(PivotPoint(
                    index=i,
                    timestamp=candle.timestamp,
                    price=current_low,
                    is_high=False,
                    candle=candle
                ))

        return pivots

    def detect_breaks(
        self,
        parsed_candles: list[ParsedCandle],
        pivots: list[PivotPoint]
    ) -> list[StructureBreak]:
        """
        Detect BOS and CHOCH based on pivot breaks.

        Logic:
        - Track current trend (bullish/bearish)
        - When price breaks opposite pivot:
          - If breaking against trend -> CHOCH
          - If breaking with trend -> BOS
        """
        breaks = []
        if len(pivots) < 2:
            return breaks

        # Determine initial trend from first two pivots
        current_trend = self._determine_initial_trend(pivots[:2])

        last_pivot_high: Optional[PivotPoint] = None
        last_pivot_low: Optional[PivotPoint] = None

        for pivot in pivots:
            if pivot.is_high:
                last_pivot_high = pivot
            else:
                last_pivot_low = pivot

            # Check for breaks after we have both high and low pivots
            if last_pivot_high and last_pivot_low:
                # Look for breaks in candles between pivots
                start_idx = max(last_pivot_high.index, last_pivot_low.index)
                end_idx = pivot.index

                for i in range(start_idx + 1, end_idx + 1):
                    if i >= len(parsed_candles):
                        break

                    candle = parsed_candles[i].original

                    # Check bullish break (break above last pivot high)
                    if current_trend in (TrendDirection.BEARISH, TrendDirection.RANGING):
                        if candle.close > last_pivot_high.price:
                            break_type = BreakType.CHOCH if current_trend == TrendDirection.BEARISH else BreakType.BOS
                            breaks.append(StructureBreak(
                                index=i,
                                timestamp=candle.timestamp,
                                price=candle.close,
                                break_type=break_type,
                                direction=TrendDirection.BULLISH,
                                previous_trend=current_trend,
                                structure_type=self.config.structure_type,
                                confirmation_candle=candle
                            ))
                            current_trend = TrendDirection.BULLISH
                            break

                    # Check bearish break (break below last pivot low)
                    if current_trend in (TrendDirection.BULLISH, TrendDirection.RANGING):
                        if candle.close < last_pivot_low.price:
                            break_type = BreakType.CHOCH if current_trend == TrendDirection.BULLISH else BreakType.BOS
                            breaks.append(StructureBreak(
                                index=i,
                                timestamp=candle.timestamp,
                                price=candle.close,
                                break_type=break_type,
                                direction=TrendDirection.BEARISH,
                                previous_trend=current_trend,
                                structure_type=self.config.structure_type,
                                confirmation_candle=candle
                            ))
                            current_trend = TrendDirection.BEARISH
                            break

        return breaks

    def _determine_initial_trend(self, first_pivots: list[PivotPoint]) -> TrendDirection:
        """Determine initial trend from first pivots."""
        if len(first_pivots) < 2:
            return TrendDirection.RANGING

        # Find first high and first low
        first_high = next((p for p in first_pivots if p.is_high), None)
        first_low = next((p for p in first_pivots if not p.is_high), None)

        if not first_high or not first_low:
            return TrendDirection.RANGING

        # Trend determined by which comes first
        if first_high.index < first_low.index:
            return TrendDirection.BEARISH  # High first = downtrend
        else:
            return TrendDirection.BULLISH  # Low first = uptrend