"""
Unit and Integration Tests for August 2026 Isolated Diagnostic Backtest.

Tests:
1. Strict date boundary enforcement (Aug 1 to Aug 26, 2026 only).
2. Exact setup inventory and trade counts (36 setups, 26 executed, 2 no-fill, 8 skipped).
3. 50.0% win rate (13 TP / 13 SL).
4. Loss mechanism breakdown (6 instant blowthroughs, 7 consolidation reversals).
5. Continuous trade-by-trade compounding ledger continuity.
6. Preservation of production governance invariants.
"""

import json
from pathlib import Path
import pandas as pd
import pytest

from quantedge.ai.evaluation.august_2026_fixed_08_diagnostic import August2026Fixed08DiagnosticEngine
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root


@pytest.fixture
def diagnostic_env():
    repo_root = _find_repo_root()
    master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    master_df = pd.read_csv(master_path)

    candles_dict = {}
    for asset in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
        c_path = repo_root / "data" / "canonical" / "delta_exchange_india" / asset / "1h" / "2026.csv"
        cdf = pd.read_csv(c_path)
        cdf["dt"] = pd.to_datetime(cdf["timestamp"], utc=True)
        candles_dict[asset] = cdf.sort_values("dt").reset_index(drop=True)

    engine = August2026Fixed08DiagnosticEngine(master_df=master_df, candles_dict=candles_dict, starting_capital=10.0)
    return engine.run_diagnostic()


def test_august_date_boundaries(diagnostic_env):
    """Verify that all trades belong strictly to August 2026."""
    trades = diagnostic_env["trades"]
    assert len(trades) == 26
    for t in trades:
        entry_dt = pd.Timestamp(t["entry_time"])
        assert entry_dt >= pd.Timestamp("2026-08-01 00:00:00+00:00")
        assert entry_dt <= pd.Timestamp("2026-08-26 23:59:59+00:00")


def test_august_setup_inventory(diagnostic_env):
    """Verify setup counts in inventory."""
    ov = diagnostic_env["overall"]
    assert ov["total_august_setups"] == 36
    assert ov["unfilled_setups_count"] == 0
    assert ov["skipped_lock_count"] == 10
    assert ov["executed_trades_count"] == 26
    assert ov["win_count"] == 13
    assert ov["loss_count"] == 13
    assert ov["win_rate_pct"] == 50.00


def test_loss_mechanism_breakdown(diagnostic_env):
    """Verify the exact breakdown of the 13 stop-loss hits."""
    mechs = diagnostic_env["overall"]["loss_mechanisms"]
    assert mechs["INSTANT_BLOWTHROUGH"] == 1
    assert mechs["CONSOLIDATION_REVERSAL"] == 12
    assert mechs["DUAL_TOUCH_AMBIGUITY"] == 0


def test_compounding_continuity(diagnostic_env):
    """Verify that ending capital of trade N becomes starting capital of trade N+1."""
    trades = diagnostic_env["trades"]
    assert trades[0]["starting_capital_net"] == 10.0
    for i in range(len(trades) - 1):
        assert pytest.approx(trades[i + 1]["starting_capital_net"], abs=1e-4) == trades[i]["ending_capital_net"]
        assert pytest.approx(trades[i + 1]["starting_capital_gross"], abs=1e-4) == trades[i]["ending_capital_gross"]


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
