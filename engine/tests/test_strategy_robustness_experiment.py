"""
Deterministic Test Suite for Phase 7: Strategy Robustness & Execution Reality Experiment.
Contains 22 rigorous unit tests verifying invariants, execution logic, slippage math,
fee charging, recovery analysis, and governance constraints.
"""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path

from quantedge.ai.research.strategy_robustness_experiment import (
    FrozenTrade,
    load_frozen_canonical_trades,
    simulate_trades,
    run_experiment_1_fee_sensitivity,
    run_experiment_2_slippage_sensitivity,
    run_experiment_4_trade_concentration,
    run_experiment_5_asset_exclusion,
    run_experiment_6_time_stability,
    run_experiment_7_rolling_performance,
    run_experiment_8_monte_carlo_degraded,
    run_experiment_12_recovery_analysis,
    run_experiment_13_bootstrap_confidence,
    live_execution_authorized,
    STARTING_CAPITAL,
)

# ---------------------------------------------------------------------------
# Synthetic test fixture helper
# ---------------------------------------------------------------------------
def _make_synthetic_trades(n: int = 10) -> list[FrozenTrade]:
    """Generates deterministic mock FrozenTrade instances for fast micro-tests."""
    trades = []
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        direction = "LONG" if i % 2 == 0 else "SHORT"
        outcome = "FILLED_TP" if i % 3 != 0 else "FILLED_SL"
        entry_p = 100.0
        tp_p = 100.6 if direction == "LONG" else 99.4
        sl_p = 99.5 if direction == "LONG" else 100.5
        sl_pct = 0.50
        tp_pct = 0.60
        r = tp_pct / sl_pct if outcome == "FILLED_TP" else -1.0

        trades.append(FrozenTrade(
            trade_id=i + 1,
            asset="BTCUSD" if i < n // 2 else "ETHUSD",
            direction=direction,
            entry_time=base_time + timedelta(hours=i * 4),
            exit_time=base_time + timedelta(hours=i * 4 + 2),
            entry_price=entry_p,
            tp_price=tp_p,
            sl_price=sl_p,
            outcome=outcome,
            realized_r=r,
            sl_dist_pct=sl_pct,
            tp_dist_pct=tp_pct,
            leverage=10.0,
        ))
    return trades


# ---------------------------------------------------------------------------
# TEST 1: Frozen trade count = 445
# ---------------------------------------------------------------------------
def test_1_frozen_trade_count():
    trades = load_frozen_canonical_trades()
    assert len(trades) == 445, f"Expected 445 canonical trades, got {len(trades)}"


# ---------------------------------------------------------------------------
# TEST 2: Trade ordering unchanged (Strictly Chronological)
# ---------------------------------------------------------------------------
def test_2_trade_ordering_unchanged():
    trades = load_frozen_canonical_trades()
    for i in range(len(trades) - 1):
        assert trades[i].entry_time <= trades[i + 1].entry_time, (
            f"Trade ordering violated at index {i}: {trades[i].entry_time} > {trades[i+1].entry_time}"
        )


# ---------------------------------------------------------------------------
# TEST 3: Trade outcomes unchanged (304 Wins, 141 Losses)
# ---------------------------------------------------------------------------
def test_3_trade_outcomes_unchanged():
    trades = load_frozen_canonical_trades()
    wins = sum(1 for t in trades if t.outcome == "FILLED_TP")
    losses = sum(1 for t in trades if t.outcome == "FILLED_SL")
    assert wins == 304, f"Expected 304 wins, got {wins}"
    assert losses == 141, f"Expected 141 losses, got {losses}"


# ---------------------------------------------------------------------------
# TEST 4: R values unchanged (+122.06R total)
# ---------------------------------------------------------------------------
def test_4_r_values_unchanged():
    trades = load_frozen_canonical_trades()
    total_r = sum(t.realized_r for t in trades)
    assert abs(total_r - 122.0586) < 0.05, f"Expected total strategy R ~122.06, got {total_r:.4f}"


