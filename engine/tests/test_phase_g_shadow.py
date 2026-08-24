"""
QuantEdge AI — Phase G Shadow Inference & Parity Test Suite
Validates:
1. Golden vectors JSON format and validity.
2. 24-feature ONNX inference matching golden vectors within 1e-4 tolerance.
3. Shadow replay engine execution and metric integrity.
4. Model governance manifest and model card compliance.
5. Invariant: AI shadow execution authorization is strictly False.
"""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest
import onnxruntime as ort

from quantedge.ai.feature_contract import FEATURE_NAMES
from quantedge.ai.evaluation.run_shadow_replay import run_historical_shadow_replay


@pytest.fixture
def repo_root() -> Path:
    curr = Path(__file__).resolve()
    while curr != curr.parent:
        if (curr / "backend").exists() and (curr / "data").exists():
            return curr
        curr = curr.parent
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def golden_vectors(repo_root: Path) -> dict:
    path = repo_root / "backend" / "src" / "test" / "resources" / "fixtures" / "phase_g_golden_vectors.json"
    assert path.exists(), f"Golden vectors file missing at {path}"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def onnx_session(repo_root: Path) -> ort.InferenceSession:
    model_path = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
    assert model_path.exists(), f"ONNX model missing at {model_path}"
    return ort.InferenceSession(str(model_path))


class TestPhaseGGoldenVectors:
    def test_golden_vectors_schema(self, golden_vectors: dict):
        assert golden_vectors["schema_version"] in ["1.0", "2.0.0"]
        assert golden_vectors["phase"] == "Phase G"
        assert golden_vectors["total_cases"] == len(golden_vectors["cases"])
        assert len(golden_vectors["cases"]) >= 20

    def test_all_golden_cases_reproduce_onnx(self, golden_vectors: dict, onnx_session: ort.InferenceSession):
        input_name = onnx_session.get_inputs()[0].name
        output_name = onnx_session.get_outputs()[0].name

        for case in golden_vectors["cases"]:
            feats = case["features_24"]
            assert len(feats) == 24
            inp = np.array([feats], dtype=np.float32)
            out = onnx_session.run([output_name], {input_name: inp})[0][0]

            expected = case["expected_onnx_output"]
            assert np.isclose(out[0], expected["predicted_realized_r"], atol=1e-4), f"Case {case['case_id']} realized_r mismatch"
            assert np.isclose(out[1], expected["predicted_mfe_r"], atol=1e-4), f"Case {case['case_id']} mfe_r mismatch"
            assert np.isclose(out[2], expected["predicted_mae_r"], atol=1e-4), f"Case {case['case_id']} mae_r mismatch"


class TestPhaseGShadowReplay:
    def test_shadow_replay_produces_valid_report(self, repo_root: Path, tmp_path: Path):
        report_file = tmp_path / "test_shadow_report.md"
        result = run_historical_shadow_replay(repo_root=repo_root, output_report_path=report_file)

        assert report_file.exists()
        assert result["total_setups"] > 0
        assert len(result["symbols"]) == 4
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            assert sym in result["symbols"]
            assert result["symbols"][sym]["total_setups"] > 0

        # Check calibration table
        calib = result["calibration_table"]
        assert len(calib) == 5
        total_b_count = sum(b["count"] for b in calib)
        assert total_b_count == result["total_setups"]

    def test_governance_invariants(self, repo_root: Path):
        manifest_path = repo_root / "docs" / "ai" / "ai_governance_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["promotion_status"] == "REJECTED"
        assert manifest["live_execution_authorized"] is False
        assert manifest["execution_boundary_policy"]["unauthorized_action"] == "HARD_BLOCK"
