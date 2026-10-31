"""
TASK N -- the operator's PERMANENT order-block invalidation ("delete this OB").

The requirement is not "hide the row". It is: an order block the operator
deletes must never trade again -- not on a later touch, not after a backend
restart, and not after the scanner rescans the same history in a fresh process.
That last case is the hard one, and it is why this feature is a LEDGER keyed on
market characteristics rather than a flag on the order-block record:

  * `state._PERSISTABLE_OB_STATES` refuses to persist an INVALIDATED order
    block -- it reads one as a torn mid-sweep capture -- so "set the state to
    INVALIDATED" cannot survive a snapshot at all;
  * `ManualSpecBOSScanner._consumed` is an in-memory set of
    `(asset, origin_bar_idx)`, so a fresh process replaying the same candles
    re-creates the identical order block with no memory that it was deleted;
  * `ob_id` is `MANUAL_<asset>_<direction>_<origin_bar_idx>_<bos_bar_idx>`, and
    a backfill of earlier candles renumbers every bar, so the id of one market
    order block is NOT stable. `TestTheFingerprintIsTheIdentity` measures that
    directly: the same market order block re-scanned with five bars of earlier
    history becomes `MANUAL_BTCUSD_SHORT_5_6`, and its fingerprint does not
    change by one character.

What is proved, and which requirement it comes from:

    a visible OB can be invalidated        TestALiveOrderBlockCanBeInvalidated
    the decision is not a UI-only hide     TestALiveOrderBlockCanBeInvalidated
    an invalidated OB never trades again   TestAnInvalidatedOrderBlockNeverTrades
    reload does not restore tradability    TestTheDecisionSurvivesARestart
    a rescan does not resurrect it         TestTheDecisionSurvivesARescan
    no conflicting duplicate records       TestInvalidationIsIdempotent
    a duplicate request is harmless        TestInvalidationIsIdempotent
    a dead OB is safe to invalidate        TestAlreadyDeadOrderBlocksAreSafe
    unrelated OBs stay tradable            TestUnrelatedOrderBlocksAreUntouched
    a live position is never stranded      TestALiveTradeIsNotAbandoned
    the automatic lifecycle is unchanged   TestTheAutomaticLifecycleIsUnchanged
    the decision can never be revoked      TestTheLedgerIsAppendOnly
    the dashboard-facing flow              TestTheRuntimeIsTheDashboardBoundary
    all of it on LuxAlgo-made blocks       TestTheLedgerHoldsLuxAlgoBlocksToo

Every number and event sequence below was measured against the engine before
the assertion was written; none is predicted.

ONE keyword is named at the construction sites: the DETECTOR. Every row in the
fixtures above is half-candle geometry drawn for `DETECTOR_MANUAL_SPEC`, and the
shipped default is now `DETECTOR_LUXALGO_INTERNAL`, whose zone is the whole
origin candle. The activation policy, the entry window and the config are still
left to the shipped defaults, so those remain the subject. Because a pin could in
principle hide the feature behind an algorithm nobody runs,
`TestTheLedgerHoldsLuxAlgoBlocksToo` re-proves the whole chain -- invalidate,
snapshot, restart, full rescan, and a backfill that renumbers every bar -- on
blocks the PRODUCTION-DEFAULT LuxAlgo detector actually produced.

NOTHING here contacts an exchange: recording a decision is a local state
mutation, and the one async method is exercised with an empty cancel list.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from quantedge.runtime import manual_smc_runtime as RT
from quantedge.strategy.manual_smc import invalidation as INV
from quantedge.strategy.manual_smc.invalidation import (
    DEFAULT_REASON,
    DEFAULT_REQUESTED_BY,
    FINGERPRINT_PREFIX,
    FINGERPRINT_SEP,
    FINGERPRINT_VERSION,
    InvalidObIdentityError,
    LedgerIntegrityError,
    ManualOBInvalidation,
    ManualOBInvalidationLedger,
    NaiveTimestampError,
    fingerprint_for_ob,
    make_fingerprint,
    record_for_ob,
)
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_FIRST_TOUCH,
    DETECTOR_LUXALGO_INTERNAL,
    DETECTOR_MANUAL_SPEC,
    ManualLifecycleEventType,
    ManualSMCLifecycle,
)
from quantedge.strategy.manual_smc.models import ManualOBState
from quantedge.strategy.manual_smc.state import (
    CandleWatermark,
    StateIntegrityError,
    UnsupportedSchemaVersionError,
    capture_state,
    dumps_state,
    loads_state,
    restore_state,
)
from quantedge.strategy.manual_smc.strategy import (
    ManualSMCEvaluation,
    ManualSMCStrategy,
)

# ---------------------------------------------------------------------------
# Fixtures. The reference SHORT order block of the first-touch suite, verbatim,
# so a reader can compare the two files line for line: origin bar 0 is bullish,
# so ob_top = CLOSE 105.0 and ob_bottom = LOW 99.0; bar 1 closes below 99.0, so
# MANUAL_BTCUSD_SHORT_0_1 exists with width 6.0, proximal 99.0, distal 105.0,
# entry 100.5, SL 105.0 and TP 99.897.
# ---------------------------------------------------------------------------
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

Row = Tuple[float, float, float, float]


def _ts(bar_idx: int) -> datetime:
    return BASE + timedelta(hours=bar_idx)


S_ORIGIN: Row = (100.0, 106.0, 99.0, 105.0)
S_BOS: Row = (104.0, 104.5, 97.0, 98.0)
S_TOUCH: Row = (98.5, 99.0, 98.0, 98.5)          # high == proximal exactly
S_ENTRY: Row = (100.0, 100.5, 99.5, 100.0)       # high == entry exactly
S_TP: Row = (100.0, 100.0, 99.8, 100.0)          # low 99.8 <= 99.897
S_NEAR: Row = (100.0, 100.4, 98.0, 100.0)        # in the zone, short of 100.5

SHORT_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"
ETH_OB_ID = "MANUAL_ETHUSD_SHORT_0_1"

#: Creation, first touch, fill, take profit. The measured event sequence is
#: [] / OB_CREATED / FIRST_TOUCH_LIMIT_ACTIVATED / ENTRY_FILLED / TRADE_CLOSED,
#: and a $10.00 account ends this trade holding $10.406466666666667.
FULL_TRADE: List[Row] = [S_ORIGIN, S_BOS, S_TOUCH, S_ENTRY, S_TP]

#: The window expires unfilled: first touch on bar 2, INVALIDATED on bar 5.
EXPIRING: List[Row] = [S_ORIGIN, S_BOS, S_TOUCH, S_NEAR, S_NEAR, S_NEAR]

#: A second, LOWER short OB (MANUAL_BTCUSD_SHORT_2_3: top 97.0, bottom 94.0,
#: entry 94.75) whose whole lifecycle runs while the reference OB waits above.
TWO_OBS: List[Row] = [
    S_ORIGIN, S_BOS,
    (95.0, 98.0, 94.0, 97.0),     # 2: bull origin, entirely below OB1's zone
    (93.0, 93.5, 92.0, 93.0),     # 3: BOS -> OB2
    (95.0, 95.0, 94.0, 95.0),     # 4: OB2 first touch AND fill
    (94.0, 94.0, 93.5, 94.0),     # 5: OB2 take profit
    S_TOUCH, S_ENTRY,             # 6, 7: OB1 finally touched, then filled
]
SECOND_OB_ID = "MANUAL_BTCUSD_SHORT_2_3"

# ---------------------------------------------------------------------------
# Drivers. NOTE the absence of ACTIVATION policy keywords: those defaults are the
# subject. The DETECTOR is named, because every row above is half-candle geometry
# (`ob_top = CLOSE 105.0`, `ob_bottom = LOW 99.0`) and the shipped default is now
# `DETECTOR_LUXALGO_INTERNAL`, whose zone is the whole origin candle. The ledger,
# the fingerprint and the suppression path are detector-agnostic -- which is
# itself asserted, on LuxAlgo-generated blocks, by
# `TestTheLedgerHoldsLuxAlgoBlocksToo` at the end of this module.
# ---------------------------------------------------------------------------
FILLED = ManualLifecycleEventType.ENTRY_FILLED
ARMED = ManualLifecycleEventType.FIRST_TOUCH_LIMIT_ACTIVATED
CREATED = ManualLifecycleEventType.OB_CREATED
INVALID = ManualLifecycleEventType.INVALIDATED
CLOSED = ManualLifecycleEventType.TRADE_CLOSED
SUPPRESSED = ManualLifecycleEventType.OB_CREATION_SUPPRESSED


def _strategy(assets: Sequence[str] = ("BTCUSD",)) -> ManualSMCStrategy:
    """Shipped activation defaults, half-candle detector, funded with $10."""
    return ManualSMCStrategy(assets=list(assets), account_balance=10.0,
                             detector=DETECTOR_MANUAL_SPEC)


def _feed(
    strategy: ManualSMCStrategy,
    rows: Sequence[Optional[Row]],
    asset: str = "BTCUSD",
    bar_shift: int = 0,
) -> List[ManualSMCEvaluation]:
    """
    Feed closed candles in order; row k is bar `k + bar_shift` at time `_ts(k)`.

    `bar_shift` models a BACKFILL and nothing else: prepending older history
    renumbers every bar index while the candles themselves keep the timestamps
    they always had. It is the only way to observe an `ob_id` change without
    changing the market, which is exactly what `TestTheFingerprintIsTheIdentity`
    needs. Left at 0, bar index and timestamp hour agree, as elsewhere.
    """
    out: List[ManualSMCEvaluation] = []
    for k, row in enumerate(rows):
        if row is None:
            continue
        out.append(strategy.evaluate_closed_candle(
            asset, k + bar_shift, _ts(k), *row))
    return out


def _run(rows: Sequence[Row],
         asset: str = "BTCUSD") -> Tuple[ManualSMCStrategy, List[ManualSMCEvaluation]]:
    strategy = _strategy((asset,))
    return strategy, _feed(strategy, rows, asset)


def _kinds(evals: Sequence[ManualSMCEvaluation]) -> List[List[str]]:
    """The event names of each candle, in order. The whole story of a run."""
    return [[e.event_type.name for e in ev.events] for ev in evals]


def _bars_with(evals: Sequence[ManualSMCEvaluation],
               kind: ManualLifecycleEventType,
               ob_id: Optional[str] = None) -> List[int]:
    return [ev.bar_idx for ev in evals for e in ev.events
            if e.event_type is kind and (ob_id is None or e.ob_id == ob_id)]


def _snapshot(strategy: ManualSMCStrategy) -> Dict[str, object]:
    """Capture through JSON, exactly as a restart does. No shared objects."""
    return loads_state(dumps_state(strategy.capture_state()))


def _resume(payload: Dict[str, object]) -> ManualSMCStrategy:
    """A restart: a NEW strategy rebuilt from the snapshot alone."""
    return ManualSMCStrategy.from_state(payload, account_balance=10.0)


def _rescan(payload: Dict[str, object],
            asset: str = "BTCUSD") -> ManualSMCStrategy:
    """
    A FRESH PROCESS that replays history from the beginning.

    `restore_state` rebuilds the lifecycle -- including the persisted ledger and
    the activation policy recorded in the snapshot, so no policy is restated
    here -- and `lifecycle.reset()` then drops the order-block pool and every
    scanner's consumed-origin set while DELIBERATELY keeping the ledger. What is
    left is precisely a new process about to re-derive its order blocks from raw
    candles: fresh watermark, fresh global-order clock, no memory of any order
    block, and the operator's decisions loaded from disk.
    """
    restored = restore_state(payload)
    restored.lifecycle.reset()
    return ManualSMCStrategy(config=restored.config, assets=[asset],
                             lifecycle=restored.lifecycle, account_balance=10.0)


def _ledger_of(payload: Dict[str, object]) -> ManualOBInvalidationLedger:
    return restore_state(payload).lifecycle.invalidations


def _runtime(symbols: Sequence[str] = ("BTCUSD",), **kw) -> "RT.ManualSMCRuntime":
    """
    A runtime on the provenance-verified instrument registry, as Task K does.

    The strategy is HANDED IN rather than left to the runtime, for one reason: the
    rows in this module are half-candle geometry, and the runtime's own default is
    `DETECTOR_LUXALGO_INTERNAL`. Everything else is copied from the production
    construction at `manual_smc_runtime.py` -- the production config, the registry
    tick specs, first-touch activation and the shipped entry window -- so what is
    exercised below is still the real dashboard boundary; only the structure
    detector is swapped for the one these fixtures were drawn for. That the
    runtime's own default is LuxAlgo is asserted separately, and positively, by
    `test_manual_smc_runtime.py` and by `TestProductionPolicyDefaults` in
    `test_manual_smc_first_touch_window.py`.
    """
    balance = float(kw.pop("account_balance", 10.0))
    resolved = list(symbols)
    config = kw.get("config") or RT.manual_smc_production_config()
    registry = RT.delta_india_registry()
    kw.setdefault("strategy", RT.ManualSMCStrategy(
        config=config,
        assets=resolved,
        account_balance=balance,
        tick_specs={s: registry.get(s) for s in resolved},
        activation_mode=RT.ACTIVATION_MODE_FIRST_TOUCH,
        entry_window_candles=RT.ENTRY_WINDOW_CANDLES,
        detector=DETECTOR_MANUAL_SPEC,
    ))
    return RT.ManualSMCRuntime(symbols=resolved, timeframe="1h", **kw)


def _feed_runtime(runtime, symbol: str, rows: Sequence[Row]) -> None:
    """
    Closed candles through the wiring layer.

    The runtime derives its own absolute bar index from the timestamp, so an
    `ob_id` here is `MANUAL_BTCUSD_SHORT_490896_490897` rather than the
    `_0_1` of the strategy-level tests. No test below hardcodes one: they are
    read back from `active_obs()`, which is what a dashboard row would carry.
    """
    for k, row in enumerate(rows):
        runtime.on_closed_candle(RT.ClosedCandle(
            symbol=symbol, ts=_ts(k), open=row[0], high=row[1],
            low=row[2], close=row[3]))


# ---------------------------------------------------------------------------
# The identity. Requirement A7: a stable key from the order block's own
# characteristics, never a dashboard row index.
# ---------------------------------------------------------------------------
class TestTheFingerprintIsTheIdentity:

    def test_the_fingerprint_is_the_documented_string(self):
        assert make_fingerprint("BTCUSD", "SHORT", _ts(0), _ts(1)) == (
            "MANUAL_SMC_OB|v1|BTCUSD|SHORT"
            "|2026-01-01T00:00:00+00:00|2026-01-01T01:00:00+00:00")

    def test_every_part_of_that_string_is_a_named_constant(self):
        """So the format cannot drift silently from what is on disk."""
        fp = make_fingerprint("BTCUSD", "SHORT", _ts(0), _ts(1))
        head, version, asset, direction, origin, bos = fp.split(FINGERPRINT_SEP)
        assert (head, version) == (FINGERPRINT_PREFIX, FINGERPRINT_VERSION)
        assert (asset, direction) == ("BTCUSD", "SHORT")
        assert (origin, bos) == (_ts(0).isoformat(), _ts(1).isoformat())

    def test_a_live_order_block_fingerprints_to_its_own_characteristics(self):
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert (ob.formation_dt, ob.bos_dt) == (_ts(0), _ts(1))
        assert fingerprint_for_ob(ob) == make_fingerprint(
            ob.asset, ob.direction, ob.formation_dt, ob.bos_dt)

    def test_a_backfill_changes_the_ob_id_and_not_the_fingerprint(self):
        """
        THE reason the key is not `ob_id`. Five bars of earlier history renumber
        the same market order block from `_0_1` to `_5_6`; the fingerprint is
        identical because it is built from the candles' timestamps.
        """
        plain, _ = _run(FULL_TRADE[:2])
        shifted = _strategy()
        _feed(shifted, FULL_TRADE[:2], bar_shift=5)

        before = plain.lifecycle.live_obs[SHORT_OB_ID]
        after, = shifted.lifecycle.live_obs.values()
        assert after.ob_id == "MANUAL_BTCUSD_SHORT_5_6" != before.ob_id
        assert fingerprint_for_ob(after) == fingerprint_for_ob(before)

    def test_direction_is_part_of_the_identity(self):
        short = make_fingerprint("BTCUSD", "SHORT", _ts(0), _ts(1))
        assert short != make_fingerprint("BTCUSD", "LONG", _ts(0), _ts(1))

    def test_asset_and_both_timestamps_are_part_of_the_identity(self):
        ref = make_fingerprint("BTCUSD", "SHORT", _ts(0), _ts(1))
        assert ref != make_fingerprint("ETHUSD", "SHORT", _ts(0), _ts(1))
        assert ref != make_fingerprint("BTCUSD", "SHORT", _ts(1), _ts(1))
        assert ref != make_fingerprint("BTCUSD", "SHORT", _ts(0), _ts(2))

    def test_a_naive_timestamp_is_refused_rather_than_assumed_to_be_utc(self):
        with pytest.raises(NaiveTimestampError):
            make_fingerprint("BTCUSD", "SHORT", datetime(2026, 1, 1), _ts(1))
        with pytest.raises(NaiveTimestampError):
            make_fingerprint("BTCUSD", "SHORT", _ts(0), datetime(2026, 1, 1, 1))

    def test_a_non_utc_timestamp_normalises_to_the_same_instant(self):
        """The operator's clock must not change which order block is deleted."""
        ist = timezone(timedelta(hours=5, minutes=30))
        assert make_fingerprint("BTCUSD", "SHORT",
                                _ts(0).astimezone(ist),
                                _ts(1).astimezone(ist)) == make_fingerprint(
            "BTCUSD", "SHORT", _ts(0), _ts(1))

    @pytest.mark.parametrize("asset,direction", [
        ("BTCUSD", "BUY"), ("BTCUSD", "short"), ("BTCUSD", ""),
        ("", "SHORT"), ("BTCUSD", "SELL"),
    ])
    def test_an_unrecognised_identity_fails_closed(self, asset, direction):
        with pytest.raises(InvalidObIdentityError):
            make_fingerprint(asset, direction, _ts(0), _ts(1))


