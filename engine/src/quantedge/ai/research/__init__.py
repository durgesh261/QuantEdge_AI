"""
Research module for QuantEdge AI.
"""

from quantedge.ai.research.displacement_gated_retest_engine import (
    ManualOBState,
    ManualOBRecord,
    ManualSpecConfig,
    ManualSpecBOSScanner,
    run_manual_spec_backtest,
)

__all__ = [
    "ManualOBState",
    "ManualOBRecord",
    "ManualSpecConfig",
    "ManualSpecBOSScanner",
    "run_manual_spec_backtest",
]
