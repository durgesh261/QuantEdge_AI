"""LIVE DATA FREQUENCY -- the EXECUTION CLOCK, proven update by update.

1h is Manual SMC's STRUCTURE timeframe. It is NOT the rate at which market data
arrives, and it is not the rate at which the engine may act. Delta Exchange India
pushes the forming `candlestick_1h` frame repeatedly during the hour; before this
seam existed every one of those frames was logged and dropped, so an order block
touched at 14:23:17 whose 25% entry was reached at 14:24:02 could not be acted on
until 15:00. That was the production defect these tests close.

TWO CLOCKS ARE ASSERTED HERE, SEPARATELY:

  A. the STRUCTURE clock -- closed 1h candles drive ATR / pivots / BOS / CHoCH /
     order-block creation. Every test below that touches it asserts it is
     UNCHANGED: no live update advances the watermark, no live update creates an
     order block, no live update reaches `evaluate_closed_candle`, and the
     backtest never calls the execution-clock API at all.
  B. the EXECUTION clock -- every live market update drives OB touch detection,
     25% entry evaluation and exit resolution, and reaches the same unmodified
     Path-A order submission the closed-candle path uses.

WHAT IS NOT ASSERTED. Nothing here claims a message frequency for the real feed:
the cadence of Delta's forming frames is not established anywhere in this
repository, and a test cannot establish it. What is asserted is that the engine
processes EVERY update it is handed, whatever the cadence turns out to be.

NO NETWORK, NO CREDENTIALS, NO EXCHANGE. Every price below is a locally built
fixture -- REPOSITORY-GENERATED synthetic frames, NOT TradingView data and not an
exchange capture. `TestNoExchangeOperation` makes socket creation itself raise
and then re-runs the whole scenario to prove it never needed one.

The geometry throughout is the suite's proven synthetic SHORT (the half-candle
`DETECTOR_MANUAL_SPEC` zone, for the same reason the rest of the suite names it:
these hand-built rows were constructed for that geometry). Bars 0-2 are CLOSED
candles and build one order block; bar 3 is then driven by LIVE UPDATES instead
of a closed candle, at the mandate's own timestamps.

Required-proof map (the mandate's ten items):
    01  many live updates inside one 1h candle ....... TestManyUpdatesInsideOneHour
    02  an active OB is touched mid-hour ............. TestTouchAndEntryMidHour
    03  the 25% entry is reached mid-hour ............ TestTouchAndEntryMidHour
    04  detected BEFORE the 1h candle closes ......... TestTouchAndEntryMidHour
    05  the entry event is on the correct update ..... TestTouchAndEntryMidHour
    06  no duplicate entry from later updates ........ TestTouchAndEntryMidHour
    07  the forming 1h OHLC keeps updating ........... TestManyUpdatesInsideOneHour
    08  rollover finalizes the previous candle once .. TestTheStructureClockIsUnchanged
    09  historical/offline backtest unchanged ........ TestTheStructureClockIsUnchanged
    10  no live exchange operation performed ......... TestNoExchangeOperation
"""

import ast
import asyncio
import inspect
import socket
import ssl
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quantedge.execution.models import OrderSide, OrderType  # noqa: E402
from quantedge.instruments.registry import delta_india_registry  # noqa: E402
from quantedge.market_data import delta_websocket as WS  # noqa: E402
from quantedge.market_data.live_aggregator import (  # noqa: E402
    SOURCE_CANDLESTICK_FORMING,
    SOURCE_PRICE,
    AmbiguousMarketUpdateError,
    FinalizedBucketError,
    LiveCandleAggregator,
    MalformedMarketUpdateError,
    MarketUpdate,
    StaleMarketUpdateError,
    bucket_index_for,
    bucket_open_ts,
    normalize_market_update,
)
from quantedge.runtime import manual_smc_runtime as RT  # noqa: E402
from quantedge.strategy.manual_smc.lifecycle import (  # noqa: E402
    ACTIVATION_MODE_FIRST_TOUCH,
    ACTIVATION_MODE_ORACLE_C,
    DETECTOR_MANUAL_SPEC,
    ENTRY_WINDOW_CANDLES,
    OUTCOME_TP,
    REASON_TP_HIT,
    LifecycleClockError,
    ManualLifecycleEventType as ET,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    ManualOBState as OBS,
    manual_smc_production_config,
)
from quantedge.strategy.manual_smc.strategy import (  # noqa: E402
    ManualSMCStrategy,
)
from quantedge.strategy.manual_smc.strategy import (  # noqa: E402
    StaleMarketUpdateError as StrategyStaleUpdateError,
)

from test_manual_smc_runtime import ExecutionStack  # noqa: E402

US = 1_000_000
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)     # a past 1h boundary
SYMBOL = "BTCUSD"
LOCAL = SYMBOL + RT.LOCAL_SYMBOL_SUFFIX

# The proven synthetic SHORT, copied verbatim from `test_manual_smc_runtime.py`:
# bar 0 origin, bar 1 BOS, bar 2 inert. Bars 3-4 exist here only for the
# closed-candle CONTROL runs.
S_ORIGIN = (100.0, 106.0, 99.0, 105.0)
S_BOS = (104.0, 104.5, 97.0, 98.0)
S_INERT = (98.0, 98.0, 98.0, 98.0)
S_TOUCH = (98.5, 99.0, 98.0, 98.5)
S_ENTRY = (100.0, 100.5, 99.5, 100.0)
STRUCTURE_ROWS = {0: S_ORIGIN, 1: S_BOS, 2: S_INERT}

# The order block those three closed candles produce, with its authorised
# geometry. Re-derived from the OB record itself by
# `TestTheFixture::test_the_geometry_is_the_authorised_geometry`, so no number
# here is taken on trust.
OB_ID = "MANUAL_BTCUSD_SHORT_490896_490897"
ZONE_BOTTOM, ZONE_TOP = 99.0, 105.0
ENTRY_25PCT = 100.5          # bottom + 0.25 * (top - bottom)
SL_FAR_EDGE = 105.0          # the opposite edge of the zone
TP_060PCT = 99.897           # entry * (1 - 0.0060)

# The mandate's own worked example, one hour earlier in the day: an OB is touched
# at :23:17 and its 25% entry is reached at :24:02, both inside bar 3.
TOUCH_M, TOUCH_S = 23, 17
ENTRY_M, ENTRY_S = 24, 2


def ts_at(bar: int) -> datetime:
    """The opening instant of a synthetic 1h bar."""
    return BASE + timedelta(hours=bar)


def at(bar: int, minute: int, second: int, micros: int = 0) -> datetime:
    """An instant INSIDE a synthetic 1h bar."""
    return ts_at(bar) + timedelta(minutes=minute, seconds=second,
                                  microseconds=micros)


def us_epoch(when: datetime) -> int:
    """Exact integer microsecond epoch (float seconds would lose the micros)."""
    return int(when.timestamp()) * US + when.microsecond

def make_strategy(balance: float = 10000.0, symbols=(SYMBOL,),
                  activation: str = ACTIVATION_MODE_FIRST_TOUCH):
    """The production strategy construction, on the verified registry."""
    registry = delta_india_registry()
    return ManualSMCStrategy(
        config=manual_smc_production_config(),
        assets=list(symbols),
        account_balance=balance,
        tick_specs={s: registry.get(s) for s in symbols},
        activation_mode=activation,
        entry_window_candles=ENTRY_WINDOW_CANDLES,
        detector=DETECTOR_MANUAL_SPEC,
    )


def make_runtime(symbols=(SYMBOL,), balance: float = 10000.0, **kw):
    kw.setdefault("strategy", make_strategy(balance, symbols))
    return RT.ManualSMCRuntime(symbols=list(symbols), timeframe="1h", **kw)


