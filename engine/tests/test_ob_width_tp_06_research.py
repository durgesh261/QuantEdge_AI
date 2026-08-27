"""
Unit and Integration Tests for OB Width TP 0.6% Research Experiment.

Tests:
1. Determinism and reproducibility across runs.
2. Global 1-trade-at-a-time portfolio lock (zero overlapping intervals).
3. Exact TP calculation for Regime A (<= 0.6%) and Regime B (> 0.6%).
4. Continuous trade-by-trade compounding accounting integrity.
5. Long and short price geometry correctness.
6. Preservation of production governance invariants.
"""

import json
from pathlib import Path
import pandas as pd
import pytest

from quantedge.ai.evaluation.ob_width_tp_06_research import OBWidthTP06ResearchEngine
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root


@pytest.fixture
def sample_master_df():
    repo_root = _find_repo_root()
    master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    assert master_path.exists()
    return pd.read_csv(master_path)


def test_ob_width_tp_determinism(sample_master_df):
    """Verify that two independent runs produce bit-for-bit identical results."""
    engine1 = OBWidthTP06ResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res1 = engine1.run_backtest()

    engine2 = OBWidthTP06ResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res2 = engine2.run_backtest()

    assert res1["overall"] == res2["overall"]
    assert len(res1["trades"]) == len(res2["trades"])


def test_global_one_trade_lock_integrity(sample_master_df):
    """Verify that no two trades overlap in time across all 4 assets."""
    engine = OBWidthTP06ResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    assert len(trades) > 100
    for i in range(len(trades) - 1):
        curr_exit = pd.Timestamp(trades[i]["exit_timestamp"])
        next_entry = pd.Timestamp(trades[i + 1]["timestamp"])
        assert next_entry >= curr_exit, f"Trade #{trades[i+1]['trade_number']} started at {next_entry} before Trade #{trades[i]['trade_number']} exited at {curr_exit}"


def test_tp_regimes_math(sample_master_df):
    """Verify that Regime A gets 1.7143R and Regime B gets 1.0000R planned RR."""
    engine = OBWidthTP06ResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    for t in trades:
        if t["ob_width_percent"] <= 0.60:
            assert t["tp_regime"] == "REGIME_A_LE_06"
            assert pytest.approx(t["planned_rr"], abs=1e-3) == 1.7143
        else:
            assert t["tp_regime"] == "REGIME_B_GT_06"
            assert pytest.approx(t["planned_rr"], abs=1e-3) == 1.0000

        # Verify price geometry
        if t["direction"] == "LONG":
            assert t["tp_price"] > t["entry_price"] > t["sl_price"]
        else:
            assert t["tp_price"] < t["entry_price"] < t["sl_price"]


def test_compounding_ledger_accounting(sample_master_df):
    """Verify that starting_capital + pnl_dollar == ending_capital for every trade."""
    engine = OBWidthTP06ResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    for i in range(len(trades)):
        t = trades[i]
        expected_end_10 = round(t["starting_capital_10pct"] + t["pnl_dollar_10pct"], 4)
        assert pytest.approx(t["ending_capital_10pct"], abs=1e-3) == expected_end_10

        if i < len(trades) - 1:
            next_t = trades[i + 1]
            assert pytest.approx(next_t["starting_capital_10pct"], abs=1e-3) == t["ending_capital_10pct"]


def test_governance_invariants():
    """Verify that production governance locks remain immutable."""
    repo_root = _find_repo_root()
    manifest_path = repo_root / "docs" / "ai" / "ai_governance_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            promo_status = manifest.get("ai_promotion_status") or manifest.get("promotion_status") or manifest.get("AI_PROMOTION_STATUS")
            assert promo_status == "REJECTED"
            assert manifest.get("live_execution_authorized") is False
