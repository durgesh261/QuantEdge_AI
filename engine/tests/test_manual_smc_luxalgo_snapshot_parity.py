"""LuxAlgo bounded snapshot -- exact continuation parity.

`LuxAlgoInternalStructure.capture_state()` does not store every candle it has
ever seen. It keeps the Pine `var` state verbatim and keeps only the ARRAY
positions at or above `causal_watermark()`, rebasing the surviving positions so
that every absolute bar index is unchanged. The claim that makes that safe is:

    no future `step()` can read an array position below the watermark.

That claim is argued from the read inventory in `causal_watermark()`'s
docstring. This module is the EMPIRICAL half of the proof, and it is deliberately
a differential test rather than a set of pinned numbers:

    1. drive one structure over the whole sequence, uninterrupted, recording a
       full observation for every bar;
    2. for each cut point, drive a second structure over the prefix, capture,
       round-trip the blob, restore, then feed the EXACT remaining candles;
    3. require the restored run's observations to equal the uninterrupted run's,
       bar by bar, from the cut to the end.

An observation covers everything the mandate names -- pivots, BOS/CHoCH tags, OB
creation, OB origin, zone high/low, mitigation and the active OB set -- plus the
ATR recursion, the three leg states, both trend biases, the trailing extremes,
the crossover snapshots and the creation counter. Equality is exact: same floats,
same datetimes, same order, no tolerance anywhere.

Because the comparison is against a run that never restored, a bug that
corrupted state identically in both runs could not hide here: only ONE of the two
runs restores.

WHAT IS NOT COMPARED, AND WHY. `all_order_blocks` is a full creation-order
audit list. A restored structure rebuilds it from the live store, so it starts
with only the blocks that were still live at the cut and its length is no longer
`_ob_seq`. `step()` reads it exclusively as `len(...)` and as a tail slice taken
from that same length, so its history is inert -- proved here by
`test_the_created_list_is_correct_despite_the_shortened_audit_list`. The nine
audit trails (`internal_events`, `swing_events`, ...) are dropped for the same
reason and are asserted to be write-only in `TestDroppedStateIsNotRead`.
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import fields as dc_fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from quantedge.strategy.manual_smc import luxalgo_structure as LS
from quantedge.strategy.manual_smc.luxalgo_structure import (
    ALGO_OB_CAP,
    ATR_LENGTH,
    LUXALGO_SNAPSHOT_KEYS,
    SWINGS_LENGTH,
    LuxAlgoInternalStructure,
    LuxAlgoOrderBlock,
    LuxAlgoStructureError,
)

# ---------------------------------------------------------------------------
# One bar's worth of observable behaviour
# ---------------------------------------------------------------------------
Row = Tuple[int, datetime, float, float, float, float]

_BLOCK_FIELDS: Tuple[str, ...] = tuple(f.name
                                       for f in dc_fields(LuxAlgoOrderBlock))


def _block_view(block: LuxAlgoOrderBlock) -> Tuple[Any, ...]:
    """Every field of an order block, including its provenance and mitigation.

    Read off `dataclasses.fields` rather than typed out, so a new field on
    `LuxAlgoOrderBlock` is compared automatically instead of being silently
    excluded from the parity claim.
    """
    return tuple(getattr(block, name) for name in _BLOCK_FIELDS)


def _pivot_view(structure: LuxAlgoInternalStructure,
                name: str) -> Tuple[Any, ...]:
    """One pivot, with `barIndex` converted to an ABSOLUTE caller bar index.

    `LuxAlgoPivot.barIndex` is a position in the structure's own arrays, and
    pruning rebases those positions on purpose. Comparing raw positions would
    therefore report a difference that is not one; comparing
    `_base_bar_idx + barIndex` compares the candle actually referred to, which
    is what the origin search consumes.
    """
    pivot = getattr(structure, name)
    base = structure._base_bar_idx
    absolute = (None if pivot.barIndex is None or base is None
                else base + pivot.barIndex)
    return (pivot.currentLevel, pivot.lastLevel, pivot.crossed, pivot.barTime,
            absolute)


def _observe(structure: LuxAlgoInternalStructure,
             created: Sequence[LuxAlgoOrderBlock],
             removed: Sequence[LuxAlgoOrderBlock]) -> Dict[str, Any]:
    """Everything one `step()` can be observed to have done.

    `removed` is the blocks that left the live store on this bar. A mitigated
    block carries `mitigated_bar_idx`; a block evicted at `ALGO_OB_CAP` does
    not. Keeping both in one channel means a mitigation silently becoming an
    eviction (or the reverse) is a parity failure, not an invisible re-labelling.
    """
    return {
        "created": tuple(_block_view(b) for b in created),
        "live": tuple(_block_view(b) for b in structure.live_order_blocks),
        "display": tuple(_block_view(b) for b in structure.display_slice()),
        "left_live": tuple(sorted(
            (b.seq, b.mitigated_bar_idx, b.mitigated_dt) for b in removed)),
        "pivots": tuple((n, _pivot_view(structure, n))
                        for n in LS._PIVOT_NAMES),
        "trends": (structure.internal_trend.bias, structure.swing_trend.bias),
        "trailing": (structure.trailing.top, structure.trailing.bottom,
                     structure.trailing.barTime, structure.trailing.barIndex,
                     structure.trailing.lastTopTime,
                     structure.trailing.lastBottomTime),
        "legs": tuple((n, getattr(structure, a).leg, getattr(structure, a).prev)
                      for n, a in LS._LEG_NAMES),
        "crossover": (structure._prev_close, structure._prev_internal_high,
                      structure._prev_internal_low, structure._prev_swing_high,
                      structure._prev_swing_low),
        "ob_seq": structure._ob_seq,
        "last_bar_idx": structure._last_bar_idx,
        "atr": (structure._atr.value, structure._atr._count,
                structure._atr._warm_sum, structure._atr._prev_close,
                structure._atr._has_prev),
    }

# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------
def _drive(structure: LuxAlgoInternalStructure,
           rows: Sequence[Row]) -> List[Dict[str, Any]]:
    """Feed candles one at a time, recording one observation per bar.

    The live store is compared by OBJECT IDENTITY between consecutive bars to
    work out what left it. Identity is only used within a single run; the
    observations themselves hold values, so two runs are still comparable.

    `previous` IS SEEDED FROM THE STRUCTURE, not from the empty list, because
    this function is called on structures that are already mid-stream -- a
    restored one, or one whose prefix was driven by an earlier call. Seeding from
    `[]` would make the first bar of such a call unable to see anything leave the
    live store, which is not a difference in behaviour but a blind spot in the
    observer, and it would silently mask a mitigation that lands exactly on the
    cut bar. A fresh structure has an empty store, so the baseline run is
    unaffected.
    """
    out: List[Dict[str, Any]] = []
    previous: List[LuxAlgoOrderBlock] = list(structure.live_order_blocks)
    for (bar_idx, ts, o, h, l, c) in rows:
        created = structure.step(bar_idx, ts, o, h, l, c)
        live = structure.live_order_blocks
        still_there = {id(b) for b in live}
        removed = [b for b in previous if id(b) not in still_there]
        out.append(_observe(structure, created, removed))
        previous = list(live)
    return out


def _json_round_trip(blob: Dict[str, Any]) -> Dict[str, Any]:
    """Prove the blob survives a text encoding, without importing `state.py`.

    `state.py` owns the production serialisation (Decimal strings, ISO
    timestamps, the digest). This is a deliberately DIFFERENT and much dumber
    encoder whose only job is to show that nothing in the snapshot is a live
    Python object that happens to work while it stays in memory -- a lambda, a
    generator, an aliased mutable, a `float('nan')`. `float` and `datetime` are
    the only two types needing help, and both are restored exactly:
    `float.hex()` is lossless and `datetime.isoformat()` round-trips a
    timezone-aware microsecond timestamp.
    """
    def enc(value: Any) -> Any:
        if isinstance(value, bool) or value is None or isinstance(value, int):
            return value
        if isinstance(value, float):
            return {"__f": value.hex()}
        if isinstance(value, datetime):
            return {"__t": value.isoformat()}
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return {"__d": [[k, enc(v)] for k, v in value.items()]}
        if isinstance(value, (list, tuple)):
            return {"__l": [enc(v) for v in value]}
        raise AssertionError(f"snapshot holds a non-portable {type(value)}")

    def dec(value: Any) -> Any:
        if isinstance(value, dict):
            if "__f" in value:
                return float.fromhex(value["__f"])
            if "__t" in value:
                return datetime.fromisoformat(value["__t"])
            if "__d" in value:
                return {k: dec(v) for k, v in value["__d"]}
            if "__l" in value:
                return [dec(v) for v in value["__l"]]
            raise AssertionError(f"unknown envelope {sorted(value)}")
        return value

    return dec(json.loads(json.dumps(enc(blob))))

# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
SYNTHETIC_BARS = 1500


def _sequence(n: int, seed: int, drift: float, vol: float,
              burst_every: int, burst_mult: float) -> List[Row]:
    """A deterministic 1h candle sequence with tunable drift and volatility.

    `burst_every` / `burst_mult` widen one bar in every N. The bursts are the
    only way to push `high - low` past `2 x ATR200`, which is what triggers the
    `parsedHigh`/`parsedLow` swap, the high-volatility origin path and inverted
    zones. `drift` decides how much the watermark can prune: a mean-reverting
    walk crosses its pivots constantly and prunes hard, a persistent trend
    leaves an uncrossed pivot in place and prunes almost nothing. BOTH are
    driven below, because the second one is the disclosed worst case.
    """
    rng = random.Random(seed)
    rows: List[Row] = []
    price = 100.0
    for i in range(n):
        burst = burst_mult if (burst_every and i % burst_every == 0 and i) else 1.0
        price *= 1.0 + drift + rng.gauss(0.0, vol) * burst
        span = abs(rng.gauss(0.0, vol * 0.7)) * burst
        high = price * (1.0 + span)
        low = price * (1.0 - span)
        rows.append((i, _T0 + timedelta(hours=i), rng.uniform(low, high),
                     high, low, rng.uniform(low, high)))
    return rows


# THE CHURN SEQUENCE. Mean-reverting, so pivots are crossed constantly and the
# causal watermark prunes hard -- this is the sequence the cut sweep runs on,
# because a snapshot that prunes nothing proves nothing about pruning.
SYNTHETIC: List[Row] = _sequence(SYNTHETIC_BARS, 20260903, 0.0, 0.006, 137, 6.0)

# THE TREND SEQUENCE. A persistent uptrend leaves an internal pivot uncrossed
# for hundreds of bars, so the watermark barely moves and the snapshot is very
# nearly the whole history. It also produces the only inverted zone available
# synthetically. Driven at its own cuts by `TestTheWorstCaseIsNotHidden`.
TRENDING: List[Row] = _sequence(SYNTHETIC_BARS, 20260903, 0.004, 0.010, 0, 1.0)

# Cut points, chosen to hit every structural boundary in the codec rather than
# to look thorough:
#   0                     nothing consumed at all (the `m == 0` branch)
#   1, 2                  a base bar exists but no leg can have changed
#   49, 50, 51            the SWINGS_LENGTH floor the watermark may not cross
#   199, 200, 201         the ATR-200 warm-up boundary, where `atr` stops
#                         being None and high-volatility becomes possible
#   137, 138              the first volatility burst and the bar after it
#   300 .. 1499           deep cuts, where pruning is actually large
#   1500                  capture at the very end, zero remaining candles
CUTS: Tuple[int, ...] = (0, 1, 2, 49, 50, 51, 137, 138, 199, 200, 201, 300,
                         437, 700, 913, 1100, 1374, 1499, 1500)

# Cuts for the TREND sequence. 455 is the bar right after the inverted zone is
# created (origin bar 447, break bar 454), so at least one capture carries a
# swapped `barHigh < barLow` box through the codec.
TRENDING_CUTS: Tuple[int, ...] = (250, 455, 800, 1499, 1500)

REPO_ROOT = Path(__file__).parent.parent.parent
EXTENDED = (REPO_ROOT / "engine" / "data" / "research" / "manual_smc_5day_parity"
            / "canonical_extended" / "delta_exchange_india")
REAL_SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
REAL_BARS = 19762
HAVE_REAL = all((EXTENDED / s / "1h" / "full_history.csv").exists()
                for s in REAL_SYMBOLS)
needs_real = pytest.mark.skipif(
    not HAVE_REAL,
    reason="extended canonical 1h history (19,762 bars) not present")
REAL_CUTS: Tuple[int, ...] = (2331, 5000, 9881, 15000, 19000, REAL_BARS)

_REAL_CACHE: Dict[str, List[Row]] = {}


def _real_rows(symbol: str) -> List[Row]:
    """The same canonical CSV history the LuxAlgo parity suite drives."""
    if symbol not in _REAL_CACHE:
        from quantedge.ai.research.displacement_gated_retest_engine import (
            _load_canonical_candles,
        )
        raw = list(_load_canonical_candles(EXTENDED, symbol))[:REAL_BARS]
        assert len(raw) == REAL_BARS, (
            f"{symbol}: expected {REAL_BARS} candles, got {len(raw)}")
        _REAL_CACHE[symbol] = [
            (i, r["timestamp"], r["open"], r["high"], r["low"], r["close"])
            for i, r in enumerate(raw)
        ]
    return _REAL_CACHE[symbol]

# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------
class CutResult:
    """What one cut point proved, kept so the tests can assert on the numbers."""

    __slots__ = ("cut", "retained", "stream_offset", "total", "live_at_cut",
                 "ob_seq_at_cut", "final_blob_matches")

    def __init__(self, cut: int, retained: int, stream_offset: int, total: int,
                 live_at_cut: int, ob_seq_at_cut: int,
                 final_blob_matches: bool) -> None:
        self.cut = cut
        self.retained = retained
        self.stream_offset = stream_offset
        self.total = total
        self.live_at_cut = live_at_cut
        self.ob_seq_at_cut = ob_seq_at_cut
        self.final_blob_matches = final_blob_matches


def _continuation_parity(asset: str, rows: Sequence[Row], cut: int,
                         baseline: Sequence[Dict[str, Any]],
                         baseline_final: Dict[str, Any]) -> CutResult:
    """Cut, capture, round-trip, restore, resume -- then demand equality.

    Returns the measured retention numbers. Raises `AssertionError` naming the
    first differing bar and the first differing observation channel, because a
    bare "dicts differ" over 1,500 bars of nested floats is unusable.
    """
    prefix = LuxAlgoInternalStructure(asset)
    _drive(prefix, rows[:cut])
    live_at_cut = len(prefix.live_order_blocks)
    ob_seq_at_cut = prefix._ob_seq

    blob = prefix.capture_state()
    ported = _json_round_trip(blob)
    assert ported == blob, f"{asset} cut {cut}: the blob did not survive JSON"
    restored = LuxAlgoInternalStructure.restore_state(ported)

    # The restored object must not share mutable state with the captured one:
    # a later `step()` mutates arrays and blocks in place, and an alias would
    # make the parity result depend on which object was stepped.
    for name in LuxAlgoInternalStructure._SNAPSHOT_ARRAYS:
        assert getattr(restored, name) is not getattr(prefix, name)
    assert all(a is not b for a in restored.live_order_blocks
               for b in prefix.live_order_blocks)

    resumed = _drive(restored, rows[cut:])
    expected = baseline[cut:]
    assert len(resumed) == len(expected)
    for offset, (got, want) in enumerate(zip(resumed, expected)):
        if got == want:
            continue
        bar = cut + offset
        channels = sorted(k for k in want if got[k] != want[k])
        raise AssertionError(
            f"{asset}: restoring at cut {cut} diverged at bar {bar} "
            f"(+{offset} bars after the cut) in {channels}\n"
            f"  restored: {[got[k] for k in channels]}\n"
            f"  baseline: {[want[k] for k in channels]}")

    final = restored.capture_state()
    return CutResult(cut, len(blob["arrays"]["times"]), blob["stream_offset"],
                     len(rows), live_at_cut, ob_seq_at_cut,
                     final == baseline_final)

_BASELINE_STRUCTURE = LuxAlgoInternalStructure("SYN")
BASELINE: List[Dict[str, Any]] = _drive(_BASELINE_STRUCTURE, SYNTHETIC)
BASELINE_FINAL: Dict[str, Any] = _BASELINE_STRUCTURE.capture_state()

_TRENDING_STRUCTURE = LuxAlgoInternalStructure("TRD")
TRENDING_BASELINE: List[Dict[str, Any]] = _drive(_TRENDING_STRUCTURE, TRENDING)
TRENDING_FINAL: Dict[str, Any] = _TRENDING_STRUCTURE.capture_state()

# A known-good snapshot taken at the END of the churn run: a large pruned prefix,
# a live store holding blocks of both tags, and mitigation bookkeeping in every
# state. Every refusal case below is this blob with exactly one field broken.
_GOOD_BLOB: Dict[str, Any] = BASELINE_FINAL

# MEASURED, then pinned. `RETAINED_AT_END` is the window `capture_state()` keeps
# after all 1,500 bars; `WORST_RETENTION` is the widest window over every bar of
# the run, which is the number a live engine has to budget for. The trend numbers
# are the DISCLOSED worst case: the watermark is causal, not constant, and a
# sequence that never crosses its internal pivot prunes almost nothing.
RETAINED_AT_END = 71
WORST_RETENTION = 444
TRENDING_RETAINED_AT_END = 1496
TRENDING_WORST_RETENTION = 1496

# MEASURED per symbol over the full 19,762-bar canonical history: the widest
# causal window the watermark ever leaves. These are the numbers that make the
# bound a bound rather than a hope, and each one is re-derived from the data by
# `test_every_symbol_restores_at_its_own_worst_bar` before it is used as a cut.
REAL_WORST: Dict[str, int] = {
    "BTCUSD": 511,
    "ETHUSD": 384,
    "SOLUSD": 506,
    "XRPUSD": 386,
}

_REAL_BASELINE: Dict[str, Tuple[List[Dict[str, Any]], Dict[str, Any]]] = {}


def _real_baseline(symbol: str) -> Tuple[List[Dict[str, Any]],
                                        Dict[str, Any]]:
    """The uninterrupted 19,762-bar run for one symbol, driven once and cached."""
    if symbol not in _REAL_BASELINE:
        structure = LuxAlgoInternalStructure(symbol)
        observations = _drive(structure, _real_rows(symbol))
        _REAL_BASELINE[symbol] = (observations, structure.capture_state())
    return _REAL_BASELINE[symbol]


class TestTheSequenceIsActuallyExercising:
    """Non-vacuity. A parity test over a sequence that produces nothing passes.

    Every number here is MEASURED from the engine, then pinned, so that a future
    change to the generator or to the detector which quietly stops exercising
    mitigation or the volatility swap fails here instead of leaving the parity
    assertions below trivially true.
    """

    def test_the_churn_run_produces_blocks_of_both_tags(self) -> None:
        blocks = _BASELINE_STRUCTURE.all_order_blocks
        assert len(blocks) == _BASELINE_STRUCTURE._ob_seq == 80
        assert {b.tag for b in blocks} == {LS.TAG_BOS, LS.TAG_CHOCH}

    def test_the_churn_run_mitigates_most_of_what_it_creates(self) -> None:
        mitigated = [b for b in _BASELINE_STRUCTURE.all_order_blocks
                     if b.mitigated_bar_idx is not None]
        assert len(mitigated) == 69
        assert len(_BASELINE_STRUCTURE.live_order_blocks) == 11
        # Mitigation happens DURING the sweep, not only at the end, so the
        # restored runs below have to reproduce it bar by bar. 68 of the 69 are
        # seen leaving the live store; the 69th never enters it, because Pine
        # mitigates it on the same bar that created it (see below).
        left = [entry for obs in BASELINE for entry in obs["left_live"]]
        assert len(left) == 68
        assert all(entry[1] is not None for entry in left)

    def test_one_block_is_created_and_mitigated_on_the_same_bar(self) -> None:
        """A real Pine behaviour, pinned so the observer's blind spot is named.

        `_store_order_block` appends the block and `_delete_order_blocks` runs on
        the same bar, so a block whose origin candle is already breached is born
        mitigated. It therefore never appears in a between-bar live-store diff,
        and its mitigation is carried by the `created` channel instead -- which
        snapshots every field, `mitigated_bar_idx` included, AFTER the step.
        """
        same_bar = [b for b in _BASELINE_STRUCTURE.all_order_blocks
                    if b.mitigated_bar_idx == b.created_bar_idx]
        assert [b.seq for b in same_bar] == [11]
        created = [view for obs in BASELINE for view in obs["created"]]
        index = _BLOCK_FIELDS.index("mitigated_bar_idx")
        born_mitigated = [v for v in created if v[index] is not None]
        assert len(born_mitigated) == 1
        assert born_mitigated[0][_BLOCK_FIELDS.index("seq")] == 11

    def test_the_churn_run_crosses_the_atr_warm_up_and_swaps(self) -> None:
        assert _BASELINE_STRUCTURE.atr_values[ATR_LENGTH - 2] is None
        assert _BASELINE_STRUCTURE.atr_values[ATR_LENGTH - 1] is not None
        assert sum(1 for v in _BASELINE_STRUCTURE.high_volatility if v) == 31

    def test_the_churn_run_moves_every_pivot_and_both_trends(self) -> None:
        for name in LS._PIVOT_NAMES:
            assert getattr(_BASELINE_STRUCTURE, name).barIndex is not None, name
        assert _BASELINE_STRUCTURE.internal_trend.bias in (LS.BULLISH,
                                                           LS.BEARISH)
        assert _BASELINE_STRUCTURE.swing_trend.bias in (LS.BULLISH, LS.BEARISH)

    def test_the_trend_run_produces_the_inverted_zone(self) -> None:
        """The `parsedHigh`/`parsedLow` swap, end to end.

        A high-volatility origin candle stores `barHigh < barLow`. The churn
        sequence never lands one as an origin, so the trend sequence is driven
        for it -- and it is carried through a restore by
        `TestTheWorstCaseIsNotHidden`.
        """
        blocks = _TRENDING_STRUCTURE.all_order_blocks
        assert len(blocks) == 51
        inverted = [b for b in blocks if b.is_inverted]
        assert len(inverted) == 1
        block = inverted[0]
        assert block.origin_high_volatility is True
        assert block.barHigh < block.barLow

    def test_the_hundred_block_cap_is_not_reached_and_that_is_disclosed(
            self) -> None:
        """AN HONEST GAP, asserted rather than left unmentioned.

        `ALGO_OB_CAP` eviction pops the OLDEST entry of the live store. Neither
        synthetic sequence, nor any of the four 19,762-bar real histories
        (measured maximum live store across all four: 68), ever reaches 100 live
        blocks, so continuation parity cannot exercise eviction organically.
        This pins that fact so it is not mistaken for coverage.

        What protects eviction across a restore is the store's ORDER, because
        `pop()` takes the last element: `restore_state` refuses any store that is
        not strictly newest-first, and `TestRestoredIdentityIsPreserved` asserts
        the order survives. The eviction mechanics themselves are covered by
        `TestPineMitigationIteration` in `test_manual_smc_luxalgo_parity.py`, on
        a hand-built store.
        """
        assert max(len(obs["live"]) for obs in BASELINE) == 14 < ALGO_OB_CAP
        assert not [entry for obs in BASELINE
                    for entry in obs["left_live"] if entry[1] is None]


class TestContinuationParity:
    """THE MANDATED PROOF, on a 1,500-bar synthetic sequence.

    One uninterrupted run; nineteen cut points; at each one a fresh structure is
    driven over the prefix, captured, JSON round-tripped, restored, and fed the
    exact remaining candles. Every bar from the cut onward must match the
    uninterrupted run in all thirteen observation channels.
    """

    @pytest.mark.parametrize("cut", CUTS)
    def test_restoring_at_a_cut_reproduces_the_rest_of_the_run(
            self, cut: int) -> None:
        result = _continuation_parity("SYN", SYNTHETIC, cut, BASELINE,
                                      BASELINE_FINAL)
        # The snapshot accounts for every bar the prefix consumed, and a cut
        # that pruned anything still left the SWINGS_LENGTH floor intact.
        assert result.stream_offset + result.retained == cut
        if result.stream_offset:
            assert result.retained >= SWINGS_LENGTH
        else:
            assert result.retained == cut

    @pytest.mark.parametrize("cut", CUTS)
    def test_the_final_snapshot_is_identical_whether_or_not_it_restored(
            self, cut: int) -> None:
        """A stronger claim than bar-by-bar equality of the observations.

        The retained window is chosen from the pivot positions and the array
        length, both of which are absolute-equal in the two runs, so the two
        final blobs must agree BYTE FOR BYTE -- including `stream_offset`, which
        is why it is derived from `_atr._count` rather than from how much this
        particular capture happened to drop.
        """
        result = _continuation_parity("SYN", SYNTHETIC, cut, BASELINE,
                                      BASELINE_FINAL)
        assert result.final_blob_matches, (
            f"cut {cut}: the final capture differs from the uninterrupted one")

    def test_two_cuts_in_a_row_also_continue_exactly(self) -> None:
        """Restore, run on, capture again, restore again -- still exact.

        A single restore could hide an error that only a second one exposes:
        `stream_offset` accumulates and the ATR count no longer equals the
        retained length, so the cross-checks in `_restore_stream_position` are
        doing different arithmetic the second time round.
        """
        first, second = 400, 950
        structure = LuxAlgoInternalStructure("SYN")
        _drive(structure, SYNTHETIC[:first])
        mid = LuxAlgoInternalStructure.restore_state(
            _json_round_trip(structure.capture_state()))
        _drive(mid, SYNTHETIC[first:second])
        blob = mid.capture_state()
        assert blob["stream_offset"] > 0
        end = LuxAlgoInternalStructure.restore_state(_json_round_trip(blob))
        resumed = _drive(end, SYNTHETIC[second:])
        assert resumed == BASELINE[second:]
        assert end.capture_state() == BASELINE_FINAL

    def test_capture_is_idempotent_across_a_restore(self) -> None:
        blob = _BASELINE_STRUCTURE.capture_state()
        again = LuxAlgoInternalStructure.restore_state(blob).capture_state()
        assert again == blob
        third = LuxAlgoInternalStructure.restore_state(again).capture_state()
        assert third == blob

class TestTheSnapshotIsActuallyBounded:
    """Parity alone would be satisfied by keeping everything. This is the payoff.

    The retained window is a COMPUTED causal watermark, not a constant, so the
    claim being pinned is a RATIO on a known sequence, not a promise about all
    possible sequences. The unbounded worst case is real and is disclosed: an
    internal pivot that is never crossed keeps its bar reachable for as long as
    it stays uncrossed.
    """

    def test_the_watermark_never_crosses_the_swings_floor(self) -> None:
        structure = LuxAlgoInternalStructure("SYN")
        for row in SYNTHETIC:
            structure.step(*row)
            n = len(structure.times)
            k = structure.causal_watermark()
            assert 0 <= k <= max(0, n - SWINGS_LENGTH)
            assert n - k >= min(n, SWINGS_LENGTH)

    def test_the_watermark_is_below_every_live_pivot(self) -> None:
        """The whole safety argument in one assertion, re-checked every bar."""
        structure = LuxAlgoInternalStructure("SYN")
        for row in SYNTHETIC:
            structure.step(*row)
            k = structure.causal_watermark()
            for name in LS._PIVOT_NAMES:
                index = getattr(structure, name).barIndex
                if index is not None:
                    assert index >= k, (name, index, k)

    def test_pivot_bar_indexes_never_go_backwards(self) -> None:
        """The lemma that lets a capture-time watermark stay valid forever.

        A pivot is only ever overwritten by a NEWER one, so the six
        `barIndex` values are monotonically non-decreasing. Without this a
        watermark computed at capture time could be invalidated by a later bar
        reaching further back, and bounded snapshotting would be unsound.
        """
        structure = LuxAlgoInternalStructure("SYN")
        seen: Dict[str, int] = {}
        for row in SYNTHETIC:
            structure.step(*row)
            for name in LS._PIVOT_NAMES:
                index = getattr(structure, name).barIndex
                if index is None:
                    continue
                assert index >= seen.get(name, -1), (name, index, seen[name])
                seen[name] = index

    def test_the_measured_retention_on_this_sequence(self) -> None:
        """MEASURED, then pinned: what the bound actually buys on 1,500 bars.

        The interesting number is not the retention at the end of the run but the
        WORST retention over every possible capture point, because a live engine
        may be asked for a snapshot on any bar.
        """
        blob = _BASELINE_STRUCTURE.capture_state()
        retained = len(blob["arrays"]["times"])
        assert blob["stream_offset"] + retained == SYNTHETIC_BARS
        assert retained == RETAINED_AT_END, retained

        structure = LuxAlgoInternalStructure("SYN")
        worst = 0
        for row in SYNTHETIC:
            structure.step(*row)
            worst = max(worst, len(structure.times)
                        - structure.causal_watermark())
        assert worst == WORST_RETENTION, worst
        # What this buys ON THIS SEQUENCE, stated as the ratio it is: under a
        # third of the stream at the worst capture point, a twentieth at the end.
        # It is NOT a claim about every sequence -- see
        # `TestTheWorstCaseIsNotHidden`, which pins a sequence retaining 99.7%.
        assert worst < SYNTHETIC_BARS // 3
        assert retained < SYNTHETIC_BARS // 20

class TestTheWorstCaseIsNotHidden:
    """The bound is CAUSAL, not CONSTANT. Here is the sequence that proves it.

    The churn sequence above prunes 95% of its history, which is the pleasant
    result. It is not the whole truth, and the mandate is explicit that an
    unknown or an unfavourable measurement must not be hidden. A persistent
    uptrend never puts in a lower low, so `swing_low` is never crossed and its
    `barIndex` stays where it was set early in the run -- and the watermark can
    never advance past a live pivot. Retention is therefore O(bars) in the worst
    case, and this class pins that number rather than leaving it to be
    discovered in production.

    Parity is asserted on this sequence too: the snapshot is still EXACT when it
    prunes almost nothing, so the bad case costs memory, never correctness.
    """

    @pytest.mark.parametrize("cut", TRENDING_CUTS)
    def test_the_trend_sequence_also_continues_exactly(self, cut: int) -> None:
        result = _continuation_parity("TRD", TRENDING, cut, TRENDING_BASELINE,
                                      TRENDING_FINAL)
        assert result.stream_offset + result.retained == cut
        assert result.final_blob_matches

    def test_the_trend_sequence_prunes_almost_nothing(self) -> None:
        """MEASURED, then pinned: 1,496 of 1,500 bars retained. 99.7%."""
        retained = len(TRENDING_FINAL["arrays"]["times"])
        assert retained == TRENDING_RETAINED_AT_END
        assert TRENDING_FINAL["stream_offset"] == SYNTHETIC_BARS - retained == 4

        structure = LuxAlgoInternalStructure("TRD")
        worst = 0
        for row in TRENDING:
            structure.step(*row)
            worst = max(worst, len(structure.times)
                        - structure.causal_watermark())
        assert worst == TRENDING_WORST_RETENTION
        # The disclosure, as an assertion so it cannot rot: on a sequence of the
        # SAME length the snapshot ends up 21x larger than the churn run's
        # (1,496 rows vs 71) and comes within four bars of the whole history.
        assert worst > 0.99 * SYNTHETIC_BARS
        assert retained > 20 * RETAINED_AT_END

    def test_an_uncrossed_swing_pivot_is_what_holds_the_watermark(self) -> None:
        """The MECHANISM, not just the number, so the cause is not guessed at.

        Without the pivot term the watermark would be the `n - SWINGS_LENGTH`
        floor and retention would be 50 bars. It is 1,496 because one uncrossed
        pivot sits at position 4, and `causal_watermark()` may not advance past a
        pivot a future `_store_order_block` can still search back to.
        """
        structure = _TRENDING_STRUCTURE
        n = len(structure.times)
        k = structure.causal_watermark()
        assert n == SYNTHETIC_BARS
        assert k == 4 < n - SWINGS_LENGTH
        holders = [name for name in LS._PIVOT_NAMES
                   if getattr(structure, name).barIndex == k]
        assert holders == ["swing_low"], holders
        assert structure.swing_low.crossed is False

    def test_the_inverted_zone_survives_a_restore_intact(self) -> None:
        """The `parsedHigh`/`parsedLow` swap through the codec, field by field.

        A codec that "helpfully" sorted the edges, or that dropped
        `origin_high_volatility`, would restore a zone that looks tradeable and
        is not. `scanner.py` rejects an inverted zone by an explicit named rule,
        so the inversion has to arrive at the scanner intact.
        """
        original = [b for b in _TRENDING_STRUCTURE.all_order_blocks
                    if b.is_inverted]
        assert len(original) == 1
        block = original[0]
        assert block.origin_high_volatility is True
        assert block.barHigh < block.barLow

        restored = LuxAlgoInternalStructure.restore_state(
            _json_round_trip(TRENDING_FINAL))
        carried = [b for b in restored.live_order_blocks if b.is_inverted]
        assert len(carried) == 1
        assert _block_view(carried[0]) == _block_view(block)


class _Poison:
    """An audit-trail entry that raises on ANY attribute access.

    Stronger than clearing a trail: clearing shows that ABSENT history is not
    read, poisoning shows that PRESENT history is not read either, and it works
    uniformly for the nine trails regardless of their element types.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:      # pragma: no cover - must not
        raise AssertionError(                     # be reachable
            f"step() read a dropped audit-trail entry (.{name})")