def closed(runtime, bar: int, row, symbol: str = SYMBOL):
    """One CLOSED candle -- the structure clock."""
    return runtime.on_closed_candle(RT.ClosedCandle(
        symbol=symbol, ts=ts_at(bar), open=row[0], high=row[1], low=row[2],
        close=row[3]))


def build_structure(runtime, symbol: str = SYMBOL):
    """Bars 0-2 as closed candles: the OB is created by the STRUCTURE clock."""
    return [closed(runtime, bar, STRUCTURE_ROWS[bar], symbol)
            for bar in sorted(STRUCTURE_ROWS)]


def the_ob(runtime, symbol: str = SYMBOL):
    """The single order block bars 0-2 produced."""
    obs = [ob for ob in runtime.strategy.lifecycle.live_obs.values()
           if ob.asset == symbol]
    assert len(obs) == 1, f"expected exactly one OB, got {[o.ob_id for o in obs]}"
    return obs[0]


def frame(bar: int, minute: int, second: int, close: float, high: float,
          low: float, open_: float = 98.2, micros: int = 0,
          symbol: str = SYMBOL, declare: bool = True) -> dict:
    """The exact dict `delta_websocket._market_update_payload` emits.

    A forming `candlestick_1h` frame: the hour's running OHLC as the exchange
    computed it, the candle's own start, and -- separately -- the instant of the
    last tick, which is what times the update.
    """
    when = at(bar, minute, second, micros)
    payload = {
        "symbol": symbol + RT.LOCAL_SYMBOL_SUFFIX,
        "exchange_symbol": symbol,
        "timeframe": "1h",
        "open": open_, "high": high, "low": low, "close": close,
        "update_ts_us": us_epoch(when),
        "is_closed": False,
        "source": SOURCE_CANDLESTICK_FORMING,
    }
    if declare:
        payload["candle_start_ts"] = int(ts_at(bar).timestamp())
    return payload

# The mandate's scenario inside bar 3, as (minute, second, close, high, low).
# Frame 1 is quiet, frame 2 reaches the zone's proximal edge, frame 3 reaches the
# 25% entry, frame 4 is later in the same hour and carries nothing new.
TICKS = (
    (10, 0, 98.2, 98.6, 98.0),
    (TOUCH_M, TOUCH_S, 99.0, 99.0, 98.0),
    (ENTRY_M, ENTRY_S, 100.5, 100.5, 98.0),
    (30, 0, 100.5, 100.5, 98.0),
)


def tick_frames(bar: int = 3, symbol: str = SYMBOL):
    return [frame(bar, m, s, c, h, l, symbol=symbol)
            for (m, s, c, h, l) in TICKS]


def ws_message(second_offset: int = 0, drop=(), **over) -> dict:
    """A raw Delta India `candlestick_1h` message inside the CURRENT hour.

    The transport decides forming-vs-closed from the wall clock
    (`_is_candle_closed`), so a frame that must arrive as FORMING has to be dated
    now. The prices are the ones documented in `_parse_candle_from_ws`; what is
    asserted about these messages is only which callback they reach.
    """
    now = int(time.time())
    start = now - (now % 3600)
    stamp = (start + second_offset) * US + 489993
    message = {"type": "candlestick_1h", "symbol": SYMBOL, "resolution": "1h",
               "open": 77809.5, "high": 77865.5, "low": 77338.5,
               "close": 77428.5, "volume": 372963.0,
               "candle_start_time": start * US,
               "timestamp": stamp, "last_updated": stamp,
               "sUID": "BTCUSD_#_BTCUSD_#_60"}
    message.update(over)
    for key in drop:
        message.pop(key, None)
    return message


def ws_client(on_market_update=None, on_candle_closed=None):
    """The real transport, with persistence off and no socket.

    `_handle_message` is a coroutine over an already-decoded dict, so the whole
    routing decision can be exercised without connecting to anything.
    """
    tmp = Path(tempfile.mkdtemp())
    return WS.DeltaWebSocketClient(
        on_candle_closed=on_candle_closed, on_market_update=on_market_update,
        persist=False, csv_path=tmp / "feed.csv",
        meta_path=tmp / "feed.meta.json")


RUNTIME_SRC = (Path(__file__).parent.parent / "src" / "quantedge" / "runtime"
               / "manual_smc_runtime.py").read_text(encoding="utf-8")
SMC_DIR = (Path(__file__).parent.parent / "src" / "quantedge" / "strategy"
           / "manual_smc")


def _source_of(func) -> str:
    """A function's own source text, for asserting what it does NOT call."""
    return inspect.getsource(func)


def _calls_in(func) -> set:
    """Every callee name in a function's BODY -- docstrings excluded.

    A prose mention of `evaluate_closed_candle` in a docstring is exactly the
    kind of thing a substring check would trip over, so the check is structural.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _names_used(path: Path) -> set:
    """Every attribute and plain name a module mentions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.alias):
            used.add(node.name.split(".")[-1])
            if node.asname:
                used.add(node.asname)
    return used


# ---------------------------------------------------------------------------
# The fixture itself, before anything is claimed about it.
# ---------------------------------------------------------------------------
class TestTheFixture:

    def test_three_closed_candles_create_exactly_one_order_block(self):
        runtime = make_runtime()
        steps = build_structure(runtime)
        assert [e.event_type for s in steps for e in s.evaluation.events] == [
            ET.OB_CREATED], (
            "the structure clock must produce the OB, and only the OB, before "
            "any live update exists")
        assert the_ob(runtime).ob_id == OB_ID

    def test_the_geometry_is_the_authorised_geometry(self):
        """Every named number is re-derived from the OB record, not assumed."""
        runtime = make_runtime()
        build_structure(runtime)
        ob = the_ob(runtime)
        assert (ob.ob_bottom, ob.ob_top) == (ZONE_BOTTOM, ZONE_TOP)
        span = ob.ob_top - ob.ob_bottom
        assert ob.entry_price == pytest.approx(
            ob.ob_bottom + 0.25 * span), "the 25% entry depth"
        assert ob.entry_price == ENTRY_25PCT
        assert ob.sl_price == ob.ob_top == SL_FAR_EDGE, "the opposite edge SL"
        assert ob.tp_price == pytest.approx(
            ob.entry_price * (1 - 0.0060)), "the fixed 0.60% TP"
        assert round(ob.tp_price, 3) == TP_060PCT
        assert ob.state is OBS.AWAITING_DISPLACEMENT
        assert runtime.strategy.cfg.entry_depth_pct == 0.25
        assert runtime.strategy.cfg.fixed_tp_market_pct == 0.60

    def test_the_order_block_is_live_before_the_next_hour_closes(self):
        """An OB created at the close of hour N is monitorable during hour N+1.

        This is the precondition the whole execution clock rests on: the OB
        created by bar 1's close is already in the live pool, so hour 2 and hour
        3 can be monitored tick by tick without waiting for another close.
        """
        runtime = make_runtime()
        closed(runtime, 0, S_ORIGIN)
        closed(runtime, 1, S_BOS)
        assert the_ob(runtime).ob_id == OB_ID
        step = runtime.handle_market_update_payload(
            frame(2, 5, 0, 98.0, 98.1, 97.9, open_=98.0))
        assert step.skipped is None
        assert step.bar_idx == bucket_index_for(at(2, 5, 0)), (
            "hour 2's updates must be monitored against an OB created at the "
            "close of hour 1")

