"""
QuantEdge AI Training Package.

Modules:
    real_dataset_builder — Real historical market dataset generator & forward outcome replay.
    dataset_builder      — Synthetic prototype generator (testing/mocking only).
    leakage_detector     — Data hygiene, 3-way purged split & leakage validator.
    train                — Full real-market training, validation & ONNX export pipeline.
"""

from quantedge.ai.training.dataset_builder import build_training_dataset, describe_dataset
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    check_feature_leakage,
    check_temporal_stationarity,
    run_all_checks,
    run_all_purged_checks,
    split_purged_chronological,
    validate_purged_chronological_split,
    validate_temporal_split,
)
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)

__all__ = [
    "build_real_training_dataset",
    "build_training_dataset",
    "describe_dataset",
    "DataHygieneReport",
    "validate_purged_chronological_split",
    "split_purged_chronological",
    "validate_temporal_split",
    "check_feature_leakage",
    "check_temporal_stationarity",
    "run_all_checks",
    "run_all_purged_checks",
    "REAL_TARGET_NAMES",
    "TARGET_REALIZED_R",
    "TARGET_MFE_R",
    "TARGET_MAE_R",
    "DEFAULT_CANONICAL_PATH",
]
