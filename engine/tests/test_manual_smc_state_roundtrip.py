"""
Manual SMC — State capture/restore acceptance tests (Phase 1 Step 5).
=====================================================================

MANDATED COVERAGE -> CLASS
    clean round-trip .................. TestCleanRoundTrip
    empty state ....................... TestEmptyState
    active trade + multiple OBs ....... TestActiveTradeAndMultipleOBs
    scanner history + consumed set .... TestScannerState
    processed-candle watermark ........ TestProcessedCandleWatermark
    deterministic continuation ........ TestDeterministicContinuation
    malformed payload rejection ....... TestMalformedPayloadRejected
    missing-field rejection ........... TestMissingFieldRejected
    unknown enum rejection ............ TestUnknownEnumRejected
    unsupported schema version ........ TestSchemaVersionRejected
    no exchange/DB/runtime imports ..... TestModuleIndependence

Plus: TestExactness (float/Decimal/datetime/enum fidelity),
TestCodecDriftGuard (a new dataclass field cannot be silently dropped),
TestStateIntegrityRejected (the invariants behind safety rules #13/#14 and the
torn-capture detector).

SCHEMA 4 — THE SHIPPED DETECTOR (see the banner comment further down)
    LuxAlgo capture/restore ........... TestLuxAlgoScannerStateRoundTrip
    detector identity survives ........ TestDetectorIdentitySurvivesTheRoundTrip
    declared name vs real scanner ..... TestTheDeclaredDetectorMustMatchTheRealOne
    both candle copies are checked ..... TestTheLuxAlgoCandleLogCannotBeTamperedWith
    the payload is causally bounded .... TestTheLuxAlgoSnapshotIsBounded

Everything ABOVE that banner names `DETECTOR_MANUAL_SPEC` explicitly, because
the 7-bar `BARS` fixture below was built for the half-candle zone. That is a pin
to the algorithm those numbers came from, not a claim about the default; the
shipped default is asserted positively by the four classes named above and by
`test_manual_smc_first_touch_window.py::TestProductionPolicyDefaults`.

The fixture candle sequence below is not synthetic decoration: it drives the
REAL lifecycle through OB creation (both directions), pre-displacement touches,
probe, displacement, fill, TP close and invalidation, so the snapshots under
test contain every shape of state the strategy can hold.
"""

from __future__ import annotations

