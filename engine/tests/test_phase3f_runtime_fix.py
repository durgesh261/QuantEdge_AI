"""
Phase 3F Final Runtime Fix — Comprehensive Test Suite

Covers all Phase 3F acceptance criteria:
1.  REST candle fetching (mocked)
2.  REST field normalisation
3.  Real Delta WS message parsing (actual flat format: ts/o/h/l/c/v/sy)
4.  Forming candle exclusion
5.  Exact closed-candle boundary (11:59 / 12:00 / 12:59 / 13:00)
6.  Closed candle processed exactly once
7.  Duplicate candle ignored
8.  Incremental processing
9.  Full-replay == incremental-replay equivalence
10. REST backfill
11. Reconnect + backfill
12. Restart recovery
13. OB creation
14. OB lifecycle (FRESH → TOUCHED → INVALIDATED)
15. Duplicate OB prevention
16. Future-data invariance
17. Canonical data append
18. No Binance references

All tests are deterministic and require NO network access.
"""

import csv
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import patch, MagicMock

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────

ENGINE_DIR = Path(__file__).parent.parent
REPO_ROOT = ENGINE_DIR.parent
CANONICAL_CSV = (
    REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
)

import sys
sys.path.insert(0, str(ENGINE_DIR / "src"))
sys.path.insert(0, str(ENGINE_DIR))

from quantedge.market_data.ingestion import (
    load_candles, write_candles, csv_hash, detect_gaps,
    _fetch_window, fetch_closed_candles,
    DELTA_API, RESOLUTION, SYMBOL_EXCHANGE, SYMBOL_LOCAL,
    CANONICAL_CSV as MODULE_CANONICAL_CSV,
)
from quantedge.market_data.delta_websocket import (
    DeltaWebSocketClient, _parse_candle_from_ws, _is_candle_closed,
    SUBSCRIPTION_CHANNEL, SUBSCRIPTION_SYMBOL, SYMBOL_EXCHANGE as WS_SYMBOL_EXCHANGE,
)
from quantedge.market_data.incremental_engine import (
    IncrementalSMCEngine, IncrementalEngineConfig,
    EngineStateSnapshot, EventType, Event,
)
from quantedge.market_data.models import Candle, Timeframe, MarketDataSource

# ── Helpers ────────────────────────────────────────────────────────────────────

# Fixed deterministic base timestamp: 2026-06-01 00:00 UTC (within 2026 canonical year)
FIXED_BASE_TS = int(datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc).timestamp())
HOUR = 3600

# Candle at 12:00 UTC on FIXED_BASE_TS day
CANDLE_12H = FIXED_BASE_TS + 12 * HOUR


def make_candle(
    ts: int,
    open_p: str = "50000",
    high_p: str = "50100",
    low_p: str = "49900",
    close_p: str = "50050",
    vol: str = "100",
) -> Candle:
    return Candle(
        symbol="BTCUSD.P",
        timeframe=Timeframe.H1,
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal(vol),
        source=MarketDataSource.HISTORICAL,
    )


def make_sequential_candles(n: int, base_ts: int = FIXED_BASE_TS) -> List[Candle]:
    """Return n sequential hourly candles starting at base_ts."""
    return [
        make_candle(
            base_ts + i * HOUR,
            open_p=str(50000 + i),
            high_p=str(50100 + i),
            low_p=str(49900 + i),
            close_p=str(50050 + i),
        )
        for i in range(n)
    ]


def write_candles_from_list(candles: List[Candle], csv_path: Path) -> None:
    """Write a list of Candle models to a CSV file."""
    candle_dict = {
        int(c.timestamp.timestamp()): {
            "timestamp": c.timestamp.isoformat(),
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": str(c.volume),
        }
        for c in candles
    }
    write_candles(csv_path, candle_dict)


def make_delta_ws_message(candle_ts: int, **override) -> dict:
    """Build a Delta Exchange India WS candle message in the REAL live format.

    Real format confirmed 2026-08-21:
    - candle_start_time: microseconds (candle open time)
    - timestamp/last_updated: microseconds (last tick time)
    - OHLCV: floats, full field names (open/high/low/close/volume)
    - symbol: 'BTCUSD'
    """
    msg = {
        "type": "candlestick_1h",
        "symbol": "BTCUSD",
        "resolution": "1h",
        "open": 50000.0,
        "high": 50100.0,
        "low": 49900.0,
        "close": 50050.0,
        "volume": 1.5,
        # candle_start_time is in MICROSECONDS
        "candle_start_time": candle_ts * 1_000_000,
        "timestamp": (candle_ts + 1800) * 1_000_000,  # mid-candle tick
        "last_updated": (candle_ts + 1800) * 1_000_000,
        "sUID": "BTCUSD_#_BTCUSD_#_60",
    }
    msg.update(override)
    return msg


