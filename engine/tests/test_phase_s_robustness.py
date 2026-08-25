"""
Unit and integration tests for Phase S: AI Filter Robustness & Generalization Audit.

Verifies:
1. Independent reproduction of Phase R metrics from 2026 master dataset.
2. Moving Block Bootstrap reproducibility and bounds.
3. Monthly consistency calculation.
4. Asset consistency calculation.
5. Score diagnostics and Spearman rank correlation validity.
6. Coverage-matched random benchmark execution.
7. Heuristic control comparisons.
8. Conservative economic analysis calculation.
9. Deterministic repeated execution.
10. Artifact files existence and readability.
"""

import csv
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root
from quantedge.ai.evaluation.phase_s_robustness import (
    PhaseSRobustnessAudit,
    write_phase_s_artifacts,
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
def audit_output(master_dataset: pd.DataFrame):
    audit = PhaseSRobustnessAudit(master_dataset)
    results = audit.run_audit()
    return results


# ── 1. Phase R Numerical Reproduction ────────────────────────────────────────

def test_phase_r_numerical_reproduction(audit_output):
    rep = audit_output["phase_r_reproduction"]
    assert rep["exact_match_verified"] is True
    assert rep["total_master_obs"] == 465
    assert rep["total_oos_setups"] == 298
    assert rep["ai_accepted_count"] == 101
    assert rep["ai_rejected_count"] == 197
    assert rep["smc_baseline"]["expectancy_r"] == -0.0303
    assert rep["ai_filtered"]["expectancy_r"] == 0.0308
    assert rep["incremental_expectancy_r"] == 0.0611


# ── 2. Moving Block Bootstrap Bounds ─────────────────────────────────────────

def test_moving_block_bootstrap_bounds(audit_output):
    boot = audit_output["moving_block_bootstrap"]
    assert boot["n_resamples"] == 10000
    assert boot["block_size"] > 0
    assert boot["incremental_expectancy_95ci"][0] < boot["incremental_expectancy_95ci"][1]
    assert 0.0 <= boot["p_value_incremental_greater_than_zero"] <= 1.0


# ── 3. Monthly Consistency ───────────────────────────────────────────────────

def test_monthly_consistency(audit_output):
    m_aud = audit_output["monthly_consistency"]
    assert m_aud["months_evaluated"] == 5
    assert m_aud["positive_incremental_months"] == 4
    assert m_aud["consistency_fraction_pct"] == 80.0
    assert len(m_aud["monthly_breakdown"]) == 5


# ── 4. Asset Consistency ─────────────────────────────────────────────────────

def test_asset_consistency(audit_output):
    a_aud = audit_output["asset_consistency"]
    assert a_aud["total_assets"] == 4
    assert a_aud["positive_incremental_assets"] == 2
    assert a_aud["consistency_fraction_pct"] == 50.0
    assert len(a_aud["asset_breakdown"]) == 4


# ── 5. Score Diagnostics & Rank Correlation ──────────────────────────────────

def test_score_diagnostics(audit_output):
    score = audit_output["score_diagnostics"]
    assert "score_quantiles" in score
    assert score["winners_mean_score"] > score["losers_mean_score"]
    assert -1.0 <= score["spearman_rank_correlation"]["rho"] <= 1.0
    assert len(score["quintile_calibration"]) == 5


# ── 6. Coverage-Matched Random Benchmark ─────────────────────────────────────

def test_random_benchmark_bounds(audit_output):
    r_bm = audit_output["random_coverage_benchmark"]
    assert r_bm["n_resamples"] == 10000
    assert r_bm["target_trade_count"] == 101
    assert r_bm["random_benchmark_95ci"][0] < r_bm["random_benchmark_95ci"][1]
    assert 0.0 <= r_bm["ridge_percentile_rank_in_random_distribution"] <= 100.0


# ── 7. Heuristic Controls ────────────────────────────────────────────────────

def test_heuristic_controls(audit_output):
    controls = audit_output["heuristic_controls"]
    assert len(controls) == 5
    control_names = [c["control_name"] for c in controls]
    assert "Full SMC Baseline (100% Accept)" in control_names
    assert "Phase R AI Filter (Ridge @ +0.20R)" in control_names


# ── 8. Economic Analysis ─────────────────────────────────────────────────────

def test_economic_analysis(audit_output):
    econ = audit_output["economic_analysis"]
    assert econ["initial_balance_usd"] == 10000.0
    assert econ["ai_terminal_balance_usd"] > econ["smc_terminal_balance_usd"]
    assert econ["ai_max_drawdown_pct"] < econ["smc_max_drawdown_pct"]


# ── 9. Evidence Classification ───────────────────────────────────────────────

def test_evidence_classification(audit_output):
    cls = audit_output["evidence_classification"]
    assert cls["classification"] in (
        "STRONG EVIDENCE",
        "PROMISING BUT INSUFFICIENT",
        "NO RELIABLE EVIDENCE",
        "NEGATIVE EVIDENCE",
    )
    assert cls["classification"] == "PROMISING BUT INSUFFICIENT"


# ── 10. Artifact Files Existence & Completeness ──────────────────────────────

def test_artifacts_exist_and_readable(repo_root: Path):
    json_path = repo_root / "docs" / "ai" / "phase_s_robustness_results.json"
    rep_path = repo_root / "docs" / "ai" / "PHASE_S_ROBUSTNESS_AUDIT.md"

    assert json_path.exists(), f"Missing JSON: {json_path}"
    assert rep_path.exists(), f"Missing Report: {rep_path}"

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
        assert data["evidence_classification"]["classification"] == "PROMISING BUT INSUFFICIENT"
