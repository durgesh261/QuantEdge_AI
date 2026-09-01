"""
The PRODUCTION Manual SMC activation policy: first touch, then a three-candle
entry window, then permanent invalidation.

Three sibling test modules (`test_manual_smc_strategy.py`,
`test_manual_smc_adapter.py`, `test_manual_smc_executable.py`) pin themselves to
the oracle keywords `ACTIVATION_MODE_ORACLE_C` + `ManualSpecConfig()` so they keep
measuring the Mode-C trades whose semantics Step 8 proved, and each of them names
THIS file as the place where the production default is covered instead. So this
module deliberately constructs its subjects with NO policy keywords at all: every
`ManualSMCStrategy()` and `ManualSMCBacktest()` below runs on the shipped defaults,
and a regression in those defaults must fail here.

What is proved, and where the requirement comes from:

    OB creation is not a trade          TestOBCreationIsNotATrade
    25% entry / opposite-edge SL /      TestOBGeometryIsTheSpecification
      fixed 0.60% TP
    first touch arms the limit          TestFirstTouchArmsTheLimit
    an untouched OB never ages out      TestUntouchedOBLivesIndefinitely
    the window is [T, T+2] inclusive    TestThreeCandleWindow
    a missed window is permanent        TestPermanentInvalidation
    crossing the OB after entry = SL    TestCaseAIsANormalStopLoss
    every active OB is retained         TestConcurrentOBs
    one trade at a time                 TestSingleTradeSlot
    per-asset state                     TestPerAssetIndependence
    one pair can be paused alone        TestOnePairCanBeStoppedWithoutTheOthers
    production defaults                 TestProductionPolicyDefaults
    the decision published to execution TestAdapterBoundary
    READY refuses an unprovable leg     TestReadyFailsClosed
    the frozen order path, end to end   TestEndToEndToTheExecutionBoundary
    leverage + limit intent survive it  TestTheLeverageIntentAndTheRestingLimitReachTheOrder
    the frozen R:R gate, as observed    TestFrozenRiskRewardGate
    a warm OB pool at backtest start    TestHistoricalPreloadOfUntouchedOBs
    an unfilled window is not a loss    TestAnUnfilledSetupIsNeverRecordedAsALoss

Every fixture below was verified empirically against the engine before a single
assertion was written; the named numbers are measured, not predicted.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.execution.models import (
    DeltaOrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import TradeLifecycleManager, TradeLifecycleState
from quantedge.execution.validation import OrderValidationGateway
from quantedge.strategy.manual_smc.adapter import (
    MISSING_BRACKET_REFUSAL,
    InconsistentEvaluationError,
    ManualSMCAdapter,
    decision_from_setup,
)
from quantedge.strategy.manual_smc.backtest import ManualSMCBacktest
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_FIRST_TOUCH,
    ACTIVATION_MODE_ORACLE_C,
    ENTRY_WINDOW_CANDLES,
    ManualLifecycleEventType,
    ManualSMCLifecycle,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_ENTRY_WINDOW_CANDLES,
    MANUAL_SMC_FIXED_TP_PCT,
    ManualOBState,
    ManualSpecConfig,
    manual_smc_production_config,
)
from quantedge.strategy.manual_smc.strategy import ManualSMCEvaluation, ManualSMCStrategy
from quantedge.strategy.models import SetupState, StrategyDirection


# ---------------------------------------------------------------------------
# Fixtures. Hourly bars, so a bar index is also an hour of OB age.
# ---------------------------------------------------------------------------
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
BTC_TICK = Decimal("0.5")
ETH_TICK = Decimal("0.05")

Row = Tuple[float, float, float, float]


def _ts(bar_idx: int) -> datetime:
    return BASE + timedelta(hours=bar_idx)


class FakeSpec:
    """The minimal structural `TickSizeSpec`, as the sibling suites use it."""

    def __init__(self, tick_size: Decimal) -> None:
        self.tick_size = tick_size


# --- the reference SHORT OB -------------------------------------------------
# Origin bar 0 is bullish, so ob_top = CLOSE 105.0 and ob_bottom = LOW 99.0.
# Bar 1 closes below 99.0 -> BOS -> MANUAL_BTCUSD_SHORT_0_1, width 6.0,
# proximal 99.0, distal 105.0, entry 100.5, SL 105.0, TP 99.897.
S_ORIGIN: Row = (100.0, 106.0, 99.0, 105.0)
S_BOS: Row = (104.0, 104.5, 97.0, 98.0)
S_INERT: Row = (98.0, 98.0, 98.0, 98.0)          # wholly below the zone
S_TOUCH: Row = (98.5, 99.0, 98.0, 98.5)          # high == proximal exactly
S_BODY_TOUCH: Row = (99.5, 99.8, 99.2, 99.6)     # body inside the zone
S_NO_TOUCH: Row = (98.5, 98.999, 98.0, 98.5)     # high just under the proximal
S_NEAR: Row = (100.0, 100.4, 98.0, 100.0)        # in the zone, short of 100.5
S_ENTRY: Row = (100.0, 100.5, 99.5, 100.0)       # high == entry exactly
S_TP: Row = (100.0, 100.0, 99.8, 100.0)          # low 99.8 <= 99.897
S_THROUGH: Row = (104.0, 105.5, 100.0, 104.0)    # crosses the whole OB

SHORT_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"

# --- the LONG mirror --------------------------------------------------------
# Origin bar 0 is bearish, so ob_top = HIGH 106.0 and ob_bottom = CLOSE 100.0.
# Bar 1 closes above 106.0 -> MANUAL_BTCUSD_LONG_0_1, proximal 106.0,
# distal 100.0, entry 104.5, SL 100.0, TP 105.127.
L_ORIGIN: Row = (105.0, 106.0, 99.0, 100.0)
L_BOS: Row = (101.0, 107.5, 100.5, 107.0)
L_INERT: Row = (107.0, 107.0, 107.0, 107.0)      # wholly above the zone
L_TOUCH: Row = (107.0, 107.0, 106.0, 107.0)      # low == proximal exactly
L_NEAR: Row = (105.0, 105.0, 104.6, 105.0)
L_ENTRY: Row = (106.0, 106.0, 104.5, 106.0)      # low == entry exactly
L_TP: Row = (105.0, 105.3, 105.0, 105.0)         # high 105.3 >= 105.127
L_THROUGH: Row = (101.0, 101.0, 100.0, 101.0)    # crosses the whole OB

LONG_OB_ID = "MANUAL_BTCUSD_LONG_0_1"

# --- a NARROWER SHORT OB, same rules, different width -----------------------
# top 102.0, bottom 99.5, width 2.5, entry 100.125, SL 102.0, TP 99.52425.
N_ORIGIN: Row = (100.0, 103.0, 99.5, 102.0)
N_BOS: Row = (101.0, 101.0, 99.0, 99.0)

# --- realistic BTC prices, for the frozen execution path --------------------
# top 78200.0, bottom 78000.0, entry 78050.0, SL 78200.0, TP 77581.7.
E_ORIGIN: Row = (78050.0, 78250.0, 78000.0, 78200.0)
E_BOS: Row = (78200.0, 78210.0, 77900.0, 77950.0)
E_INERT: Row = (77900.0, 77900.0, 77900.0, 77900.0)
E_TOUCH: Row = (77950.0, 78000.0, 77900.0, 77950.0)

# --- a realistic BTC OB whose Manual SMC leverage is deliberately NOT 100x ---
# top 78312.0, bottom 77896.0, width 416.0, entry 78000.0, SL 78312.0.
# The stop sits 0.400% away, so applied_leverage = 35 / 0.400 = 87.5 -> 87, and
# the authorized 0.60% take profit still clears the frozen 1.5 R:R gate, but only
# just: the quantized bracket is risk 312.0 / reward 468.0, i.e. R:R = EXACTLY
# 1.5. The gateway rejects `rr < minimum`, so equality passes — this fixture now
# sits ON the boundary rather than 0.125 above it, and a future widening of this
# OB (or any tightening of the gate) would flip it to INVALID_RISK_REWARD. Stated
# here rather than hidden because the margin, not just the verdict, is evidence.
M_ORIGIN: Row = (77950.0, 78400.0, 77896.0, 78312.0)
M_BOS: Row = (78300.0, 78310.0, 77800.0, 77850.0)
M_INERT: Row = (77800.0, 77800.0, 77800.0, 77800.0)
M_TOUCH: Row = (77850.0, 77896.0, 77800.0, 77850.0)

# ---------------------------------------------------------------------------
# Drivers. NOTE the absence of policy keywords: the defaults are the subject.
# ---------------------------------------------------------------------------
def _strategy(
    assets: Sequence[str] = ("BTCUSD",),
    ticks: Optional[Dict[str, Decimal]] = None,
) -> ManualSMCStrategy:
    """A production Manual SMC strategy on shipped defaults."""
    specs = None if ticks is None else {a: FakeSpec(t) for a, t in ticks.items()}
    return ManualSMCStrategy(assets=list(assets), tick_specs=specs)


def _drive(
    strategy: ManualSMCStrategy,
    rows: Sequence[Row],
    asset: str = "BTCUSD",
) -> List[ManualSMCEvaluation]:
    """Feed closed candles in order, one bar index per row, and keep every result."""
    return [strategy.evaluate_closed_candle(asset, b, _ts(b), *row)
            for b, row in enumerate(rows)]


def _run(
    rows: Sequence[Row],
    asset: str = "BTCUSD",
    ticks: Optional[Dict[str, Decimal]] = None,
) -> Tuple[ManualSMCStrategy, List[ManualSMCEvaluation]]:
    strategy = _strategy((asset,), ticks)
    return strategy, _drive(strategy, rows, asset)


def _bars_with(
    evals: Sequence[ManualSMCEvaluation],
    kind: ManualLifecycleEventType,
    ob_id: Optional[str] = None,
) -> List[int]:
    """Bar indices on which `kind` was emitted, in order."""
    return [ev.bar_idx for ev in evals for e in ev.events
            if e.event_type is kind and (ob_id is None or e.ob_id == ob_id)]


def _detail(
    evals: Sequence[ManualSMCEvaluation],
    kind: ManualLifecycleEventType,
) -> str:
    """The detail string of the first `kind` event. Fails loudly if there is none."""
    for ev in evals:
        for e in ev.events:
            if e.event_type is kind:
                return e.detail
    raise AssertionError(f"no {kind.name} event was emitted")


def _kinds_at(evals: Sequence[ManualSMCEvaluation], bar_idx: int) -> List[str]:
    return [e.event_type.name for e in evals[bar_idx].events]


FILLED = ManualLifecycleEventType.ENTRY_FILLED
ARMED = ManualLifecycleEventType.FIRST_TOUCH_LIMIT_ACTIVATED
CREATED = ManualLifecycleEventType.OB_CREATED
INVALID = ManualLifecycleEventType.INVALIDATED
CLOSED = ManualLifecycleEventType.TRADE_CLOSED
BLOCKED = ManualLifecycleEventType.ENTRY_BLOCKED_BY_ACTIVE_TRADE


class TestOBCreationIsNotATrade:
    """
    The BOS/CHOCH candle creates the OB and NOTHING else.

    This is the rule the whole file exists to protect, so it is measured on a
    fixture whose BOS candle would itself have filled the order: bar 1's high is
    104.5, far above the 100.5 entry. If creation and execution were the same
    event, or if a fresh OB were admitted before the candle it was born on had
    been evaluated, these tests would see a fill on bar 1.
    """

    def test_the_bos_candle_emits_only_ob_created(self):
        _, evals = _run([S_ORIGIN, S_BOS])
        assert _kinds_at(evals, 1) == ["OB_CREATED"]

    def test_no_fill_on_the_creation_candle_although_it_reaches_the_entry(self):
        strategy, evals = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert S_BOS[1] > ob.entry_price          # the candle did cover the entry
        assert _bars_with(evals, FILLED) == []
        assert evals[1].filled is None

    def test_no_trade_becomes_active_on_the_creation_candle(self):
        strategy, evals = _run([S_ORIGIN, S_BOS])
        assert strategy.lifecycle.active_trade is None
        assert evals[1].active_trade is None
        assert evals[1].lock_holder is None

    def test_a_fresh_ob_is_awaiting_its_first_touch(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].state is (
            ManualOBState.AWAITING_DISPLACEMENT)

    def test_a_fresh_ob_has_no_armed_entry_window(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].limit_active_from_bar is None

    def test_the_creation_candle_publishes_no_executable_decision(self):
        strategy, evals = _run([S_ORIGIN, S_BOS], ticks={"BTCUSD": BTC_TICK})
        adaptation = ManualSMCAdapter(config=strategy.cfg, timeframe="1h").adapt(evals[1])
        assert adaptation.ready_decisions == ()
        assert adaptation.decisions[0].setup_state is SetupState.WATCHING_OB

    def test_long_creation_is_equally_inert(self):
        strategy, evals = _run([L_ORIGIN, L_BOS])
        assert _kinds_at(evals, 1) == ["OB_CREATED"]
        assert L_BOS[2] < strategy.lifecycle.live_obs[LONG_OB_ID].entry_price
        assert _bars_with(evals, FILLED) == []


class TestOBGeometryIsTheSpecification:
    """
    Entry = 25% depth, SL = the opposite OB edge, TP = a flat 0.60%.

    The two SHORT fixtures have different widths (6.0 and 2.5) on purpose: a TP
    secretly derived from OB size, SL distance or an R:R multiple cannot hold the
    same 0.60% on both, so the pair rules those substitutes out by measurement.
    """

    def test_short_boundaries_come_from_the_origin_candle(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert (ob.ob_top, ob.ob_bottom) == (105.0, 99.0)     # CLOSE, then LOW
        assert (ob.proximal, ob.distal) == (99.0, 105.0)

    def test_short_entry_is_exactly_25_percent_into_the_ob(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.entry_price == 100.5
        assert ob.entry_price == pytest.approx(ob.ob_bottom + 0.25 * ob.ob_width)

    def test_short_entry_is_neither_the_midpoint_nor_an_origin_candle_price(self):
        """
        Exactness alone cannot tell 25% apart from the substitutes it is easy to
        drift into, because a fixture can be numerically right by accident. This
        names the excluded alternatives instead: the 50% midpoint, and every one
        of the four origin-candle prices the zone was cut from.
        """
        strategy, _ = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        midpoint = ob.ob_bottom + 0.50 * ob.ob_width
        assert midpoint == 102.0
        assert ob.entry_price != midpoint
        o_open, o_high, o_low, o_close = S_ORIGIN
        assert o_close == ob.ob_top and o_low == ob.ob_bottom
        assert ob.entry_price not in (o_open, o_high, o_low, o_close)
        #: 25% of the way up from the proximal, so strictly nearer the proximal
        #: edge than the midpoint is.
        assert ob.ob_bottom < ob.entry_price < midpoint

    def test_short_stop_is_the_opposite_ob_edge(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.sl_price == 105.0 == ob.ob_top == ob.distal

    def test_short_take_profit_is_a_flat_060_percent_below_the_entry(self):
        strategy, _ = _run([S_ORIGIN, S_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.tp_price == pytest.approx(99.897)
        assert ob.tp_price == pytest.approx(ob.entry_price * 0.994)

    def test_long_boundaries_come_from_the_origin_candle(self):
        strategy, _ = _run([L_ORIGIN, L_BOS])
        ob = strategy.lifecycle.live_obs[LONG_OB_ID]
        assert (ob.ob_top, ob.ob_bottom) == (106.0, 100.0)    # HIGH, then CLOSE
        assert (ob.proximal, ob.distal) == (106.0, 100.0)

    def test_long_entry_is_exactly_25_percent_into_the_ob(self):
        strategy, _ = _run([L_ORIGIN, L_BOS])
        ob = strategy.lifecycle.live_obs[LONG_OB_ID]
        assert ob.entry_price == 104.5
        assert ob.entry_price == pytest.approx(ob.ob_top - 0.25 * ob.ob_width)

    def test_long_entry_is_neither_the_midpoint_nor_an_origin_candle_price(self):
        """The LONG mirror of the SHORT exclusion test."""
        strategy, _ = _run([L_ORIGIN, L_BOS])
        ob = strategy.lifecycle.live_obs[LONG_OB_ID]
        midpoint = ob.ob_bottom + 0.50 * ob.ob_width
        assert midpoint == 103.0
        assert ob.entry_price != midpoint
        o_open, o_high, o_low, o_close = L_ORIGIN
        assert o_high == ob.ob_top and o_close == ob.ob_bottom
        assert ob.entry_price not in (o_open, o_high, o_low, o_close)
        #: 25% of the way DOWN from the proximal, so strictly nearer the proximal
        #: edge (the top, for a long) than the midpoint is.
        assert midpoint < ob.entry_price < ob.ob_top

    def test_long_stop_is_the_opposite_ob_edge(self):
        strategy, _ = _run([L_ORIGIN, L_BOS])
        ob = strategy.lifecycle.live_obs[LONG_OB_ID]
        assert ob.sl_price == 100.0 == ob.ob_bottom == ob.distal

    def test_long_take_profit_is_a_flat_060_percent_above_the_entry(self):
        strategy, _ = _run([L_ORIGIN, L_BOS])
        ob = strategy.lifecycle.live_obs[LONG_OB_ID]
        assert ob.tp_price == pytest.approx(105.127)
        assert ob.tp_price == pytest.approx(ob.entry_price * 1.006)

    def test_a_narrower_ob_follows_the_same_three_rules(self):
        strategy, _ = _run([N_ORIGIN, N_BOS])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert (ob.ob_top, ob.ob_bottom, ob.ob_width) == (102.0, 99.5, 2.5)
        assert ob.entry_price == 100.125 == ob.ob_bottom + 0.25 * ob.ob_width
        assert ob.sl_price == 102.0 == ob.distal
        assert ob.tp_price == pytest.approx(99.52425)

    def test_take_profit_is_not_derived_from_ob_width(self):
        wide, _ = _run([S_ORIGIN, S_BOS])
        narrow, _ = _run([N_ORIGIN, N_BOS])
        a = wide.lifecycle.live_obs[SHORT_OB_ID]
        b = narrow.lifecycle.live_obs[SHORT_OB_ID]
        assert a.ob_width != b.ob_width
        assert a.tp_price / a.entry_price == pytest.approx(b.tp_price / b.entry_price)
        assert a.tp_price / a.entry_price == pytest.approx(0.994)

    def test_take_profit_is_not_a_risk_reward_multiple(self):
        wide, _ = _run([S_ORIGIN, S_BOS])
        narrow, _ = _run([N_ORIGIN, N_BOS])
        a = wide.lifecycle.live_obs[SHORT_OB_ID]
        b = narrow.lifecycle.live_obs[SHORT_OB_ID]
        # Same TP rule, materially different R:R -> the TP is not an R multiple.
        def rr(ob):
            return (ob.entry_price - ob.tp_price) / (ob.sl_price - ob.entry_price)

        assert rr(a) == pytest.approx(0.1340, abs=1e-4)
        assert rr(b) == pytest.approx(0.3204, abs=1e-4)

    def test_the_stop_never_sits_beyond_the_ob(self):
        for rows, ob_id in (([S_ORIGIN, S_BOS], SHORT_OB_ID),
                            ([L_ORIGIN, L_BOS], LONG_OB_ID)):
            strategy, _ = _run(rows)
            ob = strategy.lifecycle.live_obs[ob_id]
            assert ob.ob_bottom <= ob.sl_price <= ob.ob_top

    def test_the_production_config_declares_060_percent(self):
        strategy = _strategy()
        assert strategy.cfg.fixed_tp_market_pct == 0.60 == MANUAL_SMC_FIXED_TP_PCT


class TestFirstTouchArmsTheLimit:
    """
    Re-entering the OB zone — body or wick, edge inclusive — arms the 25% limit.

    Nothing deeper is required: the touch predicate is the PROXIMAL edge, not the
    entry level, so a candle that grazes the boundary and retreats still starts
    the clock. And it starts it on its own candle, not the next one.
    """

    def test_an_edge_exact_wick_touch_arms_the_limit(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH])
        assert S_TOUCH[1] == 99.0                 # high == proximal, to the tick
        assert _bars_with(evals, ARMED) == [3]

    def test_a_body_inside_the_zone_arms_the_limit(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_BODY_TOUCH])
        assert _bars_with(evals, ARMED) == [2]

    def test_a_high_just_short_of_the_proximal_does_not_arm(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_NO_TOUCH])
        assert _bars_with(evals, ARMED) == []
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].state is (
            ManualOBState.AWAITING_DISPLACEMENT)

    def test_the_limit_is_armed_from_the_touch_candle_itself(self):
        strategy, _ = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH])
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].limit_active_from_bar == 3

    def test_the_touched_ob_is_resting_not_traded(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH])
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].state is ManualOBState.LIMIT_RESTING
        assert evals[3].filled is None
        assert strategy.lifecycle.active_trade is None

    def test_the_arming_event_names_the_inclusive_window(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH])
        assert "limit live for bars 3..5 (3 candles, inclusive)" in _detail(evals, ARMED)

    def test_the_arming_event_names_the_proximal_and_the_entry(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH])
        detail = _detail(evals, ARMED)
        assert "first zone touch at proximal 99.000000" in detail
        assert "100.500000" in detail

    def test_only_the_first_touch_arms(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR])
        assert _bars_with(evals, ARMED) == [3]

    def test_a_later_touch_does_not_extend_the_window(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR, S_ENTRY])
        assert "[3..5] expired" in _detail(evals, INVALID)

    def test_a_long_ob_arms_on_an_edge_exact_low(self):
        _, evals = _run([L_ORIGIN, L_BOS, L_INERT, L_TOUCH])
        assert L_TOUCH[2] == 106.0                # low == proximal, to the tick
        assert _bars_with(evals, ARMED) == [3]


class TestUntouchedOBLivesIndefinitely:
    """
    An untouched OB never ages out. Bars are hourly, so the parametrized waits
    are one candle, one full day, more than three days, and a little over two
    months — in every case the OB is still there, still valid, and still trades
    on the touch when it finally comes.

    This is the distinguishing test: an implementation that trades on the OB
    creation candle, or that expires an OB after N candles regardless of touch,
    cannot produce a fill 1503 bars after the BOS.
    """

    @pytest.mark.parametrize("quiet_bars", [1, 24, 80, 1500])
    def test_an_untouched_ob_stays_active_and_is_never_invalidated(self, quiet_bars):
        strategy, evals = _run([S_ORIGIN, S_BOS] + [S_INERT] * quiet_bars)
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.state is ManualOBState.AWAITING_DISPLACEMENT
        assert _bars_with(evals, INVALID) == []
        assert all(ev.invalidated == () for ev in evals)

    @pytest.mark.parametrize("quiet_bars", [1, 24, 80, 1500])
    def test_an_untouched_ob_still_arms_and_fills_much_later(self, quiet_bars):
        _, evals = _run([S_ORIGIN, S_BOS] + [S_INERT] * quiet_bars + [S_TOUCH, S_ENTRY])
        assert _bars_with(evals, ARMED) == [quiet_bars + 2]
        assert _bars_with(evals, FILLED) == [quiet_bars + 3]

    @pytest.mark.parametrize("quiet_bars,expected_age",
                             [(1, 3.0), (24, 26.0), (80, 82.0), (1500, 1502.0)])
    def test_the_recorded_ob_age_at_entry_is_the_full_wait(self, quiet_bars, expected_age):
        strategy, _ = _run(
            [S_ORIGIN, S_BOS] + [S_INERT] * quiet_bars + [S_TOUCH, S_ENTRY, S_TP])
        exit_, = strategy.lifecycle.exits
        assert exit_.outcome == "FILLED_TP"
        assert exit_.ob_age_at_entry_hours == pytest.approx(expected_age)
        assert exit_.entry_bar_from_bos == quiet_bars + 2

    def test_two_months_of_silence_costs_nothing_in_the_geometry(self):
        strategy, _ = _run([S_ORIGIN, S_BOS] + [S_INERT] * 1500)
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert (ob.entry_price, ob.sl_price) == (100.5, 105.0)
        assert ob.tp_price == pytest.approx(99.897)


class TestThreeCandleWindow:
    """
    The window is [T, T+2] INCLUSIVE, where T is the first-touch candle.

    Spelling the convention out is a requirement in its own right, so it is
    asserted three ways: the constant is 3, the arming message names the bar
    range, and fills are measured on each of the three candles while the fourth
    is proved dead. Note the first-touch candle counts as window candle one — a
    candle that touches the zone and reaches the entry fills immediately.
    """

    def test_the_window_length_constant_is_three(self):
        assert ENTRY_WINDOW_CANDLES == MANUAL_SMC_ENTRY_WINDOW_CANDLES == 3
        assert _strategy().lifecycle.entry_window_candles == 3

    def test_a_fill_on_the_first_touch_candle_itself(self):
        # S_ENTRY both enters the zone (high 100.5 >= 99.0) and reaches 100.5.
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_ENTRY])
        assert _kinds_at(evals, 3) == ["FIRST_TOUCH_LIMIT_ACTIVATED", "ENTRY_FILLED"]

    def test_a_fill_on_window_candle_two(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_ENTRY])
        assert _bars_with(evals, ARMED) == [3]
        assert _bars_with(evals, FILLED) == [4]

    def test_a_fill_on_window_candle_three_the_last_one(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_ENTRY])
        assert _bars_with(evals, ARMED) == [3]
        assert _bars_with(evals, FILLED) == [5]

    def test_no_fill_on_window_candle_four(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR, S_ENTRY])
        assert _bars_with(evals, FILLED) == []
        assert _bars_with(evals, INVALID) == [6]

    def test_expiry_beats_an_entry_touch_on_the_same_candle(self):
        strategy, evals = _run(
            [S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR, S_ENTRY])
        # Bar 6 DID reach the entry; the window had already closed, so the order
        # is cancelled rather than filled. Expiry is checked before the touch.
        assert S_ENTRY[1] >= 100.5
        assert _kinds_at(evals, 6) == ["INVALIDATED"]
        assert strategy.lifecycle.exits == []

    def test_the_window_survives_a_bar_that_leaves_the_zone_entirely(self):
        _, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_INERT, S_ENTRY])
        assert _bars_with(evals, FILLED) == [5]

    def test_a_long_ob_fills_on_its_last_window_candle(self):
        _, evals = _run([L_ORIGIN, L_BOS, L_INERT, L_TOUCH, L_NEAR, L_ENTRY])
        assert _bars_with(evals, ARMED) == [3]
        assert _bars_with(evals, FILLED) == [5]

    def test_a_long_ob_expires_on_its_fourth_candle(self):
        _, evals = _run([L_ORIGIN, L_BOS, L_INERT, L_TOUCH, L_NEAR, L_NEAR, L_ENTRY])
        assert _bars_with(evals, FILLED) == []
        assert _bars_with(evals, INVALID) == [6]


# Case B: the zone is touched, the window opens, and the 25% entry is never
# reached inside it. The order is cancelled and the OB is finished forever.
CASE_B = [S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR, S_NEAR,
          S_ENTRY, S_ENTRY]


class TestPermanentInvalidation:
    """
    A missed window is permanent. The OB leaves the live pool, its resting order
    is reported for withdrawal, and no later candle can bring it back — not even
    one that sits exactly on the entry, twice.
    """

    def test_the_expired_ob_leaves_the_live_pool(self):
        strategy, _ = _run(CASE_B)
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs

    def test_the_expired_ob_is_reported_for_order_withdrawal(self):
        _, evals = _run(CASE_B)
        assert evals[6].invalidated == (SHORT_OB_ID,)
        assert [ev.bar_idx for ev in evals if ev.invalidated] == [6]

    def test_the_expiry_message_states_the_cancellation_and_the_finality(self):
        _, evals = _run(CASE_B)
        detail = _detail(evals, INVALID)
        assert "order cancelled and this OB is permanently invalid" in detail

    def test_the_expiry_message_reports_a_missing_fill_not_a_missing_touch(self):
        # An entry reached but refused by the single trade slot also ends here, so
        # the wording is deliberately about the fill. See TestSingleTradeSlot.
        _, evals = _run(CASE_B)
        assert "without an admitted fill at 100.500000" in _detail(evals, INVALID)

    def test_the_expired_ob_never_fills_again(self):
        strategy, evals = _run(CASE_B)
        assert _bars_with(evals, FILLED) == []
        assert strategy.lifecycle.active_trade is None
        assert strategy.lifecycle.exits == []

    def test_the_expired_ob_is_never_readmitted(self):
        strategy, evals = _run(CASE_B)
        assert _bars_with(evals, CREATED) == [1]
        for ev in evals[7:]:
            assert [s.ob_id for s in ev.setups] == []
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs

    def test_the_expired_ob_stops_being_published_as_a_setup(self):
        strategy, evals = _run(CASE_B, ticks={"BTCUSD": BTC_TICK})
        adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
        assert adapter.adapt(evals[5]).ready_decisions != ()
        assert adapter.adapt(evals[8]).ready_decisions == ()

    def test_a_distal_breach_before_the_first_touch_invalidates(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_THROUGH])
        assert _bars_with(evals, INVALID) == [2]
        assert _bars_with(evals, FILLED) == []
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs

    def test_the_distal_breach_is_checked_before_the_entry_touch(self):
        # S_THROUGH covers the entry AND breaks the distal on one candle; an OB
        # whose stop has already been violated must not be entered.
        _, evals = _run([S_ORIGIN, S_BOS, S_THROUGH])
        assert S_THROUGH[1] > 105.0 and S_THROUGH[2] < 100.5
        assert _kinds_at(evals, 2) == ["INVALIDATED"]

    def test_a_long_distal_breach_before_the_first_touch_invalidates(self):
        strategy, evals = _run([L_ORIGIN, L_BOS, L_THROUGH])
        assert _bars_with(evals, INVALID) == [2]
        assert LONG_OB_ID not in strategy.lifecycle.live_obs


class TestCaseAIsANormalStopLoss:
    """
    Price crossing the whole OB AFTER the entry is filled is not an invalidation
    — it is the stop, taken at the opposite edge, for exactly -1R. Distinguishing
    this from Case B is the point: one is a trade that lost, the other is a trade
    that never happened.
    """

    def test_a_short_that_is_crossed_after_entry_stops_out(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_ENTRY, S_THROUGH])
        assert _bars_with(evals, FILLED) == [3]
        exit_, = strategy.lifecycle.exits
        assert exit_.outcome == "FILLED_SL"

    def test_the_short_stop_is_taken_at_the_opposite_edge_for_minus_one_r(self):
        strategy, _ = _run([S_ORIGIN, S_BOS, S_INERT, S_ENTRY, S_THROUGH])
        exit_, = strategy.lifecycle.exits
        assert (exit_.entry_price, exit_.exit_price) == (100.5, 105.0)
        assert exit_.realized_r == pytest.approx(-1.0)

    def test_a_long_that_is_crossed_after_entry_stops_out(self):
        strategy, evals = _run([L_ORIGIN, L_BOS, L_INERT, L_ENTRY, L_THROUGH])
        assert _bars_with(evals, FILLED) == [3]
        exit_, = strategy.lifecycle.exits
        assert exit_.outcome == "FILLED_SL"
        assert (exit_.entry_price, exit_.exit_price) == (104.5, 100.0)
        assert exit_.realized_r == pytest.approx(-1.0)

    def test_a_stopped_out_ob_is_closed_not_invalidated(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_ENTRY, S_THROUGH])
        assert _bars_with(evals, INVALID) == []
        assert _bars_with(evals, CLOSED) == [4]
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs

    def test_the_take_profit_leg_pays_the_flat_060_percent(self):
        strategy, evals = _run([S_ORIGIN, S_BOS, S_INERT, S_ENTRY, S_TP])
        exit_, = strategy.lifecycle.exits
        assert exit_.outcome == "FILLED_TP"
        assert exit_.exit_price == pytest.approx(99.897)
        assert _bars_with(evals, CLOSED) == [4]


# A second, lower SHORT OB whose whole lifecycle runs while the reference OB is
# still sitting untouched above it. OB2 = MANUAL_BTCUSD_SHORT_2_3: top 97.0,
# bottom 94.0, entry 94.75, SL 97.0, TP 94.1815.
TWO_OBS = [
    S_ORIGIN, S_BOS,
    (95.0, 98.0, 94.0, 97.0),     # 2: bull origin, entirely below OB1's zone
    (93.0, 93.5, 92.0, 93.0),     # 3: BOS -> OB2
    (95.0, 95.0, 94.0, 95.0),     # 4: OB2 first touch AND fill
    (94.0, 94.0, 93.5, 94.0),     # 5: OB2 take profit
    S_TOUCH, S_ENTRY,             # 6, 7: OB1 finally touched, then filled
]
SECOND_OB_ID = "MANUAL_BTCUSD_SHORT_2_3"


def _descending_staircase(count: int) -> List[Row]:
    """`count` OB-creating bar pairs, each below the last, so none is ever touched."""
    rows: List[Row] = []
    for k in range(count):
        base = 200.0 - 5.0 * k
        rows.append((base, base + 1.0, base - 1.0, base + 0.5))   # bull origin
        rows.append((base - 1.5, base - 1.4, base - 3.0, base - 2.5))  # BOS below
    return rows


class TestConcurrentOBs:
    """
    Every active OB is retained, and each runs its own lifecycle. Any nearest-N
    cap belongs to the dashboard, never to the engine, so the pool here is
    allowed to grow without limit and an older OB is never dropped to make room.
    """

    def test_both_obs_are_live_at_the_same_time(self):
        strategy, evals = _run(TWO_OBS[:4])
        assert set(strategy.lifecycle.live_obs) == {SHORT_OB_ID, SECOND_OB_ID}
        assert _bars_with(evals, CREATED) == [1, 3]

    def test_the_newer_ob_does_not_displace_the_older_one(self):
        strategy, _ = _run(TWO_OBS[:4])
        older = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert older.state is ManualOBState.AWAITING_DISPLACEMENT
        assert (older.entry_price, older.sl_price) == (100.5, 105.0)

    def test_the_newer_ob_trades_while_the_older_one_waits(self):
        strategy, evals = _run(TWO_OBS[:6])
        assert _bars_with(evals, ARMED, SECOND_OB_ID) == [4]
        assert _bars_with(evals, FILLED, SECOND_OB_ID) == [4]
        assert _bars_with(evals, CLOSED, SECOND_OB_ID) == [5]
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].limit_active_from_bar is None

    def test_the_older_ob_trades_afterwards_on_its_own_first_touch(self):
        strategy, evals = _run(TWO_OBS)
        assert _bars_with(evals, ARMED, SHORT_OB_ID) == [6]
        assert _bars_with(evals, FILLED, SHORT_OB_ID) == [7]
        assert [x.ob_id for x in strategy.lifecycle.exits] == [SECOND_OB_ID]

    def test_the_two_lifecycles_never_share_state(self):
        strategy, _ = _run(TWO_OBS[:6])
        older = strategy.lifecycle.live_obs[SHORT_OB_ID]
        exit_, = strategy.lifecycle.exits
        assert exit_.ob_id == SECOND_OB_ID
        assert exit_.entry_price == 94.75 != older.entry_price

    def test_the_engine_holds_far_more_than_ten_active_obs(self):
        strategy, evals = _run(_descending_staircase(12))
        assert len(strategy.lifecycle.live_obs) == 12
        assert len(_bars_with(evals, CREATED)) == 12
        assert all(ob.state is ManualOBState.AWAITING_DISPLACEMENT
                   for ob in strategy.lifecycle.live_obs.values())

    def test_every_retained_ob_is_published_as_a_setup(self):
        strategy, evals = _run(_descending_staircase(12), ticks={"BTCUSD": BTC_TICK})
        assert len(evals[-1].setups) == 12
        adaptation = ManualSMCAdapter(config=strategy.cfg, timeframe="1h").adapt(evals[-1])
        assert len(adaptation.decisions) == 12
        assert adaptation.ready_decisions == ()      # none has been touched yet


# Two assets on one clock. ETH takes the only trade slot on bar 3; BTC reaches
# its own entry on bars 4-6 and is refused each time; BTC's window then closes on
# schedule at bar 7. S_HOLD keeps the ETH trade open without touching either leg.
S_HOLD: Row = (100.0, 100.2, 99.9, 100.0)
BTC_LEG = [S_ORIGIN, S_BOS, S_INERT, S_INERT, S_ENTRY, S_ENTRY, S_ENTRY, S_ENTRY]
ETH_LEG = [S_ORIGIN, S_BOS, S_INERT, S_ENTRY, S_HOLD, S_HOLD, S_HOLD, S_HOLD]
ETH_OB_ID = "MANUAL_ETHUSD_SHORT_0_1"


def _run_two_assets() -> Tuple[ManualSMCStrategy, Dict[str, List[ManualSMCEvaluation]]]:
    """Interleave two assets bar by bar, BTC first, as a live scan would."""
    strategy = _strategy(("BTCUSD", "ETHUSD"),
                         {"BTCUSD": BTC_TICK, "ETHUSD": ETH_TICK})
    out: Dict[str, List[ManualSMCEvaluation]] = {"BTCUSD": [], "ETHUSD": []}
    for bar_idx in range(len(BTC_LEG)):
        for asset, rows in (("BTCUSD", BTC_LEG), ("ETHUSD", ETH_LEG)):
            out[asset].append(strategy.evaluate_closed_candle(
                asset, bar_idx, _ts(bar_idx), *rows[bar_idx]))
    return strategy, out


class TestSingleTradeSlot:
    """
    One trade at a time, and the OB that could not have it is left alone rather
    than quietly given a longer window.

    The refusal is reported and the resting OB stays resting, but its clock keeps
    running: it expires on the candle it was always going to expire on. That is
    one fewer trade, never an unslotted one, which is the fail-closed direction —
    the specification does not cover the case, and the expiry message says
    "without an admitted fill" precisely because the entry WAS reached here.
    """

    def test_the_first_asset_to_reach_its_entry_takes_the_slot(self):
        strategy, evals = _run_two_assets()
        assert _bars_with(evals["ETHUSD"], FILLED) == [3]
        assert strategy.lifecycle.active_trade is not None
        assert strategy.lifecycle.active_trade.ob.ob_id == ETH_OB_ID

    def test_the_other_asset_is_refused_on_every_candle_it_reaches_its_entry(self):
        _, evals = _run_two_assets()
        assert _bars_with(evals["BTCUSD"], BLOCKED) == [4, 5, 6]
        assert _bars_with(evals["BTCUSD"], FILLED) == []

    def test_the_refusal_is_reported_against_the_right_ob(self):
        _, evals = _run_two_assets()
        blocked = evals["BTCUSD"][4].blocked
        assert [b.ob_id for b in blocked] == [SHORT_OB_ID]
        assert evals["BTCUSD"][4].lock_holder is not None

    def test_a_refused_ob_stays_resting(self):
        strategy, evals = _run_two_assets()
        assert _bars_with(evals["BTCUSD"], ARMED) == [4]
        for bar_idx in (4, 5, 6):
            resting = [s for s in evals["BTCUSD"][bar_idx].setups
                       if s.ob_id == SHORT_OB_ID]
            assert [s.state for s in resting] == [ManualOBState.LIMIT_RESTING]

    def test_a_refused_ob_gets_no_extra_candles(self):
        _, evals = _run_two_assets()
        assert _bars_with(evals["BTCUSD"], INVALID) == [7]
        assert "[4..6] expired" in _detail(evals["BTCUSD"], INVALID)

    def test_the_refused_entry_never_becomes_a_trade(self):
        strategy, _ = _run_two_assets()
        assert [x.ob_id for x in strategy.lifecycle.exits] == []
        assert strategy.lifecycle.active_trade.ob.ob_id == ETH_OB_ID

    def test_a_refused_ob_is_not_published_as_executable(self):
        strategy, evals = _run_two_assets()
        adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
        adaptation = adapter.adapt(evals["BTCUSD"][4])
        assert adaptation.trade_slot_taken is True
        assert adaptation.ready_decisions == ()

    def test_the_two_assets_are_evaluated_on_the_same_bars(self):
        _, evals = _run_two_assets()
        assert [ev.bar_idx for ev in evals["BTCUSD"]] == list(range(8))
        assert [ev.bar_idx for ev in evals["ETHUSD"]] == list(range(8))


class TestPerAssetIndependence:
    """
    Per-asset state, evaluated on the same clock. Enabled pairs are scanned
    together, never serialized, and one pair's price action is invisible to
    another pair's OBs.
    """

    def test_each_asset_gets_its_own_ob(self):
        strategy = _strategy(("BTCUSD", "ETHUSD"))
        for bar_idx, row in enumerate([S_ORIGIN, S_BOS]):
            for asset in ("BTCUSD", "ETHUSD"):
                strategy.evaluate_closed_candle(asset, bar_idx, _ts(bar_idx), *row)
        assert set(strategy.lifecycle.live_obs) == {SHORT_OB_ID, ETH_OB_ID}

    def test_one_assets_touch_does_not_arm_the_other(self):
        strategy = _strategy(("BTCUSD", "ETHUSD"))
        btc = [S_ORIGIN, S_BOS, S_INERT, S_TOUCH]
        eth = [S_ORIGIN, S_BOS, S_INERT, S_INERT]
        for bar_idx in range(4):
            strategy.evaluate_closed_candle("BTCUSD", bar_idx, _ts(bar_idx), *btc[bar_idx])
            strategy.evaluate_closed_candle("ETHUSD", bar_idx, _ts(bar_idx), *eth[bar_idx])
        assert strategy.lifecycle.live_obs[SHORT_OB_ID].limit_active_from_bar == 3
        assert strategy.lifecycle.live_obs[ETH_OB_ID].limit_active_from_bar is None

    def test_an_evaluation_only_reports_its_own_assets_setups(self):
        _, evals = _run_two_assets()
        assert all(s.asset == "BTCUSD" for ev in evals["BTCUSD"] for s in ev.setups)
        assert all(s.asset == "ETHUSD" for ev in evals["ETHUSD"] for s in ev.setups)

    def test_an_invalidation_on_one_asset_leaves_the_other_alone(self):
        strategy = _strategy(("BTCUSD", "ETHUSD"))
        btc = [S_ORIGIN, S_BOS, S_THROUGH]        # distal breach -> invalidated
        eth = [S_ORIGIN, S_BOS, S_INERT]
        for bar_idx in range(3):
            strategy.evaluate_closed_candle("BTCUSD", bar_idx, _ts(bar_idx), *btc[bar_idx])
            strategy.evaluate_closed_candle("ETHUSD", bar_idx, _ts(bar_idx), *eth[bar_idx])
        assert list(strategy.lifecycle.live_obs) == [ETH_OB_ID]


def _feed(strategy, plan, assets):
    """
    Drive a multi-asset strategy from a `{bar_idx: {asset: row}}` plan.

    An asset ABSENT from a bar's entry is a pair whose scanning is switched OFF
    for that bar: the operator simply stops handing its candles over. Assets are
    fed alphabetically within a bar, which is `build_timeline`'s own tie-break.
    """
    out = {a: [] for a in assets}
    for bar_idx in sorted(plan):
        for asset in assets:
            row = plan[bar_idx].get(asset)
            if row is not None:
                out[asset].append(strategy.evaluate_closed_candle(
                    asset, bar_idx, _ts(bar_idx), *row))
    return out


class TestOnePairCanBeStoppedWithoutTheOthers:
    """
    Turning one pair off must not disturb any other pair, and must not disturb
    the paused pair's own OBs either.

    There is nothing to switch inside the strategy: `evaluate_closed_candle`
    takes the asset as an argument, so "SOL scanning OFF" is the caller not
    feeding SOL. What has to be PROVED is that this is safe — that the global
    trade-slot coupling and the global candle-order guard do not make the
    scanner depend on any one symbol still reporting. A strategy that refused
    the next candle of a resumed pair, or that aged out a paused pair's OB,
    would satisfy the type signature and still be unusable.

    THREE assets, all three forming an OB on bars 0-1, then CCC dark for bars
    2-5 while AAA completes an entire trade.
    """

    ASSETS = ("AAAUSD", "BBBUSD", "CCCUSD")
    TICKS = {a: Decimal("0.5") for a in ASSETS}
    PLAN = {
        0: {a: S_ORIGIN for a in ASSETS},
        1: {a: S_BOS for a in ASSETS},
        2: {"AAAUSD": S_INERT, "BBBUSD": S_INERT},            # CCC OFF
        3: {"AAAUSD": S_INERT, "BBBUSD": S_INERT},            # CCC OFF
        4: {"AAAUSD": S_TOUCH, "BBBUSD": S_INERT},            # CCC OFF
        5: {"AAAUSD": S_ENTRY, "BBBUSD": S_INERT},            # CCC OFF, AAA fills
        6: {"AAAUSD": S_TP, "BBBUSD": S_INERT, "CCCUSD": S_INERT},   # CCC back ON
        7: {"AAAUSD": S_INERT, "BBBUSD": S_INERT, "CCCUSD": S_TOUCH},
        8: {"AAAUSD": S_INERT, "BBBUSD": S_INERT, "CCCUSD": S_ENTRY},
    }

    def _run_plan(self):
        strategy = _strategy(self.ASSETS, self.TICKS)
        return strategy, _feed(strategy, self.PLAN, self.ASSETS)

    def test_the_running_pairs_are_unaffected_by_the_paused_one(self):
        """AAA arms, fills and takes profit over bars 4-6 with CCC dark."""
        strategy, evals = self._run_plan()
        assert _bars_with(evals["AAAUSD"], ARMED) == [4]
        assert _bars_with(evals["AAAUSD"], FILLED) == [5]
        assert _bars_with(evals["AAAUSD"], CLOSED) == [6]
        exit_, = strategy.lifecycle.exits
        assert (exit_.ob_id, exit_.outcome) == ("MANUAL_AAAUSD_SHORT_0_1",
                                               "FILLED_TP")

    def test_the_paused_pairs_ob_is_neither_advanced_nor_invalidated(self):
        strategy, evals = self._run_plan()
        #: Nothing at all happened to CCC while it was dark: no event carries
        #: its ob id on bars 2-5, because it was never evaluated on them.
        assert _bars_with(evals["CCCUSD"], INVALID) == []
        assert [ev.bar_idx for ev in evals["CCCUSD"]] == [0, 1, 6, 7, 8]

    def test_the_resumed_pair_arms_and_fills_on_its_own_first_touch(self):
        _, evals = self._run_plan()
        assert _bars_with(evals["CCCUSD"], ARMED) == [7]
        assert _bars_with(evals["CCCUSD"], FILLED) == [8]

    def test_the_resumed_pairs_ob_is_the_one_created_before_the_pause(self):
        """
        The point of the whole class: the OB that trades on bar 8 is the OB the
        BOS created on bar 1, carried untouched across four dark candles.
        """
        strategy, _ = self._run_plan()
        ob = strategy.lifecycle.live_obs["MANUAL_CCCUSD_SHORT_0_1"]
        assert (ob.origin_bar_idx, ob.bos_bar_idx) == (0, 1)
        assert ob.state is ManualOBState.TRADE_ACTIVE
        assert ob.limit_active_from_bar == 7
        assert ob.ob_age_at_entry_hours == 7.0

    def test_a_pair_that_never_resumes_keeps_its_untouched_ob(self):
        """BBB is fed throughout and simply never touches; CCC was paused. The
        two are indistinguishable to the pool, which is the correct outcome."""
        strategy, _ = self._run_plan()
        bbb = strategy.lifecycle.live_obs["MANUAL_BBBUSD_SHORT_0_1"]
        assert bbb.state is ManualOBState.AWAITING_DISPLACEMENT
        assert bbb.limit_active_from_bar is None

    def test_a_five_hundred_candle_pause_is_still_only_a_pause(self):
        """
        The paused pair's watermark falls hundreds of bars behind the others and
        the resumed candle is still accepted, because the global guard refuses a
        candle that PREDATES the last processed timestamp, not one that arrives
        after a gap.
        """
        assets = ("AAAUSD", "CCCUSD")
        strategy = _strategy(assets, {a: Decimal("0.5") for a in assets})
        _feed(strategy, {0: {a: S_ORIGIN for a in assets},
                         1: {a: S_BOS for a in assets}}, assets)
        for bar_idx in range(2, 502):                     # CCC dark throughout
            strategy.evaluate_closed_candle("AAAUSD", bar_idx, _ts(bar_idx),
                                            *S_INERT)
        ccc = strategy.lifecycle.live_obs["MANUAL_CCCUSD_SHORT_0_1"]
        assert ccc.state is ManualOBState.AWAITING_DISPLACEMENT
        assert strategy.watermark.last("CCCUSD").bar_idx == 1
        assert strategy.watermark.last("AAAUSD").bar_idx == 501
        resumed = strategy.evaluate_closed_candle("CCCUSD", 502, _ts(502),
                                                  *S_TOUCH)
        assert [e.event_type for e in resumed.events] == [ARMED]
        filled = strategy.evaluate_closed_candle("CCCUSD", 503, _ts(503),
                                                 *S_ENTRY)
        assert [e.event_type for e in filled.events] == [FILLED]

    def test_a_candle_older_than_the_last_processed_one_is_still_refused(self):
        """
        Pausing a pair must not become a licence to replay history on it. The
        global order guard is what makes the shared trade slot meaningful, and
        it survives the pause.
        """
        from quantedge.strategy.manual_smc.strategy import GlobalOrderError
        assets = ("AAAUSD", "CCCUSD")
        strategy = _strategy(assets, {a: Decimal("0.5") for a in assets})
        _feed(strategy, {0: {a: S_ORIGIN for a in assets},
                         1: {a: S_BOS for a in assets}}, assets)
        for bar_idx in range(2, 10):
            strategy.evaluate_closed_candle("AAAUSD", bar_idx, _ts(bar_idx),
                                            *S_INERT)
        with pytest.raises(GlobalOrderError):
            strategy.evaluate_closed_candle("CCCUSD", 5, _ts(5), *S_TOUCH)


class TestProductionPolicyDefaults:
    """
    The shipped defaults ARE the specification. A bare lifecycle is still the
    research engine reproducing the frozen oracle, and that split is deliberate:
    production policy is injected at the production entry points, never bolted
    onto the object the provenance gate measures.
    """

    def test_the_strategy_defaults_to_first_touch_activation(self):
        assert _strategy().lifecycle.activation_mode == ACTIVATION_MODE_FIRST_TOUCH

    def test_the_strategy_defaults_to_a_three_candle_window(self):
        assert _strategy().lifecycle.entry_window_candles == 3

    def test_the_strategy_defaults_to_the_production_config(self):
        assert _strategy().cfg == manual_smc_production_config()
        assert _strategy().cfg.fixed_tp_market_pct == 0.60

    def test_the_backtest_driver_defaults_to_the_same_policy(self):
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        assert driver.strategy.lifecycle.activation_mode == ACTIVATION_MODE_FIRST_TOUCH
        assert driver.strategy.lifecycle.entry_window_candles == 3
        assert driver.cfg.fixed_tp_market_pct == 0.60

    def test_a_bare_lifecycle_is_still_the_research_engine(self):
        research = ManualSMCLifecycle()
        assert research.activation_mode == ACTIVATION_MODE_ORACLE_C
        assert research.cfg == ManualSpecConfig()
        assert research.cfg.fixed_tp_market_pct == 0.60

    def test_the_two_configs_now_agree_in_every_field(self):
        """
        The authorized production take profit IS the oracle's 0.60%, so today the
        production config differs from the research config in NO field at all.

        That equality is a CONSEQUENCE of the current authorization, not a
        guarantee. The seam still exists so an AUTHORIZED change to
        `MANUAL_SMC_FIXED_TP_PCT` reaches every production entry point without
        mutating the oracle-pinned default that the provenance gate in
        `test_manual_smc_oracle_equivalence.py` measures. The assertion is an
        exact set equality, so a drift in ANY field — the take profit included —
        still has to fail here.
        """
        production = manual_smc_production_config()
        research = ManualSpecConfig()
        differing = {f for f in vars(production)
                     if getattr(production, f) != getattr(research, f, None)}
        assert differing == set()
        assert production == research
        assert production.fixed_tp_market_pct == MANUAL_SMC_FIXED_TP_PCT == 0.60


# The realistic-price fixture, driven to its first touch. Geometry: entry
# 78050.0, SL 78200.0, TP 77581.7 -> quantized conservatively to 77582.0 on
# BTC's 0.5 tick grid.
BTC_TO_FIRST_TOUCH = [E_ORIGIN, E_BOS, E_INERT, E_TOUCH]
BTC_SETUP_ID = "BTCUSD_1h_MANUAL_SMC_MANUAL_BTCUSD_SHORT_0_1_SHORT"


def _adapt_btc() -> Tuple[ManualSMCStrategy, List[object]]:
    """Every candle of the realistic fixture, translated at the boundary."""
    strategy, evals = _run(BTC_TO_FIRST_TOUCH, ticks={"BTCUSD": BTC_TICK})
    adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
    return strategy, [adapter.adapt(ev) for ev in evals]


class TestAdapterBoundary:
    """
    What Manual SMC actually publishes to execution, candle by candle.

    READY appears on the first-touch candle and not before, the legs are the
    quantized 25%/opposite-edge/0.60% bracket, and `quantity` is deliberately
    left unset: sizing belongs to the frozen `CapitalAllocator`, and a base-asset
    quantity leaking into a contract-count field is the one mistake this boundary
    exists to prevent.
    """

    def test_nothing_is_published_before_an_ob_exists(self):
        _, adaptations = _adapt_btc()
        assert adaptations[0].decisions[0].setup_state is SetupState.NO_SETUP
        assert adaptations[0].ready_decisions == ()

    def test_a_created_ob_is_published_as_watching_not_ready(self):
        _, adaptations = _adapt_btc()
        for bar_idx in (1, 2):
            decision, = adaptations[bar_idx].decisions
            assert decision.setup_state is SetupState.WATCHING_OB
            assert adaptations[bar_idx].ready_decisions == ()

    def test_the_first_touch_candle_publishes_exactly_one_ready_decision(self):
        _, adaptations = _adapt_btc()
        ready, = adaptations[3].ready_decisions
        assert ready.setup_state is SetupState.TRADE_SETUP_READY
        assert ready.setup_id == BTC_SETUP_ID

    def test_the_ready_decision_carries_the_quantized_bracket(self):
        _, adaptations = _adapt_btc()
        ready, = adaptations[3].ready_decisions
        assert ready.direction is StrategyDirection.SHORT
        assert (ready.entry, ready.stop_loss, ready.take_profit) == (
            Decimal("78050.0"), Decimal("78200.0"), Decimal("77582.0"))

    def test_quantization_never_moves_the_strategys_own_geometry(self):
        strategy, adaptations = _adapt_btc()
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.tp_price == pytest.approx(77581.7)        # unrounded, internally
        assert adaptations[3].ready_decisions[0].take_profit == Decimal("77582.0")

    def test_the_ready_decision_leaves_the_quantity_unset(self):
        _, adaptations = _adapt_btc()
        assert adaptations[3].ready_decisions[0].quantity is None

    def test_the_ready_decision_publishes_leverage_intent_only(self):
        strategy, adaptations = _adapt_btc()
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.applied_leverage == 100.0                 # SL is 0.192% away
        assert adaptations[3].ready_decisions[0].calculated_leverage == 100

    def test_the_manual_smc_identity_travels_with_the_decision(self):
        _, adaptations = _adapt_btc()
        ready, = adaptations[3].ready_decisions
        assert (ready.strategy_name, ready.strategy_version) == ("MANUAL_SMC", "1.0.0")

    def test_the_decision_names_the_symbol_and_timeframe_it_was_measured_on(self):
        _, adaptations = _adapt_btc()
        ready, = adaptations[3].ready_decisions
        assert (ready.symbol, ready.timeframe) == ("BTCUSD", "1h")

    def test_a_ready_decision_is_never_published_with_a_missing_leg(self):
        _, adaptations = _adapt_btc()
        for adaptation in adaptations:
            for decision in adaptation.ready_decisions:
                assert None not in (decision.entry, decision.stop_loss,
                                    decision.take_profit, decision.setup_id)


def _resting_setup() -> Tuple[ManualSMCEvaluation, object]:
    """The real first-touch evaluation and its one resting setup."""
    strategy, evals = _run(BTC_TO_FIRST_TOUCH, ticks={"BTCUSD": BTC_TICK})
    evaluation = evals[3]
    setup, = [s for s in evaluation.setups
              if s.state is ManualOBState.LIMIT_RESTING]
    return evaluation, setup


def _decide(setup) -> object:
    evaluation, _ = _resting_setup()
    return decision_from_setup(
        setup, symbol="BTCUSD", timeframe="1h", ts=evaluation.ts,
        config=manual_smc_production_config(),
        trade_slot_taken=False, entry_refused_this_candle=False)


class TestReadyFailsClosed:
    """
    The three legs are published TOGETHER or not at all, and only when they
    provably descend from this OB's own geometry.

    `TestAdapterBoundary` observes a happy path; this class attacks it. The
    subject is a REAL resting setup taken off the engine, mutated one field at a
    time with `dataclasses.replace`, so what is under test is the boundary's
    refusal and not a hand-built fixture's plausibility.
    """

    def test_the_untouched_subject_is_ready_so_the_attacks_mean_something(self):
        _, setup = _resting_setup()
        assert _decide(setup).setup_state is SetupState.TRADE_SETUP_READY

    def test_no_bracket_means_no_ready_and_no_prices_at_all(self):
        _, setup = _resting_setup()
        decision = _decide(replace(setup, quantized=None))
        assert decision.setup_state is not SetupState.TRADE_SETUP_READY
        assert (decision.entry, decision.stop_loss, decision.take_profit) == \
               (None, None, None)
        assert decision.take_profit_price is None
        assert MISSING_BRACKET_REFUSAL in decision.reasons

    @pytest.mark.parametrize("leg,role", [("raw_entry_price", "entry"),
                                          ("raw_sl_price", "stop_loss"),
                                          ("raw_tp_price", "take_profit")])
    def test_a_leg_that_does_not_descend_from_this_ob_is_refused(self, leg, role):
        """
        Each leg individually: an entry, a stop or a take profit that disagrees
        with the setup's own geometry raises instead of being published. This is
        why a "missing" or substituted leg cannot become `TRADE_SETUP_READY` —
        the boundary never gets far enough to build the decision.
        """
        _, setup = _resting_setup()
        bracket = setup.quantized
        corrupted = replace(bracket, **{leg: getattr(bracket, leg) + 1})
        with pytest.raises(InconsistentEvaluationError) as excinfo:
            _decide(replace(setup, quantized=corrupted))
        assert role in str(excinfo.value)

    def test_a_bracket_belonging_to_another_direction_is_refused(self):
        _, setup = _resting_setup()
        flipped = replace(setup.quantized, direction="LONG")
        with pytest.raises(InconsistentEvaluationError):
            _decide(replace(setup, quantized=flipped))

    def test_a_bracket_belonging_to_another_asset_is_refused(self):
        _, setup = _resting_setup()
        other = replace(setup.quantized, asset="ETHUSD")
        with pytest.raises(InconsistentEvaluationError):
            _decide(replace(setup, quantized=other))

    def test_direction_and_setup_type_survive_the_boundary(self):
        """Section 25 #38, both directions, mapped from the same source field."""
        _, short = _resting_setup()
        assert short.direction == "SHORT"
        decision = _decide(short)
        assert decision.direction is StrategyDirection.SHORT
        assert decision.setup_type == "BEARISH_OB_RETEST"
        assert decision.metadata["manual_direction"] == "SHORT"

    def test_the_long_direction_is_preserved_just_as_literally(self):
        strategy, evals = _run([L_ORIGIN, L_BOS, L_INERT, L_TOUCH],
                               ticks={"BTCUSD": BTC_TICK})
        setup, = [s for s in evals[-1].setups
                  if s.state is ManualOBState.LIMIT_RESTING]
        assert setup.direction == "LONG"
        adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
        decision, = adapter.adapt(evals[-1]).decisions
        assert decision.direction is StrategyDirection.LONG
        assert decision.setup_type == "BULLISH_OB_RETEST"
        assert decision.metadata["manual_direction"] == "LONG"

    def test_the_setup_id_names_the_strategy_the_symbol_and_the_ob(self):
        """Section 25 #40: identity is composed, never generated or reused."""
        _, setup = _resting_setup()
        setup_id = _decide(setup).setup_id
        assert setup_id == BTC_SETUP_ID
        for part in ("BTCUSD", "1h", "MANUAL_SMC", setup.ob_id, "SHORT"):
            assert part in setup_id

    def test_two_live_obs_get_two_different_setup_ids(self):
        """Uniqueness comes from `ob_id`, which carries the two bar indices."""
        strategy, evals = _run(TWO_OBS[:4], ticks={"BTCUSD": BTC_TICK})
        adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
        ids = {d.setup_id for d in adapter.adapt(evals[-1]).decisions}
        assert len(ids) == len(evals[-1].setups) >= 2


