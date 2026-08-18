"""
Confidence Scoring Engine - 9 Factor Model.

Per strategy specification:
1. Trend Alignment              15
2. OB Freshness                 15
3. First Touch                  15
4. BOS / CHOCH                  15
5. Liquidity Sweep              10
6. Premium / Discount           10
7. Session / Volatility          5
8. Risk / Reward                10
9. News / Macro Safety            5
                                  ---
                                 100
"""

from decimal import Decimal
from typing import Optional
from quantedge.smc.models import OrderBlock, MarketStructureState, TrendDirection, BreakType
from quantedge.strategy.models import ConfidenceFactors, TradeDirection


class ConfidenceScorer:
    """Calculates confidence score using 9-factor model."""

    def __init__(self, config):
        self.config = config

    def score(self, ob: OrderBlock, state: MarketStructureState, account_balance: Decimal) -> ConfidenceFactors:
        """Calculate all 9 confidence factors."""

        factors = ConfidenceFactors(
            trend_alignment=self._score_trend_alignment(ob, state),
            ob_freshness=self._score_ob_freshness(ob, state),
            first_touch=self._score_first_touch(ob),
            bos_choch=self._score_bos_choch(ob, state),
            liquidity_sweep=self._score_liquidity_sweep(ob, state),
            premium_discount=self._score_premium_discount(ob, state),
            session_volatility=self._score_session_volatility(state),
            risk_reward=self._score_risk_reward(ob, account_balance),
            news_macro_safety=self._score_news_macro_safety(),
        )

        return factors

    def _score_trend_alignment(self, ob: OrderBlock, state: MarketStructureState) -> int:
        """
        Factor 1: Trend Alignment (15 pts)

        Both swing and internal trends must align with OB direction.
        """
        score = 0
        ob_bullish = ob.is_bullish()

        # Swing trend alignment (8 pts)
        if ob_bullish and state.swing_trend == TrendDirection.BULLISH:
            score += 8
        elif not ob_bullish and state.swing_trend == TrendDirection.BEARISH:
            score += 8
        elif state.swing_trend == TrendDirection.RANGING:
            score += 3  # Partial for ranging

        # Internal trend alignment (7 pts)
        if ob_bullish and state.internal_trend == TrendDirection.BULLISH:
            score += 7
        elif not ob_bullish and state.internal_trend == TrendDirection.BEARISH:
            score += 7
        elif state.internal_trend == TrendDirection.RANGING:
            score += 2

        return min(score, 15)

    def _score_ob_freshness(self, ob: OrderBlock, state: MarketStructureState) -> int:
        """
        Factor 2: OB Freshness (15 pts)

        Based on touch count and age.
        Fresh OB (touch_count=0) = 15 pts
        1 touch = 10 pts
        2+ touches = 0 pts (rejected by first touch rule)
        """
        if ob.touch_count == 0:
            return 15
        elif ob.touch_count == 1:
            return 10
        else:
            return 0

    def _score_first_touch(self, ob: OrderBlock) -> int:
        """
        Factor 3: First Touch (15 pts)

        Binary: first touch = 15, not first touch = 0
        """
        return 15 if ob.touch_count == 0 else 0

    def _score_bos_choch(self, ob: OrderBlock, state: MarketStructureState) -> int:
        """
        Factor 4: BOS / CHOCH (15 pts)

        CHOCH = 15 (trend reversal, stronger)
        BOS = 10 (continuation)
        """
        if ob.break_type == BreakType.CHOCH:
            return 15
        elif ob.break_type == BreakType.BOS:
            return 10
        return 0

    def _score_liquidity_sweep(self, ob: OrderBlock, state: MarketStructureState) -> int:
        """
        Factor 5: Liquidity Sweep (10 pts)

        Aligned sweep (in direction of OB) = 10
        Other sweep = 5
        No sweep = 0 (baseline - doesn't invalidate)
        """
        # Check if there was a liquidity sweep aligned with OB direction
        ob_bullish = ob.is_bullish()

        if ob_bullish:
            # Look for sell-side liquidity sweep (price went below then reversed up)
            for liq in state.sell_side_liquidity:
                if liq.is_swept and liq.swept_at and liq.swept_at > ob.formation_candle.timestamp:
                    return 10
        else:
            # Look for buy-side liquidity sweep
            for liq in state.buy_side_liquidity:
                if liq.is_swept and liq.swept_at and liq.swept_at > ob.formation_candle.timestamp:
                    return 10

        # Check for any sweep (non-aligned)
        any_swept = any(l.is_swept for l in state.buy_side_liquidity + state.sell_side_liquidity)
        return 5 if any_swept else 0

    def _score_premium_discount(self, ob: OrderBlock, state: MarketStructureState) -> int:
        """
        Factor 6: Premium / Discount (10 pts)

        OB in discount (lower half of range) for longs = 10
        OB in premium (upper half) for shorts = 10
        Otherwise = 0
        """
        # This requires swing high/low range - simplified for now
        # TODO: Calculate from recent swing structure
        return 5  # Neutral baseline

    def _score_session_volatility(self, state: MarketStructureState) -> int:
        """
        Factor 7: Session / Volatility (5 pts)

        Favorable session (London/NY overlap) = 5
        Asian session = 3
        Other = 0
        """
        # Simplified - would check actual session from timestamp
        return 3  # Baseline

    def _score_risk_reward(self, ob: OrderBlock, account_balance: Decimal) -> int:
        """
        Factor 8: Risk / Reward (10 pts)

        Based on achievable R:R from OB to target.
        Target = 60% account growth, Risk = 35% account
        Account-level R:R = 60/35 ≈ 1.71
        """
        # Calculate theoretical R:R from OB
        entry = ob.calculate_entry_price()
        sl = ob.calculate_stop_loss()
        risk_per_unit = abs(entry - sl)

        if risk_per_unit == 0:
            return 0

        # Target price movement for 60% account growth at max leverage
        # This is complex - simplified scoring
        theoretical_rr = Decimal("1.71")

        if theoretical_rr >= Decimal("2.0"):
            return 10
        elif theoretical_rr >= Decimal("1.5"):
            return 7
        elif theoretical_rr >= Decimal("1.0"):
            return 5
        return 0

    def _score_news_macro_safety(self) -> int:
        """
        Factor 9: News / Macro Safety (5 pts)

        No high-impact news = 5
        Medium impact = 3
        High impact = 0 (block)
        """
        # Placeholder - would integrate with news API
        return 5  # Assume safe for now
