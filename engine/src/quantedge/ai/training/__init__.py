"""
QuantEdge AI Training Package.

Modules:
    dataset_builder   — Generates synthetic training datasets per FeatureContract.
    leakage_detector  — Data hygiene: temporal split & leakage validation.
    train             — Full pipeline: build → validate → train → export ONNX.
"""

from quantedge.ai.training.dataset_builder import build_training_dataset, describe_dataset
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    check_feature_leakage,
    check_temporal_stationarity,
    run_all_checks,
    validate_temporal_split,
)

__all__ = [
    "build_training_dataset",
    "describe_dataset",
    "DataHygieneReport",
    "validate_temporal_split",
    "check_feature_leakage",
    "check_temporal_stationarity",
    "run_all_checks",
]
