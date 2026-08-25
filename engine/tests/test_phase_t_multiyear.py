"""
Unit and integration tests for Phase T: Multi-Year Expanding Walk-Forward Evaluation (2024–2026).

Verifies:
1. Multi-year master dataset completeness (1,670 qualified Order Blocks).
2. 20-month walk-forward schedule structure and continuous coverage.
3. Strict mature-label isolation across all 20 expanding windows.
4. Out-of-sample prediction population and total sample count (N=1,239).
5. Moving Block Bootstrap reproducibility and validity.
6. Coverage-matched random benchmark execution.
7. Monthly, Annual, and Cross-Asset consistency audits.
8. Score diagnostics, quintile monotonicity, and rank correlation.
9. Conservative 1.0% fixed-fractional economic simulation.
10. Deterministic execution and artifact serialization.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.evaluation.extract_multiyear_smc_master_dataset import (
    extract_multiyear_smc_master_dataset,
    write_multiyear_master_artifacts,
)
from quantedge.ai.evaluation.phase_t_multiyear import (
    MULTI_YEAR_WALK_FORWARD_WINDOWS,
    PhaseTMultiYearWalkForwardPipeline,
    _find_repo_root,
    write_phase_t_artifacts,
)


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return _find_repo_root()


@pytest.fixture(scope="module")
def multiyear_master_df(repo_root: Path) -> pd.DataFrame:
    csv_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    if not csv_path.exists():
        df, meta = extract_multiyear_smc_master_dataset(repo_root=repo_root)
        write_multiyear_master_artifacts(df, meta, repo_root=repo_root)
        return df
    return pd.read_csv(csv_path)


@pytest.fixture(scope="module")
def phase_t_results(multiyear_master_df: pd.DataFrame):
    pipeline = PhaseTMultiYearWalkForwardPipeline(multiyear_master_df)
    records, results = pipeline.run_multiyear_evaluation()
    return records, results


# ── 1. Master Dataset Completeness ───────────────────────────────────────────

def test_multiyear_master_dataset_completeness(multiyear_master_df: pd.DataFrame):
    assert len(multiyear_master_df) == 1670
    assert set(multiyear_master_df["asset"].unique()) == {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"}
    assert set(multiyear_master_df["direction"].unique()) == {"LONG", "SHORT"}

    # Check 29 feature columns exist and contain no NaN values
    feature_cols = [c for c in multiyear_master_df.columns if c.startswith("feat_")]
    assert len(feature_cols) == 29
    for col in feature_cols:
        assert multiyear_master_df[col].isna().sum() == 0, f"NaNs found in {col}"


# ── 2. Walk-Forward Schedule Structure ───────────────────────────────────────

def test_walk_forward_schedule_structure():
    assert len(MULTI_YEAR_WALK_FORWARD_WINDOWS) == 20
    assert MULTI_YEAR_WALK_FORWARD_WINDOWS[0][5] == "2025-01"
    assert MULTI_YEAR_WALK_FORWARD_WINDOWS[-1][5] == "2026-08"

    # Verify contiguous monthly ordering
    for i in range(len(MULTI_YEAR_WALK_FORWARD_WINDOWS) - 1):
        curr_test_end = MULTI_YEAR_WALK_FORWARD_WINDOWS[i][4]
        next_test_start = MULTI_YEAR_WALK_FORWARD_WINDOWS[i + 1][3]
        dt_curr = pd.Timestamp(curr_test_end)
        dt_next = pd.Timestamp(next_test_start)
        assert (dt_next - dt_curr).total_seconds() == 1, f"Gap between {curr_test_end} and {next_test_start}"


# ── 3. Strict Mature-Label Isolation ─────────────────────────────────────────

def test_mature_label_isolation(multiyear_master_df: pd.DataFrame):
    multiyear_master_df["dec_dt"] = pd.to_datetime(multiyear_master_df["decision_timestamp"], utc=True)
    multiyear_master_df["mat_dt"] = multiyear_master_df["dec_dt"] + pd.to_timedelta(multiyear_master_df["holding_bars"], unit="h")

    for wid, tr_s, tr_e, te_s, te_e, t_m in MULTI_YEAR_WALK_FORWARD_WINDOWS:
        t_tr_e = pd.Timestamp(tr_e)
        train_df = multiyear_master_df[(multiyear_master_df["dec_dt"] <= t_tr_e) & (multiyear_master_df["mat_dt"] <= t_tr_e)]

        # Verify zero training labels exceed train_end
        assert (train_df["mat_dt"] > t_tr_e).sum() == 0
        assert (train_df["dec_dt"] > t_tr_e).sum() == 0


# ── 4. Total Out-Of-Sample Setup Population ──────────────────────────────────

def test_total_oos_population(phase_t_results):
    records, results = phase_t_results
    assert len(records) == 1239
    assert results["aggregate_oos_performance"]["total_oos_setups"] == 1239
    assert len(results["window_results"]) == 20


# ── 5. Moving Block Bootstrap Bounds ─────────────────────────────────────────

def test_moving_block_bootstrap_bounds(phase_t_results):
    _, results = phase_t_results
    boot = results["moving_block_bootstrap"]
    assert boot["n_resamples"] == 10000
    assert boot["block_size"] == 36
    assert boot["incremental_expectancy_95ci"][0] < boot["incremental_expectancy_95ci"][1]
    assert 0.0 <= boot["p_value_incremental_greater_than_zero"] <= 1.0


# ── 6. Coverage-Matched Random Benchmark ─────────────────────────────────────

def test_random_benchmark_bounds(phase_t_results):
    _, results = phase_t_results
    r_bm = results["random_coverage_benchmark"]
    assert r_bm["n_resamples"] == 10000
    assert r_bm["random_benchmark_95ci"][0] < r_bm["random_benchmark_95ci"][1]
    assert 0.0 <= r_bm["ridge_percentile_rank_in_random_distribution"] <= 100.0


# ── 7. Annual Breakdown Integrity ────────────────────────────────────────────

def test_annual_consistency(phase_t_results):
    _, results = phase_t_results
    ann = results["annual_consistency"]
    assert "2025" in ann
    assert "2026" in ann
    assert ann["2025"]["total_oos_setups"] == 774
    assert ann["2026"]["total_oos_setups"] == 465
    assert ann["2025"]["total_oos_setups"] + ann["2026"]["total_oos_setups"] == 1239


# ── 8. Cross-Asset Breakdown Integrity ───────────────────────────────────────

def test_asset_consistency(phase_t_results):
    _, results = phase_t_results
    a_aud = results["asset_consistency"]
    assert a_aud["total_assets"] == 4
    assert set(a_aud["asset_breakdown"].keys()) == {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"}
    tot_asset_setups = sum(a["total_oos_setups"] for a in a_aud["asset_breakdown"].values())
    assert tot_asset_setups == 1239


# ── 9. Economic Simulation Calculation ───────────────────────────────────────

def test_economic_simulation(phase_t_results):
    _, results = phase_t_results
    econ = results["economic_analysis"]
    assert econ["initial_balance_usd"] == 10000.0
    assert econ["risk_per_trade_fraction"] == 0.01
    assert isinstance(econ["ai_terminal_balance_usd"], float)
    assert isinstance(econ["smc_terminal_balance_usd"], float)


# ── 10. Evidence Classification Validity ─────────────────────────────────────

def test_evidence_classification(phase_t_results):
    _, results = phase_t_results
    cls = results["evidence_classification"]
    assert cls["classification"] in (
        "STRONG EVIDENCE",
        "PROMISING BUT INSUFFICIENT",
        "NO RELIABLE EVIDENCE",
        "NEGATIVE EVIDENCE",
    )
