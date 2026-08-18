"""
Tests for Confidence Scoring - 8-Factor Model and Edge Cases.

Tests cover:
1. OB State scoring (FRESH=15, TOUCHED=10, USED/INVALIDATED=0)
2. Trend alignment scoring
3. BOS/CHOCH scoring
4. Confidence threshold (85)
5. Edge cases and boundary conditions

Note: 8-factor model max total = 85, not 100. Threshold = 85 means ALL max points needed.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import (
    OrderBlock, MarketStructureState, TrendDirection, BreakType, OBState
)
from quantedge.strategy.models import (
    StrategyConfig, AccountState, ConfidenceFactors, TradeDirection
)
from quantedge.strategy.confidence import ConfidenceScorer


class TestConfidenceFactors:
    """Test ConfidenceFactors dataclass (8-factor model, max=85)."""

    def test_total_calculation_max(self):
        """Test total score calculation with max points (85 max)."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 85

    def test_threshold_check_exact(self):
        """Test threshold checking at exactly 85."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 85
        assert factors.meets_threshold(85)

    def test_threshold_check_below(self):
        """Test threshold checking below 85."""
        factors = ConfidenceFactors(
            trend_alignment=10,
            ob_state=10,
            bos_choch=10,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 70
        assert not factors.meets_threshold(85)
        assert factors.meets_threshold(70)

    def test_exact_threshold(self):
        """Test exactly at threshold."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 85
        assert factors.meets_threshold(85)


class TestOBStateScoring:
    """Test OB State factor scoring (replaces Freshness + First Touch)."""

    def create_test_ob(self, state: OBState, bullish: bool = True) -> OrderBlock:
        """Create test OrderBlock with specified state."""
        candle = Candle(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1, 0, 0, 0),
            open=Decimal("43000"),
            high=Decimal("43200"),
            low=Decimal("42800"),
            close=Decimal("43100"),
            volume=Decimal("100"),
        )

        return OrderBlock(
            index=100,
            symbol="BTCUSD.P",
            timeframe="1h",
            type="BULLISH" if bullish else "BEARISH",
            top_price=Decimal("43200"),
            bottom_price=Decimal("42800"),
            formation_candle=candle,
            formation_index=100,
            break_index=101,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BEARISH if bullish else TrendDirection.BULLISH,
            state=state,
        )

    def create_test_state(self, bullish: bool = True) -> MarketStructureState:
        """Create test MarketStructureState."""
        trend = TrendDirection.BULLISH if bullish else TrendDirection.BEARISH
        return MarketStructureState(
            symbol="BTCUSD.P",
            timeframe="1h",
            last_updated=datetime.now(),
            internal_trend=trend,
            swing_trend=trend,
            buy_side_liquidity=[],
            sell_side_liquidity=[],
            order_blocks=[],
            fair_value_gaps=[],
        )

    def test_fresh_ob_scores_15(self):
        """FRESH OB should score 15."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob = self.create_test_ob(OBState.FRESH)
        state = self.create_test_state()
        score = scorer._score_ob_state(ob)
        assert score == 15

    def test_touched_ob_scores_10(self):
        """TOUCHED OB should score 10."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob = self.create_test_ob(OBState.TOUCHED)
        state = self.create_test_state()
        score = scorer._score_ob_state(ob)
        assert score == 10

    def test_used_ob_scores_0(self):
        """USED OB should score 0."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob = self.create_test_ob(OBState.USED)
        state = self.create_test_state()
        score = scorer._score_ob_state(ob)
        assert score == 0

    def test_invalidated_ob_scores_0(self):
        """INVALIDATED OB should score 0."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob = self.create_test_ob(OBState.INVALIDATED)
        state = self.create_test_state()
        score = scorer._score_ob_state(ob)
        assert score == 0


