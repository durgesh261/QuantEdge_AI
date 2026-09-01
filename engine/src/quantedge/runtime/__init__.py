"""Production wiring for QuantEdge strategies.

This package is the *only* place where a strategy package and the frozen
execution engine are allowed to meet. Strategy packages (for example
`quantedge.strategy.manual_smc`) must stay importable without
`quantedge.execution`; the execution engine must stay ignorant of any
particular strategy. Everything that joins the two lives here.

Nothing in this package detects order blocks, sizes a position, talks to an
exchange, or owns a portfolio lock. It routes: closed candle in, existing
orchestration boundary out.
"""
from __future__ import annotations

__all__ = ["manual_smc_runtime"]