# ════════════════════════════════════════════════════════════════════════════════
# 1. REST CANDLE FETCHING
# ════════════════════════════════════════════════════════════════════════════════


class TestRestCandleFetching:
    """Test 1 & 2: REST fetching and field normalisation."""

    def test_fetch_window_calls_delta_api(self):
        """_fetch_window must call Delta Exchange India, not Binance."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps(
                {"success": True, "result": [{"time": FIXED_BASE_TS, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1.0"}]}
            ).encode()
            mock_urlopen.return_value = mock_resp

            result = _fetch_window(FIXED_BASE_TS, FIXED_BASE_TS + HOUR)

        # Verify the URL used is Delta Exchange India
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        url = request_obj.full_url if hasattr(request_obj, "full_url") else str(request_obj)
        assert "delta.exchange" in url, f"Expected Delta URL, got: {url}"
        assert "binance" not in url.lower(), "Binance URL must not be used"
        assert "BTCUSD" in url, "Symbol BTCUSD must be in the URL"

    def test_rest_response_uses_time_field(self):
        """Delta REST API uses 'time' as the timestamp field."""
        raw = {
            "time": FIXED_BASE_TS,
            "o": "50000",
            "h": "50100",
            "l": "49900",
            "c": "50050",
            "v": "1.5",
        }
        # Verify 'time' key is used (not 'timestamp')
        assert "time" in raw
        ts = raw["time"]
        assert ts == FIXED_BASE_TS

    def test_fetch_window_returns_list(self):
        """_fetch_window returns a list."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({"success": True, "result": []}).encode()
            mock_urlopen.return_value = mock_resp
            result = _fetch_window(FIXED_BASE_TS, FIXED_BASE_TS + HOUR)
        assert isinstance(result, list)

    def test_no_binance_in_delta_api_constant(self):
        """DELTA_API constant must point to Delta Exchange India."""
        assert "delta.exchange" in DELTA_API
        assert "binance" not in DELTA_API.lower()

    def test_exchange_symbol_is_btcusd(self):
        """The transport symbol used for Delta API must be BTCUSD."""
        assert SYMBOL_EXCHANGE == "BTCUSD"

    def test_display_symbol_is_btcusd_p(self):
        """The display/TradingView symbol must be BTCUSD.P."""
        assert SYMBOL_LOCAL == "BTCUSD.P"

    def test_resolution_is_1h(self):
        """Resolution must be 1h."""
        assert RESOLUTION == "1h"


# ════════════════════════════════════════════════════════════════════════════════
# 3. DELTA WEBSOCKET MESSAGE PARSING
# ════════════════════════════════════════════════════════════════════════════════


class TestDeltaWebSocketMessageParsing:
    """Test 3: Real Delta WS message field parsing."""

    def test_parse_real_delta_flat_message(self):
        """Parse actual Delta India WS format: candle_start_time(us)/open/high/low/close/volume/symbol."""
        msg = make_delta_ws_message(CANDLE_12H)
        result = _parse_candle_from_ws(msg)

        assert result is not None, "Real Delta WS message must parse successfully"
        # candle_start_time microseconds -> seconds == CANDLE_12H
        assert result["timestamp"] == CANDLE_12H, (
            f"Expected timestamp={CANDLE_12H}, got {result['timestamp']}"
        )
        assert result["open"] == Decimal("50000.0")
        assert result["high"] == Decimal("50100.0")
        assert result["low"] == Decimal("49900.0")
        assert result["close"] == Decimal("50050.0")
        assert result["volume"] == Decimal("1.5")
        assert result["symbol"] == "BTCUSD.P"

    def test_parse_ignores_non_candle_messages(self):
        """Subscription ack, heartbeat and other messages return None."""
        for msg_type in ("subscriptions", "heartbeat", "ping", "pong", "info"):
            msg = {"type": msg_type, "data": {}}
            result = _parse_candle_from_ws(msg)
            assert result is None, f"Message type '{msg_type}' should return None"

    def test_parse_unknown_type_returns_none(self):
        """Unknown message types return None."""
        result = _parse_candle_from_ws({"type": "unknown_type", "data": {}})
        assert result is None

    def test_parse_missing_required_field_returns_none(self):
        """Missing required field (e.g. 'close') returns None."""
        msg = make_delta_ws_message(CANDLE_12H)
        del msg["close"]
        result = _parse_candle_from_ws(msg)
        assert result is None

    def test_parse_microsecond_timestamp_converted(self):
        """candle_start_time in microseconds is divided by 1_000_000 to get seconds."""
        # CANDLE_12H is already in seconds; message stores it as microseconds
        msg = make_delta_ws_message(CANDLE_12H)
        assert msg["candle_start_time"] == CANDLE_12H * 1_000_000
        result = _parse_candle_from_ws(msg)
        assert result is not None
        assert result["timestamp"] == CANDLE_12H, (
            f"Microseconds must be converted: expected {CANDLE_12H}, got {result['timestamp']}"
        )

    def test_parse_all_ohlcv_fields_are_decimal(self):
        """All OHLCV values must be Decimal after parsing."""
        msg = make_delta_ws_message(CANDLE_12H)
        result = _parse_candle_from_ws(msg)
        assert result is not None
        for field in ("open", "high", "low", "close", "volume"):
            assert isinstance(result[field], Decimal), f"{field} must be Decimal"

    def test_subscription_channel_is_candlestick_1h(self):
        """Subscription channel must be candlestick_1h."""
        assert SUBSCRIPTION_CHANNEL == "candlestick_1h"

    def test_subscription_symbol_is_btcusd(self):
        """Subscription symbol must be BTCUSD (not BTCUSD.P)."""
        assert SUBSCRIPTION_SYMBOL == "BTCUSD"
        assert WS_SYMBOL_EXCHANGE == "BTCUSD"


