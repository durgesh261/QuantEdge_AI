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
import tokenize
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from quantedge.strategy.manual_smc.geometry import _make_manual_ob
from quantedge.strategy.manual_smc.lifecycle import (
    ManualActiveTrade,
    ManualLifecycleEvent,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.scanner import ManualSpecBOSScanner
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
    dumps_state,
    encode_dataclass,
    encode_datetime,
    encode_decimal,
    encode_ob_state,
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
    lifecycle = ManualSMCLifecycle()
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
        lifecycle = ManualSMCLifecycle()
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
        payload = capture_state(ManualSMCLifecycle())
        assert payload["schema_version"] == MANUAL_SMC_STATE_SCHEMA_VERSION
        with pytest.raises(UnsupportedSchemaVersionError):
            restore_state(_mutate(payload, "schema_version", value=99))

    def test_declared_assets_are_captured_before_any_candle(self):
        """`ManualSMCLifecycle(assets=[...])` pre-creates empty scanners."""
        lifecycle = ManualSMCLifecycle(assets=[BTC, ETH])
        payload = capture_state(lifecycle)
        assert [s["asset"] for s in payload["scanners"]] == [BTC, ETH]
        assert all(s["history"] == [] and s["consumed"] == []
                   for s in payload["scanners"])
        restored = restore_state(payload)
        assert sorted(restored.lifecycle._scanners) == [BTC, ETH]

    def test_an_empty_restore_then_run_matches_a_plain_run(self):
        restored = restore_state(capture_state(ManualSMCLifecycle()))
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
        a = ManualSMCLifecycle()
        _drive(a, CandleWatermark(), 0, 3, ETH)
        _drive(a, CandleWatermark(), 0, 3, BTC)
        payload = capture_state(a)
        assert [s["asset"] for s in payload["scanners"]] == sorted([BTC, ETH])
        for scanner in payload["scanners"]:
            assert scanner["consumed"] == sorted(scanner["consumed"])

    def test_multiple_assets_keep_separate_scanners(self):
        lifecycle = ManualSMCLifecycle()
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
        payload = capture_state(ManualSMCLifecycle(), wm)
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
            capture_state(ManualSMCLifecycle(), {"BTCUSD": 3})


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
        straight = ManualSMCLifecycle()
        straight_wm = CandleWatermark()
        straight_events = _drive_multi(straight, straight_wm, 0, len(BARS),
                                       assets)

        broken = ManualSMCLifecycle()
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
TOP_KEYS: Tuple[str, ...] = tuple(capture_state(ManualSMCLifecycle()))


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

    @pytest.mark.parametrize("version", [0, 2, 99, -1, 1000])
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
            "config", "scanners", "live_obs", "active_trade", "exits",
            "last_trade_closed_dt", "watermark")


class TestModuleIndependence:
    """No exchange, DB, execution, runtime, adapter, Java or live wiring."""

    def test_imports_are_stdlib_plus_three_siblings_only(self):
        assert _imported_modules() == [
            "__future__",
            "dataclasses",
            "datetime",
            "decimal",
            "json",
            "math",
            "quantedge.strategy.manual_smc.lifecycle",
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