# MEASURED at bar 700 of the churn run. `evicted_at_cap` is empty because
# ALGO_OB_CAP is never reached, and `suppressed_by_extra_condition` is empty
# because no internal break on either synthetic sequence -- nor on any of the
# four 19,762-bar real histories -- is suppressed by the internal filter's extra
# condition. Both are poisoned rather than cleared, which covers them anyway.
POPULATED_AUDIT_AT_700: Tuple[str, ...] = (
    "equal_events", "internal_events", "internal_pivots",
    "mitigated_order_blocks", "pine_loop_skips", "swing_events", "swing_pivots",
)


class TestDroppedStateIsNotRead:
    """The snapshot drops ten things. Here is the proof each one is inert.

    Not a grep. A DIFFERENTIAL run: the state under test is destroyed on a live
    structure mid-stream, in exactly the way a restore would leave it, and the
    remaining candles must still produce the uninterrupted observations.
    """

    def test_clearing_the_nine_audit_trails_changes_nothing(self) -> None:
        cut = 700
        structure = LuxAlgoInternalStructure("SYN")
        _drive(structure, SYNTHETIC[:cut])
        populated: List[str] = []
        for name in sorted(LuxAlgoInternalStructure._SNAPSHOT_AUDIT):
            trail = getattr(structure, name)
            assert isinstance(trail, list)
            if trail:
                populated.append(name)
            # Destroy the real history AND leave something behind that explodes
            # if it is ever touched, so both directions are covered at once.
            setattr(structure, name, [_Poison()])
        assert tuple(populated) == POPULATED_AUDIT_AT_700, populated
        assert _drive(structure, SYNTHETIC[cut:]) == BASELINE[cut:]

    def test_shortening_the_creation_audit_list_changes_nothing(self) -> None:
        """`all_order_blocks` is rebuilt from the live store, not carried.

        This reproduces exactly what `_restore_order_blocks` leaves behind --
        the live blocks, oldest first, and nothing else -- and shows the next
        1,000 bars are unaffected, including the `created` channel that is
        computed as a tail slice of this very list.
        """
        cut = 500
        structure = LuxAlgoInternalStructure("SYN")
        _drive(structure, SYNTHETIC[:cut])
        assert len(structure.all_order_blocks) > len(structure.live_order_blocks)
        structure.all_order_blocks = list(reversed(structure.live_order_blocks))
        assert _drive(structure, SYNTHETIC[cut:]) == BASELINE[cut:]

    def test_the_created_list_is_correct_despite_the_shortened_audit_list(
            self) -> None:
        """The tail-slice argument, isolated: every created block is returned."""
        cut = 600
        structure = LuxAlgoInternalStructure("SYN")
        _drive(structure, SYNTHETIC[:cut])
        restored = LuxAlgoInternalStructure.restore_state(
            structure.capture_state())
        seqs: List[int] = []
        for row in SYNTHETIC[cut:]:
            seqs.extend(b.seq for b in restored.step(*row))
        want = [b.seq for b in _BASELINE_STRUCTURE.all_order_blocks
                if b.seq >= structure._ob_seq]
        assert seqs == want
        assert seqs, "no block was created after the cut; the slice is untested"

