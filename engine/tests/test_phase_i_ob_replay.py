"""
Phase I — Real OB Historical Trade Replay test suite.

Covers (per Phase I specification §22):
- OB extraction from the authoritative SMC engine
- entry rule
- second-edge SL
- TP (PHASE_I_OB_60TP_35SL = 60/35 multiple)
- candle replay (forward-only)
- same-candle SL/TP handling (SL FIRST policy)
- AI decision timing (decision precedes outcome knowledge)
- no future-feature leakage
- leverage calculation (production dynamic formula)
- liquidation detection
- deterministic replay
- SMC vs SMC+AI accounting (Groups A/B/C)
- rejected-trade accounting
- governance lock (REJECTED => BLOCKED_BY_SYSTEM, live unauthorized)
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from quantedge.ai.evaluation.phase_i_ob_replay import (
    MAINTENANCE_MARGIN_RATE,
    PHASE_I_TP_RR_CONFIG,
    REPLAY_HORIZON_BARS,
    WARMUP_BARS,
    PhaseISetup,
    build_smc_context,
    compute_dynamic_leverage,
    compute_extended_metrics,
    compute_net_r,
    compute_score_buckets,
    estimate_liquidation,
    evaluate_phase_i_gate,
    extract_phase_i_setups,
    load_canonical_candles,
    mbb_block_size,
    moving_block_bootstrap_groups,
    replay_phase_i_trades,
)
from quantedge.market_data.models import Candle, MarketDataSource, Timeframe


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[3]


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════


def _mk_candles(ohlc: list[tuple[float, float, float, float]], symbol: str = "TEST") -> list[Candle]:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol=symbol,
            timeframe=Timeframe.H1,
            timestamp=t0 + timedelta(hours=i),
            open=Decimal(str(o)),
            high=Decimal(str(h)),
            low=Decimal(str(l)),
            close=Decimal(str(c)),
            volume=Decimal("10"),
            source=MarketDataSource.HISTORICAL,
        )
        for i, (o, h, l, c) in enumerate(ohlc)
    ]


def _mk_setup(
    setup_id: str = "TEST_10_LONG",
    direction: str = "LONG",
    decision_bar: int = 10,
    entry: float = 100.0,
    sl: float = 99.0,
) -> PhaseISetup:
    risk = abs(entry - sl)
    if direction == "LONG":
        tp = round(entry + risk * float(PHASE_I_TP_RR_CONFIG.reward_multiple), 8)
    else:
        tp = round(entry - risk * float(PHASE_I_TP_RR_CONFIG.reward_multiple), 8)
    return PhaseISetup(
        setup_id=setup_id,
        asset="TEST",
        timeframe="1h",
        direction=direction,
        decision_bar=decision_bar,
        decision_time=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=decision_bar)).isoformat(),
        creation_time=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=decision_bar - 5)).isoformat(),
        confirmation_time=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=decision_bar - 2)).isoformat(),
        ob_high=entry + risk * 0.5,
        ob_low=sl,
        entry_price=round(entry, 8),
        sl_price=round(sl, 8),
        tp_price=tp,
        risk_distance=round(risk, 8),
        stop_distance_percent=round(risk / entry * 100.0, 6),
        atr_normalized_stop_distance=1.0,
        leverage=compute_dynamic_leverage(risk / entry * 100.0),
        structural_event_id="internal|bos|brk5|form3",
        features_24=tuple([0.5] * 23 + ([1.0] if direction == "LONG" else [0.0])),
    )


def _flat_then_move(n_flat: int = 220, move: str = "up") -> list[Candle]:
    """Synthetic series long enough for ATR(200): flat then a directional impulse."""
    ohlc: list[tuple[float, float, float, float]] = []
    price = 100.0
    for i in range(n_flat):
        o = price + (0.02 if i % 2 else -0.02)
        c = price - (0.02 if i % 2 else -0.02)
        ohlc.append((o, max(o, c) + 0.04, min(o, c) - 0.04, c))
    step = 0.30 if move == "up" else -0.30
    for _ in range(20):
        o = price
        price = round(price + step, 4)
        ohlc.append((o, max(o, price) + 0.05, min(o, price) - 0.05, price))
    return _mk_candles(ohlc)


# ═════════════════════════════════════════════════════════════════════════════
# Leverage calculation (production formula)
# ═════════════════════════════════════════════════════════════════════════════


class TestLeverageCalculation:
    def test_one_percent_stop_is_exactly_thirty_five_x(self):
        assert compute_dynamic_leverage(1.0) == 35

    def test_proportional_scaling(self):
        assert compute_dynamic_leverage(0.5) == 70
        assert compute_dynamic_leverage(2.0) == 17  # floor(17.5)

    def test_floor_not_round(self):
        assert compute_dynamic_leverage(1.5) == 23  # floor(23.33)

    def test_cap_at_production_max(self):
        assert compute_dynamic_leverage(0.0001) == 100
        assert compute_dynamic_leverage(0.001) == 100

    def test_minimum_one(self):
        assert compute_dynamic_leverage(50.0) == 1
        assert compute_dynamic_leverage(0.0) == 1
        assert compute_dynamic_leverage(-1.0) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Liquidation detection
# ═════════════════════════════════════════════════════════════════════════════


class TestLiquidationDetection:
    def test_normal_stop_safe_long(self):
        liq = estimate_liquidation(entry_price=100.0, stop_distance_fraction=0.01, leverage=35, direction="LONG")
        assert liq["liquidation_before_sl"] is False
        assert liq["liquidation_price"] < 99.0  # beyond the SL

    def test_normal_stop_safe_short(self):
        liq = estimate_liquidation(entry_price=100.0, stop_distance_fraction=0.01, leverage=35, direction="SHORT")
        assert liq["liquidation_before_sl"] is False
        assert liq["liquidation_price"] > 101.0

    def test_extreme_leverage_flags_violation(self):
        # Effective leverage so high that liquidation sits inside the stop distance.
        liq = estimate_liquidation(entry_price=100.0, stop_distance_fraction=0.002, leverage=400, direction="LONG")
        assert liq["liq_distance_fraction"] <= 0.002
        assert liq["liquidation_before_sl"] is True

    def test_margin_fraction_inverse_of_leverage(self):
        liq = estimate_liquidation(100.0, 0.01, 35, "LONG")
        assert abs(liq["margin_fraction_of_balance"] - 1.0 / 35.0) < 1e-12


# ═════════════════════════════════════════════════════════════════════════════
# Costs
# ═════════════════════════════════════════════════════════════════════════════


class TestCostModel:
    def test_net_r_below_gross_r(self):
        net, cost = compute_net_r(1.714286, 100.0, 0.01, 12.0)
        assert cost > 0
        assert abs(net + cost - 1.714286) < 1e-9

    def test_wider_stop_lower_cost_in_r(self):
        _, cost_wide = compute_net_r(1.0, 100.0, 0.03, 12.0)
        _, cost_tight = compute_net_r(1.0, 100.0, 0.005, 12.0)
        assert cost_tight > cost_wide


# ═════════════════════════════════════════════════════════════════════════════
# TP configuration PHASE_I_OB_60TP_35SL
# ═════════════════════════════════════════════════════════════════════════════


class TestTPConfiguration:
    def test_reward_multiple_is_exactly_60_over_35(self):
        assert PHASE_I_TP_RR_CONFIG.reward_multiple == Decimal(60) / Decimal(35)

    def test_setup_record_tp_math_long_and_short(self):
        s_long = _mk_setup(direction="LONG", entry=200.0, sl=198.0)
        assert s_long.tp_price == pytest.approx(200.0 + 2.0 * (60.0 / 35.0), abs=1e-6)
        s_short = _mk_setup(direction="SHORT", entry=200.0, sl=202.0)
        assert s_short.tp_price == pytest.approx(200.0 - 2.0 * (60.0 / 35.0), abs=1e-6)


# ═════════════════════════════════════════════════════════════════════════════
# Candle-by-candle replay & intrabar policy
# ═════════════════════════════════════════════════════════════════════════════


class TestTradeReplay:
    def _records_for(self, candles, setup):
        preds = {setup.setup_id: (0.9, 1.0, 0.5)}
        return replay_phase_i_trades(candles, [setup], preds, ai_threshold=0.50)

    def test_tp_hit_long(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        candles = _mk_candles([(100, 100.2, 99.8, 100.0), (100, 103.0, 99.9, 102.5)])
        recs = self._records_for(candles, setup)
        assert len(recs) == 1
        r = recs[0]
        assert r.outcome.exit_reason == "TP_HIT"
        assert r.gross_r == pytest.approx(60.0 / 35.0, abs=1e-6)

    def test_sl_hit_long_realizes_minus_one(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        candles = _mk_candles([(100, 100.1, 99.4, 99.5), (99.5, 99.7, 98.0, 98.5)])
        r = self._records_for(candles, setup)[0]
        assert r.outcome.exit_reason == "SL_HIT"
        assert r.gross_r == pytest.approx(-1.0, abs=1e-9)

    def test_same_candle_tp_and_sl_resolves_sl_first(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        # Next candle spans both barriers; OHLC cannot establish order -> SL FIRST.
        candles = _mk_candles([(100, 100.2, 99.8, 100.0), (100, 102.5, 98.5, 101.0), (101, 101.2, 100.8, 101.0)])
        r = self._records_for(candles, setup)[0]
        assert r.outcome.exit_reason == "SL_HIT"
        assert r.gross_r == pytest.approx(-1.0, abs=1e-9)

    def test_timeout_exit_mark_to_market(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        n = REPLAY_HORIZON_BARS + 5
        candles = _mk_candles([(100, 100.1, 99.9, 100.05)] * (n + 1))
        r = self._records_for(candles, setup)[0]
        assert r.outcome.exit_reason == "TIMEOUT_EXIT"
        expected = (candles[n]["close"] if isinstance(candles[n], dict) else float(candles[n].close)) - 100.0
        assert r.gross_r == pytest.approx(expected / 1.0, abs=1e-6)
        assert r.outcome.holding_bars <= REPLAY_HORIZON_BARS

    def test_end_of_data_exit_at_final_close(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=90.0)
        candles = _mk_candles([(100, 100.2, 99.8, 100.0), (100, 101.5, 99.9, 101.0)])
        r = self._records_for(candles, setup)[0]
        assert r.outcome.exit_reason == "TIMEOUT_EXIT"
        assert r.gross_r == pytest.approx(1.0 / 10.0, abs=1e-6)

    def test_short_tp_hit(self):
        setup = _mk_setup(direction="SHORT", decision_bar=0, entry=100.0, sl=101.0)
        candles = _mk_candles([(100, 100.2, 99.8, 100.0), (100, 100.1, 97.5, 98.0)])
        r = self._records_for(candles, setup)[0]
        assert r.outcome.exit_reason == "TP_HIT"
        assert r.gross_r == pytest.approx(60.0 / 35.0, abs=1e-6)

    def test_no_lookahead_entry_bar_excluded_from_barrier_checks(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        # Decision-bar candle itself spans both barriers; must NOT trigger anything.
        candles = [(100.0, 104.0, 95.0, 100.0)] + [(100.05, 100.1, 99.95, 100.05)] * 80
        r = self._records_for(_mk_candles(candles), setup)[0]
        assert r.outcome.exit_reason == "TIMEOUT_EXIT"


class TestAIDecisionTiming:
    def test_ai_decision_precedes_outcome_information(self):
        setup = _mk_setup(direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
        candles_tp = _mk_candles([(100, 100.1, 99.9, 100.0), (100, 103.5, 99.9, 103.0)])
        preds_accept = {setup.setup_id: (0.9, 1.0, 0.5)}
        preds_reject = {setup.setup_id: (0.1, 1.0, 0.5)}

        rec_a = replay_phase_i_trades(candles_tp, [setup], preds_accept, 0.50)[0]
        rec_b = replay_phase_i_trades(candles_tp, [setup], preds_reject, 0.50)[0]

        assert rec_a.ai_decision == "ACCEPT"
        assert rec_b.ai_decision == "REJECT"
        # Identical market outcome regardless of AI decision -> decision uses no outcome info.
        assert rec_a.gross_r == rec_b.gross_r
        assert rec_a.outcome.exit_reason == rec_b.outcome.exit_reason == "TP_HIT"

    def test_threshold_boundary_accepts_at_exact_threshold(self):
        setup = _mk_setup(decision_bar=0)
        candles = _mk_candles([(100, 100.1, 99.9, 100.0)] * 5)
        at_thr = replay_phase_i_trades(candles, [setup], {setup.setup_id: (0.50, 0, 0)}, 0.50)[0]
        below = replay_phase_i_trades(candles, [setup], {setup.setup_id: (0.4999, 0, 0)}, 0.50)[0]
        assert at_thr.ai_decision == "ACCEPT"
        assert below.ai_decision == "REJECT"


# ═════════════════════════════════════════════════════════════════════════════
# Real-data OB extraction, entry/SL rules, determinism, leakage control
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def btc_candles() -> list[Candle]:
    csv_path = (
        _get_repo_root() / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
    )
    return load_canonical_candles(csv_path.parents[2], "BTCUSD")


@pytest.fixture(scope="module")
def extracted(btc_candles):
    setups, audit = extract_phase_i_setups(btc_candles, "BTCUSD")
    return setups, audit


class TestOBExtractionRealData:
    def test_extracts_setups_from_real_canonical_data(self, extracted):
        setups, audit = extracted
        assert audit["candles"] >= 5000
        assert audit["unique_setups"] > 100
        assert audit["duplicate_decisions_skipped"] > 0  # one-trade-per-OB enforced
        assert len(setups) == audit["unique_setups"]

    def test_second_edge_sl_rule(self, extracted):
        setups, _ = extracted
        assert setups, "expected non-empty setup set"
        for s in setups:
            if s.direction == "LONG":
                assert s.sl_price == pytest.approx(s.ob_low, abs=1e-6)
                assert s.entry_price > s.sl_price
            else:
                assert s.sl_price == pytest.approx(s.ob_high, abs=1e-6)
                assert s.entry_price < s.sl_price

    def test_ob_geometry_sane(self, extracted):
        for s in extracted[0][:200]:
            assert s.ob_high > s.ob_low > 0
            assert s.risk_distance > 0
            assert 0 < s.stop_distance_percent < 100
            assert len(s.features_24) == 24

    def test_creation_precedes_confirmation_precedes_decision(self, extracted):
        for s in extracted[0][:200]:
            assert s.creation_time <= s.confirmation_time <= s.decision_time

    def test_deterministic_extraction(self, btc_candles, extracted):
        setups_again, audit_again = extract_phase_i_setups(btc_candles, "BTCUSD")
        assert setups_again == extracted[0]
        assert audit_again == extracted[1]

    def test_no_future_feature_leakage(self, btc_candles):
        cut = WARMUP_BARS + 120
        candles_a = btc_candles[: cut + 40]
        candles_b = copy.deepcopy(candles_a)
        # Mutate every candle AFTER the evaluation bar massively.
        for k in range(cut + 1, len(candles_b)):
            c = candles_b[k]
            candles_b[k] = Candle(
                symbol=c.symbol, timeframe=c.timeframe, timestamp=c.timestamp,
                open=c.open * Decimal("3"), high=c.high * Decimal("4"), low=c.low / Decimal("4"), close=c.close * Decimal("2.5"),
                volume=c.volume * Decimal("50"), source=c.source,
            )
        ctx_a = build_smc_context(candles_a)
        ctx_b = build_smc_context(candles_b)
        sa, _ = extract_phase_i_setups(candles_a, "BTCUSD", ctx=ctx_a)
        sb, _ = extract_phase_i_setups(candles_b, "BTCUSD", ctx=ctx_b)
        fa = [s for s in sa if s.decision_bar == cut]
        fb = [s for s in sb if s.decision_bar == cut]
        if fa and fb:
            assert fa[0].features_24 == fb[0].features_24
            assert fa[0].entry_price == fb[0].entry_price
            assert fa[0].sl_price == fb[0].sl_price


# ═════════════════════════════════════════════════════════════════════════════
# Group accounting (SMC vs SMC+AI vs rejected)
# ═════════════════════════════════════════════════════════════════════════════


class TestGroupAccounting:
    def _synthetic_records(self, n=20):
        """Alternating TP-hit / SL-hit trades with deterministic predictions."""
        records = []
        up = _mk_candles([(100.0, 100.1, 99.9, 100.0), (100.0, 103.5, 99.9, 103.0)])
        down = _mk_candles([(100.0, 100.1, 99.9, 100.0), (100.0, 100.1, 97.5, 98.0)])
        for i in range(n):
            setup = _mk_setup(setup_id=f"T{i}", direction="LONG", decision_bar=0, entry=100.0, sl=99.0)
            pred = 0.9 if i % 2 == 0 else 0.1
            candles = up if i % 2 == 0 else down
            recs = replay_phase_i_trades(candles, [setup], {setup.setup_id: (pred, 1.0, 0.5)}, 0.50)
            assert recs[0].gross_r > 0 if i % 2 == 0 else recs[0].gross_r < 0
            records.append(recs[0])
        return records

    def test_groups_partition_full_set(self):
        from quantedge.ai.evaluation.run_phase_i import split_groups

        records = self._synthetic_records()
        a, b, c = split_groups(records)
        assert len(a) == len(records)
        assert len(a) == len(b) + len(c)
        assert set(t.setup.setup_id for t in b).isdisjoint(set(t.setup.setup_id for t in c))

    def test_rejected_trade_outcomes_preserved(self):
        from quantedge.ai.evaluation.run_phase_i import split_groups

        records = self._synthetic_records()
        a, b, c = split_groups(records)
        by_id = {t.setup.setup_id: t.gross_r for t in a}
        for t in c:
            assert by_id[t.setup.setup_id] == t.gross_r  # outcome computed though untraded

    def test_extended_metrics_consistency(self):
        records = self._synthetic_records(12)
        em = compute_extended_metrics(records)
        assert em.base.executed_setups == 12
        assert em.base.win_count == 6
        assert em.base.loss_count == 6
        assert em.best_trade_r == pytest.approx(60.0 / 35.0, abs=1e-4)
        assert em.worst_trade_r == pytest.approx(-1.0, abs=1e-9)
        assert em.max_consecutive_losses >= 1


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap determinism
# ═════════════════════════════════════════════════════════════════════════════


class TestBootstrap:
    def test_block_size_rule(self):
        assert mbb_block_size(300) == max(3, int(np.ceil(300 ** (1 / 3))))
        assert mbb_block_size(10) >= 3

    def test_bootstrap_deterministic_and_contains_point_estimate(self):
        rs = np.array([-1.0, -1.0, 1.71, -1.0, 1.71, 0.2, -0.4, 1.71, -1.0, 0.5] * 6)
        mask = np.array([i % 4 == 0 for i in range(len(rs))])
        out1 = moving_block_bootstrap_groups(rs, mask, n_boot=300, seed=42)
        out2 = moving_block_bootstrap_groups(rs, mask, n_boot=300, seed=42)
        assert out1 == out2
        lo, hi = out1["incremental_mean_r_95ci"]
        point = float(np.mean(rs[mask]) - np.mean(rs))
        assert lo - 0.75 <= point <= hi + 0.75


# ═════════════════════════════════════════════════════════════════════════════
# Score buckets
# ═════════════════════════════════════════════════════════════════════════════


class TestScoreBuckets:
    def test_bucket_assignment_counts(self):
        class FakeRec:
            pass

        recs = []
        for pr in [-0.5, 0.1, 0.3, 0.6, 1.5]:
            r = FakeRec()
            r.predicted_r = pr
            r.gross_r = pr * 2
            r.outcome = type("O", (), {"mfe_r": 1.0, "mae_r": 0.5})
            recs.append(r)
        out = compute_score_buckets(recs)  # type: ignore[arg-type]
        assert out["buckets"]["< 0R"]["count"] == 1
        total = sum(b["count"] for b in out["buckets"].values())
        assert total == 5


# ═════════════════════════════════════════════════════════════════════════════
# Governance lock
# ═════════════════════════════════════════════════════════════════════════════


class TestGovernanceLock:
    def _metrics_stub(self, exp, pf, mdd, coverage=50.0):
        base = {
            "expectancy_r": exp, "profit_factor": pf, "max_drawdown_r": mdd,
            "coverage_pct": coverage,
        }
        return type("EM", (), {"base": type("B", (), base)})

    def test_failing_criteria_reject_and_lock(self):
        gate = evaluate_phase_i_gate(
            oos_smc=self._metrics_stub(0.10, 1.20, 5.0),
            oos_ai=self._metrics_stub(0.05, 1.10, 6.0),
            incremental_ci_low=-0.2,
            per_asset_incremental={"BTCUSD": -1.0},
            rejected_expectancy=0.5,
            accepted_expectancy=0.05,
            liquidation_violations=1,
        )
        assert gate["status"] == "REJECTED"
        assert gate["live_execution_authorized"] is False
        assert "BLOCKED_BY_SYSTEM" in gate["ai_live_execution"]
        assert gate["execution_authority"] == "DETERMINISTIC_SMC"

    def test_coverage_measured_against_all_smc_setups(self):
        """Regression: C4 coverage must be vs ALL OOS SMC setups, not the AI subset."""
        gate = evaluate_phase_i_gate(
            oos_smc=self._metrics_stub(0.10, 1.20, 5.0),
            oos_ai=self._metrics_stub(0.30, 1.60, 4.0, coverage=100.0),  # subset self-coverage
            incremental_ci_low=0.05,
            per_asset_incremental={"BTCUSD": 0.2, "ETHUSD": 0.1, "SOLUSD": 0.05, "XRPUSD": -0.1},
            rejected_expectancy=-0.5,
            accepted_expectancy=0.30,
            liquidation_violations=0,
            ai_coverage_pct=4.04,  # 4 accepted / 99 total
        )
        c4 = gate["criteria"]["C4_minimum_ai_coverage"]
        assert c4["passed"] is False
        assert "4.04" in c4["detail"]

    def test_passing_gate_never_authorises_live(self):
        gate = evaluate_phase_i_gate(
            oos_smc=self._metrics_stub(-0.10, 0.80, 10.0),
            oos_ai=self._metrics_stub(0.30, 1.60, 4.0),
            incremental_ci_low=0.05,
            per_asset_incremental={"BTCUSD": 0.2, "ETHUSD": 0.1, "SOLUSD": 0.05, "XRPUSD": -0.1},
            rejected_expectancy=-0.5,
            accepted_expectancy=0.30,
            liquidation_violations=0,
        )
        assert gate["status"] == "CANDIDATE_FOR_GOVERNANCE_REVIEW"
        assert gate["live_execution_authorized"] is False


# ═════════════════════════════════════════════════════════════════════════════
# ONNX model smoke test (skips if artifact missing)
# ═════════════════════════════════════════════════════════════════════════════


class TestFrozenModelSmoke:
    def test_onnx_predict_shape(self):
        onnx_path = (
            _get_repo_root() / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
        )
        if not onnx_path.exists():
            pytest.skip("ONNX artifact not present")
        import onnxruntime as ort

        session = ort.InferenceSession(str(onnx_path))
        inp = np.zeros((1, 24), dtype=np.float32)
        out = session.run([session.get_outputs()[0].name], {session.get_inputs()[0].name: inp})[0]
        assert out.shape == (1, 3)