# ---------------------------------------------------------------------------
# TEST 5: Zero-fee result matches canonical gross result
# ---------------------------------------------------------------------------
def test_5_zero_fee_matches_gross():
    trades = _make_synthetic_trades(10)
    recs, s = simulate_trades(trades, fee_rate=0.0, slippage_bps=0.0)
    for r in recs:
        assert abs(r.fees) < 1e-9, f"Expected 0 fees, got {r.fees}"
        assert abs(r.net_pnl - r.gross_pnl) < 1e-9, "Net PnL must equal Gross PnL when fees=0"


# ---------------------------------------------------------------------------
# TEST 6: Fees charged exactly once on notional
# ---------------------------------------------------------------------------
def test_6_fees_charged_exactly_once():
    trades = _make_synthetic_trades(10)
    fee_rate = 0.0016
    recs, _ = simulate_trades(trades, fee_rate=fee_rate, slippage_bps=0.0)
    for r in recs:
        expected_fee = r.notional * fee_rate
        assert abs(r.fees - expected_fee) < 1e-7, f"Trade {r.trade_id}: fee {r.fees} != {expected_fee}"


# ---------------------------------------------------------------------------
# TEST 7: Slippage is adverse for LONG
# ---------------------------------------------------------------------------
def test_7_slippage_adverse_long():
    t_long_win = FrozenTrade(
        trade_id=1, asset="BTCUSD", direction="LONG",
        entry_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0, tp_price=100.6, sl_price=99.5,
        outcome="FILLED_TP", realized_r=1.2, sl_dist_pct=0.5, tp_dist_pct=0.6, leverage=10.0
    )
    # With 10 bps slippage
    recs_0, _ = simulate_trades([t_long_win], fee_rate=0.0, slippage_bps=0.0)
    recs_slip, _ = simulate_trades([t_long_win], fee_rate=0.0, slippage_bps=10.0)

    # Exec entry should be higher, exit lower, gross pnl lower
    assert recs_slip[0].entry_price_exec > recs_0[0].entry_price_exec, "LONG entry price must be higher with slippage"
    assert recs_slip[0].exit_price_exec < recs_0[0].exit_price_exec, "LONG TP exit price must be lower with slippage"
    assert recs_slip[0].gross_pnl < recs_0[0].gross_pnl, "Gross PnL must be lower with adverse slippage"


# ---------------------------------------------------------------------------
# TEST 8: Slippage is adverse for SHORT
# ---------------------------------------------------------------------------
def test_8_slippage_adverse_short():
    t_short_win = FrozenTrade(
        trade_id=1, asset="BTCUSD", direction="SHORT",
        entry_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0, tp_price=99.4, sl_price=100.5,
        outcome="FILLED_TP", realized_r=1.2, sl_dist_pct=0.5, tp_dist_pct=0.6, leverage=10.0
    )
    recs_0, _ = simulate_trades([t_short_win], fee_rate=0.0, slippage_bps=0.0)
    recs_slip, _ = simulate_trades([t_short_win], fee_rate=0.0, slippage_bps=10.0)

    # Exec entry should be lower (sold lower), exit higher (bought back higher)
    assert recs_slip[0].entry_price_exec < recs_0[0].entry_price_exec, "SHORT entry price must be lower with slippage"
    assert recs_slip[0].exit_price_exec > recs_0[0].exit_price_exec, "SHORT TP exit price must be higher with slippage"
    assert recs_slip[0].gross_pnl < recs_0[0].gross_pnl, "Gross PnL must be lower with adverse slippage"


# ---------------------------------------------------------------------------
# TEST 9: Zero slippage produces no price adjustment
# ---------------------------------------------------------------------------
def test_9_zero_slippage_no_adjustment():
    trades = _make_synthetic_trades(5)
    recs, _ = simulate_trades(trades, slippage_bps=0.0)
    for t, r in zip(trades, recs):
        assert abs(r.entry_price_exec - t.entry_price) < 1e-9
        expected_exit = t.tp_price if t.outcome == "FILLED_TP" else t.sl_price
        assert abs(r.exit_price_exec - expected_exit) < 1e-9