def _blob() -> Dict[str, Any]:
    """A known-good snapshot with a large pruned prefix, deep-copied."""
    return copy.deepcopy(_GOOD_BLOB)


def _mutate(**changes: Any) -> Dict[str, Any]:
    blob = _blob()
    blob.update(changes)
    return blob


def _no_key(key: str) -> Dict[str, Any]:
    blob = _blob()
    del blob[key]
    return blob


def _drop_pivot_level() -> Dict[str, Any]:
    blob = _blob()
    blob["pivots"]["internal_high"]["currentLevel"] = None
    return blob


def _flip_volatility() -> Dict[str, Any]:
    blob = _blob()
    flags = blob["arrays"]["high_volatility"]
    flags[3] = not flags[3]
    return blob


def _swap_parsed() -> Dict[str, Any]:
    blob = _blob()
    arrays = blob["arrays"]
    arrays["parsed_highs"][7], arrays["parsed_lows"][7] = (
        arrays["parsed_lows"][7], arrays["parsed_highs"][7])
    return blob


def _shift_atr_column() -> Dict[str, Any]:
    """Claim the window is at the start of the stream when it is not."""
    blob = _blob()
    blob["arrays"]["atr_values"][0] = None
    return blob


def _pivot_out_of_window() -> Dict[str, Any]:
    blob = _blob()
    blob["pivots"]["internal_low"]["barIndex"] = len(
        blob["arrays"]["times"])
    return blob