# ---------------------------------------------------------------------------
# The transport seam: which clock does a Delta frame reach?
# ---------------------------------------------------------------------------
class TestTheDeltaFrameReachesTheExecutionClock:

    def test_a_forming_frame_reaches_the_execution_clock(self):
        seen = []
        client = ws_client(on_market_update=seen.append)
        message = ws_message(second_offset=788)
        asyncio.run(client._handle_message(message))
        assert len(seen) == 1, (
            "a forming candlestick frame carries the live price; before this "
            "seam it was logged and dropped, which is what delayed every entry")
        payload = seen[0]
        assert payload["source"] == SOURCE_CANDLESTICK_FORMING
        assert payload["is_closed"] is False
        assert payload["symbol"] == LOCAL
        assert payload["exchange_symbol"] == SYMBOL
        assert payload["update_ts_us"] == message["last_updated"], (
            "the update must be timed by the frame's own last-tick microsecond, "
            "not by the candle start and not by a wall clock")
        assert payload["update_ts_us"] % US == 489993, "microsecond precision"
        assert payload["candle_start_ts"] * US == message["candle_start_time"]
        assert payload["update_ts_us"] > payload["candle_start_ts"] * US, (
            "the last tick lies INSIDE the forming hour")


    def test_the_forming_frame_does_not_reach_the_structure_clock(self):
        """The two clocks never see the same frame."""
        structure, execution = [], []
        client = ws_client(on_market_update=execution.append,
                           on_candle_closed=structure.append)
        asyncio.run(client._handle_message(ws_message(second_offset=10)))
        assert execution and not structure
        assert client.processed_timestamps == set(), (
            "a forming frame must not be marked processed")
        assert client.last_closed_ts is None, (
            "a forming frame must not move the closed-candle watermark")

    def test_a_closed_frame_reaches_only_the_structure_clock(self):
        structure, execution = [], []
        client = ws_client(on_market_update=execution.append,
                           on_candle_closed=structure.append)
        now = int(time.time())
        start = (now - (now % 3600)) - 3600            # the PREVIOUS hour
        asyncio.run(client._handle_message(ws_message(
            candle_start_time=start * US, timestamp=start * US,
            last_updated=start * US)))
        assert structure and not execution, (
            "a closed candle belongs to the 1h structure clock alone")
        assert structure[0]["is_closed"] is True

    def test_a_frame_with_no_unambiguous_tick_instant_is_not_forwarded(self):
        """`timestamp` means the candle START when `candle_start_time` is absent.

        Timing an execution-clock update by that field would mis-order the feed,
        so the frame is used for nothing rather than being interpreted.
        """
        seen = []
        client = ws_client(on_market_update=seen.append)
        asyncio.run(client._handle_message(ws_message(
            drop=("candle_start_time", "last_updated"))))
        assert seen == []

    def test_a_refused_update_does_not_stop_the_feed(self):
        """A refusal is a refusal, never a repair -- and never a dead feed."""
        def sink(payload):
            raise StaleMarketUpdateError("refused by the aggregator")

        client = ws_client(on_market_update=sink)
        asyncio.run(client._handle_message(ws_message(second_offset=5)))
        structure = []
        client.on_candle_closed = structure.append
        now = int(time.time())
        start = (now - (now % 3600)) - 3600
        asyncio.run(client._handle_message(ws_message(
            candle_start_time=start * US, timestamp=start * US,
            last_updated=start * US)))
        assert len(structure) == 1, (
            "the structure clock must keep working after the execution clock "
            "refused a frame")

    def test_the_default_transport_is_byte_for_byte_the_previous_one(self):
        client = WS.DeltaWebSocketClient(persist=False)
        assert client.on_market_update is None, (
            "the execution clock is opt-in: a client built the old way must "
            "behave exactly as it did before this seam existed")
        assert WS.SUBSCRIPTION_CHANNEL == "candlestick_1h"

# ---------------------------------------------------------------------------
# Required proofs 01 and 07.
# ---------------------------------------------------------------------------
class TestManyUpdatesInsideOneHour:

    def test_01_every_update_inside_one_hour_is_processed(self):
        runtime = make_runtime()
        build_structure(runtime)
        steps = [runtime.handle_market_update_payload(p)
                 for p in tick_frames()]
        assert len(steps) == 4
        assert runtime.updates_processed == 4, (
            "1h is the STRUCTURE timeframe, not the update rate: every update "
            "the feed supplies must be processed")
        assert runtime.updates_ignored == 0
        assert runtime.updates_refused == 0
        assert {s.bar_idx for s in steps} == {bucket_index_for(ts_at(3))}, (
            "all four updates belong to the same 1h bar")
        assert all(s.is_market_update for s in steps)
        assert all(s.skipped is None for s in steps)
        assert runtime.candles_processed == 3, (
            "no live update may be counted as a closed candle")

    def test_01b_two_updates_a_quarter_second_apart_are_both_processed(self):
        """The cadence is the exchange's to choose; the engine must not gate it.

        Nothing here asserts that Delta sends frames 250 ms apart. It asserts
        that if it does, neither is dropped -- there is no per-second, per-minute
        or per-candle throttle anywhere on the path.
        """
        runtime = make_runtime()
        build_structure(runtime)
        first = runtime.handle_market_update_payload(
            frame(3, 5, 0, 98.20, 98.30, 98.10))
        second = runtime.handle_market_update_payload(
            frame(3, 5, 0, 98.25, 98.30, 98.10, micros=250_000))
        assert first.skipped is None and second.skipped is None
        assert runtime.updates_processed == 2
        assert second.ts - first.ts == timedelta(microseconds=250_000)
        assert second.forming.update_count == 2

    def test_07_the_forming_1h_ohlc_keeps_updating_on_every_update(self):
        runtime = make_runtime()
        build_structure(runtime)
        seen = []
        for payload in tick_frames():
            seen.append(runtime.handle_market_update_payload(payload).forming)
        assert [f.update_count for f in seen] == [1, 2, 3, 4]
        assert [f.close for f in seen] == [98.2, 99.0, 100.5, 100.5], (
            "close is the latest price, updated by every single update")
        assert [f.high for f in seen] == [98.6, 99.0, 100.5, 100.5], (
            "high is the running maximum")
        assert [f.low for f in seen] == [98.0, 98.0, 98.0, 98.0]
        assert all(f.open == 98.2 for f in seen), (
            "open is the hour's first price and never moves once adopted")
        assert all(f.open_from_exchange for f in seen), (
            "the exchange's own open is preferred: a subscriber that connects "
            "mid-hour never saw the real first trade")
        assert all(f.is_ohlc_consistent for f in seen)
        assert seen[-1].bucket_ts == ts_at(3)
        assert seen[-1].first_update_ts == at(3, 10, 0)
        assert seen[-1].last_update_ts == at(3, 30, 0)

