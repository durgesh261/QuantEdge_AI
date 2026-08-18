"""
Swing and Internal Structure detection per LuxAlgo SMC.

Reference: LuxAlgo uses a stateful leg-based structure with configurable lengths.
Internal structure length = 5, Swing structure length = 50 (defaults).

Key LuxAlgo concepts:
- leg(): A swing leg between two confirmed pivots
- startOfNewLeg(): Detects when a new leg begins
- getCurrentStructure(): Returns current trend state
- Pivot confirmation requires left/right bars
- Structure break (BOS/CHOCH) confirmed on candle close

This implementation models the LuxAlgo state machine:
1. Track unconfirmed highs/lows during pivot formation
2. Confirm pivots after right-bars confirm
3. Maintain leg state (bullish/bearish/ranging)
4. Detect BOS/CHOCH on confirmed structure breaks
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, List
from enum import Enum
import numpy as np

from quantedge.market_data.models import Candle
from quantedge.smc.models import (
    PivotPoint, StructureBreak, TrendDirection, BreakType, StructureType
)
from quantedge.smc.volatility import ParsedCandle


@dataclass
class StructureConfig:
    """Configuration for structure detection (backward compatibility)."""
    length: int
    structure_type: StructureType


class PivotState(Enum):
    """State of a potential pivot during formation."""
    UNCONFIRMED_HIGH = "unconfirmed_high"
    UNCONFIRMED_LOW = "unconfirmed_low"
    CONFIRMED_HIGH = "confirmed_high"
    CONFIRMED_LOW = "confirmed_low"


@dataclass
class PotentialPivot:
    """A pivot candidate awaiting confirmation."""
    index: int
    price: Decimal
    is_high: bool
    left_bars_confirmed: int = 0
    right_bars_needed: int = 0
    candle: Optional[Candle] = None


@dataclass
class LegState:
    """Represents a confirmed leg in the structure."""
    start_index: int
    end_index: int
    start_price: Decimal
    end_price: Decimal
    direction: TrendDirection  # BULLISH (up leg) or BEARISH (down leg)
    is_confirmed: bool = False
    confirmation_index: Optional[int] = None


@dataclass
class StructureState:
    """Current structure state for internal or swing detection."""
    # Confirmed pivots
    confirmed_highs: List[PivotPoint] = field(default_factory=list)
    confirmed_lows: List[PivotPoint] = field(default_factory=list)
    
    # Unconfirmed potential pivots
    potential_high: Optional[PotentialPivot] = None
    potential_low: Optional[PotentialPivot] = None
    
    # Leg history
    legs: List[LegState] = field(default_factory=list)
    current_leg: Optional[LegState] = None
    
    # Current trend
    trend: TrendDirection = TrendDirection.RANGING
    
    # Last structure break
    last_break: Optional[StructureBreak] = None
    
    # Bars since last confirmed pivot
    bars_since_last_pivot: int = 0


class StructureDetector:
    """
    LuxAlgo-style Structure Detector.
    
    Implements stateful pivot detection with:
    - Left/right bar confirmation (length parameter)
    - Leg formation and tracking
    - BOS/CHOCH detection with proper timing
    - Distinction between pivot formation, confirmation, and break times
    """
    
    def __init__(self, config_or_length, structure_type: StructureType = None):
        if isinstance(config_or_length, StructureConfig):
            self.length = config_or_length.length
            self.structure_type = config_or_length.structure_type
        else:
            self.length = config_or_length
            self.structure_type = structure_type
        self.state = StructureState()
    
    def reset(self):
        """Reset detector state for new analysis."""
        self.state = StructureState()
    
    def process_candle(self, parsed_candle: ParsedCandle, candle_index: int) -> List[StructureBreak]:
        """
        Process a single candle and update structure state.
        
        Returns any new structure breaks detected at this candle.
        """
        breaks = []
        
        # Update potential pivots
        self._update_potential_pivots(parsed_candle, candle_index)
        
        # Check for pivot confirmations
        newly_confirmed = self._check_pivot_confirmations(candle_index)
        
        # If new pivots confirmed, update legs and trend
        if newly_confirmed:
            self._update_legs_and_trend()
        
        # Check for structure breaks
        new_breaks = self._check_structure_breaks(parsed_candle, candle_index)
        breaks.extend(new_breaks)
        
        # Update bars since last pivot
        if self.state.confirmed_highs or self.state.confirmed_lows:
            self.state.bars_since_last_pivot += 1
        else:
            self.state.bars_since_last_pivot = 0
        
        return breaks
    
    def _update_potential_pivots(self, parsed_candle: ParsedCandle, index: int):
        """Update or create potential pivots based on current candle."""
        # Check for potential high
        if self.state.potential_high is None or parsed_candle.parsed_high > self.state.potential_high.price:
            self.state.potential_high = PotentialPivot(
                index=index,
                price=parsed_candle.parsed_high,
                is_high=True,
                left_bars_confirmed=0,
                right_bars_needed=self.length,
                candle=parsed_candle.original
            )
        else:
            # Increment left bars for existing potential high
            if self.state.potential_high:
                self.state.potential_high.left_bars_confirmed += 1
        
        # Check for potential low
        if self.state.potential_low is None or parsed_candle.parsed_low < self.state.potential_low.price:
            self.state.potential_low = PotentialPivot(
                index=index,
                price=parsed_candle.parsed_low,
                is_high=False,
                left_bars_confirmed=0,
                right_bars_needed=self.length,
                candle=parsed_candle.original
            )
        else:
            # Increment left bars for existing potential low
            if self.state.potential_low:
                self.state.potential_low.left_bars_confirmed += 1
    
    def _check_pivot_confirmations(self, current_index: int) -> List[PivotPoint]:
        """Check if any potential pivots have been confirmed."""
        confirmed = []
        
        # Check potential high
        if self.state.potential_high:
            if self.state.potential_high.left_bars_confirmed >= self.length:
                # Wait for right bars
                right_bars = current_index - self.state.potential_high.index
                if right_bars >= self.length:
                    # Confirm the high
                    pivot = PivotPoint(
                        index=self.state.potential_high.index,
                        timestamp=self.state.potential_high.candle.timestamp,
                        price=self.state.potential_high.price,
                        is_high=True,
                        candle=self.state.potential_high.candle
                    )
                    self.state.confirmed_highs.append(pivot)
                    confirmed.append(pivot)
                    self.state.potential_high = None
                    self.state.bars_since_last_pivot = 0
        
        # Check potential low
        if self.state.potential_low:
            if self.state.potential_low.left_bars_confirmed >= self.length:
                right_bars = current_index - self.state.potential_low.index
                if right_bars >= self.length:
                    pivot = PivotPoint(
                        index=self.state.potential_low.index,
                        timestamp=self.state.potential_low.candle.timestamp,
                        price=self.state.potential_low.price,
                        is_high=False,
                        candle=self.state.potential_low.candle
                    )
                    self.state.confirmed_lows.append(pivot)
                    confirmed.append(pivot)
                    self.state.potential_low = None
                    self.state.bars_since_last_pivot = 0
        
        return confirmed
    
    def _update_legs_and_trend(self):
        """Update leg state and overall trend based on confirmed pivots."""
        all_pivots = sorted(
            self.state.confirmed_highs + self.state.confirmed_lows,
            key=lambda p: p.index
        )
        
        if len(all_pivots) < 2:
            self.state.trend = TrendDirection.RANGING
            return
        
        # Build legs from consecutive opposite pivots
        self.state.legs = []
        for i in range(1, len(all_pivots)):
            prev = all_pivots[i-1]
            curr = all_pivots[i]
            
            if prev.is_high != curr.is_high:
                # Valid leg: high->low or low->high
                direction = TrendDirection.BULLISH if (not prev.is_high and curr.is_high) else TrendDirection.BEARISH
                
                leg = LegState(
                    start_index=prev.index,
                    end_index=curr.index,
                    start_price=prev.price,
                    end_price=curr.price,
                    direction=direction,
                    is_confirmed=True,
                    confirmation_index=curr.index
                )
                self.state.legs.append(leg)
        
        # Determine current trend from last leg
        if self.state.legs:
            self.state.trend = self.state.legs[-1].direction
            
            # Update current leg
            last_leg = self.state.legs[-1]
            if len(all_pivots) >= 2:
                last_pivot = all_pivots[-1]
                if last_pivot.index > last_leg.end_index:
                    # New leg started
                    self.state.current_leg = LegState(
                        start_index=last_leg.end_index,
                        end_index=last_pivot.index,
                        start_price=last_leg.end_price,
                        end_price=last_pivot.price,
                        direction=TrendDirection.BULLISH if (last_leg.end_price < last_pivot.price) else TrendDirection.BEARISH,
                        is_confirmed=False
                    )
                else:
                    self.state.current_leg = last_leg
        else:
            self.state.trend = TrendDirection.RANGING
    
    def _check_structure_breaks(self, parsed_candle: ParsedCandle, index: int) -> List[StructureBreak]:
        """Check for BOS/CHOCH breaks against confirmed structure."""
        breaks = []
        
        candle = parsed_candle.original
        
        # Check for bullish break (close above any confirmed high)
        if self.state.confirmed_highs:
            last_high = self.state.confirmed_highs[-1]
            if candle.close > last_high.price:
                if self.state.trend in (TrendDirection.BEARISH, TrendDirection.RANGING):
                    break_type = BreakType.CHOCH if self.state.trend == TrendDirection.BEARISH else BreakType.BOS
                    
                    brk = StructureBreak(
                        index=index,
                        timestamp=candle.timestamp,
                        price=candle.close,
                        break_type=break_type,
                        direction=TrendDirection.BULLISH,
                        previous_trend=self.state.trend,
                        structure_type=self.structure_type,
                        confirmation_candle=candle
                    )
                    breaks.append(brk)
                    self.state.last_break = brk
                    self.state.trend = TrendDirection.BULLISH
        
        # Check for bearish break (close below any confirmed low)
        if self.state.confirmed_lows:
            last_low = self.state.confirmed_lows[-1]
            if candle.close < last_low.price:
                if self.state.trend in (TrendDirection.BULLISH, TrendDirection.RANGING):
                    break_type = BreakType.CHOCH if self.state.trend == TrendDirection.BULLISH else BreakType.BOS
                    
                    brk = StructureBreak(
                        index=index,
                        timestamp=candle.timestamp,
                        price=candle.close,
                        break_type=break_type,
                        direction=TrendDirection.BEARISH,
                        previous_trend=self.state.trend,
                        structure_type=self.structure_type,
                        confirmation_candle=candle
                    )
                    breaks.append(brk)
                    self.state.last_break = brk
                    self.state.trend = TrendDirection.BEARISH
        
        return breaks
    
    def get_confirmed_pivots(self) -> tuple[List[PivotPoint], List[PivotPoint]]:
        """Get all confirmed pivots."""
        return self.state.confirmed_highs, self.state.confirmed_lows
    
    def get_legs(self) -> List[LegState]:
        """Get all confirmed legs."""
        return self.state.legs
    
    def get_current_trend(self) -> TrendDirection:
        """Get current trend direction."""
        return self.state.trend
    
    def get_last_break(self) -> Optional[StructureBreak]:
        """Get last structure break."""
        return self.state.last_break


def detect_structure_streaming(
    parsed_candles: List[ParsedCandle],
    length: int,
    structure_type: StructureType
) -> tuple[List[PivotPoint], List[PivotPoint], List[StructureBreak], TrendDirection]:
    """
    Convenience function to run full structure detection on a candle series.
    
    Returns: (confirmed_highs, confirmed_lows, breaks, final_trend)
    """
    detector = StructureDetector(length, structure_type)
    all_breaks = []
    
    for i, pc in enumerate(parsed_candles):
        breaks = detector.process_candle(pc, i)
        all_breaks.extend(breaks)
    
    highs, lows = detector.get_confirmed_pivots()
    return highs, lows, all_breaks, detector.get_current_trend()