import ast
import collections
import copy
import dataclasses
import io
import json
import re
import tokenize
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from quantedge.strategy.manual_smc.geometry import _make_manual_ob
from quantedge.strategy.manual_smc.lifecycle import (
    DETECTOR_LUXALGO_INTERNAL,
    DETECTOR_MANUAL_SPEC,
    ManualActiveTrade,
    ManualLifecycleEvent,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.luxalgo_structure import SWINGS_LENGTH
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.scanner import (
    LuxAlgoInternalOBScanner,
    ManualSpecBOSScanner,
)
from quantedge.strategy.manual_smc.state import (
    MANUAL_SMC_STATE_SCHEMA,
    MANUAL_SMC_STATE_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    CandleMark,
    CandleWatermark,
    MalformedStateError,
    MissingFieldError,
    RestoredState,
    StateError,
    StateIntegrityError,
    StateSchemaError,
    UnknownEnumValueError,
    UnknownFieldError,
    UnsupportedSchemaVersionError,
    WatermarkRegressionError,
    assert_config_compatible,
    capture_state,
    capture_state_json,
    decode_dataclass,
    decode_datetime,
    decode_decimal,
    decode_ob_state,
    decode_ob_structure_tags,
    dumps_state,
    encode_dataclass,
    encode_datetime,
    encode_decimal,
    encode_ob_state,
    encode_ob_structure_tags,
    expected_keys,
    loads_state,
    restore_state,
    restore_state_json,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
BTC = "BTCUSD"
ETH = "ETHUSD"

#: (open, high, low, close). Drives the full lifecycle — see module docstring.
BARS: Tuple[Tuple[float, float, float, float], ...] = (
    (100.0, 101.0, 99.0, 100.5),   # 0 bullish origin
    (99.0, 99.1, 98.0, 98.5),      # 1 BOS below origin low -> SHORT OB
    (98.5, 99.6, 98.4, 99.5),      # 2 probe + a LONG OB from bar 1
    (99.5, 99.7, 98.6, 98.8),      # 3 displacement -> limit from bar 4
    (98.8, 99.5, 98.7, 99.0),      # 4 entry touched -> FILL
    (99.0, 99.1, 98.5, 98.6),      # 5 TP + LONG invalidated + new SHORT OB
    (98.6, 98.9, 98.2, 98.3),      # 6 touch on the surviving OB
)

MODULE_PATH = (Path(__file__).parent.parent / "src" / "quantedge" / "strategy"
               / "manual_smc" / "state.py")
MODULE_SRC = MODULE_PATH.read_text(encoding="utf-8")


def _code_without_strings() -> str:
    """Source with every comment and string literal removed."""
    skip = {tokenize.COMMENT, tokenize.STRING}
    for name in ("FSTRING_MIDDLE",):
        tok_type = getattr(tokenize, name, None)
        if tok_type is not None:
            skip.add(tok_type)
    pieces: List[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(MODULE_SRC).readline):
        if tok.type in skip:
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


def _imported_modules() -> List[str]:
    """Every module named by an import statement, via the AST (not a regex)."""
    out = set()
    for node in ast.walk(ast.parse(MODULE_SRC)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return sorted(out)


def _drive(
    lifecycle: ManualSMCLifecycle,
    watermark: CandleWatermark,
    first: int,
    last: int,
    asset: str = BTC,
) -> List[ManualLifecycleEvent]:
    """
    Feed bars [first, last) and advance the watermark after each candle.

    Watermark-after-lifecycle is the order a real caller must use: it is the
    only order under which a crash replays a candle rather than skipping one.
    """
    events: List[ManualLifecycleEvent] = []
    for i in range(first, last):
        o, h, l, c = BARS[i]
        events += lifecycle.process_candle(asset, i, T0 + timedelta(hours=i),
                                           o, h, l, c)
        watermark.advance(asset, i, T0 + timedelta(hours=i))
    return events


def _drive_multi(
    lifecycle: ManualSMCLifecycle,
    watermark: CandleWatermark,
    first: int,
    last: int,
    assets: Tuple[str, ...],
) -> List[ManualLifecycleEvent]:
    """
    Feed bars [first, last) INTERLEAVED across assets, bar by bar.

    The interleaving matters: the single global trade slot and
    `_last_trade_closed_dt` couple the assets, so "all of BTC then all of ETH"
    and "bar 0 of both, bar 1 of both, ..." are genuinely different runs. Any
    comparison between an unbroken run and a resumed one must hold the global
    candle order fixed, or it measures the test's own ordering, not the restore.
    """
    events: List[ManualLifecycleEvent] = []
    for i in range(first, last):
        for asset in assets:
            o, h, l, c = BARS[i]
            events += lifecycle.process_candle(asset, i,
                                               T0 + timedelta(hours=i),
                                               o, h, l, c)
            watermark.advance(asset, i, T0 + timedelta(hours=i))
    return events


def _fresh(bars: int, asset: str = BTC) -> Tuple[ManualSMCLifecycle,
                                                 CandleWatermark,
                                                 List[ManualLifecycleEvent]]:
    """A lifecycle driven through the first `bars` candles."""
    lifecycle = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
    watermark = CandleWatermark()
    events = _drive(lifecycle, watermark, 0, bars, asset)
    return lifecycle, watermark, events


#: The bar index at which the fixture's entry limit fills. `_fresh(FILL_BAR+1)`
#: is therefore the first snapshot that holds an ACTIVE TRADE.
FILL_BAR: int = 4


def _valid_payload(bars: int = FILL_BAR + 1) -> Dict[str, Any]:
    """A payload captured mid-run: 2 live OBs, one of them TRADE_ACTIVE."""
    lifecycle, watermark, _ = _fresh(bars)
    return capture_state(lifecycle, watermark)


def _mutate(payload: Dict[str, Any], *path: Any, value: Any) -> Dict[str, Any]:
    """Deep-copy `payload` and set `path` to `value`."""
    out = copy.deepcopy(payload)
    target: Any = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


def _drop(payload: Dict[str, Any], *path: Any) -> Dict[str, Any]:
    """Deep-copy `payload` and delete the key at `path`."""
    out = copy.deepcopy(payload)
    target: Any = out
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return out


def _events_repr(events: List[ManualLifecycleEvent]) -> List[Tuple[Any, ...]]:
    return [(e.event_type.value, e.asset, e.bar_idx, e.ts, e.ob_id,
             e.direction, e.state.value, e.detail) for e in events]


# ---------------------------------------------------------------------------
class TestCleanRoundTrip:
    """Capture -> JSON -> restore preserves every value."""

    def test_the_fixture_actually_exercises_the_lifecycle(self):
        """Guard the fixture itself: a degenerate one would prove nothing."""
        lifecycle, _, events = _fresh(len(BARS))
        kinds = {e.event_type.value for e in events}
        assert {"OB_CREATED", "PRE_DISPLACEMENT_TOUCH", "PROBE_CONFIRMED",
                "DISPLACEMENT_CONFIRMED", "ENTRY_FILLED", "TRADE_CLOSED",
                "INVALIDATED"} <= kinds
        assert len(lifecycle.exits) == 1
        assert lifecycle.exits[0].outcome == "FILLED_TP"

    def test_round_trip_through_json_is_byte_identical(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        text = capture_state_json(lifecycle, watermark)
        restored = restore_state_json(text)
        assert capture_state_json(restored.lifecycle, restored.watermark) == text

    def test_round_trip_preserves_the_config(self):
        lifecycle, watermark, _ = _fresh(3)
        restored = restore_state(capture_state(lifecycle, watermark))
        assert restored.config == lifecycle.cfg
        assert restored.lifecycle.cfg == lifecycle.cfg

    def test_round_trip_preserves_every_ob_field(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        assert list(restored.lifecycle.live_obs) == list(lifecycle.live_obs)
        for ob_id, original in lifecycle.live_obs.items():
            assert restored.lifecycle.live_obs[ob_id] == original

    def test_round_trip_preserves_exits(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        restored = restore_state(capture_state(lifecycle, watermark))
        assert restored.lifecycle.exits == lifecycle.exits
        assert restored.lifecycle.exits[0].strategy_name == "MANUAL_SMC"

    def test_round_trip_preserves_the_close_timestamp_watermark(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        assert lifecycle._last_trade_closed_dt is not None
        restored = restore_state(capture_state(lifecycle, watermark))
        assert (restored.lifecycle._last_trade_closed_dt
                == lifecycle._last_trade_closed_dt)

    def test_the_payload_is_plain_json(self):
        payload = _valid_payload()
        assert json.loads(json.dumps(payload)) == payload

    def test_the_payload_carries_schema_and_identity(self):
        payload = _valid_payload()
        assert payload["schema"] == MANUAL_SMC_STATE_SCHEMA
        assert payload["schema_version"] == MANUAL_SMC_STATE_SCHEMA_VERSION
        assert payload["strategy_name"] == MANUAL_SMC_STRATEGY_NAME
        assert payload["strategy_version"] == MANUAL_SMC_STRATEGY_VERSION
        assert MANUAL_SMC_STATE_SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS

    def test_capture_does_not_mutate_the_lifecycle(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        before = capture_state(lifecycle, watermark)
        capture_state(lifecycle, watermark)
        assert capture_state(lifecycle, watermark) == before
        assert lifecycle.active_trade is not None

    def test_restore_returns_a_restored_state_bundle(self):
        restored = restore_state(_valid_payload())
        assert isinstance(restored, RestoredState)
        assert isinstance(restored.lifecycle, ManualSMCLifecycle)
        assert isinstance(restored.watermark, CandleWatermark)
        assert isinstance(restored.config, ManualSpecConfig)


class TestEmptyState:
    """A never-run lifecycle round-trips as cleanly as a busy one."""

    def test_empty_lifecycle_round_trips(self):
        lifecycle = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
        payload = capture_state(lifecycle)
        assert payload["live_obs"] == []
        assert payload["active_trade"] is None
        assert payload["exits"] == []
        assert payload["scanners"] == []
        assert payload["watermark"] == []
        assert payload["last_trade_closed_dt"] is None
        restored = restore_state(payload)
        assert restored.lifecycle.live_obs == {}
        assert restored.lifecycle.active_trade is None
        assert restored.lifecycle.exits == []
        assert len(restored.watermark) == 0

    def test_empty_state_is_still_schema_checked(self):
        payload = capture_state(ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC))
        assert payload["schema_version"] == MANUAL_SMC_STATE_SCHEMA_VERSION
        with pytest.raises(UnsupportedSchemaVersionError):
            restore_state(_mutate(payload, "schema_version", value=99))

    def test_declared_assets_are_captured_before_any_candle(self):
        """`ManualSMCLifecycle(assets=[...])` pre-creates empty scanners."""
        lifecycle = ManualSMCLifecycle(assets=[BTC, ETH],
                                       detector=DETECTOR_MANUAL_SPEC)
        payload = capture_state(lifecycle)
        assert [s["asset"] for s in payload["scanners"]] == [BTC, ETH]
        assert all(s["history"] == [] and s["consumed"] == []
                   for s in payload["scanners"])
        restored = restore_state(payload)
        assert sorted(restored.lifecycle._scanners) == [BTC, ETH]

    def test_an_empty_restore_then_run_matches_a_plain_run(self):
        restored = restore_state(capture_state(ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)))
        events_a = _drive(restored.lifecycle, restored.watermark, 0, len(BARS))
        _, _, events_b = _fresh(len(BARS))
        assert _events_repr(events_a) == _events_repr(events_b)


class TestActiveTradeAndMultipleOBs:
    """The single active trade, and the pool it lives alongside."""

    def test_the_fixture_state_has_an_active_trade_and_two_obs(self):
        lifecycle, _, _ = _fresh(FILL_BAR + 1)
        assert lifecycle.active_trade is not None
        assert len(lifecycle.live_obs) == 2
        states = {ob.state for ob in lifecycle.live_obs.values()}
        assert states == {ManualOBState.TRADE_ACTIVE,
                          ManualOBState.AWAITING_DISPLACEMENT}

    def test_active_trade_round_trips_field_for_field(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        assert restored.lifecycle.active_trade == lifecycle.active_trade
        assert restored.lifecycle.has_active_trade()

    def test_the_active_trade_ob_is_the_identical_pool_object(self):
        """
        `_close_trade` mutates `at.ob.state` and pops `at.ob.ob_id` from the
        pool. If restore produced a COPY, the pool's OB would keep a stale
        state and the close would act on a different object.
        """
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        at = restored.lifecycle.active_trade
        assert at is not None
        assert restored.lifecycle.live_obs[at.ob.ob_id] is at.ob

    def test_the_active_trade_is_persisted_by_reference(self):
        payload = _valid_payload()
        assert "ob_ref" in payload["active_trade"]
        assert "ob" not in payload["active_trade"]
        assert payload["active_trade"]["ob_ref"] in {
            ob["ob_id"] for ob in payload["live_obs"]}

    def test_pool_insertion_order_survives(self):
        """
        `candidate_obs()` and `_step2_update_obs()` iterate insertion order, so
        the payload stores a LIST, not a mapping.
        """
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        payload = capture_state(lifecycle, watermark)
        assert isinstance(payload["live_obs"], list)
        assert ([ob["ob_id"] for ob in payload["live_obs"]]
                == list(lifecycle.live_obs))
        restored = restore_state(payload)
        assert list(restored.lifecycle.live_obs) == list(lifecycle.live_obs)

    def test_closing_the_restored_trade_behaves_identically(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        a = _drive(lifecycle, watermark, FILL_BAR + 1, FILL_BAR + 3)
        b = _drive(restored.lifecycle, restored.watermark,
                   FILL_BAR + 1, FILL_BAR + 3)
        assert _events_repr(a) == _events_repr(b)
        assert restored.lifecycle.exits == lifecycle.exits
        assert restored.lifecycle.active_trade is None


class TestScannerState:
    """
    The history deque and the consumed-origin set ARE the scanner's memory.

    Lose either and a restored scanner re-emits an OB for an origin it already
    consumed, breaking "one OB per origin candle forever".
    """

    def test_history_round_trips_as_tuples_in_order(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        original = lifecycle._scanners[BTC]
        restored = restore_state(capture_state(lifecycle, watermark))
        rebuilt = restored.lifecycle._scanners[BTC]
        assert list(rebuilt._history) == list(original._history)
        assert all(isinstance(row, tuple) for row in rebuilt._history)

    def test_history_keeps_the_deque_bound(self):
        """A restored `deque` without `maxlen` would grow without limit."""
        lifecycle, watermark, _ = _fresh(len(BARS))
        restored = restore_state(capture_state(lifecycle, watermark))
        rebuilt = restored.lifecycle._scanners[BTC]
        assert isinstance(rebuilt, ManualSpecBOSScanner)
        assert isinstance(rebuilt._history, collections.deque)
        assert rebuilt._history.maxlen == lifecycle.cfg.lookback + 1
        assert rebuilt._history.maxlen == lifecycle._scanners[BTC]._history.maxlen

    def test_history_rows_hold_exact_prices_and_timestamps(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        restored = restore_state(capture_state(lifecycle, watermark))
        rows = list(restored.lifecycle._scanners[BTC]._history)
        for bar_idx, o, h, l, c, ts in rows:
            assert (o, h, l, c) == BARS[bar_idx]
            assert ts == T0 + timedelta(hours=bar_idx)

    def test_consumed_set_round_trips_as_a_set_of_tuples(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        original = lifecycle._scanners[BTC]._consumed
        assert original                                  # guard the fixture
        restored = restore_state(capture_state(lifecycle, watermark))
        rebuilt = restored.lifecycle._scanners[BTC]._consumed
        assert rebuilt == original
        assert isinstance(rebuilt, set)
        assert all(isinstance(k, tuple) and len(k) == 2 for k in rebuilt)
        assert all(isinstance(k[0], str) and isinstance(k[1], int)
                   for k in rebuilt)

    def test_a_restored_scanner_refuses_a_consumed_origin(self):
        """
        Replay the exact candle that first produced an OB. The origin is already
        in `_consumed`, so a correctly restored scanner emits nothing.
        """
        lifecycle, watermark, _ = _fresh(2)
        consumed = set(lifecycle._scanners[BTC]._consumed)
        assert consumed
        restored = restore_state(capture_state(lifecycle, watermark))
        scanner = restored.lifecycle._scanners[BTC]
        o, h, l, c = BARS[1]
        again = scanner.scan(BTC, 1, T0 + timedelta(hours=1), o, h, l, c,
                             restored.config)
        assert again == []
        assert scanner._consumed == consumed

    def test_a_scanner_lacking_the_consumed_set_would_re_emit(self):
        """
        The negative control for the test above: a scanner rebuilt WITHOUT the
        consumed set does re-emit, which is exactly what persisting it prevents.
        """
        lifecycle, watermark, _ = _fresh(2)
        restored = restore_state(capture_state(lifecycle, watermark))
        scanner = restored.lifecycle._scanners[BTC]
        scanner._consumed.clear()
        o, h, l, c = BARS[1]
        again = scanner.scan(BTC, 1, T0 + timedelta(hours=1), o, h, l, c,
                             restored.config)
        assert len(again) == 1

    def test_the_payload_is_canonically_ordered(self):
        """Scanners sorted by asset, `consumed` sorted — so bytes compare."""
        a = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
        _drive(a, CandleWatermark(), 0, 3, ETH)
        _drive(a, CandleWatermark(), 0, 3, BTC)
        payload = capture_state(a)
        assert [s["asset"] for s in payload["scanners"]] == sorted([BTC, ETH])
        for scanner in payload["scanners"]:
            assert scanner["consumed"] == sorted(scanner["consumed"])

    def test_multiple_assets_keep_separate_scanners(self):
        lifecycle = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
        wm = CandleWatermark()
        _drive(lifecycle, wm, 0, 4, BTC)
        _drive(lifecycle, wm, 0, 3, ETH)
        restored = restore_state(capture_state(lifecycle, wm))
        assert sorted(restored.lifecycle._scanners) == sorted([BTC, ETH])
        for asset in (BTC, ETH):
            assert (list(restored.lifecycle._scanners[asset]._history)
                    == list(lifecycle._scanners[asset]._history))
            assert (restored.lifecycle._scanners[asset]._consumed
                    == lifecycle._scanners[asset]._consumed)

    def test_a_scanner_disagreeing_with_the_config_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError, match="lookback"):
            restore_state(_mutate(payload, "scanners", 0, "lookback", value=7))

    def test_a_scanner_min_width_disagreement_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError, match="min_width"):
            restore_state(_mutate(payload, "scanners", 0, "min_width",
                                  value=0.5))

    def test_history_longer_than_the_deque_is_refused(self):
        """Silently discarding the oldest bars would change scan results."""
        payload = _valid_payload()
        rows = payload["scanners"][0]["history"]
        stretched = copy.deepcopy(payload)
        stretched["scanners"][0]["history"] = rows * 12
        with pytest.raises(StateIntegrityError, match="capacity"):
            restore_state(stretched)

    def test_a_missing_scanner_for_a_live_ob_is_refused(self):
        payload = _valid_payload()
        assert payload["live_obs"]
        with pytest.raises(StateIntegrityError, match="no scanner state"):
            restore_state(_mutate(payload, "scanners", value=[]))

    def test_a_duplicate_scanner_for_one_asset_is_refused(self):
        payload = _valid_payload()
        doubled = copy.deepcopy(payload)
        doubled["scanners"] = doubled["scanners"] + [
            copy.deepcopy(doubled["scanners"][0])]
        with pytest.raises(StateIntegrityError, match="duplicate scanner"):
            restore_state(doubled)


class TestProcessedCandleWatermark:
    """
    The marker `ManualSMCLifecycle` does not have.

    Nothing in the lifecycle records which candle it last processed, so this is
    the only thing standing between a crash and a replayed candle.
    """

    def test_advance_records_the_mark(self):
        wm = CandleWatermark()
        assert wm.last(BTC) is None
        mark = wm.advance(BTC, 5, T0)
        assert mark == CandleMark(asset=BTC, bar_idx=5, ts=T0)
        assert wm.last(BTC) == mark
        assert wm.assets() == [BTC]
        assert len(wm) == 1

    def test_is_processed_is_inclusive_of_the_mark(self):
        wm = CandleWatermark()
        wm.advance(BTC, 5, T0)
        assert wm.is_processed(BTC, 5)
        assert wm.is_processed(BTC, 4)
        assert not wm.is_processed(BTC, 6)
        assert not wm.is_processed(ETH, 0)

    def test_replaying_a_bar_is_refused(self):
        wm = CandleWatermark()
        wm.advance(BTC, 5, T0)
        with pytest.raises(WatermarkRegressionError, match="re-fill an entry"):
            wm.advance(BTC, 5, T0 + timedelta(hours=1))

    def test_going_backwards_in_bar_index_is_refused(self):
        wm = CandleWatermark()
        wm.advance(BTC, 5, T0)
        with pytest.raises(WatermarkRegressionError):
            wm.advance(BTC, 4, T0 + timedelta(hours=1))

    def test_a_non_advancing_timestamp_is_refused(self):
        """A higher bar index with a stale timestamp is still a regression."""
        wm = CandleWatermark()
        wm.advance(BTC, 5, T0)
        with pytest.raises(WatermarkRegressionError, match="timestamp"):
            wm.advance(BTC, 6, T0)

    def test_gaps_are_allowed(self):
        """Missing candles are a feed property, not state corruption."""
        wm = CandleWatermark()
        wm.advance(BTC, 5, T0)
        mark = wm.advance(BTC, 40, T0 + timedelta(hours=35))
        assert mark.bar_idx == 40

    def test_assets_are_independent(self):
        wm = CandleWatermark()
        wm.advance(BTC, 9, T0 + timedelta(hours=9))
        wm.advance(ETH, 1, T0 + timedelta(hours=1))
        assert wm.last(BTC).bar_idx == 9
        assert wm.last(ETH).bar_idx == 1

    def test_a_non_datetime_timestamp_is_refused(self):
        wm = CandleWatermark()
        with pytest.raises(MalformedStateError, match="expected datetime"):
            wm.advance(BTC, 0, "2025-01-01T00:00:00+00:00")

    def test_a_non_int_bar_index_is_refused(self):
        wm = CandleWatermark()
        with pytest.raises(MalformedStateError):
            wm.advance(BTC, True, T0)

    def test_watermark_round_trips(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        restored = restore_state(capture_state(lifecycle, watermark))
        assert restored.watermark.assets() == watermark.assets()
        for asset in watermark.assets():
            assert restored.watermark.last(asset) == watermark.last(asset)

    def test_the_restored_watermark_still_refuses_a_replay(self):
        """The marker is only useful if it survives with its rules intact."""
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        assert restored.watermark.is_processed(BTC, FILL_BAR)
        with pytest.raises(WatermarkRegressionError):
            restored.watermark.advance(BTC, FILL_BAR,
                                       T0 + timedelta(hours=FILL_BAR))
        restored.watermark.advance(BTC, FILL_BAR + 1,
                                   T0 + timedelta(hours=FILL_BAR + 1))

    def test_the_watermark_payload_is_sorted_by_asset(self):
        wm = CandleWatermark()
        wm.advance(ETH, 0, T0)
        wm.advance(BTC, 0, T0)
        payload = capture_state(ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC), wm)
        assert [m["asset"] for m in payload["watermark"]] == sorted([BTC, ETH])

    def test_a_duplicate_watermark_entry_is_refused(self):
        lifecycle, watermark, _ = _fresh(2)
        payload = capture_state(lifecycle, watermark)
        doubled = copy.deepcopy(payload)
        doubled["watermark"] = doubled["watermark"] + [
            copy.deepcopy(doubled["watermark"][0])]
        with pytest.raises(StateIntegrityError, match="duplicate mark"):
            restore_state(doubled)

    def test_capture_without_a_watermark_yields_an_empty_one(self):
        lifecycle, _, _ = _fresh(2)
        payload = capture_state(lifecycle)
        assert payload["watermark"] == []
        assert len(restore_state(payload).watermark) == 0

    def test_a_non_watermark_object_is_refused_at_capture(self):
        with pytest.raises(MalformedStateError, match="CandleWatermark"):
            capture_state(ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC), {"BTCUSD": 3})


class TestDeterministicContinuation:
    """
    Requirement 3: a restored run must be INDISTINGUISHABLE from one that was
    never interrupted — same events, same final state, same bytes.
    """

    @pytest.mark.parametrize("split", range(1, len(BARS)))
    def test_restoring_at_every_split_point_matches_an_unbroken_run(self, split):
        straight, straight_wm, straight_events = _fresh(len(BARS))

        broken, broken_wm, before = _fresh(split)
        resumed = restore_state_json(capture_state_json(broken, broken_wm))
        after = _drive(resumed.lifecycle, resumed.watermark, split, len(BARS))

        assert _events_repr(before + after) == _events_repr(straight_events)
        assert (dumps_state(capture_state(resumed.lifecycle, resumed.watermark))
                == dumps_state(capture_state(straight, straight_wm)))

    @pytest.mark.parametrize("split", range(1, len(BARS)))
    def test_restoring_twice_in_a_row_is_still_identical(self, split):
        """A crash after the resume must not compound any drift."""
        straight, straight_wm, _ = _fresh(len(BARS))
        broken, broken_wm, _ = _fresh(split)
        once = restore_state_json(capture_state_json(broken, broken_wm))
        twice = restore_state_json(
            capture_state_json(once.lifecycle, once.watermark))
        _drive(twice.lifecycle, twice.watermark, split, len(BARS))
        assert (dumps_state(capture_state(twice.lifecycle, twice.watermark))
                == dumps_state(capture_state(straight, straight_wm)))

    def test_restoring_mid_trade_produces_the_same_exit(self):
        """The interesting split: the snapshot holds a filled position."""
        broken, broken_wm, _ = _fresh(FILL_BAR + 1)
        assert broken.active_trade is not None
        resumed = restore_state(capture_state(broken, broken_wm))
        _drive(resumed.lifecycle, resumed.watermark, FILL_BAR + 1,
               len(BARS))
        straight, _, _ = _fresh(len(BARS))
        assert [(x.ob_id, x.outcome, x.reason_for_exit, x.is_ambiguous,
                 x.realized_r) for x in resumed.lifecycle.exits] == [
            (x.ob_id, x.outcome, x.reason_for_exit, x.is_ambiguous,
             x.realized_r) for x in straight.exits]

    def test_a_resumed_run_admits_no_second_trade(self):
        """Safety rule #13 survives the restore, not just the original run."""
        broken, broken_wm, _ = _fresh(FILL_BAR + 1)
        resumed = restore_state(capture_state(broken, broken_wm))
        assert resumed.lifecycle.has_active_trade()
        _drive(resumed.lifecycle, resumed.watermark, FILL_BAR + 1,
               FILL_BAR + 2)
        assert resumed.lifecycle.active_trade is None
        assert len(resumed.lifecycle.exits) == 1

    def test_multi_asset_continuation_is_deterministic(self):
        """
        Two assets, candles interleaved bar by bar — the same global order in
        both runs, so what is compared is the restore and nothing else.
        """
        assets = (BTC, ETH)
        straight = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
        straight_wm = CandleWatermark()
        straight_events = _drive_multi(straight, straight_wm, 0, len(BARS),
                                       assets)

        broken = ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)
        broken_wm = CandleWatermark()
        before = _drive_multi(broken, broken_wm, 0, 3, assets)
        resumed = restore_state_json(capture_state_json(broken, broken_wm))
        after = _drive_multi(resumed.lifecycle, resumed.watermark, 3,
                            len(BARS), assets)

        assert _events_repr(before + after) == _events_repr(straight_events)
        assert (dumps_state(capture_state(resumed.lifecycle, resumed.watermark))
                == dumps_state(capture_state(straight, straight_wm)))


class TestMalformedPayloadRejected:
    """Requirement 4, part one: nothing is ever coerced into shape."""

    @pytest.mark.parametrize("payload", [
        None, [], 3, 3.5, "state", True, (), set(),
    ])
    def test_a_non_object_payload_is_refused(self, payload):
        with pytest.raises(MalformedStateError):
            restore_state(payload)

    def test_a_non_string_key_is_refused(self):
        with pytest.raises(MalformedStateError, match="non-string keys"):
            restore_state({1: "one"})

    def test_invalid_json_text_is_refused(self):
        with pytest.raises(MalformedStateError, match="not valid JSON"):
            restore_state_json("{not json")

    def test_json_that_is_not_an_object_is_refused(self):
        with pytest.raises(MalformedStateError):
            restore_state_json("[1,2,3]")

    def test_non_text_input_is_refused(self):
        with pytest.raises(MalformedStateError, match="expected str or bytes"):
            loads_state({"schema": "MANUAL_SMC_STATE"})

    def test_bytes_input_is_accepted(self):
        text = capture_state_json(*_fresh(3)[:2])
        assert loads_state(text.encode("utf-8")) == loads_state(text)

    def test_an_unknown_top_level_field_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(UnknownFieldError, match="different schema"):
            restore_state(_mutate(payload, "surprise", value=1))

    def test_an_unknown_ob_field_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(UnknownFieldError):
            restore_state(_mutate(payload, "live_obs", 0, "extra", value=1))

    @pytest.mark.parametrize("key", ["live_obs", "exits", "scanners",
                                     "watermark"])
    def test_a_list_field_given_an_object_is_refused(self, key):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="expected a list"):
            restore_state(_mutate(payload, key, value={}))

    @pytest.mark.parametrize("key", ["config", "active_trade"])
    def test_an_object_field_given_a_list_is_refused(self, key):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="expected an object"):
            restore_state(_mutate(payload, key, value=[]))

    def test_a_string_where_a_float_belongs_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="expected float"):
            restore_state(_mutate(payload, "live_obs", 0, "entry_price",
                                  value="99.5"))

    def test_a_bool_where_an_int_belongs_is_refused(self):
        """`bool` is an `int` subclass; accepting it would restore True as 1."""
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="expected int"):
            restore_state(_mutate(payload, "live_obs", 0, "origin_bar_idx",
                                  value=True))

    def test_an_int_where_a_bool_belongs_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="expected bool"):
            restore_state(_mutate(payload, "live_obs", 0, "probe_confirmed",
                                  value=1))

    def test_a_non_iso_timestamp_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="ISO-8601"):
            restore_state(_mutate(payload, "live_obs", 0, "bos_dt",
                                  value="last tuesday"))

    def test_a_numeric_timestamp_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="ISO-8601"):
            restore_state(_mutate(payload, "live_obs", 0, "bos_dt",
                                  value=1735689600))

    def test_a_malformed_history_row_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="cells"):
            restore_state(_mutate(payload, "scanners", 0, "history",
                                  value=[[0, 1.0, 2.0]]))

    def test_a_malformed_consumed_key_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="origin_bar_idx"):
            restore_state(_mutate(payload, "scanners", 0, "consumed",
                                  value=[[BTC]]))

    def test_a_non_finite_float_cannot_be_captured(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        next(iter(lifecycle.live_obs.values())).mfe_from_proximal = float("nan")
        with pytest.raises(MalformedStateError, match="not finite"):
            capture_state(lifecycle, watermark)

    def test_infinity_cannot_be_captured(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        next(iter(lifecycle.live_obs.values())).ob_width = float("inf")
        with pytest.raises(MalformedStateError, match="not finite"):
            capture_state(lifecycle, watermark)

    def test_dumps_refuses_non_finite_numbers_as_a_second_barrier(self):
        """Even a hand-built payload cannot be written as invalid JSON."""
        payload = _valid_payload()
        with pytest.raises(MalformedStateError, match="strict JSON"):
            dumps_state(_mutate(payload, "live_obs", 0, "entry_price",
                                value=float("inf")))

    def test_capture_refuses_a_non_lifecycle(self):
        with pytest.raises(MalformedStateError, match="ManualSMCLifecycle"):
            capture_state({"live_obs": {}})

    def test_capture_refuses_a_non_scanner_in_the_scanner_table(self):
        lifecycle, watermark, _ = _fresh(2)
        lifecycle._scanners[ETH] = "not a scanner"
        with pytest.raises(MalformedStateError, match="ManualSpecBOSScanner"):
            capture_state(lifecycle, watermark)


#: The exact top-level key set, read off a real capture (not hand-copied).
TOP_KEYS: Tuple[str, ...] = tuple(capture_state(ManualSMCLifecycle(detector=DETECTOR_MANUAL_SPEC)))


class TestMissingFieldRejected:
    """Requirement 4, part two: an absent field is never defaulted."""

    def test_every_top_level_field_is_required(self):
        payload = _valid_payload()
        assert set(TOP_KEYS) == set(payload)
        for key in TOP_KEYS:
            with pytest.raises(MissingFieldError, match=key):
                restore_state(_drop(payload, key))

    def test_an_empty_object_names_every_missing_field(self):
        with pytest.raises(MissingFieldError) as exc:
            restore_state({})
        for key in TOP_KEYS:
            assert key in str(exc.value)

    @pytest.mark.parametrize("field", expected_keys(ManualOBRecord))
    def test_every_ob_field_is_required(self, field):
        payload = _valid_payload()
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "live_obs", 0, field))

    @pytest.mark.parametrize("field", expected_keys(ManualActiveTrade))
    def test_every_active_trade_field_is_required(self, field):
        payload = _valid_payload()
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "active_trade", field))

    @pytest.mark.parametrize("field", expected_keys(ManualSpecConfig))
    def test_every_config_field_is_required(self, field):
        payload = _valid_payload()
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "config", field))

    @pytest.mark.parametrize("field", expected_keys(ManualTradeExit))
    def test_every_exit_field_is_required(self, field):
        lifecycle, watermark, _ = _fresh(len(BARS))
        payload = capture_state(lifecycle, watermark)
        assert payload["exits"]
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "exits", 0, field))

    @pytest.mark.parametrize("field", ["asset", "lookback", "min_width",
                                       "history", "consumed"])
    def test_every_scanner_field_is_required(self, field):
        payload = _valid_payload()
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "scanners", 0, field))

    @pytest.mark.parametrize("field", ["asset", "bar_idx", "ts"])
    def test_every_watermark_field_is_required(self, field):
        lifecycle, watermark, _ = _fresh(3)
        payload = capture_state(lifecycle, watermark)
        assert payload["watermark"]
        with pytest.raises(MissingFieldError, match=field):
            restore_state(_drop(payload, "watermark", 0, field))

    def test_a_null_is_not_an_absent_field(self):
        """`None` where a value belongs is malformed, not missing."""
        payload = _valid_payload()
        with pytest.raises(MalformedStateError):
            restore_state(_mutate(payload, "live_obs", 0, "entry_price",
                                  value=None))

    def test_an_optional_field_still_has_to_be_present(self):
        payload = _valid_payload()
        assert payload["live_obs"][0]["first_touch_dt"] is not None
        with pytest.raises(MissingFieldError, match="first_touch_dt"):
            restore_state(_drop(payload, "live_obs", 0, "first_touch_dt"))


