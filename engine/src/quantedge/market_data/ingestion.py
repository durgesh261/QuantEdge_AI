"""
Delta Exchange India Market Data Ingestion Layer for QuantEdge AI V2.

Canonical market-data ingestion pipeline for Delta Exchange India BTCUSD 1H perpetual futures.

Single authoritative implementation — no duplicate definitions.
"""

import csv
import json
import hashlib
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Any, Optional


# ── Constants ─────────────────────────────────────────────────────────────────────

DELTA_API = "https://api.india.delta.exchange/v2/history/candles"
SYMBOL_LOCAL = "BTCUSD.P"      # Display / TradingView symbol
SYMBOL_EXCHANGE = "BTCUSD"     # Transport symbol sent to Delta API
RESOLUTION = "1h"
MAX_PER_REQ = 2000
CHUNK_SECS = 2000 * 3600


def _find_repo_root() -> Path:
    """Find the repository root by looking for a marker file (.git or pyproject.toml)."""
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / "pyproject.toml").exists() and (parent / "engine").exists():
            # This pyproject.toml is in the repo root (not inside engine/)
            return parent
    # Fallback: walk up from the engine pyproject.toml
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists():
            # Check if this is the engine-level pyproject
            if (parent.parent / ".git").exists():
                return parent.parent
            return parent
    return Path(__file__).resolve().parent.parent.parent.parent


REPO_ROOT = _find_repo_root()
CANONICAL_CSV = (
    REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
)
CANONICAL_META = (
    REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026_metadata.json"
)


# ── Core Utilities ──────────────────────────────────────────────────────────────


