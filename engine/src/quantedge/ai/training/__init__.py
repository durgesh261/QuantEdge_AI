"""
QuantEdge AI research training package — RESEARCH ONLY.

Nothing in this package participates in production trading.  Restored verbatim
from commit bd9b7d5^ for the Phase L chronological out-of-sample reproduction.

This ``__init__`` is deliberately import-free: the original eagerly imported
`dataset_builder`, `leakage_detector` and `train`, none of which are in the
Phase L reproduction closure and none of which are restored.  Import
submodules explicitly.

Modules:
    real_dataset_builder — forward outcome replay (`replay_forward_outcome`)
                           and the causal-24 production feature encoder.
"""

__all__: list[str] = []
