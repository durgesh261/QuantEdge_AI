"""
QuantEdge AI Intelligence & Signal Enrichment Engine.

Provides pattern intelligence, signal scoring, market regime detection,
context classification, and calibrated confidence modeling.

STRICT SAFETY INVARIANT:
The AI module never alters SMC calculations, never mutates entry/SL/TP/RR/setup_id,
never executes orders, and never communicates with Delta Exchange.
"""

from quantedge.ai.models import (
    MarketRegime,
    MarketContext,
    FeatureVector,
    PatternMetrics,
    AiEnrichmentResult,
)
from quantedge.ai.features import FeatureExtractor
from quantedge.ai.engine import AiIntelligenceEngine, DeterministicBaselineAiEngine
from quantedge.ai.enricher import AiSignalEnricher

__all__ = [
    "MarketRegime",
    "MarketContext",
    "FeatureVector",
    "PatternMetrics",
    "AiEnrichmentResult",
    "FeatureExtractor",
    "AiIntelligenceEngine",
    "DeterministicBaselineAiEngine",
    "AiSignalEnricher",
]