def _mitigated_live_block() -> Dict[str, Any]:
    blob = _blob()
    blob["live_order_blocks"][0]["mitigated_bar_idx"] = 999999
    return blob


def _ascending_seqs() -> Dict[str, Any]:
    blob = _blob()
    blocks = blob["live_order_blocks"]
    blocks[0]["seq"], blocks[1]["seq"] = blocks[1]["seq"], blocks[0]["seq"]
    return blob


def _seq_beyond_counter() -> Dict[str, Any]:
    blob = _blob()
    blob["live_order_blocks"][0]["seq"] = blob["ob_seq"]
    return blob


def _foreign_block() -> Dict[str, Any]:
    blob = _blob()
    blob["live_order_blocks"][0]["asset"] = "OTHER"
    return blob


def _over_the_cap() -> Dict[str, Any]:
    blob = _blob()
    blocks = blob["live_order_blocks"]
    top = max(b["seq"] for b in blocks) + 1
    extra = copy.deepcopy(blocks[0])
    while len(blocks) <= ALGO_OB_CAP:
        extra = copy.deepcopy(extra)
        extra["seq"] = top
        top += 1
        blocks.insert(0, extra)
    blob["ob_seq"] = top
    return blob


def _nan_high() -> Dict[str, Any]:
    blob = _blob()
    blob["arrays"]["highs"][2] = float("nan")
    return blob


