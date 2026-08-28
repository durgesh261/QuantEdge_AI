"""
test_manual_smc_portfolio_sizing.py
===================================
Phase 1 Step 3 acceptance gate for `manual_smc/portfolio.py` (the global
one-trade lock) and `manual_smc/sizing.py` (capital mathematics).

Mandated coverage → class map
    lock acquisition / rejection / release ... TestPortfolioLockAcquisition,
                                              TestPortfolioLockRelease
    later-candle rejection .................. TestPortfolioLockLaterCandle
    re-entry after close .................... TestPortfolioLockReentry
    sizing at normal leverage ............... TestSizingNormalLeverage
    100x clamp .............................. TestLeverageClamp
    very tight / very large SL distances .... TestLeverageBoundaries
    fee / PnL arithmetic .................... TestFeeAndPnLArithmetic
    boundary + zero/near-zero SL distance ... TestDegenerateRiskProtection
    unknown contract_value cannot become a
    live sizing assumption .................. TestContractValueCannotBeGuessed

Plus two non-mandated but load-bearing checks: the lock's decisions are
byte-identical to `ManualSMCLifecycle._entry_blocked` (so a later phase can
substitute one for the other), and the sizing arithmetic is proven equal to
the frozen oracle's own recorded capital fields on real canonical candles.
"""

import sys
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quantedge.strategy.manual_smc.geometry import _make_manual_ob  # noqa: E402
from quantedge.strategy.manual_smc.lifecycle import (  # noqa: E402
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
    ManualSMCLifecycle,
)
from quantedge.strategy.manual_smc.models import ManualSpecConfig  # noqa: E402
from quantedge.strategy.manual_smc.portfolio import (  # noqa: E402
    OUTCOME_RECONCILED_CLOSED,
    TERMINAL_OUTCOMES,
    LockHolder,
    LockRejection,
    LockRejectionCode,
    PortfolioLock,
    PortfolioLockUnavailableError,
    PortfolioLockViolationError,
)

from quantedge.strategy.manual_smc.sizing import (  # noqa: E402
    EPS,
    MANUAL_SMC_SYMBOLS,
    UNVERIFIED,
    ContractSpec,
    ContractSpecRegistry,
    ContractValueUnverifiedError,
    DegenerateRiskError,
    PositionSizing,
    QuantitySemanticsUnverifiedError,
    SizingError,
    TradeSettlement,
    UnknownSymbolError,
    assert_executable,
    compute_leverage,
    compute_sl_dist_pct,
    realized_r_for_outcome,
    resolve_order_quantity,
    return_pct_for_outcome,
    settle_trade,
    size_position,
)

CFG = ManualSpecConfig()
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(hours: int) -> datetime:
    return BASE + timedelta(hours=hours)


def _ob(direction="SHORT", ob_top=105.0, ob_bottom=99.0, asset="AAAUSD",
        cfg=CFG):
    """Build a real OB through the frozen geometry — never hand-rolled."""
    return _make_manual_ob(
        asset=asset, bos_bar_idx=1, bos_dt=_ts(1),
        origin_bar_idx=0, origin_dt=_ts(0),
        direction=direction, ob_top=ob_top, ob_bottom=ob_bottom, cfg=cfg)


def _ob_with_sl_dist(target_pct: float, entry_hint: float = 100.0):
    """
    A SHORT OB whose sl_dist_pct is (approximately) `target_pct`.

    Solved from the geometry rather than asserted: entry = bottom + 0.25*width
    and sl = top, so sl_dist = 0.75*width. Choosing bottom = hint - 0.25*width
    makes entry exactly `entry_hint`, hence width = target*hint/75.
    """
    width = target_pct * entry_hint / 75.0
    bottom = entry_hint - 0.25 * width
    return _ob(direction="SHORT", ob_top=bottom + width, ob_bottom=bottom)