# ---------------------------------------------------------------------------
# Required proofs 02, 03, 04, 05 and 06 -- the mandate's worked example.
# ---------------------------------------------------------------------------
class TestTouchAndEntryMidHour:

    @staticmethod
    def run_scenario(upto: int = len(TICKS)):
        runtime = make_runtime()
        build_structure(runtime)
        steps = [runtime.handle_market_update_payload(p)
                 for p in tick_frames()[:upto]]
        return runtime, steps

    def test_02_an_active_order_block_is_touched_mid_hour(self):
        runtime, steps = self.run_scenario(upto=2)     # stop AT the touch
        quiet, touch = steps[0], steps[1]
        assert [e.event_type for e in quiet.evaluation.events] == [], (
            "the price had not reached the zone yet")
        assert [e.event_type for e in touch.evaluation.events] == [
            ET.FIRST_TOUCH_LIMIT_ACTIVATED]
        event = touch.evaluation.events[0]
        assert event.ts == at(3, TOUCH_M, TOUCH_S), (
            "the touch is stamped with the instant it happened, 03:23:17")
        assert event.ob_id == OB_ID
        ob = the_ob(runtime)
        assert ob.state is OBS.LIMIT_RESTING
        assert ob.first_touch_dt == at(3, TOUCH_M, TOUCH_S)
        assert ob.proximal == ZONE_BOTTOM
        assert touch.forming.high == ob.proximal, (
            "the touch is the live price reaching the zone's proximal edge")
        assert ob.limit_active_from_bar == bucket_index_for(ts_at(3)), (
            "the resting limit is live from THIS bar, so the same hour can fill")

    def test_03_and_05_the_25pct_entry_is_reached_and_fills_on_that_update(self):
        runtime, steps = self.run_scenario()
        filled = [s for s in steps
                  if any(e.event_type is ET.ENTRY_FILLED
                         for e in s.evaluation.events)]
        assert len(filled) == 1, "exactly one update may fill the entry"
        assert filled[0] is steps[2], (
            "the fill belongs to the 03:24:02 update -- not to the touch that "
            "preceded it and not to any later update")
        assert filled[0].ts == at(3, ENTRY_M, ENTRY_S)
        assert filled[0].evaluation.events[0].ts == at(3, ENTRY_M, ENTRY_S)
        trade = runtime.strategy.lifecycle.active_trade
        assert trade is not None
        assert trade.entry_price == ENTRY_25PCT, (
            "the fill price is the OB's 25% entry, not the tick's own price")
        assert trade.sl_price == SL_FAR_EDGE
        assert round(trade.tp_price, 3) == TP_060PCT
        assert trade.fill_dt == at(3, ENTRY_M, ENTRY_S)
        assert trade.fill_bar_idx == bucket_index_for(ts_at(3))
        assert 1.0 <= trade.applied_leverage <= 100.0
        assert the_ob(runtime).state is OBS.TRADE_ACTIVE

    def test_04_the_fill_happens_before_the_1h_candle_closes(self):
        runtime, steps = self.run_scenario()
        assert runtime.strategy.lifecycle.active_trade is not None
        assert runtime.candles_processed == 3, (
            "bars 0-2 closed; bar 3 has NOT closed, and the trade is already "
            "filled inside it")
        mark = runtime.strategy.watermark.last(SYMBOL)
        assert mark.bar_idx == RT.bar_index_for(ts_at(2)), (
            "the STRUCTURE clock is still at hour 2 while the EXECUTION clock "
            "has already touched, activated and filled inside hour 3")
        state = runtime.strategy.unpersisted_strategy_state()
        assert state["last_update_ts"][SYMBOL] == at(3, 30, 0), (
            "the execution clock keeps its own marker, separate from the "
            "closed-candle watermark")
        assert all(s.evaluation.watermark_advanced is False for s in steps), (
            "no live update may advance the 1h structure watermark")

    def test_06_no_later_update_can_fill_a_second_time(self):
        runtime, steps = self.run_scenario()
        fills = [e for s in steps for e in s.evaluation.events
                 if e.event_type is ET.ENTRY_FILLED]
        assert len(fills) == 1
        assert [e.event_type for e in steps[3].evaluation.events] == [], (
            "an update at 03:30:00, with the trade already filled, must produce "
            "nothing")
        extra = runtime.handle_market_update_payload(
            frame(3, 45, 0, 100.5, 100.5, 98.0))
        assert [e.event_type for e in extra.evaluation.events] == []
        assert runtime.strategy.lifecycle.active_trade.fill_dt == at(
            3, ENTRY_M, ENTRY_S), "the original fill instant is untouched"

    def test_06b_an_exactly_repeated_frame_is_absorbed_not_re_run(self):
        """Delta repeats the last-tick stamp when no new trade has printed."""
        runtime, steps = self.run_scenario()
        repeat = runtime.handle_market_update_payload(tick_frames()[-1])
        assert repeat.skipped == RT.SKIPPED_DUPLICATE_UPDATE
        assert repeat.evaluation is None, (
            "a payload carrying no new trade must not re-run the OB sweep")
        assert runtime.updates_ignored == 1
        assert runtime.updates_processed == 4

    def test_06c_the_closed_candle_for_the_same_hour_cannot_re_fill(self):
        """The hour then closes. The fill already happened inside it.

        The closed candle for bar 3 is still ACCEPTED -- the structure clock must
        not be starved -- but it can neither fill again nor resolve the exit of a
        trade filled within its own bar.
        """
        runtime, _ = self.run_scenario()
        before = runtime.strategy.lifecycle.active_trade
        step = closed(runtime, 3, (98.2, 100.5, 98.0, 100.0))
        assert step.evaluation.watermark_advanced is True
        assert [e.event_type for e in step.evaluation.events] == []
        after = runtime.strategy.lifecycle.active_trade
        assert after is before
        assert after.fill_dt == at(3, ENTRY_M, ENTRY_S)
        assert runtime.candles_processed == 4

    def test_the_touch_and_the_fill_can_be_the_same_update(self):
        """A single update that gaps straight through the zone to the entry.

        First-touch activation makes the resting limit live from the touching bar
        itself, so one update may both touch and fill. Nothing is skipped and
        nothing is deferred to the next hour.
        """
        runtime = make_runtime()
        build_structure(runtime)
        step = runtime.handle_market_update_payload(
            frame(3, 11, 9, 100.5, 100.5, 98.0))
        assert [e.event_type for e in step.evaluation.events] == [
            ET.FIRST_TOUCH_LIMIT_ACTIVATED, ET.ENTRY_FILLED]
        trade = runtime.strategy.lifecycle.active_trade
        assert trade.entry_price == ENTRY_25PCT
        assert trade.fill_dt == at(3, 11, 9)

    def test_an_overshooting_update_still_fills_at_the_25pct_entry(self):
        """The engine models a resting LIMIT: the price is the limit's price."""
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 20, 0, 99.0, 99.0, 98.0))
        runtime.handle_market_update_payload(
            frame(3, 21, 0, 100.9, 100.9, 98.0))
        trade = runtime.strategy.lifecycle.active_trade
        assert trade is not None
        assert trade.entry_price == ENTRY_25PCT, (
            "a tick at 100.9 does not become the fill price; the resting limit "
            "at 100.5 does")

