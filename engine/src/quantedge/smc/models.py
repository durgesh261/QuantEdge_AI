"""
SMC (Smart Money Concepts) models and structures.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
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


class LegState:
    """Represents a confirmed leg in the structure."""
    def __init__(self, start_index: int, end_index: int, start_price: Decimal, end_price: Decimal, direction: TrendDirection, is_confirmed: bool = False, confirmation_index: Optional[int] = None):
        self.start_index = start_index
        self.end_index = end_index
        self.start_price = start_price
        self.end_price = end_price
        self.direction = direction
        self.is_confirmed = is_confirmed
        self.confirmation_index = confirmation_index


class OBState(str, Enum):
    """
    Order Block lifecycle states.
    
    State transitions:
        FRESH -> TOUCHED -> USED
            |
            v
        INVALIDATED
    
    - FRESH: Never touched, eligible for entry
    - TOUCHED: First return/touch, eligible for ONE entry decision
    - USED: Trade executed from this OB, no further entries
    - INVALIDATED: Price closed through OB boundary, dead
    """
    FRESH = "fresh"
    TOUCHED = "touched"
    USED = "used"
    INVALIDATED = "invalidated"


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


@dataclass
class OrderBlock:
    """
    LuxAlgo-style Order Block with explicit lifecycle state.

    Created from structural breaks with specific selection logic:
    - Bullish OB: Minimum low in parsed range after bullish BOS/CHOCH
    - Bearish OB: Maximum high in parsed range after bearish BOS/CHOCH
    
    Lifecycle (explicit state machine):
        FRESH (touch_count=0) -> TOUCHED (first return) -> USED (trade executed)
                                      |
                                      v
                                  INVALIDATED (price closed through boundary)
    
    Strategy rules:
    - Only FRESH OBs are eligible for entry
    - TOUCHED OBs get ONE entry chance (first touch)
    - USED OBs cannot generate further trades
    - INVALIDATED OBs are dead
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
    state: OBState = OBState.FRESH
    touch_count: int = 0
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

    def is_eligible_for_entry(self) -> bool:
        """
        Check if OB is eligible for a new trade entry.
        
        Per strategy:
        - FRESH: eligible
        - TOUCHED: eligible for ONE entry (first touch)
        - USED: not eligible
        - INVALIDATED: not eligible
        """
        return self.state in (OBState.FRESH, OBState.TOUCHED)

    def is_fresh(self) -> bool:
        """OB has never been touched."""
        return self.state == OBState.FRESH

    def is_touched(self) -> bool:
        """OB has been touched once (first return)."""
        return self.state == OBState.TOUCHED

    def is_used(self) -> bool:
        """Trade has been executed from this OB."""
        return self.state == OBState.USED

    def is_invalidated(self) -> bool:
        """OB has been invalidated by price action."""
        return self.state == OBState.INVALIDATED

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
        """Check if price touched this OB (updates state if FRESH).
        
        A touch occurs when the candle's price range overlaps with the OB range.
        """
        if self.is_bullish():
            # Bullish OB range: [bottom_price, top_price]
            # Touch occurs when candle range overlaps with OB range
            touched = candle.low <= self.top_price and candle.high >= self.bottom_price
        else:
            # Bearish OB range: [bottom_price, top_price] (same, just semantics)
            # Touch occurs when candle range overlaps with OB range
            touched = candle.low <= self.top_price and candle.high >= self.bottom_price
        
        if touched and self.state == OBState.FRESH:
            # Transition FRESH -> TOUCHED on first touch
            self.state = OBState.TOUCHED
            self.touch_count = 1
        elif touched and self.state == OBState.TOUCHED:
            # Already touched, increment count
            self.touch_count += 1
        
        return touched

    def check_invalidation(self, candle: Candle) -> bool:
        """Check if OB is invalidated by candle close (updates state)."""
        if self.is_bullish():
            invalidated = candle.close < self.bottom_price
        else:
            invalidated = candle.close > self.top_price
        
        if invalidated and self.state != OBState.INVALIDATED:
            self.state = OBState.INVALIDATED
            self.invalidated_at = candle.timestamp
            self.invalidated_by_price = candle.close
        
        return invalidated

    def mark_used(self):
        """Mark OB as used after trade execution."""
        self.state = OBState.USED

    def contains_price(self, price: Any) -> bool:
        """Return True iff price is within the OB price zone [bottom_price, top_price]."""
        p = price if isinstance(price, Decimal) else Decimal(str(price))
        return self.bottom_price <= p <= self.top_price


def is_price_inside_ob(price: Any, order_block: OrderBlock) -> bool:
    """Return True if price is inside the OrderBlock's price zone [bottom_price, top_price].

    This is a read-only state/relevance check and does NOT generate trade signals.
    """
    return order_block.contains_price(price)


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

    def get_eligible_order_blocks(self) -> list[OrderBlock]:
        """Get OBs eligible for entry (FRESH or TOUCHED, not invalidated)."""
        return [
            ob for ob in self.order_blocks
            if ob.is_eligible_for_entry() and ob.confidence_score >= 85
        ]

    def get_fresh_order_blocks(self) -> list[OrderBlock]:
        """Get FRESH OBs (never touched)."""
        return [
            ob for ob in self.order_blocks
            if ob.is_fresh() and ob.confidence_score >= 85
        ]

    def get_recent_breaks(self, lookback: int = 10) -> list[StructureBreak]:
        """Get recent structure breaks."""
        all_breaks = sorted(
            self.internal_breaks + self.swing_breaks,
            key=lambda b: b.timestamp,
            reverse=True
        )
        return all_breaks[:lookback]