class TestPortfolioLockAcquisition:
    """Acquisition and rejection. `active_trade is not None` IS the rule."""

    def test_fresh_lock_is_free(self):
        lock = PortfolioLock("ACC1")
        assert lock.active_trade is None
        assert lock.is_held() is False
        assert lock.last_closed_dt is None
        assert lock.evaluate(_ts(0)) is None

    def test_acquire_grants_and_records_holder(self):
        lock = PortfolioLock("ACC1")
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        assert isinstance(holder, LockHolder)
        assert holder.granted is True
        assert lock.is_held() is True
        assert lock.active_trade is holder
        assert holder.account_id == "ACC1"
        assert holder.asset == "BTCUSD"
        assert holder.ob_id == "OB1"
        assert holder.direction == "SHORT"
        assert holder.acquired_at == _ts(4)
        assert holder.acquired_bar_idx == 4

    def test_second_acquisition_same_candle_is_rejected(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        decision = lock.try_acquire("ETHUSD", "OB2", "LONG", _ts(4), 4)
        assert isinstance(decision, LockRejection)
        assert decision.granted is False
        assert decision.code is LockRejectionCode.ACTIVE_TRADE_OPEN
        assert decision.held_by is lock.active_trade

    def test_raising_variant_raises_and_carries_the_rejection(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        with pytest.raises(PortfolioLockUnavailableError) as exc:
            lock.acquire("ETHUSD", "OB2", "LONG", _ts(6), 6)
        assert exc.value.rejection.code is LockRejectionCode.ACTIVE_TRADE_OPEN
        assert "BTCUSD" in str(exc.value)

    def test_rejection_does_not_disturb_the_incumbent(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        for bar in range(5, 12):
            lock.try_acquire("ETHUSD", f"OB{bar}", "LONG", _ts(bar), bar)
        assert lock.active_trade is holder      # never overwritten

    def test_evaluate_is_pure(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        before = len(lock.events)
        assert lock.evaluate(_ts(9)) is not None
        assert len(lock.events) == before       # no audit entry, no mutation

    def test_tokens_are_unique_per_acquisition(self):
        lock = PortfolioLock("ACC7")
        seen = []
        for bar in range(0, 9, 2):
            holder = lock.acquire("BTCUSD", f"OB{bar}", "SHORT", _ts(bar), bar)
            seen.append(holder.token)
            lock.release(holder.token, _ts(bar + 1), OUTCOME_TP)
        assert len(set(seen)) == len(seen)
        assert all(t.startswith("ACC7#") for t in seen)

    def test_audit_trail_records_grant_and_refusal(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.try_acquire("ETHUSD", "OB2", "LONG", _ts(6), 6)
        assert [e.event for e in lock.events] == ["ACQUIRED", "REJECTED"]
        assert lock.events[1].ob_id == "OB2"
        assert "ACTIVE_TRADE_OPEN" in lock.events[1].detail

class TestPortfolioLockLaterCandle:
    """
    The defect this phase exists to prevent. The oracle's watermark only
    refused the SAME timestamp, so a later candle silently overwrote the
    active trade. Here every later candle is refused.
    """

    def test_every_later_candle_is_refused(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        for bar in range(5, 200):
            decision = lock.try_acquire("ETHUSD", "OB2", "LONG", _ts(bar), bar)
            assert isinstance(decision, LockRejection)
            assert decision.code is LockRejectionCode.ACTIVE_TRADE_OPEN
        assert lock.active_trade is holder

    def test_refusal_is_independent_of_asset_and_direction(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        for asset in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"):
            for direction in ("SHORT", "LONG"):
                decision = lock.try_acquire(asset, "OBX", direction, _ts(9), 9)
                assert decision.code is LockRejectionCode.ACTIVE_TRADE_OPEN

    def test_never_two_holders_over_a_long_interleaved_run(self):
        lock = PortfolioLock()
        holders = []
        for bar in range(0, 60):
            decision = lock.try_acquire("BTCUSD", f"OB{bar}", "SHORT",
                                        _ts(bar), bar)
            if isinstance(decision, LockHolder):
                holders.append(decision)
            # deliberately never released
            assert (lock.active_trade is not None) == (len(holders) >= 1)
        assert len(holders) == 1

    def test_rejection_detail_names_the_incumbent_and_its_fill_time(self):
        lock = PortfolioLock()
        lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        decision = lock.try_acquire("SOLUSD", "OB2", "LONG", _ts(30), 30)
        assert "active trade already open on BTCUSD" in decision.detail
        assert _ts(4).isoformat() in decision.detail


class TestPortfolioLockRelease:
    """Safety rule #14: the slot frees only on proof of an actual close."""

    def test_release_with_matching_token_frees_the_slot(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        assert lock.is_held() is False
        assert lock.active_trade is None
        assert lock.last_closed_dt == _ts(8)
        assert lock.events[-1].event == "RELEASED"

    def test_release_without_a_holder_raises(self):
        lock = PortfolioLock()
        with pytest.raises(PortfolioLockViolationError,
                           match="no trade is active"):
            lock.release("ACC#1", _ts(8), OUTCOME_TP)

    def test_foreign_or_stale_token_cannot_release(self):
        lock = PortfolioLock()
        first = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(first.token, _ts(5), OUTCOME_SL)
        second = lock.acquire("ETHUSD", "OB2", "LONG", _ts(6), 6)
        with pytest.raises(PortfolioLockViolationError, match="token mismatch"):
            lock.release(first.token, _ts(9), OUTCOME_TP)     # stale
        with pytest.raises(PortfolioLockViolationError, match="token mismatch"):
            lock.release("somebody-elses-token", _ts(9), OUTCOME_TP)
        assert lock.active_trade is second                    # still held

    @pytest.mark.parametrize("outcome", sorted(TERMINAL_OUTCOMES))
    def test_every_terminal_outcome_is_accepted(self, outcome):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), outcome)
        assert lock.is_held() is False

    @pytest.mark.parametrize("outcome", [
        "", "OPEN", "PARTIAL", "PENDING", "SUBMITTED", "CANCELLED",
        "FILLED", "filled_tp", None,
    ])
    def test_non_terminal_outcome_cannot_release(self, outcome):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        with pytest.raises(PortfolioLockViolationError, match="not terminal"):
            lock.release(holder.token, _ts(8), outcome)
        assert lock.active_trade is holder

    def test_terminal_set_is_exactly_the_documented_four(self):
        assert TERMINAL_OUTCOMES == frozenset({
            OUTCOME_TP, OUTCOME_SL, OUTCOME_TIMEOUT, OUTCOME_RECONCILED_CLOSED})
        assert OUTCOME_RECONCILED_CLOSED == "CLOSED_RECONCILED"

    def test_double_release_is_refused(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        with pytest.raises(PortfolioLockViolationError):
            lock.release(holder.token, _ts(8), OUTCOME_TP)

    def test_reset_clears_holder_watermark_and_audit(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        lock.reset()
        assert lock.active_trade is None
        assert lock.last_closed_dt is None
        assert lock.events == ()
        assert lock.evaluate(_ts(0)) is None


class TestPortfolioLockReentry:
    """A closed trade must not permanently block the account."""

    def test_same_candle_reentry_is_refused_by_the_watermark(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        decision = lock.try_acquire("ETHUSD", "OB2", "LONG", _ts(8), 8)
        assert decision.code is LockRejectionCode.INTRA_CANDLE_AMBIGUITY
        assert decision.held_by is None

    def test_earlier_timestamp_than_the_close_is_also_refused(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        decision = lock.try_acquire("ETHUSD", "OB2", "LONG", _ts(7), 7)
        assert decision.code is LockRejectionCode.INTRA_CANDLE_AMBIGUITY

    def test_later_candle_reentry_is_granted(self):
        lock = PortfolioLock()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        second = lock.acquire("ETHUSD", "OB2", "LONG", _ts(9), 9)
        assert lock.active_trade is second
        assert second.token != holder.token

    def test_serial_trades_never_overlap(self):
        lock = PortfolioLock()
        opened, closed = [], []
        bar = 0
        for _ in range(6):
            holder = lock.acquire("BTCUSD", f"OB{bar}", "SHORT", _ts(bar), bar)
            opened.append(holder.acquired_at)
            assert lock.is_held() is True
            bar += 3
            lock.release(holder.token, _ts(bar), OUTCOME_SL)
            closed.append(_ts(bar))
            assert lock.is_held() is False
            bar += 1
        for i in range(1, len(opened)):
            assert opened[i] > closed[i - 1]      # strictly serial

    def test_reentry_is_blocked_again_once_the_new_trade_is_open(self):
        lock = PortfolioLock()
        first = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(first.token, _ts(8), OUTCOME_TP)
        lock.acquire("ETHUSD", "OB2", "LONG", _ts(9), 9)
        decision = lock.try_acquire("SOLUSD", "OB3", "SHORT", _ts(10), 10)
        assert decision.code is LockRejectionCode.ACTIVE_TRADE_OPEN


class TestPortfolioLockMatchesLifecycleGate:
    """
    The lock must decide exactly what `lifecycle._entry_blocked` decides,
    including the wording, so a later phase can swap one for the other without
    changing behaviour or logs.
    """

    def _paired(self):
        lock = PortfolioLock()
        lc = ManualSMCLifecycle(assets=["BTCUSD"])
        return lock, lc

    def test_free_state_agrees(self):
        lock, lc = self._paired()
        assert lock.evaluate(_ts(3)) is None
        assert lc._entry_blocked(_ts(3)) is None

    def test_active_trade_detail_strings_are_identical(self):
        from quantedge.strategy.manual_smc.lifecycle import ManualActiveTrade
        lock, lc = self._paired()
        ob = _ob(asset="BTCUSD")
        lock.acquire("BTCUSD", ob.ob_id, "SHORT", _ts(4), 4)
        lc.active_trade = ManualActiveTrade(
            ob=ob, asset="BTCUSD", direction="SHORT",
            entry_price=ob.entry_price, sl_price=ob.sl_price,
            tp_price=ob.tp_price, risk_dist=1.0, reward_dist=1.0,
            applied_leverage=ob.applied_leverage,
            theoretical_leverage=ob.theoretical_leverage,
            fill_dt=_ts(4), retest_dt=_ts(4), fill_bar_idx=4)
        assert lock.evaluate(_ts(9)).detail == lc._entry_blocked(_ts(9))

    def test_watermark_detail_strings_are_identical(self):
        lock, lc = self._paired()
        holder = lock.acquire("BTCUSD", "OB1", "SHORT", _ts(4), 4)
        lock.release(holder.token, _ts(8), OUTCOME_TP)
        lc._last_trade_closed_dt = _ts(8)
        assert lock.evaluate(_ts(8)).detail == lc._entry_blocked(_ts(8))
        assert lock.evaluate(_ts(9)) is None
        assert lc._entry_blocked(_ts(9)) is None

class TestSizingNormalLeverage:
    """A well-behaved setup: 4.4776% SL distance, 7.82x, no clamping."""

    def test_geometry_feeds_sizing_unchanged(self):
        ob = _ob()
        sizing = size_position(ob, 10.0, CFG)
        assert sizing.entry_price == ob.entry_price == 100.5
        assert sizing.sl_price == ob.sl_price == 105.0
        assert sizing.tp_price == ob.tp_price
        assert sizing.risk_dist == 4.5
        assert sizing.sl_dist_pct == ob.sl_dist_pct
        assert sizing.applied_leverage == ob.applied_leverage
        assert sizing.theoretical_leverage == ob.theoretical_leverage

    def test_leverage_matches_the_specified_formula(self):
        ob = _ob()
        assert ob.sl_dist_pct == pytest.approx(4.477611940298507, abs=1e-12)
        assert ob.theoretical_leverage == pytest.approx(
            35.0 / ob.sl_dist_pct, abs=1e-12)
        assert ob.applied_leverage == ob.theoretical_leverage < 100.0

    def test_notional_margin_and_fee(self):
        sizing = size_position(_ob(), 10.0, CFG)
        assert sizing.margin_usd == 10.0            # whole balance is margin
        assert sizing.account_balance == 10.0
        assert sizing.notional_usd == 10.0 * sizing.applied_leverage
        assert sizing.fee_usd == sizing.notional_usd * 0.0008
        assert sizing.notional_usd == pytest.approx(78.16666666666667)
        assert sizing.fee_usd == pytest.approx(0.06253333333333334)

    def test_gross_return_legs(self):
        sizing = size_position(_ob(), 10.0, CFG)
        assert sizing.gross_sl_return_pct == pytest.approx(35.0, abs=1e-12)
        assert sizing.gross_tp_return_pct == pytest.approx(
            0.60 * sizing.applied_leverage)
        assert sizing.leverage_clamped is False
        assert sizing.degenerate_sl_distance is False

    def test_unclamped_leverage_always_risks_exactly_35_percent(self):
        """The whole point of `35 / sl_dist_pct`: risk at SL is the budget."""
        for target in (0.5, 1.0, 2.5, 4.0, 12.0, 30.0):
            sizing = size_position(_ob_with_sl_dist(target), 10.0, CFG)
            if sizing.leverage_clamped:
                continue
            assert sizing.gross_sl_return_pct == pytest.approx(35.0, abs=1e-9)

    def test_direction_asymmetry_is_a_property_of_the_entry_level(self):
        """
        Same OB box, but SHORT enters at bottom+25% and LONG at top-25%, so the
        two entries differ and equal `risk_dist` gives DIFFERENT sl_dist_pct
        and leverage. The 35% risk budget still binds in both directions.
        """
        short = size_position(_ob("SHORT", 105.0, 99.0), 10.0, CFG)
        long_ = size_position(_ob("LONG", 105.0, 99.0), 10.0, CFG)
        assert short.risk_dist == long_.risk_dist == 4.5
        assert short.entry_price == 100.5
        assert long_.entry_price == 103.5
        assert short.sl_dist_pct > long_.sl_dist_pct       # smaller denominator
        assert short.applied_leverage < long_.applied_leverage
        assert short.gross_sl_return_pct == pytest.approx(35.0, abs=1e-9)
        assert long_.gross_sl_return_pct == pytest.approx(35.0, abs=1e-9)

    def test_balance_scales_notional_linearly(self):
        a = size_position(_ob(), 10.0, CFG)
        b = size_position(_ob(), 1000.0, CFG)
        assert b.notional_usd == pytest.approx(a.notional_usd * 100.0)
        assert b.fee_usd == pytest.approx(a.fee_usd * 100.0)
        assert b.applied_leverage == a.applied_leverage

class TestLeverageClamp:
    """`min(100, 35 / sl_dist_pct)` clamps. It must never raise."""

    def test_clamped_at_the_cap(self):
        theo, applied = compute_leverage(0.2, CFG)
        assert theo == pytest.approx(175.0)
        assert applied == 100.0

    def test_clamp_boundary_is_exactly_035_percent(self):
        theo, applied = compute_leverage(0.35, CFG)
        assert theo == 100.0
        assert applied == 100.0
        sizing = size_position(_ob_with_sl_dist(0.35), 10.0, CFG)
        assert sizing.leverage_clamped is False      # equal is not exceeding

    def test_just_inside_the_boundary_clamps(self):
        theo, applied = compute_leverage(0.3499, CFG)
        assert theo > 100.0
        assert applied == 100.0

    def test_clamping_never_raises_across_a_wide_sweep(self):
        for i in range(1, 2000):
            sl_dist_pct = i / 100.0
            theo, applied = compute_leverage(sl_dist_pct, CFG)
            assert applied == min(100.0, theo)
            assert applied <= 100.0

    def test_clamped_sizing_flags_and_reduces_risk_below_35(self):
        sizing = size_position(_ob_with_sl_dist(0.2), 10.0, CFG)
        assert sizing.leverage_clamped is True
        assert sizing.applied_leverage == 100.0
        assert sizing.gross_sl_return_pct < 35.0
        assert sizing.gross_sl_return_pct == pytest.approx(
            100.0 * sizing.sl_dist_pct)

    def test_cap_is_configurable_without_touching_the_formula(self):
        cfg = ManualSpecConfig(applied_leverage_cap=25.0)
        theo, applied = compute_leverage(0.2, cfg)
        assert theo == pytest.approx(175.0)
        assert applied == 25.0


class TestLeverageBoundaries:
    """Very tight and very large SL distances."""

    def test_extremely_tight_sl_clamps_rather_than_exploding(self):
        theo, applied = compute_leverage(0.001, CFG)
        assert theo == pytest.approx(35000.0)
        assert applied == 100.0

    def test_pathologically_tight_sl_still_clamps(self):
        theo, applied = compute_leverage(1e-8, CFG)
        assert theo > 1e9
        assert applied == 100.0

    def test_large_sl_distance_yields_sub_1x_leverage(self):
        theo, applied = compute_leverage(70.0, CFG)
        assert theo == pytest.approx(0.5)
        assert applied == pytest.approx(0.5)        # deleveraged, not floored

    def test_sl_distance_of_35_percent_is_exactly_1x(self):
        theo, applied = compute_leverage(35.0, CFG)
        assert theo == 1.0
        assert applied == 1.0

    def test_huge_sl_distance_shrinks_notional_below_balance(self):
        sizing = size_position(_ob_with_sl_dist(70.0), 10.0, CFG)
        assert sizing.applied_leverage < 1.0
        assert sizing.notional_usd < sizing.account_balance
        assert sizing.gross_sl_return_pct == pytest.approx(35.0, abs=1e-9)

class TestDegenerateRiskProtection:
    """Zero and near-zero SL distance. Oracle fallbacks preserved, then flagged."""

    def test_zero_sl_distance_falls_back_to_1x_without_raising(self):
        theo, applied = compute_leverage(0.0, CFG)
        assert theo == 1.0
        assert applied == 1.0

    def test_below_epsilon_uses_the_fallback_and_above_it_does_not(self):
        assert compute_leverage(EPS, CFG) == (1.0, 1.0)          # not > EPS
        theo, applied = compute_leverage(EPS * 10, CFG)
        assert theo > 1.0
        assert applied == 100.0

    def test_non_positive_entry_price_yields_zero_sl_dist_pct(self):
        assert compute_sl_dist_pct(0.0, 5.0) == 0.0
        assert compute_sl_dist_pct(-10.0, 5.0) == 0.0
        assert compute_sl_dist_pct(EPS, 5.0) == 0.0
        assert compute_sl_dist_pct(100.0, 100.0) == 0.0          # entry == sl

    def test_entry_equal_to_sl_is_flagged_degenerate(self):
        ob = _ob(direction="SHORT", ob_top=100.0, ob_bottom=100.0)
        sizing = size_position(ob, 10.0, CFG)
        assert sizing.risk_dist == 0.0
        assert sizing.sl_dist_pct == 0.0
        assert sizing.degenerate_sl_distance is True
        assert sizing.applied_leverage == 1.0                    # the fallback

    def test_degenerate_setup_is_refused_by_assert_executable(self):
        sizing = size_position(_ob("SHORT", 100.0, 100.0), 10.0, CFG)
        spec = ContractSpec("AAAUSD", 0.001, verification_source="delta-api")
        with pytest.raises(DegenerateRiskError, match="zero or near-zero"):
            assert_executable(sizing, spec)

    def test_degenerate_setup_can_never_produce_a_quantity(self):
        sizing = size_position(_ob("SHORT", 100.0, 100.0), 10.0, CFG)
        spec = ContractSpec("AAAUSD", 0.001, verification_source="delta-api")
        with pytest.raises(DegenerateRiskError):
            resolve_order_quantity(sizing, spec, lambda n, cv: n / cv)

    def test_sizing_itself_does_not_raise_on_degenerate_input(self):
        """Backtest arithmetic must stay oracle-identical: flag, don't throw."""
        sizing = size_position(_ob("SHORT", 100.0, 100.0), 10.0, CFG)
        settlement = settle_trade(sizing, OUTCOME_TP)
        assert settlement.realized_r == 0.0          # risk_dist <= EPS -> 0.0

    def test_zero_risk_timeout_also_returns_zero_r(self):
        sizing = size_position(_ob("SHORT", 100.0, 100.0), 10.0, CFG)
        assert realized_r_for_outcome(OUTCOME_TIMEOUT, sizing, 97.0) == 0.0

class TestFeeAndPnLArithmetic:
    """Fees on notional, PnL on balance, floor at zero, compounding output."""

    def test_take_profit_settlement(self):
        sizing = size_position(_ob(), 10.0, CFG)
        st = settle_trade(sizing, OUTCOME_TP)
        assert isinstance(st, TradeSettlement)
        assert st.realized_r == pytest.approx(
            sizing.reward_dist / sizing.risk_dist)
        assert st.return_pct == sizing.gross_tp_return_pct
        assert st.gross_pnl_usd == pytest.approx(0.469)
        assert st.fee_usd == pytest.approx(0.06253333333333334)
        assert st.net_pnl_usd == pytest.approx(0.4064666666666667)
        assert st.ending_balance == pytest.approx(10.406466666666667)

    def test_stop_loss_settlement(self):
        sizing = size_position(_ob(), 10.0, CFG)
        st = settle_trade(sizing, OUTCOME_SL)
        assert st.realized_r == -1.0
        assert st.return_pct == pytest.approx(-35.0, abs=1e-9)
        assert st.gross_pnl_usd == pytest.approx(-3.5)
        assert st.net_pnl_usd == pytest.approx(-3.5625333333333336)
        assert st.ending_balance == pytest.approx(6.4374666666666664)

    def test_timeout_settlement_uses_the_signed_price_move(self):
        sizing = size_position(_ob(), 10.0, CFG)
        st = settle_trade(sizing, OUTCOME_TIMEOUT, exit_price=100.0)
        assert st.realized_r == pytest.approx(0.5 / 4.5)
        assert st.return_pct == pytest.approx(
            st.realized_r * sizing.gross_sl_return_pct)
        assert st.ending_balance == pytest.approx(10.326355555555555)

    def test_timeout_direction_sign_is_correct(self):
        short = size_position(_ob("SHORT"), 10.0, CFG)
        long_ = size_position(_ob("LONG"), 10.0, CFG)
        # SHORT profits when price falls below entry; LONG when it rises.
        assert realized_r_for_outcome(OUTCOME_TIMEOUT, short, 90.0) > 0
        assert realized_r_for_outcome(OUTCOME_TIMEOUT, short, 110.0) < 0
        assert realized_r_for_outcome(OUTCOME_TIMEOUT, long_, 110.0) > 0
        assert realized_r_for_outcome(OUTCOME_TIMEOUT, long_, 90.0) < 0

    def test_timeout_without_an_exit_price_is_refused(self):
        sizing = size_position(_ob(), 10.0, CFG)
        with pytest.raises(SizingError, match="requires an exit price"):
            settle_trade(sizing, OUTCOME_TIMEOUT)

    def test_unknown_outcome_is_refused(self):
        sizing = size_position(_ob(), 10.0, CFG)
        with pytest.raises(SizingError, match="unknown outcome"):
            settle_trade(sizing, "FILLED_MAYBE")
        with pytest.raises(SizingError, match="unknown outcome"):
            return_pct_for_outcome("NOPE", sizing, 1.0)

    def test_fee_is_charged_once_on_notional_at_the_round_trip_rate(self):
        sizing = size_position(_ob(), 10.0, CFG)
        assert sizing.fee_usd == sizing.notional_usd * CFG.fee_rate
        assert CFG.fee_rate == 0.0008
        for outcome, px in ((OUTCOME_TP, None), (OUTCOME_SL, None),
                            (OUTCOME_TIMEOUT, 100.0)):
            st = settle_trade(sizing, outcome, px)
            assert st.fee_usd == sizing.fee_usd
            assert st.net_pnl_usd == st.gross_pnl_usd - st.fee_usd

    def test_balance_is_floored_at_zero(self):
        """Constructed directly: the strategy's own math cannot reach this."""
        base = size_position(_ob(), 10.0, CFG)
        wiped = PositionSizing(**{
            **{f.name: getattr(base, f.name) for f in fields(base)},
            "gross_sl_return_pct": 5000.0,
        })
        st = settle_trade(wiped, OUTCOME_SL)
        assert st.net_pnl_usd < -10.0
        assert st.ending_balance == 0.0            # never negative

    def test_worst_case_loss_is_bounded_by_the_35pct_budget_plus_fees(self):
        """
        Analytic consequence of `min(100, 35/sl_dist)`: applied*sl_dist <= 35,
        so an SL can never cost more than 35% of the balance plus <=8% fees.
        """
        for target in (0.05, 0.2, 0.35, 1.0, 5.0, 20.0, 60.0):
            sizing = size_position(_ob_with_sl_dist(target), 10.0, CFG)
            st = settle_trade(sizing, OUTCOME_SL)
            assert sizing.gross_sl_return_pct <= 35.0 + 1e-9
            assert st.net_pnl_usd >= -10.0 * 0.43
            assert st.ending_balance > 0.0

    def test_compounding_chain_feeds_the_next_trade(self):
        """
        Every capital term scales linearly with the balance, so a repeated
        identical setup has a CONSTANT per-trade multiplier and the chain is
        exactly geometric. The expected value is derived from that multiplier,
        not hard-coded from an estimate.
        """
        probe = size_position(_ob(), 1.0, CFG)
        mult = 1.0 + probe.gross_tp_return_pct / 100.0 - probe.fee_usd
        assert mult == pytest.approx(1.04064666666666667, abs=1e-15)

        balance = 10.0
        for _ in range(5):
            sizing = size_position(_ob(), balance, CFG)
            st = settle_trade(sizing, OUTCOME_TP)
            assert st.starting_balance == balance
            balance = st.ending_balance
        assert balance == pytest.approx(10.0 * mult ** 5, rel=1e-12)
        assert balance == pytest.approx(12.204401519344014, abs=1e-9)

    def test_starting_capital_default_is_ten_dollars(self):
        assert CFG.starting_capital == 10.0


class TestContractValueCannotBeGuessed:
    """
    Mandated: an unknown contract value must be IMPOSSIBLE to convert
    silently into a live order quantity.
    """

    def test_sizing_result_has_no_quantity_field_at_all(self):
        names = {f.name for f in fields(PositionSizing)}
        assert not [n for n in names if "quantity" in n or n in {"qty", "size"}]
        assert not [n for n in names
                    if "contract" in n or "lot" in n or "contracts" in n]

    def test_default_contract_spec_is_the_unverified_sentinel(self):
        spec = ContractSpec("BTCUSD")
        assert spec.contract_value is UNVERIFIED
        assert spec.is_verified is False
        assert bool(spec.contract_value) is False
        assert repr(spec.contract_value) == "UNVERIFIED"

    def test_sentinel_cannot_be_used_in_arithmetic(self):
        spec = ContractSpec("BTCUSD")
        for op in (lambda v: v * 2, lambda v: v + 1, lambda v: 100.0 / v,
                   lambda v: float(v)):
            with pytest.raises(TypeError):
                op(spec.contract_value)

    def test_require_verified_raises_on_the_sentinel(self):
        with pytest.raises(ContractValueUnverifiedError, match="UNVERIFIED"):
            ContractSpec("BTCUSD").require_verified()

    def test_numeric_contract_value_requires_provenance(self):
        with pytest.raises(ContractValueUnverifiedError,
                           match="verification_source"):
            ContractSpec("BTCUSD", 0.001)
        with pytest.raises(ContractValueUnverifiedError,
                           match="verification_source"):
            ContractSpec("BTCUSD", 0.001, verification_source="   ")
        ok = ContractSpec("BTCUSD", 0.001, verification_source="delta /products")
        assert ok.is_verified is True
        assert ok.require_verified() == 0.001

    def test_provenance_without_a_value_is_rejected(self):
        with pytest.raises(ContractValueUnverifiedError, match="still UNVERIFIED"):
            ContractSpec("BTCUSD", UNVERIFIED, verification_source="delta")

    @pytest.mark.parametrize("bad", [0.0, -1.0, -0.001, True, "0.001", [1]])
    def test_invalid_contract_values_are_rejected(self, bad):
        with pytest.raises(ContractValueUnverifiedError):
            ContractSpec("BTCUSD", bad, verification_source="delta")

    def test_default_registry_knows_the_four_assets_and_verifies_none(self):
        reg = ContractSpecRegistry.default()
        assert set(reg.symbols) == set(MANUAL_SMC_SYMBOLS)
        for symbol in MANUAL_SMC_SYMBOLS:
            assert reg.is_verified(symbol) is False
            assert reg.get(symbol).contract_value is UNVERIFIED

    def test_unknown_symbol_fails_closed(self):
        reg = ContractSpecRegistry.default()
        for unknown in ("DOGEUSD", "btcusd", "BTC-USD", "", "BTCUSDT"):
            with pytest.raises(UnknownSymbolError, match="not a registered"):
                reg.get(unknown)

    def test_size_position_fails_closed_on_an_unknown_symbol(self):
        reg = ContractSpecRegistry.default()
        with pytest.raises(UnknownSymbolError):
            size_position(_ob(asset="AAAUSD"), 10.0, CFG, registry=reg)
        # ...and sizing without a registry is still USD-only, never a quantity
        sizing = size_position(_ob(asset="AAAUSD"), 10.0, CFG)
        assert sizing.notional_usd > 0

    def test_quantity_is_refused_while_the_contract_value_is_unverified(self):
        sizing = size_position(_ob(asset="BTCUSD"), 10.0, CFG)
        spec = ContractSpecRegistry.default().get("BTCUSD")
        with pytest.raises(ContractValueUnverifiedError):
            resolve_order_quantity(sizing, spec)
        # Supplying a converter does NOT rescue an unverified contract value.
        with pytest.raises(ContractValueUnverifiedError):
            resolve_order_quantity(sizing, spec, lambda n, cv: n / cv)

    def test_quantity_is_refused_without_verified_conversion_semantics(self):
        sizing = size_position(_ob(asset="BTCUSD"), 10.0, CFG)
        spec = ContractSpec("BTCUSD", 0.001, verification_source="delta /products")
        with pytest.raises(QuantitySemanticsUnverifiedError,
                          match="Refusing to guess"):
            resolve_order_quantity(sizing, spec)

    def test_quantity_requires_both_and_then_uses_only_injected_semantics(self):
        sizing = size_position(_ob(asset="BTCUSD"), 10.0, CFG)
        spec = ContractSpec("BTCUSD", 0.001, verification_source="delta /products")
        seen = {}

        def converter(notional, contract_value):
            seen["args"] = (notional, contract_value)
            return notional / contract_value

        qty = resolve_order_quantity(sizing, spec, converter)
        assert seen["args"] == (sizing.notional_usd, 0.001)
        assert qty == pytest.approx(sizing.notional_usd / 0.001)

    def test_spec_for_a_different_symbol_cannot_size_this_asset(self):
        sizing = size_position(_ob(asset="BTCUSD"), 10.0, CFG)
        wrong = ContractSpec("ETHUSD", 0.01, verification_source="delta /products")
        with pytest.raises(UnknownSymbolError, match="does not match"):
            assert_executable(sizing, wrong)
        with pytest.raises(UnknownSymbolError):
            resolve_order_quantity(sizing, wrong, lambda n, cv: n / cv)

    def test_converter_returning_a_non_positive_quantity_is_refused(self):
        sizing = size_position(_ob(asset="BTCUSD"), 10.0, CFG)
        spec = ContractSpec("BTCUSD", 0.001, verification_source="delta /products")
        for bad in (0.0, -1.0):
            with pytest.raises(SizingError, match="non-positive quantity"):
                resolve_order_quantity(sizing, spec, lambda n, cv: bad)

    def test_module_ships_no_numeric_contract_value_for_any_asset(self):
        """A future edit must not quietly hard-code a Delta contract size."""
        import quantedge.strategy.manual_smc.sizing as sizing_mod
        source = Path(sizing_mod.__file__).read_text(encoding="utf-8")
        for symbol in MANUAL_SMC_SYMBOLS:
            assert f'"{symbol}": ' not in source
            assert f"'{symbol}': " not in source
        assert "contract_value: ContractValue = UNVERIFIED" in source

REPO_ROOT = Path(__file__).parent.parent.parent
CANONICAL = REPO_ROOT / "data" / "canonical" / "delta_exchange_india"
BTC_CSV = CANONICAL / "BTCUSD" / "1h" / "full_history.csv"
_ORACLE_CACHE = {}


def _oracle_trades():
    """Oracle trades for BTCUSD Jan–Mar 2026, computed once per session."""
    if "df" not in _ORACLE_CACHE:
        from quantedge.ai.research.displacement_gated_retest_engine import (
            run_manual_spec_backtest,
        )
        _ORACLE_CACHE["df"] = run_manual_spec_backtest(
            data_base_dir=CANONICAL, symbols=["BTCUSD"],
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )["trades_df"]
    return _ORACLE_CACHE["df"]


@pytest.mark.skipif(not BTC_CSV.exists(),
                    reason="canonical BTCUSD 1h history not present")
class TestSizingOracleCapitalEquivalence:
    """
    sizing.py must reproduce the frozen oracle's own recorded capital fields
    on real candles — exactly, not approximately. The oracle stores notional,
    fees, gross/net PnL and ending capital UNROUNDED, so equality is exact.
    """

    def _paired_rows(self):
        for _i, row in _oracle_trades().iterrows():
            ob = _make_manual_ob(
                asset=row["asset"], bos_bar_idx=1, bos_dt=_ts(1),
                origin_bar_idx=0, origin_dt=_ts(0),
                direction=row["direction"],
                ob_top=row["ob_high"], ob_bottom=row["ob_low"], cfg=CFG)
            sizing = size_position(ob, row["starting_capital"], CFG)
            exit_px = (row["exit_price"]
                       if row["outcome"] == OUTCOME_TIMEOUT else None)
            yield row, ob, sizing, settle_trade(sizing, row["outcome"], exit_px)

    def test_window_has_real_trades_in_both_directions(self):
        df = _oracle_trades()
        assert len(df) >= 20
        assert set(df["direction"]) == {"SHORT", "LONG"}
        assert set(df["outcome"]) <= {OUTCOME_TP, OUTCOME_SL, OUTCOME_TIMEOUT}

    def test_geometry_and_leverage_match_the_oracle_records(self):
        for row, ob, sizing, _st in self._paired_rows():
            assert round(ob.entry_price, 6) == row["entry_price"]
            assert round(ob.sl_price, 6) == row["sl_price"]
            assert round(ob.tp_price, 6) == row["tp_price"]
            assert round(sizing.sl_dist_pct, 4) == row["entry_to_sl_distance_pct"]
            assert round(sizing.theoretical_leverage, 2) == row[
                "theoretical_leverage"]
            assert round(sizing.applied_leverage, 2) == row["leverage"]

    def test_gross_return_legs_match_the_oracle_records(self):
        for row, _ob, sizing, _st in self._paired_rows():
            assert round(sizing.gross_sl_return_pct, 2) == row[
                "gross_sl_return_pct"]
            assert round(sizing.gross_tp_return_pct, 2) == row[
                "gross_tp_return_pct"]

    def test_notional_fees_pnl_and_ending_capital_are_bit_identical(self):
        for row, _ob, sizing, st in self._paired_rows():
            assert sizing.notional_usd == row["position_notional"]
            assert sizing.fee_usd == row["fees_usd"]
            assert st.starting_balance == row["starting_capital"]
            assert st.gross_pnl_usd == row["gross_pnl_usd"]
            assert st.net_pnl_usd == row["pnl_usd"]
            assert st.ending_balance == row["ending_capital"]

    def test_realized_r_and_net_return_pct_match(self):
        for row, _ob, sizing, st in self._paired_rows():
            assert round(st.realized_r, 4) == row["realized_r"]
            net_pct = (st.net_pnl_usd / st.starting_balance * 100.0
                       if st.starting_balance > 0 else 0.0)
            assert round(net_pct, 2) == row["net_return_pct"]

class TestNoExecutionOrPersistenceLeakage:
    """Phase boundary: these two modules decide, they never act."""

    MODULES = ("portfolio", "sizing")
    BANNED = (
        "delta_client", "DeltaClient", "place_order", "submit_order",
        "cancel_order", "OrderExecutionService", "_allow_direct_execution",
        "psycopg", "sqlalchemy", "requests.", "httpx", "aiohttp",
        "api_key", "api_secret", "expiresAt", "expires_at",
    )

    def _source(self, name):
        import importlib
        mod = importlib.import_module(f"quantedge.strategy.manual_smc.{name}")
        return Path(mod.__file__).read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", MODULES)
    def test_no_execution_or_persistence_symbols(self, name):
        source = self._source(name)
        for banned in self.BANNED:
            assert banned not in source, f"{name}.py mentions {banned!r}"

    @pytest.mark.parametrize("name", MODULES)
    def test_imports_stay_inside_the_manual_smc_package(self, name):
        source = self._source(name)
        for line in source.splitlines():
            if line.startswith(("import ", "from ")) and "quantedge" in line:
                assert "quantedge.strategy.manual_smc" in line, line

    def test_no_time_based_resting_expiry_was_introduced(self):
        for name in self.MODULES:
            source = self._source(name)
            for token in ("max_resting", "resting_ttl", "ttl_bars", "deadline"):
                assert token not in source

    def test_lock_carries_no_order_state(self):
        names = {f.name for f in fields(LockHolder)}
        assert not [n for n in names
                    if "order" in n or "quantity" in n or "price" in n]














