"""PHASE 1 -- safe live market-data binding, execution still disabled.

These tests pin the composition layer only (`quantedge.__main__`), the
process-level gate (`quantedge.config.Settings.live_feed_enabled`) and the
container packaging that gate depends on. Nothing here re-tests LuxAlgo, the
lifecycle, sizing, or the websocket client's own reconnect/heartbeat internals.

What is asserted, in the task's own order:

  A  the default (`live_feed_enabled=False`) startup stays inert -- no feed
     client is constructed, no socket is opened, the existing entry-point
     safety pins still hold;
  B  the enabled startup binds the closed-candle STRUCTURE clock and nothing
     else -- `on_market_update` stays unbound, no orchestrator is built, no
     credential is read;
  C  an unexpected return from `client.run()` is fatal -- CRITICAL log, non-zero
     exit, no silent fall-back to the idle loop and no reconnect loop here;
  D  the image carries the pinned instrument snapshot where the existing
     `_find_snapshot()` parent walk expects it, and the engine service has the
     restart policy the fatal exit depends on. Static: no network, no Docker.

No test here opens a socket, reads `.env`, or touches a credential.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from quantedge import __main__ as ENTRY
from quantedge.config import Settings
from quantedge.market_data import delta_websocket as WS
from quantedge.runtime import restart_recovery as RECOVERY
from quantedge.runtime.manual_smc_runtime import build_manual_smc_runtime

#: The real class, captured before any fixture patches the module attribute.
REAL_FEED_CLIENT = WS.DeltaWebSocketClient

ENGINE_ROOT = Path(__file__).resolve().parent.parent
ENGINE_SRC = ENGINE_ROOT / "src"
REPO_ROOT = ENGINE_ROOT.parent
ENTRY_PATH = ENGINE_SRC / "quantedge" / "__main__.py"
ENTRY_SRC = ENTRY_PATH.read_text(encoding="utf-8")
ENTRY_AST = ast.parse(ENTRY_SRC)
REGISTRY_SRC = (ENGINE_SRC / "quantedge" / "instruments"
                / "registry.py").read_text(encoding="utf-8")
COMPOSE_SRC = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE_SRC = (REPO_ROOT / "docker"
                  / "Dockerfile.engine").read_text(encoding="utf-8")
SNAPSHOT_REL = "data/reference/delta_exchange_india/product_specs_snapshot.json"


# ------------------------------------------------------------------- helpers
def _fn(name: str) -> ast.AST:
    """The top-level (async) def called `name` in `__main__.py`."""
    for node in ENTRY_AST.body:
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            return node
    raise AssertionError(f"{name}() is missing from {ENTRY_PATH.name}")


def _calls_to(tree: ast.AST, name: str) -> list:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


def _compose_services(text: str) -> dict:
    """Split docker-compose.yml into {service: block} without needing PyYAML."""
    lines = text.splitlines()
    try:
        start = lines.index("services:") + 1
    except ValueError:  # pragma: no cover - the file always has it
        raise AssertionError("docker-compose.yml has no `services:` mapping")
    services, current = {}, None
    for line in lines[start:]:
        if line and not line.startswith(" "):        # left the services block
            break
        header = re.fullmatch(r"  ([A-Za-z0-9_.-]+):", line)
        if header:
            current = header.group(1)
            services[current] = []
        elif current is not None:
            services[current].append(line)
    return {name: "\n".join(body) for name, body in services.items()}


COMPOSE_SERVICES = _compose_services(COMPOSE_SRC)


def _delta_candle_frame(symbol: str, *, hours_ago: int = 3) -> dict:
    """A `candlestick_1h` frame in the format `_parse_candle_from_ws` documents.

    The OHLCV values are the ones its docstring records from the live feed on
    2026-08-21; only the timestamps are moved to a recently closed hour so the
    wall-clock closure gate can pass. Nothing here invents a feed shape.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    hour_start = now - (now % 3600) - hours_ago * 3600
    last_tick = hour_start + 3599
    return {
        "type": WS.SUBSCRIPTION_CHANNEL,
        "symbol": symbol,
        "resolution": "1h",
        "open": 77809.5,
        "high": 77865.5,
        "low": 77338.5,
        "close": 77428.5,
        "volume": 372963.0,
        "candle_start_time": hour_start * 1_000_000,
        "timestamp": last_tick * 1_000_000,
        "last_updated": last_tick * 1_000_000,
        "sUID": f"{symbol}_#_{symbol}_#_60",
    }