class TestUnknownEnumRejected:
    """
    Requirement 4, part three.

    Guessing which state a crashed process was in is exactly the ambiguity that
    must never be resolved by inference (safety rule #16).
    """

    @pytest.mark.parametrize("value", [
        "awaiting_displacement",        # case-folded
        "AWAITING",                     # truncated
        " AWAITING_DISPLACEMENT",       # whitespace
        "LIMIT-RESTING",                # punctuation
        "PENDING",                      # invented
        "",
    ])
    def test_an_unrecognised_state_string_is_refused(self, value):
        payload = _valid_payload()
        with pytest.raises(UnknownEnumValueError, match="ManualOBState"):
            restore_state(_mutate(payload, "live_obs", 0, "state", value=value))

    @pytest.mark.parametrize("value", [0, 1, True, None, ["TRADE_ACTIVE"]])
    def test_a_non_string_state_is_refused(self, value):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError):
            restore_state(_mutate(payload, "live_obs", 0, "state", value=value))

    def test_the_refusal_lists_the_allowed_values(self):
        payload = _valid_payload()
        with pytest.raises(UnknownEnumValueError) as exc:
            restore_state(_mutate(payload, "live_obs", 0, "state",
                                  value="NOPE"))
        for state in ManualOBState:
            assert state.value in str(exc.value)

    def test_every_real_member_decodes_to_itself(self):
        for state in ManualOBState:
            assert decode_ob_state(state.value, "x") is state
            assert encode_ob_state(state, "x") == state.value

    def test_only_between_candle_states_are_persistable(self):
        """
        A valid enum value can still be an invalid SNAPSHOT value: OBs in a
        terminal state are popped inside `process_candle`, so persisting one
        means the capture was torn mid-candle.
        """
        lifecycle, watermark, _ = _fresh(2)
        payload = capture_state(lifecycle, watermark)
        assert len(payload["live_obs"]) == 1
        for state in (ManualOBState.AWAITING_DISPLACEMENT,
                      ManualOBState.LIMIT_RESTING):
            restore_state(_mutate(payload, "live_obs", 0, "state",
                                  value=state.value))
        for state in (ManualOBState.TRADE_CLOSED, ManualOBState.INVALIDATED):
            with pytest.raises(StateIntegrityError, match="mid-candle"):
                restore_state(_mutate(payload, "live_obs", 0, "state",
                                      value=state.value))


class TestSchemaVersionRejected:
    """Requirement 4/5: an explicit, checked schema and strategy identity."""

    # v1, v2 AND v3 are in this list on purpose. v2 added the mandatory
    # `activation` block, and a v1 payload does not record which activation mode
    # produced it. v3 added the mandatory `invalidations` list, and a v2 payload
    # does not record which order blocks the operator permanently deleted —
    # restoring one would have to assume the list is empty, which resurrects
    # every deleted order block as tradable. v4 added the mandatory `detector`
    # name, and a v3 payload does not record WHICH STRUCTURE ALGORITHM decided
    # what an order block was; the two shipped detectors disagree about the zone
    # (half origin candle vs whole origin candle), so the stop edge and the
    # derived leverage of a restored block would both be guesses. Being refused
    # rather than upgraded is the fail-closed direction in all three cases
    # (rule #13). A v3 snapshot must be recaptured, not migrated.
    #
    # v5 adds the mandatory `ob_structure_tags` side map and replaces the
    # O(bars) LuxAlgo scanner body with the bounded causal one. A v4 payload
    # carries neither: its blocks have no BOS/CHoCH label to restore, and its
    # scanner body has no `structure` block, so this build would have to invent
    # the Pine scalars from a replay it cannot prove equals what was captured.
    # So v4 joins the refused list rather than being best-effort upgraded.
    @pytest.mark.parametrize("version", [0, 1, 2, 3, 4, 99, -1, 1000])
    def test_an_unsupported_version_is_refused(self, version):
        payload = _valid_payload()
        with pytest.raises(UnsupportedSchemaVersionError, match="not restorable"):
            restore_state(_mutate(payload, "schema_version", value=version))

    @pytest.mark.parametrize("version", ["1", 1.0, None, True, [1]])
    def test_a_non_int_version_is_refused(self, version):
        payload = _valid_payload()
        with pytest.raises(MalformedStateError):
            restore_state(_mutate(payload, "schema_version", value=version))

    @pytest.mark.parametrize("schema", ["SMC_STATE", "MANUAL_SMC",
                                        "manual_smc_state", ""])
    def test_a_foreign_schema_discriminator_is_refused(self, schema):
        payload = _valid_payload()
        with pytest.raises(UnsupportedSchemaVersionError,
                           match="not Manual SMC state"):
            restore_state(_mutate(payload, "schema", value=schema))

    @pytest.mark.parametrize("name", ["SMC", "manual_smc", "LUXALGO_SMC", ""])
    def test_a_foreign_strategy_name_is_refused(self, name):
        """The approved identity policy: MANUAL_SMC is not "SMC"."""
        payload = _valid_payload()
        with pytest.raises(UnsupportedSchemaVersionError,
                           match="strategy_name"):
            restore_state(_mutate(payload, "strategy_name", value=name))

    @pytest.mark.parametrize("version", ["2.1", "1.0", "1.0.1", ""])
    def test_a_foreign_strategy_version_is_refused(self, version):
        payload = _valid_payload()
        with pytest.raises(UnsupportedSchemaVersionError,
                           match="strategy_version"):
            restore_state(_mutate(payload, "strategy_version", value=version))

    def test_the_current_version_is_declared_supported(self):
        assert MANUAL_SMC_STATE_SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS
        assert isinstance(SUPPORTED_SCHEMA_VERSIONS, frozenset)
        assert all(isinstance(v, int) for v in SUPPORTED_SCHEMA_VERSIONS)

    def test_the_identity_is_the_approved_one(self):
        assert MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC"
        assert MANUAL_SMC_STRATEGY_VERSION == "1.0.0"
        payload = _valid_payload()
        assert payload["strategy_name"] == "MANUAL_SMC"
        assert payload["strategy_version"] == "1.0.0"

    def test_a_config_change_between_crash_and_resume_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError, match="change strategy"):
            restore_state(payload,
                          expected_config=ManualSpecConfig(lookback=20))

    def test_a_matching_expected_config_is_accepted(self):
        payload = _valid_payload()
        restored = restore_state(payload, expected_config=ManualSpecConfig())
        assert restored.config == ManualSpecConfig()

    def test_assert_config_compatible_names_every_difference(self):
        with pytest.raises(StateIntegrityError) as exc:
            assert_config_compatible(
                ManualSpecConfig(),
                ManualSpecConfig(lookback=20, fee_rate=0.001))
        assert "lookback" in str(exc.value)
        assert "fee_rate" in str(exc.value)

    def test_a_non_config_expectation_is_refused(self):
        with pytest.raises(MalformedStateError, match="ManualSpecConfig"):
            restore_state(_valid_payload(), expected_config={"lookback": 10})


