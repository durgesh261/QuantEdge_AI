"""
QuantEdge Engine - Main Entry Point
"""

import asyncio
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from quantedge.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal liveness endpoint for container orchestration and diagnostics."""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = b'{"status":"UP","service":"quantedge-engine"}'
        self.send_response(HTTPStatus.OK)
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


async def main():
    """Main entry point for the Python engine."""
    logger.info(f"Starting QuantEdge Engine v2.0.0 in {settings.environment} mode")
    logger.info(f"Configured symbols: {settings.default_symbols}")
    logger.info(f"Default timeframe: {settings.default_timeframe}")

    health_server = start_health_server()
    logger.info("Engine started; execution loops remain disabled until explicitly configured")

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
            logger.debug("Engine heartbeat")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        health_server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