def _short_array() -> Dict[str, Any]:
    blob = _blob()
    blob["arrays"]["closes"] = blob["arrays"]["closes"][:-1]
    return blob

_REFUSALS: Tuple[Tuple[str, Any, str], ...] = (
    ("missing_key", lambda: _no_key("ob_seq"), "missing"),
    ("unknown_key", lambda: _mutate(surprise=1), "unexpected ['surprise']"),
    ("short_array", _short_array, "disagree about how many"),
    ("negative_offset", lambda: _mutate(stream_offset=-1), "stream_offset"),
    ("bool_offset", lambda: _mutate(stream_offset=True), "stream_offset"),
    ("offset_vs_atr_count", lambda: _mutate(stream_offset=_GOOD_BLOB[
        "stream_offset"] + 1), "the ATR has seen"),
    ("base_last_mismatch", lambda: _mutate(last_bar_idx=_GOOD_BLOB[
        "last_bar_idx"] + 1), "last_bar_idx"),
    ("atr_column_shifted", _shift_atr_column, "warm-up"),
    ("volatility_flag_flipped", _flip_volatility, "high_volatility"),
    ("parsed_swap_reversed", _swap_parsed, "parsed_high/parsed_low"),
    ("nan_price", _nan_high, "finite"),
    ("pivot_out_of_window", _pivot_out_of_window, "barIndex"),
    ("pivot_level_missing", _drop_pivot_level, "barIndex"),
    ("live_block_already_mitigated", _mitigated_live_block, "mitigated"),
    ("seqs_not_descending", _ascending_seqs, "not older than"),
    ("seq_beyond_counter", _seq_beyond_counter, "creation counter"),
    ("block_from_another_asset", _foreign_block, "OTHER"),
    ("over_the_cap", _over_the_cap, str(ALGO_OB_CAP)),
)