class TestStateIntegrityRejected:
    """
    The payload parses, but the state it describes cannot be resumed.

    These are the invariants behind safety rules #13 and #14, plus the
    torn-capture detector.
    """

    def test_a_duplicate_ob_id_is_refused(self):
        payload = _valid_payload()
        doubled = copy.deepcopy(payload)
        doubled["live_obs"] = doubled["live_obs"] + [
            copy.deepcopy(doubled["live_obs"][0])]
        with pytest.raises(StateIntegrityError, match="duplicate ob_id"):
            restore_state(doubled)

    def test_two_trade_active_obs_are_refused(self):
        """Safety rule #13: never two active trades for the same account."""
        payload = _valid_payload()
        both = _mutate(payload, "live_obs", 1, "state",
                       value=ManualOBState.TRADE_ACTIVE.value)
        with pytest.raises(StateIntegrityError, match="safety rule #13"):
            restore_state(both)

    def test_an_active_trade_with_no_active_ob_is_refused(self):
        payload = _valid_payload()
        assert (payload["live_obs"][0]["state"]
                == ManualOBState.TRADE_ACTIVE.value)
        orphaned = _mutate(payload, "live_obs", 0, "state",
                           value=ManualOBState.LIMIT_RESTING.value)
        with pytest.raises(StateIntegrityError, match="TRADE_ACTIVE"):
            restore_state(orphaned)

    def test_an_active_ob_with_no_active_trade_is_refused(self):
        """
        Safety rule #14: a filled position resumed with no trade object could
        never be closed, and the lock would never be released.
        """
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError, match="no trade to close"):
            restore_state(_mutate(payload, "active_trade", value=None))

    def test_a_dangling_active_trade_reference_is_refused(self):
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError, match="dangling"):
            restore_state(_mutate(payload, "active_trade", "ob_ref",
                                  value="MANUAL_BTCUSD_SHORT_999_999"))

    def test_an_active_trade_referencing_the_wrong_ob_is_refused(self):
        payload = _valid_payload()
        other = payload["live_obs"][1]["ob_id"]
        with pytest.raises(StateIntegrityError, match="TRADE_ACTIVE OB"):
            restore_state(_mutate(payload, "active_trade", "ob_ref",
                                  value=other))

    @pytest.mark.parametrize("field", ["entry_price", "sl_price", "tp_price"])
    def test_an_active_trade_price_disagreeing_with_its_ob_is_refused(self, field):
        payload = _valid_payload()
        drifted = _mutate(payload, "active_trade", field,
                          value=payload["active_trade"][field] + 1.0)
        with pytest.raises(StateIntegrityError, match="disagrees with its OB"):
            restore_state(drifted)

    def test_an_active_trade_direction_disagreeing_is_refused(self):
        payload = _valid_payload()
        flipped = _mutate(payload, "active_trade", "direction", value="LONG")
        with pytest.raises(StateIntegrityError, match="disagrees with its OB"):
            restore_state(flipped)

    def test_an_ob_created_after_the_watermark_is_refused(self):
        """
        THE TORN CAPTURE. `process_candle` and `watermark.advance` are two
        operations; a crash between them leaves the lifecycle ahead of the
        marker, and resuming would replay a candle.
        """
        payload = _valid_payload()
        assert payload["watermark"][0]["bar_idx"] == FILL_BAR
        torn = _mutate(payload, "watermark", 0, "bar_idx", value=1)
        torn = _mutate(torn, "watermark", 0, "ts",
                       value=(T0 + timedelta(hours=1)).isoformat())
        with pytest.raises(StateIntegrityError, match="torn"):
            restore_state(torn)

    def test_scanner_history_after_the_watermark_is_refused(self):
        """The same detector, reached through the scanner rather than the pool."""
        payload = _valid_payload()
        torn = _mutate(payload, "watermark", 0, "bar_idx", value=2)
        torn = _mutate(torn, "watermark", 0, "ts",
                       value=(T0 + timedelta(hours=2)).isoformat())
        with pytest.raises(StateIntegrityError, match="scanner history"):
            restore_state(torn)

    def test_an_asset_with_no_watermark_is_not_checked(self):
        """Gaps are allowed; a never-marked asset is simply not constrained."""
        payload = _valid_payload()
        assert payload["live_obs"]
        restored = restore_state(_mutate(payload, "watermark", value=[]))
        assert len(restored.watermark) == 0

    def test_a_clean_capture_passes_every_integrity_check(self):
        """The negative control: all of the above must not fire on real state."""
        for bars in range(1, len(BARS) + 1):
            lifecycle, watermark, _ = _fresh(bars)
            restore_state(capture_state(lifecycle, watermark))

    def test_every_integrity_failure_is_a_state_error(self):
        assert issubclass(StateIntegrityError, StateError)
        assert issubclass(MalformedStateError, StateError)
        assert issubclass(MissingFieldError, MalformedStateError)
        assert issubclass(UnknownFieldError, MalformedStateError)
        assert issubclass(UnknownEnumValueError, MalformedStateError)
        assert issubclass(UnsupportedSchemaVersionError, StateError)
        assert issubclass(StateSchemaError, StateError)
        assert issubclass(WatermarkRegressionError, StateError)

    def test_a_failed_restore_returns_no_lifecycle(self):
        """Fail closed: never a partially-populated lifecycle."""
        payload = _valid_payload()
        with pytest.raises(StateIntegrityError):
            restore_state(_mutate(payload, "active_trade", value=None))


