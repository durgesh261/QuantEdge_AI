"""FEED WATCHDOG HARDENING -- transport liveness is NOT market-data freshness.

Three defects are closed here, and every one of them was verified in the working
tree before a line was changed:

  1. FRAME-STALE FEED. The library keepalive can keep proving the socket is
     alive while the subscribed `candlestick_1h` channel produces nothing. In
     that state `/health` answers UP indefinitely - it reports DOWN only on a
     recorded fault or an inert bootstrap, and a silent channel records neither -
     while both clocks are starved. Nothing in the client measured frame
     arrival at all.
  2. APPLICATION HEARTBEAT BUGS. `_last_pong_ts` stamped the moment the ping was
     SENT (`await ws.ping()` awaits the send; the returned future is what
     resolves on the pong), so a missing pong was undetectable.
     `_check_heartbeat_timeout` had zero callers repo-wide, and had it ever been
     called it would have called `disconnect()` - which clears `running` and
     therefore CANCELS the recovery in `listen()`'s `finally`. `_start_heartbeat`
     overwrote `self._heartbeat_task` without cancelling it, leaking one live
     loop per reconnect.
  3. RECONNECT LIFECYCLE. `_reconnect` grew the stack from two directions -
     `listen()`'s `finally` awaits it, and its own `except` awaited itself - so a
     feed that flapped for days grew a stack that was never released.

WHAT IS DELIBERATELY NOT CLAIMED. No test here asserts a publication cadence for
`candlestick_1h`. Delta documents an interval for the `ticker` channel and
documents none for `candlesticks`, and nothing in this repository measures one.
The stale threshold is therefore the BAR PERIOD, which this repository proves
twice (`_is_candle_closed` floors to `now - (now % 3600)`;
`restart_recovery.newest_closed_bar_idx` uses the same 3600s bar) and already
treats as mandatory (one missing closed candle is escalated to a fatal structure
fault). Tests below inject tiny thresholds to stay deterministic; they assert the
MECHANISM, never a cadence.

NO NETWORK, NO CREDENTIALS, NO EXCHANGE, NO ORDER. Every socket below is a local
double. `TestNothingHereTrades` proves this module cannot reach an order path.

Required-proof map (the mandate's twelve items):
    01 valid frame refreshes freshness ......... TestFrameFreshnessIsStamped
    02 stale feed closes the socket ............ TestStaleFeedClosesTheSocket
    03 stale watchdog does NOT reconnect ....... TestTheWatchdogOnlyClosesTheSocket
    04 closure reaches existing reconnect ..... TestClosureReachesTheExistingPath
    05 ping/pong is not market data ........... TestFrameFreshnessIsStamped
    06 no duplicate task across reconnect ..... TestWatchdogTaskLifecycle
    07 shutdown/cancellation is clean ......... TestWatchdogTaskLifecycle
    08 no unbounded recursive reconnect ....... TestReconnectIsIterativeNotRecursive
    09 the 10-failure fatal path is intact .... TestTheConsecutiveFailureCapIsIntact
    10 LIVE_FEED_ENABLED=false stays inert .... TestTheDefaultsAreUnchanged
    11 closed-candle structure path unchanged . TestTheClosedCandlePathIsUnchanged
    12 no execution order is submitted ........ TestNothingHereTrades
"""

import ast
import asyncio
import inspect
import json
import logging
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quantedge.config import Settings  # noqa: E402
from quantedge.market_data import delta_websocket as WS  # noqa: E402

