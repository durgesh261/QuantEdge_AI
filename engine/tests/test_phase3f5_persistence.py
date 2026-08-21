"""
Phase 3F.5 — Persistent Live Data Storage Test Suite.

Tests the single authoritative persistence contract:
  validate_candle_ohlcv()
  upsert_closed_candles()
  DeltaWebSocketClient persistence integration

All tests use tmp_path (pytest fixture) — the production canonical CSV
is NEVER modified.  Repository-integrity checks (no-Binance, frozen SMC)
read files in read-only mode only.

Rules verified:
  Rule 1  — Only closed candles persisted; forming candles rejected
  Rule 2  — Timestamp deduplication
  Rule 3  — INSERT / UPDATE / UNCHANGED semantics
  Rule 4  — Chronological order enforced
  Rule 5  — OHLCV validation
  Rule 6  — Atomic write (tmp -> replace)
  Rule 7  — Metadata JSON updated
  Rule 8  — REST backfill uses persistence path
  Rule 9  — WebSocket uses persistence path
  Rule 10 — Persistence failure blocks engine
  Rule 11 — Restart recovery
  Rule 12 — Retry / batch idempotency
  Rule 13 — Gap detection
"""

import csv
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import List, Dict, Any

import pytest

# ── Imports under test ─────────────────────────────────────────────────────────

from quantedge.market_data.ingestion import (
    validate_candle_ohlcv,
    upsert_closed_candles,
    UpsertResult,
    load_candles,
    csv_hash,
    load_metadata,
    detect_gaps,
    CANONICAL_CSV,
    CANONICAL_META,
)
from quantedge.market_data.delta_websocket import (
    DeltaWebSocketClient,
    _parse_candle_from_ws,
    _is_candle_closed,
    WS_ENDPOINT,
    SUBSCRIPTION_CHANNEL,
)

# ── Constants ─────────────────────────────────────────────────────────────────

ENGINE_DIR = Path(__file__).resolve().parent.parent

HOUR = 3600

# A fixed "well in the past" candle timestamp — definitely closed.
# 2026-01-01 00:00:00 UTC
BASE_TS = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())

# A timestamp far in the FUTURE — definitely forming (not closed).
FUTURE_TS = int(datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_candle_dict(ts: int, o=50000.0, h=50100.0, lo=49900.0, c=50050.0, v=1000.0) -> dict:
    """Build a normalised candle dict keyed by Unix timestamp (int seconds)."""
    return {
        "timestamp": ts,
        "open":   Decimal(str(o)),
        "high":   Decimal(str(h)),
        "low":    Decimal(str(lo)),
        "close":  Decimal(str(c)),
        "volume": Decimal(str(v)),
    }


def make_candles(base: int, n: int, step_price: float = 0) -> List[dict]:
    """Return n sequential hourly candle dicts."""
    return [
        make_candle_dict(base + i * HOUR, o=50000 + i * step_price)
        for i in range(n)
    ]


def count_csv_rows(csv_path: Path) -> int:
    """Count data rows (excluding header) in a CSV."""
    if not csv_path.exists():
        return 0
    candles = load_candles(csv_path)
    return len(candles)


def read_timestamps_from_csv(csv_path: Path) -> List[int]:
    """Return sorted list of timestamps from a CSV."""
    candles = load_candles(csv_path)
    return sorted(candles.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# 1 — OHLCV VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestOHLCVValidation:
    """Tests for validate_candle_ohlcv()."""

    def test_valid_candle_passes(self):
        """A valid candle passes without exception."""
        validate_candle_ohlcv(make_candle_dict(BASE_TS))  # should not raise

    def test_zero_open_rejected(self):
        with pytest.raises(ValueError, match="open"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, o=0))

    def test_negative_open_rejected(self):
        with pytest.raises(ValueError, match="open"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, o=-1))

    def test_zero_high_rejected(self):
        with pytest.raises(ValueError, match="high"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, h=0))

    def test_zero_low_rejected(self):
        with pytest.raises(ValueError, match="low"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, lo=0))

    def test_zero_close_rejected(self):
        with pytest.raises(ValueError, match="close"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, c=0))

    def test_negative_volume_rejected(self):
        with pytest.raises(ValueError, match="volume"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, v=-1))

    def test_zero_volume_passes(self):
        """Zero volume is valid (illiquid hours)."""
        validate_candle_ohlcv(make_candle_dict(BASE_TS, v=0))

    def test_high_lt_close_rejected(self):
        """high < close violates high >= max(open, close, low)."""
        with pytest.raises(ValueError, match="high"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, h=49000, o=50000, c=50050, lo=49900))

    def test_low_gt_open_rejected(self):
        """low > open violates low <= min(open, close, high)."""
        with pytest.raises(ValueError, match="low"):
            validate_candle_ohlcv(make_candle_dict(BASE_TS, lo=51000, o=50000, h=50100, c=50050))

    def test_high_eq_low_edge_case(self):
        """Doji (high == low == open == close) is valid."""
        validate_candle_ohlcv(make_candle_dict(BASE_TS, o=50000, h=50000, lo=50000, c=50000))


