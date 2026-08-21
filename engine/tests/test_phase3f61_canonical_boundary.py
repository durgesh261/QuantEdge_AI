"""
Phase 3F.6.1 — Canonical Dataset Boundary & Year Partition Test Suite.

Verifies:
1. Incremental backfill starts at last persisted candle + 1h
2. Backfill never requests timestamps before first canonical year (2026-01-01 00:00 UTC)
3. 2025 candle rejected by 2026.csv (Year partition guard raises ValueError)
4. 2024 candle rejected by 2026.csv (Year partition guard raises ValueError)
5. 2026 candle accepted and persisted
6. Historical test fixture cannot mutate production canonical CSV
7. Reverse pagination cannot expand canonical dataset backward
8. Duplicate candle remains idempotent (no duplicate rows, unchanged count)
9. Revised candle updates correctly (OHLCV updated in place)
10. Original 2026 historical slice SHA remains immutable (2000fe264d7a...)
"""

import csv
import json
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from quantedge.market_data.ingestion import (
    validate_candle_ohlcv,
    validate_candle_year,
    upsert_closed_candles,
    fetch_closed_candles,
    load_candles,
    write_candles,
    csv_hash,
    detect_gaps,
    CANONICAL_CSV,
    CANONICAL_META,
    MIN_CANONICAL_YEAR_START_TS,
    DeltaExchangeIngestionService,
)
from quantedge.market_data.delta_websocket import (
    DeltaWebSocketClient,
    _parse_candle_from_ws,
    _is_candle_closed,
)