# ---------------------------------------------------------------------------
# Requirements A1-A3: the operator selects a visible order block, deletes it,
# and the deletion is a recorded decision rather than a hidden row.
# ---------------------------------------------------------------------------
class TestALiveOrderBlockCanBeInvalidated:

    def test_the_order_block_is_visible_before_the_operator_acts(self):
        strategy, evals = _run(FULL_TRADE[:2])
        assert _kinds(evals) == [[], ["OB_CREATED"]]
        assert SHORT_OB_ID in strategy.lifecycle.live_obs
        assert [ob.ob_id for ob in strategy.lifecycle.candidate_obs()] == [
            SHORT_OB_ID]

    def test_invalidating_it_reports_exactly_what_happened(self):
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        outcome = strategy.invalidate_ob(ob)
        assert outcome.created is True
        assert outcome.evicted_ob_id == SHORT_OB_ID
        assert outcome.live_trade_untouched is False
        assert outcome.fingerprint == fingerprint_for_ob(ob)

    def test_the_order_block_leaves_the_pool_immediately(self):
        """Untradable from this instant, not from the next candle."""
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        strategy.invalidate_ob(ob)
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs
        assert strategy.lifecycle.candidate_obs() == []
        assert ob.state is ManualOBState.INVALIDATED
        assert strategy.is_operator_invalidated(ob) is True

    def test_the_decision_records_the_order_block_it_names(self):
        """
        A3: not a UI-only hide. The ledger keeps the market characteristics, the
        zone, the id as it stood, and who asked -- enough to audit the decision
        after the order block itself is long gone.
        """
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        strategy.invalidate_ob(ob)
        record, = strategy.invalidated_obs()
        assert (record.asset, record.direction) == ("BTCUSD", "SHORT")
        assert (record.origin_dt, record.bos_dt) == (_ts(0), _ts(1))
        assert (record.ob_top, record.ob_bottom) == (105.0, 99.0)
        assert record.ob_id_at_request == SHORT_OB_ID
        assert (record.requested_by, record.reason) == (
            DEFAULT_REQUESTED_BY, DEFAULT_REASON)

    def test_an_operator_supplied_reason_is_kept_verbatim(self):
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID],
                               requested_by="DESK_1", reason="MISREAD_BOS")
        record, = strategy.invalidated_obs()
        assert (record.requested_by, record.reason) == ("DESK_1", "MISREAD_BOS")

    def test_the_eviction_emits_the_same_event_an_expiry_emits(self):
        """
        So the runtime's existing cancel path is reused rather than duplicated:
        `cancel_expired_entries` already keys on INVALIDATED.
        """
        strategy, _ = _run(FULL_TRADE[:2])
        events: List[object] = []
        strategy.lifecycle.invalidate_ob(
            strategy.lifecycle.live_obs[SHORT_OB_ID], bar_idx=1, events=events)
        kinds = [e.event_type for e in events]
        assert kinds == [INVALID]
        assert events[0].ob_id == SHORT_OB_ID
        assert "permanently untradable" in events[0].detail