# ---------------------------------------------------------------------------
# The frozen execution path. Mocked at the Delta client and nowhere else, so
# every gate between the strategy and the wire really runs.
# ---------------------------------------------------------------------------
class _ExecutionStack:
    """Path A, assembled exactly as `test_phase5_8_single_trade_allocation` does."""

    def __init__(self, balance: str = "100000") -> None:
        self.sent: List[object] = []
        self.client = MagicMock()
        # The gateway rejects credentials shorter than five characters, so these
        # are named placeholders rather than empty strings.
        self.client._api_key = "MOCKED_TEST_KEY_MANUAL_SMC"
        self.client._api_secret = "MOCKED_TEST_SECRET_MANUAL_SMC"
        self.client.place_order = AsyncMock(side_effect=self._place)
        self.client.cancel_order = AsyncMock(return_value=True)

        self.store = LocalStateStore(account_id="acc")
        self.store.account.user_id = "u"
        self.store.account.total_equity = Decimal(balance)
        self.store.account.available_balance = Decimal(balance)
        self.store.account.algo_enabled = True
        self.store.account.kill_switch_active = False
        self.store.account.last_synced_at = datetime.now(timezone.utc)
        self.store.connection.connection_status = "CONNECTED"

        algo = AlgoConfigStore()
        algo.update_config(user_id="u", account_id="acc", algo_enabled=True,
                           kill_switch_active=False,
                           risk_per_trade_pct=Decimal("100.00"),
                           max_daily_loss_usd=Decimal("50000"))
        self.lock = SingleTradeLockManager()
        self.manager = TradeLifecycleManager(
            client=self.client, validation_gateway=OrderValidationGateway(),
            state_store=self.store, algo_config_store=algo,
            single_trade_lock=self.lock, capital_allocator=CapitalAllocator(),
            daily_loss_limit=Decimal("50000"))
        self.orchestrator = MarketScannerOrchestrator(
            lifecycle_manager=self.manager, single_trade_lock=self.lock,
            supported_symbols=["BTCUSD"])

    def _place(self, request):
        self.sent.append(request)
        return DeltaOrderResponse(
            id=1, client_order_id=request.client_order_id, user_id=1,
            product_id=request.product_id, product_symbol=request.product_symbol,
            side=request.side, order_type=request.order_type, size=request.size,
            unfilled_size=request.size, limit_price=request.limit_price,
            stop_price=request.stop_price, average_fill_price=None,
            state=OrderStatus.OPEN, reduce_only=request.reduce_only,
            created_at=datetime.now(timezone.utc))

    def scan(self, decision):
        return asyncio.run(self.orchestrator.scan_and_execute(
            account_id="acc", user_id="u", candidate_decisions=[decision]))