# ---------------------------------------------------------------------------
# The order reaches the exchange mid-hour, through the real Path-A stack.
# ---------------------------------------------------------------------------
class TestOrderReachesTheExchangeMidHour:
    """The point of the whole seam: the order is ON the exchange at the touch.

    Manual SMC does not chase the 25% entry with a market order -- it rests a
    LIMIT there with its bracket and lets the exchange fill it. So what the
    execution clock buys is that the resting order exists from 03:23:17, instead
    of being placed after 04:00 with the entry already long gone.

    `ExecutionStack` is the real frozen execution engine (validation gateway,
    capital allocator, single-trade lock, trade-lifecycle manager, orchestrator)
    with only the exchange transport mocked.
    """

    @staticmethod
    def build():
        stack = ExecutionStack(symbols=(SYMBOL,))
        runtime = make_runtime(balance=100000.0, orchestrator=stack.orchestrator)
        return stack, runtime

    @staticmethod
    async def drive(runtime, payloads, closed_rows=STRUCTURE_ROWS):
        for bar in sorted(closed_rows):
            await runtime.process_closed_candle(
                RT.ClosedCandle(SYMBOL, ts_at(bar), *closed_rows[bar]),
                user_id="u", account_id="acc")
        out = []
        for payload in payloads:
            out.append(await runtime.process_market_update_payload(
                payload, user_id="u", account_id="acc"))
        return out

    def test_no_order_exists_while_the_price_has_not_reached_the_zone(self):
        stack, runtime = self.build()
        asyncio.run(self.drive(runtime, tick_frames()[:1]))
        assert stack.sent == [], (
            "three closed candles and a quiet update must place nothing")

    def test_the_resting_limit_is_submitted_on_the_touch_update(self):
        stack, runtime = self.build()
        steps = asyncio.run(self.drive(runtime, tick_frames()[:2]))
        assert len(stack.sent) == 1, (
            "the touch at 03:23:17 must put the order on the exchange at "
            "03:23:17, not at 04:00")
        request = stack.sent[0]
        assert request.product_symbol == SYMBOL
        assert request.side is OrderSide.SELL
        assert request.order_type is OrderType.LIMIT_ORDER
        assert request.limit_price == Decimal(str(ENTRY_25PCT))
        assert request.stop_loss_price == Decimal(str(SL_FAR_EDGE))
        assert request.take_profit_price is not None
        assert request.reduce_only is False
        assert request.size > 0
        assert steps[1].submitted == (
            runtime.setup_id_for(SYMBOL, OB_ID, "SHORT"),)
        assert steps[1].scan_result.rejection_reason is None

    def test_the_mid_hour_order_is_the_order_the_closed_candle_would_send(self):
        """PARITY. The execution clock changes WHEN, never WHAT.

        Same order block, same equity, same authorised geometry: the request
        built from the 03:23:17 update is identical, field for field, to the one
        built when bar 3 closes -- `client_order_id` included.

        That last field used to be the one deliberate difference, because the
        submission path minted it from wall-clock time plus a random suffix.
        It is now derived from `(account_id, setup_id, role)` alone, so the two
        clocks agree on the identity as well as the contents: the same setup
        can only ever name one entry order, whichever clock submits it. This is
        the property a restart depends on -- see the deterministic-identity
        tests in `test_execution_identity_and_startup_gate.py`.
        """
        tick_stack, tick_runtime = self.build()
        asyncio.run(self.drive(tick_runtime, tick_frames()[:2]))
        candle_stack, candle_runtime = self.build()
        asyncio.run(self.drive(candle_runtime, [],
                               closed_rows={**STRUCTURE_ROWS, 3: S_TOUCH}))
        assert len(tick_stack.sent) == len(candle_stack.sent) == 1
        live, control = tick_stack.sent[0], candle_stack.sent[0]
        fields = [f for f in type(live).__dataclass_fields__
                  if f != "client_order_id"]
        assert [getattr(live, f) for f in fields] == [
            getattr(control, f) for f in fields], (
            "the execution clock must not alter sizing, leverage, price, "
            "bracket or any other order field")
        assert live.client_order_id == control.client_order_id, (
            "the execution clock must not alter the order identity either: "
            "one setup, one deterministic entry id")
        assert live.client_order_id.endswith("-ENTRY"), (
            "the identity must still be role-scoped")

    def test_only_one_order_is_sent_however_many_updates_arrive(self):
        stack, runtime = self.build()
        payloads = tick_frames() + [frame(3, 45, 0, 100.5, 100.5, 98.0),
                                    frame(3, 50, 0, 100.4, 100.5, 98.0)]
        asyncio.run(self.drive(runtime, payloads))
        assert len(stack.sent) == 1, (
            "six updates, one resting order: the execution clock must not "
            "re-submit on every frame")

# ---------------------------------------------------------------------------
# Required proofs 08 and 09 -- the structure clock is untouched.
# ---------------------------------------------------------------------------
class TestTheStructureClockIsUnchanged:

    def test_08_the_rollover_finalizes_the_previous_candle_exactly_once(self):
        runtime = make_runtime()
        build_structure(runtime)
        for minute in (5, 20, 40):
            step = runtime.handle_market_update_payload(
                frame(3, minute, 0, 98.2, 98.6, 98.0))
            assert step.finalized is None, "hour 3 is still forming"
        rollover = runtime.handle_market_update_payload(
            frame(4, 0, 12, 98.3, 98.4, 98.3, open_=98.3))
        assert rollover.finalized is not None
        assert rollover.finalized.bucket_idx == bucket_index_for(ts_at(3))
        assert rollover.finalized.update_count == 3
        assert (rollover.finalized.open, rollover.finalized.high,
                rollover.finalized.low, rollover.finalized.close) == (
                    98.2, 98.6, 98.0, 98.2)
        assert runtime.aggregator.candles_finalized == 1
        again = runtime.handle_market_update_payload(
            frame(4, 1, 0, 98.35, 98.4, 98.3, open_=98.3))
        assert again.finalized is None, "finalization happens exactly once"
        assert runtime.aggregator.candles_finalized == 1
        assert runtime.aggregator.finalized_buckets(SYMBOL) == (
            bucket_index_for(ts_at(3)),)

    def test_08b_finalization_is_driven_by_data_not_by_a_wall_clock(self):
        """Nothing rolls the hour over except an update belonging to a later one.

        A gap in the feed is reported, never filled: the hours nobody sent are
        listed as skipped and are not fabricated as candles.
        """
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 5, 0, 98.2, 98.6, 98.0))
        gap = runtime.handle_market_update_payload(
            frame(6, 5, 0, 98.3, 98.4, 98.3, open_=98.3))
        assert gap.finalized.bucket_idx == bucket_index_for(ts_at(3))
        assert runtime.aggregator.candles_finalized == 1, (
            "only the bucket actually observed is finalized")
        assert runtime.aggregator.finalized_buckets(SYMBOL) == (
            bucket_index_for(ts_at(3)),)

    def test_08c_a_locally_finalized_candle_never_reaches_the_structure_clock(self):
        """LuxAlgo keeps consuming the exchange's own closed frame.

        The locally aggregated hour is observability only. If it were fed to the
        strategy as a closed candle, 1h structure semantics would depend on which
        frames this process happened to receive.
        """
        runtime = make_runtime()
        build_structure(runtime)
        before = runtime.strategy.watermark.last(SYMBOL).bar_idx
        runtime.handle_market_update_payload(frame(3, 5, 0, 98.2, 98.6, 98.0))
        rollover = runtime.handle_market_update_payload(
            frame(4, 0, 0, 98.3, 98.4, 98.3, open_=98.3))
        assert rollover.finalized is not None
        assert runtime.strategy.watermark.last(SYMBOL).bar_idx == before, (
            "a locally finalized candle must not advance the 1h watermark")
        assert runtime.candles_processed == 3
        calls = _calls_in(RT.ManualSMCRuntime.on_market_update)
        assert "evaluate_closed_candle" not in calls
        assert "on_closed_candle" not in calls
        assert "evaluate_market_update" in calls, (
            "the execution clock has exactly one strategy entry point")

    def test_09_the_offline_backtest_never_calls_the_execution_clock(self):
        """The historical path cannot have changed: it does not know it exists."""
        forbidden = {"process_market_update", "evaluate_market_update",
                     "handle_market_update_payload", "on_market_update",
                     "market_update_from_payload", "LiveCandleAggregator",
                     "normalize_market_update", "MarketUpdate"}
        for name in ("backtest.py", "executable.py", "adapter.py",
                     "scanner.py", "luxalgo_structure.py"):
            path = SMC_DIR / name
            if not path.exists():
                continue
            leaked = forbidden & _names_used(path)
            assert leaked == set(), (
                f"{name} must stay on the 1h structure clock only; it "
                f"references {sorted(leaked)}")

    def test_09b_a_closed_candle_only_drive_is_the_classic_result(self):
        """Bar 3 touches, bar 4 fills -- exactly as the suite already asserts."""
        runtime = make_runtime()
        steps = build_structure(runtime)
        steps.append(closed(runtime, 3, S_TOUCH))
        steps.append(closed(runtime, 4, S_ENTRY))
        assert [e.event_type for s in steps for e in s.evaluation.events] == [
            ET.OB_CREATED, ET.FIRST_TOUCH_LIMIT_ACTIVATED, ET.ENTRY_FILLED]
        trade = runtime.strategy.lifecycle.active_trade
        assert trade.entry_price == ENTRY_25PCT
        assert trade.sl_price == SL_FAR_EDGE
        assert trade.fill_bar_idx == RT.bar_index_for(ts_at(4))
        assert trade.fill_dt == ts_at(4), (
            "on the historical path the fill is stamped with the candle, and "
            "that has not changed")
        assert all(s.evaluation.watermark_advanced for s in steps)
        assert runtime.candles_processed == 5

    def test_09c_the_closed_candle_path_never_touches_the_aggregator(self):
        runtime = make_runtime()
        build_structure(runtime)
        closed(runtime, 3, S_TOUCH)
        closed(runtime, 4, S_ENTRY)
        assert runtime.aggregator.updates_applied == 0
        assert runtime.aggregator.symbols() == ()
        assert runtime.aggregator.forming(SYMBOL) is None
        assert runtime.updates_processed == 0
        assert runtime.strategy.unpersisted_strategy_state()[
            "last_update_ts"] == {}, (
            "an offline run leaves no execution-clock marker at all")

    def test_09d_the_execution_clock_is_not_persisted(self):
        """A restart resumes from the exchange's frames, not a stale local hour.

        The forming candle is reconstruction, not authority. Persisting it would
        let a restart inherit a partially-observed hour and treat it as fact.
        """
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 5, 0, 98.2, 98.6, 98.0))
        snapshot = runtime.snapshot()
        blob = repr(snapshot)
        for token in ("forming", "aggregator", "updates_processed",
                      "last_update_ts", "bucket_idx"):
            assert token not in blob, (
                f"{token!r} must not enter the persisted envelope")
        assert set(snapshot) == {
            "wiring_schema", "wiring_schema_version", "timeframe", "symbols",
            "disabled", "account_id", "account_balance", "balance_at_fill",
            "strategy"}

