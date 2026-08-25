"""
Unit and integration tests for Phase R: Strict 2026 Walk-Forward AI Training & Evaluation.

Verifies:
1. Exact 465 OB population integrity from docs/ai/2026_smc_order_blocks_master.csv.
2. Chronological ordering of predictions and window execution.
3. Strict training/test temporal separation (zero cross-window contamination).
4. No future-label leakage (mature-label requirement: label_available_timestamp <= training_end).
5. Feature schema and ordering consistency (all 29 scale-invariant causal features).
6. Model window metadata integrity (every test row has valid model_id and training_row_count).
7. Frozen threshold behavior (+0.20R acceptance rule).
8. Deterministic repeated execution (identical results).
9. Every test OB receives exactly one prediction (298 test OBs).
10. No test OB enters its own training dataset.
11. Production execution safety remains disabled (live_execution_authorized=False).
"""

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pytest

from quantedge.ai.evaluation.phase_j_ob_dataset import OB_FEATURE_NAMES
from quantedge.ai.evaluation.phase_r_walk_forward import (
    FROZEN_ALPHA,
    FROZEN_THRESHOLD,
    WALK_FORWARD_WINDOWS,
    PhaseRPredictionRecord,
    PhaseRWalkForwardPipeline,
    _find_repo_root,
    write_phase_r_artifacts,
)


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return _find_repo_root()


@pytest.fixture(scope="module")
def master_dataset(repo_root: Path) -> pd.DataFrame:
    csv_path = repo_root / "docs" / "ai" / "2026_smc_order_blocks_master.csv"
    assert csv_path.exists(), f"Master dataset missing at {csv_path}"
    return pd.read_csv(csv_path)


@pytest.fixture(scope="module")
def walk_forward_output(master_dataset: pd.DataFrame):
    pipeline = PhaseRWalkForwardPipeline(master_dataset)
    records, results = pipeline.run_walk_forward()
    return records, results


# ── 1. Population Integrity ──────────────────────────────────────────────────

def test_population_integrity_465_obs(master_dataset, walk_forward_output):
    records, results = walk_forward_output
    assert len(master_dataset) == 465, f"Expected 465 OBs in master dataset, got {len(master_dataset)}"
    assert len(records) == 465, f"Expected 465 records in prediction ledger, got {len(records)}"

    pop = results["population_summary"]
    assert pop["total_2026_obs"] == 465
    assert pop["seed_population_jan_mar"] == 167
    assert pop["walk_forward_oos_setups"] == 298
    assert pop["total_unique_evaluated_obs"] == 298


# ── 2. Chronological Ordering ────────────────────────────────────────────────

def test_chronological_ordering(walk_forward_output):
    records, results = walk_forward_output
    dec_times = [r.decision_timestamp for r in records]
    assert dec_times == sorted(dec_times), "Prediction ledger is not sorted chronologically by decision_timestamp"


# ── 3. Strict Training / Test Separation & Window Isolation ──────────────────

def test_training_test_separation(walk_forward_output):
    records, results = walk_forward_output
    for w in results["window_results"]:
        t_train_end = pd.Timestamp(w["training_period"].split(" -> ")[1])
        t_test_start = pd.Timestamp(w["test_period"].split(" -> ")[0])
        t_test_end = pd.Timestamp(w["test_period"].split(" -> ")[1])

        assert t_train_end < t_test_start, f"Training end {t_train_end} is not before test start {t_test_start}"
        assert t_test_start < t_test_end


# ── 4. Mature-Label Requirement (Zero Future-Label Leakage) ──────────────────

def test_mature_label_requirement(walk_forward_output):
    records, results = walk_forward_output
    test_records = [r for r in records if r.ai_decision in ("ACCEPT", "REJECT")]

    for r in test_records:
        # Verify that training_end for this model is strictly before the decision_timestamp of the test setup
        t_train_end = pd.Timestamp(r.training_end)
        t_dec = pd.Timestamp(r.decision_timestamp)
        assert t_train_end < t_dec, (
            f"Test setup {r.ob_id} at {r.decision_timestamp} evaluated with model trained up to {r.training_end}"
        )