class TestExactness:
    """
    Requirement 2: values survive exactly, not approximately.

    A price that drifts by one ULP across a restart is a different price to the
    lifecycle's comparisons, so "close enough" is not a passing grade.
    """

    def test_every_float_survives_bit_for_bit(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        restored = restore_state_json(capture_state_json(lifecycle, watermark))
        for exit_a, exit_b in zip(lifecycle.exits, restored.lifecycle.exits):
            for fld in ("entry_price", "sl_price", "tp_price", "exit_price",
                        "realized_r"):
                a = getattr(exit_a, fld)
                b = getattr(exit_b, fld)
                assert a.hex() == b.hex(), fld

    def test_awkward_floats_survive_a_json_round_trip(self):
        """
        Floats that are NOT exactly representable in decimal. `repr` is the
        shortest string that round-trips, which is why JSON is exact here.
        """
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset=BTC, bos_bar_idx=3, bos_dt=T0 + timedelta(hours=3),
            origin_bar_idx=1, origin_dt=T0 + timedelta(hours=1),
            direction="SHORT", ob_top=0.1 + 0.2, ob_bottom=0.1, cfg=cfg)
        assert ob.ob_width != 0.2                      # guard the fixture
        payload = json.loads(json.dumps(encode_dataclass(ob)))
        rebuilt = decode_dataclass(ManualOBRecord, payload, "ob")
        assert rebuilt == ob
        for fld in ("ob_top", "ob_bottom", "ob_width", "proximal", "distal",
                    "entry_price", "sl_price", "tp_price", "sl_dist_pct",
                    "theoretical_leverage", "applied_leverage"):
            assert getattr(rebuilt, fld).hex() == getattr(ob, fld).hex(), fld

    def test_decimal_scale_is_preserved_not_just_the_value(self):
        """
        An on-grid exchange price keeps the tick's own scale, so a quantized
        `Decimal("0.5000")` does not come back as `Decimal("0.5")`.
        """
        for text in ("0.5000", "0.5", "100", "1E+2", "-0.0001", "0.00"):
            value = Decimal(text)
            assert encode_decimal(value, "x") == text
            rebuilt = decode_decimal(text, "x")
            assert rebuilt == value
            assert rebuilt.as_tuple() == value.as_tuple()
            assert str(rebuilt) == text

    def test_a_float_is_never_accepted_as_a_decimal(self):
        with pytest.raises(MalformedStateError, match="silently round"):
            encode_decimal(0.5, "x")

    @pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "sNaN"])
    def test_a_non_finite_decimal_is_refused(self, text):
        with pytest.raises(MalformedStateError, match="not finite"):
            decode_decimal(text, "x")

    def test_a_non_decimal_string_is_refused(self):
        with pytest.raises(MalformedStateError, match="not a Decimal"):
            decode_decimal("0.5.0", "x")

    @pytest.mark.parametrize("moment", [
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc),
        datetime(2025, 6, 30, 12, 0, 0, 1,
                 tzinfo=timezone(timedelta(hours=-5, minutes=-30))),
        datetime(2025, 6, 30, 12, 0, 0, 500000),          # naive
    ])
    def test_datetimes_survive_to_the_microsecond_with_their_offset(self, moment):
        text = encode_datetime(moment, "x")
        rebuilt = decode_datetime(text, "x")
        assert rebuilt == moment
        assert rebuilt.microsecond == moment.microsecond
        assert rebuilt.utcoffset() == moment.utcoffset()
        assert rebuilt.tzinfo == moment.tzinfo
        assert encode_datetime(rebuilt, "x") == text

    def test_timestamps_are_not_normalised_to_utc(self):
        """Normalising would compare equal but serialise differently."""
        tz = timezone(timedelta(hours=5, minutes=30))
        moment = datetime(2025, 3, 1, 9, 15, tzinfo=tz)
        assert encode_datetime(moment, "x") == "2025-03-01T09:15:00+05:30"

    def test_enum_states_survive_as_their_exact_member(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state_json(capture_state_json(lifecycle, watermark))
        for ob_id, original in lifecycle.live_obs.items():
            rebuilt = restored.lifecycle.live_obs[ob_id]
            assert rebuilt.state is original.state
            assert isinstance(rebuilt.state, ManualOBState)

    def test_optional_fields_round_trip_as_none_and_as_values(self):
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        restored = restore_state(capture_state(lifecycle, watermark))
        seen_none = seen_value = False
        for ob_id, original in lifecycle.live_obs.items():
            rebuilt = restored.lifecycle.live_obs[ob_id]
            for fld in ("displacement_confirmed_dt", "displacement_confirmed_bar",
                        "limit_active_from_bar", "first_touch_dt"):
                a, b = getattr(original, fld), getattr(rebuilt, fld)
                assert a == b
                seen_none |= a is None
                seen_value |= a is not None
        assert seen_none and seen_value            # both branches exercised

    def test_the_serialised_form_is_stable_across_captures(self):
        lifecycle, watermark, _ = _fresh(5)
        assert (capture_state_json(lifecycle, watermark)
                == capture_state_json(lifecycle, watermark))

    def test_two_independent_runs_serialise_identically(self):
        a = capture_state_json(*_fresh(len(BARS))[:2])
        b = capture_state_json(*_fresh(len(BARS))[:2])
        assert a == b


class TestCodecDriftGuard:
    """
    A field added to a persisted dataclass tomorrow must not vanish silently.

    Without this, a new `ManualOBRecord` field would be dropped at capture and
    restored as its dataclass default — a state divergence with no error.
    """

    @pytest.mark.parametrize("cls", [ManualOBRecord, ManualSpecConfig,
                                     ManualActiveTrade, ManualTradeExit,
                                     CandleMark])
    def test_payload_keys_follow_the_field_list(self, cls):
        """Only a by-reference field is renamed; nothing is added or dropped."""
        assert expected_keys(cls) == tuple(
            f.name + ("_ref" if f.type == "ManualOBRecord" else "")
            for f in dataclasses.fields(cls))

    def test_encoding_a_real_instance_of_each_dataclass_succeeds(self):
        """
        THE drift guard. `encode_dataclass` walks every declared field and
        raises `StateSchemaError` for any annotation without a codec, so this
        fails the moment a field of an unhandled type is added to any of them.
        """
        lifecycle, watermark, _ = _fresh(len(BARS))
        encode_dataclass(lifecycle.cfg)
        encode_dataclass(lifecycle.exits[0])
        encode_dataclass(watermark.last(BTC))
        encode_dataclass(next(iter(lifecycle.live_obs.values())))
        mid, mid_wm, _ = _fresh(FILL_BAR + 1)
        encode_dataclass(mid.active_trade)

    def test_an_uncovered_annotation_is_refused_at_capture(self):
        @dataclasses.dataclass
        class _Drifted:
            ob_id: str
            surprise: complex

        with pytest.raises(StateSchemaError, match="no state codec"):
            encode_dataclass(_Drifted(ob_id="x", surprise=1j))

    def test_an_uncovered_annotation_is_refused_at_restore(self):
        @dataclasses.dataclass
        class _Drifted:
            ob_id: str
            surprise: complex

        with pytest.raises(StateSchemaError, match="no state codec"):
            decode_dataclass(_Drifted, {"ob_id": "x", "surprise": 1},
                             "drifted")

    def test_the_refusal_explains_the_consequence(self):
        @dataclasses.dataclass
        class _Drifted:
            surprise: complex

        with pytest.raises(StateSchemaError) as exc:
            encode_dataclass(_Drifted(surprise=1j))
        assert "dataclass default" in str(exc.value)

    def test_a_non_dataclass_is_refused(self):
        with pytest.raises(StateSchemaError, match="dataclass instance"):
            encode_dataclass({"ob_id": "x"})

    def test_a_dataclass_class_is_not_an_instance(self):
        with pytest.raises(StateSchemaError, match="dataclass instance"):
            encode_dataclass(ManualOBRecord)

    def test_the_by_reference_field_is_the_only_renamed_key(self):
        keys = expected_keys(ManualActiveTrade)
        assert "ob_ref" in keys
        assert "ob" not in keys
        assert [k for k in keys if k.endswith("_ref")] == ["ob_ref"]

    def test_expected_keys_matches_field_order(self):
        payload = _valid_payload()
        assert (tuple(payload["live_obs"][0])
                == expected_keys(ManualOBRecord))
        assert tuple(payload["config"]) == expected_keys(ManualSpecConfig)
        assert (tuple(payload["active_trade"])
                == expected_keys(ManualActiveTrade))

    def test_the_ob_state_enum_is_fully_accounted_for(self):
        """
        If a sixth `ManualOBState` is ever added, this fails and forces an
        explicit decision about whether a snapshot may contain it.
        """
        assert [s.value for s in ManualOBState] == [
            "AWAITING_DISPLACEMENT", "LIMIT_RESTING", "TRADE_ACTIVE",
            "TRADE_CLOSED", "INVALIDATED"]

    def test_the_top_level_key_set_is_pinned(self):
        assert TOP_KEYS == (
            "schema", "schema_version", "strategy_name", "strategy_version",
            "config", "activation", "detector", "scanners", "live_obs",
            "ob_structure_tags", "active_trade", "exits",
            "last_trade_closed_dt", "watermark", "invalidations")


class TestModuleIndependence:
    """No exchange, DB, execution, runtime, adapter, Java or live wiring."""

    def test_imports_are_stdlib_plus_five_siblings_only(self):
        assert _imported_modules() == [
            "__future__",
            "dataclasses",
            "datetime",
            "decimal",
            "hashlib",
            "json",
            "math",
            "quantedge.strategy.manual_smc.invalidation",
            "quantedge.strategy.manual_smc.lifecycle",
            "quantedge.strategy.manual_smc.luxalgo_structure",
            "quantedge.strategy.manual_smc.models",
            "quantedge.strategy.manual_smc.scanner",
            "typing",
        ]

    def test_no_persistence_or_transport_names_appear(self):
        """
        Requirement 6: no DB persistence yet. `capture_state` returns a dict and
        `dumps_state` returns a string; where those bytes go is Step 6.
        """
        code = _code_without_strings()
        for banned in ("sqlalchemy", "psycopg", "sqlite3", "httpx", "requests",
                       "quantedge.execution", "delta_client", "asyncio",
                       "subprocess", "socket", "pickle", "environ",
                       "INSERT", "SELECT", "UPDATE", "commit(", "session.",
                       "cursor", "connection", "engine."):
            assert banned not in code, f"{banned!r} appears in state.py"

    def test_no_file_or_process_io_appears(self):
        code = _code_without_strings()
        for banned in ("open(", "Path(", "pathlib", "shutil", "tempfile",
                       "os.", "sys.", "write_text", "read_text"):
            assert banned not in code, f"{banned!r} appears in state.py"

    def test_no_execution_authority_or_runtime_wiring_appears(self):
        code = _code_without_strings()
        for banned in ("place_order", "cancel_order", "kill_switch", "api_key",
                       "secret", "expiresAt", "expires_at", "live_loop",
                       "OrderExecutionService", "PortfolioLock", "quantize",
                       "ProductSpecification"):
            assert banned not in code, f"{banned!r} appears in state.py"

    def test_the_modules_own_dependency_closure_has_no_exchange_transport(self):
        """
        Measure `state.py`'s OWN closure.

        Run in a subprocess so an httpx already loaded by another test module
        cannot mask the result, and with STUB parent packages pre-seeded into
        `sys.modules` so the pre-existing `quantedge/__init__.py` (which does
        `from . import execution`) does not execute. What is left is exactly
        what `state.py` and its siblings actually need.
        """
        import subprocess
        import sys
        code = (
            "import sys, types, pathlib;"
            "root=pathlib.Path('src').resolve();"
            "sys.path.insert(0,str(root));"
            "[sys.modules.setdefault(n, _m) for n, _m in "
            "[(n, types.ModuleType(n)) for n in ("
            "'quantedge','quantedge.strategy','quantedge.strategy.manual_smc')]];"
            "sys.modules['quantedge'].__path__=[str(root/'quantedge')];"
            "sys.modules['quantedge.strategy'].__path__="
            "[str(root/'quantedge'/'strategy')];"
            "sys.modules['quantedge.strategy.manual_smc'].__path__="
            "[str(root/'quantedge'/'strategy'/'manual_smc')];"
            "import quantedge.strategy.manual_smc.state as s;"
            "s.restore_state(s.capture_state(s.ManualSMCLifecycle()));"
            "third=sorted(n for n in sys.modules "
            "if getattr(sys.modules[n],'__file__',None) "
            "and 'site-packages' in str(sys.modules[n].__file__));"
            "bad=[m for m in ('httpx','cryptography','quantedge.execution',"
            "'quantedge.execution.delta_client',"
            "'quantedge.execution.validation','sqlalchemy','psycopg') "
            "if m in sys.modules];"
            "print('LOADED:'+','.join(bad+third))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "LOADED:" in out.stdout
        assert out.stdout.strip().split("LOADED:")[1] == ""

    def test_it_adds_no_transport_beyond_the_pre_existing_package_import(self):
        """
        PRE-EXISTING FINDING, pinned rather than hidden.

        `quantedge/__init__.py` ends with `from . import execution`, so merely
        importing the top-level `quantedge` package already loads httpx, the
        signed Delta REST client and the AESGCM credential crypto. That is not
        caused by this module; what this test proves is that `state.py` adds
        NOTHING to that baseline.
        """
        import subprocess
        import sys
        probe = (
            "import sys;"
            "{stmt};"
            "print('SET:'+','.join(sorted(m for m in sys.modules "
            "if m.startswith(('httpx','cryptography','quantedge.execution')))))"
        )

        def _snapshot(stmt: str) -> set:
            out = subprocess.run(
                [sys.executable, "-c", probe.format(stmt=stmt)],
                cwd=str(Path(__file__).parent.parent),
                capture_output=True, text=True, timeout=120)
            assert out.returncode == 0, out.stderr
            body = out.stdout.strip().split("SET:")[1]
            return set(filter(None, body.split(",")))

        baseline = _snapshot("import quantedge")
        with_module = _snapshot("import quantedge.strategy.manual_smc.state")
        assert with_module == baseline
        assert "httpx" in baseline

    def test_the_module_declares_no_database_schema(self):
        """Requirement 6/8: no SQL, no migration, no table names."""
        lowered = MODULE_SRC.lower()
        for banned in ("create table", "flyway", "migration", "jdbc",
                       "postgres", "psql", " orm "):
            assert banned not in lowered, f"{banned!r} appears in state.py"

    def test_nothing_outside_this_package_is_referenced(self):
        for module in _imported_modules():
            if module.startswith("quantedge"):
                assert module.startswith("quantedge.strategy.manual_smc."), (
                    f"{module} is outside the manual_smc package")


# ===========================================================================
# SCHEMA 4 — THE DETECTOR IS PART OF THE STATE
#
# Everything above this line drives `DETECTOR_MANUAL_SPEC`, because the 7-bar
# `BARS` fixture was built for the half-candle zone and for single-candle close
# displacement. The shipped default is now `DETECTOR_LUXALGO_INTERNAL`, whose
# scanner is a Pine reconstruction: ~25 mutable attributes, an ATR(200) RMA and
# nine audit trails.
#
# It is NO LONGER persisted by replay. Schema 5 writes the Pine reconstruction
# out field-by-field through `LuxAlgoInternalStructure.capture_state()` --
# pruned to `causal_watermark()`, the position proved unreachable by any future
# read -- and a restore INSTALLS that structure instead of re-deriving it by
# re-running `scan()`. A pruned candle log rides alongside it for the one field
# the structure does not retain (`open`) and for the bar-index list the
# torn-capture check reads; the two halves are cross-checked against each other
# on restore, and a digest is diffed afterwards.
#
# The candles below are generated, not hand-built, because LuxAlgo needs a swing
# window of 50 and an ATR warm-up before it will admit anything at all. The
# generator is seeded, so the series is fixed; what is asserted about it is
# measured from the engine, never predicted.
# ===========================================================================
LUX = "ETHUSD"
LUX_BARS = 320
LUX_SPLIT = 200


def _lux_candles(n: int = LUX_BARS, seed: int = 771, base: int = 0,
                 t0: datetime = T0) -> List[Tuple[int, datetime, float, float,
                                                  float, float]]:
    """A seeded OHLC series long enough for the LuxAlgo warm-up."""
    import math
    import random
    rng = random.Random(seed)
    price = 3000.0
    out = []
    for i in range(n):
        o = price
        c = o + math.sin(i / 7.0) * 12.0 + rng.uniform(-9.0, 9.0)
        h = max(o, c) + rng.uniform(0.0, 6.0)
        l = min(o, c) - rng.uniform(0.0, 6.0)
        out.append((base + i, t0 + timedelta(hours=i), o, h, l, c))
        price = c
    return out


def _lux_lifecycle(**kw) -> ManualSMCLifecycle:
    return ManualSMCLifecycle(assets=[LUX], detector=DETECTOR_LUXALGO_INTERNAL,
                              **kw)


def _lux_drive(lifecycle: ManualSMCLifecycle, rows, watermark=None):
    """Feed generated rows, advancing the watermark exactly as a caller must."""
    watermark = CandleWatermark() if watermark is None else watermark
    events: List[ManualLifecycleEvent] = []
    for bar_idx, ts, o, h, l, c in rows:
        events += lifecycle.process_candle(LUX, bar_idx, ts, o, h, l, c)
        watermark.advance(LUX, bar_idx, ts)
    return events, watermark


def _lux_snapshot(bars: int = LUX_SPLIT) -> Tuple[Dict[str, Any],
                                                  ManualSMCLifecycle,
                                                  CandleWatermark]:
    lifecycle = _lux_lifecycle()
    events, watermark = _lux_drive(lifecycle, _lux_candles()[:bars])
    del events
    return capture_state(lifecycle, watermark), lifecycle, watermark



class TestLuxAlgoScannerStateRoundTrip:
    """
    The shipped detector's state must survive a restart. Before this, the codec
    refused a `LuxAlgoInternalOBScanner` outright, so promoting LuxAlgo to the
    default would have silently cost production its recoverability.
    """

    def test_the_snapshot_names_the_luxalgo_detector(self):
        payload, _, _ = _lux_snapshot()
        assert payload["detector"] == DETECTOR_LUXALGO_INTERNAL
        assert payload["schema_version"] == MANUAL_SMC_STATE_SCHEMA_VERSION == 5

    def test_the_scanner_block_is_the_bounded_structure_not_the_spec_shape(self):
        """
        A LuxAlgo block persists `structure` + `rejections` + `bars` + `digest`;
        a ManualSpec block persists `history` + `consumed`. The two shapes are
        disjoint on purpose, so a block cannot be read as the wrong detector's.

        `bars` is now the PRUNED window, not the whole log: 200 bars in, 109
        retained. The gap is the causal watermark, and the two numbers are both
        pinned so that a change to the pruning shows up here as a measured
        difference rather than as a silently smaller payload.
        """
        payload, _, _ = _lux_snapshot()
        block = payload["scanners"][0]
        assert sorted(block) == ["asset", "bars", "digest", "lookback",
                                 "min_width", "rejections", "structure"]
        assert block["asset"] == LUX
        assert len(block["bars"]) == 109 < LUX_SPLIT
        assert block["structure"]["asset"] == LUX
        assert len(block["structure"]["arrays"]["times"]) == len(block["bars"])
        assert block["rejections"] == {"non_positive_width": 0,
                                       "below_min_width": 0}
        spec_block = capture_state(_fresh(len(BARS))[0])["scanners"][0]
        assert sorted(spec_block) == ["asset", "consumed", "history", "lookback",
                                      "min_width"]
        assert set(block) & set(spec_block) == {"asset", "lookback", "min_width"}

    def test_the_digest_records_what_the_install_has_to_reproduce(self):
        """The measured digest of the seeded 200-bar series.

        `stream_bars` is the whole stream (200); `retained_bars` is what the
        payload carries (109); `base_bar_idx` is REBASED by the 91 pruned bars
        while `last_bar_idx` stays absolute. All four are asserted together
        because their arithmetic is the whole claim: 91 + 109 == 200, and
        91 + 109 - 1 == 199.
        """
        payload, _, _ = _lux_snapshot()
        digest = payload["scanners"][0]["digest"]
        assert digest["stream_bars"] == 200
        assert digest["retained_bars"] == 109
        assert digest["base_bar_idx"] == 91 and digest["last_bar_idx"] == 199
        assert digest["base_bar_idx"] + digest["retained_bars"] == 200
        assert (digest["base_bar_idx"] + digest["retained_bars"] - 1
                == digest["last_bar_idx"])
        assert digest["ob_seq"] == 5
        assert digest["live_obs"] == 3
        assert digest["internal_trend"] == 1 and digest["swing_trend"] == 0
        assert digest["rejected_non_positive_width"] == 0
        assert digest["rejected_below_min_width"] == 0
        assert len(digest["bars_sha256"]) == 64
        assert len(digest["structure_sha256"]) == 64
        assert len(digest["live_ob_sha256"]) == 64
        # `all_obs` is deliberately gone: `all_order_blocks` is an audit list
        # that a bounded snapshot does not carry, and `_restore_order_blocks`
        # rebuilds it from the live store, so the field could only mismatch.
        # `ob_seq` above is the creation count that survives.
        assert "all_obs" not in digest

    def test_the_snapshot_is_json_and_restores_a_luxalgo_scanner(self):
        payload, _, _ = _lux_snapshot()
        restored = restore_state_json(dumps_state(payload))
        assert restored.lifecycle.detector == DETECTOR_LUXALGO_INTERNAL
        scanner = restored.lifecycle._scanners[LUX]
        assert isinstance(scanner, LuxAlgoInternalOBScanner)
        assert not isinstance(scanner, ManualSpecBOSScanner)

    def test_the_live_ob_pool_survives_with_every_state_intact(self):
        payload, lifecycle, _ = _lux_snapshot()
        restored = restore_state_json(dumps_state(payload))
        assert lifecycle.live_obs.keys() == restored.lifecycle.live_obs.keys()
        assert len(restored.lifecycle.live_obs) == 3
        for ob_id, before in lifecycle.live_obs.items():
            after = restored.lifecycle.live_obs[ob_id]
            for field in dataclasses.fields(ManualOBRecord):
                assert getattr(before, field.name) == getattr(after, field.name), (
                    f"{ob_id}.{field.name} did not survive the round trip")
            assert before == after
        assert all(ob.state is ManualOBState.AWAITING_DISPLACEMENT
                   for ob in restored.lifecycle.live_obs.values())

    def test_the_bos_choch_tag_now_survives_the_round_trip(self):
        """
        REPLACES `test_the_bos_choch_tag_is_the_one_documented_thing_not
        _persisted`, which pinned the OPPOSITE behaviour: that a restored
        LuxAlgo block reads `""`.

        That limitation is fixed, and fixed WITHOUT moving either gate that
        caused it. `bos_or_choch` is still a `ClassVar`, so
        `dataclasses.fields()` and `expected_keys()` still hold 29 entries and
        the oracle provenance gate still passes; the label travels in the
        top-level `ob_structure_tags` side map instead. Both halves are asserted
        here, because a fix that persisted the tag BY adding a 30th field would
        also make this test's first two assertions pass.
        """
        payload, lifecycle, _ = _lux_snapshot()
        before = {ob_id: ob.bos_or_choch
                  for ob_id, ob in lifecycle.live_obs.items()}
        tagged = {ob_id: tag for ob_id, tag in before.items() if tag}
        assert tagged, "the fixture proved nothing: no LuxAlgo block was tagged"
        assert set(tagged.values()) <= {"BOS", "CHoCH"}
        # The record schema is UNCHANGED -- the tag is not a field and not a
        # per-record payload key.
        assert "bos_or_choch" not in expected_keys(ManualOBRecord)
        assert all("bos_or_choch" not in block for block in payload["live_obs"])
        # It rides in the side map, canonically ordered.
        assert payload["ob_structure_tags"] == sorted(
            [[ob_id, tag] for ob_id, tag in tagged.items()])
        restored = restore_state_json(dumps_state(payload))
        after = {ob_id: ob.bos_or_choch
                 for ob_id, ob in restored.lifecycle.live_obs.items()}
        assert after == before

    def test_the_restored_run_continues_identically_to_an_unbroken_one(self):
        """
        The acceptance test that matters. An unbroken 320-bar run and a run that
        was snapshotted at bar 200, serialized, restored and resumed must emit
        the SAME events for the remaining 120 bars — so the INSTALLED ATR, the
        parsed high/low arrays, both Pine leg states and the pivot bookkeeping
        all came back, and came back from the bounded snapshot rather than from
        a replay of the whole stream.
        """
        rows = _lux_candles()
        whole, _ = _lux_drive(_lux_lifecycle(), rows)
        head_lc = _lux_lifecycle()
        head, head_wm = _lux_drive(head_lc, rows[:LUX_SPLIT])
        restored = restore_state_json(
            dumps_state(capture_state(head_lc, head_wm)))
        tail, _ = _lux_drive(restored.lifecycle, rows[LUX_SPLIT:],
                             restored.watermark)
        assert _events_repr(whole) == _events_repr(head) + _events_repr(tail)
        assert len(tail) > 0, "the tail proved nothing: no events after the split"

    def test_a_recapture_of_the_restored_lifecycle_is_byte_identical(self):
        """Restore is a fixed point: capture(restore(x)) == x."""
        payload, _, _ = _lux_snapshot()
        restored = restore_state_json(dumps_state(payload))
        again = capture_state(restored.lifecycle, restored.watermark)
        assert again == payload

    def test_the_watermark_travels_with_the_luxalgo_log(self):
        payload, _, _ = _lux_snapshot()
        restored = restore_state_json(dumps_state(payload))
        mark = restored.watermark.last(LUX)
        assert mark is not None
        assert mark.bar_idx == LUX_SPLIT - 1
        assert restored.watermark.is_processed(LUX, LUX_SPLIT - 1)
        assert not restored.watermark.is_processed(LUX, LUX_SPLIT)
        with pytest.raises(WatermarkRegressionError):
            restored.watermark.advance(LUX, 0, T0)


class TestDetectorIdentitySurvivesTheRoundTrip:
    """
    Schema 4's whole reason to exist. The two shipped detectors disagree about
    the ZONE — half origin candle vs whole origin candle — so the distal edge,
    and therefore the stop and the derived leverage, differ. Nothing else in the
    payload settles which one produced a block, so the name is recorded and
    checked on both sides rather than defaulted (rule #13).
    """

    @pytest.mark.parametrize("detector", [DETECTOR_MANUAL_SPEC,
                                          DETECTOR_LUXALGO_INTERNAL])
    def test_each_detector_restores_as_itself(self, detector):
        lifecycle = ManualSMCLifecycle(assets=[LUX], detector=detector)
        _, watermark = _lux_drive(lifecycle, _lux_candles()[:120])
        payload = capture_state(lifecycle, watermark)
        assert payload["detector"] == detector
        restored = restore_state_json(dumps_state(payload))
        assert restored.lifecycle.detector == detector
        expected = (LuxAlgoInternalOBScanner
                    if detector == DETECTOR_LUXALGO_INTERNAL
                    else ManualSpecBOSScanner)
        assert isinstance(restored.lifecycle._scanners[LUX], expected)

    def test_the_two_detectors_do_not_produce_the_same_snapshot(self):
        """Otherwise this whole class would be measuring nothing."""
        payloads = {}
        for detector in (DETECTOR_MANUAL_SPEC, DETECTOR_LUXALGO_INTERNAL):
            lifecycle = ManualSMCLifecycle(assets=[LUX], detector=detector)
            _, watermark = _lux_drive(lifecycle, _lux_candles()[:120])
            payloads[detector] = capture_state(lifecycle, watermark)
        spec, lux = payloads.values()
        assert spec != lux
        assert spec["detector"] != lux["detector"]
        assert (len(spec["live_obs"]) != len(lux["live_obs"])
                or [o["ob_id"] for o in spec["live_obs"]]
                != [o["ob_id"] for o in lux["live_obs"]])

    def test_a_missing_detector_is_not_defaulted(self):
        """A v3-shaped payload: the key is absent, so the answer is unknown."""
        payload, _, _ = _lux_snapshot()
        with pytest.raises(MissingFieldError, match="detector"):
            restore_state(_drop(payload, "detector"))

    @pytest.mark.parametrize("name", ["", "LUXALGO", "luxalgo_internal",
                                      "MANUAL_SPEC", "LUXALGO_INTERNAL_V2",
                                      "SMC", "LUXALGO_INTERNAL "])
    def test_an_unknown_or_aliased_detector_name_is_refused(self, name):
        """No aliases, no case folding, no whitespace tolerance (rule #15)."""
        payload, _, _ = _lux_snapshot()
        with pytest.raises(StateIntegrityError, match="not a known detector"):
            restore_state(_mutate(payload, "detector", value=name))

    @pytest.mark.parametrize("bad", [None, 4, 4.0, True, ["LUXALGO_INTERNAL"],
                                     {"name": "LUXALGO_INTERNAL"}])
    def test_a_non_string_detector_is_refused(self, bad):
        payload, _, _ = _lux_snapshot()
        with pytest.raises(MalformedStateError):
            restore_state(_mutate(payload, "detector", value=bad))


class TestTheDeclaredDetectorMustMatchTheRealOne:
    """
    The declared name and the actual scanner object are cross-checked in BOTH
    directions, so a payload cannot be relabelled into the other algorithm and a
    lifecycle cannot capture a scanner it is not running.
    """

    def test_a_luxalgo_block_relabelled_as_manual_spec_is_refused(self):
        payload, _, _ = _lux_snapshot()
        with pytest.raises(StateError):
            restore_state(_mutate(payload, "detector",
                                  value=DETECTOR_MANUAL_SPEC))

    def test_a_manual_spec_block_relabelled_as_luxalgo_is_refused(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        payload = capture_state(lifecycle, watermark)
        assert payload["detector"] == DETECTOR_MANUAL_SPEC
        with pytest.raises(StateError):
            restore_state(_mutate(payload, "detector",
                                  value=DETECTOR_LUXALGO_INTERNAL))

    def test_capture_refuses_a_lifecycle_whose_scanner_is_the_other_kind(self):
        """
        The capture-side half. A lifecycle that says LuxAlgo but holds a
        ManualSpec scanner would otherwise write a payload that restores as a
        DIFFERENT strategy than the one that produced it.
        """
        lifecycle = _lux_lifecycle()
        _, watermark = _lux_drive(lifecycle, _lux_candles()[:60])
        lifecycle._scanners[LUX] = ManualSpecBOSScanner(
            lookback=lifecycle.cfg.lookback,
            min_width=lifecycle.cfg.min_ob_width)
        with pytest.raises(StateIntegrityError):
            capture_state(lifecycle, watermark)

    def test_capture_refuses_the_mirror_image_mismatch(self):
        lifecycle, watermark, _ = _fresh(len(BARS))
        lifecycle._scanners[BTC] = LuxAlgoInternalOBScanner(
            lookback=lifecycle.cfg.lookback,
            min_width=lifecycle.cfg.min_ob_width)
        with pytest.raises(StateIntegrityError):
            capture_state(lifecycle, watermark)


class TestTheLuxAlgoCandleLogCannotBeTamperedWith:
    """
    A LuxAlgo block carries the retained candle window TWICE — once as `bars`,
    once inside the `structure` block's own arrays — and the Pine `var` state
    once. Three independent checks stand over that: the structure validator in
    `restore_state`, the cross-agreement check between the two candle copies, and
    the two checksums. Which one fires depends on WHICH copy was edited, and each
    case below names the one that should.
    """

    @staticmethod
    def _tampered(**edit) -> Dict[str, Any]:
        payload, _, _ = _lux_snapshot()
        return payload, copy.deepcopy(payload)

    def test_editing_one_price_in_one_candle_is_refused(self):
        """
        A GEOMETRICALLY VALID edit — the low is pushed further down, so the
        candle still satisfies low <= open/close <= high and the structure
        validator has nothing to object to. The cross-agreement check catches it:
        `bars[10]` no longer matches the same bar in the structure's own arrays.
        """
        _, bad = self._tampered()
        row = bad["scanners"][0]["bars"][10]
        row[3] -= 5.0                                    # low, downward
        assert row[3] < min(row[1], row[2], row[4])
        with pytest.raises(StateIntegrityError,
                           match="different candles"):
            restore_state(bad)

    def test_editing_the_open_is_caught_by_the_checksum_alone(self):
        """
        The one field the cross-agreement check CANNOT corroborate. The Pine
        structure validates the open and discards it, so `bars` is the only copy
        of it in the payload and `bars_sha256` is the only thing covering it.
        This test exists to say so out loud rather than leave the gap implied.
        """
        _, bad = self._tampered()
        bad["scanners"][0]["bars"][10][1] += 3.0          # open, still in range
        with pytest.raises(StateIntegrityError, match="bars_sha256"):
            restore_state(bad)

    def test_an_edit_that_inverts_a_candle_is_refused_before_the_checksum(self):
        """The structure validator gets there first, and says why.

        Edited inside the `structure` block, because that is where the geometry
        is validated: `restore_state` checks each retained bar's parsed high/low
        against its own high/low. The same edit made in `bars` is caught by the
        cross-agreement check instead (see above).
        """
        _, bad = self._tampered()
        arrays = bad["scanners"][0]["structure"]["arrays"]
        arrays["lows"][10] = arrays["highs"][10] + 5.0    # low above the high
        with pytest.raises(StateIntegrityError,
                           match="this build can rebuild"):
            restore_state(bad)

    def test_editing_a_timestamp_is_refused(self):
        _, bad = self._tampered()
        bad["scanners"][0]["bars"][10][5] = "2030-01-01T00:00:00+00:00"
        with pytest.raises(StateError):
            restore_state(bad)

    def test_truncating_the_log_is_refused(self):
        _, bad = self._tampered()
        del bad["scanners"][0]["bars"][-1]
        with pytest.raises(StateIntegrityError):
            restore_state(bad)

    def test_appending_a_candle_to_the_log_is_refused(self):
        _, bad = self._tampered()
        bad["scanners"][0]["bars"].append(list(bad["scanners"][0]["bars"][-1]))
        with pytest.raises(StateError):
            restore_state(bad)

    def test_dropping_the_digest_is_refused(self):
        _, bad = self._tampered()
        del bad["scanners"][0]["digest"]
        with pytest.raises(MissingFieldError, match="digest"):
            restore_state(bad)

    def test_dropping_the_structure_block_is_refused(self):
        _, bad = self._tampered()
        del bad["scanners"][0]["structure"]
        with pytest.raises(MissingFieldError, match="structure"):
            restore_state(bad)

    @pytest.mark.parametrize("field", ["stream_bars", "retained_bars",
                                       "ob_seq", "live_obs",
                                       "internal_trend", "swing_trend",
                                       "rejected_non_positive_width",
                                       "rejected_below_min_width"])
    def test_editing_any_counted_digest_field_is_refused(self, field):
        _, bad = self._tampered()
        bad["scanners"][0]["digest"][field] += 1
        with pytest.raises(StateIntegrityError, match=field):
            restore_state(bad)

    @pytest.mark.parametrize("field", ["non_positive_width", "below_min_width"])
    def test_editing_a_persisted_rejection_counter_is_refused(self, field):
        """
        The counters are REAL STATE now — there is no replay to re-derive them —
        so they are carried in the block body and the digest's copies verify
        them. Editing one copy is caught by the other, which is the same
        two-coordinated-edits property the checksums have.
        """
        _, bad = self._tampered()
        bad["scanners"][0]["rejections"][field] += 1
        with pytest.raises(StateIntegrityError, match="rejected_" + field):
            restore_state(bad)

    def test_editing_the_pine_state_inside_the_structure_block_is_refused(self):
        """A trend bias flip changes no candle, so only the structure checksum
        and the digest's own copy of the bias can see it."""
        _, bad = self._tampered()
        bad["scanners"][0]["structure"]["trends"]["internal"] = -1
        with pytest.raises(StateIntegrityError,
                           match="structure_sha256|internal_trend"):
            restore_state(bad)

    def test_editing_the_structures_own_copy_of_a_close_is_refused(self):
        """The cross-agreement check, in the OTHER direction.

        `restore_state` range-checks a retained bar's high against its low and
        recomputes the parsed high/low and the volatility flag, but it does NOT
        constrain `closes[i]` to sit inside `[low, high]` -- Pine never does
        either. So a close edited inside the `structure` block clears the
        structure validator, and the cross-agreement check against the `bars`
        copy is what stops it. Without that check the edit would move
        `_display_structure`'s crossover series and change which bar breaks.
        """
        _, bad = self._tampered()
        bad["scanners"][0]["structure"]["arrays"]["closes"][10] += 4.0
        with pytest.raises(StateIntegrityError,
                           match=r"scanners\[0\]\.bars\[10\]"):
            restore_state(bad)

    def test_rebasing_the_structure_is_refused(self):
        """Sliding the whole window's absolute position is caught internally.

        `base_bar_idx` is what makes a pruned window's bar indices absolute. An
        off-by-one there would silently renumber every retained bar -- including
        the ones `_scanner_bar_indices` hands the torn-capture check -- so it is
        refused by the structure's own `last_bar_idx == base + n - 1` identity
        before the cross-agreement check is even reached.
        """
        _, bad = self._tampered()
        bad["scanners"][0]["structure"]["base_bar_idx"] += 1
        with pytest.raises(StateIntegrityError, match="last_bar_idx"):
            restore_state(bad)


LUX_TORN_CUT = 120

# The real canonical 1h history the LuxAlgo suites drive. Skipped, never faked,
# when the CSVs are not checked out -- an absent fixture must not silently turn
# a measured claim into a passing test.
_EXTENDED = (Path(__file__).parent.parent / "data" / "research"
             / "manual_smc_5day_parity" / "canonical_extended"
             / "delta_exchange_india")
REAL_BARS = 19762
needs_real_history = pytest.mark.skipif(
    not (_EXTENDED / "BTCUSD" / "1h" / "full_history.csv").exists(),
    reason="extended canonical 1h history (19,762 bars) not present")


def _real_history_rows(symbol: str) -> List[Tuple[Any, ...]]:
    """`(bar_idx, ts, o, h, l, c)` rows, in the lifecycle's argument order."""
    from quantedge.ai.research.displacement_gated_retest_engine import (
        _load_canonical_candles,
    )
    raw = list(_load_canonical_candles(_EXTENDED, symbol))[:REAL_BARS]
    assert len(raw) == REAL_BARS, f"{symbol}: got {len(raw)} candles"
    return [(i, r["timestamp"], r["open"], r["high"], r["low"], r["close"])
            for i, r in enumerate(raw)]


class TestTheLuxAlgoSnapshotIsBounded:
    """
    Schema 5's other half. Schema 4 wrote EVERY accepted candle out and replayed
    them on restore, so a long-lived asset's block grew without bound -- the
    measured real 19,762-bar single-symbol block was ~1.4 MB. Schema 5 writes
    the Pine reconstruction field-by-field, pruned to
    `LuxAlgoInternalStructure.causal_watermark()`.

    The continuation proof -- restore from many cut points, feed the remaining
    candles, compare bar-by-bar across 13 channels -- lives in
    `test_manual_smc_luxalgo_snapshot_parity.py`. What is asserted HERE is what
    the STATE CODEC owes on top of that: the payload really is bounded, the
    numbers are measured rather than hoped for, the payload is a fixed point,
    and the two safety checks that read the candle window are still binding on a
    window that no longer contains the whole stream.
    """

    def test_the_retained_window_is_bounded_not_the_stream(self):
        """Two cut points, both measured. The bound is CAUSAL, not constant.

        At 200 bars the newest live pivot is what holds the window open (109
        bars retained). At 120 bars nothing is holding it, so the `n -
        SWINGS_LENGTH` floor binds exactly -- `SWINGS_LENGTH` bars, the smallest
        window the codec will ever write. Both are asserted so that a change to
        the pruning rule shows up as a measured difference in one or both.
        """
        for bars, retained in ((LUX_SPLIT, 109), (LUX_TORN_CUT, SWINGS_LENGTH)):
            digest = _lux_snapshot(bars)[0]["scanners"][0]["digest"]
            assert digest["stream_bars"] == bars
            assert digest["retained_bars"] == retained < bars
            assert (digest["base_bar_idx"] + digest["retained_bars"]
                    == digest["stream_bars"])
        assert SWINGS_LENGTH == 50

    def test_the_newest_bar_is_always_retained(self):
        """Why pruning cannot weaken the torn-capture check.

        That check refuses a snapshot holding a scanner bar AHEAD of the
        watermark. Pruning drops OLD bars only, so the retained window is a
        contiguous suffix ending at the last bar the scanner saw -- meaning any
        bar ahead of the mark is still there to be found. Assert the suffix
        property directly, through the public accessor the check reads.
        """
        payload, _, _ = _lux_snapshot()
        digest = payload["scanners"][0]["digest"]
        restored = restore_state_json(dumps_state(payload))
        log = restored.lifecycle._scanners[LUX].bars_for(LUX)
        indices = [row[0] for row in log]
        assert indices == list(range(digest["base_bar_idx"],
                                     digest["last_bar_idx"] + 1))
        assert indices[-1] == digest["last_bar_idx"] == LUX_SPLIT - 1

    def test_a_torn_capture_is_still_refused_through_the_pruned_window(self):
        """The scanner branch of the torn-capture check, on a pruned window.

        Cut at 120 bars: the window is pruned to 50 (bars 70..119) and the
        newest live OB was created at bar 110, so a watermark placed at 110 or
        later leaves NO OB ahead of it and the refusal can only come from the
        scanner window. Every bar named in the message is inside the retained
        window, which is the point: pruning shortened the enumeration, not the
        refusal.
        """
        payload, lifecycle, _ = _lux_snapshot(LUX_TORN_CUT)
        assert max(ob.bos_bar_idx for ob in lifecycle.live_obs.values()) == 110
        assert payload["scanners"][0]["digest"]["base_bar_idx"] == 70
        for mark, named in ((118, [119]),
                            (115, [116, 117, 118, 119])):
            torn = copy.deepcopy(payload)
            torn["watermark"][0]["bar_idx"] = mark
            torn["watermark"][0]["ts"] = (
                T0 + timedelta(hours=mark)).isoformat()
            with pytest.raises(StateIntegrityError,
                               match=r"scanner history contains bar\(s\) "
                                     + re.escape(repr(named))):
                restore_state(torn)
            assert all(idx >= 70 for idx in named)

    def test_a_torn_capture_is_still_refused_through_the_ob_pool(self):
        """The other branch, unchanged: a live OB ahead of the mark.

        Included next to the one above because the two branches can mask each
        other -- at the 200-bar cut a block was created on the very last bar, so
        the pool answers first and the scanner branch is never reached. Asserting
        both means neither can quietly stop working.
        """
        payload, _, _ = _lux_snapshot()
        torn = copy.deepcopy(payload)
        torn["watermark"][0]["bar_idx"] = LUX_SPLIT - 2
        torn["watermark"][0]["ts"] = (
            T0 + timedelta(hours=LUX_SPLIT - 2)).isoformat()
        with pytest.raises(StateIntegrityError,
                           match=r"were created on bar\(s\) after the "
                                 r"processed watermark 198"):
            restore_state(torn)

    def test_the_payload_is_a_fixed_point_over_two_full_cycles(self):
        """Pruning is idempotent AT THE PAYLOAD LEVEL, not just in the codec.

        `test_a_recapture_of_the_restored_lifecycle_is_byte_identical` proves one
        cycle. A second cycle is the one that could go wrong: the first restore
        installs an ALREADY-PRUNED structure whose `n` is now 109 instead of 200,
        so a `causal_watermark()` that recomputed off the shorter window could
        prune again and shrink the payload on every restart. It does not, and the
        JSON text is compared as well as the dicts.
        """
        payload, _, _ = _lux_snapshot()
        first = restore_state_json(dumps_state(payload))
        once = capture_state(first.lifecycle, first.watermark)
        second = restore_state_json(dumps_state(once))
        twice = capture_state(second.lifecycle, second.watermark)
        assert once == payload
        assert twice == once
        assert dumps_state(twice) == dumps_state(payload)
        assert (twice["scanners"][0]["digest"]["retained_bars"]
                == payload["scanners"][0]["digest"]["retained_bars"] == 109)

    @needs_real_history
    def test_the_real_history_payload_is_bounded_and_measured(self):
        """The case the mandate named: 19,762 real 1h bars, one symbol.

        MEASURED, not projected. Schema 4 wrote one row per accepted candle, so
        this block was ~1.4 MB; schema 5 retains 114 bars and the whole block --
        structure, pruned candles, rejection counters and digest -- is 40,841
        bytes of `json.dumps(..., sort_keys=True)`. The byte count is asserted
        with a small tolerance because it is a serialisation detail, while the
        retained-bar count, the stream length and the block counts are exact.

        This is a BOUND ON THE PAYLOAD, not on memory: the running scanner still
        keeps every candle it accepted, because Pine's origin search may reach
        back to an uncrossed pivot of any age.
        """
        rows = _real_history_rows("BTCUSD")
        lifecycle = ManualSMCLifecycle(assets=["BTCUSD"],
                                       detector=DETECTOR_LUXALGO_INTERNAL)
        watermark = CandleWatermark()
        for bar_idx, ts, o, h, l, c in rows:
            lifecycle.process_candle("BTCUSD", bar_idx, ts, o, h, l, c)
            watermark.advance("BTCUSD", bar_idx, ts)
        payload = capture_state(lifecycle, watermark)
        block = payload["scanners"][0]
        digest = block["digest"]
        assert digest["stream_bars"] == REAL_BARS == 19762
        assert digest["retained_bars"] == len(block["bars"]) == 114
        assert digest["base_bar_idx"] == 19648
        assert digest["last_bar_idx"] == REAL_BARS - 1
        assert digest["ob_seq"] == 1057
        # TWO DIFFERENT POOLS, and the gap is not a defect. `digest["live_obs"]`
        # counts LuxAlgo's own un-mitigated drawing store, capped at 100;
        # `lifecycle.live_obs` counts the blocks the STRATEGY still holds, which
        # is smaller because a block leaves on fill, expiry or invalidation.
        # Asserting both stops one being read as a check on the other.
        assert digest["live_obs"] == 46
        assert len(lifecycle.live_obs) == 36
        measured = len(json.dumps(block, sort_keys=True).encode("utf-8"))
        assert abs(measured - 40841) < 2048, measured
        # Under schema 4 the same block carried one row per accepted candle.
        assert measured < 100_000 < REAL_BARS * 60
        # And it still round-trips to itself.
        restored = restore_state_json(dumps_state(payload))
        assert capture_state(restored.lifecycle, restored.watermark) == payload
        assert len(restored.lifecycle._scanners["BTCUSD"].bars_for("BTCUSD")
                   ) == 114


# ===========================================================================
# BOS / CHoCH STRUCTURE LABELS
#
# `ManualOBRecord.bos_or_choch` is a `ClassVar` shadowed per instance, so the
# per-record codec cannot see it. Rather than promote it to a 30th field --
# which would move the oracle provenance gate in
# `test_manual_smc_oracle_equivalence.py` AND invalidate every previously
# written per-record block -- the label rides in a top-level
# `ob_structure_tags` side map keyed by `ob_id`.
#
# The label is REPORTING, not a trading input, so the risk here is not a wrong
# trade: it is a wrong AUDIT TRAIL. A payload that could silently relabel a
# block, or invent a label for one that no longer exists, would let a resumed
# run disagree with its own history about what happened. Every one of these is
# therefore a refusal, never a repair.
# ===========================================================================
LUX_TAGS = ("BOS", "CHoCH")

#: The cut point at which the fixture's live pool holds BOTH labels at once.
#: Measured, not guessed: at 200 bars (`LUX_SPLIT`) every surviving block is a
#: BOS, so a round-trip claim made there would say nothing about CHoCH.
LUX_TAGGED_SPLIT: int = 216


def _tagged_snapshot(bars: int = LUX_TAGGED_SPLIT):
    """A LuxAlgo snapshot whose live pool carries at least one of each label."""
    payload, lifecycle, watermark = _lux_snapshot(bars)
    labels = {ob_id: ob.bos_or_choch
              for ob_id, ob in lifecycle.live_obs.items()}
    return payload, lifecycle, watermark, labels


def _spec_payload() -> Dict[str, Any]:
    """A ManualSpec snapshot: every record unlabelled, so the map is empty."""
    return _valid_payload()


class TestStructureLabelsSurviveTheRoundTrip:
    """The Part 1 acceptance tests: capture -> JSON text -> restore."""

    def test_the_fixture_actually_produces_both_labels(self):
        """Guards the guard. A fixture that only ever emitted BOS would make
        every CHoCH assertion below vacuously true."""
        _, lifecycle, _, labels = _tagged_snapshot()
        del lifecycle
        assert set(labels.values()) == {"BOS", "CHoCH"}, (
            f"fixture emitted {sorted(set(labels.values()))}; both labels are "
            f"needed for the round-trip claim to mean anything")

    def test_every_label_survives_capture_json_and_restore(self):
        payload, _, _, labels = _tagged_snapshot()
        restored = restore_state_json(dumps_state(payload))
        after = {ob_id: ob.bos_or_choch
                 for ob_id, ob in restored.lifecycle.live_obs.items()}
        assert after == labels
        assert set(after.values()) == {"BOS", "CHoCH"}

    @pytest.mark.parametrize("tag", LUX_TAGS)
    def test_each_label_survives_on_its_own(self, tag):
        """One label at a time, so a bug that maps everything to the first
        (or the last) entry cannot hide behind a mixed pool."""
        payload, _, _, labels = _tagged_snapshot()
        keep = {ob_id for ob_id, t in labels.items() if t == tag}
        assert keep, f"no live block carried {tag}"
        payload["ob_structure_tags"] = sorted([ob_id, tag] for ob_id in keep)
        restored = restore_state_json(dumps_state(payload))
        after = {ob_id: ob.bos_or_choch
                 for ob_id, ob in restored.lifecycle.live_obs.items()}
        assert {ob_id for ob_id, t in after.items() if t} == keep
        assert set(after[ob_id] for ob_id in keep) == {tag}

    def test_the_restored_label_is_an_instance_attribute_not_the_classvar(self):
        """`scanner.py` sets it per instance; a restore that assigned the CLASS
        attribute instead would relabel every record in the process."""
        payload, _, _, labels = _tagged_snapshot()
        restored = restore_state_json(dumps_state(payload))
        for ob_id, tag in labels.items():
            ob = restored.lifecycle.live_obs[ob_id]
            assert ("bos_or_choch" in ob.__dict__) is bool(tag)
        assert ManualOBRecord.bos_or_choch == ""

    def test_the_record_schema_did_not_move_to_achieve_this(self):
        """The whole point of the side map. If this fails, the fix took the
        forbidden route and the oracle provenance gate is next to break."""
        payload, _, _, _ = _tagged_snapshot()
        assert len(expected_keys(ManualOBRecord)) == 29
        assert "bos_or_choch" not in expected_keys(ManualOBRecord)
        assert all("bos_or_choch" not in block
                   for block in payload["live_obs"])

    def test_an_unlabelled_block_is_absent_from_the_map_not_empty_string(self):
        payload, _, _, labels = _tagged_snapshot()
        mapped = {ob_id for ob_id, _ in payload["ob_structure_tags"]}
        assert mapped == {ob_id for ob_id, t in labels.items() if t}
        assert all(tag in LUX_TAGS for _, tag in payload["ob_structure_tags"])

    def test_the_map_is_canonically_ordered_so_two_captures_are_identical(self):
        first, lifecycle, watermark, _ = _tagged_snapshot()
        second = capture_state(lifecycle, watermark)
        ids = [ob_id for ob_id, _ in first["ob_structure_tags"]]
        assert ids == sorted(ids)
        assert first["ob_structure_tags"] == second["ob_structure_tags"]
        assert dumps_state(first) == dumps_state(second)

    def test_the_open_positions_order_block_is_labelled_after_restore(self):
        """`active_trade.ob` is the IDENTICAL object as its pool entry, so the
        label must be visible through the trade too, not just through the pool.

        The fixture fills exactly once, so the cut point is FOUND by walking
        every bar rather than sampled -- a stride would miss it and the test
        would silently degrade into a skip.
        """
        lifecycle = _lux_lifecycle()
        watermark = CandleWatermark()
        for bar_idx, ts, o, h, l, c in _lux_candles():
            lifecycle.process_candle(LUX, bar_idx, ts, o, h, l, c)
            watermark.advance(LUX, bar_idx, ts)
            if lifecycle.active_trade is None:
                continue
            payload = capture_state(lifecycle, watermark)
            expected = lifecycle.active_trade.ob.bos_or_choch
            assert expected in LUX_TAGS
            restored = restore_state_json(dumps_state(payload))
            trade = restored.lifecycle.active_trade
            assert trade is not None
            assert trade.ob is restored.lifecycle.live_obs[trade.ob.ob_id]
            assert trade.ob.bos_or_choch == expected
            return
        pytest.fail("the fixture never held an open position: the claim that a "
                    "live position's label survives was never exercised")


class TestUnknownStructureLabelsAreRefused:
    """No coercion, no closest match, no silent blank."""

    @pytest.mark.parametrize("tag", [
        "bos",          # wrong case
        "BOSS",         # superstring
        "CHOCH",        # LuxAlgo spells it CHoCH; this is a DIFFERENT string
        "choch",
        "ChoCh",
        "CH0CH",        # digit zero
        "BOS ",         # trailing space
        " BOS",
        "BOS/CHoCH",
        "MSS",          # a real SMC term this build does not implement
        "UNKNOWN",
        "None",
        "-",
    ])
    def test_a_label_this_build_cannot_name_is_refused(self, tag):
        payload, _, _, labels = _tagged_snapshot()
        ob_id = next(iter(labels))
        payload["ob_structure_tags"] = [[ob_id, tag]]
        with pytest.raises(MalformedStateError, match="not one of"):
            restore_state(payload)

    def test_an_empty_label_is_refused_because_absence_means_unlabelled(self):
        """`""` is not a third legal value: an unlabelled block is simply not in
        the map. Accepting it here would create two spellings of one state and
        let a payload look labelled while carrying nothing."""
        payload, _, _, labels = _tagged_snapshot()
        payload["ob_structure_tags"] = [[next(iter(labels)), ""]]
        with pytest.raises(MalformedStateError, match="not one of"):
            restore_state(payload)

    def test_the_accepted_set_is_exactly_the_luxalgo_constants(self):
        """Pinned against the indicator module, not re-spelled, so the codec and
        the detector can never drift apart."""
        from quantedge.strategy.manual_smc import luxalgo_structure as LS
        from quantedge.strategy.manual_smc import state as ST
        assert ST._OB_STRUCTURE_TAGS == frozenset({LS.TAG_BOS, LS.TAG_CHOCH})
        assert sorted(ST._OB_STRUCTURE_TAGS) == ["BOS", "CHoCH"]
        assert "" not in ST._OB_STRUCTURE_TAGS
    def test_capture_refuses_a_label_it_could_not_restore(self):
        """The refusal is symmetric. A record carrying an unnameable label must
        not be written to disk either, or the snapshot would be dead on arrival
        with no clue where the bad value came from."""
        _, lifecycle, _, labels = _tagged_snapshot()
        ob = lifecycle.live_obs[next(iter(labels))]
        ob.bos_or_choch = "SUPERBOS"
        with pytest.raises(StateIntegrityError, match="cannot be restored"):
            encode_ob_structure_tags(lifecycle.live_obs,
                                     DETECTOR_LUXALGO_INTERNAL)

    def test_capture_refuses_a_non_string_label(self):
        _, lifecycle, _, labels = _tagged_snapshot()
        lifecycle.live_obs[next(iter(labels))].bos_or_choch = 1
        with pytest.raises(MalformedStateError):
            encode_ob_structure_tags(lifecycle.live_obs,
                                     DETECTOR_LUXALGO_INTERNAL)


class TestMalformedStructureLabelMapIsRefused:
    """Shape errors, caught before any label is even read."""

    @pytest.mark.parametrize("blob", [
        None, {}, "BOS", 0, 1.0, True,
        {"ob-1": "BOS"},                       # a mapping, not a pair list
    ])
    def test_the_map_must_be_a_list(self, blob):
        payload, _, _, _ = _tagged_snapshot()
        payload["ob_structure_tags"] = blob
        with pytest.raises(MalformedStateError):
            restore_state(payload)

    @pytest.mark.parametrize("entry", [
        "OB-1",                                # a bare string
        ["only-one"],                          # too short
        ["a", "BOS", "extra"],                 # too long
        [],
        None,
        42,
        {"ob_id": "a", "tag": "BOS"},          # a mapping entry
    ])
    def test_each_entry_must_be_a_two_element_pair(self, entry):
        payload, _, _, _ = _tagged_snapshot()
        payload["ob_structure_tags"] = [entry]
        with pytest.raises(MalformedStateError, match="2-element"):
            restore_state(payload)
    @pytest.mark.parametrize("ob_id", [None, 7, 1.5, True, ["x"]])
    def test_the_ob_id_must_be_a_string(self, ob_id):
        payload, _, _, _ = _tagged_snapshot()
        payload["ob_structure_tags"] = [[ob_id, "BOS"]]
        with pytest.raises(MalformedStateError):
            restore_state(payload)

    @pytest.mark.parametrize("tag", [None, 7, 1.5, True, ["BOS"]])
    def test_the_tag_must_be_a_string(self, tag):
        payload, _, _, labels = _tagged_snapshot()
        payload["ob_structure_tags"] = [[next(iter(labels)), tag]]
        with pytest.raises(MalformedStateError):
            restore_state(payload)

    def test_a_missing_map_is_refused_not_defaulted_to_empty(self):
        """The key is MANDATORY. Defaulting it to `[]` would silently drop every
        label of an otherwise-valid payload -- the exact regression the schema
        bump exists to prevent."""
        payload, _, _, _ = _tagged_snapshot()
        del payload["ob_structure_tags"]
        with pytest.raises(MissingFieldError, match="ob_structure_tags"):
            restore_state(payload)

    def test_a_duplicate_ob_id_is_refused(self):
        """Which label wins would otherwise depend on payload order."""
        payload, _, _, labels = _tagged_snapshot()
        ob_id = next(iter(labels))
        payload["ob_structure_tags"] = [[ob_id, "BOS"], [ob_id, "CHoCH"]]
        with pytest.raises(StateIntegrityError, match="duplicate ob_id"):
            restore_state(payload)

    def test_the_same_pair_twice_is_still_refused(self):
        """Even a harmless-looking exact repeat: a canonical map holds one entry
        per block, so a repeat means the writer was not the shipped codec."""
        payload, _, _, labels = _tagged_snapshot()
        ob_id, tag = next((k, v) for k, v in labels.items() if v)
        payload["ob_structure_tags"] = [[ob_id, tag], [ob_id, tag]]
        with pytest.raises(StateIntegrityError, match="duplicate ob_id"):
            restore_state(payload)
    def test_a_label_for_a_block_that_is_not_in_the_pool_is_refused(self):
        """An orphan label means the map and the pool were written by different
        runs; neither can then be trusted about what was live."""
        payload, _, _, _ = _tagged_snapshot()
        payload["ob_structure_tags"] = [["OB-DOES-NOT-EXIST", "BOS"]]
        with pytest.raises(StateIntegrityError, match="not in the live pool"):
            restore_state(payload)

    def test_relabelling_a_block_that_dropped_out_of_the_pool_is_refused(self):
        payload, _, _, labels = _tagged_snapshot()
        ob_id = next(ob for ob, t in labels.items() if t)
        payload["live_obs"] = [b for b in payload["live_obs"]
                               if b["ob_id"] != ob_id]
        with pytest.raises(StateError):
            restore_state(payload)


class TestManualSpecStaysBackwardCompatible:
    """The oracle-extracted detector has no trend bias and cannot classify a
    break, so its snapshots must carry an EMPTY map -- and must refuse a
    non-empty one rather than quietly adopting a classification the detector is
    incapable of producing."""

    def test_a_manual_spec_snapshot_carries_an_empty_map(self):
        payload = _spec_payload()
        assert payload["detector"] == DETECTOR_MANUAL_SPEC
        assert payload["ob_structure_tags"] == []

    def test_a_manual_spec_round_trip_leaves_every_record_unlabelled(self):
        payload = _spec_payload()
        restored = restore_state_json(dumps_state(payload))
        assert restored.lifecycle.live_obs
        assert all(ob.bos_or_choch == ""
                   for ob in restored.lifecycle.live_obs.values())
        assert all("bos_or_choch" not in ob.__dict__
                   for ob in restored.lifecycle.live_obs.values())

    @pytest.mark.parametrize("tag", LUX_TAGS)
    def test_a_labelled_manual_spec_payload_is_refused(self, tag):
        payload = _spec_payload()
        ob_id = payload["live_obs"][0]["ob_id"]
        payload["ob_structure_tags"] = [[ob_id, tag]]
        with pytest.raises(StateIntegrityError, match="cannot classify"):
            restore_state(payload)
    def test_capture_refuses_a_labelled_record_under_manual_spec(self):
        """The mirror image: a ManualSpec lifecycle whose pool somehow holds a
        labelled record is a lifecycle whose detector and contents disagree."""
        ob = _make_manual_ob(
            asset=BTC, bos_bar_idx=1, bos_dt=T0, origin_bar_idx=0,
            origin_dt=T0, direction="LONG", ob_top=101.0, ob_bottom=100.0,
            cfg=ManualSpecConfig())
        pool = {ob.ob_id: ob}
        assert encode_ob_structure_tags(pool, DETECTOR_MANUAL_SPEC) == []
        ob.bos_or_choch = "BOS"
        with pytest.raises(StateIntegrityError, match="has no trend"):
            encode_ob_structure_tags(pool, DETECTOR_MANUAL_SPEC)

    def test_the_manual_spec_record_arithmetic_is_untouched(self):
        """The label work must not have moved a single number: every persisted
        field of a restored ManualSpec pool against the live one."""
        lifecycle, watermark, _ = _fresh(FILL_BAR + 1)
        payload = capture_state(lifecycle, watermark)
        restored = restore_state_json(dumps_state(payload))
        assert restored.lifecycle.live_obs.keys() == lifecycle.live_obs.keys()
        assert lifecycle.live_obs
        for ob_id, before in lifecycle.live_obs.items():
            after = restored.lifecycle.live_obs[ob_id]
            assert before == after
            for f in dataclasses.fields(ManualOBRecord):
                assert getattr(before, f.name) == getattr(after, f.name), (
                    f"{ob_id}.{f.name} moved")
class TestStructureLabelCodecInIsolation:
    """The two helpers, exercised directly. `restore_state` reaches them only
    after a dozen other validations, so a direct test is what proves a refusal
    belongs to THIS codec rather than to something upstream."""

    @staticmethod
    def _pool(*tags: str) -> Dict[str, ManualOBRecord]:
        pool: Dict[str, ManualOBRecord] = {}
        for i, tag in enumerate(tags):
            ob = _make_manual_ob(
                asset=BTC, bos_bar_idx=i + 1, bos_dt=T0 + timedelta(hours=i),
                origin_bar_idx=i, origin_dt=T0 + timedelta(hours=i),
                direction="LONG", ob_top=101.0 + i, ob_bottom=100.0 + i,
                cfg=ManualSpecConfig())
            if tag:
                ob.bos_or_choch = tag
            pool[ob.ob_id] = ob
        return pool

    def test_encode_then_decode_is_the_identity_on_a_mixed_pool(self):
        pool = self._pool("BOS", "", "CHoCH", "BOS", "")
        before = {k: v.bos_or_choch for k, v in pool.items()}
        blob = encode_ob_structure_tags(pool, DETECTOR_LUXALGO_INTERNAL)
        assert len(blob) == 3
        target = self._pool("", "", "", "", "")
        assert list(target) == list(pool)     # same ob_ids, no labels
        decode_ob_structure_tags(blob, target, DETECTOR_LUXALGO_INTERNAL)
        assert {k: v.bos_or_choch for k, v in target.items()} == before

    def test_an_empty_pool_encodes_to_an_empty_map(self):
        assert encode_ob_structure_tags({}, DETECTOR_LUXALGO_INTERNAL) == []
        assert encode_ob_structure_tags({}, DETECTOR_MANUAL_SPEC) == []

    def test_an_empty_map_decodes_without_touching_the_pool(self):
        pool = self._pool("", "")
        decode_ob_structure_tags([], pool, DETECTOR_LUXALGO_INTERNAL)
        assert all(ob.bos_or_choch == "" for ob in pool.values())
        assert all("bos_or_choch" not in ob.__dict__ for ob in pool.values())
    def test_decode_applies_nothing_when_it_refuses(self):
        """All-or-nothing. A partially applied map would leave a pool that is
        neither the captured one nor a clean failure."""
        pool = self._pool("", "", "")
        ids = list(pool)
        blob = [[ids[0], "BOS"], [ids[1], "CHoCH"], ["ORPHAN", "BOS"]]
        with pytest.raises(StateIntegrityError):
            decode_ob_structure_tags(blob, pool, DETECTOR_LUXALGO_INTERNAL)
        assert all(ob.bos_or_choch == "" for ob in pool.values())
        assert all("bos_or_choch" not in ob.__dict__ for ob in pool.values())

    def test_the_error_message_names_the_offending_index(self):
        pool = self._pool("")
        ob_id = next(iter(pool))
        with pytest.raises(MalformedStateError,
                           match=r"ob_structure_tags\[1\]"):
            decode_ob_structure_tags([[ob_id, "BOS"], [ob_id, "NOPE"]], pool,
                                     DETECTOR_LUXALGO_INTERNAL)

    def test_a_tuple_pair_is_accepted_because_only_contents_are_pinned(self):
        """Tolerant about the CONTAINER, strict about the CONTENTS: a caller
        holding tuples in memory is not a corrupt payload, and JSON round-trips
        them to lists anyway."""
        pool = self._pool("")
        ob_id = next(iter(pool))
        decode_ob_structure_tags([(ob_id, "CHoCH")], pool,
                                 DETECTOR_LUXALGO_INTERNAL)
        assert pool[ob_id].bos_or_choch == "CHoCH"