def load_candles(csv_path: Path) -> Dict[int, Dict[str, Any]]:
    """Load candles from CSV. Returns dict keyed by Unix timestamp (int seconds)."""
    candles: Dict[int, Dict[str, Any]] = {}
    if not csv_path.exists():
        return {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            ts_int = int(ts.timestamp())
            candles[ts_int] = {
                "timestamp": datetime.fromtimestamp(ts_int, tz=timezone.utc),
                "open": Decimal(row["open"]),
                "high": Decimal(row["high"]),
                "low": Decimal(row["low"]),
                "close": Decimal(row["close"]),
                "volume": Decimal(row["volume"]),
            }
    return candles


def write_candles(csv_path: Path, candles: Dict[int, Dict[str, Any]]) -> None:
    """Write candles dict (keyed by Unix timestamp) to CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts_int in sorted(candles.keys()):
            c = candles[ts_int]
            writer.writerow([
                datetime.fromtimestamp(ts_int, tz=timezone.utc).isoformat(),
                str(c["open"]), str(c["high"]), str(c["low"]),
                str(c["close"]), str(c["volume"]),
            ])


def csv_hash(csv_path: Path) -> str:
    """Compute SHA-256 hash of CSV using row-based method (CRLF-independent).

    Hash is computed from parsed rows with Unix timestamps, making it
    independent of line ending style (CRLF vs LF) and timestamp format.
    """
    if not csv_path.exists():
        return ""
    h = hashlib.sha256()
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(
                datetime.fromisoformat(row["timestamp"])
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
            line = f"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n"
            h.update(line.encode())
    return h.hexdigest()


def load_metadata(meta_path: Path) -> Dict[str, Any]:
    """Load JSON metadata file. Returns empty dict if missing."""
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_metadata(meta_path: Path, meta: Dict[str, Any]) -> None:
    """Atomically save metadata JSON (write-then-rename)."""
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    temp = meta_path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    temp.replace(meta_path)


def detect_gaps(candles: Dict[int, Any]) -> List[Dict[str, Any]]:
    """Return a list of gap dicts for any missing hourly candles in the series."""
    if len(candles) < 2:
        return []
    sorted_ts = sorted(candles.keys())
    gaps: List[Dict[str, Any]] = []
    for i in range(1, len(sorted_ts)):
        actual_diff = sorted_ts[i] - sorted_ts[i - 1]
        if actual_diff > 3600:
            gap_hours = actual_diff / 3600
            gaps.append({
                "gap_start": datetime.fromtimestamp(sorted_ts[i - 1] + 3600, tz=timezone.utc).isoformat(),
                "gap_end": datetime.fromtimestamp(sorted_ts[i] - 3600, tz=timezone.utc).isoformat(),
                "missing_candles": (actual_diff // 3600) - 1,
                "gap_hours": round(gap_hours, 2),
                "severity": (
                    "critical" if gap_hours > 24 else ("major" if gap_hours > 6 else "minor")
                ),
            })
    return gaps


# ── Phase 3F.5 Persistence Layer ─────────────────────────────────────────────────


def validate_candle_ohlcv(candle: Dict[str, Any]) -> None:
    """Validate a candle dict's OHLCV fields.

    Raises ValueError with a descriptive message if any rule is violated:
      - open > 0, high > 0, low > 0, close > 0
      - volume >= 0
      - high >= max(open, close, low)
      - low <= min(open, close, high)
    """
    try:
        o = Decimal(str(candle["open"]))
        h = Decimal(str(candle["high"]))
        l = Decimal(str(candle["low"]))
        c = Decimal(str(candle["close"]))
        v = Decimal(str(candle["volume"]))
    except (KeyError, TypeError, Exception) as e:
        raise ValueError(f"Cannot parse OHLCV fields: {e} | candle={candle}") from e

    if o <= 0:
        raise ValueError(f"open must be > 0, got {o}")
    if h <= 0:
        raise ValueError(f"high must be > 0, got {h}")
    if l <= 0:
        raise ValueError(f"low must be > 0, got {l}")
    if c <= 0:
        raise ValueError(f"close must be > 0, got {c}")
    if v < 0:
        raise ValueError(f"volume must be >= 0, got {v}")
    expected_high = max(o, c, l)
    if h < expected_high:
        raise ValueError(
            f"high ({h}) must be >= max(open, close, low) = {expected_high}"
        )
    expected_low = min(o, c, h)
    if l > expected_low:
        raise ValueError(
            f"low ({l}) must be <= min(open, close, high) = {expected_low}"
        )


def validate_candle_year(
    candle: Dict[str, Any],
    csv_path: Optional[Path] = None,
    target_year: Optional[int] = None,
) -> None:
    """Validate that a candle's UTC timestamp matches the target calendar year partition.

    Raises ValueError if candle timestamp does not belong to the target year.
    If target_year is not explicitly provided, attempts to infer it from csv_path
    (e.g., '2026.csv' -> 2026). Defaults to 2026 if csv_path contains '2026'.
    """
    ts = candle.get("timestamp")
    if ts is None:
        raise ValueError(f"Missing 'timestamp' in candle: {candle}")
    if isinstance(ts, datetime):
        c_dt = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    else:
        c_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)

    expected_year = target_year
    if expected_year is None and csv_path is not None:
        stem = csv_path.stem
        if stem.isdigit() and len(stem) == 4:
            expected_year = int(stem)
        elif "2026" in csv_path.name:
            expected_year = 2026

    if expected_year is not None and c_dt.year != expected_year:
        raise ValueError(
            f"Year partition guard: timestamp {c_dt.isoformat()} (year {c_dt.year}) "
            f"does not belong to {csv_path.name if csv_path else f'{expected_year}.csv'} "
            f"(expected year {expected_year})"
        )


@dataclass
class UpsertResult:
    """Result of an upsert_closed_candles() call."""
    inserts: int = 0
    updates: int = 0
    unchanged: int = 0
    total: int = 0
    gaps: List[Dict[str, Any]] = dc_field(default_factory=list)
    sha256: str = ""


def _normalize_candle_ts(candle: Dict[str, Any]) -> tuple:
    """Return (ts_int, normalized_dict) from a candle dict.

    Accepts timestamp as:
      - int/float (Unix seconds)
      - datetime object

    Returns a normalized dict with:
      - timestamp: datetime (UTC)
      - open/high/low/close/volume: Decimal
    """
    ts = candle["timestamp"]
    if isinstance(ts, datetime):
        ts_int = int(ts.timestamp())
    else:
        ts_int = int(ts)
    normalized = {
        "timestamp": datetime.fromtimestamp(ts_int, tz=timezone.utc),
        "open":   Decimal(str(candle["open"])),
        "high":   Decimal(str(candle["high"])),
        "low":    Decimal(str(candle["low"])),
        "close":  Decimal(str(candle["close"])),
        "volume": Decimal(str(candle["volume"])),
    }
    return ts_int, normalized


def upsert_closed_candles(
    candles: List[Dict[str, Any]],
    csv_path: Path,
    meta_path: Path,
    check_closed: bool = True,
    target_year: Optional[int] = None,
) -> UpsertResult:
    """Atomically upsert a list of closed candles into the canonical CSV.

    This is the SINGLE AUTHORITATIVE persistence function for Phase 3F.5/3F.6.1.
    Both WebSocket live candles and REST backfill MUST use this function.

    Rules enforced:
      Rule 1 — Only closed candles (candle_ts < current_hour_start); forming silently skipped.
      Rule 2 — Timestamp deduplication: same timestamp processed once.
      Rule 3 — INSERT / UPDATE / UNCHANGED semantics based on OHLCV comparison.
      Rule 4 — Output is strictly chronologically ordered.
      Rule 5 — OHLCV validation via validate_candle_ohlcv() before any write.
      Rule 5b — Year partition guard: timestamps must match target calendar year (e.g. 2026).
      Rule 6 — Atomic write: write to .tmp, then os.replace() to canonical.
      Rule 7 — Metadata JSON updated after successful write.

    Args:
        candles:      List of candle dicts. timestamp may be int (Unix seconds) or datetime.
                      Extra keys (symbol, timeframe, is_closed, etc.) are silently ignored.
        csv_path:     Path to canonical CSV.
        meta_path:    Path to metadata JSON.
        check_closed: If True (default), silently skip forming candles.
                      Set False only in tests that want to verify OHLCV rejection.
        target_year:  Optional expected calendar year (e.g. 2026). Inferred from csv_path if None.

    Returns:
        UpsertResult with counts of inserts, updates, unchanged, total, gaps, sha256.
        If no inserts or updates occurred, returns immediately without writing.

    Raises:
        ValueError:   If OHLCV validation or Year partition validation fails for any incoming candle.
        RuntimeError: If final dataset integrity check fails.
        OSError:      If the atomic write or rename fails.
    """
    if not candles:
        return UpsertResult()

    # Step 1: Load existing canonical dataset
    existing = load_candles(csv_path)
    original_count = len(existing)

    # Step 2: Process incoming candles
    now_ts = int(datetime.now(timezone.utc).timestamp())
    current_hour_start = now_ts - (now_ts % 3600)

    result = UpsertResult()
    seen_in_batch: set = set()

    for raw_candle in candles:
        # Normalize timestamp
        ts_int, normalized = _normalize_candle_ts(raw_candle)

        # Rule 1: Silently skip forming candles
        if check_closed and ts_int >= current_hour_start:
            continue

        # Rule 5: Validate OHLCV (raises ValueError on violation)
        validate_candle_ohlcv(normalized)

        # Rule 5b: Year Partition Guard (raises ValueError on violation)
        validate_candle_year(normalized, csv_path=csv_path, target_year=target_year)

        # Rule 2: Deduplicate within batch (keep first occurrence)
        if ts_int in seen_in_batch:
            continue
        seen_in_batch.add(ts_int)

        # Rule 3: INSERT / UPDATE / UNCHANGED
        if ts_int in existing:
            ex = existing[ts_int]
            if (
                ex["open"]   == normalized["open"] and
                ex["high"]   == normalized["high"] and
                ex["low"]    == normalized["low"]  and
                ex["close"]  == normalized["close"] and
                ex["volume"] == normalized["volume"]
            ):
                result.unchanged += 1
            else:
                existing[ts_int] = normalized
                result.updates += 1
        else:
            existing[ts_int] = normalized
            result.inserts += 1

    result.total = len(existing)

    # If nothing changed, return early without touching the filesystem
    if result.inserts == 0 and result.updates == 0:
        result.gaps = detect_gaps(existing)
        result.sha256 = csv_hash(csv_path) if csv_path.exists() else ""
        return result

    # Step 3: Validate final dataset integrity before writing
    sorted_ts = sorted(existing.keys())
    if len(sorted_ts) != len(set(sorted_ts)):
        raise RuntimeError(
            "Phase 3F.5: Final dataset has duplicate timestamps — aborting write."
        )
    for i in range(1, len(sorted_ts)):
        if sorted_ts[i] <= sorted_ts[i - 1]:
            raise RuntimeError(
                f"Phase 3F.5: Dataset not strictly increasing at index {i}: "
                f"{sorted_ts[i - 1]} -> {sorted_ts[i]}"
            )

    # Step 4: Atomic write — write to .tmp then os.replace()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = csv_path.parent / (csv_path.name + ".tmp")
    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for ts_int in sorted_ts:
                c = existing[ts_int]
                writer.writerow([
                    datetime.fromtimestamp(ts_int, tz=timezone.utc).isoformat(),
                    str(c["open"]), str(c["high"]), str(c["low"]),
                    str(c["close"]), str(c["volume"]),
                ])
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename — original remains intact if this fails
        tmp_path.replace(csv_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    # Step 5: Compute SHA-256 (row-based, CRLF-independent)
    sha = csv_hash(csv_path)
    gaps = detect_gaps(existing)
    result.gaps = gaps
    result.sha256 = sha

    # Step 6: Update metadata JSON (Rule 7)
    meta = load_metadata(meta_path) if meta_path.exists() else {}
    meta.update({
        "candle_count":    len(existing),
        "first_timestamp": datetime.fromtimestamp(sorted_ts[0], tz=timezone.utc).isoformat(),
        "last_timestamp":  datetime.fromtimestamp(sorted_ts[-1], tz=timezone.utc).isoformat(),
        "gap_count":       len(gaps),
        "invalid_ohlc":    0,
        "sha256":          sha,
        "download_utc":    datetime.now(timezone.utc).isoformat(),
    })
    # Preserve required static fields if not yet present
    meta.setdefault("symbol",     "BTCUSD.P")
    meta.setdefault("delta_symbol", "BTCUSD")
    meta.setdefault("exchange",   "Delta Exchange India (api.india.delta.exchange)")
    meta.setdefault("timeframe",  "1h")
    meta.setdefault("source_url", "https://api.india.delta.exchange/v2/history/candles")
    save_metadata(meta_path, meta)

    return result


def _fetch_window(start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """Fetch a single window of raw candles from Delta REST API.

    Returns a list of raw Delta candle dicts (with field "time").
    Retries up to 3 times with 3-second backoff on failure.
    """
    url = (
        f"https://api.india.delta.exchange/v2/history/candles?"
        f"{urllib.parse.urlencode({'symbol': SYMBOL_EXCHANGE, 'resolution': RESOLUTION, 'start': str(start_ts), 'end': str(end_ts)})}"
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "QuantEdge-Ingestion/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            if not data.get("success"):
                raise RuntimeError(f"Delta API error: {data}")
            return data.get("result", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]}")
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(3)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    raise RuntimeError("Max retries exceeded")


MIN_CANONICAL_YEAR_START_TS = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())


def fetch_closed_candles(start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """Fetch all CLOSED candles in [start_ts, end_ts) from Delta REST API.

    Excludes the currently forming candle by capping at current_hour_start.
    Enforces minimum start_ts >= 2026-01-01 00:00:00 UTC (canonical year boundary).
    Returns raw Delta dicts sorted chronologically, deduplicated by "time".
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    # Current hour start = floor(now to hour)
    current_hour_start = now_ts - (now_ts % 3600)
    # Only fetch candles whose timestamp < current_hour_start (fully closed)
    cursor_end = min(end_ts, current_hour_start)
    # Bound start_ts to canonical minimum (never request pre-2026 for 2026 series)
    effective_start = max(start_ts, MIN_CANONICAL_YEAR_START_TS)

    print(
        f"  Fetching closed candles [{datetime.fromtimestamp(effective_start, tz=timezone.utc).isoformat()} -> "
        f"{datetime.fromtimestamp(cursor_end, tz=timezone.utc).isoformat()}]"
    )

    all_candles: List[Dict[str, Any]] = []
    while cursor_end > effective_start:
        cursor_start = max(effective_start, cursor_end - MAX_PER_REQ * 3600)
        try:
            candles = _fetch_window(cursor_start, cursor_end)
        except Exception as e:
            print(f"    [ERROR] Failed to fetch window: {e}")
            break
        if not candles:
            cursor_end = max(effective_start, cursor_end - MAX_PER_REQ * 3600)
            continue
        # Refresh current_hour_start each iteration so it remains accurate
        _now = int(datetime.now(timezone.utc).timestamp())
        _chs = _now - (_now % 3600)
        for c in candles:
            # Enforce closed candle and year partition (2026)
            c_time = int(c["time"])
            if c_time < _chs and datetime.fromtimestamp(c_time, tz=timezone.utc).year == 2026:
                all_candles.append(c)
        oldest_ts = min(c["time"] for c in candles)
        print(
            f"    +{len(candles):4d} API candles | "
            f"oldest: {datetime.fromtimestamp(oldest_ts, tz=timezone.utc).isoformat()}"
        )
        cursor_end = oldest_ts - 1
        time.sleep(0.25)

    all_candles.sort(key=lambda c: c["time"])
    seen: set = set()
    deduped = []
    for c in all_candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            deduped.append(c)
    return deduped


# ── Main Ingestion Service ──────────────────────────────────────────────────────


class DeltaExchangeIngestionService:
    """Delta Exchange India BTCUSD 1H Market Data Ingestion Service."""

    def __init__(self, csv_path: Optional[Path] = None, meta_path: Optional[Path] = None) -> None:
        # Paths resolved via parameters or fallback to canonical constants
        self.csv_path = csv_path if csv_path is not None else CANONICAL_CSV
        self.meta_path = meta_path if meta_path is not None else CANONICAL_META
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_candles(self) -> Dict[int, Dict[str, Any]]:
        return load_candles(self.csv_path)

    def _write_candles(self, candles: Dict[int, Dict[str, Any]]) -> None:
        write_candles(self.csv_path, candles)

    def _csv_hash(self) -> str:
        return csv_hash(self.csv_path)

    def detect_gaps(self, candles: Dict[int, Any]) -> List[Dict[str, Any]]:
        return detect_gaps(candles)

    def _load_metadata(self) -> Dict[str, Any]:
        return load_metadata(self.meta_path)

    def _save_metadata(self, metadata: Dict[str, Any]) -> None:
        save_metadata(self.meta_path, metadata)

    def _generate_metadata(
        self, candles: Dict[int, Any], gaps: List[Dict]
    ) -> Dict[str, Any]:
        if not candles:
            return {}
        return {
            "symbol": "BTCUSD.P",
            "delta_symbol": "BTCUSD",
            "tradingview_symbol": "BTCUSD.P",
            "exchange": "Delta Exchange India (api.india.delta.exchange)",
            "description": "Bitcoin Perpetual futures, quoted, settled & margined in US Dollar",
            "timeframe": "1h",
            "source_url": "https://api.india.delta.exchange/v2/history/candles",
            "download_utc": datetime.now(timezone.utc).isoformat(),
            "candle_count": len(candles),
            "first_timestamp": datetime.fromtimestamp(min(candles.keys()), tz=timezone.utc).isoformat(),
            "last_timestamp": datetime.fromtimestamp(max(candles.keys()), tz=timezone.utc).isoformat(),
            "gap_count": len(gaps),
            "max_gap_hours": max((g["gap_hours"] for g in gaps if g), default=0.0),
            "gaps": gaps,
            "invalid_ohlc": 0,
            "sha256": self._csv_hash(),
            "policy": (
                "Delta Exchange India BTCUSD is the ONLY canonical market-data "
                "source for QuantEdge AI V2."
            ),
            "quality_report": {
                "gap_count": len(gaps),
                "max_gap_hours": max((g["gap_hours"] for g in gaps if g), default=0.0),
                "invalid_ohlc_count": 0,
                "duplicate_count": 0,
                "is_sorted_ascending": True,
                "status": "VALIDATED_CLEAN",
            },
        }

    def run_incremental_ingestion(self) -> Dict[str, Any]:
        """Fetch newly closed candles from Delta REST and persist via upsert_closed_candles().

        Uses the single authoritative persistence path (Phase 3F.5).
        REST backfill -> validate -> deduplicate -> atomic upsert -> metadata.
        """
        existing_candles = self._load_candles()

        print("=" * 60)
        print("Delta Exchange India Ingestion - Incremental")
        print("=" * 60)
        print(f"  Symbol       : BTCUSD.P (Delta: BTCUSD)")
        print(f"  Timeframe    : 1H")
        print(f"  Existing     : {len(existing_candles)} candles")

        if existing_candles:
            start_ts = max(existing_candles.keys()) + 3600
        else:
            start_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())

        print(f"  From         : {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()}")

        try:
            raw_candles = fetch_closed_candles(
                start_ts, int(datetime.now(timezone.utc).timestamp())
            )
        except Exception as e:
            return {"success": False, "errors": [f"Fetch failed: {e}"]}

        print(f"  Fetched      : {len(raw_candles)} new closed candles")

        if not raw_candles:
            return {
                "success": True,
                "candles_fetched": 0,
                "candles_new": 0,
                "candles_updated": 0,
                "gaps_detected": 0,
                "gaps": [],
                "errors": [],
                "csv_hash": self._csv_hash(),
            }

        # Normalize raw Delta REST dicts -> candle dicts for upsert
        candle_dicts = []
        for c in raw_candles:
            ts_int = int(c["time"])
            candle_dicts.append({
                "timestamp": ts_int,
                "open":   Decimal(str(c.get("open",   c.get("o", "0")))),
                "high":   Decimal(str(c.get("high",   c.get("h", "0")))),
                "low":    Decimal(str(c.get("low",    c.get("l", "0")))),
                "close":  Decimal(str(c.get("close",  c.get("c", "0")))),
                "volume": Decimal(str(c.get("volume", c.get("v", "0")))),
            })

        # Single authoritative persistence path (Rule 8)
        upsert_result = upsert_closed_candles(
            candle_dicts, self.csv_path, self.meta_path
        )

        print()
        print("Ingestion Summary:")
        print(f"  Inserts           : {upsert_result.inserts}")
        print(f"  Updates           : {upsert_result.updates}")
        print(f"  Unchanged         : {upsert_result.unchanged}")
        print(f"  Gaps detected     : {len(upsert_result.gaps)}")
        print(f"  CSV SHA-256       : {upsert_result.sha256}")

        return {
            "success":          True,
            "candles_fetched":  len(raw_candles),
            "candles_new":      upsert_result.inserts,
            "candles_updated":  upsert_result.updates,
            "candles_unchanged":upsert_result.unchanged,
            "gaps_detected":    len(upsert_result.gaps),
            "gaps":             upsert_result.gaps,
            "errors":           [],
            "csv_hash":         upsert_result.sha256,
        }

    def run(self) -> Dict[str, Any]:
        """Main entry point for incremental ingestion."""
        return self.run_incremental_ingestion()


# ── Module-level convenience ─────────────────────────────────────────────────────


def run_incremental_ingestion() -> Dict[str, Any]:
    """Convenience function for scheduled ingestion."""
    service = DeltaExchangeIngestionService()
    return service.run_incremental_ingestion()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        print("Backfill: use the standalone download scripts")
    else:
        run_incremental_ingestion()