# ---------------------------------------------------------------------------
# Requirement A5: never tradable again, even if price later touches it.
# ---------------------------------------------------------------------------
class TestAnInvalidatedOrderBlockNeverTrades:

    def test_the_control_run_does_trade(self):
        """
        The A/B baseline. Without the invalidation this exact fixture arms,
        fills and closes in profit -- so a silent "no trade" below cannot be
        mistaken for a fixture that never traded in the first place.
        """
        strategy, evals = _run(FULL_TRADE)
        assert _kinds(evals) == [[], ["OB_CREATED"],
                                 ["FIRST_TOUCH_LIMIT_ACTIVATED"],
                                 ["ENTRY_FILLED"], ["TRADE_CLOSED"]]
        assert [(x.ob_id, x.outcome) for x in strategy.lifecycle.exits] == [
            (SHORT_OB_ID, "FILLED_TP")]
        assert strategy.account_balance == pytest.approx(10.406466666666667)

    def test_touching_the_entry_after_invalidation_produces_nothing(self):
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        evals = _feed(strategy, [None, None, S_TOUCH, S_ENTRY, S_TP])
        assert _kinds(evals) == [[], [], []]
        assert strategy.lifecycle.active_trade is None
        assert strategy.lifecycle.exits == []
        assert strategy.account_balance == 10.0

    def test_an_already_armed_limit_is_killed_before_it_can_fill(self):
        """The worst case: the operator deletes the setup while it is resting."""
        strategy, evals = _run(FULL_TRADE[:3])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert ob.state is ManualOBState.LIMIT_RESTING
        assert _bars_with(evals, ARMED) == [2]

        outcome = strategy.invalidate_ob(ob)
        assert outcome.evicted_ob_id == SHORT_OB_ID
        tail = _feed(strategy, [None, None, None, S_ENTRY, S_TP])
        assert _kinds(tail) == [[], []]
        assert strategy.lifecycle.active_trade is None

    def test_the_sweep_kills_a_covered_order_block_it_finds_in_the_pool(self):
        """
        The guard that does not depend on eviction timing: a ledger entry added
        WITHOUT going through `invalidate_ob` (the shape a snapshot restored
        beside an older pool could take) still stops the order block on the next
        candle, before any state branch runs.
        """
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        strategy.lifecycle.invalidations.add(record_for_ob(ob, _ts(1)))
        assert SHORT_OB_ID in strategy.lifecycle.live_obs      # still in the pool

        evals = _feed(strategy, [None, None, S_TOUCH, S_ENTRY])
        assert _kinds(evals) == [["INVALIDATED"], []]
        assert strategy.lifecycle.live_obs == {}
        assert strategy.lifecycle.active_trade is None

    def test_the_fill_itself_refuses_a_covered_order_block(self):
        """
        The last gate, asserted at the single line that creates a trade rather
        than through the ordering of two other functions. `_update_resting` is
        called directly with the entry candle, on a resting order block whose
        fingerprint is in the ledger and which is still in the pool.
        """
        strategy, _ = _run(FULL_TRADE[:3])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert (ob.state, ob.limit_active_from_bar) == (
            ManualOBState.LIMIT_RESTING, 2)
        strategy.lifecycle.invalidations.add(record_for_ob(ob, _ts(3)))

        events: List[object] = []
        dead = strategy.lifecycle._update_resting(
            ob, 3, _ts(3), S_ENTRY[1], S_ENTRY[2], events)
        assert dead is True
        assert ob.state is ManualOBState.INVALIDATED
        assert [e.event_type for e in events] == [INVALID]
        assert "fill refused" in events[0].detail
        assert strategy.lifecycle.active_trade is None


# ---------------------------------------------------------------------------
# Requirement A4: the decision lives in authoritative state and survives a
# backend restart. `state.py` schema v3 carries it; there is no parallel store.
# ---------------------------------------------------------------------------
class TestTheDecisionSurvivesARestart:

    @staticmethod
    def _invalidated_snapshot() -> Tuple[ManualSMCStrategy, Dict[str, object]]:
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        return strategy, _snapshot(strategy)

    def test_the_snapshot_carries_the_decision(self):
        _, payload = self._invalidated_snapshot()
        entry, = payload["invalidations"]
        assert entry == {
            "fingerprint": ("MANUAL_SMC_OB|v1|BTCUSD|SHORT"
                            "|2026-01-01T00:00:00+00:00"
                            "|2026-01-01T01:00:00+00:00"),
            "asset": "BTCUSD", "direction": "SHORT",
            "origin_dt": "2026-01-01T00:00:00+00:00",
            "bos_dt": "2026-01-01T01:00:00+00:00",
            "requested_at": "2026-01-01T01:00:00+00:00",
            "requested_by": "OPERATOR",
            "reason": "MANUAL_DASHBOARD_INVALIDATION",
            "ob_id_at_request": "MANUAL_BTCUSD_SHORT_0_1",
            "ob_top": 105.0, "ob_bottom": 99.0,
        }

    def test_a_clean_snapshot_carries_an_empty_list_not_a_missing_key(self):
        strategy, _ = _run(FULL_TRADE[:2])
        assert _snapshot(strategy)["invalidations"] == []

    def test_the_restored_strategy_holds_the_decision(self):
        _, payload = self._invalidated_snapshot()
        resumed = _resume(payload)
        record, = resumed.invalidated_obs()
        assert record.fingerprint == make_fingerprint(
            "BTCUSD", "SHORT", _ts(0), _ts(1))
        assert len(resumed.lifecycle.invalidations) == 1

    def test_a_reload_does_not_restore_tradability(self):
        """
        The requirement, stated as an A/B: the SAME candles after the SAME
        restart trade in the control and produce nothing here. Balance is the
        independent witness -- $10.406466666666667 against exactly $10.00.
        """
        control, _ = _run(FULL_TRADE[:2])
        resumed_control = _resume(_snapshot(control))
        control_tail = _feed(resumed_control, [None, None, S_TOUCH, S_ENTRY, S_TP])
        assert _kinds(control_tail) == [["FIRST_TOUCH_LIMIT_ACTIVATED"],
                                        ["ENTRY_FILLED"], ["TRADE_CLOSED"]]
        assert resumed_control.account_balance == pytest.approx(10.406466666666667)

        _, payload = self._invalidated_snapshot()
        resumed = _resume(payload)
        tail = _feed(resumed, [None, None, S_TOUCH, S_ENTRY, S_TP])
        assert _kinds(tail) == [[], [], []]
        assert resumed.lifecycle.exits == []
        assert resumed.account_balance == 10.0

    def test_the_payload_is_canonical_whatever_order_the_decisions_arrived_in(self):
        """Two operators deleting the same two setups snapshot byte-identically."""
        first, _ = _run(TWO_OBS[:4])
        second, _ = _run(TWO_OBS[:4])
        ids = (SHORT_OB_ID, SECOND_OB_ID)
        for ob_id in ids:
            first.invalidate_ob(first.lifecycle.live_obs[ob_id])
        for ob_id in reversed(ids):
            second.invalidate_ob(second.lifecycle.live_obs[ob_id])
        assert (dumps_state(first.capture_state())
                == dumps_state(second.capture_state()))

    def test_a_v2_snapshot_is_refused_rather_than_assumed_to_be_empty(self):
        """
        A DISCLOSED consequence of the schema bump: a v2 payload records no
        decisions, and restoring one would have to assume there were none --
        which resurrects every deleted order block. It is refused instead, so an
        older on-disk snapshot must be recaptured rather than resumed.
        """
        _, payload = self._invalidated_snapshot()
        stale = copy.deepcopy(payload)
        stale["schema_version"] = 2
        with pytest.raises(UnsupportedSchemaVersionError):
            restore_state(stale)

    def test_a_tampered_fingerprint_is_refused(self):
        """The key is re-derived on load; it cannot name a different OB."""
        _, payload = self._invalidated_snapshot()
        forged = copy.deepcopy(payload)
        forged["invalidations"][0]["fingerprint"] = make_fingerprint(
            "ETHUSD", "SHORT", _ts(0), _ts(1))
        with pytest.raises(LedgerIntegrityError):
            restore_state(forged)

    def test_two_conflicting_records_for_one_order_block_are_refused(self):
        """A5's storage half: the snapshot cannot hold two different decisions."""
        _, payload = self._invalidated_snapshot()
        conflicted = copy.deepcopy(payload)
        second = dict(conflicted["invalidations"][0])
        second["reason"] = "SOMETHING_ELSE"
        conflicted["invalidations"].append(second)
        with pytest.raises(StateIntegrityError):
            restore_state(conflicted)

    def test_a_repeated_identical_record_in_one_snapshot_is_harmless(self):
        _, payload = self._invalidated_snapshot()
        doubled = copy.deepcopy(payload)
        doubled["invalidations"].append(copy.deepcopy(
            doubled["invalidations"][0]))
        assert len(restore_state(doubled).lifecycle.invalidations) == 1

    def test_a_snapshot_that_still_holds_a_deleted_order_block_is_refused(self):
        """
        An inconsistent pair -- a live, non-active order block in the pool whose
        fingerprint is in the ledger -- cannot arise from this code, and is
        refused rather than repaired, because restoring it could let a deleted
        setup fill.
        """
        clean, _ = _run(FULL_TRADE[:2])
        _, invalidated = self._invalidated_snapshot()
        spliced = _snapshot(clean)
        spliced["invalidations"] = copy.deepcopy(invalidated["invalidations"])
        with pytest.raises(StateIntegrityError) as exc:
            restore_state(spliced)
        assert SHORT_OB_ID in str(exc.value)