def _first_touch_decision():
    """The realistic BTC fixture's READY decision, straight off the touch candle."""
    _, adaptations = _adapt_btc()
    return adaptations[3].ready_decisions[0]


class TestEndToEndToTheExecutionBoundary:
    """
    One deterministic run from candles to the order that would go on the wire.

    Nothing between the strategy and the client is stubbed: the orchestrator's
    capital allocator, the lifecycle manager's anti-tampering and geometry checks,
    the validation gateway and the single-trade lock all execute. The only mock is
    `place_order`, which records the request instead of sending it.
    """

    def test_exactly_one_order_reaches_the_client(self):
        stack = _ExecutionStack()
        result = stack.scan(_first_touch_decision())
        assert result.rejection_reason is None
        assert len(stack.sent) == 1

    def test_the_order_is_the_manual_smc_bracket_on_the_verified_product(self):
        stack = _ExecutionStack()
        stack.scan(_first_touch_decision())
        request, = stack.sent
        assert (request.product_id, request.product_symbol) == (27, "BTCUSD")
        assert (request.side, request.order_type) == (OrderSide.SELL,
                                                      OrderType.LIMIT_ORDER)
        assert request.limit_price == Decimal("78050.0")
        assert request.stop_loss_price == Decimal("78200.0")
        assert request.take_profit_price == Decimal("77582.0")

    def test_the_order_size_is_a_whole_number_of_contracts(self):
        stack = _ExecutionStack()
        stack.scan(_first_touch_decision())
        request, = stack.sent
        assert request.size == Decimal("125560")
        # The frozen serializer refuses anything that is not exactly a positive
        # whole contract count, so this is the value that would reach the wire.
        assert request.exchange_contract_count() == 125560

    def test_the_contract_count_matches_the_frozen_notional_arithmetic(self):
        stack = _ExecutionStack()
        stack.scan(_first_touch_decision())
        request, = stack.sent
        # size x contract_value x price, margined at 100x against $100k.
        notional = Decimal(request.size) * Decimal("0.001") * Decimal("78050.0")
        assert notional / Decimal("100") <= Decimal("100000")
        assert notional / Decimal("100") > Decimal("97000")

    def test_manual_smc_never_supplies_the_quantity_itself(self):
        decision = _first_touch_decision()
        assert decision.quantity is None
        stack = _ExecutionStack()
        stack.scan(decision)
        assert decision.quantity == Decimal("125560")     # set by the allocator

    def test_the_record_reaches_entry_submitted(self):
        stack = _ExecutionStack()
        record = stack.scan(_first_touch_decision()).executed_record
        assert record.state is TradeLifecycleState.ENTRY_SUBMITTED
        assert record.rejection_code is None

    def test_the_identity_survives_to_the_lifecycle_record(self):
        stack = _ExecutionStack()
        record = stack.scan(_first_touch_decision()).executed_record
        assert (record.strategy_name, record.strategy_version) == ("MANUAL_SMC", "1.0.0")

    def test_the_frozen_single_trade_lock_ends_held_by_this_setup(self):
        stack = _ExecutionStack()
        stack.scan(_first_touch_decision())
        assert stack.lock.is_locked("u", "acc") == (True, BTC_SETUP_ID, "BTCUSD")

    def test_a_second_scan_is_refused_while_the_lock_is_held(self):
        stack = _ExecutionStack()
        stack.scan(_first_touch_decision())
        again = stack.scan(_first_touch_decision())
        assert again.executed_record is None
        assert "locked with active trade" in again.rejection_reason
        assert len(stack.sent) == 1


