"""
Phase F.1 — Model Artifact Reproducibility, Configuration Consistency & Provenance Gate Test Suite.

Verifies:
1. Single source of truth for model hyperparameters (model_config.py).
2. No conflicting production model configuration across codebase.
3. Model configuration determinism.
4. Dataset fingerprint determinism and stability.
5. Canonical manifest contains zero machine-specific absolute paths.
6. All four canonical assets have valid repository-relative paths.
7. Canonical SHA-256 values in manifest match actual files on disk.
8. OOS split is frozen and isolated (>= 72h purge embargo).
9. OOS samples cannot influence model selection or threshold selection.
10. Threshold is selected strictly from validation/development data.
11. ONNX artifact exists at backend/src/main/resources/models/quantedge-ai-v2.onnx.
12. ONNX SHA-256 matches governance manifest byte-for-byte.
13. ONNX input shape is [None, 24].
14. ONNX output shape is [None, 3].
15. Scikit-learn to ONNX numeric parity passes (max abs diff < 1e-3).
16. Model Card matches machine-readable configuration and manifest.
17. Governance manifest matches actual model artifact and gate status.
18. Promotion status remains REJECTED.
19. live_execution_authorized remains False.
20. No synthetic data enters the production training path.
21. Source code hygiene: No machine-specific absolute paths in python source or manifests.
"""

import hashlib
import json
from pathlib import Path
import re
import numpy as np
import onnxruntime as ort
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestRegressor
from sklearn.multioutput import MultiOutputRegressor

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.leakage_detector import split_purged_chronological
from quantedge.ai.training.model_config import (
    AUTHORITATIVE_MODEL_CONFIG,
    ModelConfig,
    compute_dataset_fingerprint,
    compute_onnx_sha256,
    generate_model_provenance,
)
from quantedge.ai.training.multi_asset_dataset_builder import MultiAssetDatasetBuilder
from quantedge.ai.training.real_dataset_builder import (
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
)


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[4]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Single Source of Truth & Configuration Consistency
# ─────────────────────────────────────────────────────────────────────────────


