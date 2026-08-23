"""
AI Intelligence Engine interfaces and deterministic baseline implementation.
"""

from decimal import Decimal
from typing import Protocol
from quantedge.ai.models import (
    FeatureVector,
    MarketContext,
    MarketRegime,
    PatternMetrics,
)
from quantedge.strategy.models import StrategyDecision, StrategyDirection


class AiIntelligenceEngine(Protocol):
    """Protocol for AI Intelligence evaluation engines."""

    def evaluate_signal(
        self,
        decision: StrategyDecision,
        features: FeatureVector,
    ) -> PatternMetrics:
        """Evaluate strategy decision and extracted features to produce pattern metrics."""
        ...

    def version(self) -> str:
        """Returns the engine/model version identifier."""
        ...


class DeterministicBaselineAiEngine:
    """
    Deterministic rule-calibrated baseline AI Intelligence Engine.
    Requires zero external LLMs or cloud API keys.
    """

    def version(self) -> str:
        return "1.0.0-baseline"

    def evaluate_signal(
        self,
        decision: StrategyDecision,
        features: FeatureVector,
    ) -> PatternMetrics:
        # 1. Pattern Score (0.0 - 100.0)
        # Weights: trend_alignment (40%), ob_depth_ratio (30%), candle_momentum (30%)
        p_score = (
            features.trend_alignment * Decimal("40.0")
            + features.ob_depth_ratio * Decimal("30.0")
            + features.candle_momentum * Decimal("30.0")
        )
        pattern_score = min(Decimal("100.00"), max(Decimal("0.00"), p_score)).quantize(Decimal("0.01"))

        # 2. Signal Score (0.0 - 100.0)
        # Combines pattern score (60%) and risk-reward efficiency (40%)
        s_score = pattern_score * Decimal("0.60") + (features.risk_reward_efficiency * Decimal("40.0"))
        signal_score = min(Decimal("100.00"), max(Decimal("0.00"), s_score)).quantize(Decimal("0.01"))

        # 3. Calibrated Confidence (0.0 - 100.0)
        # Uses decision's SMC confidence if present as prior, adjusted by volume & pattern score
        smc_conf = Decimal(str(decision.confidence)) if decision.confidence is not None else Decimal("70.00")
        calibrated = (smc_conf * Decimal("0.50")) + (signal_score * Decimal("0.35")) + (features.volume_expansion_ratio * Decimal("5.00"))
        confidence = min(Decimal("100.00"), max(Decimal("0.00"), calibrated)).quantize(Decimal("0.01"))

        # 4. Market Regime Classification
        if features.volatility_zscore > Decimal("1.20"):
            regime = MarketRegime.VOLATILITY_EXPANSION
        elif features.trend_alignment >= Decimal("0.75"):
            regime = (
                MarketRegime.BULLISH_TRENDING
                if decision.direction == StrategyDirection.LONG
                else MarketRegime.BEARISH_TRENDING
            )
        elif features.volatility_zscore < Decimal("-0.40"):
            regime = MarketRegime.CHOPPY_RANGE
        else:
            regime = MarketRegime.TRANSITIONAL

        # 5. Market Context Classification
        if features.trend_alignment >= Decimal("0.80") and features.volume_expansion_ratio >= Decimal("1.10"):
            context = MarketContext.FAVORABLE_TREND_CONTINUATION
        elif features.volume_expansion_ratio > Decimal("1.80"):
            context = MarketContext.COMPRESSED_LIQUIDITY_SWEEP
        elif features.trend_alignment < Decimal("0.45"):
            context = MarketContext.UNFAVORABLE_HIGH_NOISE
        else:
            context = MarketContext.EQUILIBRIUM_REVERSION

        summary = {
            "trend_alignment": str(features.trend_alignment),
            "ob_depth_ratio": str(features.ob_depth_ratio),
            "volume_expansion_ratio": str(features.volume_expansion_ratio),
            "risk_reward_efficiency": str(features.risk_reward_efficiency),
            "volatility_zscore": str(features.volatility_zscore),
        }

        return PatternMetrics(
            pattern_score=pattern_score,
            signal_score=signal_score,
            confidence=confidence,
            regime=regime,
            context=context,
            feature_summary=summary,
        )
