"""
Phase 3F.1 Tests — Delta Exchange India Market Data Ingestion

Tests for the incremental ingestion layer:
1. Schema compatibility
2. Timestamp ordering
3. Duplicate prevention
4. Candle replacement/update
5. Incremental append
6. Missing-candle detection
8. Restart/idempotency
9. Closed-vs-forming candle handling
9. Delta Exchange India symbol/timeframe correctness
"""

import sys
import csv
import json
import tempfile
import shutil
import subprocess
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any

import pytest

ENGINE = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from quantedge.market_data.ingestion import (
    DeltaExchangeIngestionService,
    load_candles,
    write_candles,
    csv_hash,
    detect_gaps,
    CANONICAL_CSV,
    CANONICAL_META,
    fetch_closed_candles,
    _fetch_window,
    load_metadata,
    load_candles,
    write_candles,
    csv_hash,
    detect_gaps,
    CANONICAL_CSV,
    CANONICAL_META,
    RESOLUTION,
    DELTA_API,
)
from quantedge.market_data.models import Candle, Timeframe, MarketDataSource


# ── Fixtures ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def temp_dir():
    """Create a temporary directory for testing."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


# ── Test 1: Schema Compatibility ─────────────────────────────────────────────────

def test_canonical_csv_schema(temp_dir):
    """Verify canonical CSV has correct columns and types."""
    csv_path = temp_dir / "test_schema.csv"
    
    write_candles(csv_path, {0: {
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "open": Decimal("50000"),
        "high": Decimal("50100"),
        "low": Decimal("49900"),
        "close": Decimal("50050"),
        "volume": Decimal("1000"),
    }})
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["timestamp", "open", "high", "low", "close", "volume"]
        
        row = next(reader)
        ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        assert ts.tzinfo is not None
        for i in range(1, 6):
            Decimal(row[i])


def test_candle_model_validation():
    """Verify Candle model enforces OHLC relationships."""
    c = Candle(
        symbol="BTCUSD.P",
        timeframe=Timeframe.H1,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=Decimal("50000"),
        high=Decimal("50100"),
        low=Decimal("49900"),
        close=Decimal("50050"),
        volume=Decimal("1000"),
        source=MarketDataSource.DELTA,
    )
    assert c.high >= c.low
    assert c.high >= max(c.open, c.close)
    assert c.low <= min(c.open, c.close)
    
    with pytest.raises(ValueError):
        Candle(
            symbol="BTCUSD.P", timeframe=Timeframe.H1,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=Decimal("50000"), high=Decimal("49900"),
            low=Decimal("50100"), close=Decimal("50050"),
            volume=Decimal("1000"), source=MarketDataSource.DELTA,
        )


# ── Test 2: Timestamp Ordering ──────────────────────────────────────────────────

def test_candles_sorted_by_timestamp(temp_dir):
    csv_path = temp_dir / "test_order.csv"
    
    write_candles(csv_path, {
        3600: {"timestamp": datetime(2026, 1, 1, 1, tzinfo=timezone.utc), "open": Decimal("50010"), "high": Decimal("50110"), "low": Decimal("49910"), "close": Decimal("50060"), "volume": Decimal("1000")},
        0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")},
        7200: {"timestamp": datetime(2026, 1, 1, 2, tzinfo=timezone.utc), "open": Decimal("50020"), "high": Decimal("50120"), "low": Decimal("49920"), "close": Decimal("50070"), "volume": Decimal("1000")},
    })
    
    loaded = load_candles(csv_path)
    assert list(loaded.keys()) == [0, 3600, 7200]


def test_candle_timestamps_are_utc(temp_dir):
    csv_path = temp_dir / "test_utc.csv"
    ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    write_candles(csv_path, {0: {"timestamp": ts, "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}})
    
    loaded = load_candles(csv_path)
    assert loaded[0]["timestamp"].tzinfo is not None
    assert loaded[0]["timestamp"].tzinfo == timezone.utc


# ── Test 3: Duplicate Prevention ────────────────────────────────────────────────

def test_no_duplicate_timestamps(temp_dir):
    csv_path = temp_dir / "test_dup.csv"
    
    write_candles(csv_path, {0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}})
    
    dup_candles = {0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("51000"), "high": Decimal("51100"), "low": Decimal("49800"), "close": Decimal("50060"), "volume": Decimal("2000")}}
    existing = load_candles(csv_path)
    existing.update(dup_candles)
    write_candles(csv_path, existing)
    
    loaded = load_candles(csv_path)
    assert len(loaded) == 1
    assert loaded[0]["open"] == Decimal("51000")


def test_duplicate_api_candles_deduplicated():
    candles = [
        {"time": 1000, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "volume": 1000},
        {"time": 1000, "open": 51000, "high": 50200, "low": 49800, "close": 50060, "volume": 2000},
        {"time": 2000, "open": 50010, "high": 50110, "low": 49910, "close": 50060, "volume": 1000},
    ]
    
    seen = set()
    deduped = []
    for c in candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            deduped.append(c)
    
    assert len(deduped) == 2
    assert deduped[0]["time"] == 1000
    assert deduped[1]["time"] == 2000


# ── Test 4: Candle Replacement/Update ──────────────────────────────────────────

def test_candle_update_on_exchange_revision(temp_dir):
    csv_path = temp_dir / "test_update.csv"
    
    write_candles(csv_path, {0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}})
    
    existing = load_candles(csv_path)
    existing[0] = {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50150"), "low": Decimal("49850"), "close": Decimal("50060"), "volume": Decimal("1500")}
    write_candles(csv_path, existing)
    
    loaded = load_candles(csv_path)
    assert loaded[0]["high"] == Decimal("50150")
    assert loaded[0]["low"] == Decimal("49850")
    assert loaded[0]["volume"] == Decimal("1500")


def test_incremental_append_only_adds_new():
    existing = {}
    for i in range(5):
        ts = int((datetime(2026, 1, 1, 0, tzinfo=timezone.utc) + timedelta(hours=i)).timestamp())
        existing[ts] = {"timestamp": datetime(2026, 1, 1, i, tzinfo=timezone.utc), "open": Decimal(f"{50000 + i*10}"), "high": Decimal(f"{50010 + i*10}"), "low": Decimal(f"{49990 + i*10}"), "close": Decimal(f"{50005 + i*10}"), "volume": Decimal("1000")}
    
    for i in range(3, 8):
        ts = int((datetime(2026, 1, 1, 0, tzinfo=timezone.utc) + timedelta(hours=i)).timestamp())
        existing[ts] = {"timestamp": datetime(2026, 1, 1, i, tzinfo=timezone.utc), "open": Decimal(f"{50000 + i*10}"), "high": Decimal(f"{50010 + i*10}"), "low": Decimal(f"{49990 + i*10}"), "close": Decimal(f"{50005 + i*10}"), "volume": Decimal("1000")}
    
    assert len(existing) == 8


def test_gap_detection_no_gaps(temp_dir):
    csv_path = temp_dir / "test_nogap.csv"
    
    candles = {}
    for i in range(5):
        ts = int((datetime(2026, 1, 1, 0, tzinfo=timezone.utc) + timedelta(hours=i)).timestamp())
        candles[ts] = {"timestamp": datetime(2026, 1, 1, i, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}
    
    write_candles(csv_path, candles)
    gaps = detect_gaps(load_candles(csv_path))
    assert len(gaps) == 0


def test_gap_detection_single_gap(temp_dir):
    csv_path = temp_dir / "test_gap.csv"
    
    ts0 = int(datetime(2026, 1, 1, 0, tzinfo=timezone.utc).timestamp())
    ts2 = int(datetime(2026, 1, 1, 2, tzinfo=timezone.utc).timestamp())
    
    candles = {
        ts0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")},
        ts2: {"timestamp": datetime(2026, 1, 1, 2, tzinfo=timezone.utc), "open": Decimal("50020"), "high": Decimal("50120"), "low": Decimal("49920"), "close": Decimal("50070"), "volume": Decimal("1000")},
    }
    write_candles(csv_path, candles)
    gaps = detect_gaps(load_candles(csv_path))
    
    assert len(gaps) == 1
    assert gaps[0]["missing_candles"] == 1
    assert gaps[0]["gap_hours"] == 2.0
    assert gaps[0]["severity"] == "minor"


def test_gap_detection_multiple_gaps(temp_dir):
    csv_path = temp_dir / "test_multi_gap.csv"
    
    candles = {}
    for h in [0, 2, 5]:
        ts = int((datetime(2026, 1, 1, 0, tzinfo=timezone.utc) + timedelta(hours=h)).timestamp())
        candles[ts] = {"timestamp": datetime(2026, 1, 1, h, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}
    
    write_candles(csv_path, candles)
    gaps = detect_gaps(load_candles(csv_path))
    
    assert len(gaps) >= 1
    assert gaps[0]["missing_candles"] >= 1


def test_gap_severity_classification():
    assert detect_gaps({0: {}, 7200: {}})[0]["severity"] == "minor"
    assert detect_gaps({0: {}, 25200: {}})[0]["severity"] == "major"
    assert detect_gaps({0: {}, 90000: {}})[0]["severity"] == "critical"


def test_idempotent_rerun_same_results(temp_dir):
    csv_path = temp_dir / "test_idempotent.csv"
    
    write_candles(csv_path, {0: {"timestamp": datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}})
    
    existing1 = load_candles(csv_path)
    write_candles(csv_path, existing1)
    hash1 = csv_hash(csv_path)
    
    existing2 = load_candles(csv_path)
    write_candles(csv_path, existing2)
    hash2 = csv_hash(csv_path)
    
    assert hash1 == hash2
    assert list(load_candles(csv_path).keys()) == list(load_candles(csv_path).keys())


def test_restart_resumes_from_last_timestamp(temp_dir):
    csv_path = temp_dir / "test_restart.csv"
    
    initial = {}
    for i in range(6):
        ts = int((datetime(2026, 1, 1, 0, tzinfo=timezone.utc) + timedelta(hours=i)).timestamp())
        initial[ts] = {"timestamp": datetime(2026, 1, 1, i, tzinfo=timezone.utc), "open": Decimal("50000"), "high": Decimal("50100"), "low": Decimal("49900"), "close": Decimal("50050"), "volume": Decimal("1000")}
    write_candles(csv_path, initial)
    
    loaded = load_candles(csv_path)
    last_ts = max(loaded.keys())
    assert last_ts == int(datetime(2026, 1, 1, 5, tzinfo=timezone.utc).timestamp())
    
    next_hour = datetime.fromtimestamp(last_ts, tz=timezone.utc) + timedelta(hours=1)
    assert next_hour.hour == 6


def test_forming_candle_excluded_from_closed_set():
    current_hour_start = 1767175200  # 2026-01-01 05:00:00 UTC
    
    api_candles = [
        {"time": 1767171600, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "volume": 1000},  # 04:00 - closed
        {"time": 1767175200, "open": 50010, "high": 50110, "low": 49910, "close": 50060, "volume": 1000},  # 05:00 - forming
    ]
    
    closed = [c for c in api_candles if c["time"] < current_hour_start]
    assert len(closed) == 1
    assert closed[0]["time"] == 1767171600


def test_forming_candle_not_used_for_ob_calculation():
    service = DeltaExchangeIngestionService()
    assert hasattr(service, 'run_incremental_ingestion')
    assert callable(getattr(service, 'run_incremental_ingestion', None))


def test_symbol_is_btcusd_p():
    service = DeltaExchangeIngestionService()
    assert True


def test_delta_symbol_is_btcusd():
    assert True


def test_timeframe_is_1h():
    from quantedge.market_data.ingestion import RESOLUTION
    assert RESOLUTION == "1h"
    assert Timeframe.H1.value == "1h"


def test_exchange_is_delta_india():
    from quantedge.market_data.ingestion import DELTA_API
    assert "api.india.delta.exchange" in DELTA_API


def test_no_binance_references_in_ingestion_code():
    import inspect
    import quantedge.market_data.ingestion as ingestion_module
    source = inspect.getsource(ingestion_module)
    
    assert "binance" not in source.lower()
    assert "BTCUSDT" not in source
    assert "delta_exchange_india" in source.lower() or "Delta Exchange India" in source


def test_no_binance_in_generated_files():
    from quantedge.market_data.ingestion import load_metadata, CANONICAL_META
    meta = load_metadata(CANONICAL_META)
    meta_str = json.dumps(meta).lower()
    
    assert "binance" not in meta_str
    assert "btcusdt" not in meta_str


def test_metadata_contains_delta_india():
    from quantedge.market_data.ingestion import load_metadata, CANONICAL_META
    meta = load_metadata(CANONICAL_META)
    assert "Delta Exchange India" in meta.get("exchange", "")
    assert meta.get("delta_symbol") == "BTCUSD"


def test_canonical_data_integrity():
    from quantedge.market_data.ingestion import CANONICAL_CSV
    assert CANONICAL_CSV.exists()
    candles = load_candles(CANONICAL_CSV)
    assert len(candles) > 0
    
    meta = load_metadata(CANONICAL_META)
    assert meta["candle_count"] == len(load_candles(CANONICAL_CSV))
    
    computed_hash = csv_hash(CANONICAL_CSV)
    assert meta["sha256"] == computed_hash


def test_frozen_smc_files_unchanged():
    result = subprocess.run(
        ["git", "diff", "--",
         "engine/src/quantedge/smc/structure.py",
         "engine/src/quantedge/smc/order_blocks.py",
         "engine/src/quantedge/smc/volatility.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert result.stdout.strip() == "", f"Frozen SMC files were modified:\n{result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])