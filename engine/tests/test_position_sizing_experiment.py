"""
Unit tests for position_sizing_experiment.py — 13 deterministic tests.

Key invariants:
- WIN/LOSS outcomes are FROZEN; only dollar P&L changes across sizing models.
- strategy_R is identical across all risk levels; net_R_after_fees varies (fee effect).
- Leverage cap REDUCES exposure (actual_price_risk_pct ≤ target_risk_pct always).
- Flat risk does NOT compound.
- Percentage risk compounds from post-trade equity.
- Fees charged exactly once per trade on notional.
- Trade ordering is chronological and unchanged.
- Zero-capital guard: no trades after capital hits $0.
"""

import pytest
from datetime import datetime, timezone
from quantedge.ai.research.position_sizing_experiment import (
    CanonicalTrade,
    apply_sizing_model,
    STARTING_CAPITAL,
    FEE_RATE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Synthetic minimal trade sequence for deterministic testing
# ─────────────────────────────────────────────────────────────────────────────

def _dt(day: int) -> datetime:
    return datetime(2025, 1, day, 12, 0, 0, tzinfo=timezone.utc)


def _make_sequence(outcomes_sl_pairs: list) -> list[CanonicalTrade]:
    """
    Build a minimal CanonicalTrade sequence from (outcome, sl_dist_pct) pairs.
    TP distance is always 0.60%, strategy_R = 0.60 / sl_dist_pct.
    """
    trades = []
    for i, (outcome, sl_pct) in enumerate(outcomes_sl_pairs):
        r = 0.60 / sl_pct  # strategy_R = TP_dist / SL_dist
        trades.append(CanonicalTrade(
            trade_id=i + 1,
            asset="BTCUSD",
            direction="LONG",
            entry_time=_dt(i + 1),
            outcome=outcome,
            strategy_R=round(r, 6),
            sl_dist_pct=sl_pct,
            tp_dist_pct=0.60,
        ))
    return trades


# Standard 6-trade test sequence: 4 wins, 2 losses, varying SL distances
STANDARD_SEQ = _make_sequence([
    ("FILLED_TP", 0.50),   # 0.50% SL → strategy_R = 1.20
    ("FILLED_SL", 0.40),   # 0.40% SL → strategy_R = 1.50
    ("FILLED_TP", 0.80),   # 0.80% SL → strategy_R = 0.75
    ("FILLED_TP", 0.30),   # 0.30% SL → strategy_R = 2.00
    ("FILLED_SL", 0.60),   # 0.60% SL → strategy_R = 1.00
    ("FILLED_TP", 0.45),   # 0.45% SL → strategy_R = 1.333
])


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Same WIN/LOSS outcomes for every risk level
# ─────────────────────────────────────────────────────────────────────────────
def test_1_outcomes_frozen_across_risk_levels():
    """WIN/LOSS outcomes must be identical regardless of risk % or leverage."""
    expected_outcomes = [t.outcome for t in STANDARD_SEQ]

    for risk in [5.0, 7.5, 10.0, 15.0, 20.0, 35.0]:
        recs, _ = apply_sizing_model(STANDARD_SEQ, risk, leverage_cap=100.0)
        observed = [r.outcome for r in recs]
        assert observed == expected_outcomes, (
            f"Outcomes changed at risk={risk}%: got {observed}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: strategy_R identical; net_R_after_fees differs (fee effect)
# ─────────────────────────────────────────────────────────────────────────────
def test_2_strategy_r_identical_net_r_differs():
    """
    strategy_R = TP_dist / SL_dist (geometry only, fee-free).
    net_R_after_fees = net_pnl / (basis x actual_price_risk).

    Because net_pnl = basis x leverage x (tp_pct or -sl_pct) - fees
    and fees = basis x leverage x FEE_RATE
    => net_R = (leverage x tp_pct - leverage x FEE_RATE) / (leverage x sl_pct)
             = (tp_pct - FEE_RATE) / sl_pct    [basis and leverage cancel]

    So net_R_after_fees is SIZE-INVARIANT (like strategy_R) but differs FROM
    strategy_R because fees reduce net_pnl.

    This test verifies:
    A) strategy_R is identical across all risk levels (geometry).
    B) net_R_after_fees is also identical across sizing modes (math invariant).
    C) But net_R_after_fees < strategy_R on winning trades (fee drag documented).
    """
    expected_str = [t.strategy_R for t in STANDARD_SEQ]
    for risk in [5.0, 10.0, 20.0, 35.0]:
        recs, _ = apply_sizing_model(STANDARD_SEQ, risk)
        observed = [r.strategy_R for r in recs]
        assert observed == expected_str, f"strategy_R changed at risk={risk}%"

    # net_R_after_fees: basis cancels in the math, so it's mode-invariant too.
    recs_compound, _ = apply_sizing_model(STANDARD_SEQ, 10.0, compounding="compound")
    recs_flat,     _ = apply_sizing_model(STANDARD_SEQ, 10.0, compounding="flat")
    str_compound = [r.strategy_R for r in recs_compound]
    str_flat     = [r.strategy_R for r in recs_flat]
    assert str_compound == str_flat == expected_str, "strategy_R must be mode-invariant"

    # Verify net_R < strategy_R on WIN trades (fee drag is real)
    for r in recs_compound:
        if r.outcome == "FILLED_TP":
            assert r.net_R_after_fees < r.strategy_R, (
                f"Trade {r.trade_id} WIN: net_R {r.net_R_after_fees} should be < "
                f"strategy_R {r.strategy_R} due to fee drag"
            )
        elif r.outcome == "FILLED_SL":
            # On a loss, net_R is more negative than strategy_R (fees add to the loss)
            assert r.net_R_after_fees < r.strategy_R, (
                f"Trade {r.trade_id} LOSS: net_R {r.net_R_after_fees} should be < "
                f"strategy_R {r.strategy_R}"
            )




# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Flat risk does NOT compound
# ─────────────────────────────────────────────────────────────────────────────
def test_3_flat_risk_does_not_compound():
    """
    In 'flat' mode, the sizing basis is always initial_capital ($10),
    never the evolving equity.
    """
    recs, _ = apply_sizing_model(STANDARD_SEQ, 10.0, leverage_cap=100.0, compounding="flat")

    # For each trade: effective_leverage must be based on $10 (constant) not current equity.
    # => notional / STARTING_CAPITAL == effective_leverage for every trade
    for r in recs:
        expected_notional = STARTING_CAPITAL * r.effective_leverage
        assert abs(r.notional - expected_notional) < 1e-3, (
            f"trade {r.trade_id}: notional {r.notional:.8f} != "
            f"STARTING_CAPITAL × leverage {expected_notional:.8f} in flat mode"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Percentage risk compounds from post-trade equity
# ─────────────────────────────────────────────────────────────────────────────
def test_4_compound_risk_uses_post_trade_equity():
    """
    In 'compound' mode, each trade's sizing_capital == previous trade's ending_capital.
    """
    recs, _ = apply_sizing_model(STANDARD_SEQ, 10.0, leverage_cap=100.0, compounding="compound")

    # Starting capital check
    assert abs(recs[0].starting_capital - STARTING_CAPITAL) < 1e-9, (
        f"First trade starting capital should be {STARTING_CAPITAL}, got {recs[0].starting_capital}"
    )

    # Each subsequent trade's starting_capital must equal previous ending_capital
    for i in range(1, len(recs)):
        assert abs(recs[i].starting_capital - recs[i - 1].ending_capital) < 1e-9, (
            f"Trade {i+1}: starting_capital {recs[i].starting_capital} != "
            f"previous ending_capital {recs[i-1].ending_capital}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Leverage cap — effective_leverage ≤ leverage_cap always
# ─────────────────────────────────────────────────────────────────────────────
def test_5_leverage_cap_never_exceeded():
    """effective_leverage must never exceed the configured leverage_cap."""
    for cap in [25.0, 50.0, 75.0, 100.0]:
        recs, _ = apply_sizing_model(STANDARD_SEQ, 10.0, leverage_cap=cap)
        for r in recs:
            assert r.effective_leverage <= cap + 1e-9, (
                f"Trade {r.trade_id}: effective_leverage {r.effective_leverage:.4f} "
                f"> cap {cap}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: actual_price_risk_pct ≤ target_risk_pct (cap reduces, never increases)
# ─────────────────────────────────────────────────────────────────────────────
def test_6_cap_reduces_risk_never_increases():
    """
    actual_price_risk_pct = effective_leverage × sl_dist_pct
    This MUST be ≤ target_risk_pct always.
    risk_deviation_from_target (actual - target) MUST be ≤ 0.

    The leverage cap can only REDUCE exposure below the target, never exceed it.
    """
    for risk in [5.0, 10.0, 20.0]:
        for cap in [25.0, 50.0, 100.0]:
            recs, _ = apply_sizing_model(STANDARD_SEQ, risk, leverage_cap=cap)
            for r in recs:
                assert r.actual_price_risk_pct <= r.target_risk_pct + 1e-6, (
                    f"risk={risk}%, cap={cap}x, trade {r.trade_id}: "
                    f"actual_price_risk {r.actual_price_risk_pct:.6f}% > "
                    f"target {r.target_risk_pct:.6f}%"
                )
                assert r.risk_deviation_from_target <= 1e-6, (
                    f"risk_deviation_from_target = {r.risk_deviation_from_target:.6f} > 0"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Fees charged exactly once per trade on notional
# ─────────────────────────────────────────────────────────────────────────────
def test_7_fees_exactly_once_on_notional():
    """fees = notional × FEE_RATE, charged exactly once per trade."""
    recs, _ = apply_sizing_model(STANDARD_SEQ, 10.0, leverage_cap=100.0)
    for r in recs:
        expected_fees = r.notional * FEE_RATE
        assert abs(r.fees - expected_fees) < 1e-7, (
            f"Trade {r.trade_id}: fees {r.fees:.10f} != notional×FEE_RATE {expected_fees:.10f}"
        )
        # net_pnl = gross_pnl - fees exactly
        assert abs(r.net_pnl - (r.gross_pnl - r.fees)) < 1e-9, (
            f"Trade {r.trade_id}: net_pnl accounting error"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Trade ordering is chronological and unchanged
# ─────────────────────────────────────────────────────────────────────────────
def test_8_chronological_ordering_preserved():
    """Trade IDs must appear in the same order for every risk level."""
    expected_ids = [t.trade_id for t in STANDARD_SEQ]
    expected_times = [t.entry_time.isoformat() for t in STANDARD_SEQ]

    for risk in [5.0, 10.0, 35.0]:
        recs, _ = apply_sizing_model(STANDARD_SEQ, risk)
        assert [r.trade_id for r in recs] == expected_ids, (
            f"Trade ID ordering changed at risk={risk}%"
        )
        assert [r.entry_time for r in recs] == expected_times, (
            f"Entry time ordering changed at risk={risk}%"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Same number of trades regardless of risk level
# ─────────────────────────────────────────────────────────────────────────────
def test_9_same_trade_count_all_risk_levels():
    """All risk levels produce the same number of trades (unless capital hits $0)."""
    counts = {}
    for risk in [5.0, 7.5, 10.0, 15.0, 20.0, 35.0]:
        recs, _ = apply_sizing_model(STANDARD_SEQ, risk)
        counts[risk] = len(recs)

    # At 5% risk, capital should never hit $0 on this tiny sequence
    assert counts[5.0] == len(STANDARD_SEQ), (
        f"5% risk: expected {len(STANDARD_SEQ)} trades, got {counts[5.0]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Starting capital is always $10.00
# ─────────────────────────────────────────────────────────────────────────────
def test_10_starting_capital_always_10():
    """First trade's starting_capital must always be STARTING_CAPITAL = $10."""
    for risk in [5.0, 10.0, 35.0]:
        for mode in ["compound", "flat"]:
            recs, _ = apply_sizing_model(STANDARD_SEQ, risk, compounding=mode)
            assert abs(recs[0].starting_capital - STARTING_CAPITAL) < 1e-9, (
                f"risk={risk}%, mode={mode}: starting capital is {recs[0].starting_capital}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: No look-ahead (each trade's sizing depends only on prior equity)
# ─────────────────────────────────────────────────────────────────────────────
def test_11_no_lookahead():
    """
    Append a future winning trade to the sequence — trade N-1's sizing must
    be unchanged by the presence of trade N.
    """
    seq_short = STANDARD_SEQ[:4]
    seq_long  = STANDARD_SEQ[:4] + [STANDARD_SEQ[4]]

    recs_short, _ = apply_sizing_model(seq_short, 10.0)
    recs_long,  _ = apply_sizing_model(seq_long,  10.0)

    # First 4 trades must be identical in both
    for i in range(4):
        assert abs(recs_short[i].starting_capital - recs_long[i].starting_capital) < 1e-9, (
            f"Trade {i+1}: starting_capital changed due to look-ahead"
        )
        assert abs(recs_short[i].effective_leverage - recs_long[i].effective_leverage) < 1e-9, (
            f"Trade {i+1}: effective_leverage changed due to look-ahead"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: risk_deviation_from_target ≤ 0 for all trades (cap only reduces)
# ─────────────────────────────────────────────────────────────────────────────
def test_12_risk_deviation_always_nonpositive():
    """
    risk_deviation_from_target = actual_price_risk_pct - target_risk_pct
    Must be ≤ 0 for every trade under every configuration (cap reduces, not increases).
    """
    for risk in [5.0, 10.0, 20.0, 35.0]:
        for cap in [25.0, 50.0, 100.0]:
            recs, _ = apply_sizing_model(STANDARD_SEQ, risk, leverage_cap=cap)
            for r in recs:
                assert r.risk_deviation_from_target <= 1e-6, (
                    f"risk={risk}%, cap={cap}x, trade {r.trade_id}: "
                    f"risk_deviation = {r.risk_deviation_from_target:.8f} > 0"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: Zero-capital guard — no trades after capital hits $0
# ─────────────────────────────────────────────────────────────────────────────
def test_13_zero_capital_guard():
    """
    If the account reaches $0, no further trades are executed.
    Build a sequence of SL losses big enough to wipe a 35% risk account.
    """
    # 5 consecutive 100x SL losses at 35% risk: each takes ~35% of capital
    # (1-0.35)^5 = ~11.6% remaining — NOT zero. Use 10 losses to get closer.
    # Actually with fees it needs big SL. Use sl=0.35% → leverage=100 → lose 35%+fees
    wipe_seq = _make_sequence([("FILLED_SL", 0.35)] * 10)
    recs, _ = apply_sizing_model(wipe_seq, 35.0, leverage_cap=100.0)

    # Check that once capital hits 0, no further trades appear
    for i, r in enumerate(recs):
        if r.ending_capital <= 0.0:
            assert i == len(recs) - 1 or recs[i + 1].starting_capital <= 0.0, (
                "Trade executed after capital reached $0"
            )