# ---------------------------------------------------------------------------
# Requirement A6: a rescan of historical candles must not recreate the setup.
# ---------------------------------------------------------------------------
class TestTheDecisionSurvivesARescan:

    @staticmethod
    def _payload_after_invalidation() -> Dict[str, object]:
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        return _snapshot(strategy)

    def test_a_fresh_process_replaying_the_same_candles_suppresses_creation(self):
        """
        The scanner has no memory of the order block -- its consumed set is
        empty -- so it re-derives it from the candles and the ledger refuses to
        admit it. OB_CREATION_SUPPRESSED stands where OB_CREATED would.
        """
        rescanned = _rescan(self._payload_after_invalidation())
        evals = _feed(rescanned, FULL_TRADE)
        assert _kinds(evals) == [[], ["OB_CREATION_SUPPRESSED"], [], [], []]
        assert rescanned.lifecycle.live_obs == {}
        assert rescanned.lifecycle.active_trade is None
        assert rescanned.lifecycle.exits == []
        assert rescanned.account_balance == 10.0

    def test_the_suppression_names_the_order_block_it_refused(self):
        rescanned = _rescan(self._payload_after_invalidation())
        evals = _feed(rescanned, FULL_TRADE[:2])
        event, = evals[1].events
        assert event.event_type is SUPPRESSED
        assert event.ob_id == SHORT_OB_ID
        assert "never tradable" in event.detail

    def test_a_rescan_after_a_backfill_is_also_suppressed(self):
        """
        A6 and A7 together: earlier history renumbers the order block to
        `MANUAL_BTCUSD_SHORT_5_6`, an id the operator never saw and the ledger
        never stored, and it is still refused -- because the key is the
        timestamps, not the id.
        """
        rescanned = _rescan(self._payload_after_invalidation())
        evals = _feed(rescanned, FULL_TRADE, bar_shift=5)
        assert _kinds(evals) == [[], ["OB_CREATION_SUPPRESSED"], [], [], []]
        event, = evals[1].events
        assert event.ob_id == "MANUAL_BTCUSD_SHORT_5_6"
        assert rescanned.lifecycle.live_obs == {}

    def test_the_control_rescan_recreates_the_order_block(self):
        """
        Proof that the scanner really does re-derive it: with no ledger, the same
        replay creates the order block again and trades it. This is the failure
        mode requirement A6 describes, observed.
        """
        control = _rescan(_snapshot(_run(FULL_TRADE[:2])[0]))
        evals = _feed(control, FULL_TRADE)
        assert _kinds(evals) == [[], ["OB_CREATED"],
                                 ["FIRST_TOUCH_LIMIT_ACTIVATED"],
                                 ["ENTRY_FILLED"], ["TRADE_CLOSED"]]
        assert control.account_balance == pytest.approx(10.406466666666667)

    def test_reset_keeps_the_ledger(self):
        """
        `reset()` is the replay helper. Clearing the ledger there would make
        every replay resurrect every deleted order block, so it deliberately
        does not.
        """
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        strategy.lifecycle.reset()
        assert strategy.lifecycle.live_obs == {}
        assert len(strategy.lifecycle.invalidations) == 1

    def test_the_scanner_consumed_set_really_is_empty_after_a_rescan_start(self):
        """Names the mechanism the suppression defends against, in one line."""
        rescanned = _rescan(self._payload_after_invalidation())
        assert all(not scanner._consumed
                   for scanner in rescanned.lifecycle._scanners.values())


# ---------------------------------------------------------------------------
# Requirement A8: idempotent, and one order block cannot become two decisions.
# ---------------------------------------------------------------------------
class TestInvalidationIsIdempotent:

    def test_a_repeated_request_creates_nothing_and_raises_nothing(self):
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        first = strategy.invalidate_ob(ob)
        second = strategy.invalidate_ob(ob)
        third = strategy.invalidate_ob(ob)
        assert [o.created for o in (first, second, third)] == [True, False, False]
        assert len(strategy.lifecycle.invalidations) == 1

    def test_the_repeat_reports_no_second_eviction(self):
        """The order block left the pool once; the repeat has nothing to evict."""
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert strategy.invalidate_ob(ob).evicted_ob_id == SHORT_OB_ID
        assert strategy.invalidate_ob(ob).evicted_ob_id is None

    def test_the_first_decision_is_the_one_kept(self):
        """
        A5's other half: a later request with a different reason does NOT
        rewrite history. The audit trail keeps who decided first.
        """
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        strategy.invalidate_ob(ob, requested_by="ALICE", reason="FIRST")
        strategy.invalidate_ob(ob, requested_by="BOB", reason="SECOND")
        strategy.invalidate_ob(asset="BTCUSD", direction="SHORT",
                               origin_dt=_ts(0), bos_dt=_ts(1),
                               requested_at=_ts(9), requested_by="CAROL",
                               reason="THIRD")
        record, = strategy.invalidated_obs()
        assert (record.requested_by, record.reason) == ("ALICE", "FIRST")
        assert record.requested_at == _ts(1)

    def test_a_known_fingerprint_re_affirms_the_existing_decision(self):
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        strategy.invalidate_ob(ob, requested_by="ALICE")
        again = strategy.invalidate_ob(fingerprint=fingerprint_for_ob(ob))
        assert (again.created, again.record.requested_by) == (False, "ALICE")
        assert len(strategy.lifecycle.invalidations) == 1

    def test_an_unknown_fingerprint_cannot_create_a_record(self):
        """A bare key could not be checked against the OB it claims to name."""
        strategy, _ = _run(FULL_TRADE[:2])
        with pytest.raises(ValueError) as exc:
            strategy.invalidate_ob(fingerprint=make_fingerprint(
                "BTCUSD", "LONG", _ts(0), _ts(1)))
        assert "cannot be built from a key alone" in str(exc.value)
        assert len(strategy.lifecycle.invalidations) == 0

    def test_a_decision_without_a_timestamp_is_refused_not_guessed(self):
        strategy, _ = _run(FULL_TRADE[:2])
        with pytest.raises(ValueError) as exc:
            strategy.invalidate_ob(asset="BTCUSD", direction="SHORT",
                                   origin_dt=_ts(0), bos_dt=_ts(1))
        assert "requested_at is required" in str(exc.value)
        assert len(strategy.lifecycle.invalidations) == 0

    def test_the_two_addressing_modes_agree_on_the_same_key(self):
        """A dashboard row and a characteristics form must not disagree."""
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        by_ob = strategy.invalidate_ob(ob)
        by_chars = strategy.invalidate_ob(
            asset="BTCUSD", direction="SHORT", origin_dt=_ts(0),
            bos_dt=_ts(1), requested_at=_ts(2))
        assert by_chars.fingerprint == by_ob.fingerprint
        assert by_chars.created is False


