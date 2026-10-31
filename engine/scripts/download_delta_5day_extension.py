#!/usr/bin/env python3
"""
Delta Exchange India 1h candle EXTENSION for the 5-day parity diagnostic.
========================================================================

WHY THIS SCRIPT EXISTS
----------------------
The repository's canonical history ends at 2026-08-26T12:00:00Z for all four
pairs, so the requested diagnostic window 2026-08-28 -> 2026-09-02 contains
ZERO candles. The task authorises downloading the missing data from the
authoritative source and forbids overwriting canonical data blindly.

WHAT IT DOES AND DOES NOT TOUCH
-------------------------------
  * READS   data/canonical/delta_exchange_india/<SYM>/1h/full_history.csv
  * WRITES  engine/data/research/manual_smc_5day_parity/canonical_extended/
                delta_exchange_india/<SYM>/1h/full_history.csv
  * NEVER writes anything under data/canonical. The extended tree is a
    SEPARATE canonical-format directory, so `load_canonical_candles` reads it
    unmodified by passing it as `canonical_base`.

The existing portion of every extended file is byte-identical to its source:
the source bytes are copied verbatim and new rows are appended. That makes
"the historical baseline was not altered" checkable with sha256 rather than
assumed.

NETWORK SURFACE
---------------
Exactly one endpoint, unauthenticated, read-only:

    GET https://api.india.delta.exchange/v2/history/candles
        ?resolution=1h&symbol=<SYM>&start=<unix>&end=<unix>

the same URL `data/canonical/.../1h/2026_metadata.json` records as the
canonical source. No credentials are read, sent or required. No account,
order, position, wallet or trading endpoint is contacted.

PARTIAL CANDLES ARE REFUSED
---------------------------
Delta returns the in-progress bar. A bar is accepted only when it has fully
closed (`time + 3600 <= now`), because an unfinished bar's high/low/close are
not final and the lifecycle treats every candle it is handed as closed.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

DELTA_API = "https://api.india.delta.exchange/v2/history/candles"
RESOLUTION = "1h"
USER_AGENT = "QuantEdge/2.0"
SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

HOUR = 3600
CHUNK_HOURS = 2000
MAX_ATTEMPTS = 5
REQUEST_TIMEOUT = 20

#: Canonical rendering, reproduced from the existing files byte-for-byte:
#: `2026-08-26 12:00:00+00:00,78458.0,78686.5,77979.5,78259.0,739246.0` with
#: CRLF terminators. Verified against the source bytes before writing.
TS_FORMAT = "%Y-%m-%d %H:%M:%S+00:00"
LINE_END = b"\r\n"
HEADER = "timestamp,open,high,low,close,volume"

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_BASE = REPO_ROOT / "data" / "canonical" / "delta_exchange_india"
OUT_ROOT = (REPO_ROOT / "engine" / "data" / "research"
            / "manual_smc_5day_parity")
OUT_BASE = OUT_ROOT / "canonical_extended" / "delta_exchange_india"
PROVENANCE = OUT_ROOT / "data_provenance.json"


class DownloadError(RuntimeError):
    """Any refusal. Every failure path raises rather than degrading."""


def sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# The existing file
# ---------------------------------------------------------------------------
def read_source(symbol: str) -> Tuple[bytes, List[datetime]]:
    """
    Return the source file's RAW BYTES plus its parsed timestamps.

    The bytes are what gets copied into the extended file, so the historical
    portion cannot be reformatted, re-rounded or reordered by this script.
    The parsed timestamps are used only to decide where the download starts
    and to prove the append point is contiguous.
    """
    path = SOURCE_BASE / symbol / "1h" / "full_history.csv"
    if not path.exists():
        raise DownloadError(f"{path} does not exist; refusing to invent a "
                            f"canonical baseline for {symbol}")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    lines = [ln for ln in text.split("\r\n") if ln]
    if not lines or lines[0] != HEADER:
        raise DownloadError(
            f"{path}: header is {lines[0]!r} not {HEADER!r}; the canonical "
            f"format this script preserves is not the format on disk")
    if not raw.endswith(LINE_END):
        raise DownloadError(
            f"{path}: does not end with CRLF; appending would join two rows")
    stamps: List[datetime] = []
    for line_no, line in enumerate(lines[1:], start=2):
        cell = line.split(",")[0]
        try:
            parsed = datetime.fromisoformat(cell)
        except ValueError as exc:
            raise DownloadError(f"{path}:{line_no}: {cell!r}") from exc
        if parsed.tzinfo is None:
            raise DownloadError(
                f"{path}:{line_no}: {cell!r} carries no timezone; refusing to "
                f"assume UTC for a candle that decides a trade")
        stamps.append(parsed.astimezone(timezone.utc))
    if sorted(stamps) != stamps or len(set(stamps)) != len(stamps):
        raise DownloadError(f"{path}: not strictly ascending / has duplicates")
    return raw, stamps


# ---------------------------------------------------------------------------
# The download
# ---------------------------------------------------------------------------
def fetch_window(symbol: str, start_unix: int, end_unix: int
                 ) -> Dict[int, Dict[str, float]]:
    """
    Fetch `[start_unix, end_unix]` in chunks. Keyed by unix time, deduped.

    Delta returns rows in DESCENDING time order and may repeat rows across
    chunk boundaries; both are handled by keying on `time`. A chunk that never
    succeeds raises — a silently short download would look like a data gap.
    """
    out: Dict[int, Dict[str, float]] = {}
    cursor = start_unix
    while cursor <= end_unix:
        upper = min(end_unix, cursor + CHUNK_HOURS * HOUR)
        url = (f"{DELTA_API}?resolution={RESOLUTION}&symbol={symbol}"
               f"&start={cursor}&end={upper}")
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT})
        last_error: object = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(
                        request, timeout=REQUEST_TIMEOUT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:                # noqa: BLE001 - reported
                last_error = exc
                time.sleep(1.0 + attempt)
        else:
            raise DownloadError(
                f"{symbol}: {MAX_ATTEMPTS} attempts failed for "
                f"[{cursor}, {upper}]: {last_error!r}")
        if not payload.get("success"):
            raise DownloadError(f"{symbol}: API reported success=false for "
                                f"[{cursor}, {upper}]: {payload!r}")
        for row in payload.get("result") or ():
            stamp = int(row["time"])
            if stamp < start_unix or stamp > end_unix:
                continue
            out[stamp] = {k: float(row[k])
                          for k in ("open", "high", "low", "close", "volume")}
        cursor = upper + HOUR
        time.sleep(0.05)
    return out


# ---------------------------------------------------------------------------
# Validation — every check refuses rather than repairs
# ---------------------------------------------------------------------------
def validate_new_rows(
    symbol: str,
    rows: Sequence[Tuple[datetime, Dict[str, float]]],
    last_existing: datetime,
) -> None:
    """OHLC sanity, strict hourly contiguity, and no overlap with the source."""
    if not rows:
        raise DownloadError(
            f"{symbol}: the endpoint returned no closed candle after "
            f"{last_existing.isoformat()}; refusing to fabricate the window")
    previous = last_existing
    for stamp, bar in rows:
        if stamp <= last_existing:
            raise DownloadError(
                f"{symbol}: downloaded {stamp.isoformat()} overlaps the "
                f"existing history (last {last_existing.isoformat()})")
        delta_hours = (stamp - previous).total_seconds() / HOUR
        if delta_hours != 1.0:
            raise DownloadError(
                f"{symbol}: {previous.isoformat()} -> {stamp.isoformat()} is "
                f"{delta_hours}h, not 1h; refusing to interpolate a gap")
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        v = bar["volume"]
        for name, value in (("open", o), ("high", h), ("low", l),
                            ("close", c), ("volume", v)):
            if value != value or value in (float("inf"), float("-inf")):
                raise DownloadError(
                    f"{symbol} {stamp.isoformat()}: {name}={value!r}")
        if h < max(o, c) or l > min(o, c) or h < l or v < 0:
            raise DownloadError(
                f"{symbol} {stamp.isoformat()}: OHLC violation "
                f"O={o} H={h} L={l} C={c} V={v}")
        previous = stamp


def render_row(stamp: datetime, bar: Dict[str, float]) -> str:
    """One canonical CSV line, in the exact style already on disk."""
    return ",".join((
        stamp.strftime(TS_FORMAT),
        str(bar["open"]), str(bar["high"]), str(bar["low"]),
        str(bar["close"]), str(bar["volume"]),
    ))


# ---------------------------------------------------------------------------
# One symbol, end to end
# ---------------------------------------------------------------------------
def extend_symbol(symbol: str, now_unix: int) -> Dict[str, object]:
    raw, stamps = read_source(symbol)
    last_existing = stamps[-1]
    source_path = SOURCE_BASE / symbol / "1h" / "full_history.csv"
    source_sha = sha256_of(source_path)

    start_unix = int(last_existing.timestamp()) + HOUR
    #: Only CLOSED bars. `now_unix` is floored to the hour by the caller, so a
    #: bar is closed exactly when its open + 3600 <= now_unix.
    end_unix = now_unix - HOUR
    if end_unix < start_unix:
        raise DownloadError(
            f"{symbol}: no closed candle exists after "
            f"{last_existing.isoformat()} at this wall-clock time")

    fetched = fetch_window(symbol, start_unix, end_unix)
    rows = [(datetime.fromtimestamp(t, tz=timezone.utc), fetched[t])
            for t in sorted(fetched)]
    validate_new_rows(symbol, rows, last_existing)

    out_path = OUT_BASE / symbol / "1h" / "full_history.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    appended = LINE_END.join(
        render_row(s, b).encode("utf-8") for s, b in rows) + LINE_END
    out_path.write_bytes(raw + appended)

    written = out_path.read_bytes()
    if not written.startswith(raw):
        raise DownloadError(
            f"{symbol}: extended file does not start with the source bytes")
    return {
        "symbol": symbol,
        "timeframe": RESOLUTION,
        "source_url": DELTA_API,
        "source_file": str(source_path.relative_to(REPO_ROOT)).replace(
            "\\", "/"),
        "source_file_sha256": source_sha,
        "source_candle_count": len(stamps),
        "source_first_ts": stamps[0].isoformat(),
        "source_last_ts": last_existing.isoformat(),
        "downloaded_candle_count": len(rows),
        "downloaded_first_ts": rows[0][0].isoformat(),
        "downloaded_last_ts": rows[-1][0].isoformat(),
        "extended_file": str(out_path.relative_to(REPO_ROOT)).replace(
            "\\", "/"),
        "extended_file_sha256": sha256_of(out_path),
        "extended_candle_count": len(stamps) + len(rows),
        "extended_last_ts": rows[-1][0].isoformat(),
        "source_bytes_preserved_verbatim": True,
        "partial_candle_excluded_after": datetime.fromtimestamp(
            end_unix, tz=timezone.utc).isoformat(),
    }


def main(argv: Sequence[str]) -> int:
    started = datetime.now(timezone.utc)
    now_unix = int(started.timestamp()) // HOUR * HOUR
    print(f"download started {started.isoformat()}")
    print(f"source (read-only)   {SOURCE_BASE}")
    print(f"extended tree (new)  {OUT_BASE}")
    print(f"endpoint             {DELTA_API}  [unauthenticated GET]")

    entries: Dict[str, object] = {}
    for symbol in SYMBOLS:
        entry = extend_symbol(symbol, now_unix)
        entries[symbol] = entry
        print(f"  {symbol:8s} +{entry['downloaded_candle_count']:>4} candles  "
              f"{entry['downloaded_first_ts']} .. "
              f"{entry['downloaded_last_ts']}")

    #: Every pair must land on the SAME last candle, otherwise a portfolio run
    #: that shares one trade slot would give the longer pair extra candles.
    last_stamps = sorted({e["extended_last_ts"] for e in entries.values()})
    if len(last_stamps) != 1:
        raise DownloadError(
            f"pairs disagree on the final candle: {last_stamps}; refusing to "
            f"run a shared-slot backtest on ragged coverage")

    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(json.dumps({
        "purpose": "5-day Manual SMC / TradingView parity diagnostic",
        "download_started_utc": started.isoformat(),
        "download_finished_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Delta Exchange India",
        "source_url": DELTA_API,
        "authentication": "none - public market-data endpoint, no credentials",
        "resolution": RESOLUTION,
        "symbols": list(SYMBOLS),
        "canonical_data_overwritten": False,
        "extended_tree": str(OUT_BASE.relative_to(REPO_ROOT)).replace(
            "\\", "/"),
        "closed_candles_only": True,
        "aligned_last_candle": last_stamps[0],
        "per_symbol": entries,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\naligned last candle  {last_stamps[0]}")
    print(f"provenance           {PROVENANCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