class TestRestoreRefusesAnythingItCannotHaveProduced:
    """Fail-closed, field by field (safety rule #15).

    A snapshot arrives from a file that may have been edited. Every cross-field
    relationship the capture side guarantees is re-derived here, and a payload
    that breaks one is REFUSED rather than repaired into something plausible --
    the alternative is an engine that resumes with a trade zone on the wrong
    candle and no symptom at all.
    """

    @pytest.mark.parametrize("name,make,fragment", _REFUSALS,
                             ids=[r[0] for r in _REFUSALS])
    def test_a_broken_snapshot_raises(self, name: str, make: Any,
                                     fragment: str) -> None:
        with pytest.raises(LuxAlgoStructureError) as excinfo:
            LuxAlgoInternalStructure.restore_state(make())
        assert fragment in str(excinfo.value), (name, str(excinfo.value))

    def test_the_unmutated_blob_restores(self) -> None:
        """The control. Without it every refusal above could be a typo."""
        restored = LuxAlgoInternalStructure.restore_state(_blob())
        assert restored.capture_state() == _GOOD_BLOB

    def test_an_empty_snapshot_cannot_carry_a_stream_position(self) -> None:
        empty = LuxAlgoInternalStructure("SYN").capture_state()
        assert empty["arrays"]["times"] == []
        assert LuxAlgoInternalStructure.restore_state(empty).capture_state() \
            == empty
        for key, value in (("base_bar_idx", 0), ("last_bar_idx", 0),
                           ("stream_offset", 1)):
            broken = copy.deepcopy(empty)
            broken[key] = value
            with pytest.raises(LuxAlgoStructureError):
                LuxAlgoInternalStructure.restore_state(broken)

    def test_a_pruned_window_shorter_than_the_swings_floor_is_refused(
            self) -> None:
        blob = _blob()
        keep = SWINGS_LENGTH - 1
        drop = len(blob["arrays"]["times"]) - keep
        assert drop > 0
        for name in LuxAlgoInternalStructure._SNAPSHOT_ARRAYS:
            blob["arrays"][name] = blob["arrays"][name][drop:]
        blob["stream_offset"] += drop
        blob["base_bar_idx"] += drop
        for pivot in blob["pivots"].values():
            if pivot["barIndex"] is not None:
                pivot["barIndex"] = max(0, pivot["barIndex"] - drop)
        with pytest.raises(LuxAlgoStructureError, match="SWINGS_LENGTH"):
            LuxAlgoInternalStructure.restore_state(blob)

