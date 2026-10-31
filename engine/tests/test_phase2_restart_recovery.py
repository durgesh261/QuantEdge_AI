"""Phase 2: durable state + exact restart recovery. Mandate tests A-Q.

WHAT THIS FILE PROVES
---------------------
`quantedge.runtime.restart_recovery` brings the STRUCTURE clock from a durable
snapshot to the current closed-candle boundary EXACTLY - indistinguishable from
a process that never stopped - or it refuses. Every parity assertion compares
two runtimes that shared a prefix:

    reference : ONE runtime driven continuously over prefix + gap (+ tail)
    restored  : prefix -> save_state -> load_state -> bridge the gap through a
                FAKE fetcher -> (+ the same tail)

and requires `runtime.snapshot()` equality plus, channel by channel, the eight
things mandate section 5 names: LuxAlgo state, scanner state, OB identity, the
invalidation ledger, `_ob_seq`, the rejection counters, lifecycle active OB
state and the single-active-trade slot.

REAL HISTORY, NOT A SYNTHETIC SERIES
------------------------------------
The parity windows are slices of the repository's own tracked canonical 1h
history (`data/canonical/delta_exchange_india`; measured: 19,597 rows per
symbol, 0 holes, perfectly hour-contiguous). A 160-seed sweep of synthetic
series produced only AWAITING_DISPLACEMENT order blocks and never an active
trade, so a synthetic fixture cannot exercise "lifecycle active OB state" or
"single-active-trade state" at all. The main cut below is MEASURED to carry a
TRADE_ACTIVE order block and a non-null `active_trade`.

NOTHING HERE TOUCHES DELTA, CREDENTIALS OR EXECUTION
----------------------------------------------------
Every historical fetch is a fake injected through `fetcher=`. The tests that
exercise the production default additionally monkeypatch the REST layer to a
raiser, so a regression that reached the network would fail rather than dial
out. No credential is read, no socket is opened, no orchestrator is built, and
`on_market_update` is never bound.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import time
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest

from quantedge.config import Settings
from quantedge.market_data import delta_websocket as dws
from quantedge.market_data import ingestion
from quantedge.runtime import restart_recovery as R
from quantedge.runtime.manual_smc_runtime import (
    ManualSMCRuntime,
    ManualSMCRuntimeStep,
    RuntimeStateError,
    build_manual_smc_runtime,
    make_feed_callback,
)
from quantedge.strategy.manual_smc.backtest import load_canonical_candles
from quantedge.strategy.manual_smc.state import StateIntegrityError

HOUR = R.HOUR
REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = REPO_ROOT / "data" / "canonical" / "delta_exchange_india"

# ---------------------------------------------------------------------------
# MEASURED constants. Every number here was read off the repository, never
# assumed. The bar indices are absolute epoch hours (`bar_index_for`), so they
# pin the window even if canonical rows are ever appended.
# ---------------------------------------------------------------------------
SYM = "ETHUSD"

#: Main parity window: canonical row offsets. Row 12000 is bar 489000
#: (2025-10-14T00:00Z); the cut is the 708th bar, i.e. bar 489707
#: (2025-11-12T11:00Z), MEASURED to hold 7 live order blocks - six
#: AWAITING_DISPLACEMENT and one TRADE_ACTIVE - plus a non-null active trade.
MAIN_START = 12000
MAIN_CUT = 708
MAIN_FIRST_BAR = 489000
MAIN_CUT_BAR = 489707
MAIN_CUT_OB_SEQ = 36
MAIN_CUT_LIVE_OBS = 7
MAIN_CUT_TRADE_ACTIVE_OBS = 1

#: Gaps the mandate names (C: 1h; D: 2/3/6/24h) plus the tail replayed after
#: the bridge, which proves the two runtimes stay identical going FORWARD and
#: not merely at the join.
GAPS: Tuple[int, ...] = (1, 2, 3, 6, 24)
TAIL = 24

#: Year-boundary window (E). Prefix ends at bar 490859 (2025-12-30T11:00Z);
#: the 40-bar gap runs 490860..490899, i.e. 2025-12-30T12:00Z ->
#: 2026-01-01T03:00Z, crossing both 31 Dec 00:00 and 1 Jan 00:00.
YEAR_START = 13000
YEAR_CUT = 860
YEAR_GAP = 40
YEAR_CUT_BAR = 490859
YEAR_GAP_FIRST_BAR = 490860
YEAR_GAP_LAST_BAR = 490899

# ---------------------------------------------------------------------------
# Canonical history. Loaded once per process; the rows are never mutated.
# ---------------------------------------------------------------------------
_CANDLES: Dict[str, List[Any]] = {}


def _candles(symbol: str = SYM) -> List[Any]:
    """The tracked canonical 1h history, read by the PRODUCTION reader.

    A missing file is a hard failure, not a skip: these four CSVs are tracked
    in git (`git ls-files data/canonical` lists them), so their absence means a
    broken checkout, and silently skipping would delete the only proof of exact
    restart parity from the suite.
    """
    if symbol not in _CANDLES:
        primary = CANONICAL / symbol / "1h" / "full_history.csv"
        assert primary.is_file(), (
            f"the tracked canonical history {primary} is missing; Phase-2 "
            f"parity is measured against real history and must not be skipped")
        _CANDLES[symbol] = list(load_canonical_candles(CANONICAL, symbol))
    return _CANDLES[symbol]


def _bars(lo: int, hi: int, symbol: str = SYM) -> List[R.ClosedBar]:
    """Canonical rows [lo, hi) as `ClosedBar`s addressed by absolute epoch hour.

    `load_canonical_candles` assigns `bar_idx = row position`; production keys
    on `bar_index_for(ts)` instead, so the index is recomputed here exactly the
    way the closed-candle path does it.
    """
    return [R.ClosedBar(symbol=symbol, bar_idx=int(c.ts.timestamp()) // HOUR,
                        ts=c.ts, open=c.open, high=c.high, low=c.low,
                        close=c.close, volume=c.volume)
            for c in _candles(symbol)[lo:hi]]


def _fresh(symbols: Sequence[str] = (SYM,)) -> ManualSMCRuntime:
    """A runtime with NO orchestrator - the production Phase-2 wiring."""
    return build_manual_smc_runtime(symbols=list(symbols))


def _freeze(obj: Any) -> Any:
    """Snapshot -> plain JSON tree, so equality is value equality."""
    return json.loads(json.dumps(obj))


# ---------------------------------------------------------------------------
# The eight parity channels mandate section 5 enumerates.
# ---------------------------------------------------------------------------
def channels(runtime: ManualSMCRuntime, symbol: str = SYM) -> Dict[str, Any]:
    """Read every channel section 5 names, from the live objects AND the codec.

    `snapshot()` equality alone would already be exact, but it is one opaque
    blob: when it differs, nothing says WHICH invariant broke. These channels
    are compared first so a regression names itself.
    """
    lifecycle = runtime.strategy.lifecycle
    scanner = lifecycle._scanners[symbol]
    structure = scanner._structures[symbol]
    strategy_state = _freeze(runtime.snapshot())["strategy"]
    mark = runtime.strategy.watermark.last(symbol)
    return {
        # LuxAlgo state + scanner state, through the production codec.
        "luxalgo_scanners": strategy_state["scanners"],
        # OB identity: ids AND the state each one is in.
        "ob_identity": sorted(
            (ob.ob_id, ob.state.name) for ob in lifecycle.live_obs.values()),
        "live_obs_encoded": sorted(strategy_state["live_obs"],
                                   key=lambda row: row["ob_id"]),
        # Operator invalidation ledger.
        "invalidations": sorted(lifecycle.invalidations.fingerprints),
        "invalidations_encoded": sorted(strategy_state["invalidations"],
                                        key=lambda row: row["fingerprint"]),
        # The order-block sequence counter.
        "ob_seq": structure._ob_seq,
        # Rejection counters. MEASURED 0 for all four symbols over 1500 real
        # bars, so the real-history value is 0; `test_N_*` drives a non-zero
        # value through the same codec on purpose.
        "rejections": (scanner.rejected_non_positive_width,
                       scanner.rejected_below_min_width),
        # Single-active-trade slot.
        "active_trade": strategy_state["active_trade"],
        "has_active_trade": lifecycle.active_trade is not None,
        # Structure clock position.
        "watermark": None if mark is None else mark.bar_idx,
    }


def assert_exact_parity(restored: ManualSMCRuntime,
                        reference: Mapping[str, Any],
                        label: str,
                        symbol: str = SYM) -> None:
    """Channel-by-channel first, then the whole snapshot. Nothing is sampled."""
    got = channels(restored, symbol)
    for name, expected in reference["channels"].items():
        assert got[name] == expected, f"{label}: channel {name} diverged"
    assert _freeze(restored.snapshot()) == reference["snapshot"], (
        f"{label}: the full runtime snapshot diverged")


# ---------------------------------------------------------------------------
# Continuous references. ONE drive per gap, snapshotted at the join and again
# after the tail, cached as frozen JSON so no mutable runtime is ever shared.
# ---------------------------------------------------------------------------
_REF: Dict[Tuple[int, int, int, int], Dict[str, Any]] = {}


def reference(start: int, cut: int, gap: int, tail: int = 0,
              symbol: str = SYM) -> Dict[str, Dict[str, Any]]:
    """`{"join": {...}, "after_tail": {...}}` for an uninterrupted process."""
    key = (start, cut, gap, tail)
    if key not in _REF:
        runtime = _fresh((symbol,))
        R.replay_bars(runtime, _bars(start, start + cut + gap, symbol))
        join = {"channels": channels(runtime, symbol),
                "snapshot": _freeze(runtime.snapshot())}
        after = join
        if tail:
            R.replay_bars(runtime, _bars(start + cut + gap,
                                         start + cut + gap + tail, symbol))
            after = {"channels": channels(runtime, symbol),
                     "snapshot": _freeze(runtime.snapshot())}
        _REF[key] = {"join": join, "after_tail": after}
    return _REF[key]


_SEED: Dict[Tuple[int, int], str] = {}


def seed_text(tmp_path: Path, start: int = MAIN_START,
              cut: int = MAIN_CUT, symbol: str = SYM) -> str:
    """The bytes the EXISTING atomic `save_state()` writes at the cut.

    Produced by the real `save_state`, not by a hand-rolled `json.dumps`: the
    file format is whatever production writes, and `load_state` must accept
    exactly that.
    """
    key = (start, cut)
    if key not in _SEED:
        runtime = _fresh((symbol,))
        R.replay_bars(runtime, _bars(start, start + cut, symbol))
        written = runtime.save_state(tmp_path / "_seed_source.json")
        _SEED[key] = Path(written).read_text(encoding="utf-8")
    return _SEED[key]


def seed_state_dir(tmp_path: Path, start: int = MAIN_START,
                   cut: int = MAIN_CUT, symbol: str = SYM) -> Path:
    """A durable state directory holding a snapshot taken at the cut."""
    text = seed_text(tmp_path, start, cut, symbol)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / R.STATE_FILE_NAME).write_text(text, encoding="utf-8")
    return state_dir


def rows_from_bars(bars: Sequence[R.ClosedBar]) -> List[Dict[str, Any]]:
    """Bars rendered in Delta's raw REST row shape."""
    return [{"time": bar.bar_idx * HOUR, "open": bar.open, "high": bar.high,
             "low": bar.low, "close": bar.close, "volume": bar.volume}
            for bar in bars]


class FakeFetcher:
    """The injected historical transport. Records every call; never dials out.

    `mutate` receives the rows this fetcher was about to return and may drop,
    duplicate or corrupt them - that is how the truncation, hole and malformed
    cases (F, G) are produced without inventing an exchange behaviour.
    """

    def __init__(self, bars_by_symbol: Mapping[str, Sequence[R.ClosedBar]],
                 *, raises: Optional[BaseException] = None,
                 mutate: Optional[Any] = None) -> None:
        self._bars = {sym: list(bars) for sym, bars in bars_by_symbol.items()}
        self._raises = raises
        self._mutate = mutate
        self.calls: List[Tuple[int, int, str]] = []

    def __call__(self, start_ts: int, end_ts: int,
                 symbol: str) -> List[Dict[str, Any]]:
        self.calls.append((start_ts, end_ts, symbol))
        if self._raises is not None:
            raise self._raises
        rows = rows_from_bars([bar for bar in self._bars.get(symbol, ())
                               if start_ts <= bar.bar_idx * HOUR <= end_ts])
        if self._mutate is not None:
            rows = self._mutate(rows)
        return rows

    @property
    def call_count(self) -> int:
        return len(self.calls)


