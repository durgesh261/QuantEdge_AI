"""
QuantEdge Engine - Main Entry Point
"""

import asyncio
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from quantedge.config import settings
from quantedge.runtime.manual_smc_runtime import (
    build_manual_smc_runtime,
    make_feed_callback,
)
from quantedge.runtime.restart_recovery import (
    INERT_BOOTSTRAP_REFUSALS,
    STRUCTURE_MONITOR,
    bootstrap_or_fail,
    make_durable_feed_callback,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

#: Exit status used when the live market-data feed stops for good.
#:
#: Non-zero on purpose. `DeltaWebSocketClient.run()` returns normally once its
#: reconnect budget is exhausted, without clearing `running`, closing the socket
#: or raising, so a composition layer that treated the return as ordinary would
#: sit in a feed-less process that still answers /health with "UP". Exiting
#: hands the decision to the supervisor: the engine service carries
#: `restart: unless-stopped`, so Docker brings the container back.
FEED_FAILURE_EXIT_CODE = 1


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal liveness endpoint for container orchestration and diagnostics."""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # The body used to be a hardcoded "UP". That could not distinguish a
        # working engine from one whose STRUCTURE clock had been wedged for
        # hours, or from one that refused to subscribe at all - the HTTP server
        # is alive in every one of those cases. It now asks the one component
        # that knows. Healthy keeps the historical bytes exactly, so the compose
        # healthcheck and anything else polling this endpoint are unaffected.
        status, body = STRUCTURE_MONITOR.health_payload()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("Health endpoint: " + format, *args)


def start_health_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", 8000), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="engine-health").start()
    logger.info("Engine health endpoint listening on port 8000")
    return server


def _feed_stop_hook(client) -> "object":
    """A callable that stops the feed client before a fatal wedge exits.

    `DeltaWebSocketClient.listen()` ends with `finally: if self.running: await
    self._reconnect()`. Without clearing `running` first, a `SystemExit` raised
    from inside the closed-candle callback would be deferred behind a reconnect,
    a REST backfill and a resubscribe - which is exactly the "keep going while
    blind" behaviour the fatal path exists to end. Clearing the flag is done
    from here, at the composition layer, so `delta_websocket.py` keeps
    containing no exit of its own.
    """
    def stop() -> None:
        client.running = False
    return stop


async def _remain_inert(refusal: Exception) -> None:
    """Stay up, refuse to subscribe, and keep saying so. No exit.

    Reached only for a CONFIGURATION refusal: no durable state directory, or no
    snapshot and no declared genesis origin. Exiting would crash-loop against a
    problem no restart can fix, and cold-starting would pretend a
    path-dependent detector's history were continuous. So the process idles with
    /health reporting DOWN and the reason, which is the state an operator can
    actually see and repair.
    """
    logger.critical(
        "LIVE_FEED_ENABLED=true but the structure state layer refused to arm: "
        "%s. The market-data feed will NOT be subscribed. Configure a durable "
        "ENGINE_STATE_DIR (and, for a first boot, ENGINE_GENESIS_HISTORY_DIR) "
        "and restart.", refusal)
    while True:
        await asyncio.sleep(60)
        logger.debug("Engine heartbeat (structure clock inert)")


async def run_live_market_data(runtime, state_path) -> None:
    """Bind the PUBLIC Delta India candlestick feed to the STRUCTURE clock.

    Reached only when `settings.live_feed_enabled` is true. It wires exactly one
    edge of the two-clock design:

    * STRUCTURE clock - closed 1h candles reach LuxAlgo structure/OB creation
      through `on_candle_closed`. That is what is bound here.
    * EXECUTION clock - forming frames would reach live OB monitoring through
      `on_market_update`. It is left UNBOUND on purpose, so the client keeps
      logging and dropping every forming frame exactly as it does today.

    No orchestrator is constructed, no credential is read and `runtime.submit()`
    is never called from here, so this path cannot place an order. The
    per-account `algo_enabled` / `kill_switch_active` governance flags are
    untouched and remain the only authority over trading.

    `state_path` is the durable snapshot already brought up to the current
    closed-candle boundary by `bootstrap_or_fail`. Reaching this function at all
    means the historical gap has been bridged and verified; the wrapper below
    keeps that state current from here on.

    Does not return: a normal return from `client.run()` is itself abnormal (see
    FEED_FAILURE_EXIT_CODE) and is converted into a non-zero process exit.
    """
    # Imported inside the function, not at module scope, deliberately.
    #
    # `quantedge.market_data.delta_websocket` calls `logging.basicConfig()` at
    # import time, and `basicConfig` is first-caller-wins. A top-level import
    # here would therefore run it BEFORE the configuration at the top of this
    # file, silently discarding LOG_LEVEL and the "%(name)s" format. Importing
    # after that configuration is in place makes the module's own call the
    # harmless no-op it is meant to be.
    #
    # It also keeps the disabled path exactly as it is today: with the flag off
    # the feed module is never imported at all.
    from quantedge.market_data.delta_websocket import DeltaWebSocketClient

    symbols = list(runtime.symbols)
    client = DeltaWebSocketClient(
        symbols=symbols,
        # STRUCTURE clock only. `submit_to=None` keeps the callback evaluating
        # closed candles without ever reaching the execution boundary, and
        # `on_market_update` is intentionally not passed at all.
        #
        # The durable wrapper adds exactly two things around that unchanged
        # callback: refuse to keep running blind when a closed candle is missing
        # (LuxAlgo cannot skip a bar), and persist the snapshot after each
        # accepted candle so a restart resumes instead of guessing.
        on_candle_closed=make_durable_feed_callback(
            runtime,
            make_feed_callback(runtime, submit_to=None),
            state_path=state_path,
            monitor=STRUCTURE_MONITOR,
        ),
    )
    STRUCTURE_MONITOR.register_stop_hook(_feed_stop_hook(client))
    logger.info(
        "LIVE_FEED_ENABLED=true: binding the public Delta India %s feed for %s "
        "to the closed-candle structure clock only (forming-frame execution "
        "clock unbound, no orchestrator, no credential read); durable state at "
        "%s", runtime.timeframe, symbols, state_path)

    await client.run()

    logger.critical(
        "Live market-data feed terminated: DeltaWebSocketClient.run() returned "
        "(running=%s, reconnect_attempt=%s). A healthy feed never returns, so "
        "the engine is now blind while /health would still answer UP. Exiting "
        "with status %d rather than idling; the supervisor is expected to "
        "restart the container.",
        getattr(client, "running", None),
        getattr(client, "reconnect_attempt", None),
        FEED_FAILURE_EXIT_CODE)
    raise SystemExit(FEED_FAILURE_EXIT_CODE)


async def main():
    """Main entry point for the Python engine."""
    logger.info(f"Starting QuantEdge Engine v2.0.0 in {settings.environment} mode")
    logger.info(f"Configured symbols: {settings.default_symbols}")
    logger.info(f"Default timeframe: {settings.default_timeframe}")

    health_server = start_health_server()

    # Register the Manual SMC strategy with the runtime. No orchestrator is
    # supplied, so `ManualSMCRuntime.submit()` refuses every ready decision with
    # NO_ORCHESTRATION_BOUND: the execution boundary stays unbound whatever
    # LIVE_FEED_ENABLED says, matching the existing algo_enabled=False fail-safe.
    manual_smc = build_manual_smc_runtime()
    logger.info(
        "Registered %s v%s on %s for %s",
        manual_smc.strategy_name, manual_smc.strategy_version,
        manual_smc.timeframe, list(manual_smc.symbols))

    logger.info("Engine started; execution loops remain disabled until explicitly configured")

    try:
        if settings.live_feed_enabled:
            # Structure continuity FIRST, subscription second. LuxAlgo internal
            # structure is path-dependent from bar 0, so the feed must never be
            # subscribed before the snapshot has been restored and the missing
            # closed candles has been fetched, verified and replayed. A
            # configuration refusal keeps the process up but unsubscribed; any
            # continuity failure has already exited non-zero by this point.
            try:
                bootstrap = bootstrap_or_fail(
                    manual_smc,
                    monitor=STRUCTURE_MONITOR,
                    state_dir=settings.engine_state_dir,
                    genesis_dir=settings.engine_genesis_history_dir)
            except INERT_BOOTSTRAP_REFUSALS as refusal:
                STRUCTURE_MONITOR.mark_inert(refusal.code, str(refusal))
                await _remain_inert(refusal)
            else:
                if settings.production_execution_enabled:
                    # Production execution: canonical stack composition, startup
                    # reconciliation, admission contract, and both clocks. Never
                    # returns while the websocket is connected.
                    from quantedge.runtime.production_composition import (
                        run_production_execution,
                    )
                    await run_production_execution(
                        runtime=bootstrap.runtime,
                        state_path=bootstrap.state_path,
                        settings=settings)
                else:
                    # Market data only, and it never returns: see
                    # run_live_market_data.
                    await run_live_market_data(
                        bootstrap.runtime, bootstrap.state_path)
        else:
            # Default fail-safe path, unchanged from before the gate existed:
            # no socket is opened, no market data is consumed, nothing is
            # scanned and no order is possible.
            logger.info(
                "LIVE_FEED_ENABLED is false: no market-data socket will be "
                "opened; set it to true to bind the closed-candle feed")
            while True:
                await asyncio.sleep(60)
                logger.debug("Engine heartbeat")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        health_server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
