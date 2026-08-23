"""
Quantitative Feature Extraction for AI Intelligence Engine.

Extracts normalized statistical, momentum, and geometric features from market data
and strategy decisions without modifying underlying SMC structures.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from quantedge.ai.models import FeatureVector
from quantedge.strategy.models import StrategyDecision, StrategyDirection


class FeatureExtractor:
    """
    Extracts structured feature vectors from market candles and StrategyDecision.
    Guaranteed pure extraction — does not modify input objects.
    """

    @staticmethod
    def extract_features(
        decision: StrategyDecision,
        candles: Optional[List[Dict[str, Any]]] = None,
    ) -> FeatureVector:
        """
        Extracts a normalized FeatureVector for the given strategy decision.
        """
        # 1. Risk-Reward Efficiency
        rr_eff = Decimal("0.50")
        if decision.risk_reward is not None and decision.risk_reward > Decimal("0"):
            # Normalize RR (e.g., RR 2.0 -> 0.67, RR 3.0 -> 1.00)
            rr_eff = min(Decimal("1.00"), decision.risk_reward / Decimal("3.00"))

        # 2. OB Depth / Placement Ratio
        ob_ratio = Decimal("0.50")
        top = decision.order_block_upper_edge or (Decimal(str(decision.ob_zone[0])) if decision.ob_zone else None)
        bot = decision.order_block_lower_edge or (Decimal(str(decision.ob_zone[1])) if decision.ob_zone else None)
        if decision.entry is not None and top is not None and bot is not None:
            zone_span = abs(top - bot)
            if zone_span > Decimal("0"):
                dist = abs(decision.entry - bot)
                ob_ratio = min(Decimal("1.00"), max(Decimal("0.00"), dist / zone_span))

        # 3. Candle Analysis & Volatility from recent candles
        trend_align = Decimal("0.70")
        vol_z = Decimal("0.00")
        vol_exp = Decimal("1.00")
        momentum = Decimal("0.50")

        if candles and len(candles) >= 5:
            recent = candles[-5:]
            closes = [Decimal(str(c.get("close", 0))) for c in recent]
            highs = [Decimal(str(c.get("high", 0))) for c in recent]
            lows = [Decimal(str(c.get("low", 0))) for c in recent]
            volumes = [Decimal(str(c.get("volume", 1))) for c in recent]

            # Price trend
            price_change = closes[-1] - closes[0]
            if decision.direction == StrategyDirection.LONG:
                trend_align = Decimal("0.85") if price_change >= Decimal("0") else Decimal("0.40")
            elif decision.direction == StrategyDirection.SHORT:
                trend_align = Decimal("0.85") if price_change <= Decimal("0") else Decimal("0.40")

            # Volatility expansion
            ranges = [h - l for h, l in zip(highs, lows)]
            avg_range = sum(ranges) / Decimal(len(ranges))
            if avg_range > Decimal("0"):
                latest_range = ranges[-1]
                vol_exp = min(Decimal("3.00"), latest_range / avg_range)
                vol_z = (latest_range - avg_range) / avg_range

            # Volume expansion
            avg_vol = sum(volumes) / Decimal(len(volumes))
            if avg_vol > Decimal("0"):
                vol_exp = min(Decimal("3.00"), volumes[-1] / avg_vol)

            # Latest candle momentum
            candle_span = highs[-1] - lows[-1]
            if candle_span > Decimal("0"):
                body = closes[-1] - Decimal(str(recent[-1].get("open", closes[-1])))
                momentum = min(Decimal("1.00"), max(Decimal("0.00"), (body / candle_span + Decimal("1.00")) / Decimal("2.00")))

        raw = {
            "symbol": decision.symbol,
            "direction": decision.direction.value if decision.direction else "NONE",
            "timeframe": decision.timeframe,
            "has_ob_zone": decision.ob_zone is not None,
            "candles_analyzed": len(candles) if candles else 0,
        }

        return FeatureVector(
            trend_alignment=trend_align,
            ob_depth_ratio=ob_ratio,
            volatility_zscore=vol_z,
            volume_expansion_ratio=vol_exp,
            risk_reward_efficiency=rr_eff,
            candle_momentum=momentum,
            raw_features=raw,
        )