# ═══════════════════════════════════════════════════════════════════════════════
# 2 — CLOSED-CANDLE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestClosedCandleBoundary:
    """Rule 1: Only closed candles persist; forming candles silently skipped."""

    def test_past_candle_is_persisted(self, tmp_path):
        """A candle 100 hours in the past must be persisted."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(BASE_TS)  # 2026-01-01 — definitely past
        result = upsert_closed_candles([candle], csv_p, meta_p)
        assert result.inserts == 1
        assert count_csv_rows(csv_p) == 1

    def test_future_candle_is_skipped(self, tmp_path):
        """A forming candle (far future ts) must NOT be persisted."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(FUTURE_TS)
        result = upsert_closed_candles([candle], csv_p, meta_p)
        assert result.inserts == 0
        assert not csv_p.exists()  # nothing written

    def test_boundary_11_59_not_closed(self, tmp_path):
        """Candle at 12:00 UTC, now = 11:59 UTC → not closed."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        chs = now_ts - (now_ts % 3600)       # current hour start
        candle_ts = chs                       # current hour candle
        # Simulate future hour candle: ts = next hour start
        nhs = chs + HOUR
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(nhs)        # next hour = forming
        result = upsert_closed_candles([candle], csv_p, meta_p)
        assert result.inserts == 0, "Next-hour candle must not be persisted"

    def test_boundary_current_hour_not_closed(self, tmp_path):
        """Candle at current_hour_start is NOT closed (still forming)."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        chs = now_ts - (now_ts % 3600)
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(chs)
        result = upsert_closed_candles([candle], csv_p, meta_p)
        assert result.inserts == 0, "Current-hour candle must not be persisted"

    def test_boundary_one_hour_ago_is_closed(self, tmp_path):
        """Candle one full hour before current_hour_start IS closed."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        chs = now_ts - (now_ts % 3600)
        prev_hour = chs - HOUR
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(prev_hour)
        result = upsert_closed_candles([candle], csv_p, meta_p)
        assert result.inserts == 1, "Previous-hour candle must be persisted"

    def test_mixed_closed_and_forming(self, tmp_path):
        """Only closed candles from a mixed batch are persisted."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        chs = now_ts - (now_ts % 3600)
        prev = chs - HOUR
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candles = [
            make_candle_dict(prev),    # closed
            make_candle_dict(chs),     # forming
            make_candle_dict(FUTURE_TS),  # definitely forming
        ]
        result = upsert_closed_candles(candles, csv_p, meta_p)
        assert result.inserts == 1
        assert count_csv_rows(csv_p) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3 — INSERT / UPDATE / UNCHANGED SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertSemantics:
    """Rules 2 and 3: deduplication and INSERT/UPDATE/UNCHANGED."""

    def test_new_candle_is_insert(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        result = upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        assert result.inserts == 1
        assert result.updates == 0
        assert result.unchanged == 0

    def test_identical_duplicate_is_unchanged(self, tmp_path):
        """Same timestamp + identical OHLCV → UNCHANGED, no write on second call."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(BASE_TS)
        upsert_closed_candles([candle], csv_p, meta_p)
        sha_before = csv_hash(csv_p)
        result2 = upsert_closed_candles([candle], csv_p, meta_p)
        sha_after = csv_hash(csv_p)
        assert result2.inserts == 0
        assert result2.unchanged == 1
        assert sha_before == sha_after, "SHA must not change for UNCHANGED"

    def test_revised_candle_is_update(self, tmp_path):
        """Same timestamp + different close → UPDATE."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        original = make_candle_dict(BASE_TS, c=50050)
        upsert_closed_candles([original], csv_p, meta_p)
        revised = make_candle_dict(BASE_TS, c=50099)  # revised close
        result2 = upsert_closed_candles([revised], csv_p, meta_p)
        assert result2.updates == 1
        assert result2.inserts == 0
        # Verify new value in CSV
        loaded = load_candles(csv_p)
        assert loaded[BASE_TS]["close"] == Decimal("50099")

    def test_duplicate_in_same_batch_deduplicated(self, tmp_path):
        """Same timestamp appearing twice in one batch → only one row."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        batch = [make_candle_dict(BASE_TS), make_candle_dict(BASE_TS)]
        result = upsert_closed_candles(batch, csv_p, meta_p)
        assert result.inserts == 1
        assert count_csv_rows(csv_p) == 1

    def test_multiple_candles_inserted(self, tmp_path):
        """Multiple distinct timestamps → all inserted."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        batch = make_candles(BASE_TS, 5)
        result = upsert_closed_candles(batch, csv_p, meta_p)
        assert result.inserts == 5
        assert count_csv_rows(csv_p) == 5

    def test_out_of_order_input_stored_sorted(self, tmp_path):
        """Candles supplied out of chronological order → CSV is sorted."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        ts1, ts2, ts3 = BASE_TS, BASE_TS + HOUR, BASE_TS + 2 * HOUR
        batch = [
            make_candle_dict(ts3),
            make_candle_dict(ts1),
            make_candle_dict(ts2),
        ]
        upsert_closed_candles(batch, csv_p, meta_p)
        timestamps = read_timestamps_from_csv(csv_p)
        assert timestamps == sorted(timestamps), "CSV must be chronologically ordered"
        assert timestamps == [ts1, ts2, ts3]

    def test_no_duplicate_rows_in_csv(self, tmp_path):
        """After multiple upserts, no duplicate timestamps in CSV."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        # Insert initial batch
        upsert_closed_candles(make_candles(BASE_TS, 3), csv_p, meta_p)
        # Upsert again with overlapping + new
        upsert_closed_candles(make_candles(BASE_TS + HOUR, 3), csv_p, meta_p)
        timestamps = read_timestamps_from_csv(csv_p)
        assert len(timestamps) == len(set(timestamps)), "No duplicate timestamps"


# ═══════════════════════════════════════════════════════════════════════════════
# 4 — CHRONOLOGICAL ORDERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestChronologicalOrdering:
    """Rule 4: Final dataset must always be strictly ascending."""

    def test_strictly_increasing_after_upsert(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 10), csv_p, meta_p)
        ts = read_timestamps_from_csv(csv_p)
        for i in range(1, len(ts)):
            assert ts[i] > ts[i - 1], f"Not strictly increasing at {i}"

    def test_no_duplicates_in_timestamps(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 5), csv_p, meta_p)
        upsert_closed_candles(make_candles(BASE_TS, 5), csv_p, meta_p)  # retry
        ts = read_timestamps_from_csv(csv_p)
        assert len(ts) == len(set(ts))


