"""
QuantEdge AI — Single Source of Truth for Model Configuration & Dataset Fingerprinting.

Defines the authoritative production model hyperparameters, feature contracts,
target specifications, split boundaries, and cryptographic dataset fingerprinting logic.
Every training, evaluation, export, manifest generation, and documentation module
must consume this configuration.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.real_dataset_builder import (
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
)


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


@dataclass(frozen=True)
class ModelConfig:
    """Authoritative production model configuration and hyperparameters."""
    model_name: str = "quantedge-ai-v2"
    model_type: str = "RandomForestRegressor"
    n_estimators: int = 100
    max_depth: int = 4
    min_samples_leaf: int = 5
    max_features: float = 0.5
    random_state: int = 42
    n_jobs: int = -1
    feature_count: int = FEATURE_COUNT
    feature_contract_version: str = "canonical-24-v2"
    feature_names: Tuple[str, ...] = tuple(FEATURE_NAMES)
    target_columns: Tuple[str, ...] = (TARGET_REALIZED_R, TARGET_MFE_R, TARGET_MAE_R)
    threshold: float = 0.50  # Frozen validation threshold (+0.50R)
    training_assets: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
    timeframe: str = "1h"
    replay_horizon_hours: int = 72
    clustering_window_hours: float = 3.0
    embargo_hours: float = 72.0
    train_ratio: float = 0.60
    val_ratio: float = 0.20
    test_ratio: float = 0.20

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["feature_names"] = list(self.feature_names)
        d["target_columns"] = list(self.target_columns)
        d["training_assets"] = list(self.training_assets)
        return d


# Global authoritative singleton instance
AUTHORITATIVE_MODEL_CONFIG = ModelConfig()
MODEL_CONFIG = AUTHORITATIVE_MODEL_CONFIG.to_dict()


def compute_dataset_fingerprint(
    canonical_base: Optional[Path] = None,
    config: Optional[ModelConfig] = None,
) -> str:
    """
    Computes a deterministic SHA-256 fingerprint representing the exact dataset definition.
    Combines:
    - Canonical data files' SHA-256 hashes
    - Feature contract version and ordered feature names
    - Target column definitions
    - Split ratios and purge/embargo windows
    - Setup extraction and clustering parameters
    """
    cfg = config or AUTHORITATIVE_MODEL_CONFIG
    base_dir = canonical_base or (_find_repo_root() / "data" / "canonical" / "delta_exchange_india")

    asset_hashes: Dict[str, str] = {}
    for sym in sorted(cfg.training_assets):
        csv_path = base_dir / sym / cfg.timeframe / "2026.csv"
        if csv_path.exists():
            h = hashlib.sha256()
            with open(csv_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            asset_hashes[sym] = h.hexdigest()
        else:
            asset_hashes[sym] = "MISSING"

    fingerprint_payload = {
        "canonical_asset_hashes": asset_hashes,
        "feature_contract_version": cfg.feature_contract_version,
        "feature_count": cfg.feature_count,
        "feature_names": list(cfg.feature_names),
        "target_columns": list(cfg.target_columns),
        "training_assets": list(cfg.training_assets),
        "timeframe": cfg.timeframe,
        "replay_horizon_hours": cfg.replay_horizon_hours,
        "clustering_window_hours": cfg.clustering_window_hours,
        "embargo_hours": cfg.embargo_hours,
        "split_ratios": {
            "train": cfg.train_ratio,
            "val": cfg.val_ratio,
            "test": cfg.test_ratio,
        },
    }

    serialized = json.dumps(fingerprint_payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_onnx_sha256(onnx_path: Optional[Path] = None) -> str:
    """Calculates the SHA-256 checksum of the committed ONNX artifact."""
    p = onnx_path or (_find_repo_root() / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx")
    if not p.exists():
        return "MISSING"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_model_provenance(
    canonical_base: Optional[Path] = None,
    onnx_path: Optional[Path] = None,
    config: Optional[ModelConfig] = None,
    training_samples: int = 912,
    val_samples: int = 233,
    oos_samples: int = 320,
    train_start: str = "2026-01-01T00:00:00+00:00",
    train_end: str = "2026-06-03T18:00:00+00:00",
    val_start: str = "2026-06-06T20:00:00+00:00",
    val_end: str = "2026-07-02T22:00:00+00:00",
    oos_start: str = "2026-07-06T00:00:00+00:00",
    oos_end: str = "2026-08-21T14:00:00+00:00",
    promotion_status: str = "REJECTED",
    live_execution_authorized: bool = False,
) -> Dict[str, Any]:
    """Generates a complete, deterministic, machine-readable model provenance record."""
    cfg = config or AUTHORITATIVE_MODEL_CONFIG
    base_dir = canonical_base or (_find_repo_root() / "data" / "canonical" / "delta_exchange_india")
    onnx_p = onnx_path or (_find_repo_root() / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx")

    canonical_hashes: Dict[str, str] = {}
    for sym in sorted(cfg.training_assets):
        csv_path = base_dir / sym / cfg.timeframe / "2026.csv"
        if csv_path.exists():
            h = hashlib.sha256()
            with open(csv_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            canonical_hashes[sym] = h.hexdigest()
        else:
            canonical_hashes[sym] = "MISSING"

    manifest_file = base_dir / "manifest.json"
    manifest_sha = "MISSING"
    if manifest_file.exists():
        manifest_sha = hashlib.sha256(manifest_file.read_bytes()).hexdigest()

    dataset_fingerprint = compute_dataset_fingerprint(base_dir, cfg)
    onnx_sha = compute_onnx_sha256(onnx_p)

    return {
        "provenance_schema_version": "1.0.0",
        "model_name": cfg.model_name,
        "model_type": cfg.model_type,
        "model_hyperparameters": {
            "n_estimators": cfg.n_estimators,
            "max_depth": cfg.max_depth,
            "min_samples_leaf": cfg.min_samples_leaf,
            "max_features": cfg.max_features,
            "random_state": cfg.random_state,
        },
        "feature_contract": {
            "version": cfg.feature_contract_version,
            "feature_count": cfg.feature_count,
            "feature_names": list(cfg.feature_names),
        },
        "targets": list(cfg.target_columns),
        "threshold": cfg.threshold,
        "training_assets": list(cfg.training_assets),
        "timeframe": cfg.timeframe,
        "dataset_fingerprint": dataset_fingerprint,
        "canonical_data": {
            "manifest_sha256": manifest_sha,
            "asset_hashes": canonical_hashes,
        },
        "split_boundaries": {
            "training": {
                "samples": training_samples,
                "start_utc": train_start,
                "end_utc": train_end,
            },
            "validation": {
                "samples": val_samples,
                "start_utc": val_start,
                "end_utc": val_end,
                "embargo_hours_prior": cfg.embargo_hours,
            },
            "out_of_sample": {
                "samples": oos_samples,
                "start_utc": oos_start,
                "end_utc": oos_end,
                "embargo_hours_prior": cfg.embargo_hours,
                "is_frozen": True,
            },
        },
        "artifacts": {
            "onnx_relative_path": "backend/src/main/resources/models/quantedge-ai-v2.onnx",
            "onnx_sha256": onnx_sha,
            "input_shape": [None, cfg.feature_count],
            "output_shape": [None, len(cfg.target_columns)],
        },
        "governance": {
            "promotion_status": promotion_status,
            "live_execution_authorized": live_execution_authorized,
            "authoritative_execution_engine": "Deterministic SMC Engine",
        },
    }