def _sub_hundred_leverage_decision():
    """The M_ fixture's READY decision, whose leverage intent is 87x, not 100x."""
    strategy = _strategy(("BTCUSD",), {"BTCUSD": BTC_TICK})
    adapter = ManualSMCAdapter(config=strategy.cfg, timeframe="1h")
    adaptations = [adapter.adapt(strategy.evaluate_closed_candle(
        "BTCUSD", b, _ts(b), *row))
        for b, row in enumerate([M_ORIGIN, M_BOS, M_INERT, M_TOUCH])]
    return strategy, adaptations[3].ready_decisions[0]


class TestTheLeverageIntentAndTheRestingLimitReachTheOrder:
    """
    Leverage and order-type intent are the two things Manual SMC states that it
    does not itself act on, so both need proving at the far end.

    `trade_lifecycle.py:456` reads `decision.calculated_leverage or 100`. On the
    E_ fixture the Manual SMC intent IS 100x, so that path cannot distinguish a
    preserved value from the fallback. This class therefore uses an OB whose stop
    is 0.400% away — leverage 87x, and still inside the frozen 1.5 R:R gate,
    though at 0.60% TP its quantized R:R is EXACTLY 1.5, the gate's own boundary —
    and checks the contract count the allocator actually produced.
    """

    def test_the_fixture_really_does_ask_for_less_than_100x(self):
        strategy, decision = _sub_hundred_leverage_decision()
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.sl_dist_pct == pytest.approx(0.400)
        assert ob.applied_leverage == pytest.approx(87.5)
        assert decision.calculated_leverage == 87
        #: The exact fractional intent is retained beside the floored int.
        assert decision.metadata["applied_leverage"] == "87.5"
        assert decision.metadata["leverage_truncated_to_int"] is True

    def test_the_allocator_sizes_on_87x_and_not_on_the_100x_fallback(self):
        _, decision = _sub_hundred_leverage_decision()
        stack = _ExecutionStack()
        assert stack.scan(decision).rejection_reason is None
        request, = stack.sent
        assert request.size == Decimal("109307")
        notional = Decimal(request.size) * Decimal("0.001") * Decimal("78000.0")
        #: 98% of the $100k balance is usable margin, so 87x consumes almost all
        #: of it. Had the fallback been used the count would have been 125641.
        assert notional / Decimal("87") <= Decimal("98000")
        assert notional / Decimal("87") > Decimal("97900")
        assert request.size < Decimal("125641")

    def test_the_order_is_a_resting_limit_at_the_25_percent_level(self):
        _, decision = _sub_hundred_leverage_decision()
        assert decision.metadata["entry_order_type"] == "LIMIT"
        assert decision.metadata["entry_is_resting_limit"] is True
        assert "NO_TIME_BASED_EXPIRY" in \
               decision.metadata["resting_order_expiry_policy"]
        stack = _ExecutionStack()
        stack.scan(decision)
        request, = stack.sent
        assert request.order_type is OrderType.LIMIT_ORDER
        assert request.limit_price == Decimal("78000.0")

    def test_manual_smc_itself_never_calls_the_client(self):
        """
        The forbidden architecture, stated as a test: the strategy and the
        adapter run to completion with a client that would record any call, and
        the client stays untouched until the ORCHESTRATOR is invoked.
        """
        stack = _ExecutionStack()
        _, decision = _sub_hundred_leverage_decision()
        assert stack.client.place_order.await_count == 0
        assert stack.client.cancel_order.await_count == 0
        stack.scan(decision)
        assert stack.client.place_order.await_count == 1