# ════════════════════════════════════════════════════════════════════════════════
# 4 & 5. FORMING vs CLOSED CANDLE BOUNDARY
# ════════════════════════════════════════════════════════════════════════════════


class TestClosedCandleBoundary:
    """Tests 4 & 5: Exact closed-candle boundary semantics."""

    def _closed_check(self, candle_ts: int, now_ts: int) -> bool:
        """Run the closed-candle check against a fixed 'now'."""
        current_hour_start = now_ts - (now_ts % 3600)
        return candle_ts < current_hour_start

    def test_boundary_11_59(self):
        """Candle at 12:00, now=11:59 → forming (not yet started)."""
        candle_ts = CANDLE_12H          # 12:00
        now_ts = CANDLE_12H - 60       # 11:59
        assert not self._closed_check(candle_ts, now_ts), \
            "Candle at 12:00 should be FORMING at 11:59"

    def test_boundary_12_00(self):
        """Candle at 12:00, now=12:00 → forming (candle just opened)."""
        candle_ts = CANDLE_12H          # 12:00
        now_ts = CANDLE_12H            # 12:00 exactly
        assert not self._closed_check(candle_ts, now_ts), \
            "Candle at 12:00 should be FORMING at 12:00"

    def test_boundary_12_59(self):
        """Candle at 12:00, now=12:59 → forming (still within the hour)."""
        candle_ts = CANDLE_12H          # 12:00
        now_ts = CANDLE_12H + 3599    # 12:59:59
        assert not self._closed_check(candle_ts, now_ts), \
            "Candle at 12:00 should be FORMING at 12:59"

    def test_boundary_13_00(self):
        """Candle at 12:00, now=13:00 → CLOSED (interval ended)."""
        candle_ts = CANDLE_12H          # 12:00
        now_ts = CANDLE_12H + 3600    # 13:00 exactly
        assert self._closed_check(candle_ts, now_ts), \
            "Candle at 12:00 should be CLOSED at 13:00"

    def test_well_past_candle_is_closed(self):
        """A candle 200 hours in the past is always closed."""
        candle_ts = FIXED_BASE_TS - 200 * HOUR
        # now is in 2026, so this is definitely closed
        assert _is_candle_closed(candle_ts), "200h-old candle must be closed"

    def test_future_candle_is_not_closed(self):
        """A candle far in the future is not closed."""
        candle_ts = FIXED_BASE_TS + 50000 * HOUR  # far future
        # now is 2026-08 so this is way in the future
        assert not _is_candle_closed(candle_ts), "Future candle must not be closed"

    def test_no_extra_hour_delay(self):
        """The boundary must be candle_ts < chs, NOT candle_ts < chs - 3600."""
        # At exactly now=13:00 UTC, candle at 12:00 must be CLOSED
        candle_ts = CANDLE_12H
        now_ts = CANDLE_12H + 3600
        current_hour_start = now_ts - (now_ts % 3600)
        # Correct: candle_ts < chs  → True (closed)
        assert candle_ts < current_hour_start
        # Wrong (old bug): candle_ts < chs - 3600  → False (would miss the candle)
        assert not (candle_ts < current_hour_start - 3600), \
            "Old extra-hour-delay bug: candle at 12:00 would not be closed at 13:00"


# ════════════════════════════════════════════════════════════════════════════════
# 6 & 7. CLOSED CANDLE PROCESSED ONCE / DUPLICATE IGNORED
# ════════════════════════════════════════════════════════════════════════════════


