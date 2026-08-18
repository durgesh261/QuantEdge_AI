"""
Main SMC Analyzer - orchestrates all SMC components.

This is the primary entry point for SMC analysis.
It coordinates:
1. Volatility parsing (ATR-based)
2. Structure detection (internal & swing)
3. Order Block detection
4. Liquidity detection
5. Equal levels detection
5. FVG detection
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
import numpy as np

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import (
    MarketStructureState, PivotPoint, StructureBreak, OrderBlock,
    LiquidityLevel, EqualLevel, FairValueGap, TrendDirection,
    BreakType, StructureType
)
from quantedge.smc.volatility import calculate_atr, parse_candles_with_volatility, ParsedCandle
from quantedge.smc.structure import StructureDetector, StructureConfig
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig
from quantedge.smc.liquidity import LiquidityDetector, LiquidityConfig
from quantedge.smc.equal_levels import EqualLevelsDetector, EqualLevelsConfig
from quantedge.smc.fvg import FVGDetector, FVGConfig


@dataclass
class SMCAnalyzerConfig:
    # Structure lengths
    internal_length: int = 5
    swing_length: int = 50

    # Volatility
    atr_period: int = 200
    atr_multiplier: float = 2.0

    # Liquidity
    liquidity_lookback: int = 50
    liquidity_min_touches: int = 2

    # Equal levels
    equal_threshold_pct: float = 0.05
    equal_min_touches: int = 2

    # FVG
    fvg_min_gap_pct: float = 0.01


class SMCAnalyzer:
    """
    Complete SMC Analysis Engine.

    Processes candles through the full LuxAlgo SMC pipeline:
    Raw Candles -> Volatility Parsing -> Structure -> OBs -> Liquidity -> FVGs
    """

    def __init__(self, config: Optional[SMCAnalyzerConfig] = None):
        self.config = config or SMCAnalyzerConfig()

        # Initialize sub-detectors
        self.internal_structure = StructureDetector(StructureConfig(
            length=self.config.internal_length,
            structure_type=StructureType.INTERNAL
        ))
        self.swing_structure = StructureDetector(StructureConfig(
            length=self.config.swing_length,
            structure_type=StructureType.SWING
        ))
        self.ob_detector = OrderBlockDetector(OrderBlockConfig(
            internal_length=self.config.internal_length,
            swing_length=self.config.swing_length,
            atr_period=self.config.atr_period,
            atr_multiplier=self.config.atr_multiplier,
        ))
        self.liquidity_detector = LiquidityDetector(LiquidityConfig(
            lookback=self.config.liquidity_lookback,
            min_touches=self.config.liquidity_min_touches,
        ))
        self.equal_levels_detector = EqualLevelsDetector(EqualLevelsConfig(
            threshold_pct=self.config.equal_threshold_pct,
            min_touches=self.config.equal_min_touches,
            lookback=self.config.liquidity_lookback,
        ))
        self.fvg_detector = FVGDetector(FVGConfig(
            min_gap_size_pct=self.config.fvg_min_gap_pct,
        ))

    def analyze(
        self,
        candles: list[Candle],
        symbol: str,
        timeframe: Timeframe,
    ) -> MarketStructureState:
        """
        Run complete SMC analysis on candle data.

        Args:
            candles: List of candles in chronological order (oldest first)
            symbol: Trading symbol
            timeframe: Candle timeframe

        Returns:
            Complete MarketStructureState
        """
        if len(candles) < self.config.swing_length + self.config.atr_period + 10:
            raise ValueError(
                f"Insufficient candles: need at least "
                f"{self.config.swing_length + self.config.atr_period + 10}, got {len(candles)}"
            )

        # 1. Volatility parsing (ATR-based)
        parsed_candles = parse_candles_with_volatility(
            candles=candles,
            atr_period=self.config.atr_period,
            atr_multiplier=self.config.atr_multiplier,
        )

        # 2. Find pivots for both structures
        internal_pivots = self.internal_structure.find_pivots(parsed_candles)
        swing_pivots = self.swing_structure.find_pivots(parsed_candles)

        # 3. Detect structure breaks
        internal_breaks = self.internal_structure.detect_breaks(parsed_candles, internal_pivots)
        swing_breaks = self.swing_structure.detect_breaks(parsed_candles, swing_pivots)

        # 4. Detect Order Blocks
        order_blocks = self.ob_detector.detect_order_blocks(
            parsed_candles=parsed_candles,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            internal_pivots=internal_pivots,
            swing_pivots=swing_pivots,
        )

        # 5. Detect Equal Highs/Lows
        equal_highs, equal_lows = self.equal_levels_detector.detect_equal_levels(
            candles=candles,
            pivot_highs=[p for p in internal_pivots + swing_pivots if p.is_high],
            pivot_lows=[p for p in internal_pivots + swing_pivots if not p.is_high],
        )

        # 6. Detect Liquidity
        buy_side_liq, sell_side_liq = self.liquidity_detector.detect_liquidity(
            candles=candles,
            pivot_highs=[p for p in swing_pivots if p.is_high],
            pivot_lows=[p for p in swing_pivots if not p.is_high],
            equal_highs=equal_highs,
            equal_lows=equal_lows,
        )

        # 7. Detect FVGs
        fair_value_gaps = self.fvg_detector.detect_fvgs(candles)

        # 8. Determine current trends
        internal_trend = self._get_current_trend(internal_breaks)
        swing_trend = self._get_current_trend(swing_breaks)

        # Build state
        state = MarketStructureState(
            symbol=symbol,
            timeframe=timeframe.value,
            last_updated=datetime.now(),
            internal_pivots=internal_pivots,
            swing_pivots=swing_pivots,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            buy_side_liquidity=buy_side_liq,
            sell_side_liquidity=sell_side_liq,
            order_blocks=order_blocks,
            fair_value_gaps=fair_value_gaps,
            internal_trend=internal_trend,
            swing_trend=swing_trend,
        )

        return state

    def analyze_incremental(
        self,
        state: MarketStructureState,
        new_candles: list[Candle],
    ) -> MarketStructureState:
        """
        Incrementally update state with new candles.

        This avoids full re-analysis for real-time updates.
        """
        # For now, re-analyze full dataset
        # TODO: Implement true incremental updates
        all_candles = self._reconstruct_candles(state) + new_candles
        return self.analyze(all_candles, state.symbol, Timeframe(state.timeframe))

    def _get_current_trend(self, breaks: list[StructureBreak]) -> TrendDirection:
        """Determine current trend from latest breaks."""
        if not breaks:
            return TrendDirection.RANGING

        latest_break = max(breaks, key=lambda b: b.timestamp)
        return latest_break.direction

    def _reconstruct_candles(self, state: MarketStructureState) -> list[Candle]:
        """Reconstruct candle list from state (for incremental updates)."""
        # This is a placeholder - in practice, you'd store candles separately
        # or maintain a rolling window
        candles = []

        # Add formation candles from order blocks
        for ob in state.order_blocks:
            candles.append(ob.formation_candle)

        # Add break confirmation candles
        for brk in state.internal_breaks + state.swing_breaks:
            candles.append(brk.confirmation_candle)

        # Deduplicate and sort
        unique_candles = {}
        for c in candles:
            key = (c.timestamp, c.symbol, c.timeframe)
            unique_candles[key] = c

        return sorted(unique_candles.values(), key=lambda c: c.timestamp)