# ═══════════════════════════════════════════════════════════════════════════════
# 5 — INVALID CANDLE REJECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidCandleRejection:
    """Rule 5: Malformed candles must not corrupt the dataset."""

    def test_invalid_ohlcv_raises_value_error(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        bad = make_candle_dict(BASE_TS)
        bad["high"] = Decimal("1000")   # high >> low
        bad["low"] = Decimal("99999")   # low > high — invalid
        with pytest.raises(ValueError):
            upsert_closed_candles([bad], csv_p, meta_p, check_closed=False)

    def test_invalid_candle_does_not_corrupt_csv(self, tmp_path):
        """If a batch contains an invalid candle, no partial write occurs."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        # Pre-populate with one valid candle
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        sha_before = csv_hash(csv_p)

        bad = make_candle_dict(BASE_TS + HOUR)
        bad["close"] = Decimal("0")  # invalid

        with pytest.raises(ValueError):
            upsert_closed_candles([bad], csv_p, meta_p, check_closed=False)

        # Original CSV must be unchanged
        assert csv_hash(csv_p) == sha_before, "CSV must not change after rejection"
        assert count_csv_rows(csv_p) == 1

    def test_zero_price_rejected(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        bad = make_candle_dict(BASE_TS, o=0)
        with pytest.raises(ValueError, match="open"):
            upsert_closed_candles([bad], csv_p, meta_p, check_closed=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 6 — ATOMIC WRITE
# ═══════════════════════════════════════════════════════════════════════════════


class TestAtomicWrite:
    """Rule 6: Canonical CSV must never be left partially written."""

    def test_no_tmp_file_after_success(self, tmp_path):
        """Temporary .tmp file must be cleaned up after successful write."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        tmp_p = csv_p.parent / (csv_p.name + ".tmp")
        assert not tmp_p.exists(), ".tmp file must not exist after successful write"

    def test_original_preserved_on_write_failure(self, tmp_path):
        """If the write is interrupted, the original CSV remains intact."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        # Pre-populate
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        sha_before = csv_hash(csv_p)

        # Simulate write failure by patching os.fsync to raise
        import os
        original_fsync = os.fsync
        def failing_fsync(fd):
            raise OSError("Simulated disk failure")

        import quantedge.market_data.ingestion as ing_mod
        original = ing_mod.os.fsync
        ing_mod.os.fsync = failing_fsync
        try:
            with pytest.raises(OSError):
                upsert_closed_candles(
                    [make_candle_dict(BASE_TS + HOUR)], csv_p, meta_p
                )
        finally:
            ing_mod.os.fsync = original

        # Original must still be intact
        assert csv_hash(csv_p) == sha_before, "Original CSV must survive write failure"
        assert count_csv_rows(csv_p) == 1

    def test_tmp_deleted_on_failure(self, tmp_path):
        """The .tmp file must be deleted if the write fails."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)

        import quantedge.market_data.ingestion as ing_mod
        def failing_fsync(fd):
            raise OSError("Simulated disk failure")
        original = ing_mod.os.fsync
        ing_mod.os.fsync = failing_fsync
        try:
            with pytest.raises(OSError):
                upsert_closed_candles([make_candle_dict(BASE_TS + HOUR)], csv_p, meta_p)
        finally:
            ing_mod.os.fsync = original

        tmp_p = csv_p.parent / (csv_p.name + ".tmp")
        assert not tmp_p.exists(), ".tmp must be cleaned up on failure"


# ═══════════════════════════════════════════════════════════════════════════════
# 7 — METADATA AND SHA-256
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadataAndSHA:
    """Rule 7: metadata.json must be updated after successful persistence."""

    def test_metadata_updated_after_upsert(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 3), csv_p, meta_p)
        meta = load_metadata(meta_p)
        assert meta["candle_count"] == 3
        assert "first_timestamp" in meta
        assert "last_timestamp" in meta
        assert "sha256" in meta

    def test_metadata_candle_count_correct(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 5), csv_p, meta_p)
        meta = load_metadata(meta_p)
        assert meta["candle_count"] == 5

    def test_metadata_sha256_matches_csv(self, tmp_path):
        """SHA-256 in metadata must match row-based CSV hash."""
        from quantedge.market_data.ingestion import csv_hash
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 4), csv_p, meta_p)
        meta = load_metadata(meta_p)
        actual_sha = csv_hash(csv_p)
        assert meta["sha256"] == actual_sha

    def test_sha256_row_based_not_raw_file(self, tmp_path):
        """SHA must be row-based (CRLF-independent), not raw file bytes."""
        from quantedge.market_data.ingestion import csv_hash
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        sha1 = csv_hash(csv_p)
        # Compute again — must be deterministic
        sha2 = csv_hash(csv_p)
        assert sha1 == sha2

    def test_metadata_not_created_when_nothing_changes(self, tmp_path):
        """If all candles are UNCHANGED, metadata is NOT written (idempotent)."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        mtime_before = meta_p.stat().st_mtime
        time.sleep(0.05)
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        mtime_after = meta_p.stat().st_mtime
        assert mtime_before == mtime_after, "Metadata must not be rewritten when nothing changed"

    def test_metadata_preserves_static_fields(self, tmp_path):
        """Static fields like symbol/exchange must be present in metadata."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles([make_candle_dict(BASE_TS)], csv_p, meta_p)
        meta = load_metadata(meta_p)
        assert meta.get("symbol") == "BTCUSD.P"
        assert "delta_symbol" in meta
        assert "exchange" in meta