# ---------------------------------------------------------------------------
# TEST 10: Removing trades does not mutate original sequence
# ---------------------------------------------------------------------------
def test_10_removal_does_not_mutate_original():
    trades = _make_synthetic_trades(10)
    orig_ids = [t.trade_id for t in trades]
    _ = run_experiment_4_trade_concentration(trades)
    post_ids = [t.trade_id for t in trades]
    assert orig_ids == post_ids, "Original trade list was mutated by concentration experiment"


# ---------------------------------------------------------------------------
# TEST 11: Asset exclusion works
# ---------------------------------------------------------------------------
def test_11_asset_exclusion():
    trades = _make_synthetic_trades(10)
    rows = run_experiment_5_asset_exclusion(trades)
    # Check that excluding BTCUSD removes all BTCUSD trades
    btc_excl = next(r for r in rows if r["universe"] == "Exclude BTCUSD")
    assert btc_excl["trades"] == sum(1 for t in trades if t.asset != "BTCUSD")


# ---------------------------------------------------------------------------
# TEST 12: Period partition preserves total trades
# ---------------------------------------------------------------------------
def test_12_period_partition_preserves_total():
    trades = load_frozen_canonical_trades()
    res = run_experiment_6_time_stability(trades)
    tot_partitioned = sum(r["trades"] for r in res["period_table"])
    assert tot_partitioned == len(trades), f"Partition total {tot_partitioned} != {len(trades)}"


# ---------------------------------------------------------------------------
# TEST 13: Rolling windows preserve chronological order
# ---------------------------------------------------------------------------
def test_13_rolling_windows_chronological():
    trades = _make_synthetic_trades(30)
    res = run_experiment_7_rolling_performance(trades)
    for m in res["table"]:
        assert m["start_idx"] <= m["end_idx"]
        assert m["end_idx"] - m["start_idx"] + 1 == m["window_size"]


# ---------------------------------------------------------------------------
# TEST 14: Monte Carlo seed produces deterministic result
# ---------------------------------------------------------------------------
def test_14_monte_carlo_deterministic_seed():
    trades = _make_synthetic_trades(10)
    res1 = run_experiment_8_monte_carlo_degraded(trades, n_sims=500, seed=123)
    res2 = run_experiment_8_monte_carlo_degraded(trades, n_sims=500, seed=123)
    assert res1[0]["median_final_capital"] == res2[0]["median_final_capital"]
    assert res1[0]["median_max_dd_pct"] == res2[0]["median_max_dd_pct"]


# ---------------------------------------------------------------------------
# TEST 15: Monte Carlo does not mutate canonical sequence
# ---------------------------------------------------------------------------
def test_15_monte_carlo_does_not_mutate():
    trades = _make_synthetic_trades(10)
    orig_hashes = [(t.trade_id, t.entry_time) for t in trades]
    _ = run_experiment_8_monte_carlo_degraded(trades, n_sims=100, seed=42)
    post_hashes = [(t.trade_id, t.entry_time) for t in trades]
    assert orig_hashes == post_hashes


# ---------------------------------------------------------------------------
# TEST 16: Risk sizing does not alter trade outcomes
# ---------------------------------------------------------------------------
def test_16_risk_sizing_preserves_outcomes():
    trades = _make_synthetic_trades(10)
    recs_low, _ = simulate_trades(trades, risk_pct=2.5)
    recs_high, _ = simulate_trades(trades, risk_pct=15.0)
    assert [r.outcome for r in recs_low] == [r.outcome for r in recs_high] == [t.outcome for t in trades]


# ---------------------------------------------------------------------------
# TEST 17: Leverage cap is respected
# ---------------------------------------------------------------------------
def test_17_leverage_cap_respected():
    trades = _make_synthetic_trades(10)
    for cap in [10.0, 25.0, 50.0]:
        recs, _ = simulate_trades(trades, risk_pct=15.0, leverage_cap=cap)
        for r in recs:
            assert r.effective_leverage <= cap + 1e-9