class TestTheSnapshotContractIsMechanical:
    """The guard that makes the codec un-rottable.

    A field-by-field codec has one failure mode a full replay does not: someone
    adds an attribute to `__init__` and the codec silently drops it. The four
    class-level sets are checked against `vars(self)` at BOTH capture and
    restore, so that mistake raises instead of shipping.
    """

    def test_every_instance_attribute_is_classified(self) -> None:
        cls = LuxAlgoInternalStructure
        declared = (cls._SNAPSHOT_SCALARS | set(cls._SNAPSHOT_ARRAYS)
                    | cls._SNAPSHOT_DERIVED | cls._SNAPSHOT_AUDIT)
        assert set(vars(LuxAlgoInternalStructure("SYN"))) == declared
        assert set(vars(_BASELINE_STRUCTURE)) == declared
        # The four sets partition; nothing is classified twice.
        assert len(declared) == (len(cls._SNAPSHOT_SCALARS)
                                 + len(cls._SNAPSHOT_ARRAYS)
                                 + len(cls._SNAPSHOT_DERIVED)
                                 + len(cls._SNAPSHOT_AUDIT))

    def test_an_unclassified_attribute_raises_at_capture(self) -> None:
        structure = LuxAlgoInternalStructure("SYN")
        structure.step(*SYNTHETIC[0])
        structure._brand_new_thing = 1                 # type: ignore[attr-defined]
        with pytest.raises(LuxAlgoStructureError, match="_brand_new_thing"):
            structure.capture_state()

    def test_a_vanished_attribute_raises_at_capture(self) -> None:
        structure = LuxAlgoInternalStructure("SYN")
        structure.step(*SYNTHETIC[0])
        del structure.internal_events
        with pytest.raises(LuxAlgoStructureError, match="internal_events"):
            structure.capture_state()

    def test_the_snapshot_key_list_matches_what_capture_writes(self) -> None:
        assert tuple(_GOOD_BLOB) == LUXALGO_SNAPSHOT_KEYS
        assert len(set(LUXALGO_SNAPSHOT_KEYS)) == len(LUXALGO_SNAPSHOT_KEYS)

    def test_the_snapshot_holds_no_live_python_objects(self) -> None:
        """Everything is a primitive, a datetime, a list or a dict.

        Asserted by the portability encoder, which raises on anything else, so a
        future field holding a `LuxAlgoPivot` or a numpy scalar fails here rather
        than at the first attempt to write a state file.
        """
        assert _json_round_trip(_GOOD_BLOB) == _GOOD_BLOB

    def test_the_restored_object_shares_nothing_with_the_blob(self) -> None:
        blob = _blob()
        restored = LuxAlgoInternalStructure.restore_state(blob)
        for name in LuxAlgoInternalStructure._SNAPSHOT_ARRAYS:
            assert getattr(restored, name) is not blob["arrays"][name]
        restored.times.append(None)
        assert len(blob["arrays"]["times"]) != len(restored.times)