#: How far behind the newest closed hour the Phase-2 bootstrap is made to stop
#: in these tests. It is exactly `_delta_candle_frame`'s default `hours_ago`
#: minus one, so the frame that test 7 feeds is the IMMEDIATE successor of the
#: bootstrapped watermark - which is the only closed candle a correctly
#: bootstrapped engine will accept next. Without this the bootstrap would leave
#: the watermark at the newest closed hour, and every candle a test can
#: synthesise would be a duplicate the strategy rightly refuses.
BOOT_LAG_HOURS = 3

#: Bars written per symbol for the synthetic genesis history. Small on purpose:
#: genesis policy is "replay the declared history from its first row", and these
#: tests pin the composition layer, not LuxAlgo's warmup behaviour.
GENESIS_BARS = 48


def _write_genesis_history(base: Path, symbols, *, newest_bar_idx: int) -> None:
    """Write one canonical `1h/full_history.csv` per symbol under `base`.

    Same layout and same column set as the repository's real canonical files, so
    the production reader (`backtest.load_canonical_candles`) is the one used.
    """
    for offset, symbol in enumerate(symbols):
        tf_dir = base / symbol / "1h"
        tf_dir.mkdir(parents=True, exist_ok=True)
        rows = ["timestamp,open,high,low,close,volume"]
        for i in range(GENESIS_BARS):
            bar_idx = newest_bar_idx - GENESIS_BARS + 1 + i
            ts = datetime.fromtimestamp(bar_idx * 3600, tz=timezone.utc)
            base_price = 100.0 + offset * 10.0 + (i % 7)
            rows.append(
                f"{ts.isoformat(sep=' ')},{base_price:.1f},"
                f"{base_price + 1.5:.1f},{base_price - 1.5:.1f},"
                f"{base_price + 0.5:.1f},1000.0")
        (tf_dir / "full_history.csv").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")


def _no_transport(*args, **kwargs):
    """Installed over the REST range fetch: no test may reach the network."""
    raise AssertionError(
        "the historical transport must not be called by these tests "
        f"(args={args!r})")


# ------------------------------------------------------------------- doubles
class _IdleLoopReached(Exception):
    """Raised instead of the 60s heartbeat sleep, so the idle path terminates."""


class _RecordingFeedClient:
    """Stand-in for `DeltaWebSocketClient` that opens no socket.

    `run()` returns normally and leaves `running` set, which is exactly what the
    real client does once `MAX_RECONNECT_ATTEMPTS` is exhausted -- the case the
    composition layer has to treat as fatal.
    """

    constructed: list = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = dict(kwargs)
        self.running = False
        self.reconnect_attempt = 0
        self.run_calls = 0
        type(self).constructed.append(self)

    async def run(self) -> None:
        self.run_calls += 1
        self.running = True
        self.reconnect_attempt = WS.MAX_RECONNECT_ATTEMPTS + 1


class _StubHealthServer:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def feed_clients(monkeypatch):
    """Every `DeltaWebSocketClient` the entry point constructs, in order."""
    _RecordingFeedClient.constructed = []
    monkeypatch.setattr(WS, "DeltaWebSocketClient", _RecordingFeedClient)
    return _RecordingFeedClient.constructed


@pytest.fixture
def health(monkeypatch):
    """Replace the real health server so no test binds port 8000."""
    stub = _StubHealthServer()
    monkeypatch.setattr(ENTRY, "start_health_server", lambda: stub)
    return stub


@pytest.fixture
def runtimes(monkeypatch):
    """Records `(args, kwargs, runtime)` for each runtime the entry point builds."""
    made = []
    real = ENTRY.build_manual_smc_runtime

    def _spy(*args, **kwargs):
        runtime = real(*args, **kwargs)
        made.append((args, kwargs, runtime))
        return runtime

    monkeypatch.setattr(ENTRY, "build_manual_smc_runtime", _spy)
    return made


@pytest.fixture
def feed_callbacks(monkeypatch):
    """Records how `make_feed_callback` was called, `submit_to` included."""
    calls = []
    real = ENTRY.make_feed_callback

    def _spy(runtime, submit_to=None):
        calls.append({"runtime": runtime, "submit_to": submit_to})
        return real(runtime, submit_to=submit_to)

    monkeypatch.setattr(ENTRY, "make_feed_callback", _spy)
    return calls