def raising_fetcher(*_args: Any, **_kwargs: Any) -> List[Dict[str, Any]]:
    """Stands in for the REST layer where a test must prove it is never used."""
    raise AssertionError(
        "a Phase-2 test reached the real historical transport; deterministic "
        "tests must never contact Delta")


def boot_clock(newest_bar_idx: int) -> int:
    """`now_ts` that makes `newest_closed_bar_idx()` return exactly this bar.

    `newest_closed_bar_idx(now) = now // 3600 - 1`, so the forming hour is the
    one after the bar under test. Every test bar therefore lies in the real
    past, which the closed-candle path independently insists on.
    """
    return (newest_bar_idx + 1) * HOUR


def write_genesis_csv(base: Path, symbol: str, bars: Sequence[R.ClosedBar],
                      *, name: str = "full_history.csv") -> Path:
    """One canonical CSV in the repository's own layout and column order.

    Read back by the PRODUCTION reader (`load_canonical_candles`), so the
    genesis path under test is the real one. The `full_history` stem carries no
    4-digit year and no "2026", so `validate_candle_year` applies no year gate -
    the same rule the shipped `full_history.csv` files rely on.
    """
    directory = base / symbol / "1h"
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    for bar in bars:
        lines.append(f"{bar.ts.isoformat(sep=' ')},{bar.open},{bar.high},"
                     f"{bar.low},{bar.close},{bar.volume}")
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def monitor() -> R.StructureContinuityMonitor:
    """A private monitor, so no test can influence another through the singleton."""
    return R.StructureContinuityMonitor()


@pytest.fixture()
def clean_singleton():
    """Borrow the process-wide monitor and always hand it back pristine."""
    R.STRUCTURE_MONITOR.reset()
    try:
        yield R.STRUCTURE_MONITOR
    finally:
        R.STRUCTURE_MONITOR.reset()


class Restart:
    """One restart-across-a-gap experiment, assembled from measured history."""

    def __init__(self, tmp_path: Path, gap: int, *, start: int = MAIN_START,
                 cut: int = MAIN_CUT, symbol: str = SYM,
                 mutate: Optional[Any] = None,
                 raises: Optional[BaseException] = None) -> None:
        self.symbol = symbol
        self.start = start
        self.cut = cut
        self.gap = gap
        self.state_dir = seed_state_dir(tmp_path, start, cut, symbol)
        self.state_file = self.state_dir / R.STATE_FILE_NAME
        self.seed_bytes = self.state_file.read_bytes()
        self.gap_bars = _bars(start + cut, start + cut + gap, symbol)
        self.newest = self.gap_bars[-1].bar_idx
        self.first_missing = self.gap_bars[0].bar_idx
        self.fetcher = FakeFetcher({symbol: self.gap_bars}, mutate=mutate,
                                   raises=raises)

    def _kwargs(self) -> Dict[str, Any]:
        return {"state_dir": str(self.state_dir), "genesis_dir": None,
                "fetcher": self.fetcher, "now_ts": boot_clock(self.newest)}

    def run(self) -> R.BootstrapReport:
        return R.bootstrap_structure_state(_fresh((self.symbol,)),
                                           **self._kwargs())

    def run_or_fail(self, monitor: R.StructureContinuityMonitor
                    ) -> R.BootstrapReport:
        return R.bootstrap_or_fail(_fresh((self.symbol,)), monitor=monitor,
                                   **self._kwargs())

    @property
    def expected_call(self) -> Tuple[int, int, str]:
        return (self.first_missing * HOUR, self.newest * HOUR, self.symbol)


# ===========================================================================
# A - the durable state path is configured correctly
# ===========================================================================
class TestA_DurableStatePath:
    """Mandate section 1 and section 2: where state lives, and that it survives."""

    def test_the_state_file_sits_in_the_configured_directory(self, tmp_path):
        target = tmp_path / "deep" / "state"
        path = R.resolve_state_file(str(target))
        assert path == target / R.STATE_FILE_NAME
        assert path.name == "manual_smc_runtime_state.json"
        assert target.is_dir(), "the durable directory is created if absent"

    def test_the_write_probe_proves_writability_and_leaves_nothing_behind(
            self, tmp_path):
        target = tmp_path / "state"
        R.resolve_state_file(str(target))
        assert list(target.iterdir()) == [], (
            "the writability probe must be removed again; a stray file in the "
            "durable directory is indistinguishable from real state")

    @pytest.mark.parametrize("configured", [None, "", "   "])
    def test_an_unconfigured_location_is_refused_and_is_inert(self, configured):
        with pytest.raises(R.StateLocationError) as excinfo:
            R.resolve_state_file(configured)
        assert excinfo.value.code == R.INERT_NO_STATE_DIR
        assert isinstance(excinfo.value, R.INERT_BOOTSTRAP_REFUSALS)
        assert "ENGINE_STATE_DIR" in str(excinfo.value)

    def test_an_unusable_location_is_refused_before_any_subscription(
            self, tmp_path):
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("", encoding="utf-8")
        with pytest.raises(R.StateLocationError) as excinfo:
            R.resolve_state_file(str(blocker))
        assert excinfo.value.code == R.INERT_NO_STATE_DIR
        assert "not writable" in str(excinfo.value)

    def test_both_settings_exist_and_default_to_unconfigured(self, monkeypatch):
        """Fail-closed defaults: neither location is guessed."""
        for field in ("engine_state_dir", "engine_genesis_history_dir"):
            assert field in Settings.model_fields, (
                f"{field} must exist so the durable location is configuration, "
                f"not a hardcoded path")
        monkeypatch.delenv("ENGINE_STATE_DIR", raising=False)
        monkeypatch.delenv("ENGINE_GENESIS_HISTORY_DIR", raising=False)
        fresh = Settings()
        assert fresh.engine_state_dir is None
        assert fresh.engine_genesis_history_dir is None

    def test_compose_mounts_a_dedicated_persistent_engine_state_volume(self):
        """Section 2: state must survive container recreation."""
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "engine_state:/var/lib/quantedge/state" in compose, (
            "engine state must live on a named volume, not in the container "
            "filesystem")
        assert "ENGINE_STATE_DIR: ${ENGINE_STATE_DIR:-/var/lib/quantedge/state}" \
            in compose
        top_level = compose.split("\nvolumes:\n")[-1]
        assert "engine_state:" in top_level, (
            "the named volume must be declared at the top level or Docker will "
            "not create it")
        assert "engine_state:/app" not in compose, (
            "only the state directory is persistent; /app stays immutable")

    def test_the_image_owns_the_state_directory_before_dropping_privileges(self):
        """Docker copies the mount point's ownership onto a fresh volume."""
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.engine").read_text(
            encoding="utf-8")
        assert "mkdir -p /var/lib/quantedge/state" in dockerfile
        assert "chown -R appuser:appgroup /var/lib/quantedge" in dockerfile
        head, _, tail = dockerfile.partition("\nUSER appuser")
        assert tail, "the engine image must drop to the non-root user"
        assert "chown -R appuser:appgroup /var/lib/quantedge" in head, (
            "ownership must be set BEFORE USER appuser, or the volume is "
            "seeded root-owned and save_state fails an hour into operation")
        assert "DELTA_API" not in dockerfile, "no credential is baked in"


# ===========================================================================
# B - no snapshot must never become a silent continuous cold start
# ===========================================================================
class TestB_NoSnapshotNoSilentColdStart:
    """Mandate section 3: no invented warmup, no pretend continuity."""

    def test_no_snapshot_and_no_genesis_is_refused_not_cold_started(
            self, tmp_path):
        state_dir = tmp_path / "state"
        runtime = _fresh()
        with pytest.raises(R.GenesisUnavailableError) as excinfo:
            R.bootstrap_structure_state(
                runtime, state_dir=str(state_dir), genesis_dir=None,
                fetcher=raising_fetcher, now_ts=boot_clock(MAIN_CUT_BAR))
        assert excinfo.value.code == R.INERT_NO_GENESIS_SOURCE
        assert "finite warmup" in str(excinfo.value)
        assert runtime.candles_processed == 0, "nothing was replayed"
        assert not (state_dir / R.STATE_FILE_NAME).exists()
        assert list(state_dir.iterdir()) == [], (
            "a refused bootstrap must not leave a snapshot behind, or the next "
            "restart would restore a state that stands for no history")

    def test_the_configuration_refusal_stays_inert_instead_of_exiting(
            self, tmp_path, monitor):
        """Restarting cannot fix a missing configuration, so it must not exit."""
        with pytest.raises(R.GenesisUnavailableError):
            R.bootstrap_or_fail(
                _fresh(), monitor=monitor, state_dir=str(tmp_path / "state"),
                genesis_dir=None, fetcher=raising_fetcher,
                now_ts=boot_clock(MAIN_CUT_BAR))
        assert monitor.fault is None, "an inert refusal is not a fault"

    def test_an_inert_engine_reports_not_healthy(self, monitor):
        monitor.mark_inert(R.INERT_NO_GENESIS_SOURCE, "no genesis configured")
        assert monitor.is_healthy() is False
        status, body = monitor.health_payload()
        assert status == 503
        payload = json.loads(body)
        assert payload["status"] == "DOWN"
        assert payload["reason"] == R.INERT_NO_GENESIS_SOURCE

    def test_a_year_partition_csv_is_not_accepted_as_an_origin(self, tmp_path):
        """`2026.csv` is a truncated history dressed up as a complete one."""
        genesis = tmp_path / "genesis"
        write_genesis_csv(genesis, SYM, _bars(MAIN_START, MAIN_START + 40),
                          name="2026.csv")
        with pytest.raises(R.GenesisUnavailableError) as excinfo:
            R.bootstrap_structure_state(
                _fresh(), state_dir=str(tmp_path / "state"),
                genesis_dir=str(genesis), fetcher=raising_fetcher,
                now_ts=boot_clock(MAIN_CUT_BAR))
        assert "full_history.csv" in str(excinfo.value)

    def test_an_origin_with_no_closed_candles_is_refused(self, tmp_path):
        """Every row newer than the boundary leaves nothing to replay."""
        genesis = tmp_path / "genesis"
        bars = _bars(MAIN_START, MAIN_START + 40)
        write_genesis_csv(genesis, SYM, bars)
        with pytest.raises(R.GenesisUnavailableError) as excinfo:
            R.bootstrap_structure_state(
                _fresh(), state_dir=str(tmp_path / "state"),
                genesis_dir=str(genesis), fetcher=raising_fetcher,
                now_ts=boot_clock(bars[0].bar_idx - 10))
        assert "no closed" in str(excinfo.value)

    def test_a_non_contiguous_origin_is_refused(self, tmp_path):
        """A hole in the declared origin diverges the ATR recursion forever."""
        genesis = tmp_path / "genesis"
        bars = _bars(MAIN_START, MAIN_START + 40)
        holed = bars[:20] + bars[21:]
        write_genesis_csv(genesis, SYM, holed)
        with pytest.raises(R.HistoryBootstrapError) as excinfo:
            R.bootstrap_structure_state(
                _fresh(), state_dir=str(tmp_path / "state"),
                genesis_dir=str(genesis), fetcher=raising_fetcher,
                now_ts=boot_clock(bars[-1].bar_idx))
        assert "not contiguous" in str(excinfo.value)

    def test_a_declared_origin_is_replayed_from_row_zero_and_persisted(
            self, tmp_path):
        """The genesis POLICY: the operator's declared history, from its first row."""
        genesis = tmp_path / "genesis"
        bars = _bars(MAIN_START, MAIN_START + 300)
        write_genesis_csv(genesis, SYM, bars)
        state_dir = tmp_path / "state"
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(state_dir), genesis_dir=str(genesis),
            fetcher=raising_fetcher, now_ts=boot_clock(bars[-1].bar_idx))
        assert report.mode == R.MODE_GENESIS
        assert report.genesis_first_bar[SYM] == bars[0].bar_idx
        assert report.replayed[SYM] == len(bars)
        assert report.watermarks[SYM] == bars[-1].bar_idx
        assert report.newest_bar_idx == bars[-1].bar_idx
        assert (state_dir / R.STATE_FILE_NAME).is_file(), (
            "genesis must persist, or the next restart repeats the whole replay")
        assert_exact_parity(report.runtime,
                            reference(MAIN_START, 300, 0)["join"],
                            "genesis replay")

    def test_the_next_restart_restores_instead_of_repeating_genesis(
            self, tmp_path):
        genesis = tmp_path / "genesis"
        bars = _bars(MAIN_START, MAIN_START + 300)
        write_genesis_csv(genesis, SYM, bars)
        state_dir = tmp_path / "state"
        common = {"state_dir": str(state_dir), "genesis_dir": str(genesis),
                  "fetcher": raising_fetcher,
                  "now_ts": boot_clock(bars[-1].bar_idx)}
        first = R.bootstrap_structure_state(_fresh(), **common)
        second = R.bootstrap_structure_state(_fresh(), **common)
        assert first.mode == R.MODE_GENESIS
        assert second.mode == R.MODE_RESTORED
        assert second.total_replayed == 0, "nothing was due"
        assert channels(second.runtime) == channels(first.runtime)
        assert _freeze(second.runtime.snapshot()) == _freeze(
            first.runtime.snapshot())