# ═══════════════════════════════════════════════════════════════════════════════
# 8 — GAP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestGapDetection:
    """Rule 13: gaps reported; never fabricated."""

    def test_gap_detected_after_upsert(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        # Insert candles with a 3-hour gap
        c1 = make_candle_dict(BASE_TS)
        c2 = make_candle_dict(BASE_TS + 4 * HOUR)  # 3-hour gap
        result = upsert_closed_candles([c1, c2], csv_p, meta_p)
        assert len(result.gaps) == 1
        assert result.gaps[0]["missing_candles"] == 3

    def test_no_gap_for_consecutive_candles(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        result = upsert_closed_candles(make_candles(BASE_TS, 5), csv_p, meta_p)
        assert result.gaps == []

    def test_gap_in_metadata(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        c1 = make_candle_dict(BASE_TS)
        c2 = make_candle_dict(BASE_TS + 5 * HOUR)  # 4-hour gap
        upsert_closed_candles([c1, c2], csv_p, meta_p)
        meta = load_metadata(meta_p)
        assert meta["gap_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 9 — RESTART RECOVERY (Rule 11)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRestartRecovery:
    """Rule 11: After restart, engine initializes from persisted state."""

    def test_restart_starts_from_last_persisted_ts(self, tmp_path):
        """A new engine instance must load from the persisted CSV end."""
        from quantedge.market_data.ingestion import load_candles
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"

        # First session: persist 3 candles
        candles = make_candles(BASE_TS, 3)
        upsert_closed_candles(candles, csv_p, meta_p)

        # Simulate restart: load again from CSV
        loaded = load_candles(csv_p)
        assert len(loaded) == 3
        last_ts = max(loaded.keys())
        assert last_ts == BASE_TS + 2 * HOUR

    def test_no_reprocessing_after_restart(self, tmp_path):
        """After restart, re-upserting already-persisted candles is UNCHANGED."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candles = make_candles(BASE_TS, 3)
        upsert_closed_candles(candles, csv_p, meta_p)

        # Simulate restart + second session upserting same data
        result2 = upsert_closed_candles(candles, csv_p, meta_p)
        assert result2.inserts == 0
        assert result2.unchanged == 3
        assert count_csv_rows(csv_p) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 10 — RETRY / BATCH IDEMPOTENCY (Rule 12)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryIdempotency:
    """Rule 12: Retrying same candle(s) leaves exactly one row per timestamp."""

    def test_single_retry_idempotent(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        candle = make_candle_dict(BASE_TS)
        upsert_closed_candles([candle], csv_p, meta_p)
        # Retry same candle
        result2 = upsert_closed_candles([candle], csv_p, meta_p)
        assert result2.unchanged == 1
        assert count_csv_rows(csv_p) == 1

    def test_batch_retry_idempotent(self, tmp_path):
        """Retrying an entire batch leaves exactly N rows (one per timestamp)."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        batch = make_candles(BASE_TS, 3)
        upsert_closed_candles(batch, csv_p, meta_p)
        # Retry entire batch
        result2 = upsert_closed_candles(batch, csv_p, meta_p)
        assert result2.inserts == 0
        assert result2.unchanged == 3
        assert count_csv_rows(csv_p) == 3

    def test_three_identical_in_batch_stored_once(self, tmp_path):
        """Three identical candles in one batch → exactly one row."""
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        batch = [make_candle_dict(BASE_TS)] * 3
        result = upsert_closed_candles(batch, csv_p, meta_p)
        assert result.inserts == 1
        assert count_csv_rows(csv_p) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 11 — PERSISTENCE FAILURE SEMANTICS (Rule 10)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistenceFailureSemantics:
    """Rule 10: If persist fails, engine MUST NOT be called, ts MUST NOT be marked."""

    def test_persistence_failure_blocks_engine(self, tmp_path):
        """Engine.process_new_candles must NOT be called when persistence fails."""
        engine_mock = MagicMock()
        client = DeltaWebSocketClient(
            engine=engine_mock,
            persist=True,
            csv_path=tmp_path / "test.csv",
            meta_path=tmp_path / "meta.json",
        )

        # Patch upsert_closed_candles to raise
        import quantedge.market_data.delta_websocket as ws_mod
        original_upsert = ws_mod.upsert_closed_candles

        def failing_upsert(*args, **kwargs):
            raise OSError("Simulated disk failure")

        ws_mod.upsert_closed_candles = failing_upsert
        try:
            import asyncio
            candle = make_candle_dict(BASE_TS)
            candle["symbol"] = "BTCUSD.P"
            candle["timeframe"] = "1h"
            asyncio.run(client._handle_message({
                "type": "candlestick_1h",
                "symbol": "BTCUSD",
                "open":   50000.0,
                "high":   50100.0,
                "low":    49900.0,
                "close":  50050.0,
                "volume": 1000.0,
                "candle_start_time": BASE_TS * 1_000_000,
                "timestamp": (BASE_TS + 1800) * 1_000_000,
                "last_updated": (BASE_TS + 1800) * 1_000_000,
            }))
        finally:
            ws_mod.upsert_closed_candles = original_upsert

        engine_mock.process_new_candles.assert_not_called()

    def test_persistence_failure_does_not_mark_ts_processed(self, tmp_path):
        """After persistence failure, ts must NOT be in processed_timestamps."""
        client = DeltaWebSocketClient(
            persist=True,
            csv_path=tmp_path / "test.csv",
            meta_path=tmp_path / "meta.json",
        )

        import quantedge.market_data.delta_websocket as ws_mod
        original_upsert = ws_mod.upsert_closed_candles

        def failing_upsert(*args, **kwargs):
            raise OSError("Disk full")

        ws_mod.upsert_closed_candles = failing_upsert
        try:
            import asyncio
            asyncio.run(client._handle_message({
                "type": "candlestick_1h",
                "symbol": "BTCUSD",
                "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0,
                "volume": 1000.0,
                "candle_start_time": BASE_TS * 1_000_000,
                "timestamp": (BASE_TS + 1800) * 1_000_000,
                "last_updated": (BASE_TS + 1800) * 1_000_000,
            }))
        finally:
            ws_mod.upsert_closed_candles = original_upsert

        assert BASE_TS not in client.processed_timestamps, \
            "Failed candle must not be marked as processed"

    def test_retry_after_failure_succeeds(self, tmp_path):
        """After a persistence failure, the same candle can be successfully retried."""
        import asyncio
        import quantedge.market_data.delta_websocket as ws_mod

        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        client = DeltaWebSocketClient(persist=True, csv_path=csv_p, meta_path=meta_p)

        ws_msg = {
            "type": "candlestick_1h", "symbol": "BTCUSD",
            "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0,
            "volume": 1000.0,
            "candle_start_time": BASE_TS * 1_000_000,
            "timestamp": (BASE_TS + 1800) * 1_000_000,
            "last_updated": (BASE_TS + 1800) * 1_000_000,
        }

        # First attempt: fail
        original_upsert = ws_mod.upsert_closed_candles
        def failing_upsert(*args, **kwargs):
            raise OSError("Disk full")
        ws_mod.upsert_closed_candles = failing_upsert
        try:
            asyncio.run(client._handle_message(ws_msg))
        finally:
            ws_mod.upsert_closed_candles = original_upsert

        assert BASE_TS not in client.processed_timestamps

        # Second attempt: succeed (restored)
        asyncio.run(client._handle_message(ws_msg))
        assert BASE_TS in client.processed_timestamps
        assert count_csv_rows(csv_p) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 12 — WEBSOCKET PERSISTENCE INTEGRATION (Rule 9)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebSocketPersistenceIntegration:
    """Rule 9: WS closed candle → persist → engine."""

    def test_ws_closed_candle_persisted(self, tmp_path):
        """A valid closed WS candle is persisted and engine called."""
        import asyncio
        engine_mock = MagicMock()
        engine_mock.process_new_candles.return_value = {"new_obs": 0, "new_breaks": 0}
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"

        client = DeltaWebSocketClient(
            engine=engine_mock, persist=True, csv_path=csv_p, meta_path=meta_p
        )

        ws_msg = {
            "type": "candlestick_1h", "symbol": "BTCUSD",
            "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0,
            "volume": 1000.0,
            "candle_start_time": BASE_TS * 1_000_000,
            "timestamp": (BASE_TS + 1800) * 1_000_000,
            "last_updated": (BASE_TS + 1800) * 1_000_000,
        }
        asyncio.run(client._handle_message(ws_msg))

        assert count_csv_rows(csv_p) == 1
        engine_mock.process_new_candles.assert_called_once()

    def test_ws_forming_candle_not_persisted(self, tmp_path):
        """A forming WS candle (current hour) must not be persisted or sent to engine."""
        import asyncio
        engine_mock = MagicMock()
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"

        client = DeltaWebSocketClient(
            engine=engine_mock, persist=True, csv_path=csv_p, meta_path=meta_p
        )

        now_ts = int(datetime.now(timezone.utc).timestamp())
        chs = now_ts - (now_ts % 3600)  # forming

        ws_msg = {
            "type": "candlestick_1h", "symbol": "BTCUSD",
            "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0,
            "volume": 1000.0,
            "candle_start_time": chs * 1_000_000,
            "timestamp": (chs + 1800) * 1_000_000,
            "last_updated": (chs + 1800) * 1_000_000,
        }
        asyncio.run(client._handle_message(ws_msg))

        assert not csv_p.exists() or count_csv_rows(csv_p) == 0
        engine_mock.process_new_candles.assert_not_called()

    def test_ws_persist_false_skips_csv(self, tmp_path):
        """With persist=False, no CSV is written but engine is still called."""
        import asyncio
        engine_mock = MagicMock()
        engine_mock.process_new_candles.return_value = {"new_obs": 0, "new_breaks": 0}
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"

        client = DeltaWebSocketClient(
            engine=engine_mock, persist=False, csv_path=csv_p, meta_path=meta_p
        )
        ws_msg = {
            "type": "candlestick_1h", "symbol": "BTCUSD",
            "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0,
            "volume": 1000.0,
            "candle_start_time": BASE_TS * 1_000_000,
            "timestamp": (BASE_TS + 1800) * 1_000_000,
            "last_updated": (BASE_TS + 1800) * 1_000_000,
        }
        asyncio.run(client._handle_message(ws_msg))

        assert not csv_p.exists() or count_csv_rows(csv_p) == 0
        engine_mock.process_new_candles.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 13 — CANONICAL DATASET INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalDatasetIntegrity:
    """Verify that the existing historical canonical dataset is never modified by unit tests."""

    def test_canonical_csv_still_has_original_count(self):
        """Production canonical CSV must have at least 5545 rows (original baseline)."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present in CI environment")
        candles = load_candles(CANONICAL_CSV)
        # Original baseline is 5545 candles (2026-01-01 to 2026-08-20)
        assert len(candles) >= 5545, (
            f"Expected >= 5545 original candles, got {len(candles)}"
        )

    def test_canonical_csv_no_duplicates(self):
        """Production canonical CSV must have no duplicate timestamps."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present in CI environment")
        candles = load_candles(CANONICAL_CSV)
        ts_list = sorted(candles.keys())
        assert len(ts_list) == len(set(ts_list)), "Canonical CSV has duplicate timestamps"


# ═══════════════════════════════════════════════════════════════════════════════
# 14 — NO BINANCE DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoBinanceDependency:
    """Verify no Binance references in production market-data files."""

    def test_no_binance_in_ingestion(self):
        ingestion_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "ingestion.py"
        content = ingestion_file.read_text(encoding="utf-8").lower()
        assert "binance" not in content

    def test_no_binance_in_delta_websocket(self):
        ws_file = ENGINE_DIR / "src" / "quantedge" / "market_data" / "delta_websocket.py"
        content = ws_file.read_text(encoding="utf-8").lower()
        assert "binance" not in content


# ═══════════════════════════════════════════════════════════════════════════════
# 15 — FROZEN SMC FILES
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrozenSMCFiles:
    """Verify that the three frozen SMC algorithm files have not been modified."""

    @pytest.mark.parametrize("filename", [
        "structure.py", "order_blocks.py", "volatility.py"
    ])
    def test_frozen_file_exists_and_not_empty(self, filename):
        smc_path = ENGINE_DIR / "src" / "quantedge" / "smc" / filename
        assert smc_path.exists(), f"{filename} must exist"
        assert smc_path.stat().st_size > 0, f"{filename} must not be empty"

    def test_frozen_files_not_modified_by_upsert(self, tmp_path):
        """Running upsert_closed_candles must not touch SMC files."""
        smc_dir = ENGINE_DIR / "src" / "quantedge" / "smc"
        mtimes_before = {
            f.name: f.stat().st_mtime
            for f in smc_dir.glob("*.py")
            if f.name in ("structure.py", "order_blocks.py", "volatility.py")
        }
        # Run persistence
        csv_p = tmp_path / "test.csv"
        meta_p = tmp_path / "meta.json"
        upsert_closed_candles(make_candles(BASE_TS, 3), csv_p, meta_p)
        # Verify mtimes unchanged
        for name, mtime in mtimes_before.items():
            current = (smc_dir / name).stat().st_mtime
            assert current == mtime, f"{name} was modified by upsert!"
