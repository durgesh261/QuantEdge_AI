"""
Research module for QuantEdge AI.
"""

from quantedge.ai.research.fixed_target_smc_engine import (
    FixedSMCConfig,
    FixedSMCTrade,
    run_fixed_target_smc_backtest,
    to_ist_string,
)

__all__ = [
    "FixedSMCConfig",
    "FixedSMCTrade",
    "run_fixed_target_smc_backtest",
    "to_ist_string",
]