US = 1_000_000
SYMBOL = "BTCUSD"
CLIENT_SRC = Path(WS.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------------- doubles
class FakeSocket:
    """A socket that records what was done to it and opens nothing.

    Iteration blocks until `close()` is called and then ENDS, which is how the
    real client experiences a socket the watchdog closed: `listen()` falls out of
    its `async for` and its `finally` decides what happens next.
    """

    def __init__(self) -> None:
        self.close_calls = 0
        self.ping_calls = 0
        self.sent: list = []
        self._closed = asyncio.Event()

    async def close(self) -> None:
        self.close_calls += 1
        self._closed.set()

    async def ping(self) -> None:
        self.ping_calls += 1

    async def send(self, payload) -> None:
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration


class FeedingSocket(FakeSocket):
    """A socket that delivers queued frames and then goes silent.

    Silence, not closure: iteration blocks after the queue drains, exactly as a
    real socket does when the subscribed channel stops publishing while the
    transport stays up. That is the failure mode the watchdog exists for.
    """

    def __init__(self, frames=()) -> None:
        super().__init__()
        self._frames = [json.dumps(f) for f in frames]

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        await self._closed.wait()
        raise StopAsyncIteration


def ws_client(**over):
    """The real transport with persistence off and no socket."""
    tmp = Path(tempfile.mkdtemp())
    kwargs = dict(persist=False, csv_path=tmp / "feed.csv",
                  meta_path=tmp / "feed.meta.json")
    kwargs.update(over)
    return WS.DeltaWebSocketClient(**kwargs)


def watchdog_client(**over):
    """A client whose watchdog fires in milliseconds instead of in an hour.

    The injected values are what keep these tests deterministic. They are NOT a
    claim about the production threshold, which is `FRAME_STALE_TIMEOUT_SECONDS`.
    """
    kwargs = dict(frame_stale_after=0.02, freshness_poll_seconds=0.001)
    kwargs.update(over)
    return ws_client(**kwargs)


def lifecycle_client(**over):
    """A client whose watchdog stays parked in its poll sleep.

    Task lifecycle is the subject of those tests, so the watchdog must be alive
    and must NOT fire: a long poll keeps it in a cancellable sleep instead of
    racing the test.
    """
    kwargs = dict(frame_stale_after=3600, freshness_poll_seconds=30)
    kwargs.update(over)
    return ws_client(**kwargs)


def forming_frame(second_offset: int = 7, **over) -> dict:
    """A raw Delta India `candlestick_1h` frame inside the CURRENT hour."""
    now = int(time.time())
    start = now - (now % 3600)
    stamp = (start + second_offset) * US + 489993
    frame = {"type": "candlestick_1h", "symbol": SYMBOL, "resolution": "1h",
             "open": 77809.5, "high": 77865.5, "low": 77338.5,
             "close": 77428.5, "volume": 372963.0,
             "candle_start_time": start * US,
             "timestamp": stamp, "last_updated": stamp,
             "sUID": "BTCUSD_#_BTCUSD_#_60"}
    frame.update(over)
    return frame


def closed_frame(**over) -> dict:
    """A frame for the PREVIOUS hour, which `_is_candle_closed` accepts."""
    now = int(time.time())
    start = now - (now % 3600) - 3600
    stamp = (start + 3599) * US + 12345
    return forming_frame(candle_start_time=start * US, timestamp=stamp,
                         last_updated=stamp, **over)


def calls_in(func) -> set:
    """Every callee name in a function BODY. Docstring prose is excluded.

    The watchdog's docstring names `_reconnect` and `disconnect` precisely to say
    it must not call them, so a substring check would prove the opposite of the
    truth. This is structural.
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


def watchdog_tasks() -> list:
    """Every live task running the freshness watchdog, however it was started."""
    return [t for t in asyncio.all_tasks()
            if t.get_coro().__qualname__.endswith("_watchdog_loop")]


# ---------------------------------------------------------------------------
# 01 + 05. What does, and does not, count as market data arriving.
# ---------------------------------------------------------------------------
class TestFrameFreshnessIsStamped:

    def test_a_valid_market_data_frame_refreshes_the_stamp(self):
        client = ws_client()
        assert client._last_frame_ts is None, "no socket, no frame, no stamp"
        before = time.monotonic()
        asyncio.run(client._handle_message(forming_frame()))
        assert client._last_frame_ts is not None
        assert client._last_frame_ts >= before, (
            "the stamp is a monotonic reading taken when the frame arrived")

    def test_every_later_frame_moves_the_stamp_forward(self):
        client = ws_client()
        asyncio.run(client._handle_message(forming_frame(second_offset=1)))
        first = client._last_frame_ts
        asyncio.run(client._handle_message(forming_frame(second_offset=2)))
        assert client._last_frame_ts >= first
        assert client._last_frame_ts is not first or first == 0

    def test_a_closed_frame_also_counts_as_market_data(self):
        """Freshness is about supply, not about which clock consumes it."""
        client = ws_client()
        asyncio.run(client._handle_message(closed_frame()))
        assert client._last_frame_ts is not None

    @pytest.mark.parametrize(
        "control", ["subscriptions", "heartbeat", "ping", "pong", "info",
                    "l2_orderbook", "trades", ""])
    def test_a_control_frame_never_refreshes_the_stamp(self, control):
        """Item 05: transport liveness must not masquerade as market data.

        A pong proves the SOCKET is alive. The starved-feed failure is a live
        socket delivering no candles, so if a pong refreshed this stamp the
        watchdog would be blind to exactly the state it exists to catch.
        """
        client = ws_client()
        client._last_frame_ts = -1.0
        asyncio.run(client._handle_message({"type": control, "x": 1}))
        assert client._last_frame_ts == -1.0, (
            f"a {control!r} frame is not market data")

    def test_a_candle_for_an_unsubscribed_pair_never_refreshes_the_stamp(self):
        client = ws_client()          # BTCUSD only, by default
        client._last_frame_ts = -1.0
        asyncio.run(client._handle_message(forming_frame(symbol="ETHUSD")))
        assert client._last_frame_ts == -1.0, (
            "ETHUSD is a real registry pair but this client did not subscribe "
            "to it; another pair's frame cannot vouch for this feed")

    def test_a_candle_for_an_unregistered_symbol_never_refreshes_the_stamp(self):
        client = ws_client()
        client._last_frame_ts = -1.0
        asyncio.run(client._handle_message(forming_frame(symbol="NOTAPAIR")))
        assert client._last_frame_ts == -1.0

    @pytest.mark.parametrize("mangle", [
        {"close": None},              # KeyError-free but absent below
        {"candle_start_time": "abc"},  # int("abc") -> ValueError
    ])
    def test_a_malformed_candle_never_refreshes_the_stamp(self, mangle):
        client = ws_client()
        client._last_frame_ts = -1.0
        frame = forming_frame()
        for key, value in mangle.items():
            if value is None:
                frame.pop(key, None)
            else:
                frame[key] = value
        asyncio.run(client._handle_message(frame))
        assert client._last_frame_ts == -1.0

    def test_a_nonnumeric_price_stamps_nothing_though_it_escapes_the_parser(self):
        """A PRE-EXISTING parser gap, pinned here only for what this work owns.

        `_parse_candle_from_ws` catches `(KeyError, ValueError, TypeError)`, and
        `Decimal(str(...))` on a non-numeric string raises
        `decimal.InvalidOperation`, which is an `ArithmeticError` and therefore
        escapes. That is the behaviour BEFORE this hardening and it is out of
        scope: the mandate forbids modifying `candlestick_1h` parsing semantics.
        It is reported as a remaining unknown rather than silently changed.

        What this test pins is the property the watchdog rests on: even on that
        path the freshness stamp is NOT written, so a channel emitting nothing
        but garbage still reads as stale.
        """
        client = ws_client()
        client._last_frame_ts = -1.0
        with pytest.raises(Exception):
            asyncio.run(client._handle_message(
                forming_frame(close="not-a-number")))
        assert client._last_frame_ts == -1.0

    def test_only_the_parser_gate_and_a_new_socket_may_stamp_freshness(self):
        """Structural: the stamp cannot be written from anywhere else.

        Behavioural tests prove the current control frames do not stamp. This
        proves no FUTURE path can start stamping without a reader noticing,
        which is the property the whole watchdog rests on.
        """
        tree = ast.parse(CLIENT_SRC)
        writers = set()
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                elif isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    continue
                if any(isinstance(t, ast.Attribute)
                       and t.attr == "_last_frame_ts" for t in targets):
                    writers.add(func.name)
        assert writers == {"__init__", "connect", "_handle_message"}, (
            f"unexpected writer of the freshness stamp: {writers}")

    def test_the_client_sends_no_pings_of_its_own(self):
        """The library owns transport liveness; nothing here fakes it.

        The removed application heartbeat stamped its own send time and called it
        a pong, which is why it could never detect a missing pong.
        """
        assert ".ping(" not in CLIENT_SRC
        assert "_last_pong_ts" not in CLIENT_SRC
        assert "_start_heartbeat" not in CLIENT_SRC
        assert "_check_heartbeat_timeout" not in CLIENT_SRC
        assert not hasattr(WS, "EVENT_HEARTBEAT")
        assert not hasattr(WS, "EVENT_HEARTBEAT_TIMEOUT")
        assert not hasattr(WS, "HEARTBEAT_INTERVAL_SECONDS")
        assert not hasattr(WS, "HEARTBEAT_TIMEOUT_SECONDS")


# ---------------------------------------------------------------------------
# 02. A silent channel ends the socket.
# ---------------------------------------------------------------------------
class TestStaleFeedClosesTheSocket:

    def test_a_stale_feed_closes_the_socket(self):
        client = watchdog_client()
        sock = FakeSocket()
        client.ws = sock
        client.running = True
        client._last_frame_ts = time.monotonic() - 5.0

        asyncio.run(asyncio.wait_for(client._watchdog_loop(), 5))
        assert sock.close_calls == 1, (
            "the socket is closed exactly once and the loop then returns; "
            "recovery belongs to the reconnect path, not to a retry here")

    def test_a_fresh_feed_closes_nothing(self):
        client = ws_client(frame_stale_after=3600, freshness_poll_seconds=0.001)
        sock = FakeSocket()
        client.ws = sock
        client.running = True

        async def scenario():
            client._last_frame_ts = time.monotonic()
            task = asyncio.create_task(client._watchdog_loop())
            for _ in range(20):                  # ~20 poll cycles of real time
                await asyncio.sleep(0.005)
                client._last_frame_ts = time.monotonic()
            client.running = False
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        assert sock.close_calls == 0, (
            "a feed that keeps delivering must never be closed")

    def test_a_feed_that_has_never_delivered_is_not_judged_before_it_starts(self):
        """`None` means "no reference yet", and it is never treated as stale.

        `connect()` stamps the reference, so in production this state does not
        outlive a connection. It must still not be able to close a socket.
        """
        client = watchdog_client()
        sock = FakeSocket()
        client.ws = sock
        client.running = True
        client._last_frame_ts = None

        async def scenario():
            task = asyncio.create_task(client._watchdog_loop())
            await asyncio.sleep(0.05)
            client.running = False
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        assert sock.close_calls == 0


# ---------------------------------------------------------------------------
# 03. The watchdog closes the socket and does NOTHING else.
# ---------------------------------------------------------------------------
class TestTheWatchdogOnlyClosesTheSocket:

    def _stale_run(self, client):
        sock = FakeSocket()
        client.ws = sock
        client.running = True
        client._last_frame_ts = time.monotonic() - 5.0
        asyncio.run(asyncio.wait_for(client._watchdog_loop(), 5))
        return sock

    def test_the_watchdog_never_calls_reconnect_connect_subscribe_or_disconnect(self):
        client = watchdog_client()

        async def forbidden(*a, **k):
            raise AssertionError(
                "the watchdog must hand recovery to the existing outer path")

        for name in ("_reconnect", "connect", "subscribe", "disconnect"):
            setattr(client, name, forbidden)
        sock = self._stale_run(client)
        assert sock.close_calls == 1

    def test_the_watchdog_leaves_the_recovery_state_untouched(self):
        """`disconnect()` clears `running`, and that CANCELS recovery.

        `listen()` reconnects only `if self.running`. The removed heartbeat
        helper called `disconnect()` first, so had it ever run it would have
        disabled the very path it was trying to trigger.
        """
        client = watchdog_client()
        sock = self._stale_run(client)
        assert sock.close_calls == 1
        assert client.running is True, (
            "clearing `running` here would make `listen()` skip the reconnect")
        assert client.ws is sock, (
            "the client keeps its socket reference; `listen()` is still "
            "iterating it and the reconnect path replaces it")
        assert client.reconnect_attempt == 0, (
            "the watchdog does not consume a reconnect attempt")
        assert client._reconnecting is False

    def test_structurally_the_watchdog_calls_none_of_them(self):
        called = calls_in(WS.DeltaWebSocketClient._watchdog_loop)
        for forbidden in ("_reconnect", "connect", "subscribe", "disconnect",
                          "_start_watchdog", "_backfill_gaps", "run"):
            assert forbidden not in called, (
                f"_watchdog_loop must not call {forbidden}")
        assert "close" in called, "closing the socket is what it does do"

    def test_the_stale_verdict_is_logged_as_a_feed_event_with_its_evidence(self):
        client = watchdog_client()
        events = []
        client._log_event = lambda event, **kw: events.append((event, kw))
        sock = self._stale_run(client)
        assert sock.close_calls == 1
        assert [e for e, _ in events] == [WS.EVENT_FEED_STALE]
        _, detail = events[0]
        assert detail["threshold_seconds"] == client._frame_stale_after
        assert detail["silent_for_seconds"] > client._frame_stale_after, (
            "the log carries the measured silence, so an operator can tell a "
            "starved channel from a dropped socket without guessing")

    def test_a_close_that_itself_fails_does_not_raise_out_of_the_watchdog(self):
        """A socket that refuses to close must not kill the task silently.

        The loop returns either way, and `listen()` still owns recovery.
        """
        client = watchdog_client()

        class Stubborn(FakeSocket):
            async def close(self):
                self.close_calls += 1
                raise OSError("socket already gone")

        sock = Stubborn()
        client.ws = sock
        client.running = True
        client._last_frame_ts = time.monotonic() - 5.0
        asyncio.run(asyncio.wait_for(client._watchdog_loop(), 5))
        assert sock.close_calls == 1

    def test_a_watchdog_with_no_socket_does_not_raise(self):
        client = watchdog_client()
        client.ws = None
        client.running = True
        client._last_frame_ts = time.monotonic() - 5.0
        asyncio.run(asyncio.wait_for(client._watchdog_loop(), 5))


# ---------------------------------------------------------------------------
# 04. Closing the socket reaches the reconnect path that already existed.
# ---------------------------------------------------------------------------
class TestClosureReachesTheExistingPath:

    def test_the_watchdog_closure_reaches_reconnect_through_listen(self):
        """End to end, with no step faked in between.

        The watchdog closes -> `listen()`'s iteration ends -> its `finally`
        awaits `_reconnect`. That `finally` is untouched by this work; the whole
        recovery design is to feed it, not to replace it.
        """
        client = watchdog_client()
        sock = FakeSocket()
        client.ws = sock
        client.running = True
        reconnects = []

        async def record_reconnect():
            reconnects.append(True)
            client.running = False          # keep the test finite

        client._reconnect = record_reconnect

        async def scenario():
            client._last_frame_ts = time.monotonic() - 5.0
            await asyncio.gather(client._watchdog_loop(), client.listen())

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert sock.close_calls == 1
        assert reconnects == [True], (
            "exactly one recovery, triggered by the socket ending - not by the "
            "watchdog calling reconnect itself")

    def test_listen_still_skips_reconnect_when_running_was_cleared(self):
        """The fatal-wedge stop hook clears `running`; that must still win."""
        client = ws_client()
        sock = FakeSocket()
        client.ws = sock
        client.running = False
        client._reconnect = None            # any call would raise TypeError

        async def scenario():
            await sock.close()
            await client.listen()

        asyncio.run(asyncio.wait_for(scenario(), 5))

    def test_listen_still_owns_recovery_and_was_not_rewritten(self):
        """`listen()` is the recovery seam and was deliberately not edited."""
        src = inspect.getsource(WS.DeltaWebSocketClient.listen)
        assert "if self.running:\n                await self._reconnect()" in src
        assert "_watchdog" not in src, (
            "the watchdog is started by connect(), not woven into the read loop")


# ---------------------------------------------------------------------------
# 06 + 07. One watchdog task, ever. Shutdown reaps it.
# ---------------------------------------------------------------------------
def patch_connect(monkeypatch, sockets):
    """Make `websockets.connect` hand out local doubles, opening no socket."""
    async def fake_connect(*args, **kwargs):
        sock = FakeSocket()
        sock.connect_kwargs = dict(kwargs)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(WS.websockets, "connect", fake_connect)
    return sockets


class TestWatchdogTaskLifecycle:

    def test_reconnecting_never_accumulates_watchdog_tasks(self, monkeypatch):
        """The old heartbeat leaked one live loop per reconnect.

        `connect()` runs again on every reconnect attempt, and the previous
        implementation simply rebound `self._heartbeat_task`. `running` stays
        True across a reconnect, so each orphan kept waking up and pinging
        whatever socket was current.
        """
        client = lifecycle_client()
        patch_connect(monkeypatch, [])
        seen = []

        async def scenario():
            for _ in range(5):
                await client.connect()
                seen.append(client._watchdog_task)
                assert len(watchdog_tasks()) == 1, (
                    "exactly one live watchdog, however many reconnects")
            await client.disconnect()

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert len(seen) == 5
        assert len(set(id(t) for t in seen)) == 5, "a new task each connect"
        for task in seen:
            assert task.done(), "every previous watchdog was reaped"

    def test_disconnect_cancels_and_reaps_the_watchdog(self, monkeypatch):
        client = lifecycle_client()
        patch_connect(monkeypatch, [])
        captured = {}

        async def scenario():
            await client.connect()
            captured["task"] = client._watchdog_task
            await client.disconnect()
            assert not watchdog_tasks(), "shutdown leaves no live task behind"

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert captured["task"].done()
        assert client._watchdog_task is None
        assert client.running is False
        assert client.ws is None

    def test_cancelling_with_no_watchdog_running_is_a_no_op(self):
        client = lifecycle_client()
        asyncio.run(client._cancel_watchdog())
        assert client._watchdog_task is None

    def test_cancelling_twice_is_safe(self, monkeypatch):
        client = lifecycle_client()
        patch_connect(monkeypatch, [])

        async def scenario():
            await client.connect()
            await client._cancel_watchdog()
            await client._cancel_watchdog()

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert client._watchdog_task is None

    def test_a_finished_watchdog_is_cleared_without_being_awaited(self):
        """A one-shot watchdog that already closed a socket is simply dropped."""
        client = watchdog_client()
        sock = FakeSocket()
        client.ws = sock
        client.running = True

        async def scenario():
            client._last_frame_ts = time.monotonic() - 5.0
            await client._start_watchdog()
            task = client._watchdog_task
            await asyncio.wait_for(task, 5)      # it fires and returns
            assert task.done() and not task.cancelled()
            await client._cancel_watchdog()
            return task

        task = asyncio.run(asyncio.wait_for(scenario(), 10))
        assert sock.close_calls == 1
        assert task.exception() is None
        assert client._watchdog_task is None

    def test_start_replaces_a_live_watchdog_rather_than_adding_one(self):
        client = lifecycle_client()
        client.running = True

        async def scenario():
            await client._start_watchdog()
            first = client._watchdog_task
            await client._start_watchdog()
            second = client._watchdog_task
            assert first is not second
            assert first.done(), "cancel-before-start, not start-and-forget"
            assert len(watchdog_tasks()) == 1
            await client._cancel_watchdog()

        asyncio.run(asyncio.wait_for(scenario(), 5))


# ---------------------------------------------------------------------------
# 08 + 09. Reconnect: iterative, capped, and still fatal after 10 in a row.
# ---------------------------------------------------------------------------
RECONNECT_CODE = WS.DeltaWebSocketClient._reconnect.__code__


def reconnect_depth() -> int:
    """How many `_reconnect` frames are on the stack right now.

    The old shape added one per flap, from `listen()`'s `finally` and from its
    own `except`, and never released them. This is the measurement of that.
    """
    depth, frame = 0, inspect.currentframe()
    while frame is not None:
        if frame.f_code is RECONNECT_CODE:
            depth += 1
        frame = frame.f_back
    return depth


@pytest.fixture
def instant_backoff(monkeypatch):
    """Zero backoff so the retry ladder is exercised in real time, not slept."""
    monkeypatch.setattr(WS, "MIN_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(WS, "MAX_BACKOFF_SECONDS", 0)
    return monkeypatch


class TestReconnectIsIterativeNotRecursive:

    def test_repeated_connect_failures_never_grow_the_stack(self, instant_backoff):
        client = lifecycle_client()
        depths = []

        async def failing_connect():
            depths.append(reconnect_depth())
            raise ConnectionRefusedError("no route to the exchange")

        async def no_backfill():
            return None

        client.connect = failing_connect
        client._backfill_gaps = no_backfill

        asyncio.run(asyncio.wait_for(client._reconnect(), 10))
        assert len(depths) == WS.MAX_RECONNECT_ATTEMPTS, (
            "ten consecutive attempts, exactly as before")
        assert set(depths) == {1}, (
            f"the retry ladder must stay one frame deep; saw {depths}")

    def test_flapping_through_listen_never_grows_the_stack(self, instant_backoff):
        """The other recursion arm: `listen()`'s `finally` re-enters `_reconnect`.

        Five successful connects that each die immediately. Under the old shape
        each flap left a frame behind for the life of the process.
        """
        client = lifecycle_client()
        depths, cycles = [], []

        async def flapping_connect():
            depths.append(reconnect_depth())
            cycles.append(True)
            sock = FakeSocket()
            await sock.close()               # dies the moment it is read
            client.ws = sock
            client.reconnect_attempt = 0     # what the real connect() does
            client.running = len(cycles) < 5
            client._last_frame_ts = time.monotonic()

        async def no_backfill():
            return None

        client.connect = flapping_connect
        client._backfill_gaps = no_backfill
        client.subscribe = no_backfill

        asyncio.run(asyncio.wait_for(client._reconnect(), 10))
        assert len(cycles) == 5, "the loop takes each new attempt itself"
        assert set(depths) == {1}, f"depth must stay 1; saw {depths}"
        assert client._reconnecting is False, "the guard is released"

    def test_a_nested_call_is_absorbed_and_changes_nothing(self):
        """What `listen()`'s `finally` does while the loop already owns retry."""
        client = lifecycle_client()

        async def forbidden(*a, **k):
            raise AssertionError("the nested call must do nothing at all")

        client.connect = forbidden
        client._backfill_gaps = forbidden
        client._reconnecting = True
        asyncio.run(asyncio.wait_for(client._reconnect(), 5))
        assert client.reconnect_attempt == 0, (
            "an absorbed call must not consume an attempt or reset the ladder")
        assert client._reconnecting is True, (
            "the owner of the loop clears the flag, not the absorbed call")

    def test_the_guard_is_released_even_when_the_ladder_gives_up(
            self, instant_backoff):
        client = lifecycle_client()

        async def failing_connect():
            raise OSError("still refused")

        async def no_backfill():
            return None

        client.connect = failing_connect
        client._backfill_gaps = no_backfill
        asyncio.run(asyncio.wait_for(client._reconnect(), 10))
        assert client._reconnecting is False


class TestTheConsecutiveFailureCapIsIntact:

    def _failing_client(self, calls, events):
        client = lifecycle_client()

        async def failing_connect():
            calls.append(True)
            raise ConnectionResetError("refused")

        async def no_backfill():
            return None

        client.connect = failing_connect
        client._backfill_gaps = no_backfill
        client._log_event = lambda event, **kw: events.append((event, kw))
        return client

    def test_ten_consecutive_failures_then_it_gives_up(
            self, instant_backoff, caplog):
        calls, events = [], []
        client = self._failing_client(calls, events)
        with caplog.at_level(logging.ERROR, logger=WS.logger.name):
            asyncio.run(asyncio.wait_for(client._reconnect(), 10))

        assert WS.MAX_RECONNECT_ATTEMPTS == 10, (
            "the cap itself is unchanged by this work")
        assert len(calls) == 10, "ten attempts, then the ladder ends"
        assert client.reconnect_attempt == 11, (
            "the counter records the attempt that was refused by the cap")
        assert [e for e, _ in events] == ["RECONNECT_ATTEMPT"] * 10
        assert any("Max reconnect attempts" in r.message for r in caplog.records)

    def test_giving_up_returns_rather_than_raising(self, instant_backoff):
        """The composition layer converts the return into the process exit.

        `run()` returning is what the entry point treats as fatal. This module
        deliberately contains no exit of its own.
        """
        calls, events = [], []
        client = self._failing_client(calls, events)
        result = asyncio.run(asyncio.wait_for(client._reconnect(), 10))
        assert result is None
        assert "SystemExit" not in CLIENT_SRC and "sys.exit" not in CLIENT_SRC

    def test_the_cap_counts_CONSECUTIVE_failures_only(
            self, instant_backoff, monkeypatch):
        """A successful connect resets the ladder, as it always did."""
        client = lifecycle_client()
        patch_connect(monkeypatch, [])
        client.reconnect_attempt = 7

        async def scenario():
            await client.connect()
            await client.disconnect()

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert client.reconnect_attempt == 0


# ---------------------------------------------------------------------------
# 04 + scope D. Transport liveness is the LIBRARY's job, stated explicitly.
# ---------------------------------------------------------------------------
class TestTransportKeepaliveIsExplicit:

    def _kwargs(self, monkeypatch):
        client = lifecycle_client()
        sockets = patch_connect(monkeypatch, [])

        async def scenario():
            await client.connect()
            await client.disconnect()

        asyncio.run(asyncio.wait_for(scenario(), 5))
        assert len(sockets) == 1
        return sockets[0].connect_kwargs

    def test_connect_pins_the_keepalive_settings_rather_than_inheriting_them(
            self, monkeypatch):
        kwargs = self._kwargs(monkeypatch)
        assert kwargs["ping_interval"] == WS.WS_PING_INTERVAL_SECONDS
        assert kwargs["ping_timeout"] == WS.WS_PING_TIMEOUT_SECONDS
        assert kwargs["close_timeout"] == WS.WS_CLOSE_TIMEOUT_SECONDS

    def test_the_pinned_values_are_the_ones_that_were_justified(self):
        """Pinned to what websockets 17.0.1 does today, in writing.

        The point of pinning is that a library upgrade cannot silently redefine
        the transport-liveness contract this feed depends on. Changing these
        three numbers is a deliberate act with a visible diff.
        """
        assert WS.WS_PING_INTERVAL_SECONDS == 20
        assert WS.WS_PING_TIMEOUT_SECONDS == 20
        assert WS.WS_CLOSE_TIMEOUT_SECONDS == 10

    def test_transport_liveness_and_frame_freshness_are_separate_numbers(self):
        """Two questions, two thresholds, no arithmetic relationship.

        Ping/pong answers "is the socket alive". `FRAME_STALE_TIMEOUT_SECONDS`
        answers "is market data still arriving". A stale channel on a live
        socket is precisely the failure the watchdog exists for, so the second
        threshold is necessarily far looser than the first.
        """
        assert WS.FRAME_STALE_TIMEOUT_SECONDS > WS.WS_PING_TIMEOUT_SECONDS
        assert WS.FRAME_FRESHNESS_POLL_SECONDS < WS.FRAME_STALE_TIMEOUT_SECONDS
        assert WS.FRAME_STALE_TIMEOUT_SECONDS == WS.CLOSED_CANDLE_THRESHOLD_SECONDS


# ---------------------------------------------------------------------------
# 10. The default remains inert. Hardening a feed must not arm one.
# ---------------------------------------------------------------------------
ENTRY_SRC = (Path(WS.__file__).parents[2] / "quantedge" / "__main__.py"
             ).read_text(encoding="utf-8")


class TestTheDefaultsAreUnchanged:

    def test_live_feed_enabled_still_defaults_to_false(self):
        field = Settings.model_fields["live_feed_enabled"]
        assert field.default is False, (
            "the gate is opt-in; nothing in this work may arm it")

    def test_the_disabled_branch_still_opens_no_socket(self):
        # Matched against SOURCE text, so the needle must fit on one line.
        assert "LIVE_FEED_ENABLED is false: no market-data socket will be " in ENTRY_SRC
        assert "opened; set it to true to bind the closed-candle feed" in ENTRY_SRC
        assert "if settings.live_feed_enabled:" in ENTRY_SRC

    def test_constructing_the_client_opens_nothing_and_starts_no_task(self):
        """A client is inert until `connect()` is awaited.

        The watchdog is started by `connect()`, so importing the module or
        building a client cannot produce a live task, a socket, or a stamp.
        """
        client = ws_client()
        assert client.ws is None
        assert client.running is False
        assert client._watchdog_task is None
        assert client._last_frame_ts is None
        assert client._reconnecting is False

        async def probe():
            return len(watchdog_tasks())

        assert asyncio.run(asyncio.wait_for(probe(), 5)) == 0

    def test_the_thresholds_are_overridable_but_default_to_the_module_values(self):
        """A deployment with MEASURED cadence can tighten this on evidence.

        The default is the only value justified by the repository today, so the
        constructor default is that value and nothing in production overrides it.
        """
        client = ws_client()
        assert client._frame_stale_after == WS.FRAME_STALE_TIMEOUT_SECONDS
        assert client._freshness_poll == WS.FRAME_FRESHNESS_POLL_SECONDS
        tight = ws_client(frame_stale_after=90, freshness_poll_seconds=5)
        assert tight._frame_stale_after == 90
        assert tight._freshness_poll == 5
        assert "frame_stale_after" not in ENTRY_SRC, (
            "production takes the justified default, not an injected guess")


# ---------------------------------------------------------------------------
# 11. The closed-candle STRUCTURE path is untouched by the freshness stamp.
# ---------------------------------------------------------------------------
class TestTheClosedCandlePathIsUnchanged:

    def test_a_closed_candle_still_reaches_the_structure_clock_exactly_once(self):
        seen = []
        client = ws_client(on_candle_closed=lambda c: seen.append(c))
        frame = closed_frame()
        asyncio.run(client._handle_message(frame))

        assert len(seen) == 1, "one closed candle, one structure callback"
        candle = seen[0]
        assert candle["is_closed"] is True
        assert candle["exchange_symbol"] == SYMBOL
        assert candle["timestamp"] == frame["candle_start_time"] // US
        assert candle["timestamp"] in client._processed[SYMBOL]
        assert client._last_closed[SYMBOL] == candle["timestamp"]

    def test_the_same_closed_candle_is_still_deduplicated(self):
        seen = []
        client = ws_client(on_candle_closed=lambda c: seen.append(c))
        frame = closed_frame()
        asyncio.run(client._handle_message(frame))
        asyncio.run(client._handle_message(frame))
        assert len(seen) == 1, "dedup by timestamp, unchanged"

    def test_a_forming_frame_still_reaches_neither_clock_when_unbound(self):
        """Production binds only `on_candle_closed`; `on_market_update` is None.

        The forming frame is still excluded from structure and is still not
        persisted. The ONLY thing it now does is prove the feed is alive.
        """
        closed_calls, update_calls = [], []
        client = ws_client(on_candle_closed=lambda c: closed_calls.append(c))
        assert client.on_market_update is None
        asyncio.run(client._handle_message(forming_frame()))

        assert closed_calls == [], "a forming frame is not structure"
        assert update_calls == []
        assert client._processed[SYMBOL] == set()
        assert client._last_closed[SYMBOL] is None, (
            "the per-pair closed marker is still untouched by a forming frame")
        assert client._last_frame_ts is not None, "but it IS market data"

    def test_the_stamp_happens_before_the_closed_candle_can_be_rejected(self):
        """Freshness means "the channel is delivering", not "we liked it".

        A wrong-year or malformed closed candle is still rejected by the
        existing validation, and the feed is still demonstrably alive.
        """
        client = ws_client()
        asyncio.run(client._handle_message(
            closed_frame(high=1.0, low=99999.0)))
        assert client._last_frame_ts is not None
        assert client._processed[SYMBOL] == set(), "validation still rejects it"


# ---------------------------------------------------------------------------
# 12. Nothing in this module can place an order.
# ---------------------------------------------------------------------------
FORBIDDEN_CALLS = {
    "submit", "submit_order", "place_order", "create_order", "cancel_order",
    "close_position", "set_leverage", "post", "put", "delete",
}


class TestNothingHereTrades:

    def test_no_order_submitting_call_exists_anywhere_in_the_module(self):
        tree = ast.parse(CLIENT_SRC)
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (target.attr if isinstance(target, ast.Attribute)
                    else target.id if isinstance(target, ast.Name) else None)
            if name in FORBIDDEN_CALLS:
                found.add(name)
        assert found == set(), f"execution surface in a market-data client: {found}"

    def test_the_module_imports_nothing_from_the_execution_package(self):
        tree = ast.parse(CLIENT_SRC)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        offenders = {m for m in modules
                     if "execution" in m or "order" in m or "broker" in m}
        assert offenders == set(), f"unexpected imports: {sorted(offenders)}"

    def test_the_watchdog_work_added_no_credential_or_private_surface(self):
        """Public market data only. The watchdog closes a socket; that is all."""
        for needle in ("api_key", "api_secret", "DELTA_API", "signature",
                       "hmac", "Authorization", "/live", "enable_heartbeat"):
            assert needle not in CLIENT_SRC, f"unexpected surface: {needle}"
        assert "socket.india.delta.exchange" in CLIENT_SRC
        assert "binance" not in CLIENT_SRC.lower()

    def test_the_only_thing_the_stale_path_does_is_close(self):
        """The mandated shape of the fix, asserted as a whole.

        `_watchdog_loop` may log, may sleep, may read the clock and may close
        the socket. It may not recover, and it may not trade.
        """
        names = calls_in(WS.DeltaWebSocketClient._watchdog_loop)
        assert "close" in names
        assert names & FORBIDDEN_CALLS == set()
        assert not names & {"_reconnect", "connect", "subscribe", "disconnect",
                            "_backfill_gaps", "run", "listen", "_handle_message"}


# ---------------------------------------------------------------------------
# 02 + 04 + 06, end to end. Stale -> close -> recover -> ARMED AGAIN.
# ---------------------------------------------------------------------------
class TestTheFullRecoveryCycle:
    """The watchdog is one-shot by design: it fires once and returns.

    That is only safe because `connect()` starts a fresh one, so recovery must
    re-arm it. These tests drive the real `connect/subscribe/listen/_reconnect`
    chain against local doubles and assert the cycle closes on itself.
    """

    def _run_cycle(self, monkeypatch, feeding: int = 2):
        sockets, events, backfills, started = [], [], [], []
        frames = [[forming_frame(second_offset=i + 1)] for i in range(feeding)]

        async def fake_connect(*args, **kwargs):
            if not frames:
                raise ConnectionRefusedError("no more sockets in this scenario")
            sock = FeedingSocket(frames.pop(0))
            sock.connect_kwargs = dict(kwargs)
            sockets.append(sock)
            return sock

        monkeypatch.setattr(WS.websockets, "connect", fake_connect)
        client = watchdog_client()
        real_start = client._start_watchdog

        async def spy_start():
            await real_start()
            started.append(client._watchdog_task)
            assert len(watchdog_tasks()) == 1, "never two watchdogs at once"

        async def spy_backfill():
            backfills.append(True)

        client._start_watchdog = spy_start
        client._backfill_gaps = spy_backfill
        client._log_event = lambda event, **kw: events.append((event, kw))

        async def scenario():
            await client.connect()
            await client.subscribe()
            await client.listen()

        asyncio.run(asyncio.wait_for(scenario(), 30))
        return client, sockets, events, backfills, started

    def test_a_stale_socket_is_closed_recovered_and_watched_again(
            self, instant_backoff, monkeypatch):
        client, sockets, events, backfills, started = self._run_cycle(monkeypatch)
        names = [e for e, _ in events]

        assert len(sockets) == 2, "the first socket went stale, the second replaced it"
        assert [s.close_calls for s in sockets] == [1, 1], (
            "each stale socket was closed exactly once")
        assert names.count(WS.EVENT_FEED_STALE) == 2, (
            "the SECOND stale verdict is the proof the watchdog was re-armed: a "
            "one-shot watchdog that recovery forgot to restart could only fire once")
        assert len(started) == 2, "one watchdog per connect, no more"
        assert started[0] is not started[1]
        assert all(t.done() for t in started), "and none of them outlived its socket"

    def test_recovery_resubscribes_and_backfills_on_the_new_socket(
            self, instant_backoff, monkeypatch):
        client, sockets, events, backfills, started = self._run_cycle(monkeypatch)
        for sock in sockets:
            assert len(sock.sent) == 1, "every socket was subscribed exactly once"
            payload = json.loads(sock.sent[0])
            assert payload["type"] == "subscribe"
            assert (payload["payload"]["channels"][0]["name"]
                    == WS.SUBSCRIPTION_CHANNEL)
        assert len(backfills) >= 2, (
            "REST gap backfill still runs before every attempt, so the hour the "
            "stale feed missed is repaired rather than skipped")
        names = [e for e, _ in events]
        assert names.count("RECONNECT_ATTEMPT") == len(backfills)

    def test_the_cycle_still_ends_at_the_consecutive_failure_cap(
            self, instant_backoff, monkeypatch):
        """A feed that cannot be re-established still reaches the fatal return."""
        client, sockets, events, backfills, started = self._run_cycle(monkeypatch)
        assert client.reconnect_attempt == WS.MAX_RECONNECT_ATTEMPTS + 1
        assert client._reconnecting is False
        assert client.running is True, (
            "this module never clears `running`; the entry point owns the exit")

    def test_frames_on_the_replacement_socket_stamp_freshness_again(
            self, instant_backoff, monkeypatch):
        client, sockets, events, backfills, started = self._run_cycle(monkeypatch)
        names = [e for e, _ in events]
        assert names.count(WS.EVENT_CANDLE_FORMING) == 2, (
            "both sockets delivered a frame, and both frames were parsed")
        assert client._last_frame_ts is not None

    def test_an_exception_escaping_listen_starts_ONE_ladder_not_two(
            self, instant_backoff, monkeypatch):
        """The path where the old shape could open two sockets at once.

        `listen()`'s `finally` runs BEFORE its exception propagates. The old
        `except` then called `_reconnect` again, so a raising read loop ran a
        full nested ladder and then a second one on top of it. The guard makes
        that unreachable: the nested call returns immediately and the loop takes
        exactly one next attempt, so the ladder stays bounded by the cap.

        The scenario is bounded on purpose. `connect()` resets
        `reconnect_attempt` to 0 on every success, so a flap that reconnects is
        always attempt 1 again and only the refusals that follow can climb to
        the cap. A read loop that raises against an endlessly available socket
        therefore flaps forever - in production that is the supervisor's problem
        (unknown #3, reported), and in a test it is a hang, not a stronger
        proof. So the socket is available for exactly `FLAPS` opens and refuses
        after that: three flaps through the raising read loop, then ten
        consecutive refusals, then the fatal return.
        """
        FLAPS = 3
        client = lifecycle_client()
        connects, depths, sockets = [], [], []

        async def fake_connect(*args, **kwargs):
            # Counted here rather than around `client.connect`, so the number is
            # literally "how many sockets were opened" - which is the quantity
            # the two-ladder bug inflated.
            connects.append(True)
            if len(connects) > FLAPS:
                raise ConnectionRefusedError("no route to the exchange")
            sock = FakeSocket()
            sockets.append(sock)
            return sock

        monkeypatch.setattr(WS.websockets, "connect", fake_connect)

        async def raising_listen():
            depths.append(reconnect_depth())
            try:
                raise RuntimeError("socket iterator blew up")
            finally:
                if client.running:
                    await client._reconnect()

        async def no_backfill():
            return None

        client.listen = raising_listen
        client._backfill_gaps = no_backfill

        async def scenario():
            try:
                await asyncio.wait_for(client._reconnect(), 30)
            finally:
                # Reaped INSIDE the loop that owns the task. Cancelling it after
                # `asyncio.run` returns would await a task on a closed loop.
                await client._cancel_watchdog()

        asyncio.run(scenario())

        assert len(depths) == FLAPS, (
            f"the read loop ran once per flap and no more; saw {depths}")
        assert set(depths) == {1}, (
            f"and the ladder never went past one frame deep; saw {depths}")
        assert len(connects) == FLAPS + WS.MAX_RECONNECT_ATTEMPTS, (
            "one bounded ladder: three flaps, then ten consecutive refusals - "
            "not a nested ladder plus a second one on top of it")
        assert len(sockets) == FLAPS, (
            "one socket per successful open, never a second alongside a live one")
        assert client.reconnect_attempt == WS.MAX_RECONNECT_ATTEMPTS + 1, (
            "and the cap is still what ends it")
        assert client._reconnecting is False
