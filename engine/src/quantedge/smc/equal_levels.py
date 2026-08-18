"""
Equal Highs / Equal Lows detection.

Equal levels form when multiple candles have the same high or low price
(within a small threshold). These are key liquidity levels.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from quantedge.market_data.models import Candle
from quantedge.smc.models import EqualLevel, PivotPoint


@dataclass
class EqualLevelsConfig:
    threshold_pct: float = 0.05  # 0.05% threshold for "equal"
    min_touches: int = 2
    lookback: int = 100


class EqualLevelsDetector:
    """Detects Equal Highs and Equal Lows from pivot points."""

    def __init__(self, config: EqualLevelsConfig):
        self.config = config

    def detect_equal_levels(
        self,
        candles: list[Candle],
        pivot_highs: list[PivotPoint],
        pivot_lows: list[PivotPoint],
    ) -> tuple[list[EqualLevel], list[EqualLevel]]:
        """
        Detect equal highs and equal lows.

        Groups pivots by price level (within threshold).
        """
        equal_highs = self._group_equal_levels(pivot_highs, True)
        equal_lows = self._group_equal_levels(pivot_lows, False)

        return equal_highs, equal_lows

    def _group_equal_levels(
        self,
        pivots: list[PivotPoint],
        is_high: bool,
    ) -> list[EqualLevel]:
        """Group pivots by price level within threshold."""
        if not pivots:
            return []

        threshold_multiplier = Decimal(str(self.config.threshold_pct / 100))
        groups = []

        for pivot in pivots:
            if len(groups) >= 50:  # Limit
                break

            # Find existing group within threshold
            found_group = None
            for group in groups:
                price_diff = abs(pivot.price - group.price) / group.price
                if price_diff <= threshold_multiplier:
                    found_group = group
                    break

            if found_group:
                found_group.touch_count += 1
                found_group.candles.append(pivot.candle)
                # Update timestamp to most recent
                if pivot.timestamp > found_group.timestamp:
                    found_group.timestamp = pivot.timestamp
            else:
                # Create new group
                groups.append(EqualLevel(
                    index=pivot.index,
                    timestamp=pivot.timestamp,
                    price=pivot.price,
                    is_high=is_high,
                    touch_count=1,
                    candles=[pivot.candle],
                ))

        # Filter by minimum touches
        return [g for g in groups if g.touch_count >= self.config.min_touches]