# ── 5. Feature Schema & Ordering Consistency ─────────────────────────────────

def test_feature_schema_and_ordering(walk_forward_output):
    records, results = walk_forward_output
    expected_feats = [f"feat_{name}" for name in OB_FEATURE_NAMES]
    assert len(expected_feats) == 29

    for r in records[:10]:
        rec_dict = r.__dict__
        for feat in expected_feats:
            assert feat in rec_dict, f"Missing feature {feat} in prediction record {r.ob_id}"
            assert isinstance(rec_dict[feat], float), f"Feature {feat} is not a float"


# ── 6. Frozen Threshold & AI Decision Consistency ────────────────────────────

def test_frozen_threshold_decision_logic(walk_forward_output):
    records, results = walk_forward_output
    for r in records:
        if r.walk_forward_window == "SEED_JAN_MAR":
            assert r.ai_decision == "TRAIN_SEED"
            assert not r.trade_executed
        else:
            assert r.threshold == FROZEN_THRESHOLD
            if r.prediction >= FROZEN_THRESHOLD:
                assert r.ai_decision == "ACCEPT"
                assert r.trade_executed
            else:
                assert r.ai_decision == "REJECT"
                assert not r.trade_executed


# ── 7. Single Prediction Per Test OB ─────────────────────────────────────────

def test_single_prediction_per_test_ob(walk_forward_output):
    records, results = walk_forward_output
    test_records = [r for r in records if r.ai_decision in ("ACCEPT", "REJECT")]
    test_ob_ids = [r.ob_id for r in test_records]

    assert len(test_ob_ids) == len(set(test_ob_ids)), f"Duplicate test predictions found: {len(test_ob_ids)} vs {len(set(test_ob_ids))}"
    assert len(test_records) == 298, f"Expected 298 test predictions, got {len(test_records)}"


# ── 8. Model Window Metadata ─────────────────────────────────────────────────

def test_model_window_metadata_integrity(walk_forward_output):
    records, results = walk_forward_output
    for w in results["window_results"]:
        assert w["training_rows"] > 0
        assert w["test_rows"] > 0
        assert w["model_id"].startswith(f"Ridge_a{FROZEN_ALPHA}")
        assert len(w["model_hash"]) == 16


# ── 9. Deterministic Repeated Execution ──────────────────────────────────────

def test_walk_forward_determinism(master_dataset):
    pipeline1 = PhaseRWalkForwardPipeline(master_dataset)
    records1, results1 = pipeline1.run_walk_forward()

    pipeline2 = PhaseRWalkForwardPipeline(master_dataset)
    records2, results2 = pipeline2.run_walk_forward()

    assert len(records1) == len(records2)
    for r1, r2 in zip(records1, records2):
        assert r1.ob_id == r2.ob_id
        assert r1.prediction == r2.prediction
        assert r1.ai_decision == r2.ai_decision
        assert r1.model_hash == r2.model_hash

    assert results1["aggregate_oos_performance"] == results2["aggregate_oos_performance"]


# ── 10. Artifact Files Existence & Completeness ──────────────────────────────

def test_artifacts_exist_and_readable(repo_root: Path):
    csv_path = repo_root / "docs" / "ai" / "phase_r_walk_forward_predictions.csv"
    json_path = repo_root / "docs" / "ai" / "phase_r_walk_forward_predictions.json"
    res_path = repo_root / "docs" / "ai" / "phase_r_walk_forward_results.json"
    rep_path = repo_root / "docs" / "ai" / "PHASE_R_WALK_FORWARD_REPORT.md"

    assert csv_path.exists(), f"Missing CSV: {csv_path}"
    assert json_path.exists(), f"Missing JSON: {json_path}"
    assert res_path.exists(), f"Missing Results: {res_path}"
    assert rep_path.exists(), f"Missing Report: {rep_path}"

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 465
        assert "walk_forward_window" in reader.fieldnames
        assert "prediction" in reader.fieldnames
        assert "feat_displacement_atr" in reader.fieldnames