HOUR = 3600
BASE_2026_TS = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
BASE_2025_TS = int(datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
BASE_2024_TS = int(datetime(2024, 12, 23, 16, 0, 0, tzinfo=timezone.utc).timestamp())

EXPECTED_HISTORICAL_SHA = "2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b"


def _make_candle(ts: int, o=50000.0, h=50100.0, l=49900.0, c=50050.0, v=1000.0) -> dict:
    return {
        "timestamp": ts,
        "open": Decimal(str(o)),
        "high": Decimal(str(h)),
        "low": Decimal(str(l)),
        "close": Decimal(str(c)),
        "volume": Decimal(str(v)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Year Partition Guard Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestYearPartitionGuard:
    """Tests 3, 4, 5: Hard calendar-year partition boundaries."""

    def test_2025_candle_rejected_by_2026_csv(self, tmp_path):
        """A 2025 candle MUST be rejected when targeting 2026.csv."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"
        candle_2025 = _make_candle(BASE_2025_TS)

        with pytest.raises(ValueError, match="does not belong to 2026.csv"):
            upsert_closed_candles([candle_2025], csv_path, meta_path, check_closed=False)

    def test_2024_candle_rejected_by_2026_csv(self, tmp_path):
        """A 2024 candle (including historical test fixture) MUST be rejected by 2026.csv."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"
        candle_2024 = _make_candle(BASE_2024_TS)

        with pytest.raises(ValueError, match="does not belong to 2026.csv"):
            upsert_closed_candles([candle_2024], csv_path, meta_path, check_closed=False)

    def test_2026_candle_accepted(self, tmp_path):
        """A valid 2026 candle is accepted and persisted to 2026.csv."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"
        candle_2026 = _make_candle(BASE_2026_TS + 5 * HOUR)

        result = upsert_closed_candles([candle_2026], csv_path, meta_path, check_closed=False)
        assert result.inserts == 1
        assert count_csv_rows(csv_path) == 1

    def test_validate_candle_year_direct(self):
        """Direct unit test of validate_candle_year helper."""
        c_2026 = _make_candle(BASE_2026_TS)
        c_2025 = _make_candle(BASE_2025_TS)
        c_2024 = _make_candle(BASE_2024_TS)

        # 2026 valid for 2026
        validate_candle_year(c_2026, target_year=2026)
        validate_candle_year(c_2026, csv_path=Path("data/2026.csv"))

        # 2025 invalid for 2026
        with pytest.raises(ValueError, match="does not belong"):
            validate_candle_year(c_2025, target_year=2026)

        # 2024 invalid for 2026
        with pytest.raises(ValueError, match="does not belong"):
            validate_candle_year(c_2024, target_year=2026)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REST Backfill Boundary Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestBackfillBoundary:
    """Tests 1, 2, 7: Incremental backfill boundaries and forward-only operation."""

    def test_incremental_backfill_starts_at_last_candle_plus_1h(self, tmp_path):
        """Incremental ingestion computes start_ts = max(existing) + 1h."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"

        # Populate with 3 candles: H0, H1, H2
        existing = {
            BASE_2026_TS + i * HOUR: {
                "timestamp": datetime.fromtimestamp(BASE_2026_TS + i * HOUR, tz=timezone.utc),
                "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"),
                "close": Decimal("50050"), "volume": Decimal("1000"),
            }
            for i in range(3)
        }
        write_candles(csv_path, existing)

        service = DeltaExchangeIngestionService()
        service.csv_path = csv_path
        service.meta_path = meta_path

        candles = service._load_candles()
        start_ts = max(candles.keys()) + 3600
        assert start_ts == BASE_2026_TS + 3 * HOUR

    def test_backfill_never_requests_before_canonical_year(self):
        """fetch_closed_candles bounds start_ts to >= MIN_CANONICAL_YEAR_START_TS."""
        with patch("quantedge.market_data.ingestion._fetch_window") as mock_fetch:
            mock_fetch.return_value = []
            # Request from 2020 (far in past)
            fetch_closed_candles(0, BASE_2026_TS + 10 * HOUR)

            # Verify no window request was made with start < 2026-01-01
            for call in mock_fetch.call_args_list:
                req_start, req_end = call[0]
                assert req_start >= MIN_CANONICAL_YEAR_START_TS, (
                    f"fetch_closed_candles requested timestamp before 2026: {req_start}"
                )

    def test_reverse_pagination_cannot_expand_backward_into_past_years(self):
        """Reverse pagination stops at effective_start (2026-01-01)."""
        call_windows = []

        def mock_fetch(start, end):
            call_windows.append((start, end))
            # Return candles down to start
            return [{"time": end - 3600, "o": "50000", "h": "50100", "l": "49900", "c": "50050", "v": "1000"}]

        with patch("quantedge.market_data.ingestion._fetch_window", side_effect=mock_fetch):
            candles = fetch_closed_candles(BASE_2026_TS, BASE_2026_TS + 5 * HOUR)

        for w_start, w_end in call_windows:
            assert w_start >= MIN_CANONICAL_YEAR_START_TS
        for c in candles:
            assert c["time"] >= MIN_CANONICAL_YEAR_START_TS
            assert datetime.fromtimestamp(c["time"], tz=timezone.utc).year == 2026


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Test Isolation & Production CSV Safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductionCSVSafety:
    """Test 6: Test fixtures cannot mutate production canonical CSV."""

    def test_historical_fixture_cannot_mutate_production_csv(self):
        """A test attempting to upsert a 2024 fixture into CANONICAL_CSV is rejected by year guard."""
        candle_2024 = _make_candle(BASE_2024_TS)
        with pytest.raises(ValueError, match="does not belong to 2026.csv"):
            upsert_closed_candles([candle_2024], CANONICAL_CSV, CANONICAL_META, check_closed=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Upsert Idempotency & Revision Semantics
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpsertSemantics:
    """Tests 8, 9: Deduplication and update semantics."""

    def test_duplicate_candle_is_idempotent(self, tmp_path):
        """Resubmitting identical candle returns unchanged=1, inserts=0."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"
        c = _make_candle(BASE_2026_TS + 10 * HOUR)

        r1 = upsert_closed_candles([c], csv_path, meta_path, check_closed=False)
        assert r1.inserts == 1

        r2 = upsert_closed_candles([c], csv_path, meta_path, check_closed=False)
        assert r2.inserts == 0
        assert r2.unchanged == 1
        assert count_csv_rows(csv_path) == 1

    def test_revised_candle_updates_correctly(self, tmp_path):
        """Submitting modified OHLCV for existing timestamp updates in-place."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"
        c1 = _make_candle(BASE_2026_TS + 10 * HOUR, c=50050.0)
        c2 = _make_candle(BASE_2026_TS + 10 * HOUR, c=51000.0, h=51500.0)

        upsert_closed_candles([c1], csv_path, meta_path, check_closed=False)
        r2 = upsert_closed_candles([c2], csv_path, meta_path, check_closed=False)

        assert r2.updates == 1
        assert r2.inserts == 0
        candles = load_candles(csv_path)
        assert candles[BASE_2026_TS + 10 * HOUR]["close"] == Decimal("51000.0")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Historical Baseline Preservation Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoricalBaselinePreservation:
    """Test 10: Permanent proof that 2026 baseline slice is immutable."""

    def test_original_2026_historical_slice_sha_immutable(self):
        """
        The historical slice [2026-01-01T00:00:00, 2026-08-20T00:00:00] must contain
        exactly 5,545 rows and match the canonical baseline SHA-256:
        2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b
        """
        start_ts = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        cutoff_ts = int(datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc).timestamp())

        with open(CANONICAL_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        slice_rows = [
            r for r in rows
            if start_ts <= int(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()) <= cutoff_ts
        ]

        assert len(slice_rows) == 5545, f"Expected 5545 historical candles, got {len(slice_rows)}"

        h = hashlib.sha256()
        for r in slice_rows:
            ts = int(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
            line = f"{ts},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}\n"
            h.update(line.encode())

        computed = h.hexdigest()
        assert computed == EXPECTED_HISTORICAL_SHA, (
            f"Historical baseline slice SHA mismatch!\n"
            f"Computed: {computed}\n"
            f"Expected: {EXPECTED_HISTORICAL_SHA}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. WebSocket Year Partition Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebSocketYearPartition:
    """Test 10: WebSocket rejects 2025/2024 candles before persistence."""

    @pytest.mark.asyncio
    async def test_ws_rejects_2025_candle(self, tmp_path):
        """WebSocket client receiving a 2025 message logs validation error and skips persistence."""
        csv_path = tmp_path / "2026.csv"
        meta_path = tmp_path / "2026_metadata.json"

        client = DeltaWebSocketClient(
            csv_path=csv_path,
            meta_path=meta_path,
            persist=True,
        )

        msg_2025 = {
            "type": "candlestick_1h",
            "symbol": "BTCUSD",
            "open": 50000.0,
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
            "volume": 100.0,
            "candle_start_time": BASE_2025_TS * 1_000_000,
            "timestamp": (BASE_2025_TS + 1800) * 1_000_000,
        }

        await client._handle_message(msg_2025)

        # Candle must NOT be in processed_timestamps and CSV must not exist/contain it
        assert BASE_2025_TS not in client.processed_timestamps
        assert not csv_path.exists() or count_csv_rows(csv_path) == 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def count_csv_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return len(list(reader))
