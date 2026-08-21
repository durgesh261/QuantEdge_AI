"""
QuantEdge Strategy Layer Package.

Implements Phase 4.0 deterministic strategy evaluation, state scoring, and models.
"""

from quantedge.strategy.models import (
    StrategyDecision,
    StrategyDirection,
    SetupType,
    SetupState,
    RiskRewardConfig,
    generate_setup_id,
    TradeSetup,
    TradeDirection,
    StrategySignal,
    ConfidenceFactors,
    StrategyConfig,
    AccountState,
    RiskValidationResult,
)
from quantedge.strategy.engine import (
    StrategyEngine,
    StrategyEngineConfig,
)
from quantedge.strategy.confidence import ConfidenceScorer
from quantedge.strategy.risk import RiskCalculator

__all__ = [
    "StrategyDecision",
    "StrategyDirection",
    "SetupType",
    "StrategyEngine",
    "StrategyEngineConfig",
    "TradeSetup",
    "TradeDirection",
    "StrategySignal",
    "ConfidenceFactors",
    "StrategyConfig",
    "AccountState",
    "RiskValidationResult",
    "ConfidenceScorer",
    "RiskCalculator",
]
