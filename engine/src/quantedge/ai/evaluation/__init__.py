"""
QuantEdge AI Evaluation Package.

Modules:
    smc_baseline           — Standalone SMC performance metrics evaluator.
    predictive_gate        — Comprehensive AI predictive-value gate & promotion engine.
    four_instrument_audit  — Canonical dataset audit for BTC, ETH, SOL, XRP.
"""

from quantedge.ai.evaluation.four_instrument_audit import (
    InstrumentAuditRecord,
    audit_four_instruments,
    format_four_instrument_report,
)
from quantedge.ai.evaluation.predictive_gate import (
    AIPredictiveValueGate,
    GateResults,
    ThresholdEvaluation,
)
from quantedge.ai.evaluation.smc_baseline import (
    PerformanceMetrics,
    calculate_performance_metrics,
    format_performance_table,
)

__all__ = [
    "PerformanceMetrics",
    "calculate_performance_metrics",
    "format_performance_table",
    "InstrumentAuditRecord",
    "audit_four_instruments",
    "format_four_instrument_report",
    "AIPredictiveValueGate",
    "GateResults",
    "ThresholdEvaluation",
]
