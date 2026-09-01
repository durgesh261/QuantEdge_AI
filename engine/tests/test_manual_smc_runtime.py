"""TASK K -- Manual SMC production wiring.

These tests pin the *wiring layer only*: `quantedge.runtime.manual_smc_runtime`.
The strategy itself is already proven by `test_manual_smc_*.py`; nothing here
re-tests OB detection, sizing or the lifecycle for its own sake. What is
asserted is that the wiring is behaviourally transparent -- a closed candle
handed to the runtime reaches the one shared `ManualSMCStrategy`, and the
resulting decision reaches the existing Path-A orchestration unchanged.

Test group numbering follows the task's section 24 checklist.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.execution.models import DeltaOrderResponse, OrderStatus, OrderType
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments.registry import delta_india_registry
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_FIRST_TOUCH,
    ACTIVATION_MODE_ORACLE_C,
    ENTRY_WINDOW_CANDLES,
    ManualLifecycleEventType,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    ManualOBState,
    manual_smc_production_config,
)
from quantedge.strategy.manual_smc.strategy import (
    DuplicateCandleError,
    GlobalOrderError,
    ManualSMCStrategy,
    OutOfOrderCandleError,
)
from quantedge.strategy.models import SetupState, StrategyDirection

from quantedge.runtime import manual_smc_runtime as RT

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)   # a past 1H boundary
PAIRS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

# The proven synthetic SHORT sequence (same rows the first-touch suite uses):
# bar 0 origin, bar 1 BOS, bar 3 first touch -> limit, bar 4 entry filled.
S_ORIGIN = (100.0, 106.0, 99.0, 105.0)
S_BOS = (104.0, 104.5, 97.0, 98.0)
S_INERT = (98.0, 98.0, 98.0, 98.0)
S_TOUCH = (98.5, 99.0, 98.0, 98.5)
S_ENTRY = (100.0, 100.5, 99.5, 100.0)
S_TP = (100.0, 100.0, 99.8, 100.0)
S_SL = (100.0, 107.0, 99.9, 106.5)
S_NEAR = (100.0, 100.4, 98.0, 100.0)   # inside the window, entry not reached

# A realistic BTC SHORT: entry 78000, SL 78312 (0.400%), applied leverage 87.5.
B_ORIGIN = (77950.0, 78400.0, 77896.0, 78312.0)
B_BOS = (78300.0, 78310.0, 77800.0, 77850.0)
B_INERT = (77800.0, 77800.0, 77800.0, 77800.0)
B_TOUCH = (77850.0, 77896.0, 77800.0, 77850.0)
B_ROWS = (B_ORIGIN, B_BOS, B_INERT, B_TOUCH)


def ts_at(bar: int) -> datetime:
    return BASE + timedelta(hours=bar)


def make_runtime(symbols=("BTCUSD",), **kw):
    """A runtime on the provenance-verified instrument registry."""
    kw.setdefault("account_balance", 10000.0)
    return RT.ManualSMCRuntime(symbols=list(symbols), timeframe="1h", **kw)


def feed(runtime, symbol, bar, row):
    """One closed candle, addressed by synthetic bar number."""
    return runtime.on_closed_candle(RT.ClosedCandle(
        symbol=symbol, ts=ts_at(bar), open=row[0], high=row[1],
        low=row[2], close=row[3]))


def payload_for(symbol, bar, row, local=True):
    """The exact dict shape `delta_websocket._parse_candle_from_ws` emits."""
    return {"symbol": f"{symbol}{RT.LOCAL_SYMBOL_SUFFIX}" if local else symbol,
            "timeframe": "1h", "timestamp": int(ts_at(bar).timestamp()),
            "open": Decimal(str(row[0])), "high": Decimal(str(row[1])),
            "low": Decimal(str(row[2])), "close": Decimal(str(row[3])),
            "volume": Decimal("1"), "is_closed": True}


def drive(runtime, plan, symbols=None):
    """`plan` maps bar -> {symbol: row}; symbols are fed alphabetically."""
    steps = []
    for bar in sorted(plan):
        for symbol in sorted(symbols or plan[bar]):
            row = plan[bar].get(symbol)
            if row is not None:
                steps.append(feed(runtime, symbol, bar, row))
    return steps


def event_names(steps):
    return [(s.symbol, e.event_type.name)
            for s in steps for e in s.evaluation.events]


class ExecutionStack:
    """The real Path-A stack with only the exchange transport mocked.

    Nothing here is a Manual SMC object: this is the frozen execution engine,
    assembled exactly as `test_manual_smc_first_touch_window.py` assembles it.
    """

    def __init__(self, balance="100000", symbols=PAIRS):
        self.sent = []
        self.client = MagicMock()
        self.client._api_key = "MOCKED_TEST_KEY_TASK_K"
        self.client._api_secret = "MOCKED_TEST_SECRET_TASK_K"
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
        self.algo = AlgoConfigStore()
        self.algo.update_config(
            user_id="u", account_id="acc", algo_enabled=True,
            kill_switch_active=False, risk_per_trade_pct=Decimal("100.00"),
            max_daily_loss_usd=Decimal("50000"))
        self.lock = SingleTradeLockManager()
        self.lifecycle = TradeLifecycleManager(
            client=self.client, validation_gateway=OrderValidationGateway(),
            state_store=self.store, algo_config_store=self.algo,
            single_trade_lock=self.lock, capital_allocator=CapitalAllocator(),
            daily_loss_limit=Decimal("50000"))
        self.orchestrator = MarketScannerOrchestrator(
            lifecycle_manager=self.lifecycle, single_trade_lock=self.lock,
            supported_symbols=list(symbols))

    def _place(self, request):
        self.sent.append(request)
        return DeltaOrderResponse(
            id=len(self.sent), client_order_id=request.client_order_id,
            user_id=1, product_id=request.product_id,
            product_symbol=request.product_symbol, side=request.side,
            order_type=request.order_type, size=request.size,
            unfilled_size=request.size, limit_price=request.limit_price,
            stop_price=request.stop_price, average_fill_price=None,
            state=OrderStatus.OPEN, reduce_only=request.reduce_only,
            created_at=datetime.now(timezone.utc))


RUNTIME_PATH = (Path(__file__).parent.parent / "src" / "quantedge" / "runtime"
                / "manual_smc_runtime.py")
RUNTIME_SRC = RUNTIME_PATH.read_text(encoding="utf-8")
RUNTIME_AST = ast.parse(RUNTIME_SRC)


# ---------------------------------------------------------------- 24.1 - 24.3
class TestRuntimeRegistration:

    def test_1_the_production_entry_point_registers_manual_smc(self):
        runtime = RT.build_manual_smc_runtime()
        assert isinstance(runtime.strategy, ManualSMCStrategy)
        assert runtime.strategy_name == MANUAL_SMC_STRATEGY_NAME
        main_src = (Path(__file__).parent.parent / "src" / "quantedge"
                    / "__main__.py").read_text(encoding="utf-8")
        assert "build_manual_smc_runtime" in main_src, (
            "the application entry point must actually register the runtime, "
            "otherwise Manual SMC is still not reachable from production")

    def test_2_it_is_instantiated_with_the_production_config(self):
        runtime = RT.build_manual_smc_runtime()
        cfg = runtime.strategy.cfg
        assert cfg.fixed_tp_market_pct == 0.60
        assert cfg.entry_depth_pct == 0.25
        assert cfg.applied_leverage_cap == 100.0
        assert cfg.max_sl_account_risk_pct == 35.0
        assert cfg == manual_smc_production_config()
        assert runtime.strategy.lifecycle.activation_mode == (
            ACTIVATION_MODE_FIRST_TOUCH)
        assert runtime.strategy.lifecycle.activation_mode != (
            ACTIVATION_MODE_ORACLE_C)
        assert runtime.strategy.lifecycle.entry_window_candles == 3
        assert ENTRY_WINDOW_CANDLES == 3
        assert ACTIVATION_MODE_ORACLE_C not in RUNTIME_SRC, (
            "the wiring layer must not even name the research activation mode")

    def test_3_every_configured_pair_is_registered_with_its_own_scanner(self):
        runtime = RT.build_manual_smc_runtime()
        assert runtime.symbols == PAIRS
        scanners = runtime.strategy.lifecycle._scanners
        assert set(scanners) == set(PAIRS)
        assert len({id(s) for s in scanners.values()}) == len(PAIRS)
        registry = delta_india_registry()
        for symbol in PAIRS:
            assert (runtime.tick_specs[symbol].tick_size
                    == registry.get(symbol).tick_size), (
                "tick sizes must come from the provenance-verified registry, "
                "never from a literal in the wiring layer")


# ---------------------------------------------------------------- 24.4 - 24.7
class TestClosedCandleFlow:

    def test_4_a_closed_candle_reaches_the_strategy_exactly_once(self):
        runtime = make_runtime()
        calls = []
        real = runtime.strategy.evaluate_closed_candle

        def spy(*a, **k):
            calls.append(a)
            return real(*a, **k)

        runtime.strategy.evaluate_closed_candle = spy
        step = feed(runtime, "BTCUSD", 0, S_ORIGIN)
        assert len(calls) == 1
        assert calls[0][0] == "BTCUSD"
        assert calls[0][1] == step.bar_idx == RT.bar_index_for(ts_at(0), "1h")
        assert calls[0][2] == ts_at(0)
        assert calls[0][3:] == S_ORIGIN
        assert step.evaluation is not None and step.adaptation is not None

    def test_5_a_forming_candle_can_never_reach_the_strategy(self):
        runtime = make_runtime()
        now = int(time.time())
        current_bar_start = now - (now % 3600)
        forming = {"symbol": "BTCUSD.P", "timeframe": "1h",
                   "timestamp": current_bar_start, "open": Decimal("1"),
                   "high": Decimal("2"), "low": Decimal("1"),
                   "close": Decimal("2"), "is_closed": False}
        with pytest.raises(RT.FormingCandleError):
            runtime.handle_feed_payload(forming)
        # the flag alone is not trusted: the clock is checked too
        with pytest.raises(RT.FormingCandleError):
            runtime.handle_feed_payload({**forming, "is_closed": True})
        assert runtime.strategy.watermark.last("BTCUSD") is None
        assert runtime.candles_processed == 0

    def test_5b_the_gate_is_the_same_contract_as_the_live_feed(self):
        from quantedge.market_data import delta_websocket as dws
        now = int(time.time())
        current_bar_start = now - (now % 3600)
        for candle_ts in (current_bar_start - 7200, current_bar_start - 3600,
                          current_bar_start, current_bar_start + 3600):
            assert (RT.is_candle_closed(candle_ts, "1h")
                    == dws._is_candle_closed(candle_ts)), candle_ts
        assert dws.SYMBOL_LOCAL == dws.SYMBOL_EXCHANGE + RT.LOCAL_SYMBOL_SUFFIX

    def test_6_the_bar_index_is_derived_from_the_closed_candle_clock(self):
        assert RT.bar_index_for(ts_at(0), "1h") + 1 == RT.bar_index_for(
            ts_at(1), "1h")
        assert RT.bar_index_for(ts_at(0), "1h") == int(
            ts_at(0).timestamp()) // 3600
        with pytest.raises(RT.CandleBoundaryError):
            RT.bar_index_for(ts_at(0) + timedelta(minutes=30), "1h")
        with pytest.raises(RT.CandleBoundaryError):
            RT.bar_index_for(datetime(2026, 1, 1), "1h")      # naive
        with pytest.raises(RT.TimeframeError):
            RT.bar_index_for(ts_at(0), "5m")

    def test_7_the_live_feed_payload_reaches_the_strategy(self):
        """The real DeltaWebSocketClient, driven with a real Delta message."""
        from quantedge.market_data.delta_websocket import DeltaWebSocketClient
        import tempfile
        runtime = make_runtime()
        tmp = Path(tempfile.mkdtemp())
        client = DeltaWebSocketClient(
            on_candle_closed=runtime.handle_feed_payload, persist=False,
            csv_path=tmp / "feed.csv", meta_path=tmp / "feed.meta.json")
        now = int(time.time())
        closed_start = (now - (now % 3600)) - 3600
        asyncio.run(client._handle_message({
            "type": "candlestick_1h", "symbol": "BTCUSD", "resolution": "1h",
            "open": 77950.0, "high": 78400.0, "low": 77896.0,
            "close": 78312.0, "volume": 372963.0,
            "candle_start_time": closed_start * 1_000_000,
            "timestamp": closed_start * 1_000_000}))
        mark = runtime.strategy.watermark.last("BTCUSD")
        assert mark is not None, (
            "a closed candle from the real websocket client must reach the "
            "one shared Manual SMC strategy")
        assert mark.bar_idx == closed_start // 3600
        assert runtime.candles_processed == 1


# --------------------------------------------------------------- 24.8 - 24.11
class TestMultiPairIndependence:

    def test_8_all_configured_pairs_scan_simultaneously(self):
        runtime = make_runtime(PAIRS)
        plan = {0: {p: S_ORIGIN for p in PAIRS},
                1: {p: S_BOS for p in PAIRS}}
        drive(runtime, plan)
        obs = runtime.active_obs()
        assert {ob.asset for ob in obs} == set(PAIRS)
        # separate OB pools, separate scanners, separate watermarks
        assert len({ob.ob_id for ob in obs}) == len(PAIRS)
        for p in PAIRS:
            assert len(runtime.active_obs(p)) == 1
            assert runtime.strategy.watermark.last(p).bar_idx == (
                RT.bar_index_for(ts_at(1), "1h"))
        scanners = runtime.strategy.lifecycle._scanners
        assert len({id(s) for s in scanners.values()}) == len(PAIRS)

    def test_8b_one_pair_advancing_does_not_move_another_pairs_index(self):
        runtime = make_runtime(("BTCUSD", "ETHUSD"))
        feed(runtime, "BTCUSD", 0, S_ORIGIN)
        feed(runtime, "BTCUSD", 1, S_BOS)
        feed(runtime, "ETHUSD", 1, S_ORIGIN)
        assert runtime.strategy.watermark.last("BTCUSD").bar_idx == (
            RT.bar_index_for(ts_at(1), "1h"))
        assert runtime.strategy.watermark.last("ETHUSD").bar_idx == (
            RT.bar_index_for(ts_at(1), "1h"))
        assert runtime.active_obs("ETHUSD") == ()
        assert len(runtime.active_obs("BTCUSD")) == 1

    def test_9_disabling_one_pair_stops_only_that_pair(self):
        runtime = make_runtime(PAIRS)
        drive(runtime, {0: {p: S_ORIGIN for p in PAIRS},
                        1: {p: S_BOS for p in PAIRS}})
        before = {ob.ob_id: (ob.state, ob.limit_active_from_bar)
                  for ob in runtime.active_obs("XRPUSD")}
        runtime.disable("XRPUSD")
        assert runtime.enabled_symbols == ("BTCUSD", "ETHUSD", "SOLUSD")
        step = feed(runtime, "XRPUSD", 2, S_TOUCH)
        assert step.skipped == RT.SKIPPED_DISABLED
        assert step.evaluation is None and step.adaptation is None
        for p in ("BTCUSD", "ETHUSD", "SOLUSD"):
            assert feed(runtime, p, 2, S_TOUCH).skipped is None
        # the disabled pair kept every OB and its watermark, untouched
        assert {ob.ob_id: (ob.state, ob.limit_active_from_bar)
                for ob in runtime.active_obs("XRPUSD")} == before
        assert len(runtime.active_obs("XRPUSD")) == 1
        assert runtime.strategy.watermark.last("XRPUSD").bar_idx == (
            RT.bar_index_for(ts_at(1), "1h"))
        # ... while the enabled pairs armed their limits on the same candle
        for p in ("BTCUSD", "ETHUSD", "SOLUSD"):
            assert runtime.active_obs(p)[0].state == ManualOBState.LIMIT_RESTING

    def test_10_re_enabling_resumes_from_the_correct_candle_state(self):
        runtime = make_runtime(("BTCUSD", "ETHUSD"))
        drive(runtime, {0: {p: S_ORIGIN for p in ("BTCUSD", "ETHUSD")},
                        1: {p: S_BOS for p in ("BTCUSD", "ETHUSD")}})
        runtime.disable("ETHUSD")
        for bar in (2, 3, 4, 5):
            feed(runtime, "BTCUSD", bar, S_INERT)
        runtime.enable("ETHUSD")
        assert runtime.is_enabled("ETHUSD")
        step = feed(runtime, "ETHUSD", 6, S_TOUCH)
        assert step.skipped is None
        names = [e.event_type.name for e in step.evaluation.events]
        assert names == ["FIRST_TOUCH_LIMIT_ACTIVATED"], (
            "the paused pair must resume from its own preserved OB state, not "
            "from a rebuilt or reset scanner")
        assert runtime.strategy.watermark.last("ETHUSD").bar_idx == (
            RT.bar_index_for(ts_at(6), "1h"))

    def test_11_duplicate_and_out_of_order_candles_are_still_refused(self):
        runtime = make_runtime(("BTCUSD",))
        feed(runtime, "BTCUSD", 100, S_INERT)
        with pytest.raises(DuplicateCandleError):
            feed(runtime, "BTCUSD", 100, S_INERT)
        assert feed(runtime, "BTCUSD", 101, S_INERT).skipped is None
        assert feed(runtime, "BTCUSD", 102, S_INERT).skipped is None
        with pytest.raises(OutOfOrderCandleError):
            feed(runtime, "BTCUSD", 99, S_INERT)
        assert runtime.strategy.watermark.last("BTCUSD").bar_idx == (
            RT.bar_index_for(ts_at(102), "1h"))

    def test_11b_a_stale_candle_on_another_pair_is_refused_globally(self):
        runtime = make_runtime(("BTCUSD", "ETHUSD"))
        feed(runtime, "BTCUSD", 10, S_INERT)
        with pytest.raises(GlobalOrderError):
            feed(runtime, "ETHUSD", 9, S_INERT)

    def test_11c_the_transport_edge_refuses_without_killing_the_feed(self):
        """A refusal must not propagate out of the websocket callback."""
        runtime = make_runtime(("BTCUSD",))
        callback = RT.make_feed_callback(runtime)
        payload = payload_for("BTCUSD", 0, S_ORIGIN)
        callback(payload)
        callback(payload)          # duplicate: logged and dropped, not raised
        assert runtime.candles_processed == 1
        assert runtime.candles_refused == 1


# -------------------------------------------------------------- 24.12 - 24.19
class TestLifecycleThroughTheRuntime:

    def test_12_the_bos_candle_creates_an_ob_and_trades_nothing(self):
        runtime = make_runtime()
        feed(runtime, "BTCUSD", 0, S_ORIGIN)
        step = feed(runtime, "BTCUSD", 1, S_BOS)
        assert [e.event_type for e in step.evaluation.events] == [
            ManualLifecycleEventType.OB_CREATED]
        ob = runtime.active_obs("BTCUSD")[0]
        assert ob.state == ManualOBState.AWAITING_DISPLACEMENT
        assert step.adaptation.ready_decisions == ()
        assert step.evaluation.active_trade is None
        assert step.submitted == ()

    def test_13_the_first_touch_arms_a_resting_limit_25_percent_in(self):
        runtime = make_runtime()
        feed(runtime, "BTCUSD", 0, S_ORIGIN)
        feed(runtime, "BTCUSD", 1, S_BOS)
        step = feed(runtime, "BTCUSD", 2, S_TOUCH)
        assert [e.event_type for e in step.evaluation.events] == [
            ManualLifecycleEventType.FIRST_TOUCH_LIMIT_ACTIVATED]
        setup = step.evaluation.setups[0]
        width = setup.ob_top - setup.ob_bottom
        assert setup.entry_price == pytest.approx(
            setup.ob_bottom + 0.25 * width)
        assert setup.state == ManualOBState.LIMIT_RESTING
        assert setup.sl_price == setup.ob_top          # opposite OB edge
        assert setup.tp_price == pytest.approx(setup.entry_price * (1 - 0.0060))

    def test_14_the_window_is_exactly_t_t1_t2_then_permanent(self):
        runtime = make_runtime()
        feed(runtime, "BTCUSD", 0, S_ORIGIN)
        feed(runtime, "BTCUSD", 1, S_BOS)
        feed(runtime, "BTCUSD", 2, S_TOUCH)          # T
        ob_id = runtime.active_obs("BTCUSD")[0].ob_id
        assert feed(runtime, "BTCUSD", 3, S_NEAR).evaluation.filled is None
        assert feed(runtime, "BTCUSD", 4, S_NEAR).evaluation.filled is None
        expired = feed(runtime, "BTCUSD", 5, S_NEAR)
        assert [e.event_type for e in expired.evaluation.events] == [
            ManualLifecycleEventType.INVALIDATED]
        assert expired.adaptation.invalidated_ob_ids == (ob_id,)
        assert runtime.active_obs("BTCUSD") == ()
        # a later return to the same zone can never reopen that window
        drive(runtime, {6: {"BTCUSD": S_TOUCH}, 7: {"BTCUSD": S_ENTRY}})
        assert ob_id not in {ob.ob_id for ob in runtime.active_obs()}
        assert runtime.strategy.lifecycle.active_trade is None
        assert runtime.strategy.lifecycle.exits == []

    def test_15_a_fill_uses_the_opposite_edge_sl_and_the_fixed_tp(self):
        runtime = make_runtime()
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                        2: {"BTCUSD": S_TOUCH}})
        setup = runtime.active_obs("BTCUSD")[0]
        step = feed(runtime, "BTCUSD", 3, S_ENTRY)
        fill = step.evaluation.filled
        assert fill is not None
        assert fill.entry_price == pytest.approx(
            setup.ob_bottom + 0.25 * (setup.ob_top - setup.ob_bottom))
        assert fill.sl_price == setup.ob_top
        assert fill.tp_price == pytest.approx(fill.entry_price * (1 - 0.0060))
        assert step.evaluation.active_trade is not None
        assert step.adaptation.filled is not None

    def test_16_case_a_a_filled_setup_stopped_out_is_a_real_loss(self):
        runtime = make_runtime()
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                        2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_ENTRY}})
        balance_before = runtime.strategy.account_balance
        step = feed(runtime, "BTCUSD", 4, S_SL)
        closed = step.evaluation.closed
        assert closed is not None
        assert closed.exit.outcome == "FILLED_SL"
        assert closed.exit.realized_r < 0
        assert closed.balance_after < balance_before
        assert runtime.strategy.lifecycle.exits[-1].outcome == "FILLED_SL"

    def test_17_case_b_an_unfilled_window_is_not_a_trade_at_all(self):
        runtime = make_runtime()
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                        2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_NEAR},
                        4: {"BTCUSD": S_NEAR}, 5: {"BTCUSD": S_NEAR}})
        assert runtime.strategy.lifecycle.exits == [], (
            "an expired window is not a loss: no exit row, no -1R, no fee")
        assert runtime.strategy.account_balance == pytest.approx(10000.0)
        assert runtime.strategy.lifecycle.active_trade is None

    def test_18_every_active_ob_is_retained_indefinitely(self):
        runtime = make_runtime()
        bar = 0
        for i in range(12):
            price = 1000.0 - 20 * i
            for row in ((price, price + 6, price - 1, price + 5),
                        (price + 4, price + 4.5, price - 3, price - 2)):
                feed(runtime, "BTCUSD", bar, row)
                bar += 1
        obs = runtime.active_obs("BTCUSD")
        assert len(obs) == 12, "no nearest-N cap may exist in the backend"
        assert all(ob.state == ManualOBState.AWAITING_DISPLACEMENT
                   for ob in obs)
        # ... and an untouched OB survives an arbitrary stretch of candles
        for extra in range(200):
            feed(runtime, "BTCUSD", bar, (700.0, 700.0, 700.0, 700.0))
            bar += 1
        assert len(runtime.active_obs("BTCUSD")) == 12

    def test_19_nearest_ten_is_a_view_and_never_a_deletion(self):
        runtime = make_runtime()
        bar = 0
        for i in range(12):
            price = 1000.0 - 20 * i
            for row in ((price, price + 6, price - 1, price + 5),
                        (price + 4, price + 4.5, price - 3, price - 2)):
                feed(runtime, "BTCUSD", bar, row)
                bar += 1
        view = runtime.nearest_obs("BTCUSD", 1000.0, limit=10)
        assert len(view) == 10
        assert [ob.ob_id for ob in view] == [
            ob.ob_id for ob in sorted(
                runtime.active_obs("BTCUSD"),
                key=lambda o: (RT.ob_distance(o, 1000.0), o.ob_id))[:10]]
        assert len(runtime.active_obs("BTCUSD")) == 12, (
            "the display query must not evict anything from the backend")
        assert len(runtime.strategy.lifecycle.live_obs) == 12


# -------------------------------------------------------------- 24.20 - 24.27
class TestExecutionBoundary:

    def build(self):
        stack = ExecutionStack()
        runtime = make_runtime(("BTCUSD",), orchestrator=stack.orchestrator)
        return stack, runtime

    def drive_btc(self, runtime, rows=B_ROWS):
        return [feed(runtime, "BTCUSD", bar, row)
                for bar, row in enumerate(rows)]

    async def run_last(self, runtime, rows=B_ROWS, user_id="u",
                       account_id="acc"):
        """Drive every candle but the last synchronously, then submit."""
        for bar, row in enumerate(rows[:-1]):
            feed(runtime, "BTCUSD", bar, row)
        last = len(rows) - 1
        return await runtime.process_closed_candle(
            RT.ClosedCandle("BTCUSD", ts_at(last), *rows[last]),
            user_id=user_id, account_id=account_id)

    def test_20_manual_smc_publishes_no_quantity(self):
        stack, runtime = self.build()
        steps = self.drive_btc(runtime)
        decision = steps[-1].adaptation.ready_decisions[0]
        assert decision.quantity is None, (
            "sizing is the frozen allocator's job; Manual SMC publishes only "
            "leverage intent")
        for pattern in ("quantity =", "quantity=", "contract_count",
                        "notional ="):
            assert pattern not in RUNTIME_SRC, pattern

    def test_21_ready_decisions_are_handed_to_the_existing_orchestrator(self):
        stack, runtime = self.build()
        seen = {}
        real = stack.orchestrator.scan_and_execute

        async def spy(**kwargs):
            seen.update(kwargs)
            return await real(**kwargs)

        stack.orchestrator.scan_and_execute = spy
        step = asyncio.run(self.run_last(runtime))
        assert seen["candidate_decisions"] == list(
            step.adaptation.ready_decisions)
        assert seen["account_id"] == "acc" and seen["user_id"] == "u"
        assert step.scan_result is not None
        assert step.scan_result.rejection_reason is None
        assert len(stack.sent) == 1

    def test_22_the_frozen_allocator_supplies_the_contract_count(self):
        stack, runtime = self.build()
        step = asyncio.run(self.run_last(runtime))
        decision = step.adaptation.ready_decisions[0]
        assert decision.quantity is not None, "the allocator must fill this in"
        assert decision.quantity == int(decision.quantity) >= 1
        request = stack.sent[0]
        assert Decimal(str(request.size)) == Decimal(str(decision.quantity))
        assert request.exchange_contract_count() == int(decision.quantity)

    def test_23_the_wiring_layer_owns_no_exchange_transport(self):
        imported = set()
        for node in ast.walk(RUNTIME_AST):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any("delta_client" in m or "websocket" in m
                       or "httpx" in m for m in imported), imported
        for banned in ("place_order", "cancel_order", "DeltaOrderRequest",
                       "exchange_contract_count", "CapitalAllocator",
                       "OrderValidationGateway", "TradeLifecycleManager",
                       "SingleTradeLockManager", "contract_value"):
            assert banned not in RUNTIME_SRC, (
                f"{banned} in the wiring layer would be a second execution "
                f"path")
        assert "quantedge.execution.market_orchestrator" in imported

    def test_23b_manual_smc_never_reaches_the_exchange_itself(self):
        stack, runtime = self.build()
        asyncio.run(self.run_last(runtime))
        assert stack.client.place_order.await_count == 1
        # every call came from the lifecycle manager, never from the strategy
        assert runtime.strategy.__class__.__module__.startswith(
            "quantedge.strategy.manual_smc")
        assert not hasattr(runtime.strategy, "client")
        assert not hasattr(runtime.adapter, "client")

    def test_24_the_bracket_identity_survives_the_boundary(self):
        stack, runtime = self.build()
        step = asyncio.run(self.run_last(runtime))
        decision = step.adaptation.ready_decisions[0]
        setup = step.evaluation.setups[0]
        assert decision.symbol == "BTCUSD"
        assert decision.direction == StrategyDirection.SHORT
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.setup_id and setup.ob_id in decision.setup_id
        assert float(decision.entry) == pytest.approx(setup.entry_price)
        assert float(decision.stop_loss) == pytest.approx(setup.sl_price)
        assert float(decision.take_profit) == pytest.approx(setup.tp_price)
        request = stack.sent[0]
        assert Decimal(str(request.limit_price)) == decision.entry
        assert Decimal(str(request.stop_loss_price)) == decision.stop_loss
        assert Decimal(str(request.take_profit_price)) == decision.take_profit

    def test_25_the_leverage_intent_is_what_sizes_the_order(self):
        stack, runtime = self.build()
        step = asyncio.run(self.run_last(runtime))
        decision = step.adaptation.ready_decisions[0]
        setup = step.evaluation.setups[0]
        assert decision.calculated_leverage == int(setup.applied_leverage)
        assert decision.calculated_leverage not in (10, 100), (
            "this fixture exists so the orchestrator's `or 10` and the "
            "lifecycle's `or 100` fallbacks cannot masquerade as preserved "
            "leverage")
        allocator = CapitalAllocator()
        from quantedge.execution.validation import DEFAULT_DELTA_INDIA_PRODUCTS
        spec = DEFAULT_DELTA_INDIA_PRODUCTS["BTCUSD"]

        def sized(leverage):
            return allocator.calculate_100_percent_allocation(
                symbol="BTCUSD", entry_price=decision.entry,
                available_balance=Decimal("100000"), leverage=leverage,
                contract_unit=spec.contract_value,
                lot_size_step=spec.size_step,
                min_quantity=spec.min_size).position_quantity

        expected = sized(decision.calculated_leverage)
        assert Decimal(str(stack.sent[0].size)) == expected
        assert sized(10) != expected and sized(100) != expected

    def test_26_the_resting_limit_intent_survives_the_boundary(self):
        stack, runtime = self.build()
        step = asyncio.run(self.run_last(runtime))
        decision = step.adaptation.ready_decisions[0]
        assert decision.metadata["entry_order_type"] == "LIMIT"
        assert decision.metadata["entry_is_resting_limit"] is True
        assert decision.metadata["resting_order_expiry_policy"]
        request = stack.sent[0]
        assert request.order_type is OrderType.LIMIT_ORDER
        assert request.limit_price is not None

    def test_27_there_is_exactly_one_order_path(self):
        stack, runtime = self.build()
        asyncio.run(self.run_last(runtime))
        assert len(stack.sent) == 1
        assert stack.client.place_order.await_count == 1
        assert stack.client.cancel_order.await_count == 0
        # the runtime holds no client, no allocator and no gateway of its own
        assert set(vars(runtime)) & {
            "client", "allocator", "capital_allocator", "gateway",
            "validation_gateway", "lifecycle_manager"} == set()
        assert runtime.orchestrator is stack.orchestrator


# -------------------------------------------------------------- 24.28 - 24.32
class TestRestartSafety:

    def test_28_a_snapshot_round_trip_preserves_every_live_ob(self, tmp_path):
        runtime = make_runtime(PAIRS, state_path=tmp_path / "manual_smc.json")
        drive(runtime, {0: {p: S_ORIGIN for p in PAIRS},
                        1: {p: S_BOS for p in PAIRS},
                        2: {"BTCUSD": S_TOUCH}})
        path = runtime.save_state()
        assert path.exists() and not list(tmp_path.glob("*.tmp"))
        restored = RT.ManualSMCRuntime.load_state(path)
        before = runtime.strategy.capture_state()
        assert restored.strategy.capture_state() == before
        assert restored.symbols == runtime.symbols
        assert {ob.ob_id: ob.state for ob in restored.active_obs()} == {
            ob.ob_id: ob.state for ob in runtime.active_obs()}
        for p in PAIRS:
            assert (restored.strategy.watermark.last(p).bar_idx
                    == runtime.strategy.watermark.last(p).bar_idx)
        from quantedge.strategy.manual_smc.state import encode_scanner
        assert (encode_scanner(
                    "BTCUSD", restored.strategy.lifecycle._scanners["BTCUSD"])
                == encode_scanner(
                    "BTCUSD", runtime.strategy.lifecycle._scanners["BTCUSD"])), (
            "consumed-origin state must survive the restart")

    def test_29_a_restored_runtime_matches_one_that_never_stopped(
            self, tmp_path):
        plan_head = {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS}}
        plan_tail = {2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_ENTRY},
                     4: {"BTCUSD": S_TP}, 5: {"BTCUSD": S_INERT}}
        continuous = make_runtime()
        drive(continuous, plan_head)
        tail_a = drive(continuous, plan_tail)

        crashed = make_runtime(state_path=tmp_path / "s.json")
        drive(crashed, plan_head)
        resumed = RT.ManualSMCRuntime.load_state(crashed.save_state())
        tail_b = drive(resumed, plan_tail)

        assert event_names(tail_b) == event_names(tail_a)
        assert [ (s.symbol, s.bar_idx) for s in tail_b] == [
            (s.symbol, s.bar_idx) for s in tail_a]
        assert ([(e.ob_id, e.outcome, e.realized_r)
                 for e in resumed.strategy.lifecycle.exits]
                == [(e.ob_id, e.outcome, e.realized_r)
                    for e in continuous.strategy.lifecycle.exits])
        assert (resumed.strategy.account_balance
                == pytest.approx(continuous.strategy.account_balance))
        assert resumed.strategy.capture_state() == continuous.strategy.capture_state()

    def test_30_an_open_trade_is_restored_with_its_slot_and_its_sizing(
            self, tmp_path):
        runtime = make_runtime(state_path=tmp_path / "s.json")
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                        2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_ENTRY}})
        assert runtime.strategy.lifecycle.active_trade is not None
        sizing_before = runtime.strategy._open_sizing
        resumed = RT.ManualSMCRuntime.load_state(runtime.save_state())
        trade = resumed.strategy.lifecycle.active_trade
        assert trade is not None and trade.asset == "BTCUSD"
        assert resumed.strategy._open_sizing == sizing_before
        assert resumed.strategy.lock.is_held(), (
            "the portfolio slot must be occupied from the first candle after "
            "a restart (safety rule 13)")
        assert resumed.strategy.lock.active_trade is not None
        # and the trade still closes on exactly the same candle, for the
        # same money, as it would have without the restart
        continuous = make_runtime()
        drive(continuous, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                           2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_ENTRY}})
        reference = feed(continuous, "BTCUSD", 4, S_TP)
        step = feed(resumed, "BTCUSD", 4, S_TP)
        assert step.evaluation.closed.exit.outcome == "FILLED_TP"
        assert (step.evaluation.closed.balance_after
                == pytest.approx(reference.evaluation.closed.balance_after))

    def test_31_the_disabled_set_survives_a_restart(self, tmp_path):
        runtime = make_runtime(PAIRS, state_path=tmp_path / "s.json")
        drive(runtime, {0: {p: S_ORIGIN for p in PAIRS}})
        runtime.disable("SOLUSD")
        resumed = RT.ManualSMCRuntime.load_state(runtime.save_state())
        assert resumed.enabled_symbols == ("BTCUSD", "ETHUSD", "XRPUSD")
        assert not resumed.is_enabled("SOLUSD")
        assert feed(resumed, "SOLUSD", 1, S_BOS).skipped == RT.SKIPPED_DISABLED

    def test_32_the_snapshot_is_written_atomically(self, tmp_path):
        runtime = make_runtime(state_path=tmp_path / "s.json")
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS}})
        runtime.save_state()
        original = (tmp_path / "s.json").read_text(encoding="utf-8")
        real_replace = Path.replace

        def boom(self, target):
            raise OSError("simulated crash between write and rename")

        try:
            Path.replace = boom
            with pytest.raises(OSError):
                runtime.save_state()
        finally:
            Path.replace = real_replace
        assert (tmp_path / "s.json").read_text(encoding="utf-8") == original, (
            "a failed save must never leave a torn snapshot behind")
        assert not list(tmp_path.glob("*.tmp"))
        assert RT.ManualSMCRuntime.load_state(tmp_path / "s.json") is not None

    def test_32b_the_persistence_log_lines_report_real_values(
            self, tmp_path, caplog):
        """`has_active_trade` is a method: log it called, not as a repr."""
        runtime = make_runtime(state_path=tmp_path / "s.json")
        drive(runtime, {0: {"BTCUSD": S_ORIGIN}, 1: {"BTCUSD": S_BOS},
                        2: {"BTCUSD": S_TOUCH}, 3: {"BTCUSD": S_ENTRY}})
        with caplog.at_level("INFO", logger=RT.logger.name):
            path = runtime.save_state()
            RT.ManualSMCRuntime.load_state(path)
        lines = [r.getMessage() for r in caplog.records
                 if "active_trade=" in r.getMessage()]
        assert len(lines) == 2, lines
        for line in lines:
            assert "active_trade=True" in line, line
            assert "bound method" not in line, line


# -------------------------------------------------------------- 24.33 - 24.38
class TestFailClosed:

    def test_33_an_unknown_feed_symbol_is_refused(self):
        runtime = make_runtime(("BTCUSD",))
        with pytest.raises(RT.UnknownFeedSymbolError):
            runtime.handle_feed_payload(payload_for("DOGEUSD", 0, S_ORIGIN))
        with pytest.raises(RT.UnknownFeedSymbolError):
            feed(runtime, "btcusd", 0, S_ORIGIN)          # no case folding
        with pytest.raises(RT.UnknownFeedSymbolError):
            feed(runtime, "ETHUSD", 0, S_ORIGIN)          # not configured here
        assert runtime.candles_processed == 0

    def test_34_an_unknown_timeframe_is_refused_at_construction(self):
        with pytest.raises(RT.TimeframeError):
            RT.ManualSMCRuntime(symbols=["BTCUSD"], timeframe="5m")
        with pytest.raises(RT.TimeframeError):
            RT.timeframe_seconds("1d")

    def test_35_a_malformed_payload_is_refused(self):
        runtime = make_runtime(("BTCUSD",))
        good = payload_for("BTCUSD", 0, S_ORIGIN)
        for missing in ("open", "high", "low", "close", "timestamp", "symbol"):
            broken = {k: v for k, v in good.items() if k != missing}
            with pytest.raises(RT.MalformedCandleError):
                runtime.handle_feed_payload(broken)
        with pytest.raises(RT.MalformedCandleError):
            runtime.handle_feed_payload({**good, "high": Decimal("1")})
        with pytest.raises(RT.MalformedCandleError):
            runtime.handle_feed_payload({**good, "close": None})
        assert runtime.candles_processed == 0

    def test_36_an_unregistered_symbol_cannot_be_wired_at_all(self):
        with pytest.raises(RT.SymbolNotRegisteredError):
            RT.ManualSMCRuntime(symbols=["BTCUSD", "DOGEUSD"])
        with pytest.raises(RT.SymbolNotRegisteredError):
            RT.ManualSMCRuntime(symbols=["BTCUSD.P"])
        with pytest.raises(RT.SymbolNotRegisteredError):
            RT.ManualSMCRuntime(symbols=[])
        runtime = make_runtime(("BTCUSD",))
        for op in (runtime.enable, runtime.disable, runtime.is_enabled):
            with pytest.raises(RT.SymbolNotRegisteredError):
                op("DOGEUSD")

    def test_37_a_ready_decision_always_carries_complete_geometry(self):
        runtime = make_runtime(("BTCUSD",))
        ready = []
        for bar, row in enumerate(B_ROWS):
            step = feed(runtime, "BTCUSD", bar, row)
            ready.extend(step.adaptation.ready_decisions)
        assert ready, "the BTC fixture must produce a ready decision"
        for d in ready:
            assert d.setup_state is SetupState.TRADE_SETUP_READY
            assert d.entry is not None and d.entry > 0
            assert d.stop_loss is not None and d.stop_loss > 0
            assert d.take_profit is not None and d.take_profit > 0
            assert d.setup_id
            assert d.symbol == "BTCUSD"
            assert d.calculated_leverage is not None
            assert d.quantity is None

    def test_38_a_foreign_snapshot_is_refused(self, tmp_path):
        runtime = make_runtime(("BTCUSD",))
        feed(runtime, "BTCUSD", 0, B_ORIGIN)
        path = tmp_path / "s.json"
        runtime.save_state(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["strategy"]["strategy_name"] == MANUAL_SMC_STRATEGY_NAME

        foreign = copy.deepcopy(raw)
        foreign["strategy"]["strategy_name"] = "LUXALGO_SMC"
        (tmp_path / "foreign.json").write_text(json.dumps(foreign),
                                               encoding="utf-8")
        with pytest.raises(Exception):
            RT.ManualSMCRuntime.load_state(tmp_path / "foreign.json")

        mismatched = copy.deepcopy(raw)
        mismatched["strategy"]["config"]["fixed_tp_market_pct"] = 1.23
        (tmp_path / "cfg.json").write_text(json.dumps(mismatched),
                                           encoding="utf-8")
        with pytest.raises(Exception):
            RT.ManualSMCRuntime.load_state(tmp_path / "cfg.json")

        truncated = tmp_path / "truncated.json"
        truncated.write_text("{\"strategy\":", encoding="utf-8")
        with pytest.raises(Exception):
            RT.ManualSMCRuntime.load_state(truncated)

        alien = copy.deepcopy(raw)
        alien["wiring_schema"] = "SOMETHING_ELSE"
        (tmp_path / "alien.json").write_text(json.dumps(alien),
                                             encoding="utf-8")
        with pytest.raises(Exception):
            RT.ManualSMCRuntime.load_state(tmp_path / "alien.json")


# ------------------------------------------------------------------ section 25
class TestEndToEndLiveSimulation:
    """One full pass: live-shaped feed payload -> ... -> mocked exchange."""

    @staticmethod
    def live_payloads():
        """Real `candlestick_1h` payloads on real, already-closed hours."""
        now = int(time.time())
        base = (now - (now % 3600)) - 10 * 3600
        out = []
        for i, row in enumerate(B_ROWS):
            out.append({"symbol": "BTCUSD.P", "timeframe": "1h",
                        "timestamp": base + i * 3600,
                        "open": Decimal(str(row[0])),
                        "high": Decimal(str(row[1])),
                        "low": Decimal(str(row[2])),
                        "close": Decimal(str(row[3])),
                        "volume": Decimal("372963"), "is_closed": True})
        return out

    def test_end_to_end_identity_is_preserved(self):
        stack = ExecutionStack()
        runtime = make_runtime(("BTCUSD",), orchestrator=stack.orchestrator)

        async def go():
            steps = []
            for payload in self.live_payloads():
                steps.append(await runtime.process_feed_payload(
                    payload, user_id="u", account_id="acc"))
            return steps

        steps = asyncio.run(go())
        assert runtime.candles_processed == len(B_ROWS)
        assert runtime.candles_refused == 0

        final = steps[-1]
        decision = final.adaptation.ready_decisions[0]
        setup = final.evaluation.setups[0]
        assert len(stack.sent) == 1, "exactly one order for one setup"
        request = stack.sent[0]

        # symbol / direction / setup identity
        assert request.product_symbol == "BTCUSD" == decision.symbol
        assert decision.direction is StrategyDirection.SHORT
        assert str(request.side.value).lower() == "sell"
        assert decision.setup_id and setup.ob_id in decision.setup_id
        assert decision.metadata["manual_direction"] == "SHORT"

        # bracket identity, unchanged from the strategy's own geometry
        assert Decimal(str(request.limit_price)) == decision.entry
        assert Decimal(str(request.stop_loss_price)) == decision.stop_loss
        assert Decimal(str(request.take_profit_price)) == decision.take_profit
        assert float(decision.entry) == pytest.approx(setup.entry_price)
        assert float(decision.stop_loss) == pytest.approx(setup.sl_price)
        assert float(decision.take_profit) == pytest.approx(setup.tp_price)

        # leverage intent, and a whole contract count from the frozen allocator
        assert decision.calculated_leverage == int(setup.applied_leverage)
        assert decision.calculated_leverage not in (10, 100)
        assert decision.quantity is not None
        assert request.exchange_contract_count() == int(decision.quantity)

        # resting limit, never a market order on touch
        assert decision.metadata["entry_order_type"] == "LIMIT"
        assert decision.metadata["entry_is_resting_limit"] is True
        assert request.order_type is OrderType.LIMIT_ORDER

        # Manual SMC itself never spoke to the exchange
        assert stack.client.place_order.await_count == 1
        assert stack.client.cancel_order.await_count == 0
        for obj in (runtime.strategy, runtime.adapter,
                    runtime.strategy.lifecycle):
            assert not hasattr(obj, "client")
            assert not hasattr(obj, "place_order")
        assert final.scan_result.rejection_reason is None
        assert final.scan_result.qualifying_symbol == "BTCUSD"
        assert final.submitted == (decision.setup_id,)


# ------------------------------------------------------------------ section 26
def walk_candles(symbol_seed, bars, start_bar, base_price):
    """A deterministic OHLC walk whose `bar_idx` is the real epoch bar index."""
    from quantedge.strategy.manual_smc.backtest import Candle
    out = []
    state = symbol_seed
    price = base_price
    for i in range(bars):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        drift = ((state >> 7) % 2001 - 1000) / 1000.0
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        span = ((state >> 11) % 1500) / 1000.0 + 0.2
        open_ = price
        close = round(open_ * (1 + drift * 0.004), 6)
        high = round(max(open_, close) * (1 + span * 0.002), 6)
        low = round(min(open_, close) * (1 - span * 0.002), 6)
        bar_idx = start_bar + i
        out.append(Candle(bar_idx=bar_idx,
                          ts=datetime.fromtimestamp(bar_idx * 3600,
                                                    tz=timezone.utc),
                          open=open_, high=high, low=low, close=close))
        price = close
    return out


class TestHistoricalTransparency:
    """The wiring layer must not change what the strategy would have done."""

    SYMBOLS = ("BTCUSD", "ETHUSD")

    def datasets(self, bars=700):
        start = RT.bar_index_for(BASE, "1h")
        return {"BTCUSD": walk_candles(11, bars, start, 78000.0),
                "ETHUSD": walk_candles(29, bars, start, 3100.0)}

    def test_the_runtime_reproduces_the_backtest_exactly(self):
        from quantedge.strategy.manual_smc.backtest import (
            ManualSMCBacktest, build_timeline)
        data = self.datasets()
        timeline = build_timeline(data, symbols=list(self.SYMBOLS))
        cfg = manual_smc_production_config()
        registry = delta_india_registry()
        ticks = {s: registry.get(s) for s in self.SYMBOLS}

        driver = ManualSMCBacktest(config=cfg, symbols=self.SYMBOLS,
                                   tick_specs=ticks,
                                   starting_capital=cfg.starting_capital)
        result = driver.run(timeline, data)

        runtime = RT.ManualSMCRuntime(symbols=list(self.SYMBOLS),
                                      timeframe="1h",
                                      account_balance=cfg.starting_capital)
        runtime_events = []
        for row in timeline:
            candle = next(c for c in data[row.symbol]
                          if c.bar_idx == row.bar_idx)
            step = runtime.on_closed_candle(RT.ClosedCandle(
                symbol=row.symbol, ts=candle.ts, open=candle.open,
                high=candle.high, low=candle.low, close=candle.close))
            for event in step.evaluation.events:
                runtime_events.append(
                    (row.symbol, step.bar_idx, event.event_type.name))

        # 1. the same trades, in the same order, for the same money
        assert [(t.ob_id, t.outcome, t.realized_r, t.exit_bar_idx)
                for t in result.trades] == [
            (e.ob_id, e.outcome, e.realized_r, e.exit_bar_idx)
            for e in runtime.strategy.lifecycle.exits]
        assert result.trades, "the fixture must actually produce trades"
        # 2. the same surviving OB pool
        assert sorted(driver.strategy.lifecycle.live_obs) == sorted(
            runtime.strategy.lifecycle.live_obs)
        assert runtime_events, "events must have been observed"
        # 3. the same capital curve endpoint
        assert runtime.strategy.account_balance == pytest.approx(
            result.ending_capital)
        assert result.starting_capital == pytest.approx(cfg.starting_capital)
        # 4. the same watermarks
        for symbol in self.SYMBOLS:
            assert (runtime.strategy.watermark.last(symbol).bar_idx
                    == driver.strategy.watermark.last(symbol).bar_idx)

    def test_the_runtime_reproduces_the_backtest_event_stream(self):
        """Same candles, two drivers, one event stream."""
        from quantedge.strategy.manual_smc.backtest import (
            ManualSMCBacktest, build_timeline)
        data = self.datasets(bars=400)
        timeline = build_timeline(data, symbols=list(self.SYMBOLS))
        cfg = manual_smc_production_config()
        registry = delta_india_registry()
        ticks = {s: registry.get(s) for s in self.SYMBOLS}

        driver = ManualSMCBacktest(config=cfg, symbols=self.SYMBOLS,
                                   tick_specs=ticks,
                                   starting_capital=cfg.starting_capital)
        index = {s: {c.bar_idx: c for c in rows} for s, rows in data.items()}
        backtest_events = [
            (ev.asset, ev.bar_idx, e.event_type.name)
            for ev in driver.iter_run(timeline, data)
            for e in ev.events]

        runtime = RT.ManualSMCRuntime(symbols=list(self.SYMBOLS),
                                      timeframe="1h",
                                      account_balance=cfg.starting_capital)
        runtime_events = []
        for row in timeline:
            c = index[row.symbol][row.bar_idx]
            step = runtime.on_closed_candle(RT.ClosedCandle(
                symbol=row.symbol, ts=c.ts, open=c.open, high=c.high,
                low=c.low, close=c.close))
            runtime_events.extend(
                (row.symbol, step.bar_idx, e.event_type.name)
                for e in step.evaluation.events)

        assert runtime_events == backtest_events, (
            "the wiring layer must be behaviourally transparent")
        assert runtime.strategy.capture_state() == (
            driver.strategy.capture_state())



