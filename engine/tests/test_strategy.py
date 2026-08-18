"""
Tests for Strategy Engine - Confidence Scoring & Risk
"""

import pytest
from decimal import Decimal
from datetime import datetime
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import (
    OrderBlock, MarketStructureState, TrendDirection, BreakType, StructureType, PivotPoint
)
from quantedge.strategy.models import (
    StrategyConfig, AccountState, ConfidenceFactors, TradeDirection, StrategySignal
)
from quantedge.strategy.confidence import ConfidenceScorer
from quantedge.strategy.risk import RiskCalculator


class TestConfidenceScoring:
    """Test 9-factor confidence scoring."""

    def create_test_ob(self, bullish: bool = True, touch_count: int = 0, break_type: BreakType = BreakType.BOS) -> OrderBlock:
        """Create a test OrderBlock."""
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
            top_price=Decimal("43200") if bullish else Decimal("43200"),
            bottom_price=Decimal("42800") if bullish else Decimal("42800"),
            formation_candle=candle,
            formation_index=100,
            break_index=101,
            break_type=break_type,
            trend_before_break=TrendDirection.BEARISH if bullish else TrendDirection.BULLISH,
            touch_count=touch_count,
            is_used=False,
            is_invalidated=False,
            swing_trend=TrendDirection.BULLISH if bullish else TrendDirection.BEARISH,
            internal_trend=TrendDirection.BULLISH if bullish else TrendDirection.BEARISH,
        )

    def create_test_state(self, bullish: bool = True) -> MarketStructureState:
        """Create a test MarketStructureState."""
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

    def test_confidence_factors_total(self):
        """Test ConfidenceFactors total calculation."""
        factors = ConfidenceFactors(
            trend_alignment=15,
            ob_freshness=15,
            first_touch=15,
            bos_choch=15,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 100
        assert factors.meets_threshold(85)

    def test_confidence_threshold(self):
        """Test threshold checking."""
        factors = ConfidenceFactors(
            trend_alignment=10,
            ob_freshness=10,
            first_touch=10,
            bos_choch=10,
            liquidity_sweep=10,
            premium_discount=10,
            session_volatility=5,
            risk_reward=10,
            news_macro_safety=5,
        )
        assert factors.total == 80
        assert not factors.meets_threshold(85)
        assert factors.meets_threshold(80)

    def test_scorer_trend_alignment(self):
        """Test trend alignment scoring."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        # Aligned trends
        ob = self.create_test_ob(bullish=True)
        state = self.create_test_state(bullish=True)
        score = scorer._score_trend_alignment(ob, state)
        assert score == 15  # 8 + 7

        # Conflicting trends
        state_conflict = self.create_test_state(bullish=False)
        state_conflict.swing_trend = TrendDirection.BULLISH
        state_conflict.internal_trend = TrendDirection.BEARISH
        score = scorer._score_trend_alignment(ob, state_conflict)
        # Should be lower due to conflict
        assert score < 15

    def test_scorer_ob_freshness(self):
        """Test OB freshness scoring."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        # Fresh OB (touch_count=0)
        ob_fresh = self.create_test_ob(touch_count=0)
        state = self.create_test_state()
        score = scorer._score_ob_freshness(ob_fresh, state)
        assert score == 15

        # One touch
        ob_touched = self.create_test_ob(touch_count=1)
        score = scorer._score_ob_freshness(ob_touched, state)
        assert score == 10

        # Two touches
        ob_retouched = self.create_test_ob(touch_count=2)
        score = scorer._score_ob_freshness(ob_retouched, state)
        assert score == 0

    def test_scorer_first_touch(self):
        """Test first touch scoring (binary)."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob_fresh = self.create_test_ob(touch_count=0)
        state = self.create_test_state()
        score = scorer._score_first_touch(ob_fresh)
        assert score == 15

        ob_touched = self.create_test_ob(touch_count=1)
        score = scorer._score_first_touch(ob_touched)
        assert score == 0

    def test_scorer_bos_choch(self):
        """Test BOS/CHOCH scoring."""
        config = StrategyConfig()
        scorer = ConfidenceScorer(config)

        ob_bos = self.create_test_ob(break_type=BreakType.BOS)
        state = self.create_test_state()
        score = scorer._score_bos_choch(ob_bos, state)
        assert score == 10

        ob_choch = self.create_test_ob(break_type=BreakType.CHOCH)
        score = scorer._score_bos_choch(ob_choch, state)
        assert score == 15


class TestRiskCalculator:
    """Test risk calculations."""

    def setup_method(self):
        self.config = StrategyConfig()
        self.calculator = RiskCalculator(self.config)

    def test_validate_risk_basic(self):
        """Test basic risk validation."""
        result = self.calculator.validate_risk(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("42800"),
            account_balance=Decimal("10000"),
            account_equity=Decimal("10000"),
        )

        assert result.is_valid
        assert result.max_position_size is not None
        assert result.max_leverage is not None
        assert result.max_leverage <= 100

    def test_validate_risk_zero_distance(self):
        """Test rejection when entry == stop loss."""
        result = self.calculator.validate_risk(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("43000"),
            account_balance=Decimal("10000"),
            account_equity=Decimal("10000"),
        )

        assert not result.is_valid
        assert "Zero risk distance" in result.rejection_reason

    def test_validate_risk_insufficient_balance(self):
        """Test rejection with zero balance."""
        result = self.calculator.validate_risk(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("42800"),
            account_balance=Decimal("0"),
            account_equity=Decimal("0"),
        )

        assert not result.is_valid
        assert "Insufficient account balance" in result.rejection_reason

    def test_calculate_take_profit_long(self):
        """Test TP calculation for long."""
        tp = self.calculator.calculate_take_profit(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("42800"),
            direction=TradeDirection.LONG,
            account_balance=Decimal("10000"),
            risk_amount=Decimal("3500"),  # 35% of 10000
        )

        # risk_distance = 200
        # position_size = 3500 / 200 = 17.5
        # price_move = 6000 / 17.5 = 342.857...
        # TP = 43000 + 342.857 = 43342.857...
        expected_move = Decimal("6000") / (Decimal("3500") / Decimal("200"))
        expected_tp = Decimal("43000") + expected_move

        assert abs(tp - expected_tp) < Decimal("1")

    def test_calculate_take_profit_short(self):
        """Test TP calculation for short."""
        tp = self.calculator.calculate_take_profit(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("43200"),
            direction=TradeDirection.SHORT,
            account_balance=Decimal("10000"),
            risk_amount=Decimal("3500"),
        )

        expected_move = Decimal("6000") / (Decimal("3500") / Decimal("200"))
        expected_tp = Decimal("43000") - expected_move

        assert abs(tp - expected_tp) < Decimal("1")

    def test_account_rr(self):
        """Test account-level R:R calculation."""
        rr = self.calculator.calculate_account_rr()
        # 60 / 35 = 1.714...
        assert abs(rr - Decimal("1.714")) < Decimal("0.01")

    def test_leverage_cap(self):
        """Test leverage capped at 100x."""
        # Very tight stop -> high leverage needed
        result = self.calculator.validate_risk(
            entry_price=Decimal("43000"),
            stop_loss=Decimal("42999"),  # 1 point risk
            account_balance=Decimal("10000"),
            account_equity=Decimal("10000"),
        )

        # With 1 point risk, position would be huge
        # Leverage should be capped at 100
        assert result.is_valid
        assert result.max_leverage <= 100