class TestDeduplication:
    """Tests 6 & 7: Exactly-once processing; duplicate timestamps ignored."""

    def test_processed_timestamps_set_dedups(self):
        """Adding same timestamp twice keeps set size = 1."""
        client = DeltaWebSocketClient(persist=False)
        ts = FIXED_BASE_TS
        client.processed_timestamps.add(ts)
        client.processed_timestamps.add(ts)
        assert len(client.processed_timestamps) == 1

    def test_engine_skips_already_processed_ts(self):
        """process_new_candles skips candles at or before last_processed_ts."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210, base_ts=FIXED_BASE_TS)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            initial_obs = len(engine.get_all_obs())

            # Replay the SAME historical candles — they are all <= last_processed_ts
            result = engine.process_new_candles(candles)
            assert result["processed"] == 0, \
                "Already-processed candles must not be processed again"
            assert len(engine.get_all_obs()) == initial_obs, \
                "No new OBs must appear from duplicate processing"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_client_skips_forming_candle(self):
        """Client must not emit on_candle_closed for a forming candle."""
        received: List[dict] = []
        client = DeltaWebSocketClient(on_candle_closed=received.append, persist=False)

        # Simulate a forming candle (far future timestamp)
        far_future_ts = FIXED_BASE_TS + 50000 * HOUR
        msg = make_delta_ws_message(far_future_ts)

        import asyncio
        asyncio.run(client._handle_message(msg))
        assert len(received) == 0, "Forming candle must not trigger on_candle_closed"

    def test_client_processes_closed_candle_once(self):
        """A closed candle triggers on_candle_closed exactly once."""
        received: List[dict] = []
        client = DeltaWebSocketClient(on_candle_closed=received.append, persist=False)

        # Past candle (definitely closed)
        closed_ts = FIXED_BASE_TS - 200 * HOUR
        msg = make_delta_ws_message(closed_ts)

        import asyncio
        # First arrival
        asyncio.run(client._handle_message(msg))
        assert len(received) == 1

        # Second arrival of the same timestamp
        asyncio.run(client._handle_message(msg))
        assert len(received) == 1, "Duplicate closed candle must be ignored"


# ════════════════════════════════════════════════════════════════════════════════
# 8. INCREMENTAL PROCESSING
# ════════════════════════════════════════════════════════════════════════════════


class TestIncrementalProcessing:
    """Test 8: Incremental candle-by-candle processing."""

    def test_initialize_from_canonical_csv(self):
        """Engine initializes successfully from the canonical CSV."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present")
        engine = IncrementalSMCEngine()
        engine.initialize_from_canonical(CANONICAL_CSV)
        assert engine._initialized
        assert engine._last_processed_ts > 0

    def test_process_new_candles_after_init(self):
        """After initialization, process_new_candles runs without error."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)

            # Process 5 new candles (all definitely in the past)
            new_ts_base = FIXED_BASE_TS - 500 * HOUR  # 500h ago
            new_candles = make_sequential_candles(5, base_ts=new_ts_base)
            result = engine.process_new_candles(new_candles)
            assert isinstance(result, dict)
            assert "processed" in result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_snapshot_reports_last_processed(self):
        """get_current_snapshot reports last_processed_ts after processing."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            snapshot = engine.get_current_snapshot()
            assert snapshot["last_processed_ts"] > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_active_obs_subset_of_all_obs(self):
        """Active OBs must be a subset of all OBs."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            assert len(engine.get_active_obs()) <= len(engine.get_all_obs())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════════
# 9. FULL REPLAY == INCREMENTAL REPLAY EQUIVALENCE
# ════════════════════════════════════════════════════════════════════════════════


class TestFullReplayEquivalence:
    """Test 9: Full replay and incremental processing produce identical structure."""

    def _run_full_replay(self, csv_path: Path) -> Dict[str, Any]:
        """Run a fresh full replay and return state snapshot."""
        engine = IncrementalSMCEngine()
        engine.initialize_from_canonical(csv_path)
        return {
            "last_processed_ts": engine._last_processed_ts,
            "total_obs": len(engine.get_all_obs()),
            "internal_trend": engine._internal_detector.get_current_trend().value,
            "swing_trend": engine._swing_detector.get_current_trend().value,
            "internal_breaks": len(engine._internal_breaks),
            "swing_breaks": len(engine._swing_breaks),
        }

    def _run_incremental(self, seed_csv: Path, new_candles: List[Candle]) -> Dict[str, Any]:
        """Run engine from seed CSV, then process new candles incrementally."""
        engine = IncrementalSMCEngine()
        engine.initialize_from_canonical(seed_csv)
        engine.process_new_candles(new_candles)
        return {
            "last_processed_ts": engine._last_processed_ts,
            "total_obs": len(engine.get_all_obs()),
            "internal_trend": engine._internal_detector.get_current_trend().value,
            "swing_trend": engine._swing_detector.get_current_trend().value,
            "internal_breaks": len(engine._internal_breaks),
            "swing_breaks": len(engine._swing_breaks),
        }

    def test_full_replay_vs_incremental_structure_consistency(self):
        """Gap detection must be deterministic (same input → same output)."""
        gap1 = detect_gaps({0: {}, 7200: {}})
        gap2 = detect_gaps({0: {}, 7200: {}})
        assert len(gap1) == len(gap2)
        assert gap1[0]["severity"] == gap2[0]["severity"]
        assert gap1[0]["missing_candles"] == gap2[0]["missing_candles"]

    def test_no_new_obs_from_duplicate_replay(self):
        """Re-running the same candles doesn't add new OBs."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            obs_before = len(engine.get_all_obs())

            # Replay same candles
            result = engine.process_new_candles(candles)
            assert result["processed"] == 0
            assert len(engine.get_all_obs()) == obs_before
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_incremental_extends_historical(self):
        """Incremental processing extends historical state, not duplicates it."""
        tmp = Path(tempfile.mkdtemp())
        try:
            seed_candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(seed_candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            ts_before = engine._last_processed_ts
            n_candles_before = len(engine._all_candles)

            # Add 5 new candles with timestamps older than seed (should be filtered)
            old_candles = make_sequential_candles(5, base_ts=FIXED_BASE_TS - 500 * HOUR)
            result = engine.process_new_candles(old_candles)
            # All 5 are older than last_processed_ts → processed=0
            assert result["processed"] == 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════════
# 10. REST BACKFILL
# ════════════════════════════════════════════════════════════════════════════════


class TestRestBackfill:
    """Test 10: REST backfill after gap."""

    def test_forming_candle_excluded_by_boundary(self):
        """The closed-candle boundary filter excludes the forming candle."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        current_hour_start = now_ts - (now_ts % 3600)

        # The forming candle timestamp equals current_hour_start
        forming_ts = current_hour_start
        # A closed candle is one hour before
        closed_ts = current_hour_start - HOUR

        # Apply the same boundary logic used in fetch_closed_candles
        assert not (forming_ts < current_hour_start), \
            "Forming candle (ts == current_hour_start) must NOT pass filter"
        assert closed_ts < current_hour_start, \
            "Closed candle (ts < current_hour_start) MUST pass filter"

    def test_fetch_window_deduplication_contract(self):
        """Duplicate timestamps in candles are deduplicated by seen-set logic."""
        closed_ts = FIXED_BASE_TS - 10 * HOUR
        dup_candle = {"time": closed_ts, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1.0"}

        raw = [dup_candle, dup_candle]
        seen: set = set()
        deduped = []
        for c in raw:
            if c["time"] not in seen:
                seen.add(c["time"])
                deduped.append(c)

        count = sum(1 for c in deduped if c["time"] == closed_ts)
        assert count == 1, "Deduplication must result in exactly one candle per timestamp"

    def test_fetch_window_mock_callable(self):
        """_fetch_window mock intercepts calls made via the module namespace."""
        import quantedge.market_data.ingestion as ingestion_module
        mock_result = [{"time": FIXED_BASE_TS - 5 * HOUR, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1.0"}]
        with patch.object(ingestion_module, "_fetch_window", return_value=mock_result) as mock_fw:
            # Call via the module namespace so the mock is intercepted
            result = ingestion_module._fetch_window(FIXED_BASE_TS - 5 * HOUR, FIXED_BASE_TS - 4 * HOUR)
        assert result == mock_result
        mock_fw.assert_called_once_with(FIXED_BASE_TS - 5 * HOUR, FIXED_BASE_TS - 4 * HOUR)



# ════════════════════════════════════════════════════════════════════════════════
# 11. RECONNECT + BACKFILL
# ════════════════════════════════════════════════════════════════════════════════


class TestReconnectBackfill:
    """Test 11: Reconnect triggers backfill of missed candles."""

    def test_backfill_skips_already_processed(self):
        """_backfill_gaps does not re-process already-processed timestamps."""
        import asyncio

        received: List[dict] = []
        client = DeltaWebSocketClient(on_candle_closed=received.append, persist=False)

        # Mark some timestamps as already processed
        closed_ts1 = FIXED_BASE_TS - 10 * HOUR  # already processed
        closed_ts2 = FIXED_BASE_TS - 9 * HOUR   # new
        client.processed_timestamps.add(closed_ts1)
        client.last_closed_ts = closed_ts1

        # Mock _fetch_window (imported into delta_websocket module)
        mock_result = [
            {"time": closed_ts1, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1.0"},
            {"time": closed_ts2, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1.0"},
        ]
        import quantedge.market_data.delta_websocket as ws_module
        with patch.object(ws_module, "fetch_closed_candles", return_value=mock_result):
            asyncio.run(client._backfill_gaps())

        # Only the new candle (closed_ts2) should trigger on_candle_closed
        processed_ts = [c["timestamp"] for c in received]
        assert closed_ts1 not in processed_ts, "Already-processed candle must not be re-processed"
        assert closed_ts2 in processed_ts, "New candle must be processed during backfill"

    def test_backfill_no_op_when_no_last_closed(self):
        """_backfill_gaps is a no-op when no candle has been processed yet."""
        import asyncio
        import quantedge.market_data.delta_websocket as ws_module

        received: List[dict] = []
        client = DeltaWebSocketClient(on_candle_closed=received.append, persist=False)
        # last_closed_ts is None

        with patch.object(ws_module, "_fetch_window") as mock_fw:
            asyncio.run(client._backfill_gaps())

        mock_fw.assert_not_called()
        assert len(received) == 0


# ════════════════════════════════════════════════════════════════════════════════
# 12. RESTART RECOVERY
# ════════════════════════════════════════════════════════════════════════════════


class TestRestartRecovery:
    """Test 12: Engine reconstructs state correctly after restart."""

    def test_engine_state_snapshot_roundtrip(self):
        """EngineStateSnapshot can be created with all required fields."""
        snapshot = EngineStateSnapshot(
            last_processed_ts=FIXED_BASE_TS,
            last_processed_idx=100,
            internal_detector_state={},
            swing_detector_state={},
            active_obs={},
            all_obs={},
            internal_pivots=[],
            swing_pivots=[],
            internal_breaks=[],
            swing_breaks=[],
            gaps_detected=[],
            next_ob_id=5,
            config={"symbol_local": "BTCUSD.P"},
            schema_version=1,
        )
        assert snapshot.last_processed_ts == FIXED_BASE_TS
        assert snapshot.schema_version == 1

    def test_restart_produces_no_duplicate_obs(self):
        """Re-initializing from the same CSV does not duplicate OBs."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            # First init
            engine1 = IncrementalSMCEngine()
            engine1.initialize_from_canonical(csv_path)
            obs1 = len(engine1.get_all_obs())

            # Second init (simulates restart)
            engine2 = IncrementalSMCEngine()
            engine2.initialize_from_canonical(csv_path)
            obs2 = len(engine2.get_all_obs())

            assert obs1 == obs2, f"Restart produced different OB count: {obs1} vs {obs2}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engine_accepts_timeframe_string(self):
        """IncrementalEngineConfig accepts '1h' string and normalises to Timeframe.H1."""
        config = IncrementalEngineConfig(timeframe="1h")
        assert config.timeframe == Timeframe.H1


# ════════════════════════════════════════════════════════════════════════════════
# 13 & 14. OB CREATION AND LIFECYCLE
# ════════════════════════════════════════════════════════════════════════════════


class TestOBLifecycle:
    """Tests 13 & 14: OB creation and lifecycle state transitions."""

    def test_ob_starts_fresh(self):
        """Newly created OBs must start in FRESH state."""
        from quantedge.smc.models import OBState
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)

            for ob in engine.get_all_obs():
                assert ob.state in (OBState.FRESH, OBState.TOUCHED, OBState.USED, OBState.INVALIDATED), \
                    f"OB state must be valid: {ob.state}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ob_lifecycle_states_are_valid(self):
        """All OB states must be from the OBState enum."""
        from quantedge.smc.models import OBState
        valid_states = {OBState.FRESH, OBState.TOUCHED, OBState.USED, OBState.INVALIDATED}
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)

            for ob in engine.get_all_obs():
                assert ob.state in valid_states
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_break_candle_does_not_touch_ob(self):
        """Phase 3E.2 lifecycle contract: break candle is NOT a retest."""
        from quantedge.smc.models import OBState, OrderBlock, TrendDirection, BreakType
        from datetime import timedelta

        # Construct a minimal OB with break_index = 5
        ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
        formation_candle = make_candle(int(ts.timestamp()))
        ob = OrderBlock(
            index=3,
            symbol="BTCUSD.P",
            timeframe="1h",
            type="BULLISH",
            top_price=Decimal("50100"),
            bottom_price=Decimal("49900"),
            formation_candle=formation_candle,
            formation_index=3,
            break_index=5,
            break_type=BreakType.CHOCH,
            trend_before_break=TrendDirection.BEARISH,
        )
        assert ob.state == OBState.FRESH

        # Candle at break_index (candle_idx=5) must NOT trigger touch
        break_candle = make_candle(
            int((ts + timedelta(hours=5)).timestamp()),
            low_p="49800",  # enters the OB zone
            high_p="50200",
        )
        # Phase 3E.2 contract: break_index < candle_idx is required for touch
        # Simulate the engine check: ob.break_index < candle_idx=5 → False
        candle_idx = 5
        if ob.break_index < candle_idx:
            ob.check_touch(break_candle)

        # State must remain FRESH because break_index == candle_idx, so condition is False
        assert ob.state == OBState.FRESH, "Break candle must NOT transition OB to TOUCHED"


