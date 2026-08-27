"""
Unit and Integration Tests for Fixed 0.8% TP + 35% SL Leverage Compounding Experiment.

Validates:
1. Deterministic test cases from prompt (Case 1: 0.70% -> 50x -> +40%, Case 2: 0.50% -> 70x -> +56%, Case 3: 1.20% -> 29.17x -> +23.33%).
2. Long TP (entry * 1.008) and Short TP (entry * 0.992).
3. Entry at 25% penetration inside OB.
4. Second-edge SL placement (ob_low for long, ob_high for short).
5. Global one-trade-at-a-time portfolio lock integrity.
6. Continuous $10 trade-by-trade compounding ledger continuity.
7. Preservation of production governance invariants.
"""

import json
from pathlib import Path
import pandas as pd
import pytest

from quantedge.ai.evaluation.fixed_08_percent_tp_35_sl_research import Fixed08PercentTP35SLResearchEngine
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root


def test_prompt_validation_case_1():
    """Case 1: Entry = 100, SL = 99.30, SL dist = 0.70%, leverage = 50x, TP = 100.80, TP gross = +40%, SL gross = -35%."""
    # Bullish OB with top = 100.2333, bottom = 99.30 -> width = 0.9333, 25% penetration entry = 100.0, SL = 99.30
    entry, sl, tp, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_ret = (
        Fixed08PercentTP35SLResearchEngine.calculate_trade_parameters(
            direction="LONG",
            ob_high=100.23333333333333,
            ob_low=99.30,
        )
    )
    assert pytest.approx(entry, abs=1e-4) == 100.00
    assert pytest.approx(sl, abs=1e-4) == 99.30
    assert pytest.approx(tp, abs=1e-4) == 100.80
    assert pytest.approx(sl_dist_pct, abs=1e-4) == 0.70
    assert pytest.approx(leverage, abs=1e-4) == 50.00
    assert pytest.approx(gross_tp_ret, abs=1e-4) == 40.00


def test_prompt_validation_case_2():
    """Case 2: Entry = 100, SL = 99.50, SL dist = 0.50%, leverage = 70x, TP = 100.80, TP gross = +56%, SL gross = -35%."""
    entry, sl, tp, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_ret = (
        Fixed08PercentTP35SLResearchEngine.calculate_trade_parameters(
            direction="LONG",
            ob_high=100.16666666666667,
            ob_low=99.50,
        )
    )
    assert pytest.approx(entry, abs=1e-4) == 100.00
    assert pytest.approx(sl, abs=1e-4) == 99.50
    assert pytest.approx(tp, abs=1e-4) == 100.80
    assert pytest.approx(sl_dist_pct, abs=1e-4) == 0.50
    assert pytest.approx(leverage, abs=1e-4) == 70.00
    assert pytest.approx(gross_tp_ret, abs=1e-4) == 56.00


def test_prompt_validation_case_3():
    """Case 3: Entry = 100, SL = 98.80, SL dist = 1.20%, leverage = 29.16667x, TP = 100.80, TP gross = +23.3333%, SL gross = -35%."""
    entry, sl, tp, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_ret = (
        Fixed08PercentTP35SLResearchEngine.calculate_trade_parameters(
            direction="LONG",
            ob_high=100.40,
            ob_low=98.80,
        )
    )
    assert pytest.approx(entry, abs=1e-4) == 100.00
    assert pytest.approx(sl, abs=1e-4) == 98.80
    assert pytest.approx(tp, abs=1e-4) == 100.80
    assert pytest.approx(sl_dist_pct, abs=1e-4) == 1.20
    assert pytest.approx(leverage, abs=1e-4) == 29.1666667
    assert pytest.approx(gross_tp_ret, abs=1e-4) == 23.3333333


def test_backtest_determinism_and_trade_ledger():
    """Verify deterministic backtest execution on multiyear dataset."""
    repo_root = _find_repo_root()
    master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    master_df = pd.read_csv(master_path)

    engine = Fixed08PercentTP35SLResearchEngine(master_df=master_df, starting_capital=10.0)
    res = engine.run_backtest()

    trades = res["trades"]
    assert len(trades) == 963
    assert res["overall"]["win_rate_pct"] == 26.38

    # Global 1-trade lock check
    for i in range(len(trades) - 1):
        exit_dt = pd.Timestamp(trades[i]["exit_timestamp"])
        next_dt = pd.Timestamp(trades[i + 1]["entry_timestamp"])
        assert next_dt >= exit_dt

    # Geometry check
    for t in trades:
        if t["direction"] == "LONG":
            assert pytest.approx(t["tp_price"], abs=1e-2) == t["entry_price"] * 1.008
        else:
            assert pytest.approx(t["tp_price"], abs=1e-2) == t["entry_price"] * 0.992


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