# ---------------------------------------------------------------------------
# Ambiguity fails closed. Nothing here is repaired, averaged or guessed.
# ---------------------------------------------------------------------------
class TestFailClosed:

    def test_an_update_that_predates_the_last_one_is_refused(self):
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 20, 0, 98.2, 98.6, 98.0))
        with pytest.raises(StaleMarketUpdateError):
            runtime.handle_market_update_payload(
                frame(3, 19, 59, 98.2, 98.6, 98.0))

    def test_two_different_prices_for_the_same_instant_are_refused(self):
        """Which one is true decides whether an order block was touched."""
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 20, 0, 98.2, 98.6, 98.0))
        with pytest.raises(AmbiguousMarketUpdateError):
            runtime.handle_market_update_payload(
                frame(3, 20, 0, 98.9, 98.9, 98.0))

    def test_a_finalized_hour_is_closed_and_its_candle_is_left_intact(self):
        """Two guards, in this order: monotonicity, then the finalized bucket.

        A monotone stream can only ever reach the first of them -- an update
        inside an hour that has already rolled over necessarily predates the
        update that rolled it. Either way the refusal is a refusal: the
        finalized candle is not re-opened and not amended.
        """
        runtime = make_runtime()
        build_structure(runtime)
        runtime.handle_market_update_payload(frame(3, 5, 0, 98.2, 98.6, 98.0))
        runtime.handle_market_update_payload(
            frame(4, 5, 0, 98.3, 98.4, 98.3, open_=98.3))
        bucket = bucket_index_for(ts_at(3))
        assert runtime.aggregator.is_finalized(SYMBOL, bucket)
        with pytest.raises(StaleMarketUpdateError):
            runtime.handle_market_update_payload(
                frame(3, 55, 0, 98.4, 98.6, 98.0))
        assert runtime.aggregator.finalized_buckets(SYMBOL) == (bucket,)
        assert runtime.aggregator.forming(SYMBOL).bucket_idx == bucket + 1
        assert runtime.aggregator.candles_finalized == 1

    def test_the_finalized_bucket_guard_holds_against_a_mismatched_index(self):
        """Defence in depth, below the monotonic guard.

        `apply()` also accepts an already-normalised `MarketUpdate`, so it does
        not get to assume the caller's `bucket_idx` agrees with the caller's
        `ts`. `normalize_market_update` derives one from the other and would
        never produce this pair; the guard is what makes a caller that does
        produce it fail closed rather than re-open a completed candle.
        """
        aggregator = LiveCandleAggregator("1h")
        aggregator.update(frame(3, 5, 0, 98.2, 98.6, 98.0))
        aggregator.update(frame(4, 5, 0, 98.3, 98.4, 98.3, open_=98.3))
        bucket = bucket_index_for(ts_at(3))
        assert aggregator.is_finalized(LOCAL, bucket)
        with pytest.raises(FinalizedBucketError):
            aggregator.apply(MarketUpdate(
                symbol=LOCAL, ts=at(4, 10, 0), price=98.4, high=98.4,
                low=98.4, bucket_idx=bucket, source=SOURCE_PRICE))
        assert aggregator.candles_finalized == 1
        assert aggregator.forming(LOCAL).close == 98.3

    def test_a_frame_whose_declared_hour_contradicts_its_tick_is_refused(self):
        """One of the two fields is wrong, and which one mis-times an entry."""
        payload = frame(3, 20, 0, 98.2, 98.6, 98.0)
        payload["candle_start_ts"] = int(ts_at(2).timestamp())
        with pytest.raises(MalformedMarketUpdateError):
            normalize_market_update(payload, "1h")

    def test_a_closed_frame_is_refused_by_the_execution_clock(self):
        payload = frame(3, 20, 0, 98.2, 98.6, 98.0)
        payload["is_closed"] = True
        with pytest.raises(MalformedMarketUpdateError):
            normalize_market_update(payload, "1h")

    def test_a_price_outside_its_own_range_is_refused(self):
        with pytest.raises(MalformedMarketUpdateError):
            normalize_market_update(frame(3, 20, 0, 99.9, 98.6, 98.0), "1h")

    def test_a_non_positive_or_non_finite_price_is_refused(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), True, "98.2"):
            with pytest.raises(MalformedMarketUpdateError):
                normalize_market_update(
                    {"symbol": LOCAL, "price": bad, "ts": at(3, 20, 0)}, "1h")

    def test_an_unsupported_timeframe_is_refused_not_guessed(self):
        with pytest.raises(MalformedMarketUpdateError):
            normalize_market_update(
                {"symbol": LOCAL, "price": 98.2, "ts": at(3, 20, 0)}, "5m")

    def test_an_unknown_symbol_is_refused(self):
        runtime = make_runtime()
        with pytest.raises(RT.UnknownFeedSymbolError):
            runtime.handle_market_update_payload(
                frame(3, 20, 0, 98.2, 98.6, 98.0, symbol="DOGEUSD"))

    def test_only_first_touch_activation_may_run_on_the_execution_clock(self):
        """Mode C is close-based research geometry; a tick cannot serve it.

        Rather than reinterpreting that mode for intra-hour prices -- which would
        change what it means -- the execution clock refuses it outright.
        """
        runtime = make_runtime(
            strategy=make_strategy(activation=ACTIVATION_MODE_ORACLE_C))
        build_structure(runtime)
        with pytest.raises(LifecycleClockError):
            runtime.handle_market_update_payload(
                frame(3, 20, 0, 98.2, 98.6, 98.0))

    def test_a_disabled_pair_still_keeps_a_truthful_forming_candle(self):
        """Disabling a pair is routing only, not amnesia.

        The fold happens before the routing decision, so re-enabling mid-hour
        does not leave a hole in the hour's OHLC.
        """
        runtime = make_runtime()
        build_structure(runtime)
        runtime.disable(SYMBOL)
        step = runtime.handle_market_update_payload(
            frame(3, 20, 0, 98.2, 98.6, 98.0))
        assert step.skipped == RT.SKIPPED_DISABLED
        assert step.evaluation is None
        assert step.forming.close == 98.2
        runtime.enable(SYMBOL)
        back = runtime.handle_market_update_payload(
            frame(3, 21, 0, 98.3, 98.6, 98.0))
        assert back.skipped is None
        assert back.forming.update_count == 2

    def test_the_transport_callback_counts_a_refusal_and_survives_it(self):
        runtime = make_runtime()
        build_structure(runtime)
        callback = RT.make_market_update_callback(runtime)
        assert callback({"symbol": LOCAL}) is None
        assert runtime.updates_refused == 1
        assert callback(frame(3, 20, 0, 98.2, 98.6, 98.0)) is not None
        assert runtime.updates_processed == 1