@pytest.fixture
def idle_sleeps(monkeypatch):
    """Counts heartbeat sleeps and breaks the idle loop on the first one."""
    real_sleep = asyncio.sleep
    seen = []

    async def _sleep(delay, *args, **kwargs):
        if delay == 60:
            seen.append(delay)
            raise _IdleLoopReached
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return seen


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """Set `settings.live_feed_enabled` for the duration of one test.

    Enabling ALSO satisfies the Phase-2 structure-state layer, because the entry
    point now refuses to subscribe before the STRUCTURE clock has been restored
    and brought to the current closed-candle boundary. That is setup only - no
    assertion below is relaxed:

    * a temporary durable state directory (empty, so the genesis path runs);
    * a synthetic canonical history in the repository's own layout, read by the
      production reader;
    * a boot clock stopped `BOOT_LAG_HOURS` behind the newest closed hour, so
      nothing has to be bridged and the candle a test feeds afterwards is the
      immediate successor rather than a duplicate;
    * the REST range fetch replaced by a raiser, proving no test reaches Delta.
    """
    def _set(value: bool) -> None:
        monkeypatch.setattr(ENTRY.settings, "live_feed_enabled", value)
        RECOVERY.STRUCTURE_MONITOR.reset()
        if not value:
            return
        boot_newest = RECOVERY.newest_closed_bar_idx() - BOOT_LAG_HOURS
        genesis = tmp_path / "genesis"
        _write_genesis_history(genesis, build_manual_smc_runtime().symbols,
                               newest_bar_idx=boot_newest)
        monkeypatch.setattr(ENTRY.settings, "engine_state_dir",
                            str(tmp_path / "state"))
        monkeypatch.setattr(ENTRY.settings, "engine_genesis_history_dir",
                            str(genesis))
        monkeypatch.setattr(RECOVERY, "newest_closed_bar_idx",
                            lambda now_ts=None: boot_newest)
        monkeypatch.setattr(RECOVERY, "fetch_closed_candle_range", _no_transport)
    return _set


# ============================================================== A. default off
class TestTheGateItself:

    def test_1_it_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("LIVE_FEED_ENABLED", raising=False)
        assert Settings(_env_file=None).live_feed_enabled is False
        assert Settings.model_fields["live_feed_enabled"].default is False

    def test_2_an_absent_env_var_leaves_it_false(self, monkeypatch):
        monkeypatch.delenv("LIVE_FEED_ENABLED", raising=False)
        assert os.environ.get("LIVE_FEED_ENABLED") is None
        assert Settings(_env_file=None).live_feed_enabled is False

    @pytest.mark.parametrize("raw", ["banana", "", "maybe", "2", "on-ish"])
    def test_3_a_malformed_value_fails_closed(self, monkeypatch, raw):
        """A value pydantic cannot coerce must raise, never fall back to a bool.

        The raise happens while `quantedge.config` is imported -- before the
        health server, the runtime or any socket exists.
        """
        monkeypatch.setenv("LIVE_FEED_ENABLED", raw)
        with pytest.raises(ValidationError) as exc:
            Settings(_env_file=None)
        assert "live_feed_enabled" in str(exc.value)

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("True", True), ("1", True),
        ("false", False), ("False", False), ("0", False),
    ])
    def test_4_the_operable_surface_is_an_explicit_boolean(self, monkeypatch,
                                                          raw, expected):
        monkeypatch.setenv("LIVE_FEED_ENABLED", raw)
        assert Settings(_env_file=None).live_feed_enabled is expected

    def test_5_it_is_not_an_execution_switch(self):
        """The gate must be its own flag, not a reuse of a governance flag."""
        names = set(Settings.model_fields)
        assert "live_feed_enabled" in names
        for governance in ("algo_enabled", "kill_switch_active",
                           "execution_enabled", "live_trading_enabled",
                           "orders_enabled"):
            assert governance not in names, (
                f"{governance} must stay where it is; Phase 1 adds no "
                f"execution-enable flag to process config")