# ---------------------------------------------------------------------------
# Requirement A9: invalidating an order block that is already non-tradable or
# expired is safe.
# ---------------------------------------------------------------------------
class TestAlreadyDeadOrderBlocksAreSafe:

    def test_an_expired_order_block_can_still_be_recorded(self):
        strategy, evals = _run(EXPIRING)
        assert _bars_with(evals, INVALID, SHORT_OB_ID) == [5]
        assert strategy.lifecycle.live_obs == {}

        outcome = strategy.invalidate_ob(
            asset="BTCUSD", direction="SHORT", origin_dt=_ts(0),
            bos_dt=_ts(1), requested_at=_ts(9))
        assert (outcome.created, outcome.evicted_ob_id) == (True, None)
        assert outcome.live_trade_untouched is False
        assert len(strategy.lifecycle.invalidations) == 1

    def test_recording_a_dead_order_block_keeps_it_dead_on_a_rescan(self):
        """The point of allowing A9: the expired setup cannot come back either."""
        strategy, _ = _run(EXPIRING)
        strategy.invalidate_ob(asset="BTCUSD", direction="SHORT",
                               origin_dt=_ts(0), bos_dt=_ts(1),
                               requested_at=_ts(9))
        rescanned = _rescan(_snapshot(strategy))
        evals = _feed(rescanned, FULL_TRADE)
        assert _kinds(evals) == [[], ["OB_CREATION_SUPPRESSED"], [], [], []]

    def test_an_order_block_that_never_existed_can_be_recorded(self):
        """No lookup, no exception: the decision is about characteristics."""
        strategy = _strategy()
        outcome = strategy.invalidate_ob(
            asset="BTCUSD", direction="LONG", origin_dt=_ts(0),
            bos_dt=_ts(1), requested_at=_ts(1))
        assert (outcome.created, outcome.evicted_ob_id) == (True, None)
        assert len(strategy.lifecycle.invalidations) == 1

    def test_a_settled_order_block_can_be_recorded(self):
        strategy, _ = _run(FULL_TRADE)
        assert [x.outcome for x in strategy.lifecycle.exits] == ["FILLED_TP"]
        outcome = strategy.invalidate_ob(
            asset="BTCUSD", direction="SHORT", origin_dt=_ts(0),
            bos_dt=_ts(1), requested_at=_ts(9))
        assert outcome.created is True
        assert strategy.lifecycle.exits[0].outcome == "FILLED_TP"   # untouched


# ---------------------------------------------------------------------------
# Requirements A10 and A11: one decision, one order block; everything else
# keeps its ordinary automatic lifecycle.
# ---------------------------------------------------------------------------
class TestUnrelatedOrderBlocksAreUntouched:

    def test_a_sibling_order_block_on_the_same_pair_still_trades(self):
        strategy, _ = _run(TWO_OBS[:4])
        assert sorted(strategy.lifecycle.live_obs) == [SHORT_OB_ID, SECOND_OB_ID]
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        assert list(strategy.lifecycle.live_obs) == [SECOND_OB_ID]

        tail = _feed(strategy, [None] * 4 + TWO_OBS[4:])
        assert _kinds(tail) == [
            ["FIRST_TOUCH_LIMIT_ACTIVATED", "ENTRY_FILLED"],
            ["TRADE_CLOSED"], [], []]
        assert [(x.ob_id, x.outcome) for x in strategy.lifecycle.exits] == [
            (SECOND_OB_ID, "FILLED_TP")]
        assert len(strategy.lifecycle.invalidations) == 1

    def test_the_surviving_order_block_keeps_its_own_geometry(self):
        strategy, _ = _run(TWO_OBS[:4])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        survivor = strategy.lifecycle.live_obs[SECOND_OB_ID]
        assert (survivor.entry_price, survivor.sl_price) == (94.75, 97.0)
        assert survivor.state is ManualOBState.AWAITING_DISPLACEMENT
        assert strategy.is_operator_invalidated(survivor) is False

    def test_another_pair_is_unaffected(self):
        strategy = _strategy(("BTCUSD", "ETHUSD"))
        for k, row in enumerate(FULL_TRADE[:2]):
            for asset in ("BTCUSD", "ETHUSD"):
                strategy.evaluate_closed_candle(asset, k, _ts(k), *row)
        assert sorted(strategy.lifecycle.live_obs) == [SHORT_OB_ID, ETH_OB_ID]

        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        assert list(strategy.lifecycle.live_obs) == [ETH_OB_ID]
        assert strategy.invalidated_obs("ETHUSD") == ()
        assert len(strategy.invalidated_obs("BTCUSD")) == 1

        eth_tail: List[ManualSMCEvaluation] = []
        for k, row in enumerate(FULL_TRADE[2:], start=2):
            for asset in ("BTCUSD", "ETHUSD"):
                ev = strategy.evaluate_closed_candle(asset, k, _ts(k), *row)
                if asset == "ETHUSD":
                    eth_tail.append(ev)
        assert _kinds(eth_tail) == [["FIRST_TOUCH_LIMIT_ACTIVATED"],
                                    ["ENTRY_FILLED"], ["TRADE_CLOSED"]]
        assert [(x.ob_id, x.outcome) for x in strategy.lifecycle.exits] == [
            (ETH_OB_ID, "FILLED_TP")]

    def test_the_same_timestamps_on_another_pair_are_a_different_decision(self):
        """The asset is in the key, so one pair's deletion is not another's."""
        strategy = _strategy(("BTCUSD", "ETHUSD"))
        for k, row in enumerate(FULL_TRADE[:2]):
            for asset in ("BTCUSD", "ETHUSD"):
                strategy.evaluate_closed_candle(asset, k, _ts(k), *row)
        btc = strategy.lifecycle.live_obs[SHORT_OB_ID]
        eth = strategy.lifecycle.live_obs[ETH_OB_ID]
        assert (btc.formation_dt, btc.bos_dt) == (eth.formation_dt, eth.bos_dt)
        strategy.invalidate_ob(btc)
        assert strategy.is_operator_invalidated(eth) is False


# ---------------------------------------------------------------------------
# The disclosed case. A position already in the market is NOT abandoned by a
# dashboard click: safety rule #14 forbids releasing a slot before the position
# is proven closed. The decision is still recorded, and the outcome says so.
# ---------------------------------------------------------------------------
class TestALiveTradeIsNotAbandoned:

    @staticmethod
    def _filled() -> Tuple[ManualSMCStrategy, object]:
        strategy, _ = _run(FULL_TRADE[:4])
        assert strategy.lifecycle.active_trade is not None
        return strategy, strategy.lifecycle.live_obs[SHORT_OB_ID]

    def test_the_position_is_left_alone_and_the_report_says_so(self):
        strategy, ob = self._filled()
        outcome = strategy.invalidate_ob(ob)
        assert outcome.created is True
        assert outcome.evicted_ob_id is None
        assert outcome.live_trade_untouched is True
        assert strategy.lifecycle.active_trade is not None
        assert ob.state is ManualOBState.TRADE_ACTIVE
        assert SHORT_OB_ID in strategy.lifecycle.live_obs

    def test_the_decision_is_recorded_anyway(self):
        strategy, ob = self._filled()
        strategy.invalidate_ob(ob)
        assert len(strategy.lifecycle.invalidations) == 1
        assert strategy.is_operator_invalidated(ob) is True

    def test_the_open_trade_still_settles_on_its_own_legs(self):
        strategy, ob = self._filled()
        strategy.invalidate_ob(ob)
        tail = _feed(strategy, [None, None, None, None, S_TP])
        assert _kinds(tail) == [["TRADE_CLOSED"]]
        assert [(x.ob_id, x.outcome) for x in strategy.lifecycle.exits] == [
            (SHORT_OB_ID, "FILLED_TP")]
        assert strategy.account_balance == pytest.approx(10.406466666666667)

    def test_and_it_can_never_be_entered_again_after_settling(self):
        strategy, ob = self._filled()
        strategy.invalidate_ob(ob)
        _feed(strategy, [None, None, None, None, S_TP])
        rescanned = _rescan(_snapshot(strategy))
        evals = _feed(rescanned, FULL_TRADE)
        assert _kinds(evals) == [[], ["OB_CREATION_SUPPRESSED"], [], [], []]
        assert rescanned.lifecycle.exits == []


