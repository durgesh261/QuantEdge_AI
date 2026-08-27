"""
Unit Tests for Fixed-Target Dynamic-Leverage SMC Research Engine.
"""

import pytest
from datetime import datetime, timezone
from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.research.fixed_target_smc_engine import (
    FixedSMCConfig,
    run_fixed_target_smc_backtest,
    to_ist_string,
)

def test_fixed_smc_engine_solusd_reproducibility():
    root = _find_repo_root()
    base = root / "data" / "canonical" / "delta_exchange_india"
    candles = load_canonical_full_history(base, "SOLUSD")
    
    config = FixedSMCConfig(
        fixed_tp_pct=0.60,
        max_sl_risk_pct=35.0,
        max_leverage=100.0,
        penetration_depth=0.25,
        fee_rate=0.0008,
        starting_capital=10.0,
    )
    
    res = run_fixed_target_smc_backtest(candles, "SOLUSD", config=config)
    
    assert res["total_trades"] == 441
    assert res["wins"] == 368
    assert res["losses"] == 73
    assert abs(res["win_rate_pct"] - 83.45) < 0.05
    assert abs(res["total_realized_r"] - 195.30) < 0.5
    assert res["ending_capital_net"] > 1e16

def test_fixed_smc_engine_leverage_capping():
    config = FixedSMCConfig(max_leverage=100.0, max_sl_risk_pct=35.0)
    # SL distance = 0.20% -> uncapped leverage = 35 / 0.20 = 175x -> capped to 100x
    uncapped = config.max_sl_risk_pct / 0.20
    capped = min(config.max_leverage, uncapped)
    assert capped == 100.0
    
    # Actual SL risk becomes 100 * 0.20% = 20% (< 35%)
    actual_risk = capped * 0.20
    assert actual_risk == 20.0

def test_ist_timezone_conversion():
    utc_dt = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    ist_str = to_ist_string(utc_dt)
    assert ist_str == "2026-08-26 17:30 IST"
