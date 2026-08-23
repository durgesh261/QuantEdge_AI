"""
AI Signal Enricher with formal SMC invariance verification.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from quantedge.ai.engine import AiIntelligenceEngine, DeterministicBaselineAiEngine
from quantedge.ai.features import FeatureExtractor
from quantedge.ai.models import AiEnrichmentResult
from quantedge.strategy.models import StrategyDecision


class AiSignalEnricher:
    """
    Enriches deterministic SMC setups with AI intelligence while asserting
    strict invariance of all underlying SMC parameters.
    """

    def __init__(self, engine: Optional[AiIntelligenceEngine] = None):
        self.engine = engine if engine is not None else DeterministicBaselineAiEngine()

    def enrich_setup(
        self,
        decision: StrategyDecision,
        candles: Optional[List[Dict[str, Any]]] = None,
    ) -> AiEnrichmentResult:
        """
        Enriches a StrategyDecision with AI pattern intelligence.
        Guarantees that the StrategyDecision is not modified.
        """
        if not decision.setup_id:
            raise ValueError("StrategyDecision must have a valid setup_id for AI enrichment.")

        # Capture snapshot of authoritative SMC state before enrichment
        entry_before = decision.entry
        sl_before = decision.stop_loss
        tp_before = decision.take_profit
        rr_before = decision.risk_reward
        dir_before = decision.direction
        setup_id_before = decision.setup_id
        state_before = decision.setup_state

        # 1. Feature Extraction
        features = FeatureExtractor.extract_features(decision, candles)

        # 2. Intelligence Evaluation
        metrics = self.engine.evaluate_signal(decision, features)

        # 3. Formal Invariance Safety Verification
        if (
            decision.entry != entry_before
            or decision.stop_loss != sl_before
            or decision.take_profit != tp_before
            or decision.risk_reward != rr_before
            or decision.direction != dir_before
            or decision.setup_id != setup_id_before
            or decision.setup_state != state_before
        ):
            raise RuntimeError(
                "CRITICAL SAFETY VIOLATION: AI Intelligence Engine attempted to modify authoritative SMC parameters!"
            )

        metadata = {
            "engine": self.engine.__class__.__name__,
            "version": self.engine.version(),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

        return AiEnrichmentResult(
            setup_id=decision.setup_id,
            symbol=decision.symbol,
            direction=decision.direction.value if decision.direction else "NONE",
            intelligence_version=self.engine.version(),
            pattern_score=metrics.pattern_score,
            signal_score=metrics.signal_score,
            confidence=metrics.confidence,
            market_regime=metrics.regime,
            market_context=metrics.context,
            model_metadata=metadata,
            feature_summary=metrics.feature_summary,
            generated_at=datetime.now(timezone.utc),
        )
