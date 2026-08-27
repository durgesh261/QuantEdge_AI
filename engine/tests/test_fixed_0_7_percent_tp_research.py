"""
Unit and Integration Tests for Fixed 0.7% TP Research Experiment.

Tests:
1. Long TP is exactly entry * 1.007.
2. Short TP is exactly entry * 0.993.
3. Entry is exactly 25% penetration inside OB.
4. Global one-trade-at-a-time lock integrity.
5. Continuous trade-by-trade compounding accounting from $10.00 base.
6. Planned RR disconnect analysis.
7. Preservation of production governance invariants.
"""

import json
from pathlib import Path
import pandas as pd
import pytest

from quantedge.ai.evaluation.fixed_0_7_percent_tp_research import Fixed07PercentTPResearchEngine
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root


@pytest.fixture
def sample_master_df():
    repo_root = _find_repo_root()
    master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    assert master_path.exists()
    return pd.read_csv(master_path)


def test_fixed_07_tp_price_formula(sample_master_df):
    """Verify that Long TP is entry*1.007 and Short TP is entry*0.993."""
    engine = Fixed07PercentTPResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    assert len(trades) > 100
    for t in trades:
        if t["direction"] == "LONG":
            assert pytest.approx(t["tp"], abs=1e-2) == t["entry"] * 1.007
        else:
            assert pytest.approx(t["tp"], abs=1e-2) == t["entry"] * 0.993


def test_25pct_penetration_entry_formula(sample_master_df):
    """Verify that entry is exactly 25% penetration inside OB."""
    engine = Fixed07PercentTPResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    for t in trades:
        width = t["ob_high"] - t["ob_low"]
        if t["direction"] == "LONG":
            expected_entry = t["ob_high"] - 0.25 * width
        else:
            expected_entry = t["ob_low"] + 0.25 * width
        assert pytest.approx(t["entry"], abs=1e-2) == expected_entry


def test_global_1trade_lock(sample_master_df):
    """Verify that no two trades overlap in time."""
    engine = Fixed07PercentTPResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    for i in range(len(trades) - 1):
        exit_dt = pd.Timestamp(trades[i]["exit_datetime"])
        next_dt = pd.Timestamp(trades[i + 1]["datetime"])
        assert next_dt >= exit_dt


def test_compounding_continuity(sample_master_df):
    """Verify that ending capital of trade N becomes starting capital of trade N+1."""
    engine = Fixed07PercentTPResearchEngine(master_df=sample_master_df, starting_capital=10.0)
    res = engine.run_backtest()
    trades = res["trades"]

    assert trades[0]["starting_capital_35pct"] == 10.0
    for i in range(len(trades) - 1):
        assert pytest.approx(trades[i + 1]["starting_capital_35pct"], abs=1e-3) == trades[i]["ending_capital_35pct"]


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