class TestFrozenRiskRewardGate:
    """
    An OBSERVATION, pinned so it cannot change unnoticed, not a rule Manual SMC
    chose.

    Path A's validation gateway requires R:R >= 1.5. A flat 0.60% take profit
    against a stop at the far OB edge gives R:R = 0.0060 x entry / (0.75 x width),
    so any OB wider than about 0.533% of price is un-executable through Path A.
    (At the withdrawn 0.65% the same arithmetic allowed up to ~0.578%, so the
    authorized 0.60% TP TIGHTENS this ceiling — a consequence that is recorded
    here, not hidden.) The reference fixture (6.0 wide at ~100) is such an OB and
    is rejected before any order is built; the realistic fixture (200 wide at
    ~78,000) is not.

    Neither side of this can be fixed here: the take profit is specified as a flat
    percentage and must not be derived from R:R, and the gateway is frozen. The
    rejection is fail-closed — no order, and the trade lock is released.
    """

    def test_a_wide_ob_is_rejected_before_any_order_is_built(self):
        strategy, evals = _run(
            [S_ORIGIN, S_BOS, S_INERT, S_TOUCH], ticks={"BTCUSD": BTC_TICK})
        decision = ManualSMCAdapter(
            config=strategy.cfg, timeframe="1h").adapt(evals[3]).ready_decisions[0]
        stack = _ExecutionStack()
        record = stack.scan(decision).executed_record
        assert record.state is TradeLifecycleState.ENTRY_REJECTED
        assert record.rejection_code == "INVALID_RISK_REWARD"
        assert stack.sent == []

    def test_the_rejection_releases_the_trade_lock(self):
        strategy, evals = _run(
            [S_ORIGIN, S_BOS, S_INERT, S_TOUCH], ticks={"BTCUSD": BTC_TICK})
        decision = ManualSMCAdapter(
            config=strategy.cfg, timeframe="1h").adapt(evals[3]).ready_decisions[0]
        stack = _ExecutionStack()
        stack.scan(decision)
        assert stack.lock.is_locked("u", "acc") == (False, None, None)

    def test_the_narrow_realistic_ob_clears_the_same_gate(self):
        stack = _ExecutionStack()
        record = stack.scan(_first_touch_decision()).executed_record
        assert record.state is TradeLifecycleState.ENTRY_SUBMITTED

    def test_the_boundary_is_the_ob_width_relative_to_price(self):
        wide, _ = _run([S_ORIGIN, S_BOS])
        narrow, _ = _run([E_ORIGIN, E_BOS])
        a = wide.lifecycle.live_obs[SHORT_OB_ID]
        b = narrow.lifecycle.live_obs[SHORT_OB_ID]
        assert a.ob_width / a.entry_price > Decimal("0.00533")
        assert b.ob_width / b.entry_price < Decimal("0.00533")