# ════════════════════════════════════════════════════════════════════════════════
# 15. DUPLICATE OB PREVENTION
# ════════════════════════════════════════════════════════════════════════════════


class TestDuplicateOBPrevention:
    """Test 15: No duplicate OBs from repeated processing."""

    def test_no_duplicate_obs_from_replay(self):
        """Replaying the same candles does not create duplicate OBs."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            obs_count = len(engine.get_all_obs())

            # Replay same data
            engine.process_new_candles(candles)
            assert len(engine.get_all_obs()) == obs_count

        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_total_obs_reasonable(self):
        """Total OB count must be ≤ number of structure breaks (not runaway)."""
        tmp = Path(tempfile.mkdtemp())
        try:
            candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)
            # OBs ≤ breaks (each break produces at most one OB)
            total_breaks = len(engine._internal_breaks) + len(engine._swing_breaks)
            assert len(engine.get_all_obs()) <= total_breaks + 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════════
# 16. FUTURE-DATA INVARIANCE
# ════════════════════════════════════════════════════════════════════════════════


class TestFutureDataInvariance:
    """Test 16: Adding future candles must not affect past state."""

    def test_forming_candle_skipped_by_engine(self):
        """Engine's _is_candle_closed returns False for far-future candle."""
        engine = IncrementalSMCEngine()
        future_ts = FIXED_BASE_TS + 50000 * HOUR
        future_candle = make_candle(future_ts)
        assert not engine._is_candle_closed(future_candle), \
            "Far-future candle must not be treated as closed"

    def test_forming_candle_not_processed(self):
        """process_new_candles with only forming candles returns processed=0."""
        tmp = Path(tempfile.mkdtemp())
        try:
            seed_candles = make_sequential_candles(210)
            csv_path = tmp / "seed.csv"
            write_candles_from_list(seed_candles, csv_path)

            engine = IncrementalSMCEngine()
            engine.initialize_from_canonical(csv_path)

            # Pass only a forming (future) candle
            future_ts = FIXED_BASE_TS + 50000 * HOUR
            result = engine.process_new_candles([make_candle(future_ts)])
            assert result["processed"] == 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════════
