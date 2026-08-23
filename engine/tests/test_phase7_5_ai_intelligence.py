"""
Phase 7.5: AI Intelligence & Signal Enrichment Tests.

Validates that:
1. Deterministic SMC setups remain 100% invariant before and after AI enrichment.
2. AI scores (pattern, signal, confidence) are strictly bounded [0.0, 100.0].
3. Regime detection and context classification operate deterministically.
4. AI module has zero dependency on exchange execution or private APIs.
"""

from datetime import datetime, timezone
from decimal import Decimal
import inspect
import pytest

from quantedge.ai.enricher import AiSignalEnricher
from quantedge.ai.engine import DeterministicBaselineAiEngine
from quantedge.ai.features import FeatureExtractor
from quantedge.ai.models import (
    AiEnrichmentResult,
    FeatureVector,
    MarketContext,
    MarketRegime,
    PatternMetrics,
)
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
)


@pytest.fixture
def sample_decision() -> StrategyDecision:
    return StrategyDecision(
        timestamp=datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        timeframe="15m",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="setup-det-20260823-001",
        setup_type="BULLISH_OB_RETEST",
        entry=Decimal("60000.00"),
        stop_loss=Decimal("59000.00"),
        take_profit=Decimal("63000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.00"),
        minimum_risk_reward=Decimal("2.00"),
        confidence=80.0,
        order_block_upper_edge=Decimal("60500.00"),
        order_block_lower_edge=Decimal("59500.00"),
    )


@pytest.fixture
def sample_candles() -> list:
    return [
        {"open": 59200, "high": 59600, "low": 59100, "close": 59500, "volume": 100},
        {"open": 59500, "high": 59800, "low": 59400, "close": 59750, "volume": 120},
        {"open": 59750, "high": 60100, "low": 59700, "close": 60000, "volume": 150},
        {"open": 60000, "high": 60300, "low": 59900, "close": 60200, "volume": 180},
        {"open": 60200, "high": 60500, "low": 60100, "close": 60400, "volume": 200},
    ]


class TestSmcInvariance:
    """Critical safety tests verifying SMC parameters are immutable."""

    def test_smc_setup_completely_unchanged_after_enrichment(self, sample_decision, sample_candles):
        enricher = AiSignalEnricher()

        # Capture exact initial state
        orig_entry = sample_decision.entry
        orig_sl = sample_decision.stop_loss
        orig_tp = sample_decision.take_profit
        orig_rr = sample_decision.risk_reward
        orig_dir = sample_decision.direction
        orig_id = sample_decision.setup_id
        orig_state = sample_decision.setup_state

        result = enricher.enrich_setup(sample_decision, sample_candles)

        # Assert result is produced
        assert isinstance(result, AiEnrichmentResult)
        assert result.setup_id == orig_id
        assert result.symbol == sample_decision.symbol
        assert result.direction == orig_dir.value

        # Assert sample_decision was not mutated in any way
        assert sample_decision.entry == orig_entry
        assert sample_decision.stop_loss == orig_sl
        assert sample_decision.take_profit == orig_tp
        assert sample_decision.risk_reward == orig_rr
        assert sample_decision.direction == orig_dir
        assert sample_decision.setup_id == orig_id
        assert sample_decision.setup_state == orig_state


class TestAiScoringBounds:
    """Tests score bounds and validation."""

    def test_scores_are_within_valid_bounds(self, sample_decision, sample_candles):
        enricher = AiSignalEnricher()
        result = enricher.enrich_setup(sample_decision, sample_candles)

        assert Decimal("0.00") <= result.pattern_score <= Decimal("100.00")
        assert Decimal("0.00") <= result.signal_score <= Decimal("100.00")
        assert Decimal("0.00") <= result.confidence <= Decimal("100.00")
        assert isinstance(result.market_regime, MarketRegime)
        assert isinstance(result.market_context, MarketContext)

    def test_invalid_scores_raise_value_error(self):
        with pytest.raises(ValueError, match="pattern_score must be between 0.00 and 100.00"):
            PatternMetrics(
                pattern_score=Decimal("-5.00"),
                signal_score=Decimal("50.00"),
                confidence=Decimal("80.00"),
                regime=MarketRegime.BULLISH_TRENDING,
                context=MarketContext.FAVORABLE_TREND_CONTINUATION,
            )

        with pytest.raises(ValueError, match="confidence must be between 0.00 and 100.00"):
            PatternMetrics(
                pattern_score=Decimal("50.00"),
                signal_score=Decimal("50.00"),
                confidence=Decimal("120.00"),
                regime=MarketRegime.BULLISH_TRENDING,
                context=MarketContext.FAVORABLE_TREND_CONTINUATION,
            )


class TestDeterministicBehavior:
    """Tests repeatability of baseline AI intelligence."""

    def test_identical_inputs_produce_identical_enrichments(self, sample_decision, sample_candles):
        enricher = AiSignalEnricher()

        res1 = enricher.enrich_setup(sample_decision, sample_candles)
        res2 = enricher.enrich_setup(sample_decision, sample_candles)

        assert res1.pattern_score == res2.pattern_score
        assert res1.signal_score == res2.signal_score
        assert res1.confidence == res2.confidence
        assert res1.market_regime == res2.market_regime
        assert res1.market_context == res2.market_context
        assert res1.feature_summary == res2.feature_summary


class TestAiArchitectureSafety:
    """Architecture tests ensuring no execution bypass or frozen SMC modifications."""

    def test_ai_module_has_no_exchange_or_execution_imports(self):
        import quantedge.ai.engine as ai_engine
        import quantedge.ai.enricher as ai_enricher
        import quantedge.ai.features as ai_features
        import quantedge.ai.models as ai_models

        modules = [ai_engine, ai_enricher, ai_features, ai_models]
        forbidden = ["delta", "rest_client", "websocket", "OrderExecutionService", "execute_order"]

        for mod in modules:
            source = inspect.getsource(mod)
            for term in forbidden:
                assert term.lower() not in source.lower(), (
                    f"Forbidden dependency '{term}' found in {mod.__name__}"
                )
