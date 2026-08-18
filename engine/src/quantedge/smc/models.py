"""
SMC (Smart Money Concepts) models and structures.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from quantedge.market_data.models import Candle


class StructureType(str, Enum):
    INTERNAL = "internal"
    SWING = "swing"


class TrendDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class BreakType(str, Enum):
    BOS = "bos"
    CHOCH = "choch"


@dataclass(frozen=True)
class PivotPoint:
    """A confirmed pivot high or low."""
    index: int
    timestamp: datetime
    price: Decimal
    is_high: bool
    candle: Candle


@dataclass(frozen=True)
class StructureBreak:
    """A Break of Structure (BOS) or Change of Character (CHOCH)."""
    index: int
    timestamp: datetime
    price: Decimal
    break_type: BreakType
    direction: TrendDirection  # Direction of the break
    previous_trend: TrendDirection
    structure_type: StructureType
    confirmation_candle: Candle


@dataclass(frozen=True)
class EqualLevel:
    """Equal High or Equal Low."""
    index: int
    timestamp: datetime
    price: Decimal
    is_high: bool
    touch_count: int
    candles: list[Candle]


@dataclass(frozen=True)
class LiquidityLevel:
    """Liquidity level (buy-side or sell-side)."""
    price: Decimal
    timestamp: datetime
    is_buy_side: bool  # True = buy-side liquidity (above), False = sell-side (below)
    strength: float  # 0.0 to 1.0
    sweep_count: int = 0
    is_swept: bool = False
    swept_at: Optional[datetime] = None


@dataclass(frozen=True)
class OrderBlock:
    """
    LuxAlgo-style Order Block.

    Created from structural breaks with specific selection logic:
    - Bullish OB: Minimum low in parsed range after bullish BOS/CHOCH
    - Bearish OB: Maximum high in parsed range after bearish BOS/CHOCH
    """
    index: int
    symbol: str
    timeframe: str
    type: str  # "BULLISH" or "BEARISH"
    top_price: Decimal
    bottom_price: Decimal
    formation_candle: Candle
    formation_index: int
    break_index: int
    break_type: BreakType
    trend_before_break: TrendDirection
    touch_count: int = 0
    is_used: bool = False
    is_invalidated: bool = False
    invalidated_at: Optional[datetime] = None
    invalidated_by_price: Optional[Decimal] = None
    swing_trend: TrendDirection = TrendDirection.RANGING
    internal_trend: TrendDirection = TrendDirection.RANGING
    confidence_score: int = 0

    @property
    def width(self) -> Decimal:
        return self.top_price - self.bottom_price

    @property
    def width_percent(self) -> Decimal:
        return (self.width / self.bottom_price) * Decimal("100")

    @property
    def midline(self) -> Decimal:
        return (self.top_price + self.bottom_price) / Decimal("2")

    def is_bullish(self) -> bool:
        return self.type == "BULLISH"

    def is_bearish(self) -> bool:
        return self.type == "BEARISH"

    def calculate_entry_price(self) -> Decimal:
        """Dynamic entry based on OB width per strategy spec."""
        if self.width_percent <= Decimal("0.6"):
            # Narrow OB - edge entry
            if self.is_bullish():
                return self.top_price
            else:
                return self.bottom_price
        else:
            # Wide OB - 25% from edge
            if self.is_bullish():
                return self.top_price - (self.width * Decimal("0.25"))
            else:
                return self.bottom_price + (self.width * Decimal("0.25"))

    def calculate_stop_loss(self) -> Decimal:
        """Stop loss at opposite OB boundary."""
        if self.is_bullish():
            return self.bottom_price
        else:
            return self.top_price

    def check_touch(self, candle: Candle) -> bool:
        """Check if price touched this OB."""
        if self.is_bullish():
            return candle.low <= self.top_price and candle.low >= self.bottom_price
        else:
            return candle.high >= self.bottom_price and candle.high <= self.top_price

    def check_invalidation(self, candle: Candle) -> bool:
        """Check if OB is invalidated by candle close."""
        if self.is_bullish():
            return candle.close < self.bottom_price
        else:
            return candle.close > self.top_price


@dataclass(frozen=True)
class FairValueGap:
    """Fair Value Gap (FVG) / Imbalance."""
    index: int
    timestamp: datetime
    type: str  # "BULLISH" or "BEARISH"
    top_price: Decimal
    bottom_price: Decimal
    forming_candles: tuple[Candle, Candle, Candle]
    is_mitigated: bool = False
    mitigated_at: Optional[datetime] = None


@dataclass
class MarketStructureState:
    """Complete market structure state for a symbol/timeframe."""
    symbol: str
    timeframe: str
    last_updated: datetime

    # Structure
    internal_pivots: list[PivotPoint] = field(default_factory=list)
    swing_pivots: list[PivotPoint] = field(default_factory=list)
    internal_breaks: list[StructureBreak] = field(default_factory=list)
    swing_breaks: list[StructureBreak] = field(default_factory=list)

    # Equal levels
    equal_highs: list[EqualLevel] = field(default_factory=list)
    equal_lows: list[EqualLevel] = field(default_factory=list)

    # Liquidity
    buy_side_liquidity: list[LiquidityLevel] = field(default_factory=list)
    sell_side_liquidity: list[LiquidityLevel] = field(default_factory=list)

    # Order Blocks
    order_blocks: list[OrderBlock] = field(default_factory=list)

    # FVGs
    fair_value_gaps: list[FairValueGap] = field(default_factory=list)

    # Current trends
    internal_trend: TrendDirection = TrendDirection.RANGING
    swing_trend: TrendDirection = TrendDirection.RANGING

    def get_active_order_blocks(self) -> list[OrderBlock]:
        """Get valid, non-used order blocks."""
        return [
            ob for ob in self.order_blocks
            if not ob.is_invalidated and not ob.is_used and ob.confidence_score >= 85
        ]

    def get_recent_breaks(self, lookback: int = 10) -> list[StructureBreak]:
        """Get recent structure breaks."""
        all_breaks = sorted(
            self.internal_breaks + self.swing_breaks,
            key=lambda b: b.timestamp,
            reverse=True
        )
        return all_breaks[:lookback]