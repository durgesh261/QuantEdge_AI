"""
Data models for QuantEdge AI Intelligence Layer.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional


class MarketRegime(str, Enum):
    """Macro and micro structural regime classifications."""
    BULLISH_TRENDING = "BULLISH_TRENDING"
    BEARISH_TRENDING = "BEARISH_TRENDING"
    CHOPPY_RANGE = "CHOPPY_RANGE"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    TRANSITIONAL = "TRANSITIONAL"


class MarketContext(str, Enum):
    """Contextual trade environment classification."""
    FAVORABLE_TREND_CONTINUATION = "FAVORABLE_TREND_CONTINUATION"
    COUNTER_TREND_EXHAUSTION = "COUNTER_TREND_EXHAUSTION"
    COMPRESSED_LIQUIDITY_SWEEP = "COMPRESSED_LIQUIDITY_SWEEP"
    EQUILIBRIUM_REVERSION = "EQUILIBRIUM_REVERSION"
    UNFAVORABLE_HIGH_NOISE = "UNFAVORABLE_HIGH_NOISE"


@dataclass(frozen=True)
class FeatureVector:
    """Quantitative feature vector extracted from candle history and setup geometry."""
    trend_alignment: Decimal = Decimal("0.50")
    ob_depth_ratio: Decimal = Decimal("0.50")
    volatility_zscore: Decimal = Decimal("0.00")
    volume_expansion_ratio: Decimal = Decimal("1.00")
    risk_reward_efficiency: Decimal = Decimal("1.00")
    candle_momentum: Decimal = Decimal("0.00")
    raw_features: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatternMetrics:
    """Core intelligence metrics computed by the AI Engine."""
    pattern_score: Decimal
    signal_score: Decimal
    confidence: Decimal
    regime: MarketRegime
    context: MarketContext
    feature_summary: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for score_name, score_val in [
            ("pattern_score", self.pattern_score),
            ("signal_score", self.signal_score),
            ("confidence", self.confidence),
        ]:
            if not isinstance(score_val, Decimal):
                score_dec = Decimal(str(score_val))
                object.__setattr__(self, score_name, score_dec)
            else:
                score_dec = score_val

            if score_dec < Decimal("0.00") or score_dec > Decimal("100.00"):
                raise ValueError(f"{score_name} must be between 0.00 and 100.00, got {score_dec}")


@dataclass(frozen=True)
class AiEnrichmentResult:
    """Authoritative AI enrichment artifact linked strictly to a setup_id."""
    setup_id: str
    symbol: str
    direction: str
    intelligence_version: str
    pattern_score: Decimal
    signal_score: Decimal
    confidence: Decimal
    market_regime: MarketRegime
    market_context: MarketContext
    model_metadata: Dict[str, Any] = field(default_factory=dict)
    feature_summary: Dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.setup_id or not self.setup_id.strip():
            raise ValueError("setup_id cannot be empty")
        for score_name, score_val in [
            ("pattern_score", self.pattern_score),
            ("signal_score", self.signal_score),
            ("confidence", self.confidence),
        ]:
            val_dec = Decimal(str(score_val)) if not isinstance(score_val, Decimal) else score_val
            if val_dec < Decimal("0.00") or val_dec > Decimal("100.00"):
                raise ValueError(f"{score_name} must be between 0.00 and 100.00, got {val_dec}")
            object.__setattr__(self, score_name, val_dec)