# ---------------------------------------------------------------------------
# The rule that keeps the seam honest: a frame's RANGE may arm and fill an order
# block, but only a published PRICE may resolve an exit.
# ---------------------------------------------------------------------------
class TestTheRangeIsForOrderBlocksNotExits:
    """A forming frame's high/low span the WHOLE hour so far.

    An extreme it reports may therefore have occurred BEFORE a fill made later in
    the same hour. Resolving a stop or a target from it would invent an exit that
    never happened after the fill, so step 1 sees the instantaneous price only.
    The execution clock can bring an exit FORWARD; it can never manufacture one.
    """

    @staticmethod
    def filled():
        """Structure, then the touch and the fill: filled at 03:24:02."""
        runtime = make_runtime()
        build_structure(runtime)
        for payload in tick_frames()[:3]:
            runtime.handle_market_update_payload(payload)
        trade = runtime.strategy.lifecycle.active_trade
        assert trade is not None and trade.fill_dt == at(3, ENTRY_M, ENTRY_S)
        return runtime, trade

    def test_a_low_that_predates_the_fill_does_not_take_the_target(self):
        """98.0 is this hour's low and it printed at 03:10, before the fill."""
        runtime, trade = self.filled()
        assert trade.tp_price > 98.0, "the hour's low is beyond the target"
        step = runtime.handle_market_update_payload(tick_frames()[3])
        assert step.forming.low == 98.0, "the frame does report that low"
        assert [e.event_type for e in step.evaluation.events] == []
        assert runtime.strategy.lifecycle.active_trade is trade
        assert runtime.strategy.lifecycle.exits == []

    def test_a_high_that_predates_the_fill_does_not_take_the_stop(self):
        runtime, trade = self.filled()
        step = runtime.handle_market_update_payload(
            frame(3, 32, 0, 100.6, 105.2, 98.0))
        assert step.forming.high == 105.2
        assert step.forming.high > trade.sl_price
        assert [e.event_type for e in step.evaluation.events] == []
        assert runtime.strategy.lifecycle.active_trade is trade
        assert runtime.strategy.lifecycle.exits == []

    def test_a_published_price_at_the_target_closes_the_trade_at_that_instant(
            self):
        """And it closes MID-HOUR: this is the exit the seam brings forward.

        The closed candle for bar 3 cannot resolve this exit at all -- its own
        range predates the fill -- so on the candle clock alone the earliest
        resolution is bar 4's candle, which does not exist until 05:00.
        """
        runtime, trade = self.filled()
        when = at(3, 31, 0)
        step = runtime.handle_market_update_payload(
            frame(3, 31, 0, 99.8, 100.5, 98.0))
        assert [e.event_type for e in step.evaluation.events] == [
            ET.TRADE_CLOSED]
        assert step.evaluation.events[0].ts == when
        assert step.evaluation.closed is not None
        exit_record = runtime.strategy.lifecycle.exits[-1]
        assert exit_record.outcome == OUTCOME_TP
        assert exit_record.reason_for_exit == REASON_TP_HIT
        assert exit_record.exit_price == trade.tp_price
        assert exit_record.exit_dt == when
        assert exit_record.exit_bar_idx == RT.bar_index_for(ts_at(3))
        assert exit_record.fill_dt == at(3, ENTRY_M, ENTRY_S)
        assert runtime.strategy.lifecycle.active_trade is None
        assert OB_ID not in runtime.strategy.lifecycle.live_obs

    def test_an_exit_is_never_resolved_from_the_fills_own_instant(self):
        """`ts <= fill_dt` is refused: the fill's own frame cannot also exit."""
        runtime = make_runtime()
        build_structure(runtime)
        for payload in tick_frames()[:2]:
            runtime.handle_market_update_payload(payload)
        # This one frame reaches the entry AND spans the target, at one instant.
        step = runtime.handle_market_update_payload(
            frame(3, ENTRY_M, ENTRY_S, 100.5, 100.5, 98.0))
        assert [e.event_type for e in step.evaluation.events] == [ET.ENTRY_FILLED]
        assert runtime.strategy.lifecycle.exits == []

# ---------------------------------------------------------------------------
# The entry window: the live clock enforces only the half of the rule that
# cannot be decided by transport timing.
# ---------------------------------------------------------------------------
INERT_ROW = (98.5, 98.9, 98.0, 98.5)     # touches nothing, breaches nothing


def events_for(step, ob_id: str = OB_ID):
    return [e.event_type for e in step.evaluation.events if e.ob_id == ob_id]


class TestWindowExpiryIsFillRefusingNotDestructive:
    """Expiry is a BAR COUNT, and bars are the structure clock's unit.

    A live update that arrives past the window refuses to fill -- that half is
    unambiguous and fails closed. It does NOT delete the order block, because the
    closed candle for the window's last bar may still be in flight behind it, and
    transport timing must never decide whether a legitimate trade exists.
    """

    @staticmethod
    def touched():
        """Zone touched at 03:23:17, entry never reached: limit resting."""
        runtime = make_runtime()
        build_structure(runtime)
        step = runtime.handle_market_update_payload(tick_frames()[1])
        assert events_for(step) == [ET.FIRST_TOUCH_LIMIT_ACTIVATED]
        ob = the_ob(runtime)
        assert ob.state is OBS.LIMIT_RESTING
        assert ob.limit_active_from_bar == RT.bar_index_for(ts_at(3))
        assert (ob.limit_active_from_bar + ENTRY_WINDOW_CANDLES - 1
                == RT.bar_index_for(ts_at(5))), "window is bars 3..5 inclusive"
        return runtime, ob

    def test_a_live_update_past_the_window_refuses_the_fill(self):
        runtime, ob = self.touched()
        step = runtime.handle_market_update_payload(
            frame(6, 10, 0, 100.6, 100.6, 98.0))
        assert step.forming.high >= ENTRY_25PCT, "the entry WAS reached"
        assert events_for(step) == [], "and it must not have filled"
        assert runtime.strategy.lifecycle.active_trade is None
        assert step.evaluation.filled is None

    def test_that_refusal_does_not_destroy_the_order_block(self):
        runtime, ob = self.touched()
        runtime.handle_market_update_payload(frame(6, 10, 0, 100.6, 100.6, 98.0))
        assert OB_ID in runtime.strategy.lifecycle.live_obs
        assert ob.state is OBS.LIMIT_RESTING
        assert runtime.strategy.lifecycle.live_obs[OB_ID] is ob

    def test_the_closed_candle_still_expires_it_once_at_the_same_bar(self):
        runtime, ob = self.touched()
        runtime.handle_market_update_payload(frame(6, 10, 0, 100.6, 100.6, 98.0))
        seen = []
        for bar in (3, 4, 5, 6):
            seen.extend(events_for(closed(runtime, bar, INERT_ROW)))
        assert seen == [ET.INVALIDATED], (
            "exactly one expiry, and only on the candle after bar 5")
        assert ob.state is OBS.INVALIDATED
        assert OB_ID not in runtime.strategy.lifecycle.live_obs
        assert runtime.strategy.lifecycle.exits == []

# ---------------------------------------------------------------------------
# One integer, two clocks. If these ever disagreed, a live update would be
# attributed to a different candle than its own closed candle.
# ---------------------------------------------------------------------------
class TestTheTwoClocksShareOneBarIndex:

    def test_every_instant_inside_an_hour_maps_to_that_hours_bar(self):
        expected = RT.bar_index_for(ts_at(3))
        for minute, second, micros in ((0, 0, 0), (23, 17, 0), (24, 2, 500_000),
                                       (59, 59, 999_999)):
            assert bucket_index_for(at(3, minute, second, micros)) == expected

    def test_the_boundary_belongs_to_the_hour_it_opens(self):
        assert bucket_index_for(ts_at(4)) == RT.bar_index_for(ts_at(4))
        assert bucket_index_for(at(3, 59, 59, 999_999)) == RT.bar_index_for(
            ts_at(3))
        assert (RT.bar_index_for(ts_at(4)) - RT.bar_index_for(ts_at(3))) == 1

    def test_the_bucket_opening_instant_round_trips(self):
        for bar in range(0, 7):
            for minute in (0, 17, 45):
                idx = bucket_index_for(at(bar, minute, 3))
                assert bucket_open_ts(idx) == ts_at(bar)
                assert RT.bar_index_for(bucket_open_ts(idx)) == idx

    def test_the_runtime_reports_the_same_bar_for_an_update_as_for_its_candle(
            self):
        runtime = make_runtime()
        build_structure(runtime)
        live = runtime.handle_market_update_payload(tick_frames()[1])
        control = make_runtime()
        build_structure(control)
        candle = closed(control, 3, S_TOUCH)
        assert live.bar_idx == candle.bar_idx == RT.bar_index_for(ts_at(3))