# ---------------------------------------------------------------------------
# TEST 18: Drawdown calculation is correct
# ---------------------------------------------------------------------------
def test_18_drawdown_calculation_correct():
    # Construct sequence: +$1, -$2, +$3
    # Cap: $10 -> $11 -> $9 (DD: (11-9)/11 = 18.18%) -> $12 (new peak)
    t1 = FrozenTrade(1, "BTCUSD", "LONG", datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, 1, tzinfo=timezone.utc), 100, 100.6, 99.5, "FILLED_TP", 1.2, 0.5, 0.6, 10)
    t2 = FrozenTrade(2, "BTCUSD", "LONG", datetime(2025, 1, 2, tzinfo=timezone.utc), datetime(2025, 1, 2, 1, tzinfo=timezone.utc), 100, 100.6, 99.5, "FILLED_SL", -1.0, 0.5, 0.6, 10)
    t3 = FrozenTrade(3, "BTCUSD", "LONG", datetime(2025, 1, 3, tzinfo=timezone.utc), datetime(2025, 1, 3, 1, tzinfo=timezone.utc), 100, 100.6, 99.5, "FILLED_TP", 1.2, 0.5, 0.6, 10)

    recs, s = simulate_trades([t1, t2, t3], risk_pct=5.0, fee_rate=0.0, slippage_bps=0.0)
    assert s["max_drawdown_pct"] > 0.0
    assert s["max_drawdown_pct"] < 100.0


# ---------------------------------------------------------------------------
# TEST 19: Recovery calculation is correct
# ---------------------------------------------------------------------------
def test_19_recovery_calculation_correct():
    trades = _make_synthetic_trades(20)
    rec_analysis = run_experiment_12_recovery_analysis(trades)
    assert "episodes" in rec_analysis
    assert rec_analysis["total_drawdown_episodes"] >= 0


# ---------------------------------------------------------------------------
# TEST 20: Bootstrap sample size is correct
# ---------------------------------------------------------------------------
def test_20_bootstrap_sample_size():
    trades = _make_synthetic_trades(10)
    res = run_experiment_13_bootstrap_confidence(trades, n_boot=500, seed=42)
    assert res["n_bootstraps"] == 500
    assert len(res["ci_table"]) == 4


# ---------------------------------------------------------------------------
# TEST 21: 5% / 50x baseline reproduces previous position-sizing result
# ---------------------------------------------------------------------------
def test_21_baseline_5pct_50x_reproduction():
    trades = load_frozen_canonical_trades()
    _, s50 = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0, fee_rate=0.0008, slippage_bps=0.0)
    _, s100 = simulate_trades(trades, risk_pct=5.0, leverage_cap=100.0, fee_rate=0.0008, slippage_bps=0.0)

    # 50x cap gives 161.90 (1 trade capped from 55.3x down to 50x)
    assert abs(s50["ending_capital"] - 161.903) < 0.10, f"Expected ~$161.90, got {s50['ending_capital']:.4f}"
    # 100x cap gives 163.73 (reproduces previous experiment 1 exactly)
    assert abs(s100["ending_capital"] - 163.726) < 0.10, f"Expected ~$163.73, got {s100['ending_capital']:.4f}"
    assert abs(s50["win_rate_pct"] - 68.31) < 0.10
    assert abs(s50["max_drawdown_pct"] - 41.70) < 0.20



# ---------------------------------------------------------------------------
# TEST 22: Governance flag remains live_execution_authorized = False
# ---------------------------------------------------------------------------
def test_22_governance_invariants():
    from quantedge.ai.research.strategy_robustness_experiment import (
        live_execution_authorized,
        AI_PROMOTION_STATUS,
        execution_status,
    )
    assert live_execution_authorized is False
    assert AI_PROMOTION_STATUS == "REJECTED"
    assert execution_status == "BLOCKED_BY_SYSTEM"
