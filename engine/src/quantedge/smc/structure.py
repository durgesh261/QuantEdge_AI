"""
Swing and Internal Structure detection per LuxAlgo SMC.

Reference: LuxAlgo uses a stateful leg-based structure with configurable lengths.
Internal structure length = 5, Swing structure length = 50 (defaults).

Key LuxAlgo concepts from Pine Script:
- leg(size): Returns current leg direction using high[size] > ta.highest(size) and low[size] < ta.lowest(size)
- Leg state persists across bars (var leg = 0)
- Leg transitions set pivot levels at the EXTREME of the previous leg (swing points)
- Within a leg, track swing points for BOS detection
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
class LegState:
    """Represents a confirmed leg in the structure (backward compatibility)."""
    start_index: int
    end_index: int
    start_price: Decimal
    end_price: Decimal
    direction: TrendDirection  # BULLISH (up leg) or BEARISH (down leg)
    is_confirmed: bool = False
    confirmation_index: Optional[int] = None


@dataclass
class PivotLevel:
    """A pivot level with crossed state for break detection."""
    index: int
    timestamp: datetime
    price: Decimal
    is_high: bool
    candle: Candle
    crossed: bool = False  # Whether price has crossed this level (for break detection)
    confirmed: bool = False  # Whether pivot is confirmed by right bars


@dataclass
class StructureState:
    """Current structure state for internal or swing detection."""
    # Current leg direction (per LuxAlgo leg() state variable)
    current_leg: int = 0  # 1 = bullish (up), -1 = bearish (down), 0 = none
    
    # All confirmed pivot levels
    all_pivot_highs: List['PivotLevel'] = field(default_factory=list)
    all_pivot_lows: List['PivotLevel'] = field(default_factory=list)
    
    # Current pivot levels (most recent confirmed for break detection)
    pivot_high: Optional['PivotLevel'] = None
    pivot_low: Optional['PivotLevel'] = None
    
    # Current trend bias (derived from leg direction)
    trend: TrendDirection = TrendDirection.RANGING
    
    # Last structure break
    last_break: Optional[StructureBreak] = None


class StructureDetector:
    """
    Structure Detector combining traditional swing detection with LuxAlgo trend logic.
    
    - Traditional swing points detected using left/right confirmation on RAW high/low
    - Leg direction from LuxAlgo leg() using PARSED values for BOS/CHOCH classification
    - Within a leg, track swing points for BOS detection
    - Structure breaks on crossover of confirmed swing levels
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
        # Full history for swing detection using RAW high/low values (never popped)
        self._high_history: List[Decimal] = []
        self._low_history: List[Decimal] = []
        # Store ALL candles for pivot creation (never popped)
        self._candles: List[Candle] = []
        # Track the extreme point of the current leg (for pivot at leg transition)
        self._leg_extreme_price: Optional[Decimal] = None
        self._leg_extreme_idx: int = 0
        # Track swing points within the current leg for BOS detection
        self._leg_swing_high: Optional[Decimal] = None
        self._leg_swing_high_idx: int = 0
        self._leg_swing_low: Optional[Decimal] = None
        self._leg_swing_low_idx: int = 0
        # Track potential swing points waiting for right-bar confirmation
        self._potential_swing_high: Optional[Decimal] = None
        self._potential_swing_high_idx: int = 0
        self._potential_swing_low: Optional[Decimal] = None
        self._potential_swing_low_idx: int = 0
        # Track if first leg has been established
        self._first_leg_established: bool = False
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
        self._leg_swing_high = None
        self._leg_swing_high_idx = 0
        self._leg_swing_low = None
        self._leg_swing_low_idx = 0
        self._potential_swing_high = None
        self._potential_swing_high_idx = 0
        self._potential_swing_low = None
        self._potential_swing_low_idx = 0
        self._first_leg_established = False
    
    def process_candle(self, parsed_candle: ParsedCandle, candle_index: int) -> List[StructureBreak]:
        """
        Process a single candle and update structure state.
        
        Returns any new structure breaks detected at this candle.
        """
        breaks = []
        candle = parsed_candle.original
        
        # Update full history for leg() calculation - use PARSED values
        self._high_parsed_history.append(parsed_candle.parsed_high)
        self._low_parsed_history.append(parsed_candle.parsed_low)
        # Update full history for swing detection - use RAW values
        self._high_history.append(candle.high)
        self._low_history.append(candle.low)
        self._candles.append(candle)
        self._candle_count += 1
        
        current_high = candle.high
        current_low = candle.low
        
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
        
        # Track traditional swing points (independent of leg direction)
        self._track_traditional_swings()
        
        # Track swing points within leg for BOS detection
        self._update_swing_tracking(current_high, current_low)
        
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
        
        # Detect leg transitions for trend classification
        if new_leg_high:
            if self.state.current_leg != -1:
                self._on_leg_change(-1)
            self.state.current_leg = -1
            self.state.trend = TrendDirection.BEARISH
        elif new_leg_low:
            if self.state.current_leg != 1:
                self._on_leg_change(1)
            self.state.current_leg = 1
            self.state.trend = TrendDirection.BULLISH
        
        # Fallback: if no leg established after sufficient candles, infer from price action
        if not self._first_leg_established and self._candle_count >= self.length * 2:
            self._establish_initial_leg()
        
        # Check for structure breaks (BOS/CHOCH) with crossed state
        breaks = self._check_structure_breaks()
        return breaks
    
    def _track_traditional_swings(self):
        """Track traditional swing points using left/right confirmation (independent of leg)."""
        # A swing point at index i is confirmed when we have `length` bars to the right
        # So at candle count n, we can confirm swing at index n - 1 - length
        if self._candle_count - 1 < self.length * 2:
            return  # Not enough bars for any confirmation
        
        check_idx = self._candle_count - 1 - self.length
        
        # Check swing high at check_idx (using RAW high values)
        if check_idx >= self.length:
            high_at_check = self._high_history[check_idx]
            is_swing_high = True
            # Check left side (length bars)
            for j in range(1, self.length + 1):
                if self._high_history[check_idx - j] >= high_at_check:
                    is_swing_high = False
                    break
            # Check right side (length bars, up to current)
            if is_swing_high:
                for j in range(1, self.length + 1):
                    right_idx = check_idx + j
                    if right_idx >= self._candle_count:
                        is_swing_high = False
                        break
                    if self._high_history[right_idx] >= high_at_check:
                        is_swing_high = False
                        break
            
            if is_swing_high:
                # Swing high confirmed!
                if check_idx < len(self._candles):
                    pivot = PivotLevel(
                        index=check_idx,
                        timestamp=self._candles[check_idx].timestamp,
                        price=high_at_check,
                        is_high=True,
                        candle=self._candles[check_idx],
                        crossed=False,
                        confirmed=True
                    )
                    # Only add if not already exists
                    if not any(p.index == check_idx for p in self.state.all_pivot_highs):
                        self.state.all_pivot_highs.append(pivot)
                        self.state.pivot_high = pivot
        
        # Check swing low at check_idx (using RAW low values)
        if check_idx >= self.length:
            low_at_check = self._low_history[check_idx]
            is_swing_low = True
            # Check left side
            for j in range(1, self.length + 1):
                if self._low_history[check_idx - j] <= low_at_check:
                    is_swing_low = False
                    break
            # Check right side
            if is_swing_low:
                for j in range(1, self.length + 1):
                    right_idx = check_idx + j
                    if right_idx >= self._candle_count:
                        is_swing_low = False
                        break
                    if self._low_history[right_idx] <= low_at_check:
                        is_swing_low = False
                        break
            
            if is_swing_low:
                # Swing low confirmed!
                if check_idx < len(self._candles):
                    pivot = PivotLevel(
                        index=check_idx,
                        timestamp=self._candles[check_idx].timestamp,
                        price=low_at_check,
                        is_high=False,
                        candle=self._candles[check_idx],
                        crossed=False,
                        confirmed=True
                    )
                    if not any(p.index == check_idx for p in self.state.all_pivot_lows):
                        self.state.all_pivot_lows.append(pivot)
                        self.state.pivot_low = pivot
    
    def _update_swing_tracking(self, current_high: Decimal, current_low: Decimal):
        """Track swing points within the current leg for BOS detection."""
        if self.state.current_leg == 1:
            # Bullish leg: track swing highs (resistance for BOS)
            if self._potential_swing_high is None or current_high > self._potential_swing_high:
                self._potential_swing_high = current_high
                self._potential_swing_high_idx = self._candle_count - 1
            elif self._potential_swing_high is not None:
                # Check if potential swing high is confirmed (length bars with lower highs)
                confirmed = True
                for i in range(1, self.length + 1):
                    idx = self._candle_count - 1 - i
                    if idx < 0:
                        confirmed = False
                        break
                    if self._high_history[idx] > self._potential_swing_high:
                        confirmed = False
                        break
                if confirmed:
                    self._leg_swing_high = self._potential_swing_high
                    self._leg_swing_high_idx = self._potential_swing_high_idx
                    # Update pivot_high for BOS detection
                    if self._leg_swing_high_idx < len(self._candles):
                        pivot = PivotLevel(
                            index=self._leg_swing_high_idx,
                            timestamp=self._candles[self._leg_swing_high_idx].timestamp,
                            price=self._leg_swing_high,
                            is_high=True,
                            candle=self._candles[self._leg_swing_high_idx],
                            crossed=False,
                            confirmed=True
                        )
                        self.state.pivot_high = pivot
                        if not any(p.index == self._leg_swing_high_idx for p in self.state.all_pivot_highs):
                            self.state.all_pivot_highs.append(pivot)
                    self._potential_swing_high = None
                    self._potential_swing_high_idx = 0
            
            # Track swing lows (support for pullbacks)
            if self._potential_swing_low is None or current_low < self._potential_swing_low:
                self._potential_swing_low = current_low
                self._potential_swing_low_idx = self._candle_count - 1
            elif self._potential_swing_low is not None:
                confirmed = True
                for i in range(1, self.length + 1):
                    idx = self._candle_count - 1 - i
                    if idx < 0:
                        confirmed = False
                        break
                    if self._low_history[idx] < self._potential_swing_low:
                        confirmed = False
                        break
                if confirmed:
                    self._leg_swing_low = self._potential_swing_low
                    self._leg_swing_low_idx = self._potential_swing_low_idx
                    self._potential_swing_low = None
                    self._potential_swing_low_idx = 0
                    
        elif self.state.current_leg == -1:
            # Bearish leg: track swing lows (support for BOS)
            if self._potential_swing_low is None or current_low < self._potential_swing_low:
                self._potential_swing_low = current_low
                self._potential_swing_low_idx = self._candle_count - 1
            elif self._potential_swing_low is not None:
                confirmed = True
                for i in range(1, self.length + 1):
                    idx = self._candle_count - 1 - i
                    if idx < 0:
                        confirmed = False
                        break
                    if self._low_history[idx] < self._potential_swing_low:
                        confirmed = False
                        break
                if confirmed:
                    self._leg_swing_low = self._potential_swing_low
                    self._leg_swing_low_idx = self._potential_swing_low_idx
                    # Update pivot_low for BOS detection
                    if self._leg_swing_low_idx < len(self._candles):
                        pivot = PivotLevel(
                            index=self._leg_swing_low_idx,
                            timestamp=self._candles[self._leg_swing_low_idx].timestamp,
                            price=self._leg_swing_low,
                            is_high=False,
                            candle=self._candles[self._leg_swing_low_idx],
                            crossed=False,
                            confirmed=True
                        )
                        self.state.pivot_low = pivot
                        if not any(p.index == self._leg_swing_low_idx for p in self.state.all_pivot_lows):
                            self.state.all_pivot_lows.append(pivot)
                    self._potential_swing_low = None
                    self._potential_swing_low_idx = 0
            
            # Track swing highs (resistance for pullbacks)
            if self._potential_swing_high is None or current_high > self._potential_swing_high:
                self._potential_swing_high = current_high
                self._potential_swing_high_idx = self._candle_count - 1
            elif self._potential_swing_high is not None:
                confirmed = True
                for i in range(1, self.length + 1):
                    idx = self._candle_count - 1 - i
                    if idx < 0:
                        confirmed = False
                        break
                    if self._high_history[idx] > self._potential_swing_high:
                        confirmed = False
                        break
                if confirmed:
                    self._leg_swing_high = self._potential_swing_high
                    self._leg_swing_high_idx = self._potential_swing_high_idx
                    self._potential_swing_high = None
                    self._potential_swing_high_idx = 0
    
    def _establish_initial_leg(self):
        """Establish initial leg direction from price action when LuxAlgo leg() doesn't trigger."""
        if self._first_leg_established:
            return
        
        first_close = self._candles[0].close
        last_close = self._candles[-1].close
        
        if last_close > first_close:
            # Uptrend - bullish leg
            if self.state.current_leg != 1:
                self._on_leg_change(1)
            self.state.current_leg = 1
        elif last_close < first_close:
            # Downtrend - bearish leg
            if self.state.current_leg != -1:
                self._on_leg_change(-1)
            self.state.current_leg = -1
        
        self._first_leg_established = True
    
    def _on_leg_change(self, new_leg: int):
        """Handle leg direction change - set pivot levels at the extreme of the previous leg."""
        # Use the tracked extreme of the previous leg as the pivot
        pivot_idx = self._leg_extreme_idx
        pivot_price = self._leg_extreme_price
        
        if self.state.current_leg == 1 and new_leg == -1:
            # Bullish -> Bearish: set pivot high at the previous leg's peak
            if pivot_price is not None and pivot_idx < len(self._candles):
                pivot = PivotLevel(
                    index=pivot_idx,
                    timestamp=self._candles[pivot_idx].timestamp,
                    price=pivot_price,
                    is_high=True,
                    candle=self._candles[pivot_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_high = pivot
                if not any(p.index == pivot_idx for p in self.state.all_pivot_highs):
                    self.state.all_pivot_highs.append(pivot)
        elif self.state.current_leg == -1 and new_leg == 1:
            # Bearish -> Bullish: set pivot low at the previous leg's valley
            if pivot_price is not None and pivot_idx < len(self._candles):
                pivot = PivotLevel(
                    index=pivot_idx,
                    timestamp=self._candles[pivot_idx].timestamp,
                    price=pivot_price,
                    is_high=False,
                    candle=self._candles[pivot_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_low = pivot
                if not any(p.index == pivot_idx for p in self.state.all_pivot_lows):
                    self.state.all_pivot_lows.append(pivot)
        
        # If this is the FIRST leg being established, create BOTH pivot levels
        if not self._first_leg_established:
            self._create_initial_pivots(new_leg)
            self._first_leg_established = True
        
        # Reset swing tracking for the new leg
        self._leg_extreme_price = None
        self._leg_extreme_idx = self._candle_count
        self._leg_swing_high = None
        self._leg_swing_high_idx = 0
        self._leg_swing_low = None
        self._leg_swing_low_idx = 0
        self._potential_swing_high = None
        self._potential_swing_high_idx = 0
        self._potential_swing_low = None
        self._potential_swing_low_idx = 0
        
        # Update trend based on new leg
        if new_leg == 1:
            self.state.trend = TrendDirection.BULLISH
        elif new_leg == -1:
            self.state.trend = TrendDirection.BEARISH
        else:
            self.state.trend = TrendDirection.RANGING
    
    def _create_initial_pivots(self, new_leg: int):
        """Create initial pivot levels when first leg is established."""
        if new_leg == 1:
            # First leg is bullish - create pivot low at tracked extreme
            if self._leg_extreme_price is not None and self._leg_extreme_idx < len(self._candles):
                pivot = PivotLevel(
                    index=self._leg_extreme_idx,
                    timestamp=self._candles[self._leg_extreme_idx].timestamp,
                    price=self._leg_extreme_price,
                    is_high=False,
                    candle=self._candles[self._leg_extreme_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_low = pivot
                self.state.all_pivot_lows.append(pivot)
                self._leg_swing_low = self._leg_extreme_price
                self._leg_swing_low_idx = self._leg_extreme_idx
            # Also create pivot_high from the opposite swing (highest before pivot low)
            max_high = Decimal('0')
            max_high_idx = 0
            for i in range(self._leg_extreme_idx):
                if self._high_history[i] > max_high:
                    max_high = self._high_history[i]
                    max_high_idx = i
            if max_high > 0 and max_high_idx < len(self._candles):
                pivot = PivotLevel(
                    index=max_high_idx,
                    timestamp=self._candles[max_high_idx].timestamp,
                    price=max_high,
                    is_high=True,
                    candle=self._candles[max_high_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_high = pivot
                self.state.all_pivot_highs.append(pivot)
                self._leg_swing_high = max_high
                self._leg_swing_high_idx = max_high_idx
        elif new_leg == -1:
            # First leg is bearish - create pivot high at tracked extreme
            if self._leg_extreme_price is not None and self._leg_extreme_idx < len(self._candles):
                pivot = PivotLevel(
                    index=self._leg_extreme_idx,
                    timestamp=self._candles[self._leg_extreme_idx].timestamp,
                    price=self._leg_extreme_price,
                    is_high=True,
                    candle=self._candles[self._leg_extreme_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_high = pivot
                self.state.all_pivot_highs.append(pivot)
                self._leg_swing_high = self._leg_extreme_price
                self._leg_swing_high_idx = self._leg_extreme_idx
            # Also create pivot_low from the opposite swing (lowest before pivot high)
            min_low = Decimal('999999999')
            min_low_idx = 0
            for i in range(self._leg_extreme_idx):
                if self._low_history[i] < min_low:
                    min_low = self._low_history[i]
                    min_low_idx = i
            if min_low < Decimal('999999999') and min_low_idx < len(self._candles):
                pivot = PivotLevel(
                    index=min_low_idx,
                    timestamp=self._candles[min_low_idx].timestamp,
                    price=min_low,
                    is_high=False,
                    candle=self._candles[min_low_idx],
                    crossed=False,
                    confirmed=True
                )
                self.state.pivot_low = pivot
                self.state.all_pivot_lows.append(pivot)
                self._leg_swing_low = min_low
                self._leg_swing_low_idx = min_low_idx
    
    def _check_structure_breaks(self) -> List[StructureBreak]:
        """Check for BOS/CHOCH breaks using ta.crossover with crossed state."""
        breaks = []
        if not self._candles:
            return []
        
        candle = self._candles[-1]
        
        # Check bullish break: ta.crossover(close, pivot_high) AND NOT crossed
        if self.state.pivot_high and self.state.pivot_high.confirmed and not self.state.pivot_high.crossed:
            if candle.close > self.state.pivot_high.price:
                brk = StructureBreak(
                    index=self._candle_count - 1,
                    timestamp=candle.timestamp,
                    price=candle.close,
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
        
        # Check bearish break: close crosses below pivot_low
        if self.state.pivot_low and self.state.pivot_low.confirmed and not self.state.pivot_low.crossed:
            if candle.close < self.state.pivot_low.price:
                brk = StructureBreak(
                    index=self._candle_count - 1,
                    timestamp=candle.timestamp,
                    price=candle.close,
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
        """Get all confirmed pivots for compatibility."""
        highs = []
        lows = []
        for ph in self.state.all_pivot_highs:
            highs.append(PivotPoint(
                index=ph.index,
                timestamp=ph.timestamp,
                price=ph.price,
                is_high=True,
                candle=ph.candle
            ))
        for pl in self.state.all_pivot_lows:
            lows.append(PivotPoint(
                index=pl.index,
                timestamp=pl.timestamp,
                price=pl.price,
                is_high=False,
                candle=pl.candle
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