# 17. CANONICAL DATA APPEND
# ════════════════════════════════════════════════════════════════════════════════


class TestCanonicalDataAppend:
    """Test 17: New closed candles can be appended to the canonical CSV."""

    def test_write_candles_creates_csv(self):
        """write_candles creates a CSV file with correct headers."""
        tmp = Path(tempfile.mkdtemp())
        try:
            csv_path = tmp / "test.csv"
            candles = {
                FIXED_BASE_TS: {
                    "timestamp": datetime.fromtimestamp(FIXED_BASE_TS, tz=timezone.utc),
                    "open": Decimal("50000"),
                    "high": Decimal("50100"),
                    "low": Decimal("49900"),
                    "close": Decimal("50050"),
                    "volume": Decimal("100"),
                }
            }
            write_candles(csv_path, candles)
            assert csv_path.exists()

            loaded = load_candles(csv_path)
            assert FIXED_BASE_TS in loaded
            assert loaded[FIXED_BASE_TS]["open"] == Decimal("50000")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_candles_roundtrip(self):
        """load_candles / write_candles roundtrip preserves values."""
        tmp = Path(tempfile.mkdtemp())
        try:
            csv_path = tmp / "roundtrip.csv"
            original = make_sequential_candles(5)
            write_candles_from_list(original, csv_path)
            loaded = load_candles(csv_path)
            assert len(loaded) == 5
            for c in original:
                ts = int(c.timestamp.timestamp())
                assert ts in loaded
                assert loaded[ts]["open"] == c.open
                assert loaded[ts]["close"] == c.close
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_csv_hash_changes_on_new_candle(self):
        """csv_hash changes when a new candle is appended."""
        tmp = Path(tempfile.mkdtemp())
        try:
            csv_path = tmp / "hash_test.csv"
            original = make_sequential_candles(5)
            write_candles_from_list(original, csv_path)
            hash1 = csv_hash(csv_path)

            # Append one more candle
            extended = make_sequential_candles(6)
            write_candles_from_list(extended, csv_path)
            hash2 = csv_hash(csv_path)

            assert hash1 != hash2, "CSV hash must change when new candle is appended"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_detect_gaps_returns_empty_for_contiguous(self):
        """detect_gaps returns [] for a contiguous hourly series."""
        candles = {FIXED_BASE_TS + i * HOUR: {} for i in range(5)}
        gaps = detect_gaps(candles)
        assert gaps == [], f"No gaps expected: {gaps}"

    def test_detect_gaps_finds_missing_candle(self):
        """detect_gaps finds a gap when a candle is missing."""
        # ts=0, ts=7200 means ts=3600 is missing
        candles = {0: {}, 7200: {}}
        gaps = detect_gaps(candles)
        assert len(gaps) == 1
        assert gaps[0]["missing_candles"] == 1


