"""
Liquidity detection per LuxAlgo SMC.

Liquidity levels form at equal highs/lows and swing extremes.
Buy-side liquidity = above price (sell stops, breakout buyers)
Sell-side liquidity = below price (buy stops, breakdown sellers)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from quantedge.market_data.models import Candle
from quantedge.smc.models import (
    LiquidityLevel, EqualLevel, PivotPoint, TrendDirection
)


@dataclass
class LiquidityConfig:
    lookback: int = 50
    min_touches: int = 2
    strength_decay: float = 0.95


class LiquidityDetector:
    """
    Detects liquidity levels from equal highs/lows and swing extremes.
    """

    def __init__(self, config: LiquidityConfig):
        self.config = config

    def detect_liquidity(
        self,
        candles: list[Candle],
        pivot_highs: list[PivotPoint],
        pivot_lows: list[PivotPoint],
        equal_highs: list[EqualLevel],
        equal_lows: list[EqualLevel],
    ) -> tuple[list[LiquidityLevel], list[LiquidityLevel]]:
        """
        Detect buy-side and sell-side liquidity levels.

        Returns:
            Tuple of (buy_side_liquidity, sell_side_liquidity)
        """
        buy_side = []
        sell_side = []

        # Liquidity from equal highs (buy-side - stops above)
        for eq_high in equal_highs:
            if eq_high.touch_count >= self.config.min_touches:
                strength = min(1.0, eq_high.touch_count / 5.0)
                buy_side.append(LiquidityLevel(
                    price=eq_high.price,
                    timestamp=eq_high.timestamp,
                    is_buy_side=True,
                    strength=strength,
                    sweep_count=0,
                    is_swept=False,
                ))

        # Liquidity from equal lows (sell-side - stops below)
        for eq_low in equal_lows:
            if eq_low.touch_count >= self.config.min_touches:
                strength = min(1.0, eq_low.touch_count / 5.0)
                sell_side.append(LiquidityLevel(
                    price=eq_low.price,
                    timestamp=eq_low.timestamp,
                    is_buy_side=False,
                    strength=strength,
                    sweep_count=0,
                    is_swept=False,
                ))

        # Liquidity from swing highs (buy-side)
        for pivot in pivot_highs:
            # Only recent swing highs
            if len(buy_side) < 10:  # Limit
                strength = 0.5  # Base strength for swing highs
                buy_side.append(LiquidityLevel(
                    price=pivot.price,
                    timestamp=pivot.timestamp,
                    is_buy_side=True,
                    strength=strength,
                    sweep_count=0,
                    is_swept=False,
                ))

        # Liquidity from swing lows (sell-side)
        for pivot in pivot_lows:
            if len(sell_side) < 10:
                strength = 0.5
                sell_side.append(LiquidityLevel(
                    price=pivot.price,
                    timestamp=pivot.timestamp,
                    is_buy_side=False,
                    strength=strength,
                    sweep_count=0,
                    is_swept=False,
                ))

        # Sort by price (buy-side ascending, sell-side descending)
        buy_side.sort(key=lambda x: x.price)
        sell_side.sort(key=lambda x: x.price, reverse=True)

        return buy_side, sell_side

    def check_liquidity_sweep(
        self,
        candle: Candle,
        buy_side: list[LiquidityLevel],
        sell_side: list[LiquidityLevel],
    ) -> tuple[Optional[LiquidityLevel], Optional[LiquidityLevel]]:
        """
        Check if candle swept any liquidity levels.

        Returns:
            Tuple of (swept_buy_side, swept_sell_side)
        """
        swept_buy = None
        swept_sell = None

        # Check buy-side liquidity sweep (price went above)
        for level in buy_side:
            if not level.is_swept and candle.high >= level.price:
                level.is_swept = True
                level.swept_at = candle.timestamp
                level.sweep_count += 1
                swept_buy = level

        # Check sell-side liquidity sweep (price went below)
        for level in sell_side:
            if not level.is_swept and candle.low <= level.price:
                level.is_swept = True
                level.swept_at = candle.timestamp
                level.sweep_count += 1
                swept_sell = level

        return swept_buy, swept_sell

    def get_nearest_liquidity(
        self,
        price: Decimal,
        buy_side: list[LiquidityLevel],
        sell_side: list[LiquidityLevel],
    ) -> tuple[Optional[LiquidityLevel], Optional[LiquidityLevel]]:
        """Get nearest liquidity levels above and below current price."""
        # Nearest buy-side (above price)
        nearest_buy = None
        for level in buy_side:
            if level.price > price:
                nearest_buy = level
                break

        # Nearest sell-side (below price)
        nearest_sell = None
        for level in sell_side:
            if level.price < price:
                nearest_sell = level
                break

        return nearest_buy, nearest_sell