# ===========================================================================
# Section 17: a backtest must NOT start with an empty OB pool
# ===========================================================================
#: Origin+BOS on bars 0-1, then a long quiet stretch, then the first touch and
#: the fill. Splitting the SAME rows at bar 4 puts creation strictly inside the
#: preload and the touch strictly inside the measured window, so the two runs
#: below differ in nothing except whether the pool was warmed.
PRELOAD_ROWS: List[Row] = [
    S_ORIGIN, S_BOS, S_INERT, S_INERT,
    S_TOUCH, S_ENTRY, S_TP, S_TP,
]
PRELOAD_SPLIT_BAR = 4


def _candles(rows: Sequence[Row], first_bar: int = 0):
    from quantedge.strategy.manual_smc.backtest import Candle
    return [Candle(bar_idx=first_bar + i, ts=_ts(first_bar + i),
                   open=o, high=h, low=l, close=c)
            for i, (o, h, l, c) in enumerate(rows)]


def _timeline(candles, symbol: str = "BTCUSD"):
    from quantedge.strategy.manual_smc.backtest import TimelineRow
    return [TimelineRow(c.ts, symbol, c.bar_idx) for c in candles]


def _driver_run(driver, candles, symbol: str = "BTCUSD"):
    return driver.run(_timeline(candles, symbol), {symbol: candles})