class TestDefaultStartupStaysInert:

    def test_1_no_feed_client_is_constructed(self, gate, health, feed_clients,
                                             runtimes, idle_sleeps):
        gate(False)
        with pytest.raises(_IdleLoopReached):
            asyncio.run(ENTRY.main())
        assert feed_clients == [], (
            "with the gate off the entry point must not construct a feed "
            "client at all")
        assert idle_sleeps == [60], "the pre-existing idle loop must still run"
        assert len(runtimes) == 1, "the runtime is still registered"
        assert runtimes[0][2].orchestrator is None
        assert health.shutdown_calls == 1

    def test_2_the_feed_module_is_imported_lazily(self):
        """A top-level import would hijack this module's `basicConfig`.

        `quantedge.market_data.delta_websocket` calls `logging.basicConfig()` at
        import time and `basicConfig` is first-caller-wins, so importing it at
        module scope here would silently discard LOG_LEVEL and the format set at
        the top of `__main__.py`. Keeping the import inside the enabled branch
        also means the disabled path never imports the feed at all.
        """
        top_level = [n.module for n in ENTRY_AST.body
                     if isinstance(n, ast.ImportFrom)]
        assert not any("delta_websocket" in (m or "") for m in top_level), (
            "the feed client must not be imported at module scope")
        inner = [n for n in ast.walk(_fn("run_live_market_data"))
                 if isinstance(n, ast.ImportFrom)]
        assert [n.module for n in inner] == [
            "quantedge.market_data.delta_websocket"]
        assert [a.name for a in inner[0].names] == ["DeltaWebSocketClient"]

    def test_3_the_feed_is_reachable_only_through_the_gate(self):
        main_fn = _fn("main")
        calls = _calls_to(main_fn, "run_live_market_data")
        assert len(calls) == 1, "exactly one call site, or the gate is bypassable"
        gates = [n for n in ast.walk(main_fn) if isinstance(n, ast.If)
                 and ast.unparse(n.test) == "settings.live_feed_enabled"]
        assert len(gates) == 1
        guarded = [c for stmt in gates[0].body for c in
                   _calls_to(stmt, "run_live_market_data")]
        unguarded = [c for stmt in gates[0].orelse for c in
                     _calls_to(stmt, "run_live_market_data")]
        assert len(guarded) == 1 and unguarded == []

    def test_4_the_existing_entry_point_pins_still_hold(self):
        for required in ("execution loops remain disabled until explicitly "
                         "configured", "start_health_server",
                         "build_manual_smc_runtime"):
            assert required in ENTRY_SRC
        for forbidden in ("MultiUserExecutionOrchestrator",
                          "DeltaReconciliationService"):
            assert forbidden not in ENTRY_SRC

    def test_5_importing_the_feed_module_really_does_configure_logging(
            self, tmp_path):
        """The premise behind test_2, proven in a clean interpreter.

        Run from `tmp_path` so pydantic-settings resolves `env_file=".env"`
        against an empty directory: no credential file is opened.
        """
        code = (
            "import logging, sys\n"
            "assert not logging.getLogger().handlers, 'unexpected handlers'\n"
            "import quantedge.market_data.delta_websocket\n"
            "sys.stdout.write(str(len(logging.getLogger().handlers)))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ENGINE_SRC)
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                              env=env, capture_output=True, text=True,
                              timeout=180)
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert proc.stdout.strip() == "1", (
            "importing the feed module installs a root handler, which is why "
            "the entry point imports it lazily")
        assert not (tmp_path / ".env").exists()