# ---------------------------------------------------------------------------
# Requirement A11: nothing changes for an order block nobody deleted.
# ---------------------------------------------------------------------------
class TestTheAutomaticLifecycleIsUnchanged:

    @staticmethod
    def _story(strategy: ManualSMCStrategy) -> List[Tuple[str, str, str]]:
        return [(e.event_type.name, e.ob_id, e.detail)
                for ev in _feed(strategy, FULL_TRADE) for e in ev.events]

    def test_an_empty_ledger_reproduces_the_shipped_behaviour_exactly(self):
        """Event for event, detail string for detail string."""
        default = _strategy()
        injected = _strategy()
        injected.lifecycle.invalidations = ManualOBInvalidationLedger()
        assert self._story(injected) == self._story(default)
        assert injected.account_balance == default.account_balance

    def test_an_empty_ledger_is_falsy_so_every_check_short_circuits(self):
        ledger = ManualOBInvalidationLedger()
        assert not ledger and len(ledger) == 0
        assert ledger.records() == ()
        assert ledger.fingerprints == ()

    def test_the_automatic_expiry_still_fires_on_schedule(self):
        """The three-candle window is not touched by any of this."""
        strategy, evals = _run(EXPIRING)
        assert _bars_with(evals, ARMED) == [2]
        assert _bars_with(evals, INVALID) == [5]
        assert strategy.lifecycle.invalidations.records() == ()

    def test_a_foreign_ledger_object_is_refused(self):
        for bad in ("nope", [], {}, 0, object()):
            with pytest.raises(TypeError):
                ManualSMCLifecycle(invalidations=bad)

    def test_the_default_lifecycle_starts_with_no_decisions(self):
        assert len(ManualSMCLifecycle().invalidations) == 0
        assert len(_strategy().lifecycle.invalidations) == 0