# ---------------------------------------------------------------------------
# The seam is not tied to the candlestick channel. Whether Delta India exposes a
# dedicated trades or ticker channel is UNKNOWN in this repository -- so the
# execution clock accepts a bare price print too, and behaves identically.
# ---------------------------------------------------------------------------
class TestABarePriceUpdateDrivesTheSameSeam:

    @staticmethod
    def price_payload(when, price):
        return {"symbol": LOCAL, "price": price, "ts": when,
                "source": SOURCE_PRICE}

    def test_a_single_trade_print_can_touch_and_fill(self):
        """`high == low == price` is the degenerate pair the predicates take."""
        runtime = make_runtime()
        build_structure(runtime)
        step = runtime.handle_market_update_payload(
            self.price_payload(at(3, ENTRY_M, ENTRY_S), ENTRY_25PCT))
        assert [e.event_type for e in step.evaluation.events] == [
            ET.FIRST_TOUCH_LIMIT_ACTIVATED, ET.ENTRY_FILLED]
        trade = runtime.strategy.lifecycle.active_trade
        assert trade.entry_price == ENTRY_25PCT
        assert trade.fill_dt == at(3, ENTRY_M, ENTRY_S)

    def test_a_trade_print_opens_the_hour_without_claiming_the_exchange_open(
            self):
        runtime = make_runtime()
        build_structure(runtime)
        step = runtime.handle_market_update_payload(
            self.price_payload(at(3, 10, 0), 98.2))
        forming = step.forming
        assert (forming.open, forming.high, forming.low, forming.close) == (
            98.2, 98.2, 98.2, 98.2)
        assert forming.open_from_exchange is False
        assert forming.sources == (SOURCE_PRICE,)

    def test_the_strategy_refuses_a_reordered_update_on_its_own(self):
        """Belt and braces: the aggregator's guard is not the only one."""
        strategy = make_strategy()
        for bar in sorted(STRUCTURE_ROWS):
            row = STRUCTURE_ROWS[bar]
            strategy.evaluate_closed_candle(SYMBOL, RT.bar_index_for(ts_at(bar)),
                                            ts_at(bar), *row)
        bar_idx = RT.bar_index_for(ts_at(3))
        strategy.evaluate_market_update(SYMBOL, bar_idx, at(3, 20, 0), 98.2)
        with pytest.raises(StrategyStaleUpdateError):
            strategy.evaluate_market_update(SYMBOL, bar_idx, at(3, 19, 0), 98.2)

    def test_the_strategy_refuses_an_update_inside_an_already_closed_hour(self):
        strategy = make_strategy()
        for bar in sorted(STRUCTURE_ROWS):
            row = STRUCTURE_ROWS[bar]
            strategy.evaluate_closed_candle(SYMBOL, RT.bar_index_for(ts_at(bar)),
                                            ts_at(bar), *row)
        with pytest.raises(StrategyStaleUpdateError):
            strategy.evaluate_market_update(
                SYMBOL, RT.bar_index_for(ts_at(2)), at(2, 30, 0), 98.0)

# ---------------------------------------------------------------------------
# Required proof 10 -- no live exchange operation is performed during testing.
# ---------------------------------------------------------------------------
class TestNoExchangeOperation:
    """The whole mid-hour scenario, run with the network amputated.

    Every price here is a locally built fixture, no credential is read and no
    module that could reach Delta is started. This class removes the possibility
    rather than asserting the intention: socket creation, DNS resolution, TLS
    wrapping and `websockets.connect` are all made to raise, and the scenario is
    then re-run in full. The only thing it reaches is the `ExecutionStack`'s
    mocked transport, which returns a fabricated acknowledgement.

    The event loop is built BEFORE the amputation on purpose: on Windows its own
    wake-up self-pipe is a loopback `socketpair`, which is not an exchange
    operation and must not be confused with one.
    """

    NAMES = ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
             "socket.socketpair", "ssl.SSLContext.wrap_socket",
             "websockets.connect")

    @staticmethod
    def amputate(monkeypatch, attempts):
        def refuse(name):
            def boom(*args, **kwargs):
                attempts.append(name)
                raise AssertionError(
                    f"this test tried to reach the network via {name}")
            return boom
        for target, attr, name in (
                (socket, "socket", "socket.socket"),
                (socket, "create_connection", "socket.create_connection"),
                (socket, "getaddrinfo", "socket.getaddrinfo"),
                (socket, "socketpair", "socket.socketpair"),
                (ssl.SSLContext, "wrap_socket", "ssl.SSLContext.wrap_socket"),
                (WS.websockets, "connect", "websockets.connect")):
            monkeypatch.setattr(target, attr, refuse(name))

    @staticmethod
    async def scenario(runtime):
        for bar in sorted(STRUCTURE_ROWS):
            await runtime.process_closed_candle(
                RT.ClosedCandle(SYMBOL, ts_at(bar), *STRUCTURE_ROWS[bar]),
                user_id="u", account_id="acc")
        out = []
        for payload in tick_frames()[:3]:
            out.append(await runtime.process_market_update_payload(
                payload, user_id="u", account_id="acc"))
        return out

    def test_10_the_touch_the_fill_and_the_order_need_no_network(self,
                                                                monkeypatch):
        stack = ExecutionStack(symbols=(SYMBOL,))
        runtime = make_runtime(balance=100000.0, orchestrator=stack.orchestrator)
        loop = asyncio.new_event_loop()
        attempts = []
        try:
            self.amputate(monkeypatch, attempts)
            steps = loop.run_until_complete(self.scenario(runtime))
        finally:
            monkeypatch.undo()
            loop.close()
        assert attempts == [], f"the network was touched: {attempts}"
        assert len(stack.sent) == 1, "the resting LIMIT was still submitted"
        assert steps[1].submitted == (
            runtime.setup_id_for(SYMBOL, OB_ID, "SHORT"),)
        assert runtime.strategy.lifecycle.active_trade is not None

    def test_10b_the_transport_routes_a_frame_without_a_socket(self,
                                                              monkeypatch):
        """`_handle_message` is a coroutine over a decoded dict, nothing more."""
        runtime = make_runtime()
        build_structure(runtime)
        loop = asyncio.new_event_loop()
        attempts = []
        try:
            self.amputate(monkeypatch, attempts)
            client = ws_client(
                on_market_update=RT.make_market_update_callback(runtime))
            assert client.ws is None
            loop.run_until_complete(client._handle_message(ws_message()))
        finally:
            monkeypatch.undo()
            loop.close()
        assert attempts == []
        assert runtime.updates_processed + runtime.updates_refused == 1

    def test_10c_every_network_primitive_this_class_blocks_is_named(self):
        """The amputation list is asserted, so it cannot silently shrink."""
        blocked = []

        class Recorder:
            def __init__(self):
                self.calls = []

            def setattr(self, target, attr, value):
                blocked.append(f"{getattr(target, '__name__', target)}.{attr}")

            def undo(self):
                pass

        self.amputate(Recorder(), [])
        assert len(blocked) == len(self.NAMES)
        assert [n.rsplit(".", 1)[-1] for n in blocked] == [
            n.rsplit(".", 1)[-1] for n in self.NAMES]