# ====================================================== B. enabled binds ONLY
#                                                        the structure clock
class TestEnabledModeBindsTheStructureClockOnly:

    @pytest.fixture(autouse=True)
    def _armed(self, gate, health, feed_clients, runtimes, feed_callbacks,
               idle_sleeps):
        """Run the entry point once with the gate on and expose what it built."""
        gate(True)
        with pytest.raises(SystemExit):
            asyncio.run(ENTRY.main())
        self.clients = feed_clients
        self.runtimes = runtimes
        self.callbacks = feed_callbacks
        self.idle_sleeps = idle_sleeps
        self.health = health
        # Genesis replay processes the declared history through the same closed-
        # candle path, so the counter is non-zero before the feed delivers
        # anything. Test 7 asserts the DELTA, which is what it always meant.
        self.processed_at_boot = runtimes[0][2].candles_processed

    def test_1_one_feed_client_is_constructed_for_the_runtime_pairs(self):
        assert len(self.clients) == 1
        client = self.clients[0]
        assert client.args == (), "every argument must be explicit"
        assert client.kwargs["symbols"] == list(self.runtimes[0][2].symbols)
        assert client.kwargs["symbols"] == list(
            build_manual_smc_runtime().symbols)
        assert client.run_calls == 1

    def test_2_only_the_closed_candle_callback_is_bound(self):
        client = self.clients[0]
        assert set(client.kwargs) == {"symbols", "on_candle_closed"}, (
            "the STRUCTURE clock is the only edge Phase 1 wires")
        assert callable(client.kwargs["on_candle_closed"])

    def test_3_the_execution_clock_is_left_unbound(self):
        client = self.clients[0]
        assert "on_market_update" not in client.kwargs
        params = inspect.signature(REAL_FEED_CLIENT.__init__).parameters
        assert params["on_market_update"].default is None, (
            "an unbound execution clock must keep meaning 'log and drop'")
        assert params["engine"].default is None

    def test_4_the_entry_point_never_names_the_execution_clock_in_code(self):
        """Comments may explain it; code must not reference it."""
        keywords = {k.arg for n in ast.walk(ENTRY_AST)
                    if isinstance(n, ast.Call) for k in n.keywords}
        assert "on_market_update" not in keywords
        assert "engine" not in keywords
        names = {n.id for n in ast.walk(ENTRY_AST) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(ENTRY_AST)
                 if isinstance(n, ast.Attribute)}
        imported = {a.name for n in ast.walk(ENTRY_AST)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        for forbidden in ("make_market_update_callback", "on_market_update",
                          "LiveCandleAggregator", "make_execution_clock_callback"):
            assert forbidden not in names | attrs | imported

    def test_5_no_execution_orchestrator_is_constructed(self):
        args, kwargs, runtime = self.runtimes[0]
        assert (args, kwargs) == ((), {}), (
            "the runtime must still be built with no orchestrator argument")
        assert runtime.orchestrator is None
        assert self.callbacks[0]["submit_to"] is None, (
            "submit_to=None is what keeps the callback from reaching the "
            "execution boundary")
        assert self.callbacks[0]["runtime"] is runtime
        # `.submit()` is never called from the composition layer at all.
        attrs = {n.attr for n in ast.walk(ENTRY_AST)
                 if isinstance(n, ast.Attribute)}
        assert "submit" not in attrs
        # and the runtime-side gate that would refuse it is still in place
        runtime_src = (ENGINE_SRC / "quantedge" / "runtime"
                       / "manual_smc_runtime.py").read_text(encoding="utf-8")
        assert "if self.orchestrator is None:" in runtime_src
        assert "NO_ORCHESTRATION_BOUND" in runtime_src

    def test_6_no_credential_is_read_on_the_enabled_path(self):
        attrs = {n.attr for n in ast.walk(ENTRY_AST)
                 if isinstance(n, ast.Attribute)}
        for secret in ("delta_api_key", "delta_api_secret", "backend_api_key"):
            assert secret not in attrs
            assert secret not in ENTRY_SRC
        for env_read in ("os.environ", "os.getenv", "load_dotenv"):
            assert env_read not in ENTRY_SRC
        literals = {n.value for n in ast.walk(ENTRY_AST)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert ".env" not in literals
        assert _calls_to(ENTRY_AST, "open") == [], (
            "the composition layer opens no file of its own")
        imported = {a.name for n in ast.walk(ENTRY_AST)
                    if isinstance(n, ast.Import) for a in n.names}
        assert "os" not in imported, (
            "the composition layer reads config through Settings only")
        client = self.clients[0]
        assert not any("key" in k or "secret" in k for k in client.kwargs)

    def test_7_a_real_closed_candle_reaches_the_strategy(self):
        """The binding must actually work, not merely construct.

        A frame in the live Delta India shape goes through the real parser and
        the real callback, so the payload contract between the feed and the
        runtime is proven without a socket.
        """
        client = self.clients[0]
        runtime = self.runtimes[0][2]
        frame = _delta_candle_frame(client.kwargs["symbols"][0])
        candle = WS._parse_candle_from_ws(frame, accepted={frame["symbol"]})
        assert candle is not None, "the parser refused a well-formed frame"
        candle["is_closed"] = WS._is_candle_closed(candle["timestamp"])
        assert candle["is_closed"] is True

        step = client.kwargs["on_candle_closed"](candle)

        assert step is not None, "the bound callback refused a closed candle"
        assert step.skipped is None
        assert step.symbol == frame["symbol"]
        assert runtime.candles_processed == self.processed_at_boot + 1
        assert runtime.candles_refused == 0
        assert step.submitted == (), "nothing may be submitted in Phase 1"
        assert step.scan_result is None


# ================================================ C. an unexpected stop is fatal
class TestAnUnexpectedFeedStopIsFatal:

    def test_1_a_returning_run_exits_non_zero(self, gate, health, feed_clients,
                                              idle_sleeps):
        gate(True)
        with pytest.raises(SystemExit) as exc:
            asyncio.run(ENTRY.main())
        assert exc.value.code == ENTRY.FEED_FAILURE_EXIT_CODE
        assert exc.value.code != 0
        assert ENTRY.FEED_FAILURE_EXIT_CODE == 1

    def test_2_it_does_not_fall_back_to_the_idle_loop(self, gate, health,
                                                     feed_clients, idle_sleeps):
        gate(True)
        with pytest.raises(SystemExit):
            asyncio.run(ENTRY.main())
        assert idle_sleeps == [], (
            "a dead feed must not leave a feed-less process idling while "
            "/health still answers UP")
        assert feed_clients[0].run_calls == 1, "and it must not be retried here"

    def test_3_it_logs_at_critical_first(self, gate, health, feed_clients,
                                        idle_sleeps, caplog):
        gate(True)
        with caplog.at_level(logging.DEBUG, logger=ENTRY.logger.name):
            with pytest.raises(SystemExit):
                asyncio.run(ENTRY.main())
        critical = [r for r in caplog.records
                    if r.levelno == logging.CRITICAL
                    and r.name == ENTRY.logger.name]
        assert len(critical) == 1
        message = critical[0].getMessage()
        assert "run() returned" in message
        assert str(ENTRY.FEED_FAILURE_EXIT_CODE) in message

    def test_4_the_health_server_is_still_shut_down(self, gate, health,
                                                    feed_clients, idle_sleeps):
        gate(True)
        with pytest.raises(SystemExit):
            asyncio.run(ENTRY.main())
        assert health.shutdown_calls == 1

    def test_5_there_is_no_reconnect_loop_at_the_composition_layer(self):
        fn = _fn("run_live_market_data")
        loops = [n for n in ast.walk(fn)
                 if isinstance(n, (ast.While, ast.For, ast.AsyncFor))]
        assert loops == [], "reconnect belongs to the client, not to startup"
        awaits = [ast.unparse(n.value) for n in ast.walk(fn)
                  if isinstance(n, ast.Await)]
        assert awaits == ["client.run()"]
        raises = [ast.unparse(n) for n in ast.walk(fn)
                  if isinstance(n, ast.Raise)]
        assert raises == ["raise SystemExit(FEED_FAILURE_EXIT_CODE)"]

    def test_6_the_client_internals_were_not_touched_for_this(self):
        """§5: the fatal path is composed above the client, not inside it."""
        assert WS.MAX_RECONNECT_ATTEMPTS == 10
        ws_src = (ENGINE_SRC / "quantedge" / "market_data"
                  / "delta_websocket.py").read_text(encoding="utf-8")
        assert "SystemExit" not in ws_src
        assert "sys.exit" not in ws_src


# =============================================== D. container packaging (static)
class TestTheImageCarriesThePinnedSnapshot:

    def test_1_the_snapshot_is_checked_in(self):
        assert (REPO_ROOT / SNAPSHOT_REL).is_file(), (
            f"{SNAPSHOT_REL} must be in the build context")

    def test_2_the_dockerfile_copies_it_from_the_build_context(self):
        copies = [line.strip() for line in DOCKERFILE_SRC.splitlines()
                  if line.strip().startswith("COPY ")
                  and "product_specs_snapshot.json" in line]
        assert len(copies) == 1, f"expected exactly one COPY, got {copies}"
        line = copies[0]
        assert SNAPSHOT_REL in line
        assert "--from=builder" not in line, (
            "the snapshot must come from the checked-in build context, not "
            "from a stage that could have regenerated it")
        assert line.endswith("./data/reference/delta_exchange_india/")

    def test_3_it_lands_where_the_existing_parent_walk_looks(self):
        """`/app` is a parent of `/app/src/quantedge/instruments/registry.py`."""
        assert re.search(r"^WORKDIR /app$", DOCKERFILE_SRC, re.M)
        assert "COPY --from=builder /app/src ./src" in DOCKERFILE_SRC
        registry_at = PurePosixPath("/app/src/quantedge/instruments/registry.py")
        landed = PurePosixPath("/app") / SNAPSHOT_REL
        assert any(parent / SNAPSHOT_REL == landed
                   for parent in registry_at.parents), (
            "the copied path must be on registry.py's own parent walk")

    def test_4_registry_py_was_not_changed_to_work_around_it(self):
        from quantedge.instruments.registry import SNAPSHOT_RELPATH
        assert SNAPSHOT_RELPATH.as_posix() == SNAPSHOT_REL, (
            "the Dockerfile targets the path registry.py actually looks for")
        assert "raise SnapshotUnavailableError" in REGISTRY_SRC
        for workaround in ("os.environ", "getenv", "importlib.resources",
                           "urlopen", "requests.get"):
            assert workaround not in REGISTRY_SRC, (
                f"{workaround} would be a way around the missing snapshot; "
                f"Phase 1 fixes the image instead")

    def test_5_the_copy_precedes_the_ownership_fix(self):
        copy_at = DOCKERFILE_SRC.index("product_specs_snapshot.json")
        chown_at = DOCKERFILE_SRC.index("chown -R appuser:appgroup /app")
        assert copy_at < chown_at, (
            "the snapshot must be owned by the runtime user, so it has to be "
            "copied before the existing chown")

    def test_6_nothing_excludes_the_snapshot_from_the_build_context(self):
        """A `.dockerignore` hit would make the new COPY fail at build time."""
        dockerignore = REPO_ROOT / ".dockerignore"
        if not dockerignore.is_file():
            return
        from fnmatch import fnmatch
        candidates = [SNAPSHOT_REL, "data", "data/reference",
                      "data/reference/delta_exchange_india"]
        for raw in dockerignore.read_text(encoding="utf-8").splitlines():
            pattern = raw.strip()
            if not pattern or pattern.startswith("#") or pattern.startswith("!"):
                continue
            pattern = pattern.rstrip("/").lstrip("./")
            assert not any(fnmatch(c, pattern) for c in candidates), (
                f".dockerignore pattern {raw!r} would exclude {SNAPSHOT_REL} "
                f"from the build context and break the COPY")

    def test_7_the_snapshot_is_committed_not_generated(self):
        """A fresh clone must already contain it: the image builds from git."""
        import shutil
        git = shutil.which("git")
        if git is None:                                  # pragma: no cover
            pytest.skip("git is unavailable in this environment")
        proc = subprocess.run(
            [git, "ls-files", "--error-unmatch", SNAPSHOT_REL],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
        if "not a git repository" in proc.stderr:        # pragma: no cover
            pytest.skip("not running from a git checkout")
        assert proc.returncode == 0, (
            f"{SNAPSHOT_REL} is not tracked, so a clean build context would "
            f"not have it: {proc.stderr.strip()}")


class TestTheComposeSurface:

    def test_1_the_engine_restarts_unless_stopped(self):
        assert "restart: unless-stopped" in COMPOSE_SERVICES["engine"], (
            "the non-zero exit on a dead feed is only useful with a supervisor")

    def test_2_no_other_service_gained_a_restart_policy(self):
        for name, body in COMPOSE_SERVICES.items():
            if name == "engine":
                continue
            assert "restart:" not in body, (
                f"{name} must keep the restart policy it had")

    def test_3_the_gate_has_an_operable_surface_defaulting_to_false(self):
        assert ("LIVE_FEED_ENABLED: ${LIVE_FEED_ENABLED:-false}"
                in COMPOSE_SERVICES["engine"]), (
            "without a passthrough an operator setting the flag would be "
            "silently ignored")
        for name, body in COMPOSE_SERVICES.items():
            if name != "engine":
                assert "LIVE_FEED_ENABLED" not in body

    def test_4_the_engine_is_given_no_execution_credential(self):
        engine = COMPOSE_SERVICES["engine"]
        for forbidden in ("DELTA_API_KEY", "DELTA_API_SECRET",
                          "BACKEND_ACCOUNT_ID"):
            assert forbidden not in engine, (
                f"{forbidden} must not reach a data-only engine")

    def test_5_the_five_known_services_are_all_still_present(self):
        assert set(COMPOSE_SERVICES) == {
            "postgres", "backend", "engine", "user-app", "developer-app"}