# ---------------------------------------------------------------------------
# The ledger's own invariants. A permanent decision that can be revoked by an
# API call is not permanent, so there is no such call.
# ---------------------------------------------------------------------------
class TestTheLedgerIsAppendOnly:

    #: Every public name the ledger exposes. A new one is a deliberate decision.
    SURFACE = ("add", "contains", "covers", "fingerprints", "get",
               "record_for", "records")

    def test_the_public_surface_is_pinned(self):
        assert tuple(sorted(
            n for n in dir(ManualOBInvalidationLedger)
            if not n.startswith("_"))) == self.SURFACE

    @pytest.mark.parametrize("name", [
        "remove", "un_invalidate", "uninvalidate", "revoke", "restore",
        "clear", "pop", "discard", "delete", "update", "undo", "__delitem__",
        "__setitem__",
    ])
    def test_there_is_no_way_to_take_a_decision_back(self, name):
        assert not hasattr(ManualOBInvalidationLedger, name)

    def test_no_reversal_is_defined_anywhere_in_the_module(self):
        """
        Stronger than a substring scan, which the module's own docstring would
        trip over where it *documents* the absence: this walks the syntax tree
        and asserts no such function or method is defined at any nesting depth.
        """
        defined = {
            node.name
            for node in ast.walk(ast.parse(inspect.getsource(INV)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert defined & {
            "remove", "un_invalidate", "uninvalidate", "revoke", "restore",
            "undo", "delete", "discard", "pop", "clear",
            "__delitem__", "__setitem__"} == set()
        assert "add" in defined, "the only writer is still there"

    def test_a_record_cannot_be_edited_after_the_fact(self):
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        record, = strategy.invalidated_obs()
        assert ManualOBInvalidation.__dataclass_params__.frozen is True
        with pytest.raises(Exception):
            record.reason = "CHANGED"          # type: ignore[misc]

    def test_the_records_view_cannot_be_mutated_through(self):
        strategy, _ = _run(FULL_TRADE[:2])
        strategy.invalidate_ob(strategy.lifecycle.live_obs[SHORT_OB_ID])
        records = strategy.lifecycle.invalidations.records()
        assert isinstance(records, tuple)
        assert len(strategy.lifecycle.invalidations) == 1

    def test_records_are_returned_in_a_canonical_order(self):
        strategy, _ = _run(TWO_OBS[:4])
        for ob_id in (SECOND_OB_ID, SHORT_OB_ID):
            strategy.invalidate_ob(strategy.lifecycle.live_obs[ob_id])
        fingerprints = [r.fingerprint for r in strategy.invalidated_obs()]
        assert fingerprints == sorted(fingerprints)

    def test_covers_and_contains_agree(self):
        strategy, _ = _run(FULL_TRADE[:2])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        ledger = strategy.lifecycle.invalidations
        assert ledger.covers(ob) is False
        assert ledger.contains(fingerprint_for_ob(ob)) is False
        strategy.invalidate_ob(ob)
        assert ledger.covers(ob) is True
        assert ledger.contains(fingerprint_for_ob(ob)) is True
        assert fingerprint_for_ob(ob) in ledger


# ---------------------------------------------------------------------------
# The dashboard-facing boundary. There is no Manual SMC order-block UI in this
# repository today, so `ManualSMCRuntime` IS the surface a dashboard calls:
# `active_obs` is the row source, `find_ob` resolves the row, `invalidate_ob`
# records the decision, `cancel_invalidated_entries` asks the exchange to cancel
# the resting order through the path an automatic expiry already uses.
# ---------------------------------------------------------------------------
class TestTheRuntimeIsTheDashboardBoundary:

    @staticmethod
    def _armed(symbols=("BTCUSD",), **kw):
        """
        Feed the pairs interleaved bar by bar. The single trade slot couples the
        assets, so the strategy requires global candle order: draining one pair
        before starting the next raises `GlobalOrderError` by design.
        """
        runtime = _runtime(symbols, **kw)
        for k, row in enumerate(FULL_TRADE[:3]):
            for symbol in symbols:
                runtime.on_closed_candle(RT.ClosedCandle(
                    symbol=symbol, ts=_ts(k), open=row[0], high=row[1],
                    low=row[2], close=row[3]))
        return runtime

    def test_the_row_source_shows_the_order_block(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        assert ob.state is ManualOBState.LIMIT_RESTING
        assert runtime.find_ob(ob.ob_id) is ob
        assert runtime.invalidated_obs("BTCUSD") == ()

    def test_the_report_is_what_a_dashboard_needs_to_show(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob("BTCUSD", ob.ob_id,
                                       requested_by="DESK_1",
                                       reason="DASHBOARD_DELETE")
        assert report["symbol"] == "BTCUSD"
        assert report["fingerprint"] == fingerprint_for_ob(ob)
        assert report["created"] is True
        assert report["evicted_ob_id"] == ob.ob_id
        assert report["live_trade_untouched"] is False
        assert report["requested_by"] == "DESK_1"
        assert report["reason"] == "DASHBOARD_DELETE"
        assert report["invalidated_count"] == 1
        assert report["requested_at"].tzinfo is timezone.utc
        assert runtime.active_obs("BTCUSD") == ()

    def test_the_cancel_id_is_the_one_the_expiry_path_would_use(self):
        """
        One formula in one place, so an operator deletion and an automatic
        expiry can never disagree about which resting order to cancel.
        """
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob("BTCUSD", ob.ob_id)
        assert report["cancel_setup_ids"] == (
            runtime.setup_id_for("BTCUSD", ob.ob_id, "SHORT"),)
        assert report["cancel_setup_ids"][0].endswith(f"{ob.ob_id}_SHORT")

    def test_the_report_carries_every_field_and_no_others(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob("BTCUSD", ob.ob_id)
        assert tuple(report) == (
            "symbol", "fingerprint", "created", "evicted_ob_id",
            "live_trade_untouched", "cancel_setup_ids", "requested_at",
            "requested_by", "reason", "invalidated_count")

    def test_the_operator_is_named_by_default_and_not_guessed(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob("BTCUSD", ob.ob_id)
        assert report["requested_by"] == DEFAULT_REQUESTED_BY
        assert report["reason"] == DEFAULT_REASON

    def test_the_invalidated_order_block_is_listed_as_such(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        runtime.invalidate_ob("BTCUSD", ob.ob_id)
        records = runtime.invalidated_obs("BTCUSD")
        assert len(records) == 1
        assert records[0].fingerprint == fingerprint_for_ob(ob)
        assert runtime.find_ob(ob.ob_id) is None

    def test_a_second_click_is_harmless(self):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        first = runtime.invalidate_ob("BTCUSD", ob.ob_id, requested_by="A")
        second = runtime.invalidate_ob(
            "BTCUSD", fingerprint=first["fingerprint"], requested_by="B")
        assert (first["created"], second["created"]) == (True, False)
        assert second["evicted_ob_id"] is None
        assert second["cancel_setup_ids"] == ()
        assert second["invalidated_count"] == 1
        assert runtime.invalidated_obs("BTCUSD")[0].requested_by == "A"

    def test_an_unknown_pair_is_refused(self):
        runtime = self._armed()
        with pytest.raises(RT.SymbolNotRegisteredError):
            runtime.invalidate_ob("DOGEUSD", "MANUAL_DOGEUSD_SHORT_0_1")

    def test_an_order_block_on_another_pair_is_refused(self):
        runtime = self._armed(symbols=("BTCUSD", "ETHUSD"))
        ob, = runtime.active_obs("BTCUSD")
        with pytest.raises(RT.ManualSMCWiringError, match="belongs to"):
            runtime.invalidate_ob("ETHUSD", ob.ob_id)
        assert runtime.active_obs("BTCUSD") == (ob,)
        assert runtime.invalidated_obs() == ()

    def test_an_unresolvable_id_is_refused_and_the_way_out_is_named(self):
        """
        An expired order block is no longer addressable by id. Rather than
        silently record nothing, the runtime says which characteristics build
        the permanent key (requirement A9's supported path).
        """
        runtime = self._armed()
        with pytest.raises(RT.ManualSMCWiringError,
                           match="direction, origin_dt and bos_dt"):
            runtime.invalidate_ob("BTCUSD", "MANUAL_BTCUSD_SHORT_999_1000")
        assert runtime.invalidated_obs("BTCUSD") == ()

    def test_naming_nothing_at_all_is_refused(self):
        runtime = self._armed()
        with pytest.raises(RT.ManualSMCWiringError,
                           match="no order block was named"):
            runtime.invalidate_ob("BTCUSD")

    def test_an_order_block_no_longer_in_the_pool_is_still_recordable(self):
        """
        Requirement A9: recording a decision about an order block that has
        already expired or been swept out must be safe. It is not addressable by
        id any more, so it is addressed by the characteristics the permanent key
        is built from.
        """
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob(
            "BTCUSD", direction="SHORT",
            origin_dt=_ts(40), bos_dt=_ts(41), reason="STALE_ZONE")
        assert report["created"] is True
        assert report["fingerprint"] == make_fingerprint(
            "BTCUSD", "SHORT", _ts(40), _ts(41))
        assert report["evicted_ob_id"] is None
        assert report["cancel_setup_ids"] == ()
        assert report["reason"] == "STALE_ZONE"
        assert runtime.active_obs("BTCUSD") == (ob,), "unrelated OB untouched"

    def test_the_characteristics_path_reaches_a_live_order_block_too(self):
        """
        Measured: the two addressing modes are one key, so naming a *live*
        order block by its characteristics evicts it exactly as naming it by id
        does. An operator cannot end up with a recorded decision and a still
        resting order because they used the other field.
        """
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        report = runtime.invalidate_ob(
            "BTCUSD", direction="SHORT", origin_dt=_ts(0), bos_dt=_ts(1))
        assert report["fingerprint"] == fingerprint_for_ob(ob)
        assert report["evicted_ob_id"] == ob.ob_id
        assert report["cancel_setup_ids"] == (
            runtime.setup_id_for("BTCUSD", ob.ob_id, "SHORT"),)
        assert runtime.active_obs("BTCUSD") == ()

    def test_recording_the_decision_contacts_no_exchange(self):
        """
        `invalidate_ob` is synchronous and touches local state only, so the
        decision is recorded even with the exchange unreachable. The cancel is
        a separate awaited step.
        """
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        assert not inspect.iscoroutinefunction(runtime.invalidate_ob)
        report = runtime.invalidate_ob("BTCUSD", ob.ob_id)
        assert asyncio.run(
            runtime.cancel_invalidated_entries(
                {"symbol": "BTCUSD", "cancel_setup_ids": ()})) == ()
        assert report["cancel_setup_ids"] != ()

    def test_the_decision_survives_the_runtime_state_file(self, tmp_path):
        runtime = self._armed()
        ob, = runtime.active_obs("BTCUSD")
        fingerprint = runtime.invalidate_ob("BTCUSD", ob.ob_id)["fingerprint"]
        path = runtime.save_state(tmp_path / "manual_smc_state.json")
        reloaded = RT.ManualSMCRuntime.load_state(path)
        assert [r.fingerprint for r in reloaded.invalidated_obs("BTCUSD")] == [
            fingerprint]
        assert reloaded.active_obs("BTCUSD") == ()


# ---------------------------------------------------------------------------
# Hygiene. The identity module is the bottom of the dependency graph on
# purpose: a key that outlives a restart must not be able to change meaning
# because a strategy module changed.
# ---------------------------------------------------------------------------
class TestModuleHygiene:

    def test_the_identity_module_imports_the_standard_library_only(self):
        roots = set()
        for node in ast.walk(ast.parse(inspect.getsource(INV))):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, "no relative import belongs here"
                roots.add((node.module or "").split(".")[0])
        assert roots == {"__future__", "dataclasses", "datetime", "typing"}

    def test_nothing_public_is_left_out_of_all(self):
        defined = {
            name for name, value in vars(INV).items()
            if not name.startswith("_")
            and getattr(value, "__module__", INV.__name__) == INV.__name__}
        assert defined == set(INV.__all__)
        assert len(INV.__all__) == len(set(INV.__all__))

    def test_the_key_is_built_from_declared_constants(self):
        assert (FINGERPRINT_PREFIX, FINGERPRINT_VERSION, FINGERPRINT_SEP) == (
            "MANUAL_SMC_OB", "v1", "|")
        assert (DEFAULT_REQUESTED_BY, DEFAULT_REASON) == (
            "OPERATOR", "MANUAL_DASHBOARD_INVALIDATION")

    def test_only_the_two_documented_directions_are_keyable(self):
        assert INV.VALID_DIRECTIONS == frozenset({"LONG", "SHORT"})

    def test_the_error_hierarchy_is_catchable_as_one(self):
        for exc in (InvalidObIdentityError, NaiveTimestampError,
                    LedgerIntegrityError):
            assert issubclass(exc, INV.InvalidationError)
        assert issubclass(INV.InvalidationError, Exception)


# ---------------------------------------------------------------------------
# The same feature, on blocks the SHIPPED detector made.
#
# Everything above runs `DETECTOR_MANUAL_SPEC`, because its fixtures are
# hand-drawn half-candle geometry. Production now runs
# `DETECTOR_LUXALGO_INTERNAL`, and its blocks differ in every way that could
# matter to a ledger keyed on market characteristics: the zone is the whole
# origin candle, the origin is chosen by a pivot rule rather than by the bar
# before the break, blocks are mitigated and popped by LuxAlgo's own rules, and
# there are roughly a fifth as many of them. So the chain is re-proved here from
# raw candles, with nothing hand-built: a real LuxAlgo block is picked out of a
# control run, invalidated, snapshotted, restored in a fresh process, and then
# hunted for through a FULL RESCAN from bar zero and through a backfill that
# renumbers every bar index in the series.
#
# The series is 320 seeded candles: LuxAlgo needs ATR(200) plus a swing window
# of 50 before it will admit anything, which is exactly why no fixture above is
# long enough to test it.
# ---------------------------------------------------------------------------
LUX_ASSET = "ETHUSD"
LUX_T0 = datetime(2025, 3, 1, tzinfo=timezone.utc)
LUX_BARS = 320
LUX_SPLIT = 200

# Measured from the control run below, and asserted rather than assumed:
LUX_VICTIM_ID = "MANUAL_ETHUSD_LONG_132_144"
LUX_VICTIM_FP = ("MANUAL_SMC_OB|v1|ETHUSD|LONG"
                 "|2025-03-06T12:00:00+00:00|2025-03-07T00:00:00+00:00")
LUX_BACKFILLED_ID = "MANUAL_ETHUSD_LONG_182_194"
LUX_CONTROL_IDS = (
    "MANUAL_ETHUSD_SHORT_20_39", "MANUAL_ETHUSD_LONG_42_65",
    "MANUAL_ETHUSD_LONG_91_110", "MANUAL_ETHUSD_LONG_132_144",
    "MANUAL_ETHUSD_LONG_176_199", "MANUAL_ETHUSD_SHORT_200_215",
    "MANUAL_ETHUSD_LONG_265_283")


def _lux_candles(n: int = LUX_BARS, seed: int = 771, base: int = 0,
                 t0: datetime = LUX_T0) -> List[Tuple]:
    """A seeded OHLC series long enough for the LuxAlgo warm-up."""
    import math
    import random
    rng = random.Random(seed)
    price, out = 3000.0, []
    for i in range(n):
        o = price
        c = o + math.sin(i / 7.0) * 12.0 + rng.uniform(-9.0, 9.0)
        h = max(o, c) + rng.uniform(0.0, 6.0)
        low = min(o, c) - rng.uniform(0.0, 6.0)
        out.append((base + i, t0 + timedelta(hours=i), o, h, low, c))
        price = c
    return out
def _lux_lifecycle(**kw) -> ManualSMCLifecycle:
    """The production detector and the production activation mode."""
    return ManualSMCLifecycle(assets=[LUX_ASSET],
                              activation_mode=ACTIVATION_MODE_FIRST_TOUCH,
                              detector=DETECTOR_LUXALGO_INTERNAL, **kw)


def _lux_drive(lifecycle: ManualSMCLifecycle,
               rows: Sequence[Tuple],
               watermark: Optional[CandleWatermark] = None,
               seen: Optional[Dict[str, object]] = None):
    """Closed candles in order, advancing the watermark AFTER each one."""
    events = []
    watermark = CandleWatermark() if watermark is None else watermark
    for bar_idx, ts, o, h, low, c in rows:
        events.extend(lifecycle.process_candle(
            LUX_ASSET, bar_idx, ts, o, h, low, c))
        if seen is not None:
            seen.update({ob.ob_id: ob for ob in lifecycle.live_obs.values()})
        watermark.advance(LUX_ASSET, bar_idx, ts)
    return events, watermark


def _lux_ids(events: Sequence, kind: ManualLifecycleEventType) -> List[str]:
    return [e.ob_id for e in events if e.event_type is kind]


def _lux_victim() -> Tuple[object, List[str]]:
    """
    A REAL LuxAlgo block, taken from a clean run over the whole series.

    Returned with the full created-id list so a later rescan can be compared
    against it rather than against a predicted count.
    """
    control = _lux_lifecycle()
    seen: Dict[str, object] = {}
    events, _ = _lux_drive(control, _lux_candles(), seen=seen)
    made = _lux_ids(events, ManualLifecycleEventType.OB_CREATED)
    return seen[made[len(made) // 2]], made
class TestTheLedgerHoldsLuxAlgoBlocksToo:

    def test_the_control_run_really_is_the_shipped_detector(self):
        """The measurement all the rest of this class is taken from."""
        control = _lux_lifecycle()
        assert control.detector == DETECTOR_LUXALGO_INTERNAL
        assert control.detector == ManualSMCLifecycle().detector, (
            "the control run must be on the SHIPPED detector, not a pinned one")
        events, _ = _lux_drive(control, _lux_candles())
        assert tuple(_lux_ids(
            events, ManualLifecycleEventType.OB_CREATED)) == LUX_CONTROL_IDS
        assert _lux_ids(
            events, ManualLifecycleEventType.OB_CREATION_SUPPRESSED) == []

    def test_a_luxalgo_block_is_keyed_by_its_own_characteristics(self):
        victim, _ = _lux_victim()
        assert victim.ob_id == LUX_VICTIM_ID
        assert fingerprint_for_ob(victim) == LUX_VICTIM_FP
        assert victim.bos_or_choch == "BOS"
        # The zone is the WHOLE origin candle, which is what makes these blocks
        # different from every fixture above; the key ignores geometry entirely.
        assert float(victim.ob_top) > float(victim.ob_bottom)
        assert LUX_VICTIM_FP == make_fingerprint(
            LUX_ASSET, "LONG", victim.formation_dt, victim.bos_dt)

    def test_a_live_luxalgo_block_is_evicted_when_invalidated(self):
        victim, _ = _lux_victim()
        lifecycle = _lux_lifecycle()
        _lux_drive(lifecycle, _lux_candles()[:LUX_SPLIT])
        assert LUX_VICTIM_ID in {ob.ob_id for ob in lifecycle.live_obs.values()}
        outcome = lifecycle.invalidate_ob(
            asset=LUX_ASSET, direction=victim.direction,
            origin_dt=victim.formation_dt, bos_dt=victim.bos_dt,
            requested_at=_ts(0), requested_by="DESK_7",
            reason="LUXALGO_DASHBOARD_DELETE")
        assert (outcome.created, outcome.evicted_ob_id) == (
            True, LUX_VICTIM_ID)
        assert outcome.live_trade_untouched is False
        assert outcome.fingerprint == LUX_VICTIM_FP
        assert sorted(ob.ob_id for ob in lifecycle.live_obs.values()) == [
            "MANUAL_ETHUSD_LONG_176_199", "MANUAL_ETHUSD_LONG_91_110"]
    @staticmethod
    def _invalidated_snapshot():
        """
        Mid-stream: 200 bars in, the operator deletes a live LuxAlgo block, and
        the process is snapshotted THROUGH JSON. Returns the payload and the
        victim, sharing no object with either.
        """
        victim, _ = _lux_victim()
        lifecycle = _lux_lifecycle()
        _, watermark = _lux_drive(lifecycle, _lux_candles()[:LUX_SPLIT])
        lifecycle.invalidate_ob(
            asset=LUX_ASSET, direction=victim.direction,
            origin_dt=victim.formation_dt, bos_dt=victim.bos_dt,
            requested_at=_ts(0), requested_by="DESK_7",
            reason="LUXALGO_DASHBOARD_DELETE")
        return loads_state(dumps_state(
            capture_state(lifecycle, watermark))), victim

    def test_the_decision_travels_in_a_luxalgo_snapshot(self):
        payload, _ = self._invalidated_snapshot()
        assert payload["detector"] == DETECTOR_LUXALGO_INTERNAL
        assert payload["schema_version"] == 5
        assert [r["fingerprint"] for r in payload["invalidations"]] == [
            LUX_VICTIM_FP]

    def test_the_decision_survives_a_restart_onto_the_luxalgo_detector(self):
        payload, _ = self._invalidated_snapshot()
        restored = restore_state(payload)
        assert restored.lifecycle.detector == DETECTOR_LUXALGO_INTERNAL
        assert len(restored.lifecycle.invalidations) == 1
        record = restored.lifecycle.invalidations.get(LUX_VICTIM_FP)
        assert (record.asset, record.direction) == (LUX_ASSET, "LONG")
        assert record.requested_by == "DESK_7"
        assert record.reason == "LUXALGO_DASHBOARD_DELETE"
        assert record.origin_dt.isoformat() == "2025-03-06T12:00:00+00:00"
        # And the block itself is NOT resurrected by the restore.
        assert LUX_VICTIM_ID not in {
            ob.ob_id for ob in restored.lifecycle.live_obs.values()}
    def test_a_full_luxalgo_rescan_from_bar_zero_refuses_to_re_admit_it(self):
        """
        The hard case, on the shipped detector: a fresh process with the ledger
        loaded from disk, replaying the ENTIRE series from bar zero. The block is
        not created-then-hidden -- it is never admitted, and the engine says so
        with its own event type.
        """
        payload, _ = self._invalidated_snapshot()
        ledger = restore_state(payload).lifecycle.invalidations
        rescan = _lux_lifecycle(invalidations=ledger)
        events, _ = _lux_drive(rescan, _lux_candles())
        created = _lux_ids(events, ManualLifecycleEventType.OB_CREATED)
        assert LUX_VICTIM_ID not in created
        assert created == [i for i in LUX_CONTROL_IDS if i != LUX_VICTIM_ID]
        assert _lux_ids(
            events, ManualLifecycleEventType.OB_CREATION_SUPPRESSED) == [
                LUX_VICTIM_ID]
        assert LUX_VICTIM_ID not in {
            ob.ob_id for ob in rescan.live_obs.values()}
        # It appears in exactly one event, and that event is the refusal.
        assert [e.event_type for e in events if e.ob_id == LUX_VICTIM_ID] == [
            ManualLifecycleEventType.OB_CREATION_SUPPRESSED]

    def test_the_refusal_names_the_operator_decision_as_the_reason(self):
        payload, _ = self._invalidated_snapshot()
        rescan = _lux_lifecycle(
            invalidations=restore_state(payload).lifecycle.invalidations)
        events, _ = _lux_drive(rescan, _lux_candles())
        refusal, = [e for e in events if e.event_type
                    is ManualLifecycleEventType.OB_CREATION_SUPPRESSED]
        assert refusal.asset == LUX_ASSET
        assert refusal.bar_idx == 144
        assert "operator invalidation" in refusal.detail
        assert "never tradable" in refusal.detail

    def test_a_backfill_that_renumbers_every_bar_still_suppresses_it(self):
        """
        Requirement A7 on the shipped detector: the key is market
        characteristics, so prepending 50 earlier candles -- which moves the
        block from bar 132 to bar 182 and changes its `ob_id` -- must not let it
        back in. If the ledger were keyed on `ob_id`, this rescan would trade it.
        """
        payload, _ = self._invalidated_snapshot()
        ledger = restore_state(payload).lifecycle.invalidations
        earlier = _lux_candles(50, seed=98, t0=LUX_T0 - timedelta(hours=50))
        shifted = [(i + 50, ts, o, h, low, c)
                   for i, ts, o, h, low, c in _lux_candles()]
        backfilled = _lux_lifecycle(invalidations=ledger)
        events, _ = _lux_drive(backfilled, earlier + shifted)
        assert _lux_ids(
            events, ManualLifecycleEventType.OB_CREATION_SUPPRESSED) == [
                LUX_BACKFILLED_ID]
        assert LUX_BACKFILLED_ID != LUX_VICTIM_ID, "the backfill must renumber"
        assert LUX_VICTIM_ID not in _lux_ids(
            events, ManualLifecycleEventType.OB_CREATED)
        assert LUX_BACKFILLED_ID not in {
            ob.ob_id for ob in backfilled.live_obs.values()}
    def test_the_restored_ledger_is_still_append_only(self):
        """A second click after the restart keeps the FIRST operator's record."""
        payload, victim = self._invalidated_snapshot()
        restored = restore_state(payload)
        outcome = restored.lifecycle.invalidate_ob(
            asset=LUX_ASSET, direction=victim.direction,
            origin_dt=victim.formation_dt, bos_dt=victim.bos_dt,
            requested_at=_ts(9), requested_by="DESK_9", reason="SECOND_CLICK")
        assert outcome.created is False
        assert len(restored.lifecycle.invalidations) == 1
        kept = restored.lifecycle.invalidations.get(LUX_VICTIM_FP)
        assert (kept.requested_by, kept.reason) == (
            "DESK_7", "LUXALGO_DASHBOARD_DELETE")

    def test_a_luxalgo_snapshot_still_holding_the_block_is_refused(self):
        """
        The pool and the ledger must never disagree. A payload whose order-block
        pool still contains a block the ledger says was deleted is a torn or
        tampered capture, and restoring it would resurrect a dead block.
        """
        control = _lux_lifecycle()
        _, watermark = _lux_drive(control, _lux_candles()[:LUX_SPLIT])
        resident = next(ob for ob in control.live_obs.values())
        poisoned = capture_state(control, watermark)
        donor = _lux_lifecycle()
        donor.invalidate_ob(
            asset=resident.asset, direction=resident.direction,
            origin_dt=resident.formation_dt, bos_dt=resident.bos_dt,
            requested_at=_ts(0), requested_by="DESK_7",
            reason="POOL_CONSISTENCY")
        poisoned["invalidations"] = capture_state(donor)["invalidations"]
        with pytest.raises(StateIntegrityError):
            restore_state(poisoned)