class TestTrendAlignmentScoring:
    """Test trend alignment scoring."""

    def create_aligned_ob_state(self, bullish: bool = True):
        """Create OB and state with aligned trends."""
        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h",
            type="BULLISH" if bullish else "BEARISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=BreakType.BOS,
            trend_before_break=TrendDirection.BEARISH if bullish else TrendDirection.BULLISH,
        )
        trend = TrendDirection.BULLISH if bullish else TrendDirection.BEARISH
        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=trend, swing_trend=trend,
            buy_side_liquidity=[], sell_side_liquidity=[],
            order_blocks=[], fair_value_gaps=[],
        )
        return ob, state

    def test_fully_aligned_scores_15(self):
        """Both swing and internal aligned = 15."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_aligned_ob_state(bullish=True)
        score = scorer._score_trend_alignment(ob, state)
        assert score == 15

    def test_swing_aligned_internal_ranging_scores_10(self):
        """Swing aligned (8) + internal ranging (2) = 10."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_aligned_ob_state(bullish=True)
        state.internal_trend = TrendDirection.RANGING
        score = scorer._score_trend_alignment(ob, state)
        assert score == 10

    def test_swing_ranging_internal_aligned_scores_10(self):
        """Swing ranging (3) + internal aligned (7) = 10."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_aligned_ob_state(bullish=True)
        state.swing_trend = TrendDirection.RANGING
        score = scorer._score_trend_alignment(ob, state)
        assert score == 10

    def test_both_ranging_scores_5(self):
        """Both ranging = 5."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_aligned_ob_state(bullish=True)
        state.swing_trend = TrendDirection.RANGING
        state.internal_trend = TrendDirection.RANGING
        score = scorer._score_trend_alignment(ob, state)
        assert score == 5

    def test_conflicting_trends_scores_low(self):
        """Conflicting trends (swing bullish, internal bearish) = low score."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_aligned_ob_state(bullish=True)
        state.internal_trend = TrendDirection.BEARISH
        score = scorer._score_trend_alignment(ob, state)
        assert score <= 8


class TestBOSCHOCHScoring:
    """Test BOS/CHOCH scoring."""

    def create_ob_with_break_type(self, break_type: BreakType):
        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h",
            type="BULLISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=break_type,
            trend_before_break=TrendDirection.BEARISH,
        )
        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=TrendDirection.BULLISH, swing_trend=TrendDirection.BULLISH,
            buy_side_liquidity=[], sell_side_liquidity=[],
            order_blocks=[], fair_value_gaps=[],
        )
        return ob, state

    def test_choch_scores_15(self):
        """CHOCH should score 15."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_ob_with_break_type(BreakType.CHOCH)
        score = scorer._score_bos_choch(ob, state)
        assert score == 15

    def test_bos_scores_10(self):
        """BOS should score 10."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob, state = self.create_ob_with_break_type(BreakType.BOS)
        score = scorer._score_bos_choch(ob, state)
        assert score == 10


class TestLiquiditySweepScoring:
    """Test liquidity sweep scoring."""

    def test_aligned_sweep_scores_10(self):
        """Aligned sweep scores 10."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h", type="BULLISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=BreakType.BOS, trend_before_break=TrendDirection.BEARISH,
        )

        from quantedge.smc.models import LiquidityLevel
        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=TrendDirection.BULLISH, swing_trend=TrendDirection.BULLISH,
            buy_side_liquidity=[],
            sell_side_liquidity=[
                LiquidityLevel(
                    price=Decimal("42700"),
                    timestamp=datetime.now(),
                    is_buy_side=False,
                    strength=0.8,
                    is_swept=True,
                    swept_at=datetime.now()
                )
            ],
            order_blocks=[], fair_value_gaps=[],
        )

        score = scorer._score_liquidity_sweep(ob, state)
        assert score == 10

    def test_no_sweep_scores_0(self):
        """No sweep scores 0."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h", type="BULLISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=BreakType.BOS, trend_before_break=TrendDirection.BEARISH,
        )

        from quantedge.smc.models import LiquidityLevel
        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=TrendDirection.BULLISH, swing_trend=TrendDirection.BULLISH,
            buy_side_liquidity=[],
            sell_side_liquidity=[
                LiquidityLevel(
                    price=Decimal("42700"),
                    timestamp=datetime.now(),
                    is_buy_side=False,
                    strength=0.8,
                    is_swept=False,
                )
            ],
            order_blocks=[], fair_value_gaps=[],
        )

        score = scorer._score_liquidity_sweep(ob, state)
        assert score == 0


class TestConfidenceThreshold:
    """Test confidence threshold behavior."""

    def test_exactly_85_passes(self):
        """Exactly 85 should pass threshold."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 85
        assert factors.meets_threshold(85)

    def test_84_fails(self):
        """84 should fail threshold."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=14,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 84
        assert not factors.meets_threshold(85)

    def test_86_passes(self):
        """86 should pass threshold."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_state=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=6,
        )
        assert factors.total == 86
        assert factors.meets_threshold(85)


class TestConfidenceScorerIntegration:
    """Integration tests for full confidence scoring."""

    def test_used_ob_fails_threshold(self):
        """USED OB should fail threshold (0 for OB State)."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h", type="BULLISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=BreakType.CHOCH, trend_before_break=TrendDirection.BEARISH,
            state=OBState.USED,
        )

        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=TrendDirection.BULLISH, swing_trend=TrendDirection.BULLISH,
            buy_side_liquidity=[], sell_side_liquidity=[],
            order_blocks=[], fair_value_gaps=[],
        )

        factors = scorer.score(ob, state, Decimal("10000"))
        assert factors.total <= 85
        assert not factors.meets_threshold(85)

    def test_invalidated_ob_fails_threshold(self):
        """INVALIDATED OB should fail threshold."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        candle = Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2024,1,1), open=Decimal("43000"),
            high=Decimal("43200"), low=Decimal("42800"), close=Decimal("43100"),
            volume=Decimal("100")
        )
        ob = OrderBlock(
            index=100, symbol="BTCUSD.P", timeframe="1h", type="BULLISH",
            top_price=Decimal("43200"), bottom_price=Decimal("42800"),
            formation_candle=candle, formation_index=100, break_index=101,
            break_type=BreakType.CHOCH, trend_before_break=TrendDirection.BEARISH,
            state=OBState.INVALIDATED,
        )

        state = MarketStructureState(
            symbol="BTCUSD.P", timeframe="1h", last_updated=datetime.now(),
            internal_trend=TrendDirection.BULLISH, swing_trend=TrendDirection.BULLISH,
            buy_side_liquidity=[], sell_side_liquidity=[],
            order_blocks=[], fair_value_gaps=[],
        )

        factors = scorer.score(ob, state, Decimal("10000"))
        assert factors.total <= 85
        assert not factors.meets_threshold(85)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])