# ===========================================================================
# C - a valid snapshot plus a one-hour gap continues EXACTLY
# ===========================================================================
class TestC_OneHourGap:
    """Mandate C: restore, replay the one missing candle, continue, exact parity."""

    def test_the_cut_carries_the_state_the_parity_test_needs(self, tmp_path):
        """Guard the MEASURED window: a silent data change must not weaken C-N."""
        runtime = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        state = channels(runtime)
        assert state["watermark"] == MAIN_CUT_BAR
        assert state["ob_seq"] == MAIN_CUT_OB_SEQ
        assert len(state["ob_identity"]) == MAIN_CUT_LIVE_OBS
        active = [name for _, name in state["ob_identity"]
                  if name == "TRADE_ACTIVE"]
        assert len(active) == MAIN_CUT_TRADE_ACTIVE_OBS, (
            "the parity window must exercise a TRADE_ACTIVE order block")
        assert state["has_active_trade"] is True, (
            "the parity window must exercise the single-trade slot")
        assert _bars(MAIN_START, MAIN_START + 1)[0].bar_idx == MAIN_FIRST_BAR

    def test_one_missing_candle_is_bridged_and_the_state_is_identical(
            self, tmp_path):
        restart = Restart(tmp_path, 1)
        report = restart.run()
        assert report.mode == R.MODE_RESTORED
        assert report.replayed == {SYM: 1}
        assert report.newest_bar_idx == restart.newest
        assert report.watermarks[SYM] == restart.newest
        assert restart.fetcher.calls == [restart.expected_call], (
            "exactly the missing hour is requested - no wider, no narrower")
        assert_exact_parity(report.runtime,
                            reference(MAIN_START, MAIN_CUT, 1, TAIL)["join"],
                            "1h gap join")

    def test_the_bridged_runtime_keeps_matching_going_forward(self, tmp_path):
        """Parity at the join is not enough: the state must stay identical."""
        restart = Restart(tmp_path, 1)
        report = restart.run()
        tail = _bars(MAIN_START + MAIN_CUT + 1,
                     MAIN_START + MAIN_CUT + 1 + TAIL)
        assert R.replay_bars(report.runtime, tail) == {SYM: TAIL}
        assert_exact_parity(
            report.runtime,
            reference(MAIN_START, MAIN_CUT, 1, TAIL)["after_tail"],
            f"1h gap + {TAIL} live candles")

    def test_the_snapshot_on_disk_matches_the_bridged_runtime(self, tmp_path):
        restart = Restart(tmp_path, 1)
        report = restart.run()
        on_disk = json.loads(restart.state_file.read_text(encoding="utf-8"))
        assert on_disk == report.runtime.snapshot(), (
            "the persisted snapshot must be the state that was just bridged")
        assert restart.state_file.read_bytes() != restart.seed_bytes


# ===========================================================================
# D - 2h, 3h, 6h and 24h gaps continue EXACTLY
# ===========================================================================
class TestD_MultiHourGaps:
    """Mandate D. Same exactness requirement, longer outages."""

    @pytest.mark.parametrize("gap", [g for g in GAPS if g != 1])
    def test_a_multi_hour_gap_is_bridged_exactly(self, tmp_path, gap):
        restart = Restart(tmp_path, gap)
        report = restart.run()
        assert report.replayed == {SYM: gap}
        assert restart.fetcher.calls == [restart.expected_call]
        assert_exact_parity(report.runtime,
                            reference(MAIN_START, MAIN_CUT, gap, TAIL)["join"],
                            f"{gap}h gap join")

    @pytest.mark.parametrize("gap", [g for g in GAPS if g != 1])
    def test_a_multi_hour_gap_keeps_matching_going_forward(self, tmp_path, gap):
        restart = Restart(tmp_path, gap)
        report = restart.run()
        tail = _bars(MAIN_START + MAIN_CUT + gap,
                     MAIN_START + MAIN_CUT + gap + TAIL)
        assert R.replay_bars(report.runtime, tail) == {SYM: TAIL}
        assert_exact_parity(
            report.runtime,
            reference(MAIN_START, MAIN_CUT, gap, TAIL)["after_tail"],
            f"{gap}h gap + {TAIL} live candles")

    def test_nothing_is_fetched_when_structure_is_already_current(self, tmp_path):
        """The boundary case between the gaps: a zero-hour outage."""
        state_dir = seed_state_dir(tmp_path)
        fetcher = FakeFetcher({})
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(state_dir), genesis_dir=None,
            fetcher=fetcher, now_ts=boot_clock(MAIN_CUT_BAR))
        assert report.total_replayed == 0
        assert fetcher.call_count == 0, (
            "an up-to-date engine must not ask for a range it does not need")
        assert report.watermarks[SYM] == MAIN_CUT_BAR
        assert_exact_parity(report.runtime,
                            reference(MAIN_START, MAIN_CUT, 0)["join"],
                            "no gap")