class TestHistoricalPreloadOfUntouchedOBs:
    """
    Section 17: an initialisation that starts with `active_OBs = empty` throws
    away every OB the market had already left behind, and those are exactly the
    OBs this strategy trades. Proved by running the SAME candles two ways.
    """

    def test_a_cold_started_driver_begins_with_no_order_blocks(self):
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        assert driver.strategy.lifecycle.live_obs == {}
        assert driver.trades == ()

    def test_a_cold_start_at_the_split_bar_never_sees_the_order_block(self):
        """The OB was created before the window opened, so it does not exist."""
        cold = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        measured = _candles(PRELOAD_ROWS, 0)[PRELOAD_SPLIT_BAR:]
        _driver_run(cold, measured)
        assert cold.strategy.lifecycle.live_obs == {}
        assert cold.trades == ()

    def test_the_preload_leaves_an_untouched_order_block_in_the_pool(self):
        warm = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        preload = _candles(PRELOAD_ROWS, 0)[:PRELOAD_SPLIT_BAR]
        _driver_run(warm, preload)
        assert list(warm.strategy.lifecycle.live_obs) == [SHORT_OB_ID]
        assert warm.trades == ()
        ob = warm.strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.limit_active_from_bar is None

    def test_the_preloaded_pool_produces_the_trade_the_cold_start_missed(self):
        warm = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        rows = _candles(PRELOAD_ROWS, 0)
        _driver_run(warm, rows[:PRELOAD_SPLIT_BAR])
        assert warm.trades == ()
        _driver_run(warm, rows[PRELOAD_SPLIT_BAR:])
        assert len(warm.trades) == 1
        trade = warm.trades[0]
        assert trade.ob_id == SHORT_OB_ID
        assert trade.origin_bar_idx == 0
        assert trade.fill_bar_idx == PRELOAD_SPLIT_BAR + 1
        assert trade.outcome == "FILLED_TP"

    def test_the_inherited_order_block_predates_the_measured_window(self):
        """`formation_dt < window start <= fill_dt` is the inheritance test."""
        warm = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        rows = _candles(PRELOAD_ROWS, 0)
        _driver_run(warm, rows[:PRELOAD_SPLIT_BAR])
        _driver_run(warm, rows[PRELOAD_SPLIT_BAR:])
        trade = warm.trades[0]
        boundary = _ts(PRELOAD_SPLIT_BAR)
        assert trade.formation_dt < boundary <= trade.fill_dt
        #: Age is measured from the BOS candle (bar 1) to the fill (bar 5).
        assert trade.ob_age_at_entry_hours == 4.0
        assert trade.bos_bar_idx == 1 and trade.fill_bar_idx == 5

    def test_a_split_run_is_identical_to_one_continuous_run(self):
        """The preload is a warm-up, not a second strategy."""
        rows = _candles(PRELOAD_ROWS, 0)
        split = ManualSMCBacktest(symbols=("BTCUSD",),
                                  tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        _driver_run(split, rows[:PRELOAD_SPLIT_BAR])
        _driver_run(split, rows[PRELOAD_SPLIT_BAR:])
        whole = ManualSMCBacktest(symbols=("BTCUSD",),
                                  tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        _driver_run(whole, rows)
        assert len(split.trades) == len(whole.trades) == 1
        a, b = split.trades[0], whole.trades[0]
        assert (a.ob_id, a.fill_bar_idx, a.exit_bar_idx, a.outcome) == \
               (b.ob_id, b.fill_bar_idx, b.exit_bar_idx, b.outcome)
        assert a.entry_price == b.entry_price
        assert a.sl_price == b.sl_price
        assert a.tp_price == b.tp_price
        assert a.ending_capital == b.ending_capital

    def test_the_ledger_records_the_activation_mode_it_actually_ran(self):
        """
        A production trade labelled `C_PROBE_PULLBACK` would be a provenance
        lie, so the driver reads the mode off the lifecycle instead of taking
        the dataclass default.
        """
        warm = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        _driver_run(warm, _candles(PRELOAD_ROWS, 0))
        assert warm.trades[0].displacement_mode == ACTIVATION_MODE_FIRST_TOUCH

    def test_new_order_blocks_keep_being_admitted_after_the_preload(self):
        """
        A preload that froze the pool would be a different bug from an empty
        one: the inherited OBs would trade and nothing new would ever join
        them. The measured window therefore ends with a SECOND origin/BOS pair,
        and the pool has to contain that new OB under its own identity while the
        inherited one has already been consumed by its fill.
        """
        rows = _candles(list(PRELOAD_ROWS) + [S_ORIGIN, S_BOS], 0)
        warm = ManualSMCBacktest(symbols=("BTCUSD",),
                                 tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        _driver_run(warm, rows[:PRELOAD_SPLIT_BAR])
        assert list(warm.strategy.lifecycle.live_obs) == [SHORT_OB_ID]
        _driver_run(warm, rows[PRELOAD_SPLIT_BAR:])
        assert len(warm.trades) == 1 and warm.trades[0].ob_id == SHORT_OB_ID
        live = warm.strategy.lifecycle.live_obs
        #: The new OB carries the bars it was actually built from, so its id is
        #: distinct from the inherited one by construction.
        assert list(live) == ["MANUAL_BTCUSD_SHORT_8_9"]
        assert SHORT_OB_ID not in live
        fresh = live["MANUAL_BTCUSD_SHORT_8_9"]
        assert (fresh.origin_bar_idx, fresh.bos_bar_idx) == (8, 9)
        assert fresh.limit_active_from_bar is None


# ---------------------------------------------------------------------------
# The two outcomes must never be conflated: a filled trade that stops out is a
# loss, an expired first-touch window is not a trade at all.
# ---------------------------------------------------------------------------
#: touch on bar 3, then three candles that enter the zone but stay short of the
#: 100.5 entry, so the window [3, 5] expires unfilled on bar 5.
EXPIRES_UNFILLED = [S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_NEAR, S_NEAR, S_INERT]
#: touch on bar 3, filled on bar 4, and then straight through the distal edge.
FILLS_THEN_STOPS = [S_ORIGIN, S_BOS, S_INERT, S_TOUCH, S_ENTRY, S_THROUGH]


class TestAnUnfilledSetupIsNeverRecordedAsALoss:
    """
    State 2 is not a losing trade.

    Both failure modes end with the OB gone from the pool, which is exactly why
    they are easy to conflate — and conflating them would corrupt every measured
    statistic at once: the trade count, the win rate, the R total and the equity
    curve. So the two are compared side by side, on the same fixture family,
    through the ledger that a report would actually be built from.

    `TestCaseAIsANormalStopLoss` proves the real stop-out records a loss; this
    class proves the unfilled window records nothing.
    """

    def _ledger(self, rows):
        driver = ManualSMCBacktest(
            symbols=("BTCUSD",), tick_specs={"BTCUSD": FakeSpec(BTC_TICK)})
        return driver, _driver_run(driver, _candles(rows))

    def test_the_window_really_does_expire_unfilled_on_this_fixture(self):
        _, evals = _run(EXPIRES_UNFILLED)
        assert _bars_with(evals, ARMED) == [3]
        assert _bars_with(evals, FILLED) == []
        #: The window is [3, 5]; bar 6 is the first bar past it, which is where
        #: the cancellation is reported — the same convention as
        #: `TestThreeCandleWindow.test_no_fill_on_window_candle_four`.
        assert _bars_with(evals, INVALID) == [6]

    def test_an_expired_window_puts_no_row_on_the_ledger(self):
        _, result = self._ledger(EXPIRES_UNFILLED)
        assert result.trades == ()
        assert result.invalidations == 1

    def test_an_expired_window_is_not_counted_as_a_loss(self):
        _, result = self._ledger(EXPIRES_UNFILLED)
        assert (result.overall.trades, result.overall.wins,
                result.overall.losses) == (0, 0, 0)
        assert result.overall.total_r == 0.0

    def test_an_expired_window_costs_the_account_nothing(self):
        """Not even a fee: no order was ever filled, so none was ever paid."""
        _, result = self._ledger(EXPIRES_UNFILLED)
        assert result.ending_capital == result.starting_capital
        assert result.max_drawdown_pct == 0.0

    def test_an_expired_window_leaves_no_exit_record(self):
        driver, _ = self._ledger(EXPIRES_UNFILLED)
        assert driver.strategy.lifecycle.exits == []
        assert driver.strategy.lifecycle.active_trade is None

    def test_the_real_stop_out_by_contrast_is_recorded_as_one_loss(self):
        """
        The control. Same OB, same touch, same distal edge — the only difference
        is that bar 4 reached the 25% entry first, so there was a position to
        stop out of.
        """
        driver, result = self._ledger(FILLS_THEN_STOPS)
        trade, = result.trades
        assert trade.outcome == "FILLED_SL"
        assert (result.overall.trades, result.overall.wins,
                result.overall.losses) == (1, 0, 1)
        assert result.overall.total_r == pytest.approx(-1.0)
        assert result.ending_capital < result.starting_capital
        exit_, = driver.strategy.lifecycle.exits
        assert exit_.realized_r == pytest.approx(-1.0)

    def test_the_two_outcomes_are_distinguishable_by_the_invalidation_counter(self):
        """
        An expiry increments `invalidations` and nothing else; a stop-out
        increments the trade ledger and NOT `invalidations`. Neither counter can
        absorb the other's event.
        """
        _, expired = self._ledger(EXPIRES_UNFILLED)
        _, stopped = self._ledger(FILLS_THEN_STOPS)
        assert (len(expired.trades), expired.invalidations) == (0, 1)
        assert (len(stopped.trades), stopped.invalidations) == (1, 0)












