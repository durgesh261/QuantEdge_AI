"""
QuantEdge AI research evaluation package — RESEARCH ONLY.

Nothing in this package participates in production trading.  It contains no
order placement, no order cancellation, no sizing authority, no leverage
authority and no execution gate.  It is imported by offline research drivers
(`engine/scripts/`) and by tests; it is never imported by
`quantedge.execution`, `quantedge.strategy.manual_smc` or `quantedge.runtime`.

Restored verbatim from commit bd9b7d5^ for the Phase L chronological
out-of-sample reproduction.  This ``__init__`` is deliberately import-free:
the original eagerly imported `four_instrument_audit` and `predictive_gate`,
which were not part of the Phase L reproduction closure and are therefore not
restored.  Import submodules explicitly.

Modules:
    smc_baseline       — performance metrics (expectancy, PF, drawdown).
    phase_i_ob_replay  — authoritative OB setup extraction + SMC context.
    phase_j_ob_dataset — the 29-feature `phase-j-ob-causal-v1` contract.
    phase_l_research   — the pre-registered chronological OOS pipeline.
"""

__all__: list[str] = []