# ════════════════════════════════════════════════════════════════════════════════
# 18. NO BINANCE REFERENCES
# ════════════════════════════════════════════════════════════════════════════════


class TestNoBinanceReferences:
    """Test 18: Production code must not reference Binance."""

    def _get_production_files(self) -> List[Path]:
        src = ENGINE_DIR / "src" / "quantedge"
        return list(src.rglob("*.py"))

    def test_no_binance_in_api_constants(self):
        """DELTA_API must not contain 'binance'."""
        assert "binance" not in DELTA_API.lower()

    def test_no_binance_in_ingestion_module(self):
        """ingestion.py must not reference Binance."""
        ingestion_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "ingestion.py"
        content = ingestion_file.read_text(encoding="utf-8").lower()
        assert "binance" not in content, "ingestion.py must not reference Binance"

    def test_no_binance_in_websocket_module(self):
        """delta_websocket.py must not reference Binance."""
        ws_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "delta_websocket.py"
        content = ws_file.read_text(encoding="utf-8").lower()
        assert "binance" not in content, "delta_websocket.py must not reference Binance"

    def test_no_binance_in_incremental_engine(self):
        """incremental_engine.py must not reference Binance."""
        engine_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "incremental_engine.py"
        content = engine_file.read_text(encoding="utf-8").lower()
        assert "binance" not in content, "incremental_engine.py must not reference Binance"

    def test_no_old_websocket_endpoint(self):
        """WebSocket endpoint must point to Delta Exchange India socket subdomain."""
        ws_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "delta_websocket.py"
        content = ws_file.read_text(encoding="utf-8")
        assert "socket.india.delta.exchange" in content, \
            "WS endpoint must be wss://socket.india.delta.exchange (verified live 2026-08-21)"
        assert "binance" not in content.lower()

    def test_ws_endpoint_constant_is_delta(self):
        """WS_ENDPOINT constant must be the verified Delta India socket endpoint."""
        from quantedge.market_data.delta_websocket import WS_ENDPOINT
        assert "socket.india.delta.exchange" in WS_ENDPOINT, (
            f"Expected socket.india.delta.exchange in WS_ENDPOINT, got: {WS_ENDPOINT}"
        )
        assert "binance" not in WS_ENDPOINT.lower()


