"""
Main SMC Analyzer - orchestrates all SMC components.

This is the primary entry point for SMC analysis.
It coordinates:
1. Volatility parsing (ATR-based)
2. Structure detection (internal & swing) - LuxAlgo stateful streaming
3. Order Block detection - LuxAlgo slice semantics
4. Liquidity detection
5. Equal levels detection
6. FVG detection
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing: Optional
import numpy as np

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import (
    MarketStructureState, PivotPoint, StructureBreak, OrderBlock,
    LiquidityLevel, EqualLevel, FairValueGap, TrendDirection,
    BreakType, StructureType
)
from quantedge.smc.volatility import calculate_atr, parse_candles_with_volatility, ParsedCandle
from quantedge.smc.structure import StructureDetector, detect_structure_streaming
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming
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

        # 2. Detect structure for both internal and swing (streaming/stateful)
        internal_highs, internal_lows, internal_breaks, internal_trend = detect_structure_streaming(
            parsed_candles=parsed_candles,
            length=self.config.internal_length,
            structure_type=StructureType.INTERNAL
        )
        swing_highs, swing_lows, swing_breaks, swing_trend = detect_structure_streaming(
            parsed_candles=parsed_candles,
            length=self.config.swing_length,
            structure_type=StructureType.SWING
        )

        # 3. Detect Order Blocks using LuxAlgo slice semantics
        order_blocks = detect_order_blocks_streaming(
            parsed_candles=parsed_candles,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            internal_pivots=internal_highs + internal_lows,
            swing_pivots=swing_highs + swing_lows,
            config=OrderBlockConfig(
                internal_length=self.config.internal_length,
                swing_length=self.config.swing_length,
                atr_period=self.config.atr_period,
                atr_multiplier=self.config.atr_multiplier,
            )
        )

        # 4. Detect Equal Highs/Lows
        equal_highs, equal_lows = self.equal_levels_detector.detect_equal_levels(
            candles=candles,
            pivot_highs=internal_highs + swing_highs,
            pivot_lows=internal_lows + swing_lows,
        )

        # 5. Detect Liquidity
        buy_side_liq, sell_side_liq = self.liquidity_detector.detect_liquidity(
            candles=candles,
            pivot_highs=swing_highs,
            pivot_lows=swing_lows,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
        )

        # 6. Detect FVGs
        fair_value_gaps = self.fvg_detector.detect_fvgs(candles)

        # Build state
        state = MarketStructureState(
            symbol=symbol,
            timeframe=timeframe.value,
            last_updated=datetime.now(),
            internal_pivots=internal_highs + internal_lows,
            swing_pivots=swing_highs + swing_lows,
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
        # TODO: Implement true incremental updates using streaming detectors
        all_candles = self._reconstruct_candles(state) + new_candles
        return self.analyze(all_candles, state.symbol, Timeframe(state.timeframe))

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