class TestRestoredIdentityIsPreserved:
    """Direction, zone, origin, tag and detector identity all survive a restore.

    PART 1 of the hardening task persists the BOS/CHoCH label through
    `state.py`; this is the structure-level half -- the label is a real field on
    `LuxAlgoOrderBlock` and is carried verbatim, along with everything the
    trading lifecycle reads off a block.
    """

    def test_every_live_block_survives_field_for_field(self) -> None:
        restored = LuxAlgoInternalStructure.restore_state(_blob())
        before = [_block_view(b) for b in _BASELINE_STRUCTURE.live_order_blocks]
        after = [_block_view(b) for b in restored.live_order_blocks]
        assert after == before
        assert before, "no live blocks; the field-for-field claim is untested"

    def test_both_tags_are_present_and_exact(self) -> None:
        restored = LuxAlgoInternalStructure.restore_state(_blob())
        tags = [b.tag for b in restored.live_order_blocks]
        assert set(tags) <= {LS.TAG_BOS, LS.TAG_CHOCH}
        assert LS.TAG_BOS in tags and LS.TAG_CHOCH in tags, tags
        assert (LS.TAG_BOS, LS.TAG_CHOCH) == ("BOS", "CHoCH")

    def test_the_live_store_order_is_newest_first(self) -> None:
        restored = LuxAlgoInternalStructure.restore_state(_blob())
        seqs = [b.seq for b in restored.live_order_blocks]
        assert seqs == sorted(seqs, reverse=True)
        assert restored.all_order_blocks == list(
            reversed(restored.live_order_blocks))
        assert restored.display_slice() == restored.live_order_blocks[
            :LS.INTERNAL_OB_DISPLAY]

@needs_real
class TestContinuationParityOnRealHistory:
    """The same proof on 19,762 real 1h candles per symbol.

    Synthetic bars can only exercise the code paths I thought to generate. Real
    Delta Exchange India history is what the production engine actually replays,
    it is the sequence the 1.9 MB measurement came from, and it is where the
    worst-case retention numbers in the report were measured.
    """

    @pytest.mark.parametrize("cut", REAL_CUTS)
    def test_btcusd_restores_and_continues_exactly(self, cut: int) -> None:
        rows = _real_rows("BTCUSD")
        baseline, final = _real_baseline("BTCUSD")
        result = _continuation_parity("BTCUSD", rows, cut, baseline, final)
        assert result.stream_offset + result.retained == cut
        assert result.final_blob_matches
        # The point of the exercise: the window is a small fraction of the cut.
        assert result.retained <= 1024, result.retained

    @pytest.mark.parametrize("symbol", REAL_SYMBOLS)
    def test_every_symbol_restores_at_its_own_worst_bar(self,
                                                       symbol: str) -> None:
        """Cut where the causal window is WIDEST, not at a round number.

        A cut chosen for convenience could miss the one shape that matters: an
        ancient uncrossed internal pivot holding the watermark far back. This
        finds that bar by measurement and cuts exactly there.
        """
        rows = _real_rows(symbol)
        baseline, final = _real_baseline(symbol)
        probe = LuxAlgoInternalStructure(symbol)
        worst_bar, worst = 0, 0
        for row in rows:
            probe.step(*row)
            width = len(probe.times) - probe.causal_watermark()
            if width > worst:
                worst_bar, worst = len(probe.times), width
        assert worst == REAL_WORST[symbol], (symbol, worst_bar, worst)
        result = _continuation_parity(symbol, rows, worst_bar, baseline, final)
        assert result.retained == worst
        assert result.final_blob_matches

    def test_the_bound_is_a_large_reduction_on_real_data(self) -> None:
        """The size claim, measured on the full four-symbol history."""
        for symbol in REAL_SYMBOLS:
            structure = LuxAlgoInternalStructure(symbol)
            for row in _real_rows(symbol):
                structure.step(*row)
            blob = structure.capture_state()
            retained = len(blob["arrays"]["times"])
            assert blob["stream_offset"] + retained == REAL_BARS
            assert retained < REAL_BARS // 20, (symbol, retained)