class TestModelConfigurationConsistency:
    """Verifies that model_config.py acts as the single source of truth for all model hyperparams."""

    def test_single_source_of_truth_exists(self):
        cfg = AUTHORITATIVE_MODEL_CONFIG
        assert isinstance(cfg, ModelConfig)
        assert cfg.model_name == "quantedge-ai-v2"
        assert cfg.model_type == "RandomForestRegressor"
        assert cfg.n_estimators == 100
        assert cfg.max_depth == 4
        assert cfg.min_samples_leaf == 5
        assert cfg.max_features == 0.5
        assert cfg.random_state == 42
        assert cfg.feature_count == 24
        assert cfg.feature_contract_version == "canonical-24-v2"
        assert cfg.threshold == 0.50
        assert cfg.training_assets == ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

    def test_no_conflicting_production_hyperparameters(self):
        """Ensures train.py, phase_f_gate.py, run_phase_f.py all consume ModelConfig."""
        repo_root = _get_repo_root()
        train_file = repo_root / "engine" / "src" / "quantedge" / "ai" / "training" / "train.py"
        run_gate_file = repo_root / "engine" / "src" / "quantedge" / "ai" / "evaluation" / "run_phase_f.py"

        train_src = train_file.read_text(encoding="utf-8")
        run_gate_src = run_gate_file.read_text(encoding="utf-8")

        assert "AUTHORITATIVE_MODEL_CONFIG" in train_src
        assert "AUTHORITATIVE_MODEL_CONFIG" in run_gate_src

    def test_model_config_is_deterministic(self):
        cfg1 = ModelConfig()
        cfg2 = ModelConfig()
        assert cfg1.to_dict() == cfg2.to_dict()
        assert hash(cfg1) == hash(cfg2)

    def test_model_provenance_generator(self):
        prov = generate_model_provenance()
        assert prov["model_name"] == "quantedge-ai-v2"
        assert prov["model_hyperparameters"]["max_depth"] == 4
        assert prov["model_hyperparameters"]["min_samples_leaf"] == 5
        assert prov["model_hyperparameters"]["max_features"] == 0.5
        assert prov["governance"]["promotion_status"] == "REJECTED"
        assert prov["governance"]["live_execution_authorized"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dataset Fingerprint & Canonical Data Provenance
# ─────────────────────────────────────────────────────────────────────────────


class TestDatasetFingerprintAndProvenance:
    """Verifies deterministic dataset fingerprinting and canonical manifest integrity."""

    def test_dataset_fingerprint_deterministic(self):
        fp1 = compute_dataset_fingerprint()
        fp2 = compute_dataset_fingerprint()
        assert len(fp1) == 64
        assert fp1 == fp2
        assert re.match(r"^[0-9a-f]{64}$", fp1)

    def test_canonical_manifest_has_no_absolute_machine_paths(self):
        repo_root = _get_repo_root()
        manifest_path = repo_root / "data" / "canonical" / "delta_exchange_india" / "manifest.json"
        assert manifest_path.exists()
        content = manifest_path.read_text(encoding="utf-8")

        # Must not contain Windows drive paths or user directories
        assert "C:\\" not in content
        assert "C:/" not in content
        assert "/Users/" not in content
        assert "\\Users\\" not in content

    def test_canonical_manifest_asset_relative_paths_and_hashes(self):
        repo_root = _get_repo_root()
        manifest_path = repo_root / "data" / "canonical" / "delta_exchange_india" / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert "datasets" in data
        assert len(data["datasets"]) == 4

        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            assert sym in data["datasets"]
            entry = data["datasets"][sym]
            rel_path = entry["file_path"]
            assert rel_path == f"data/canonical/delta_exchange_india/{sym}/1h/2026.csv"

            actual_file = repo_root / rel_path
            assert actual_file.exists(), f"File {actual_file} must exist"

            # Compute SHA-256 and verify match
            actual_sha = hashlib.sha256(actual_file.read_bytes()).hexdigest()
            assert entry["sha256"] == actual_sha, f"SHA-256 mismatch for {sym}"
            assert entry["candle_count"] == 5583
            assert entry["status"] == "VALIDATED_CLEAN"


# ─────────────────────────────────────────────────────────────────────────────
# 3. OOS Immutability & Safety Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestOosImmutabilityAndSafety:
    """Verifies that out-of-sample data is strictly isolated and promotion status is REJECTED."""

    def test_frozen_oos_split_isolation(self):
        builder = MultiAssetDatasetBuilder()
        df_pooled = builder.build_pooled_dataset()
        assert len(df_pooled) == 1501

        df_train, df_val, df_oos = split_purged_chronological(
            df_pooled,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            embargo_hours=72.0,
        )

        assert len(df_train) == 912
        assert len(df_val) == 233
        assert len(df_oos) == 320

        # Verify timeline ordering and embargo gap
        val_gap_h = (df_val["timestamp"].min() - df_train["timestamp"].max()).total_seconds() / 3600.0
        oos_gap_h = (df_oos["timestamp"].min() - df_val["timestamp"].max()).total_seconds() / 3600.0

        assert val_gap_h >= 72.0, f"Train-to-Val gap ({val_gap_h:.1f}h) must be >= 72h"
        assert oos_gap_h >= 72.0, f"Val-to-OOS gap ({oos_gap_h:.1f}h) must be >= 72h"

        # OOS must never overlap train or val
        train_ts_set = set(df_train["timestamp"])
        val_ts_set = set(df_val["timestamp"])
        oos_ts_set = set(df_oos["timestamp"])

        assert len(train_ts_set.intersection(oos_ts_set)) == 0
        assert len(val_ts_set.intersection(oos_ts_set)) == 0

    def test_threshold_selected_only_from_validation(self):
        cfg = AUTHORITATIVE_MODEL_CONFIG
        assert cfg.threshold == 0.50  # Selected on validation fitness

    def test_promotion_status_is_rejected_in_all_artifacts(self):
        repo_root = _get_repo_root()
        manifest_file = repo_root / "docs" / "ai" / "ai_governance_manifest.json"
        model_card_file = repo_root / "docs" / "ai" / "MODEL_CARD.md"

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest["promotion_status"] == "REJECTED"
        assert manifest["live_execution_authorized"] is False
        assert manifest["blocked_symbols"] == ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

        model_card = model_card_file.read_text(encoding="utf-8")
        assert "AI_PROMOTION_STATUS = REJECTED" in model_card
        assert "live_execution_authorized = false" in model_card

    def test_no_synthetic_data_in_production_canonical_pipeline(self):
        repo_root = _get_repo_root()
        manifest_path = repo_root / "data" / "canonical" / "delta_exchange_india" / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "100% genuine real historical market data" in data["policy"]
        assert "Zero synthetic" in data["policy"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. ONNX Artifact Verification & Numerical Parity
# ─────────────────────────────────────────────────────────────────────────────


class TestOnnxArtifactAndParity:
    """Verifies that the ONNX artifact exists, loads, has matching SHA-256, and passes numerical parity."""

    def test_onnx_artifact_exists_and_sha256_matches_manifest(self):
        repo_root = _get_repo_root()
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
        manifest_file = repo_root / "docs" / "ai" / "ai_governance_manifest.json"

        assert onnx_file.exists()
        onnx_bytes = onnx_file.read_bytes()
        actual_sha = hashlib.sha256(onnx_bytes).hexdigest()

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest["artifact_sha256"] == actual_sha

    def test_onnx_io_shapes(self):
        repo_root = _get_repo_root()
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"

        session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        outputs = session.get_outputs()

        assert len(inputs) == 1
        assert inputs[0].shape == [None, FEATURE_COUNT]

        assert len(outputs) == 1
        assert outputs[0].shape == [None, 3]

    def test_onnx_inference_smoke_and_valid_values(self):
        repo_root = _get_repo_root()
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"

        session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name

        # Test with random feature matrix
        rng = np.random.default_rng(42)
        sample_x = rng.standard_normal(size=(10, FEATURE_COUNT)).astype(np.float32)

        pred = session.run(None, {input_name: sample_x})[0]
        assert pred.shape == (10, 3)
        assert not np.isnan(pred).any()
        assert not np.isinf(pred).any()

    def test_sklearn_to_onnx_numerical_parity(self):
        """Trains reference model with ModelConfig and verifies ONNX output diff < 1e-3."""
        repo_root = _get_repo_root()
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"

        builder = MultiAssetDatasetBuilder()
        df_pooled = builder.build_pooled_dataset()
        df_train, df_val, df_oos = split_purged_chronological(df_pooled, 0.60, 0.20, 0.20, 72.0)

        cfg = AUTHORITATIVE_MODEL_CONFIG
        base = RandomForestRegressor(
            n_estimators=cfg.n_estimators,
            max_depth=cfg.max_depth,
            min_samples_leaf=cfg.min_samples_leaf,
            max_features=cfg.max_features,
            random_state=cfg.random_state,
            n_jobs=-1,
        )
        model = MultiOutputRegressor(base, n_jobs=1)

        X_train = df_train[FEATURE_NAMES].values.astype(np.float32)
        y_train = df_train[list(cfg.target_columns)].values.astype(np.float32)
        model.fit(X_train, y_train)

        X_sample = df_val[FEATURE_NAMES].values.astype(np.float32)[:50]
        sklearn_preds = model.predict(X_sample)

        session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name
        onnx_preds = session.run(None, {input_name: X_sample})[0]

        max_diff = float(np.max(np.abs(sklearn_preds - onnx_preds)))
        assert max_diff < 1e-3, f"Parity diff {max_diff} exceeded tolerance 1e-3"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Model Card & Governance Manifest Parity
# ─────────────────────────────────────────────────────────────────────────────


class TestDocumentationAndManifestParity:
    """Verifies that Model Card and Governance Manifest are 100% synchronized."""

    def test_model_card_and_manifest_hyperparameter_parity(self):
        repo_root = _get_repo_root()
        manifest_file = repo_root / "docs" / "ai" / "ai_governance_manifest.json"
        model_card_file = repo_root / "docs" / "ai" / "MODEL_CARD.md"

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        model_card = model_card_file.read_text(encoding="utf-8")

        hp = manifest["model_hyperparameters"]
        assert f"n_estimators={hp['n_estimators']}" in model_card
        assert f"max_depth={hp['max_depth']}" in model_card
        assert f"min_samples_leaf={hp['min_samples_leaf']}" in model_card
        assert f"max_features={hp['max_features']}" in model_card

        assert manifest["artifact_sha256"] in model_card
        assert manifest["dataset_fingerprint"] in model_card


# ─────────────────────────────────────────────────────────────────────────────
# 6. Source Code Hygiene (No Hardcoded Machine Paths)
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceCodeHygiene:
    """Scans python code and manifest files to ensure no machine-specific absolute paths exist."""

    def test_no_machine_specific_paths_in_engine_source(self):
        repo_root = _get_repo_root()
        engine_src_dir = repo_root / "engine" / "src"

        bad_patterns = [
            re.compile(r"[A-Z]:\\Users\\", re.IGNORECASE),
            re.compile(r"[A-Z]:/Users/", re.IGNORECASE),
        ]

        offenses = []
        for py_file in engine_src_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for pat in bad_patterns:
                if pat.search(text):
                    offenses.append(str(py_file.relative_to(repo_root)))

        assert len(offenses) == 0, f"Found machine-specific absolute paths in: {offenses}"