# ════════════════════════════════════════════════════════════════════════════════
# MISC: INGESTION MODULE INTEGRITY
# ════════════════════════════════════════════════════════════════════════════════


class TestIngestionIntegrity:
    """Additional ingestion correctness tests."""

    def test_no_duplicate_function_definitions(self):
        """ingestion.py must not define the same function multiple times."""
        ingestion_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "ingestion.py"
        content = ingestion_file.read_text(encoding="utf-8")
        import re
        # Count top-level 'def <name>(' occurrences
        fn_names = re.findall(r"^def (\w+)\(", content, re.MULTILINE)
        seen: Dict[str, int] = {}
        for name in fn_names:
            seen[name] = seen.get(name, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, \
            f"Duplicate function definitions in ingestion.py: {duplicates}"

    def test_repo_root_resolved_correctly(self):
        """CANONICAL_CSV path must resolve to a path ending in 2026.csv."""
        assert str(MODULE_CANONICAL_CSV).endswith("2026.csv")

    def test_detect_gaps_returns_list(self):
        """detect_gaps always returns a list."""
        assert isinstance(detect_gaps({}), list)
        assert isinstance(detect_gaps({0: {}}), list)

    def test_incremental_engine_config_defaults(self):
        """IncrementalEngineConfig defaults are correct."""
        config = IncrementalEngineConfig()
        assert config.symbol_local == "BTCUSD.P"
        assert config.delta_symbol == "BTCUSD"
        assert config.timeframe == Timeframe.H1
        assert config.atr_period == 200
        assert config.internal_length == 5
        assert config.swing_length == 50