# ===========================================================================
# E - a gap that crosses 31 Dec -> 1 Jan
# ===========================================================================
class TestE_YearBoundaryGap:
    """Mandate section 4: the bootstrap must "support a gap crossing calendar years"."""

    def test_the_measured_window_really_does_cross_new_year(self):
        gap_bars = _bars(YEAR_START + YEAR_CUT,
                         YEAR_START + YEAR_CUT + YEAR_GAP)
        assert gap_bars[0].bar_idx == YEAR_GAP_FIRST_BAR
        assert gap_bars[-1].bar_idx == YEAR_GAP_LAST_BAR
        assert {bar.ts.year for bar in gap_bars} == {2025, 2026}, (
            "this window is only a year-boundary test if it holds both years")
        stamps = {bar.ts.isoformat() for bar in gap_bars}
        # The two calendar edges themselves, not merely one hour either side.
        assert "2025-12-31T00:00:00+00:00" in stamps
        assert "2026-01-01T00:00:00+00:00" in stamps

    def test_the_year_boundary_gap_is_bridged_with_exact_parity(self, tmp_path):
        restart = Restart(tmp_path, YEAR_GAP, start=YEAR_START, cut=YEAR_CUT)
        report = restart.run()
        assert report.mode == R.MODE_RESTORED
        assert report.replayed == {SYM: YEAR_GAP}
        assert restart.fetcher.calls == [restart.expected_call], (
            "exactly one range request, for exactly the missing hours")
        assert report.watermarks[SYM] == YEAR_GAP_LAST_BAR
        assert_exact_parity(
            report.runtime,
            reference(YEAR_START, YEAR_CUT, YEAR_GAP)["join"],
            f"{YEAR_GAP}h gap across 31 Dec -> 1 Jan")

    def test_structure_keeps_matching_after_the_year_boundary_bridge(
            self, tmp_path):
        restart = Restart(tmp_path, YEAR_GAP, start=YEAR_START, cut=YEAR_CUT)
        report = restart.run()
        tail = _bars(YEAR_START + YEAR_CUT + YEAR_GAP,
                     YEAR_START + YEAR_CUT + YEAR_GAP + TAIL)
        assert R.replay_bars(report.runtime, tail) == {SYM: TAIL}
        assert_exact_parity(
            report.runtime,
            reference(YEAR_START, YEAR_CUT, YEAR_GAP, TAIL)["after_tail"],
            f"{YEAR_GAP}h year-boundary gap + {TAIL} live candles")

    # -- the transport underneath, faked at `_fetch_window` ------------------
    def _fake_window(self, monkeypatch, rows, extra=()):
        """Replace ONLY the HTTP leaf. The paging logic under test stays real."""
        windows: List[Tuple[int, int, str]] = []

        def fake_window(start_ts, end_ts, symbol=ingestion.SYMBOL_EXCHANGE):
            windows.append((start_ts, end_ts, symbol))
            return [row for row in rows
                    if start_ts <= row["time"] <= end_ts] + list(extra)

        monkeypatch.setattr(ingestion, "_fetch_window", fake_window)
        return windows

    def test_the_bootstrap_transport_returns_both_calendar_years(
            self, monkeypatch):
        rows = rows_from_bars(_bars(YEAR_START + YEAR_CUT,
                                    YEAR_START + YEAR_CUT + YEAR_GAP))
        windows = self._fake_window(monkeypatch, rows)
        got = ingestion.fetch_closed_candle_range(
            YEAR_GAP_FIRST_BAR * HOUR, YEAR_GAP_LAST_BAR * HOUR, SYM,
            now_ts=boot_clock(YEAR_GAP_LAST_BAR), sleep_between_windows=0)
        assert [row["time"] for row in got] == [
            bar * HOUR
            for bar in range(YEAR_GAP_FIRST_BAR, YEAR_GAP_LAST_BAR + 1)], (
            "the bootstrap range must come back whole, December half included")
        assert {datetime.fromtimestamp(row["time"], tz=timezone.utc).year
                for row in got} == {2025, 2026}
        assert windows, "the real paging loop must have run"
        assert min(win[0] for win in windows) == YEAR_GAP_FIRST_BAR * HOUR, (
            "the December half must actually be requested, not bounded away")

    def test_the_existing_canonical_year_partition_is_untouched(
            self, monkeypatch):
        """`fetch_closed_candles` keeps its 2026 gate; only the new path is year-blind."""
        assert ingestion.MIN_CANONICAL_YEAR_START_TS == int(
            datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        rows = rows_from_bars(_bars(YEAR_START + YEAR_CUT,
                                    YEAR_START + YEAR_CUT + YEAR_GAP))
        december = [row for row in rows
                    if row["time"] < ingestion.MIN_CANONICAL_YEAR_START_TS]
        assert december, "the fixture must contain 2025 rows to be a real test"
        # The exchange hands back a December row that was never asked for.
        windows = self._fake_window(monkeypatch, rows, extra=december[-1:])
        got = ingestion.fetch_closed_candles(
            YEAR_GAP_FIRST_BAR * HOUR, YEAR_GAP_LAST_BAR * HOUR, SYM)
        assert {datetime.fromtimestamp(row["time"], tz=timezone.utc).year
                for row in got} == {2026}, "the 2026 partition still holds"
        assert min(win[0] for win in windows) == (
            ingestion.MIN_CANONICAL_YEAR_START_TS), (
            "the canonical start bound is still enforced before the request")

# ===========================================================================
# F - a truncated fetch is refused
# ===========================================================================
class TestF_TruncatedFetchRefused:
    """Mandate section 4: "never silently truncate"; partial history is not history."""

    GAP = 6

    @pytest.mark.parametrize("mutate, expected", [
        (lambda rows: rows[1:], "truncated at the oldest end"),
        (lambda rows: rows[:-1], "truncated at the newest end"),
        (lambda rows: rows[2:-2], "truncated at the"),
        (lambda rows: [], "refusing to treat an empty result as an "
                          "up-to-date history"),
    ])
    def test_a_short_range_is_refused(self, tmp_path, mutate, expected):
        restart = Restart(tmp_path, self.GAP, mutate=mutate)
        with pytest.raises(R.HistoryBootstrapError) as raised:
            restart.run()
        assert expected in str(raised.value)

    def test_a_refused_range_leaves_the_durable_snapshot_untouched(
            self, tmp_path):
        """No half-bridged state may be written: the next boot must still resume."""
        restart = Restart(tmp_path, self.GAP, mutate=lambda rows: rows[:-1])
        with pytest.raises(R.HistoryBootstrapError):
            restart.run()
        assert restart.state_file.read_bytes() == restart.seed_bytes
        resumed = ManualSMCRuntime.load_state(restart.state_file)
        assert resumed.strategy.watermark.last(SYM).bar_idx == MAIN_CUT_BAR
        assert_exact_parity(resumed,
                            reference(MAIN_START, MAIN_CUT, 0)["join"],
                            "snapshot after a refused truncated fetch")

    def test_a_truncated_range_is_a_fatal_startup_condition(
            self, tmp_path, monitor):
        restart = Restart(tmp_path, self.GAP, mutate=lambda rows: rows[1:])
        with pytest.raises(SystemExit) as raised:
            restart.run_or_fail(monitor)
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE == 2
        assert monitor.fault is not None
        assert monitor.fault[0] == R.FAULT_BOOTSTRAP_FAILED
        assert "truncated at the oldest end" in monitor.fault[1]
        assert not monitor.is_healthy()
        assert monitor.health_payload()[0] == 503

# ===========================================================================
# G - a non-contiguous fetch is refused
# ===========================================================================
def _drop_middle(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove one interior hour: first and last still match, the series does not."""
    return rows[:2] + rows[3:]


def _duplicate_middle(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return rows[:2] + [dict(rows[2])] + rows[2:]


class TestG_NonContiguousFetchRefused:
    """Mandate section 4: verify no duplicates, hourly contiguity, chronological order."""

    GAP = 6

    def test_an_interior_hole_is_refused(self, tmp_path):
        restart = Restart(tmp_path, self.GAP, mutate=_drop_middle)
        with pytest.raises(R.HistoryBootstrapError) as raised:
            restart.run()
        message = str(raised.value)
        assert "the hourly series is not contiguous" in message
        assert "1 closed candle(s) missing" in message

    def test_a_duplicated_hour_is_refused(self, tmp_path):
        restart = Restart(tmp_path, self.GAP, mutate=_duplicate_middle)
        with pytest.raises(R.HistoryBootstrapError) as raised:
            restart.run()
        assert "duplicate closed candle at bar" in str(raised.value)

    def test_an_interior_hole_is_a_fatal_startup_condition(
            self, tmp_path, monitor):
        restart = Restart(tmp_path, self.GAP, mutate=_drop_middle)
        with pytest.raises(SystemExit) as raised:
            restart.run_or_fail(monitor)
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert monitor.fault[0] == R.FAULT_BOOTSTRAP_FAILED
        assert restart.state_file.read_bytes() == restart.seed_bytes

    def test_out_of_order_history_is_refused(self):
        """`bars_from_delta_rows` sorts, so this refusal is proved at its own layer."""
        bars = _bars(MAIN_START, MAIN_START + 4)
        with pytest.raises(R.HistoryBootstrapError) as raised:
            R.verify_bar_series(SYM, list(reversed(bars)))
        assert "not in chronological order" in str(raised.value)

    def test_a_foreign_symbol_row_is_refused(self):
        bars = list(_bars(MAIN_START, MAIN_START + 3))
        intruder = bars[1]
        bars[1] = R.ClosedBar(
            symbol="BTCUSD", bar_idx=intruder.bar_idx, ts=intruder.ts,
            open=intruder.open, high=intruder.high, low=intruder.low,
            close=intruder.close, volume=intruder.volume)
        with pytest.raises(R.HistoryBootstrapError) as raised:
            R.verify_bar_series(SYM, bars)
        assert "history contains a row for" in str(raised.value)

    # -- malformed rows are refused, never skipped (section 4) ---------------
    @pytest.mark.parametrize("row, expected", [
        (["not", "a", "mapping"], "is not a candle mapping"),
        ({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
         "is not a candle mapping"),
        ({"time": "half past three", "open": 1.0, "high": 1.0, "low": 1.0,
          "close": 1.0}, "non-integer time"),
        ({"time": 489000 * HOUR + 1, "open": 1.0, "high": 1.0, "low": 1.0,
          "close": 1.0}, "is not on a 1h boundary"),
        ({"time": 489000 * HOUR, "open": "n/a", "high": 1.0, "low": 1.0,
          "close": 1.0}, "non-numeric open"),
        ({"time": 489000 * HOUR, "open": 1.0, "high": float("nan"),
          "low": 1.0, "close": 1.0}, "non-finite or non-positive high"),
        ({"time": 489000 * HOUR, "open": 1.0, "high": 1.0, "low": 0.0,
          "close": 1.0}, "non-finite or non-positive low"),
        ({"time": 489000 * HOUR, "open": 1.0, "high": 1.0, "low": 1.0,
          "close": -5.0}, "non-finite or non-positive close"),
    ])
    def test_a_malformed_row_is_refused(self, row, expected):
        with pytest.raises(R.HistoryBootstrapError) as raised:
            R.bars_from_delta_rows(SYM, [row])
        assert expected in str(raised.value)

    def test_a_malformed_row_inside_the_bridge_is_fatal(self, tmp_path, monitor):
        def corrupt(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            broken = dict(rows[2])
            broken["close"] = None
            return rows[:2] + [broken] + rows[3:]

        restart = Restart(tmp_path, self.GAP, mutate=corrupt)
        with pytest.raises(SystemExit) as raised:
            restart.run_or_fail(monitor)
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert "non-numeric close" in monitor.fault[1]

# ===========================================================================
# H - a failed fetch is a failure, never an empty success
# ===========================================================================
class TestH_FetchExceptionRefused:
    """Mandate section 4: "never silently truncate on request failure"."""

    GAP = 3

    def test_a_transport_exception_becomes_a_bootstrap_refusal(self, tmp_path):
        restart = Restart(tmp_path, self.GAP,
                          raises=ConnectionResetError("socket went away"))
        with pytest.raises(R.HistoryBootstrapError) as raised:
            restart.run()
        message = str(raised.value)
        assert "a failed fetch is never a complete history" in message
        assert "ConnectionResetError" in message, (
            "the underlying cause must survive into the operator's log line")

    def test_a_transport_exception_is_fatal_and_persists_nothing(
            self, tmp_path, monitor):
        restart = Restart(tmp_path, self.GAP,
                          raises=TimeoutError("read timed out"))
        with pytest.raises(SystemExit) as raised:
            restart.run_or_fail(monitor)
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert monitor.fault[0] == R.FAULT_BOOTSTRAP_FAILED
        assert restart.state_file.read_bytes() == restart.seed_bytes, (
            "a failed boot must not overwrite the snapshot it could not extend")

    def test_the_real_range_transport_raises_instead_of_returning_short(
            self, monkeypatch):
        """`fetch_closed_candle_range` must not degrade a dead window into a short list."""
        def exploding_window(start_ts, end_ts, symbol=ingestion.SYMBOL_EXCHANGE):
            raise ConnectionError("HTTPSConnectionPool: max retries exceeded")

        monkeypatch.setattr(ingestion, "_fetch_window", exploding_window)
        with pytest.raises(ingestion.HistoryRangeError) as raised:
            ingestion.fetch_closed_candle_range(
                MAIN_CUT_BAR * HOUR, (MAIN_CUT_BAR + 5) * HOUR, SYM,
                now_ts=boot_clock(MAIN_CUT_BAR + 5), sleep_between_windows=0)
        assert "failed" in str(raised.value)
        assert "ConnectionError" in str(raised.value)

    @pytest.mark.parametrize("row, expected", [
        ({"open": 1.0}, "malformed candle in window"),
        ({"time": "noon"}, "non-integer candle time"),
        ({"time": MAIN_CUT_BAR * HOUR + 60}, "is not hour-aligned"),
    ])
    def test_the_real_range_transport_rejects_malformed_rows(
            self, monkeypatch, row, expected):
        monkeypatch.setattr(
            ingestion, "_fetch_window",
            lambda start_ts, end_ts, symbol=None: [row])
        with pytest.raises(ingestion.HistoryRangeError) as raised:
            ingestion.fetch_closed_candle_range(
                MAIN_CUT_BAR * HOUR, (MAIN_CUT_BAR + 5) * HOUR, SYM,
                now_ts=boot_clock(MAIN_CUT_BAR + 5), sleep_between_windows=0)
        assert expected in str(raised.value)

    def test_unaligned_range_bounds_are_refused_before_any_request(
            self, monkeypatch):
        monkeypatch.setattr(ingestion, "_fetch_window", raising_fetcher)
        with pytest.raises(ingestion.HistoryRangeError) as raised:
            ingestion.fetch_closed_candle_range(
                MAIN_CUT_BAR * HOUR + 1, (MAIN_CUT_BAR + 5) * HOUR, SYM,
                now_ts=boot_clock(MAIN_CUT_BAR + 5), sleep_between_windows=0)
        assert "must be hour-aligned" in str(raised.value)

# ===========================================================================
# I - a skipped cold-start backfill is not a success
# ===========================================================================
def _newest_closed_now() -> int:
    """The newest fully closed hour by the REAL clock, as `_backfill_symbol` sees it."""
    now = int(time.time())
    return (now - now % 3600) - 3600


def _rest_rows(hours: Sequence[int]) -> List[Dict[str, Any]]:
    """Raw Delta REST rows at the given absolute epoch seconds."""
    return [{"time": hour, "open": 100.0 + i, "high": 101.0 + i,
             "low": 99.0 + i, "close": 100.5 + i, "volume": 12.0}
            for i, hour in enumerate(hours)]


class BackfillHarness:
    """One `_backfill_symbol` run with the transport faked and events collected.

    Nothing here dials out: `fetch_closed_candles` and `upsert_closed_candles`
    are both replaced in the module namespace the coroutine resolves them from.
    """

    def __init__(self, tmp_path: Path, monkeypatch, *, csv_name: str = "probe.csv",
                 persist: bool = False) -> None:
        self.client = dws.DeltaWebSocketClient(
            persist=persist,
            csv_path=tmp_path / csv_name,
            meta_path=tmp_path / "probe.meta.json",
            on_candle_closed=None,
            engine=None)
        self.exchange = self.client._exchange_symbols[0]
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.client._log_event = (
            lambda event_type, **kw: self.events.append((event_type, kw)))
        self.monkeypatch = monkeypatch
        self.fetch_calls: List[Tuple[Any, ...]] = []

    def fetch(self, rows=None, raises=None):
        def fake_fetch(start_ts, end_ts, symbol):
            self.fetch_calls.append((start_ts, end_ts, symbol))
            if raises is not None:
                raise raises
            return list(rows or ())
        self.monkeypatch.setattr(dws, "fetch_closed_candles", fake_fetch)
        return self

    def persistence_fails(self, exc: Exception):
        def boom(*_args, **_kwargs):
            raise exc
        self.monkeypatch.setattr(dws, "upsert_closed_candles", boom)
        return self

    def watermark(self, last_closed: Optional[int]):
        self.client._last_closed[self.exchange] = last_closed
        return self

    def already_processed(self, hours: Sequence[int]):
        """Absorb an hour rollover mid-test without loosening any assertion."""
        self.client._processed[self.exchange].update(hours)
        return self

    def run(self) -> Dict[str, Any]:
        asyncio.run(self.client._backfill_symbol(self.exchange))
        completed = [kw for name, kw in self.events
                     if name == dws.EVENT_BACKFILL_COMPLETED]
        assert len(completed) == 1, (
            "exactly one BACKFILL_COMPLETED event per run")
        return completed[0]

class TestI_BackfillResultCorrectness:
    """Mandate section 8: only a complete backfill may report `success=True`."""

    def test_only_complete_and_up_to_date_count_as_success(self):
        assert dws.BACKFILL_SUCCESS_OUTCOMES == frozenset(
            {dws.BACKFILL_COMPLETE, dws.BACKFILL_UP_TO_DATE})
        for outcome in (dws.BACKFILL_NO_WATERMARK, dws.BACKFILL_FETCH_FAILED,
                        dws.BACKFILL_PERSIST_FAILED,
                        dws.BACKFILL_INCOMPLETE_RANGE,
                        dws.BACKFILL_YEAR_PARTITION_REJECTED):
            assert outcome not in dws.BACKFILL_SUCCESS_OUTCOMES

    def test_a_cold_start_skip_is_not_reported_as_success(
            self, tmp_path, monkeypatch):
        """THE section 8 case: no watermark means nothing was attempted."""
        harness = BackfillHarness(tmp_path, monkeypatch).fetch(rows=[])
        harness.watermark(None)
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_NO_WATERMARK
        assert event["success"] is False
        assert harness.fetch_calls == [], (
            "a cold start must not even ask for a range it cannot bound")

    def test_a_failed_fetch_is_not_reported_as_success(
            self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        harness = BackfillHarness(tmp_path, monkeypatch)
        harness.fetch(raises=ConnectionError("max retries exceeded"))
        harness.watermark(newest - 5 * HOUR)
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_FETCH_FAILED
        assert event["success"] is False
        assert any(name == dws.EVENT_BACKFILL_ERROR
                   for name, _ in harness.events)

    def test_an_empty_reply_with_hours_due_is_incomplete_not_up_to_date(
            self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        harness = BackfillHarness(tmp_path, monkeypatch).fetch(rows=[])
        harness.watermark(newest - 10 * HOUR)
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_INCOMPLETE_RANGE
        assert event["success"] is False

    def test_nothing_due_is_up_to_date_and_may_report_success(
            self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        harness = BackfillHarness(tmp_path, monkeypatch).fetch(rows=[])
        harness.watermark(newest).already_processed(
            [newest + n * HOUR for n in (1, 2, 3)])
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_UP_TO_DATE
        assert event["success"] is True

    def test_a_fully_covered_range_reports_complete(self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        due = [newest - n * HOUR for n in (4, 3, 2, 1, 0)]
        harness = BackfillHarness(tmp_path, monkeypatch)
        harness.fetch(rows=_rest_rows(due))
        harness.watermark(newest - 5 * HOUR).already_processed(
            [newest + n * HOUR for n in (1, 2, 3)])
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_COMPLETE
        assert event["success"] is True
        assert harness.client._last_closed[harness.exchange] == newest, (
            "the watermark advances only for candles that were really accepted")

    def test_a_hole_in_a_non_empty_reply_reports_incomplete(
            self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        # The 3h-old hour is simply absent: a real exchange hole.
        due = [newest - n * HOUR for n in (4, 2, 1, 0)]
        harness = BackfillHarness(tmp_path, monkeypatch)
        harness.fetch(rows=_rest_rows(due))
        harness.watermark(newest - 5 * HOUR).already_processed(
            [newest + n * HOUR for n in (1, 2, 3)])
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_INCOMPLETE_RANGE
        assert event["success"] is False

    def test_a_year_rejected_batch_is_not_reported_as_success(
            self, tmp_path, monkeypatch):
        """A 2026-partitioned CSV must not silently swallow a 2025 candle."""
        newest = _newest_closed_now()
        harness = BackfillHarness(tmp_path, monkeypatch, csv_name="2026.csv")
        harness.fetch(rows=_rest_rows([YEAR_GAP_FIRST_BAR * HOUR]))
        harness.watermark(newest - HOUR)
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_YEAR_PARTITION_REJECTED
        assert event["success"] is False

    def test_a_persistence_failure_is_not_reported_as_success(
            self, tmp_path, monkeypatch):
        newest = _newest_closed_now()
        due = [newest - n * HOUR for n in (4, 3, 2, 1, 0)]
        harness = BackfillHarness(tmp_path, monkeypatch, persist=True)
        harness.fetch(rows=_rest_rows(due))
        harness.persistence_fails(OSError("read-only file system"))
        harness.watermark(newest - 5 * HOUR)
        event = harness.run()
        assert event["outcome"] == dws.BACKFILL_PERSIST_FAILED
        assert event["success"] is False
        assert harness.client._last_closed[harness.exchange] == (
            newest - 5 * HOUR), (
            "an unpersisted candle must not advance the watermark")

# ===========================================================================
# J - a persistence failure fails closed
# ===========================================================================
class _StubPersister:
    """The smallest object `persist_state` needs: one writer and one snapshot.

    Deliberately not a real runtime: these three branches are about the write
    and the read-back, and a stub cannot accidentally succeed for the wrong
    reason.
    """

    def __init__(self, *, snapshot: Dict[str, Any],
                 raise_on_save: Optional[Exception] = None,
                 write: Optional[str] = None) -> None:
        self._snapshot = snapshot
        self._raise = raise_on_save
        self._write = write

    def save_state(self, path: Any) -> Path:
        if self._raise is not None:
            raise self._raise
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            self._write if self._write is not None
            else json.dumps(self._snapshot), encoding="utf-8")
        return target

    def snapshot(self) -> Dict[str, Any]:
        return self._snapshot


class TestJ_PersistenceFailureFailsClosed:
    """Mandate section 6: never report successful structure processing without durability."""

    def test_a_write_failure_is_refused(self, tmp_path):
        stub = _StubPersister(snapshot={"a": 1},
                              raise_on_save=OSError("no space left on device"))
        with pytest.raises(R.StatePersistenceError) as raised:
            R.persist_state(stub, tmp_path / "state.json")
        message = str(raised.value)
        assert "could not write the durable snapshot" in message
        assert "refusing to continue as if durable state existed" in message
        assert raised.value.code == R.FAULT_STATE_PERSISTENCE_FAILED

    def test_an_unreadable_write_is_refused(self, tmp_path):
        stub = _StubPersister(snapshot={"a": 1}, write="{ this is not json")
        with pytest.raises(R.StatePersistenceError) as raised:
            R.persist_state(stub, tmp_path / "state.json")
        assert "could not be read back" in str(raised.value)

    def test_a_torn_write_is_refused(self, tmp_path):
        """Bytes on disk that are not the state in memory are not durability."""
        stub = _StubPersister(snapshot={"a": 1}, write=json.dumps({"a": 2}))
        with pytest.raises(R.StatePersistenceError) as raised:
            R.persist_state(stub, tmp_path / "state.json")
        assert "does not match the in-memory state" in str(raised.value)

    def test_a_real_runtime_round_trips_through_persist_state(self, tmp_path):
        """The positive control: the same helper accepts the production writer."""
        runtime = ManualSMCRuntime.load_state(seed_state_dir(tmp_path)
                                              / R.STATE_FILE_NAME)
        target = tmp_path / "written" / R.STATE_FILE_NAME
        R.persist_state(runtime, target)
        assert json.loads(target.read_text(encoding="utf-8")) == (
            runtime.snapshot())

    @staticmethod
    def _break_save(monkeypatch):
        def boom(self, path=None):
            raise OSError("read-only file system")
        monkeypatch.setattr(ManualSMCRuntime, "save_state", boom)

    def test_a_bootstrap_that_cannot_persist_is_fatal(
            self, tmp_path, monkeypatch, monitor):
        # Built BEFORE the patch, so the seed itself is written by the real
        # `save_state` and only the bootstrap's own write is broken.
        restart = Restart(tmp_path, 2)
        self._break_save(monkeypatch)
        with pytest.raises(SystemExit) as raised:
            restart.run_or_fail(monitor)
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert monitor.fault[0] == R.FAULT_STATE_PERSISTENCE_FAILED
        assert "refusing to continue as if durable state existed" in (
            monitor.fault[1])

    def test_a_live_candle_that_cannot_be_persisted_is_fatal(
            self, tmp_path, monkeypatch, monitor):
        state_dir = seed_state_dir(tmp_path)
        state_path = state_dir / R.STATE_FILE_NAME
        runtime = ManualSMCRuntime.load_state(state_path)
        callback = R.make_durable_feed_callback(
            runtime, make_feed_callback(runtime, submit_to=None),
            state_path=state_path, monitor=monitor)
        successor = _bars(MAIN_START + MAIN_CUT, MAIN_START + MAIN_CUT + 1)[0]
        self._break_save(monkeypatch)
        with pytest.raises(SystemExit) as raised:
            callback(successor.payload(runtime.timeframe))
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert monitor.fault[0] == R.FAULT_STATE_PERSISTENCE_FAILED
        assert not monitor.is_healthy(), (
            "a processed candle that was never persisted must not read healthy")

    def test_a_live_candle_that_persists_keeps_the_snapshot_current(
            self, tmp_path, monitor):
        """The positive control for section 6: persist after an ACCEPTED candle."""
        state_dir = seed_state_dir(tmp_path)
        state_path = state_dir / R.STATE_FILE_NAME
        runtime = ManualSMCRuntime.load_state(state_path)
        callback = R.make_durable_feed_callback(
            runtime, make_feed_callback(runtime, submit_to=None),
            state_path=state_path, monitor=monitor)
        successor = _bars(MAIN_START + MAIN_CUT, MAIN_START + MAIN_CUT + 1)[0]
        step = callback(successor.payload(runtime.timeframe))
        assert step is not None and step.skipped is None
        assert json.loads(state_path.read_text(encoding="utf-8")) == (
            runtime.snapshot())
        assert runtime.strategy.watermark.last(SYM).bar_idx == MAIN_CUT_BAR + 1
        assert monitor.is_healthy()

# ===========================================================================
# K - the existing integrity checks still refuse a tampered snapshot
# ===========================================================================
def tampered_state_dir(tmp_path: Path, mutate: Any, *, name: str) -> Path:
    """A durable directory whose snapshot has been edited on disk."""
    body = json.loads(seed_text(tmp_path))
    mutate(body)
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / R.STATE_FILE_NAME).write_text(
        json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    return directory


def _flip_close(body: Dict[str, Any]) -> None:
    """A single OHLC value in the retained LuxAlgo bar window.

    Bars are encoded positionally as [bar_idx, open, high, low, close, ts], so
    index 4 is the close and index 3 the low.
    """
    bar = body["strategy"]["scanners"][0]["bars"][-1]
    bar[4] = bar[3] if bar[4] != bar[3] else bar[2]


TAMPERS = [
    ("dropped_field", lambda b: b.pop("symbols"), RuntimeStateError,
     "do not match the expected"),
    ("added_field", lambda b: b.update(surprise=1), RuntimeStateError,
     "do not match the expected"),
    ("foreign_envelope",
     lambda b: b.update(wiring_schema="SOME_OTHER_ENGINE"),
     RuntimeStateError, "is not a Manual SMC runtime"),
    ("future_version",
     lambda b: b.update(wiring_schema_version=99), RuntimeStateError,
     "unsupported snapshot version"),
    ("forged_bar_digest",
     lambda b: b["strategy"]["scanners"][0]["digest"].update(
         bars_sha256="0" * 64), StateIntegrityError, ""),
    ("edited_candle", _flip_close, StateIntegrityError, ""),
    ("forged_ob_seq",
     lambda b: b["strategy"]["scanners"][0]["structure"].update(ob_seq=999),
     StateIntegrityError, ""),
]


class TestK_TamperedSnapshotRefused:
    """Mandate test K: the pre-existing integrity checks remain enforced."""

    @pytest.mark.parametrize("label, mutate, error, fragment", TAMPERS,
                             ids=[case[0] for case in TAMPERS])
    def test_a_tampered_snapshot_is_refused_by_load_state(
            self, tmp_path, label, mutate, error, fragment):
        directory = tampered_state_dir(tmp_path, mutate, name=label)
        with pytest.raises(error) as raised:
            ManualSMCRuntime.load_state(directory / R.STATE_FILE_NAME)
        if fragment:
            assert fragment in str(raised.value)

    @pytest.mark.parametrize("label, mutate, error, fragment", TAMPERS,
                             ids=[case[0] for case in TAMPERS])
    def test_a_tampered_snapshot_is_a_fatal_startup_condition(
            self, tmp_path, monitor, label, mutate, error, fragment):
        directory = tampered_state_dir(tmp_path, mutate, name=label)
        with pytest.raises(SystemExit) as raised:
            R.bootstrap_or_fail(
                _fresh(), monitor=monitor, state_dir=str(directory),
                genesis_dir=None, fetcher=raising_fetcher,
                now_ts=boot_clock(MAIN_CUT_BAR))
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert monitor.fault[0] == R.FAULT_BOOTSTRAP_FAILED
        assert error.__name__ in monitor.fault[1], (
            "the operator must see which integrity check refused the snapshot")

    def test_an_unparsable_snapshot_is_a_fatal_startup_condition(
            self, tmp_path, monitor):
        directory = tmp_path / "shredded"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / R.STATE_FILE_NAME).write_text(
            seed_text(tmp_path)[:400], encoding="utf-8")
        with pytest.raises(SystemExit) as raised:
            R.bootstrap_or_fail(
                _fresh(), monitor=monitor, state_dir=str(directory),
                genesis_dir=None, fetcher=raising_fetcher,
                now_ts=boot_clock(MAIN_CUT_BAR))
        assert raised.value.code == R.STRUCTURE_WEDGE_EXIT_CODE
        assert not monitor.is_healthy()

    def test_the_untampered_seed_is_accepted(self, tmp_path):
        """The control: the same file, unedited, restores exactly."""
        runtime = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        assert_exact_parity(runtime,
                            reference(MAIN_START, MAIN_CUT, 0)["join"],
                            "untampered seed")

# ===========================================================================
# L - an operator invalidation survives a restart and cannot be undone by one
# ===========================================================================
#: Fixed so the restarted process and the continuous reference record the SAME
#: decision. `invalidate_ob` would otherwise stamp `datetime.now`, and two
#: different stamps are two different snapshots.
FIXED_REQUEST = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

_LREF: Dict[Tuple[int, int], Dict[str, Any]] = {}


def _invalidation_target(runtime: ManualSMCRuntime) -> str:
    """The lowest-id OB that is not the live trade - an operator's own pick.

    Deliberately NOT the TRADE_ACTIVE one: this task changes no trading rule,
    and section 12 keeps execution out of scope, so the live trade is left alone.
    """
    live = runtime.strategy.lifecycle.live_obs
    candidates = sorted(ob_id for ob_id, ob in live.items()
                        if ob.state.name == "AWAITING_DISPLACEMENT")
    assert candidates, (
        "the measured cut must hold a non-trading OB for the operator to invalidate")
    return candidates[0]


def invalidated_reference(gap: int, tail: int) -> Dict[str, Any]:
    """An uninterrupted process that made the same decision at the same bar."""
    key = (gap, tail)
    if key not in _LREF:
        runtime = _fresh()
        R.replay_bars(runtime, _bars(MAIN_START, MAIN_START + MAIN_CUT))
        target = _invalidation_target(runtime)
        result = runtime.invalidate_ob(SYM, target, requested_at=FIXED_REQUEST)
        R.replay_bars(runtime, _bars(MAIN_START + MAIN_CUT,
                                     MAIN_START + MAIN_CUT + gap + tail))
        _LREF[key] = {"channels": channels(runtime),
                      "snapshot": _freeze(runtime.snapshot()),
                      "target": target,
                      "fingerprint": result["fingerprint"]}
    return _LREF[key]


def invalidated_state_dir(tmp_path: Path) -> Tuple[Path, str, Dict[str, Any]]:
    """Restore the seed, invalidate one OB, and persist that decision."""
    runtime = ManualSMCRuntime.load_state(
        seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
    target = _invalidation_target(runtime)
    result = runtime.invalidate_ob(SYM, target, requested_at=FIXED_REQUEST)
    directory = tmp_path / "invalidated"
    directory.mkdir(parents=True, exist_ok=True)
    R.persist_state(runtime, directory / R.STATE_FILE_NAME)
    return directory, target, result

class TestL_OperatorInvalidationSurvives:
    """Mandate L: invalidate, snapshot, restart, replay - it stays invalidated.

    The decision is keyed on a rescan-invariant fingerprint built from the order
    block's TIMESTAMPS, so replaying the gap re-derives the same order block from
    the same candles and the ledger must still refuse it. A restart that quietly
    resurrected an operator-invalidated setup would put a setup the operator
    banned back in front of the execution boundary.
    """

    GAP = 6

    def test_the_invalidation_is_recorded_before_the_restart(self, tmp_path):
        directory, target, result = invalidated_state_dir(tmp_path)
        runtime = ManualSMCRuntime.load_state(directory / R.STATE_FILE_NAME)
        lifecycle = runtime.strategy.lifecycle
        assert result["created"] is True
        assert result["evicted_ob_id"] == target
        # `live_trade_untouched=True` means the named block was TRADE_ACTIVE and
        # its open position was deliberately left alone (lifecycle.py:630-633).
        # This target is an AWAITING_DISPLACEMENT block on purpose, so the flag
        # is False and the block really was evicted.
        assert result["live_trade_untouched"] is False
        assert len(result["cancel_setup_ids"]) == 1
        assert result["requested_at"] == FIXED_REQUEST
        assert result["fingerprint"] in lifecycle.invalidations.fingerprints
        assert target not in lifecycle.live_obs

    def test_the_invalidated_ob_stays_invalidated_across_restart(self, tmp_path):
        directory, target, result = invalidated_state_dir(tmp_path)
        gap_bars = _bars(MAIN_START + MAIN_CUT,
                         MAIN_START + MAIN_CUT + self.GAP)
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(directory), genesis_dir=None,
            fetcher=FakeFetcher({SYM: gap_bars}),
            now_ts=boot_clock(gap_bars[-1].bar_idx))
        lifecycle = report.runtime.strategy.lifecycle
        ledger = lifecycle.invalidations
        assert report.mode == R.MODE_RESTORED
        assert result["fingerprint"] in ledger.fingerprints
        assert ledger.contains(result["fingerprint"])
        assert target not in lifecycle.live_obs
        assert target not in {ob.ob_id for ob in report.runtime.active_obs()}
        # The stronger statement: no order block that survived the bridge is
        # covered by the ledger, whatever id the rescan gave it.
        assert not [ob for ob in report.runtime.active_obs()
                    if ledger.covers(ob)]

    def test_the_bridge_matches_a_process_that_never_stopped(self, tmp_path):
        expected = invalidated_reference(self.GAP, TAIL)
        directory, target, _ = invalidated_state_dir(tmp_path)
        assert target == expected["target"], (
            "the restored runtime picked a different order block than the "
            "continuous one, so restore is already not exact")
        gap_bars = _bars(MAIN_START + MAIN_CUT,
                         MAIN_START + MAIN_CUT + self.GAP)
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(directory), genesis_dir=None,
            fetcher=FakeFetcher({SYM: gap_bars}),
            now_ts=boot_clock(gap_bars[-1].bar_idx))
        R.replay_bars(report.runtime,
                      _bars(MAIN_START + MAIN_CUT + self.GAP,
                            MAIN_START + MAIN_CUT + self.GAP + TAIL))
        assert_exact_parity(report.runtime, expected,
                            "invalidation + restart + bridge + tail")

    def test_the_invalidation_record_survives_field_by_field(self, tmp_path):
        directory, _, _ = invalidated_state_dir(tmp_path)
        before = ManualSMCRuntime.load_state(
            directory / R.STATE_FILE_NAME
        ).strategy.lifecycle.invalidations.records(asset=SYM)
        gap_bars = _bars(MAIN_START + MAIN_CUT,
                         MAIN_START + MAIN_CUT + self.GAP)
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(directory), genesis_dir=None,
            fetcher=FakeFetcher({SYM: gap_bars}),
            now_ts=boot_clock(gap_bars[-1].bar_idx))
        after = report.runtime.strategy.lifecycle.invalidations.records(
            asset=SYM)
        assert len(before) == 1 and len(after) == 1
        for field in dataclasses.fields(before[0]):
            assert getattr(after[0], field.name) == getattr(
                before[0], field.name), (
                f"invalidation field {field.name} did not survive the restart")
        # The timestamps are the permanent key: prove they are real datetimes on
        # the far side, not ISO strings the codec forgot to parse.
        assert isinstance(after[0].origin_dt, datetime)
        assert isinstance(after[0].bos_dt, datetime)
        assert after[0].requested_at == FIXED_REQUEST

    def test_the_same_order_block_would_otherwise_have_survived(self, tmp_path):
        """The control that stops the test above from being vacuous.

        Without the invalidation, the identical restart + bridge keeps that order
        block live. So its absence in the invalidated run is the ledger's doing,
        not an expiry that would have happened anyway.
        """
        target = invalidated_reference(self.GAP, TAIL)["target"]
        plain = Restart(tmp_path, self.GAP)
        report = plain.run()
        R.replay_bars(report.runtime,
                      _bars(MAIN_START + MAIN_CUT + self.GAP,
                            MAIN_START + MAIN_CUT + self.GAP + TAIL))
        live = report.runtime.strategy.lifecycle.live_obs
        assert target in live
        assert not report.runtime.strategy.lifecycle.invalidations.fingerprints

    def test_the_ledger_is_never_shortened_by_a_restart(self, tmp_path):
        """Append-only across the boundary: restore cannot drop a decision."""
        directory, _, result = invalidated_state_dir(tmp_path)
        gap_bars = _bars(MAIN_START + MAIN_CUT,
                         MAIN_START + MAIN_CUT + self.GAP)
        report = R.bootstrap_structure_state(
            _fresh(), state_dir=str(directory), genesis_dir=None,
            fetcher=FakeFetcher({SYM: gap_bars}),
            now_ts=boot_clock(gap_bars[-1].bar_idx))
        ledger = report.runtime.strategy.lifecycle.invalidations
        assert len(ledger) == result["invalidated_count"] == 1
        # Re-affirming the same fingerprint after the restart is idempotent: the
        # decision is already permanent, so nothing is duplicated.
        again = report.runtime.invalidate_ob(
            SYM, fingerprint=result["fingerprint"],
            requested_at=FIXED_REQUEST)
        assert again["created"] is False
        assert len(ledger) == 1
        # And the durable snapshot still round-trips after the re-affirmation.
        R.persist_state(report.runtime, directory / R.STATE_FILE_NAME)
        reloaded = ManualSMCRuntime.load_state(directory / R.STATE_FILE_NAME)
        assert reloaded.snapshot() == report.runtime.snapshot()

# ===========================================================================
# M - a live order block, including the open trade, survives the restart
# ===========================================================================
def drive(start: int, cut: int, symbol: str = SYM) -> ManualSMCRuntime:
    """An uninterrupted runtime, driven bar by bar. Returned live, not frozen.

    `reference()` caches frozen JSON, which is the right thing for snapshot
    equality but useless for comparing the RECORD OBJECTS field by field. This
    returns the objects themselves.
    """
    runtime = _fresh((symbol,))
    R.replay_bars(runtime, _bars(start, start + cut, symbol))
    return runtime


def ob_fields(ob: Any) -> Dict[str, Any]:
    """Every dataclass field, plus the ClassVar-shadowing structure tag.

    `bos_or_choch` is a `ClassVar` on `ManualOBRecord` (models.py:174) precisely
    so `dataclasses.fields()` excludes it, and the LuxAlgo scanner sets it per
    instance. It is persisted through a side map, so it is read explicitly here
    rather than being silently dropped from the comparison.
    """
    values = {f.name: getattr(ob, f.name) for f in dataclasses.fields(ob)}
    values["bos_or_choch"] = getattr(ob, "bos_or_choch")
    return values


def assert_records_match(restored: ManualSMCRuntime,
                         continuous: ManualSMCRuntime, label: str) -> None:
    """Every live order block, field by field, against a process that never stopped."""
    got = restored.strategy.lifecycle.live_obs
    want = continuous.strategy.lifecycle.live_obs
    assert sorted(got) == sorted(want), f"{label}: the live OB set differs"
    for ob_id in sorted(want):
        mine, theirs = ob_fields(got[ob_id]), ob_fields(want[ob_id])
        for name in theirs:
            assert mine[name] == theirs[name], (
                f"{label}: {ob_id} field {name} differs "
                f"({mine[name]!r} != {theirs[name]!r})")
            assert type(mine[name]) is type(theirs[name]), (
                f"{label}: {ob_id} field {name} changed type across the "
                f"snapshot ({type(mine[name])} != {type(theirs[name])})")

class TestM_ActiveOrderBlockSurvives:
    """Mandate M: an active order block survives the snapshot and the restart.

    The measured cut is chosen because it is the state-RICH one: seven retained
    order blocks, one of them TRADE_ACTIVE with a non-null single-trade slot. A
    restart that lost the open trade would free the single-trade lock and let a
    second position be admitted while the first is still in the market.
    """

    GAP = 3

    def test_the_measured_cut_holds_the_state_this_suite_claims(self):
        lifecycle = drive(MAIN_START, MAIN_CUT).strategy.lifecycle
        active = [ob for ob in lifecycle.live_obs.values()
                  if ob.state.name == "TRADE_ACTIVE"]
        assert len(lifecycle.live_obs) == MAIN_CUT_LIVE_OBS
        assert len(active) == MAIN_CUT_TRADE_ACTIVE_OBS
        assert lifecycle.active_trade is not None
        assert lifecycle.has_active_trade() is True
        assert lifecycle.active_trade.ob.ob_id == active[0].ob_id
        assert lifecycle.active_trade.ob.ob_id in lifecycle.live_obs

    def test_every_live_ob_survives_the_round_trip_field_by_field(
            self, tmp_path):
        restored = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        assert_records_match(restored, drive(MAIN_START, MAIN_CUT),
                             "restore at the cut")
        # Not a vacuous pass: the comparison really did look at seven records
        # and at the structure tags the LuxAlgo detector sets.
        assert len(restored.strategy.lifecycle.live_obs) == MAIN_CUT_LIVE_OBS
        assert {getattr(ob, "bos_or_choch") for ob
                in restored.strategy.lifecycle.live_obs.values()} == {
                    "BOS", "CHoCH"}

    def test_the_single_trade_slot_survives_the_round_trip(self, tmp_path):
        restored = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        mine = restored.strategy.lifecycle.active_trade
        theirs = drive(MAIN_START, MAIN_CUT).strategy.lifecycle.active_trade
        assert mine is not None and theirs is not None
        for f in dataclasses.fields(theirs):
            assert getattr(mine, f.name) == getattr(theirs, f.name), (
                f"active-trade field {f.name} did not survive the restart")
        assert isinstance(mine.fill_dt, datetime)
        assert isinstance(mine.retest_dt, datetime)
        # The open trade and the retained order block must remain the SAME
        # object, as they are in a process that never stopped. Two copies would
        # let a later lifecycle mutation reach one and not the other.
        assert mine.ob is restored.strategy.lifecycle.live_obs[mine.ob.ob_id]

    def test_the_records_still_match_after_the_bridge_and_the_tail(
            self, tmp_path):
        restart = Restart(tmp_path, self.GAP)
        report = restart.run()
        R.replay_bars(report.runtime,
                      _bars(MAIN_START + MAIN_CUT + self.GAP,
                            MAIN_START + MAIN_CUT + self.GAP + TAIL))
        continuous = drive(MAIN_START, MAIN_CUT + self.GAP + TAIL)
        assert_records_match(report.runtime, continuous,
                             "restore + bridge + tail")
        mine = report.runtime.strategy.lifecycle.active_trade
        theirs = continuous.strategy.lifecycle.active_trade
        assert (mine is None) == (theirs is None)
        if theirs is not None:
            for f in dataclasses.fields(theirs):
                assert getattr(mine, f.name) == getattr(theirs, f.name), (
                    f"active-trade field {f.name} diverged after the bridge")

    def test_the_closed_trade_history_survives_the_restart(self, tmp_path):
        """The exits list is what the single-trade watermark is judged against."""
        restored = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        continuous = drive(MAIN_START, MAIN_CUT)
        mine = restored.strategy.lifecycle.exits
        theirs = continuous.strategy.lifecycle.exits
        assert len(mine) == len(theirs) > 0
        for left, right in zip(mine, theirs):
            for f in dataclasses.fields(right):
                assert getattr(left, f.name) == getattr(right, f.name), (
                    f"exit field {f.name} did not survive the restart")
        assert (restored.strategy.lifecycle._last_trade_closed_dt
                == continuous.strategy.lifecycle._last_trade_closed_dt)

# ===========================================================================
# N - the order-block sequence counter and the rejection counters survive
# ===========================================================================
def structure_of(runtime: ManualSMCRuntime, symbol: str = SYM) -> Any:
    """The LuxAlgo structure object the sequence counter lives on."""
    return runtime.strategy.lifecycle._scanners[symbol]._structures[symbol]


class TestN_SequenceAndCountersSurvive:
    """Mandate N: `_ob_seq` survives the restart exactly.

    The counter is what makes a freshly detected order block distinguishable
    from one LuxAlgo already emitted. A restart that reset it to zero would
    re-issue sequence numbers already in use, so two different blocks in the same
    session could carry the same internal identity.
    """

    GAP = 6

    def test_the_sequence_counter_survives_the_restore(self, tmp_path):
        restored = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        continuous = drive(MAIN_START, MAIN_CUT)
        assert structure_of(continuous)._ob_seq == MAIN_CUT_OB_SEQ
        assert structure_of(restored)._ob_seq == MAIN_CUT_OB_SEQ
        # Stated separately, because "equal to the reference" would also be true
        # if a bug reset BOTH sides to zero.
        assert structure_of(restored)._ob_seq > 0

    def test_the_counter_keeps_incrementing_identically_after_the_bridge(
            self, tmp_path):
        report = Restart(tmp_path, self.GAP).run()
        R.replay_bars(report.runtime,
                      _bars(MAIN_START + MAIN_CUT + self.GAP,
                            MAIN_START + MAIN_CUT + self.GAP + TAIL))
        continuous = drive(MAIN_START, MAIN_CUT + self.GAP + TAIL)
        mine = structure_of(report.runtime)._ob_seq
        assert mine == structure_of(continuous)._ob_seq
        # And it really did advance over the bridge + tail, so the equality above
        # is not the trivial "nothing happened" case.
        assert mine > MAIN_CUT_OB_SEQ

    def test_the_rejection_counters_are_zero_in_this_real_history(self):
        """MEASURED, and recorded here so the next test is understood.

        Over the real canonical rows in this window LuxAlgo rejects nothing for
        width, so a round-trip of the real state can only ever prove `0 == 0`.
        That is why the next test drives deliberate values through the codec.
        """
        scanner = drive(MAIN_START, MAIN_CUT).strategy.lifecycle._scanners[SYM]
        assert (scanner.rejected_non_positive_width,
                scanner.rejected_below_min_width) == (0, 0)

    def test_non_zero_rejection_counters_survive_the_codec(self, tmp_path):
        """Deliberate values, through the real `save_state`/`load_state`.

        The counters are the audit trail for order blocks LuxAlgo saw and
        refused; losing them across a restart would silently under-report what
        the detector rejected. `state.py:1410` records that they are read back
        from `rejections`, not from the digest, so both are checked.
        """
        runtime = drive(MAIN_START, MAIN_CUT)
        scanner = runtime.strategy.lifecycle._scanners[SYM]
        scanner.rejected_non_positive_width = 4
        scanner.rejected_below_min_width = 9
        target = tmp_path / R.STATE_FILE_NAME
        R.persist_state(runtime, target)
        body = json.loads(target.read_text(encoding="utf-8"))
        encoded = body["strategy"]["scanners"][0]
        assert encoded["rejections"] == {"non_positive_width": 4,
                                        "below_min_width": 9}
        # The digest is re-derived from the live values at save time, so the
        # integrity check agrees with the counters rather than fighting them.
        assert encoded["digest"]["rejected_non_positive_width"] == 4
        assert encoded["digest"]["rejected_below_min_width"] == 9
        restored = ManualSMCRuntime.load_state(target)
        back = restored.strategy.lifecycle._scanners[SYM]
        assert (back.rejected_non_positive_width,
                back.rejected_below_min_width) == (4, 9)
        assert structure_of(restored)._ob_seq == MAIN_CUT_OB_SEQ

    def test_the_counter_is_carried_by_the_snapshot_not_recomputed(self,
                                                                  tmp_path):
        """A snapshot restored into a runtime that saw NO candles still has it."""
        restored = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        assert restored.candles_processed == 0
        assert structure_of(restored)._ob_seq == MAIN_CUT_OB_SEQ
        assert len(restored.strategy.lifecycle.live_obs) == MAIN_CUT_LIVE_OBS

# ===========================================================================
# O - a wedged structure stream is fatal, not a warning repeated every hour
# ===========================================================================
class _FakeFeedClient:
    """Stands in for `DeltaWebSocketClient` so the stop hook can be observed."""

    def __init__(self) -> None:
        self.running = True
        self.reconnect_attempt = 0


def durable_callback(tmp_path: Path,
                     monitor: R.StructureContinuityMonitor,
                     *, start: int = MAIN_START, cut: int = MAIN_CUT):
    """A restored runtime behind the PRODUCTION live callback composition."""
    state_file = seed_state_dir(tmp_path, start, cut) / R.STATE_FILE_NAME
    runtime = ManualSMCRuntime.load_state(state_file)
    callback = R.make_durable_feed_callback(
        runtime, make_feed_callback(runtime, submit_to=None),
        state_path=state_file, monitor=monitor)
    return runtime, callback, state_file


def live_payload(offset: int, *, start: int = MAIN_START,
                 cut: int = MAIN_CUT) -> Dict[str, Any]:
    """The closed-candle payload `offset` bars after the snapshot's last bar."""
    bar = _bars(start + cut + offset, start + cut + offset + 1)[0]
    return bar.payload("1h")


class TestO_StructureWedgeIsFatal:
    """Mandate O: escalated, not a chronic hourly warning.

    `make_feed_callback` catches every refusal and counts it, which is right for
    a transport edge but wrong for a hole: LuxAlgo cannot skip a bar, so the very
    next candle raises too, forever, one warning per hour, while /health answers
    UP. Only the unrecoverable case is escalated - duplicates, out-of-order
    candles and malformed payloads stay ordinary refusals, as section 7 requires.
    """

    def test_a_hole_in_the_live_stream_is_fatal(self, tmp_path, monitor):
        runtime, callback, _ = durable_callback(tmp_path, monitor)
        # Skip one closed hour, then deliver the one after it.
        with pytest.raises(SystemExit) as exit_info:
            callback(live_payload(1))
        assert exit_info.value.code == R.STRUCTURE_WEDGE_EXIT_CODE == 2
        assert monitor.fault is not None
        assert monitor.fault[0] == R.FAULT_STRUCTURE_GAP
        assert "1 closed 1h candle(s) are missing" in monitor.fault[1]
        assert monitor.is_healthy() is False
        assert monitor.health_payload()[0] == 503
        # The candle was never handed to LuxAlgo, so nothing was half-processed.
        assert runtime.candles_processed == 0

    def test_the_feed_is_stopped_before_the_fatal_exit(self, tmp_path, monitor):
        """`listen()` ends with `if self.running: await self._reconnect()`.

        Without clearing `running` first, the `SystemExit` would be deferred
        behind a reconnect, a REST backfill and a resubscribe - exactly the "keep
        going while blind" behaviour the fatal path exists to end.
        """
        from quantedge import __main__ as entrypoint

        client = _FakeFeedClient()
        monitor.register_stop_hook(entrypoint._feed_stop_hook(client))
        _, callback, _ = durable_callback(tmp_path, monitor)
        with pytest.raises(SystemExit):
            callback(live_payload(1))
        assert client.running is False

    def test_a_hole_is_fatal_on_the_very_first_offending_candle(self,
                                                               tmp_path,
                                                               monitor):
        """Not on the tenth, and not after an hour of warnings."""
        _, callback, _ = durable_callback(tmp_path, monitor)
        with pytest.raises(SystemExit):
            callback(live_payload(4))
        assert monitor.fault[0] == R.FAULT_STRUCTURE_GAP
        assert "4 closed 1h candle(s) are missing" in monitor.fault[1]

    def test_a_contiguous_candle_is_accepted_and_leaves_no_fault(self, tmp_path,
                                                                monitor):
        runtime, callback, state_file = durable_callback(tmp_path, monitor)
        payload = live_payload(0)
        assert runtime.continuity_gap_for_payload(payload) == 0
        step = callback(payload)
        assert step is not None and step.skipped is None
        assert monitor.is_healthy() is True
        assert monitor.health_payload()[0] == 200
        assert runtime.candles_processed == 1
        # Persisted at the safest point: after the lifecycle and the watermark
        # have both advanced, so the file is whole rather than torn.
        assert json.loads(state_file.read_text(encoding="utf-8")) == _freeze(
            runtime.snapshot())
        assert runtime.strategy.watermark.last(SYM).bar_idx == MAIN_CUT_BAR + 1

    def test_a_duplicate_candle_stays_an_ordinary_refusal(self, tmp_path,
                                                          monitor):
        runtime, callback, _ = durable_callback(tmp_path, monitor)
        payload = live_payload(0)
        assert callback(payload) is not None
        # The same closed hour again. The watermark already refuses it safely.
        assert runtime.continuity_gap_for_payload(payload) == -1
        assert callback(payload) is None
        assert runtime.candles_refused == 1
        assert monitor.is_healthy() is True
        assert monitor.fault is None

    def test_an_out_of_order_candle_stays_an_ordinary_refusal(self, tmp_path,
                                                              monitor):
        runtime, callback, _ = durable_callback(tmp_path, monitor)
        stale = _bars(MAIN_START + MAIN_CUT - 3,
                      MAIN_START + MAIN_CUT - 2)[0].payload("1h")
        assert runtime.continuity_gap_for_payload(stale) < 0
        assert callback(stale) is None
        assert runtime.candles_refused == 1
        assert monitor.is_healthy() is True

    @pytest.mark.parametrize("payload", [
        "not a mapping",
        {},
        {"symbol": SYM, "timeframe": "1h"},
        {"symbol": SYM, "timeframe": "5m", "timestamp": MAIN_CUT_BAR * HOUR,
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "is_closed": True},
        {"symbol": "NOT_REGISTERED", "timeframe": "1h",
         "timestamp": (MAIN_CUT_BAR + 1) * HOUR, "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "is_closed": True},
    ])
    def test_a_malformed_payload_stays_an_ordinary_refusal(self, tmp_path,
                                                           monitor, payload):
        """`None` from the gap probe means "the question does not apply"."""
        runtime, callback, _ = durable_callback(tmp_path, monitor)
        assert runtime.continuity_gap_for_payload(payload) is None
        assert callback(payload) is None
        assert runtime.candles_refused == 1
        assert monitor.is_healthy() is True
        assert monitor.fault is None

    def test_the_gap_probe_mutates_nothing(self, tmp_path, monitor):
        """It is consulted before every candle, so it must be inert."""
        runtime, _, _ = durable_callback(tmp_path, monitor)
        before = _freeze(runtime.snapshot())
        counters = (runtime.candles_processed, runtime.candles_refused,
                    runtime.candles_skipped)
        for payload in (live_payload(0), live_payload(1), live_payload(5),
                        "garbage", {}, {"symbol": SYM}):
            runtime.continuity_gap_for_payload(payload)
        assert _freeze(runtime.snapshot()) == before
        assert (runtime.candles_processed, runtime.candles_refused,
                runtime.candles_skipped) == counters
        assert monitor.is_healthy() is True

    def test_the_bare_callback_still_swallows_a_hole(self, tmp_path):
        """Section 7's control: LuxAlgo's own exception semantics are unchanged.

        Without the wrapper the hole is still caught, still counted and still
        logged as one warning - which is precisely the behaviour that made a
        wedge invisible, and precisely what must NOT change underneath.
        """
        runtime = ManualSMCRuntime.load_state(
            seed_state_dir(tmp_path) / R.STATE_FILE_NAME)
        bare = make_feed_callback(runtime, submit_to=None)
        assert bare(live_payload(1)) is None
        assert runtime.candles_refused == 1
        assert runtime.candles_processed == 0
        # No exception escaped, nothing exited, and the runtime is still willing
        # to be fed - the chronic-warning behaviour, intact.
        assert bare(live_payload(2)) is None
        assert runtime.candles_refused == 2

    def test_a_wedge_is_not_reported_as_a_persistence_failure(self, tmp_path,
                                                              monitor):
        """The two fault codes stay distinguishable to an operator."""
        _, callback, _ = durable_callback(tmp_path, monitor)
        with pytest.raises(SystemExit):
            callback(live_payload(2))
        assert monitor.fault[0] == R.FAULT_STRUCTURE_GAP
        assert monitor.fault[0] != R.FAULT_STATE_PERSISTENCE_FAILED
        assert json.loads(monitor.health_payload()[1])["reason"] == (
            R.FAULT_STRUCTURE_GAP)

# ===========================================================================
# P - /health cannot look healthy while the structure clock is wedged
# ===========================================================================
#: The bytes `/health` answered before this task, taken from `git show
#: HEAD:engine/src/quantedge/__main__.py`. The healthy body must stay identical
#: so the compose healthcheck and anything else polling it are unaffected.
HISTORICAL_UP_BODY = b'{"status":"UP","service":"quantedge-engine"}'


class _CapturedResponse:
    """Records what `HealthHandler.do_GET` wrote, without opening a socket."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.status: Optional[int] = None
        self.headers: List[Tuple[str, str]] = []
        self.errors: List[Any] = []
        self.ended = False
        self.wfile = io.BytesIO()

    # -- the BaseHTTPRequestHandler surface `do_GET` actually touches ------
    def send_response(self, status: Any) -> None:
        self.status = int(status)

    def send_header(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def end_headers(self) -> None:
        self.ended = True

    def send_error(self, status: Any) -> None:
        self.errors.append(status)

    @property
    def body(self) -> bytes:
        return self.wfile.getvalue()


def call_health(path: str = "/health") -> _CapturedResponse:
    """Run the REAL `do_GET` against a captured response."""
    from quantedge.__main__ import HealthHandler

    handler = HealthHandler.__new__(HealthHandler)
    captured = _CapturedResponse(path)
    for name in ("path", "send_response", "send_header", "end_headers",
                 "send_error", "wfile"):
        setattr(handler, name, getattr(captured, name))
    HealthHandler.do_GET(handler)
    return captured

class TestP_HealthCannotHideAWedge:
    """Mandate P: a permanently wedged engine must not answer healthy.

    `/health` used to be a hardcoded `{"status":"UP"}`, so it could not tell a
    working engine from one whose STRUCTURE clock had been wedged for hours, or
    from one that refused to subscribe at all - the HTTP server is alive in every
    one of those cases. It now asks the one component that knows.
    """

    def test_a_healthy_monitor_keeps_the_historical_body_byte_for_byte(
            self, monitor):
        assert monitor.health_payload() == (200, HISTORICAL_UP_BODY)

    def test_a_wedged_structure_clock_is_not_healthy(self, monitor):
        with pytest.raises(SystemExit):
            monitor.mark_fault(R.FAULT_STRUCTURE_GAP, "a hole in the stream")
        status, body = monitor.health_payload()
        assert status == 503
        decoded = json.loads(body)
        assert decoded["status"] == "DOWN"
        assert decoded["service"] == "quantedge-engine"
        assert decoded["reason"] == R.FAULT_STRUCTURE_GAP
        assert "a hole in the stream" in decoded["detail"]

    def test_a_configuration_refusal_is_not_healthy_either(self, monitor):
        """Inert: the process is up, but it never subscribed to anything."""
        monitor.mark_inert(R.INERT_NO_STATE_DIR, "no durable state directory")
        assert monitor.is_healthy() is False
        status, body = monitor.health_payload()
        assert status == 503
        assert json.loads(body)["reason"] == R.INERT_NO_STATE_DIR

    def test_a_fault_outranks_an_inert_refusal(self, monitor):
        monitor.mark_inert(R.INERT_NO_GENESIS_SOURCE, "no genesis source")
        with pytest.raises(SystemExit):
            monitor.mark_fault(R.FAULT_STATE_PERSISTENCE_FAILED, "disk full")
        assert json.loads(monitor.health_payload()[1])["reason"] == (
            R.FAULT_STATE_PERSISTENCE_FAILED)

    def test_the_first_problem_is_the_one_reported(self, monitor):
        monitor.mark_inert(R.INERT_NO_STATE_DIR, "first")
        monitor.mark_inert(R.INERT_NO_GENESIS_SOURCE, "second")
        assert monitor.inert == (R.INERT_NO_STATE_DIR, "first")

    def test_the_detail_is_bounded_so_the_body_stays_small(self, monitor):
        monitor.mark_inert(R.INERT_NO_STATE_DIR, "x" * 500)
        assert len(json.loads(monitor.health_payload()[1])["detail"]) == 200

    def test_the_endpoint_serves_the_historical_bytes_when_healthy(
            self, clean_singleton):
        captured = call_health()
        assert captured.status == 200
        assert captured.body == HISTORICAL_UP_BODY
        assert ("Content-Type", "application/json") in captured.headers
        assert ("Content-Length", str(len(HISTORICAL_UP_BODY))
                ) in captured.headers
        assert captured.ended is True
        assert captured.errors == []

    def test_the_endpoint_reports_503_once_the_clock_is_wedged(
            self, clean_singleton):
        with pytest.raises(SystemExit):
            clean_singleton.mark_fault(R.FAULT_STRUCTURE_GAP, "wedged")
        captured = call_health()
        assert captured.status == 503
        assert json.loads(captured.body)["reason"] == R.FAULT_STRUCTURE_GAP
        # Content-Length must describe the DOWN body, not the historical one.
        assert ("Content-Length", str(len(captured.body))) in captured.headers

    def test_the_endpoint_reports_503_while_inert(self, clean_singleton):
        clean_singleton.mark_inert(R.INERT_NO_STATE_DIR, "unconfigured")
        captured = call_health()
        assert captured.status == 503
        assert json.loads(captured.body)["status"] == "DOWN"

    def test_a_foreign_path_is_still_a_404(self, clean_singleton):
        captured = call_health("/metrics")
        assert captured.errors == [HTTPStatus.NOT_FOUND]
        assert captured.status is None
        assert captured.body == b""

    def test_the_compose_healthcheck_still_polls_this_endpoint(self):
        """So a 503 actually reaches the supervisor.

        `wget --spider` exits non-zero on a non-2xx response, which is what turns
        the DOWN body into an unhealthy container rather than a line nobody reads.
        """
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "http://localhost:8000/health" in compose
        assert "--spider" in compose

# ===========================================================================
# Q - no execution surface was added by any of the above
# ===========================================================================
ENGINE_SRC = REPO_ROOT / "engine" / "src" / "quantedge"

#: Tokens that must appear NOWHERE in the new restart/state module. Measured, not
#: assumed: `orchestrator`, `credential` and `submit(` are deliberately NOT in
#: this list because all three occur in that module's own docstring, which says
#: it never builds an orchestrator, never reads a credential and never calls
#: `runtime.submit()`. Pinning their absence would forbid the promise itself.
FORBIDDEN_IN_RESTART_RECOVERY = (
    "on_market_update",
    "make_market_update_callback",
    "api_key",
    "api_secret",
    "DELTA_API",
    "os.environ",
    "getenv",
    ".env",
)


class TestQ_NoExecutionSurfaceAdded:
    """Mandate section 12: the execution boundary stays exactly where it was.

    The restart/state layer is allowed to read history and write a snapshot. It
    is not allowed to acquire a credential, an orchestrator, or the forming-frame
    clock that turns a touch into an order.
    """

    @pytest.mark.parametrize("token", FORBIDDEN_IN_RESTART_RECOVERY)
    def test_the_restart_layer_names_no_execution_surface(self, token):
        source = (ENGINE_SRC / "runtime" / "restart_recovery.py").read_text(
            encoding="utf-8")
        assert token not in source, (
            f"{token!r} appeared in restart_recovery.py; the restart/state layer "
            f"must not reach the execution clock or any credential")

    def test_the_entrypoint_never_binds_the_execution_clock(self):
        source = (ENGINE_SRC / "__main__.py").read_text(encoding="utf-8")
        # The assignment form, not the word: the module's comments explain at
        # length that the forming-frame clock is left UNBOUND on purpose, and
        # that explanation must survive.
        assert "on_market_update=" not in source
        assert "make_market_update_callback" not in source
        assert "orchestrator=" not in source
        assert "submit_to=None" in source
        for token in ("api_key", "api_secret", "DELTA_API", "getenv",
                      "os.environ"):
            assert token not in source, f"{token!r} appeared in __main__.py"

    def test_the_bootstrapped_runtime_has_no_orchestrator(self, tmp_path):
        report = Restart(tmp_path, 1).run()
        assert report.runtime.orchestrator is None
        assert report.runtime.strategy.lifecycle.active_trade is not None, (
            "the state-rich cut is in use, so this is not vacuously true")

    def test_a_ready_decision_cannot_be_submitted(self, tmp_path):
        """The unchanged NO_ORCHESTRATION_BOUND fail-safe, exercised for real.

        A plain accepted candle produces no ready decision, so asserting on one
        would pass whether or not the gate exists. The step is therefore built
        carrying a ready decision, which is what forces `submit` past its two
        early returns (`adaptation is None`, `not ready`) and onto the
        `orchestrator is None` branch at manual_smc_runtime.py:1044.
        """
        runtime, _, _ = durable_callback(
            tmp_path, monitor=R.StructureContinuityMonitor())
        assert runtime.orchestrator is None
        assert runtime.lifecycle_manager is None

        class _Decision:
            setup_id = "PIN_SETUP"

        class _Adaptation:
            ready_decisions = (_Decision(),)

        step = ManualSMCRuntimeStep(symbol=SYM, ts=_candles()[MAIN_START].ts,
                                    bar_idx=MAIN_CUT_BAR,
                                    adaptation=_Adaptation())
        assert step.adaptation.ready_decisions, "not a vacuous pin"
        result = asyncio.run(runtime.submit(step, user_id="u", account_id="a"))
        assert result is step
        assert result.submitted == ()
        assert result.scan_result is None
        assert result.expiries == ()

    def test_the_live_binding_never_passes_the_forming_frame_clock(
            self, tmp_path, monkeypatch, clean_singleton):
        """Constructs the REAL composition with a fake transport class."""
        captured: Dict[str, Any] = {}

        class _Client:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)
                self.running = True
                self.reconnect_attempt = 0

            async def run(self) -> None:
                return None

        from quantedge import __main__ as entrypoint
        monkeypatch.setattr(dws, "DeltaWebSocketClient", _Client)
        state_file = seed_state_dir(tmp_path) / R.STATE_FILE_NAME
        runtime = ManualSMCRuntime.load_state(state_file)
        with pytest.raises(SystemExit) as exit_info:
            asyncio.run(entrypoint.run_live_market_data(runtime, state_file))
        # A feed client that returns is itself abnormal: exiting non-zero hands
        # the decision to the supervisor instead of idling while blind.
        assert exit_info.value.code == entrypoint.FEED_FAILURE_EXIT_CODE == 1
        assert "on_market_update" not in captured
        assert sorted(captured) == ["on_candle_closed", "symbols"]
        assert callable(captured["on_candle_closed"])
        assert captured["symbols"] == [SYM]

    def test_the_durable_wrapper_submits_nothing_of_its_own(self, tmp_path):
        """Persisting a snapshot must not become a second execution path."""
        runtime, callback, state_file = durable_callback(
            tmp_path, monitor=R.StructureContinuityMonitor())
        step = callback(live_payload(0))
        assert step is not None and step.skipped is None
        assert step.submitted == ()
        assert step.scan_result is None
        assert step.expiries == ()
        assert runtime.orchestrator is None
        # The wrapper's only side effect is the snapshot on disk.
        assert json.loads(state_file.read_text(encoding="utf-8"))

















