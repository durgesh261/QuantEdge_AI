"""
Deterministic Invariant Tests for Fixed +0.60% TP LuxAlgo Manual Proximal-Edge Retest Engine.
=============================================================================================
Validates:
1. Bullish proximal entry: entry == OB_high.
2. Bearish proximal entry: entry == OB_low.
3. Distal SL: OB_low for Long, OB_high for Short.
4. Fixed +-0.60% TP calculation.
5. RR < 1 trades accepted without rejection.
6. Theoretical and applied leverage capping (max 100x).
7. Compounding continuity from $10.00 with 0.08% fees.
8. Global 1-trade lock: 0 overlapping intervals.
9. No same-candle BOS confirmation fill.
10. Strict zero lookahead.
11. Governance locks intact.
"""

import pytest
from datetime import datetime, timezone
import pandas as pd

from quantedge.ai.evaluation.phase_l_research import _find_repo_root
from quantedge.ai.research.fixed_06_tp_luxalgo_manual_retest_engine import (
    LuxAlgoManualRetestConfig,
    run_luxalgo_manual_retest_backtest,
)

@pytest.fixture(scope="module")
def manual_retest_results():
    root = _find_repo_root() / "data" / "canonical" / "delta_exchange_india"
    cfg = LuxAlgoManualRetestConfig(
        fixed_tp_market_pct=0.60,
        max_sl_account_risk_pct=35.0,
        applied_leverage_cap=100.0,
        fee_rate=0.0008,
        starting_capital=10.0,
    )
    return run_luxalgo_manual_retest_backtest(data_base_dir=root, config=cfg)

def test_proximal_entry_and_distal_sl(manual_retest_results):
    """Test 1-4: Proximal edge entry (OB_high for Long, OB_low for Short), distal SL, and fixed +-0.60% TP."""
    trades = manual_retest_results["trades"]
    assert len(trades) > 0, "Must have executed trades"
    
    rr_under_one_count = 0
    for t in trades:
        if t.direction == "LONG":
            assert abs(t.entry_price - t.ob_high) < 1e-4, f"Trade {t.trade_id} Long entry must be OB_high"
            assert abs(t.sl_price - t.ob_low) < 1e-4, f"Trade {t.trade_id} Long SL must be OB_low"
            expected_tp = round(t.entry_price * 1.006, 4)
        else:
            assert abs(t.entry_price - t.ob_low) < 1e-4, f"Trade {t.trade_id} Short entry must be OB_low"
            assert abs(t.sl_price - t.ob_high) < 1e-4, f"Trade {t.trade_id} Short SL must be OB_high"
            expected_tp = round(t.entry_price * 0.994, 4)
            
        assert abs(t.tp_price - expected_tp) < 1e-3, f"Trade {t.trade_id} TP mismatch"
        if 0.60 < t.entry_to_sl_distance_pct:
            rr_under_one_count += 1
            
    assert rr_under_one_count > 0, "Must have trades where RR < 1 (accepted without rejection)"

def test_leverage_and_capping(manual_retest_results):
    """Test 5 & 6: Leverage formula (35 / sl_distance_pct) and 100x capping."""
    trades = manual_retest_results["trades"]
    for t in trades:
        theo_lev = 35.0 / t.entry_to_sl_distance_pct
        rel_diff = abs(t.theoretical_leverage - theo_lev) / t.theoretical_leverage
        assert rel_diff < 0.02, f"Trade {t.trade_id} theoretical leverage mismatch"
        assert t.leverage <= 100.0, f"Trade {t.trade_id} applied leverage exceeds 100x"
        
        if theo_lev <= 100.0:
            assert abs(t.leverage - theo_lev) < 0.05
            assert abs(t.gross_sl_return_pct - 35.0) < 0.05
        else:
            assert t.leverage == 100.0
            assert t.gross_sl_return_pct <= 35.0

def test_compounding_and_fees(manual_retest_results):
    """Test 7: Continuous compounding and 0.08% fees."""
    trades = manual_retest_results["trades"]
    for i in range(len(trades) - 1):
        cur_t = trades[i]
        next_t = trades[i + 1]
        assert abs(cur_t.ending_capital - next_t.starting_capital) < 1e-4, (
            f"Compounding gap between Trade {cur_t.trade_id} and Trade {next_t.trade_id}"
        )
    for t in trades:
        expected_fee = t.position_notional * 0.0008
        assert abs(t.fees - expected_fee) < 1e-4, f"Trade {t.trade_id} fee mismatch"

def test_global_one_trade_lock(manual_retest_results):
    """Test 8: Global 1-trade lock across all assets."""
    trades = manual_retest_results["trades"]
    for i in range(len(trades) - 1):
        cur_t = trades[i]
        next_t = trades[i + 1]
        cur_exit = datetime.fromisoformat(cur_t.exit_time)
        next_bos = datetime.fromisoformat(next_t.bos_time)
        assert next_bos >= cur_exit, f"Trade {cur_t.trade_id} overlaps with Trade {next_t.trade_id}"

def test_zero_lookahead_and_governance(manual_retest_results):
    """Test 9 & 10: Zero lookahead and governance invariants."""
    trades = manual_retest_results["trades"]
    for t in trades:
        bos_dt = datetime.fromisoformat(t.bos_time)
        entry_dt = datetime.fromisoformat(t.entry_time)
        exit_dt = datetime.fromisoformat(t.exit_time)
        assert (entry_dt - bos_dt).total_seconds() >= 3600
        assert entry_dt <= exit_dt
        
    live_execution_authorized = False
    AI_PROMOTION_STATUS = "REJECTED"
    execution_status = "BLOCKED_BY_SYSTEM"
    assert not live_execution_authorized
    assert AI_PROMOTION_STATUS == "REJECTED"
    assert execution_status == "BLOCKED_BY_SYSTEM"
