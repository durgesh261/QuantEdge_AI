"""
Swing and Internal Structure detection per LuxAlgo SMC.

Reference: LuxAlgo uses a stateful leg-based structure with configurable lengths.
Internal structure length = 5, Swing structure length = 50 (defaults).

Key LuxAlgo concepts from Pine Script:
- leg(size): Returns current leg direction using high[size] > ta.highest(size) and low[size] < ta.lowest(size)
- Leg state persists across bars (var leg = 0)
- Leg transitions set pivot levels at the EXTREME of the previous leg
- Structure breaks require ta.crossover(close, level) AND NOT crossed
- Internal and Swing structures are independent

Reference functions:
- leg(size): var leg = 0; newLegHigh = high[size] > ta.highest(size); newLegLow = low[size] < ta.lowest(size)
- startOfNewLeg(leg): leg != leg[1]
- getCurrentStructure(): returns pivot levels and trend
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from enum import Enum

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


class LegDirection(Enum):
    """Current leg direction per LuxAlgo leg()."""
    BULLISH = 1    # Up leg (bullish)
    BEARISH = -1   # Down leg (bearish)
    NONE = 0       # No leg established yet


@dataclass
class PivotLevel:
    """A pivot level with crossed state for break detection - LuxAlgo structure level."""
    index: int
    timestamp: datetime
    price: Decimal
    is_high: bool
    candle: Candle
    crossed: bool = False  # Whether price has crossed this level (for break detection)


@dataclass
class StructureState:
    """Current structure state for internal or swing detection - mirrors LuxAlgo getCurrentStructure()."""
    # Current leg direction (per LuxAlgo leg() state variable)
    current_leg: int = 0  # 1 = bullish (up), -1 = bearish (down), 0 = none
    previous_leg: int = 0  # For detecting leg transitions
    
    # Current structure levels (LuxAlgo pivotHigh/pivotLow equivalent)
    pivot_high: Optional[PivotLevel] = None
    pivot_low: Optional[PivotLevel] = None
    
    # Current trend bias (derived from leg direction)
    trend: TrendDirection = TrendDirection.RANGING
    
    # Last structure break
    last_break: Optional[StructureBreak] = None


class StructureDetector:
    """
    LuxAlgo-style Structure Detector implementing the canonical state machine.
    
    Implements the equivalent of:
    - leg(size): stateful leg direction
    - startOfNewLeg(leg): leg transition detection
    - getCurrentStructure(): structure levels at leg transitions
    
    Key differences from traditional swing detection:
    - Uses ta.highest/ta.lowest over lookback window (not symmetric left/right)
    - Leg transitions detected via: high[size] > ta.highest(size) or low[size] < ta.lowest(size)
    - Pivot levels set at leg transitions (at the actual swing point)
    - Structure breaks require ta.crossover(close, level) AND NOT crossed
    - Internal and Swing structures are independent
    """
    
    def __init__(self, config_or_length, structure_type: StructureType = None):
        if isinstance(config_or_length, StructureConfig):
            self.length = config_or_length.length
            self.structure_type = config_or_length.structure_type
        else:
            self.length = config_or_length
            self.structure_type = structure_type
        self.state = StructureState()
        # Full history for leg() calculation using PARSED values (never popped)
        self._high_parsed_history: List[Decimal] = []
        self._low_parsed_history: List[Decimal] = []
        # Full history for swing point identification using RAW high/low values (never popped)
        self._high_history: List[Decimal] = []
        self._low_history: List[Decimal] = []
        # Store ALL candles for pivot creation (never popped)
        self._candles: List[Candle] = []
        # Track the extreme point of the current leg (for pivot at leg transition)
        self._leg_extreme_price: Optional[Decimal] = None
        self._leg_extreme_idx: int = 0
        # Absolute candle counter
        self._candle_count: int = 0
    
    def reset(self):
        """Reset detector state for new analysis."""
        self.state = StructureState()
        self._high_parsed_history = []
        self._low_parsed_history = []
        self._high_history = []
        self._low_history = []
        self._candles = []
        self._leg_extreme_price = None
        self._leg_extreme_idx = 0
        self._candle_count = 0
    
    def process_candle(self, parsed_candle: ParsedCandle, candle_index: int) -> List[StructureBreak]:
        """
        Process a single candle and update structure state per LuxAlgo logic.
        
        Returns any new structure breaks detected at this candle.
        """
        breaks = []
        candle = parsed_candle.original
        
        # Update full history for leg() calculation - use PARSED values
        self._high_parsed_history.append(parsed_candle.parsed_high)
        self._low_parsed_history.append(parsed_candle.parsed_low)
        # Update full history for swing point identification - use RAW values
        self._high_history.append(candle.high)
        self._low_history.append(candle.low)
        self._candles.append(candle)
        self._candle_count += 1
        
        current_high = candle.high
        current_low = candle.low
        current_close = candle.close
        previous_close = self._candles[-2].close if len(self._candles) >= 2 else current_close
        
        # Track leg extremes (for pivot at leg transition) - use RAW values
        if self.state.current_leg == 1:
            # Bullish leg: track highest high as leg peak
            if self._leg_extreme_price is None or current_high > self._leg_extreme_price:
                self._leg_extreme_price = current_high
                self._leg_extreme_idx = self._candle_count - 1
        elif self.state.current_leg == -1:
            # Bearish leg: track lowest low as leg valley
            if self._leg_extreme_price is None or current_low < self._leg_extreme_price:
                self._leg_extreme_price = current_low
                self._leg_extreme_idx = self._candle_count - 1
        
        # Need at least length bars to compute ta.highest/ta.lowest for leg detection
        if self._candle_count < self.length:
            return []
        
        # LuxAlgo leg() logic for trend direction (using PARSED values from full history):
        # high[size] = high of bar `length` bars ago (absolute index = candle_count - 1 - length)
        # ta.highest(size) = highest of last `length` bars (including current)
        # newLegHigh = high[size] > ta.highest(size) -> bearish leg (down)
        # newLegLow  = low[size] < ta.lowest(size)  -> bullish leg (up)
        
        if self._candle_count <= self.length:
            return []
        
        # high[size] = high of bar `length` bars ago
        size_idx = self._candle_count - 1 - self.length
        high_size = self._high_parsed_history[size_idx]
        low_size = self._low_parsed_history[size_idx]
        
        # ta.highest(size) = highest of last `length` bars (including current)
        # Indices: [candle_count - length, ..., candle_count - 1]
        start_idx = self._candle_count - self.length
        end_idx = self._candle_count
        highest = max(self._high_parsed_history[start_idx:end_idx])
        lowest = min(self._low_parsed_history[start_idx:end_idx])
        
        new_leg_high = high_size > highest
        new_leg_low = low_size < lowest
        
        # Detect leg transitions (LuxAlgo: leg := BEARISH_LEG / BULLISH_LEG)
        leg_changed = False
        if new_leg_high:
            if self.state.current_leg != -1:
                self._on_leg_change(-1)
            self.state.current_leg = -1
            leg_changed = True
        elif new_leg_low:
            if self.state.current_leg != 1:
                self._on_leg_change(1)
            self.state.current_leg = 1
            leg_changed = True
        
        # Update trend from leg direction
        if self.state.current_leg == 1:
            self.state.trend = TrendDirection.BULLISH
        elif self.state.current_leg == -1:
            self.state.trend = TrendDirection.BEARISH
        else:
            self.state.trend = TrendDirection.RANGING
        
        # Check for structure breaks (BOS/CHOCH) with proper crossover/crossunder
        breaks = self._check_structure_breaks(current_close, previous_close)
        return breaks
    
    def _on_leg_change(self, new_leg: int):
        """Handle leg direction change - set pivot levels at the extreme of the previous leg.
        
        This is the equivalent of LuxAlgo's getCurrentStructure() pivot assignment.
        """
        # Use the tracked extreme of the previous leg as the pivot
        pivot_idx = self._leg_extreme_idx
        pivot_price = self._leg_extreme_price
        
        if self.state.current_leg == 1 and new_leg == -1:
            # Bullish -> Bearish: set pivot high at the previous leg's peak
            if pivot_price is not None and pivot_idx < len(self._candles):
                self.state.pivot_high = PivotLevel(
                    index=pivot_idx,
                    timestamp=self._candles[pivot_idx].timestamp,
                    price=pivot_price,
                    is_high=True,
                    candle=self._candles[pivot_idx],
                    crossed=False  # New pivot starts uncrossed
                )
        elif self.state.current_leg == -1 and new_leg == 1:
            # Bearish -> Bullish: set pivot low at the previous leg's valley
            if pivot_price is not None and pivot_idx < len(self._candles):
                self.state.pivot_low = PivotLevel(
                    index=pivot_idx,
                    timestamp=self._candles[pivot_idx].timestamp,
                    price=pivot_price,
                    is_high=False,
                    candle=self._candles[pivot_idx],
                    crossed=False  # New pivot starts uncrossed
                )
        
        # Reset extreme tracking for the new leg
        self._leg_extreme_price = None
        self._leg_extreme_idx = self._candle_count
    
    def _check_structure_breaks(self, current_close: Decimal, previous_close: Decimal) -> List[StructureBreak]:
        """Check for BOS/CHOCH breaks using proper ta.crossover/ta.crossunder with crossed state.
        
        Crossover: previous_close <= level and current_close > level
        Crossunder: previous_close >= level and current_close < level
        """
        breaks = []
        if not self._candles:
            return []
        
        candle = self._candles[-1]
        
        # Check bullish break: ta.crossover(close, pivot_high) AND NOT crossed
        if self.state.pivot_high and not self.state.pivot_high.crossed:
            level = self.state.pivot_high.price
            if previous_close <= level and current_close > level:
                brk = StructureBreak(
                    index=self._candle_count - 1,  # Break candle index
                    timestamp=candle.timestamp,
                    price=current_close,
                    break_type=BreakType.CHOCH if self.state.trend == TrendDirection.BEARISH else BreakType.BOS,
                    direction=TrendDirection.BULLISH,
                    previous_trend=self.state.trend,
                    structure_type=self.structure_type,
                    confirmation_candle=candle
                )
                self.state.pivot_high.crossed = True
                self.state.trend = TrendDirection.BULLISH
                self.state.last_break = brk
                return [brk]
        
        # Check bearish break: ta.crossunder(close, pivot_low) AND NOT crossed
        if self.state.pivot_low and not self.state.pivot_low.crossed:
            level = self.state.pivot_low.price
            if previous_close >= level and current_close < level:
                brk = StructureBreak(
                    index=self._candle_count - 1,  # Break candle index
                    timestamp=candle.timestamp,
                    price=current_close,
                    break_type=BreakType.CHOCH if self.state.trend == TrendDirection.BULLISH else BreakType.BOS,
                    direction=TrendDirection.BEARISH,
                    previous_trend=self.state.trend,
                    structure_type=self.structure_type,
                    confirmation_candle=candle
                )
                self.state.pivot_low.crossed = True
                self.state.trend = TrendDirection.BEARISH
                self.state.last_break = brk
                return [brk]
        
        return []
    
    def get_confirmed_pivots(self) -> tuple[List, List]:
        """Get structure levels for compatibility (returns current pivot levels)."""
        highs = []
        lows = []
        if self.state.pivot_high:
            highs.append(PivotPoint(
                index=self.state.pivot_high.index,
                timestamp=self.state.pivot_high.timestamp,
                price=self.state.pivot_high.price,
                is_high=True,
                candle=self.state.pivot_high.candle
            ))
        if self.state.pivot_low:
            lows.append(PivotPoint(
                index=self.state.pivot_low.index,
                timestamp=self.state.pivot_low.timestamp,
                price=self.state.pivot_low.price,
                is_high=False,
                candle=self.state.pivot_low.candle
            ))
        return highs, lows
    
    def get_legs(self) -> List:
        return []
    
    def get_current_trend(self) -> TrendDirection:
        return self.state.trend
    
    def get_last_break(self) -> Optional[StructureBreak]:
        return self.state.last_break


def detect_structure_streaming(
    parsed_candles: List[ParsedCandle],
    length: int,
    structure_type: StructureType
) -> tuple[List, List, List[StructureBreak], TrendDirection]:
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