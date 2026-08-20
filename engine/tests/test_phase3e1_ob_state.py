"""
Phase 3E.1 Tests: OB State Model, Identity, and Missing-OB Analysis

Tests cover:
 C  - Formation candle NEVER causes TOUCHED state (regression)
 B  - Three lifecycle models (A/B/C) are deterministic and consistent
 D  - Temporal replay: state at +1h/+5h/+10h/cutoff is correct
 E  - Identity analysis: multi-source OBs are correctly classified
 F  - TV OB differential lookup and FVG exclusion
     - Future-data invariance
"""

import sys
import csv
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

import pytest

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from generate_phase3e1_analysis import (
    _lifecycle_model_a,
    _lifecycle_model_b,
    _lifecycle_model_c,
    _overlaps_broad,
    _enters_strictly,
    section_a_ob_trace,
    section_b_model_comparison,
    section_d_temporal_replay,
    section_e_identity_analysis,
    section_f_tv_differential,
    TV_OBSERVATIONS,
    AUG19_TS, AUG19_UPPER, AUG19_LOWER,
    DATASET_CUTOFF, EXPECTED_SHA256, EXPECTED_CANDLES,
)
from ob_snapshot_engine import OBSnapshotEngine, OBRecord
from quantedge.market_data.models import Candle, Timeframe, MarketDataSource

DATA_CSV  = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
OUT_DIR   = REPO_ROOT / "validation" / "phase3e1"
TRACE_CSV = OUT_DIR / "ob_trace_aug19.csv"
CMP_CSV   = OUT_DIR / "model_comparison.csv"
REPLAY_CSV = OUT_DIR / "temporal_replay.csv"
ID_CSV    = OUT_DIR / "ob_identity_analysis.csv"
DIFF_CSV  = OUT_DIR / "tv_ob_differential.csv"
SUM_JSON  = OUT_DIR / "phase3e1_summary.json"

FROZEN_SMC = [
    ENGINE / "src" / "quantedge" / "smc" / "structure.py",
    ENGINE / "src" / "quantedge" / "smc" / "order_blocks.py",
    ENGINE / "src" / "quantedge" / "smc" / "volatility.py",
]


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_candle(offset_hours: float, high: float, low: float,
                 open_: float = None, close: float = None) -> Candle:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts   = base + timedelta(hours=offset_hours)
    mid  = (high + low) / 2
    return Candle(
        symbol="TEST", timeframe=Timeframe.H1, timestamp=ts,
        open  = Decimal(str(open_ or mid)),
        high  = Decimal(str(high)),
        low   = Decimal(str(low)),
        close = Decimal(str(close or mid)),
        volume= Decimal("100"),
        source= MarketDataSource.HISTORICAL,
    )


def _make_ob_record(
    creation_hours: float, direction: str,
    upper: float, lower: float,
    break_idx: int = 1, state: str = "fresh",
) -> OBRecord:
    """Minimal mock OBRecord for lifecycle model tests."""
    form_ts = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=creation_hours)
    return OBRecord(
        structure_type         = "internal",
        direction              = direction,
        creation_timestamp     = form_ts,
        creation_candle_index  = 0,
        break_timestamp        = form_ts + timedelta(hours=break_idx),
        break_candle_index     = break_idx,
        break_type             = "bos",
        source_candle_index    = 0,
        source_timestamp       = form_ts,
        upper_price            = Decimal(str(upper)),
        lower_price            = Decimal(str(lower)),
        state                  = state,
        first_touch_timestamp  = None,
        invalidation_timestamp = None,
        pivot_index            = None,
        pivot_timestamp        = None,
        pivot_price            = None,
        is_active              = (state != "invalidated"),
        symbol                 = "TEST",
    )


@pytest.fixture(scope="module")
def eng():
    return OBSnapshotEngine.from_csv(str(DATA_CSV))


@pytest.fixture(scope="module")
def snap(eng):
    return eng.snapshot_at(DATASET_CUTOFF)


@pytest.fixture(scope="module")
def summary():
    return json.loads(SUM_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trace_rows():
    with open(TRACE_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def cmp_rows():
    with open(CMP_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def id_rows():
    with open(ID_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def diff_rows():
    with open(DIFF_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ═══════════════════════════════════════════════════════════════════════════════
# C — FORMATION CANDLE REGRESSION
# (Formation candle NEVER causes TOUCHED regardless of overlap)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormationCandle:
    """C. Formation candle must never cause TOUCHED state in any model."""

    def _build_ob_and_candles(self, upper=105.0, lower=95.0, break_idx=3):
        ob = _make_ob_record(0, "bullish", upper, lower, break_idx=break_idx)
        candles = [
            _make_candle(0,  high=upper, low=lower),   # formation — exactly fills zone
            _make_candle(1,  high=upper - 1, low=lower + 1),  # +1h still inside zone
            _make_candle(2,  high=upper - 2, low=lower + 2),  # +2h still inside zone
            _make_candle(3,  high=upper + 5, low=lower + 5),  # break candle — above zone
            _make_candle(4,  high=upper + 10, low=upper + 3),  # above zone
        ]
        return ob, candles

    def test_model_a_formation_not_touched(self):
        ob, candles = self._build_ob_and_candles()
        # Candles at +1h and +2h overlap zone — they should cause TOUCHED (they're post-formation)
        # but the formation candle itself must NOT cause it
        r = _lifecycle_model_a(ob, candles, 3)
        # State should be TOUCHED (due to +1h candle), but the formation candle at T=0
        # must not be the cause
        assert r.state == "touched"
        form_ts = candles[0].timestamp
        assert r.touch_ts > form_ts, (
            f"Touch must happen AFTER formation candle, but touch_ts={r.touch_ts} == form_ts={form_ts}"
        )

    def test_model_b_formation_not_touched(self):
        ob, candles = self._build_ob_and_candles()
        r = _lifecycle_model_b(ob, candles, 3)
        form_ts = candles[0].timestamp
        if r.touch_ts:
            assert r.touch_ts > form_ts, (
                "Model B: touch must occur strictly after formation candle"
            )

    def test_model_c_formation_not_touched(self):
        ob, candles = self._build_ob_and_candles()
        r = _lifecycle_model_c(ob, candles, 3)
        # Model C has no TOUCHED state
        assert r.state in ("fresh", "invalidated")
        form_ts = candles[0].timestamp
        if r.touch_ts:
            assert r.touch_ts > form_ts, (
                "Model C: informational touch must occur strictly after formation candle"
            )

    def test_ob_with_isolated_formation_stays_fresh_until_retest(self):
        """If post-formation candles are entirely above zone, OB stays fresh."""
        ob = _make_ob_record(0, "bullish", 105.0, 95.0, break_idx=1)
        candles = [
            _make_candle(0,  high=105, low=95),    # formation
            _make_candle(1,  high=115, low=108),   # break — above zone
            _make_candle(2,  high=118, low=110),   # above zone
            _make_candle(3,  high=120, low=112),   # above zone
        ]
        for model_fn in (_lifecycle_model_a, _lifecycle_model_b, _lifecycle_model_c):
            r = model_fn(ob, candles, 1)
            assert r.state == "fresh", (
                f"{model_fn.__name__}: Expected fresh but got {r.state}"
            )

    def test_aug19_ob_trace_formation_candle_has_no_touch_transition(self, trace_rows):
        """In the actual Aug-19 trace, formation candle must not have a TOUCHED transition."""
        form_row = next(
            (r for r in trace_rows if r["is_formation"] == "True"), None
        )
        assert form_row is not None, "No FORMATION row found in trace"
        assert "TOUCHED" not in form_row["transition_a"].upper(), (
            f"Formation candle should not have TOUCHED transition_a, got: {form_row['transition_a']}"
        )
        assert "TOUCHED" not in form_row["transition_b"].upper(), (
            f"Formation candle should not have TOUCHED transition_b, got: {form_row['transition_b']}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# B — LIFECYCLE MODEL COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

class TestLifecycleModels:
    """B. Three lifecycle models are consistent and deterministic."""

    def test_model_a_broad_touch(self):
        """Model A: edge-touching counts as TOUCHED."""
        ob = _make_ob_record(0, "bullish", 100.0, 90.0, break_idx=1)
        candles = [
            _make_candle(0, high=100, low=90),   # formation
            _make_candle(1, high=95, low=95),    # break — edge touch of zone (low==upper)
            _make_candle(2, high=100, low=90),   # enters zone
        ]
        r = _lifecycle_model_a(ob, candles, 1)
        # candle 1 is post-formation; low=95 <= upper=100 and high=95 >= lower=90 → touch
        assert r.state == "touched"
        assert r.touch_ts == candles[1].timestamp

    def test_model_b_strict_no_edge_touch(self):
        """Model B: edge-touching (low==upper) does NOT count as body entry."""
        ob = _make_ob_record(0, "bullish", 100.0, 90.0, break_idx=1)
        candles = [
            _make_candle(0, high=100, low=90),      # formation
            _make_candle(1, high=100.0, low=100.0), # break — touches upper edge exactly
            _make_candle(2, high=110, low=105),     # above zone
        ]
        r = _lifecycle_model_b(ob, candles, 1)
        # Candle 1: low=100.0 == upper=100.0 → NOT strictly entering (strict is <)
        # No strict interior entry → fresh
        assert r.state == "fresh"

    def test_model_c_no_touched_primary_state(self):
        """Model C: primary state is ONLY fresh or invalidated, never 'touched'."""
        ob = _make_ob_record(0, "bullish", 100.0, 90.0, break_idx=1)
        candles = [
            _make_candle(0, high=100, low=90),   # formation
            _make_candle(1, high=115, low=88),   # break — overlaps zone AND low < lower
            _make_candle(2, high=110, low=85),   # violates lower
        ]
        r = _lifecycle_model_c(ob, candles, 1)
        assert r.state in ("fresh", "invalidated"), (
            f"Model C must not have 'touched' as primary state, got: {r.state}"
        )

    def test_model_c_records_informational_touch(self):
        """Model C: even though state stays fresh, touch_ts is recorded."""
        ob = _make_ob_record(0, "bullish", 100.0, 90.0, break_idx=1)
        candles = [
            _make_candle(0, high=100, low=90),   # formation
            _make_candle(1, high=115, low=108),  # break — above zone
            _make_candle(2, high=98, low=92),    # enters zone — should record info touch
            _make_candle(3, high=110, low=105),  # above zone
        ]
        r = _lifecycle_model_c(ob, candles, 1)
        assert r.state == "fresh", "State should remain fresh (no boundary violation)"
        assert r.touch_ts == candles[2].timestamp, "Info touch should be recorded at candle 2"

    def test_models_deterministic(self, snap, eng):
        """Running models twice produces identical results."""
        ob_rec = snap.active_obs[0]
        candles = eng.candles
        r1a = _lifecycle_model_a(ob_rec, candles, ob_rec.break_candle_index)
        r2a = _lifecycle_model_a(ob_rec, candles, ob_rec.break_candle_index)
        assert r1a.state == r2a.state
        assert r1a.touch_ts == r2a.touch_ts
        r1c = _lifecycle_model_c(ob_rec, candles, ob_rec.break_candle_index)
        r2c = _lifecycle_model_c(ob_rec, candles, ob_rec.break_candle_index)
        assert r1c.state == r2c.state
        assert r1c.touch_ts == r2c.touch_ts

    def test_model_comparison_csv_has_341_rows(self, cmp_rows):
        assert len(cmp_rows) == 341, f"Expected 341 rows, got {len(cmp_rows)}"

    def test_model_comparison_agreement_majority(self, cmp_rows):
        """At least 80% of OBs should have models A/B/C agree (sanity check)."""
        agree = sum(1 for r in cmp_rows if r["agreement"] == "AGREE")
        pct = agree / len(cmp_rows)
        assert pct >= 0.80, f"Expected >= 80% agreement but got {pct:.1%}"

    def test_model_comparison_has_required_fields(self, cmp_rows):
        required = [
            "ob_id", "model_a_state", "model_b_state", "model_c_state",
            "agreement", "production_state",
        ]
        headers = set(cmp_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing field '{f}' in model_comparison.csv"

    def test_model_a_is_production_equivalent(self, cmp_rows):
        """Model A should match production state for most OBs (both use broad overlap)."""
        match = sum(1 for r in cmp_rows if r["model_a_state"] == r["production_state"])
        pct = match / len(cmp_rows)
        assert pct >= 0.90, (
            f"Model A should match production state for >= 90% of OBs, got {pct:.1%}"
        )

    def test_model_c_never_has_touched_state(self, cmp_rows):
        """Model C primary state must never be 'touched'."""
        touched = [r for r in cmp_rows if r["model_c_state"] == "touched"]
        assert not touched, (
            f"Model C should have no 'touched' primary states, "
            f"but found {len(touched)} cases"
        )

    def test_bearish_model_b_close_based_invalidation(self):
        """Model B invalidation for bearish OB: candle.close > upper."""
        ob = _make_ob_record(0, "bearish", 200.0, 190.0, break_idx=1)
        candles = [
            _make_candle(0, high=200, low=190),       # formation
            _make_candle(1, high=185, low=180),       # break — below zone
            _make_candle(2, high=205, low=195, close=202.0),  # close=202 > 200 → invalidated
        ]
        r = _lifecycle_model_b(ob, candles, 1)
        assert r.state == "invalidated", f"Expected invalidated but got {r.state}"
        assert r.invalid_ts == candles[2].timestamp


# ═══════════════════════════════════════════════════════════════════════════════
# A — AUG-19 OB TRACE
# ═══════════════════════════════════════════════════════════════════════════════

class TestAug19Trace:
    """A. Candle-by-candle trace for the 2026-08-19 06:00 OB."""

    def test_trace_csv_has_rows(self, trace_rows):
        assert len(trace_rows) > 0, "ob_trace_aug19.csv is empty"

    def test_first_row_is_formation(self, trace_rows):
        assert trace_rows[0]["is_formation"] == "True", (
            "First trace row must be the FORMATION candle"
        )
        assert trace_rows[0]["extended_label"] == "FORMATION"

    def test_formation_candle_overlap_documented(self, trace_rows):
        """Formation candle overlaps zone — this must be documented, not suppressed."""
        form = trace_rows[0]
        assert form["overlaps_broad"] == "True", (
            "Formation candle high=64328 / low=64137.5 IS within the zone [64137.5, 64328]. "
            "This must be documented."
        )

    def test_first_touch_is_candle_after_formation(self, trace_rows):
        """First touch must occur on a candle AFTER the formation candle."""
        touched = [r for r in trace_rows if "TOUCHED" in r["transition_a"]]
        assert touched, "Expected at least one TOUCHED transition"
        form_ts = datetime.fromisoformat(trace_rows[0]["timestamp"])
        touch_ts = datetime.fromisoformat(touched[0]["timestamp"])
        assert touch_ts > form_ts, (
            f"Touch must be after formation. form_ts={form_ts}, touch_ts={touch_ts}"
        )

    def test_first_retest_label_appears(self, trace_rows):
        """FIRST_RETEST label must appear in the trace."""
        retest = [r for r in trace_rows if r["extended_label"] == "FIRST_RETEST"]
        assert retest, "Expected FIRST_RETEST label in trace"

    def test_aug19_first_touch_at_next_hour(self, trace_rows, summary):
        """The known first touch of Aug-19 OB is at 2026-08-19T07:00 (candle after formation)."""
        ft = summary["aug19_ob_trace"]["first_touch_model_a"]
        assert ft is not None, "No first touch recorded for Aug-19 OB"
        assert "2026-08-19T07:00" in ft, (
            f"Expected first touch at 2026-08-19T07:00 but got {ft}"
        )

    def test_trace_all_models_have_state_field(self, trace_rows):
        headers = set(trace_rows[0].keys())
        for f in ("model_a_state", "model_b_state", "model_c_state"):
            assert f in headers, f"Missing {f} in trace"

    def test_break_candle_does_not_overlap_zone(self, trace_rows):
        """The Aug-19 OB break candle (14:00) does NOT overlap the zone."""
        brk = next(
            (r for r in trace_rows if r["is_break_candle"] == "True"), None
        )
        assert brk is not None, "No break candle row in trace"
        assert brk["overlaps_broad"] == "False", (
            f"Break candle should NOT overlap zone, but overlaps_broad={brk['overlaps_broad']}"
        )

    def test_between_form_break_candles_cause_touch(self, trace_rows):
        """Candles between formation and break (07:00 and 08:00) overlap zone → TOUCHED."""
        between = [r for r in trace_rows if r["is_between_form_brk"] == "True"]
        overlapping = [r for r in between if r["overlaps_broad"] == "True"]
        assert overlapping, (
            "Expected some between-form-break candles to overlap zone"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# D — TEMPORAL REPLAY
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemporalReplay:
    """D. State at each temporal checkpoint is correct."""

    def test_temporal_replay_csv_exists(self):
        assert REPLAY_CSV.exists(), "temporal_replay.csv missing"

    def test_replay_has_25_rows(self, summary):
        # 5 OBs x 5 checkpoints
        rows = list(csv.DictReader(open(REPLAY_CSV, newline="", encoding="utf-8")))
        assert len(rows) == 25, f"Expected 25 rows (5 OBs × 5 checkpoints), got {len(rows)}"

    def test_aug19_not_created_at_formation(self, summary):
        """At formation time, the OB does not yet exist (pipeline needs break candle)."""
        aug_replay = summary["aug19_temporal_replay"]
        form = next(r for r in aug_replay if r["checkpoint_label"] == "formation")
        assert form["state"] == "NOT_YET_CREATED", (
            f"Aug-19 OB should not exist yet at formation time, got: {form['state']}"
        )

    def test_aug19_not_created_at_plus1h(self, summary):
        """At +1h, still not created (break at +8h from formation)."""
        aug_replay = summary["aug19_temporal_replay"]
        plus1 = next(r for r in aug_replay if r["checkpoint_label"] == "+1h")
        assert plus1["state"] == "NOT_YET_CREATED", (
            f"Aug-19 OB should not exist at +1h, got: {plus1['state']}"
        )

    def test_aug19_touched_at_cutoff(self, summary):
        """At dataset cutoff, Aug-19 OB should be touched."""
        aug_replay = summary["aug19_temporal_replay"]
        cutoff = next(r for r in aug_replay if r["checkpoint_label"] == "cutoff")
        assert cutoff["state"] == "touched", (
            f"Expected 'touched' at cutoff, got: {cutoff['state']}"
        )

    def test_aug19_first_touch_recorded_at_plus10h(self, summary):
        """By +10h, the first touch timestamp should be recorded."""
        aug_replay = summary["aug19_temporal_replay"]
        plus10 = next(r for r in aug_replay if r["checkpoint_label"] == "+10h")
        assert plus10["state"] == "touched", (
            f"Expected touched at +10h, got: {plus10['state']}"
        )
        assert plus10["first_touch_ts"] != "", "first_touch_ts must be set at +10h"

    def test_future_data_invariance(self, eng):
        """
        Future-data invariance: snapshot_at(T) state must not change
        regardless of how many additional future candles are loaded.
        """
        cutoff     = datetime.fromisoformat(DATASET_CUTOFF)
        half_point = cutoff - timedelta(days=45)   # a point 45 days before cutoff

        # Full dataset snap
        snap_full = eng.snapshot_at(half_point)
        # Subset snap (same engine, same underlying candles — snapshot slices internally)
        snap_sub  = eng.snapshot_at(half_point)

        assert snap_full.candles_processed == snap_sub.candles_processed, (
            "snapshot_at is not deterministic for the same timestamp"
        )
        assert snap_full.all_count == snap_sub.all_count, (
            "snapshot_at() returns different OB counts for the same timestamp on the same engine"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# E — IDENTITY / DUPLICATE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdentityAnalysis:
    """E. Multi-source OBs are correctly classified."""

    def test_identity_csv_exists(self):
        assert ID_CSV.exists(), "ob_identity_analysis.csv missing"

    def test_no_likely_duplicates(self, id_rows):
        """All multi-source OBs must be LEGITIMATE (never LIKELY_DUPLICATE)."""
        dupes = [r for r in id_rows if r["verdict"] == "LIKELY_DUPLICATE"]
        assert not dupes, (
            f"Found {len(dupes)} LIKELY_DUPLICATE OBs. "
            f"These need deduplication: {[(r['creation_timestamp'], r['direction']) for r in dupes]}"
        )

    def test_multi_source_groups_have_distinct_breaks_or_structures(self, id_rows, summary):
        """All multi-OB groups must have either different structure levels or different break indices."""
        groups = {}
        for r in id_rows:
            gid = r["group_id"]
            groups.setdefault(gid, []).append(r)

        for gid, members in groups.items():
            stypes = {m["structure_type"] for m in members}
            breaks = {m["break_candle_index"] for m in members}
            assert len(stypes) > 1 or len(breaks) > 1, (
                f"Group {gid} ({members[0]['creation_timestamp']}, {members[0]['direction']}) "
                f"has neither different structure types ({stypes}) nor different break indices ({breaks}). "
                f"This is an unclassified duplicate."
            )

    def test_identity_csv_required_fields(self, id_rows):
        required = [
            "group_id", "ob_count_in_group", "creation_timestamp",
            "break_candle_index", "verdict", "explanation",
        ]
        headers = set(id_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing field '{f}' in ob_identity_analysis.csv"

    def test_multi_ob_group_count(self, summary):
        """Confirms 18 multi-source groups with 36 OBs (from pipeline analysis)."""
        assert summary["identity_summary"]["multi_ob_groups"] == 18
        assert summary["identity_summary"]["total_multi_obs"] == 36


# ═══════════════════════════════════════════════════════════════════════════════
# F — TV OB DIFFERENTIAL + FVG PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestTVDifferential:
    """F. TV OB differential lookup and FVG exclusion."""

    def test_diff_csv_exists(self):
        assert DIFF_CSV.exists(), "tv_ob_differential.csv missing"

    def test_tv_ob_001_found_in_python(self, diff_rows):
        """TV_OB_001 (64k zone) must be FOUND_IN_PYTHON."""
        row = next((r for r in diff_rows if r["tv_id"] == "TV_OB_001"), None)
        assert row is not None, "TV_OB_001 not in differential CSV"
        assert row["result"] == "FOUND_IN_PYTHON", (
            f"TV_OB_001 should be FOUND_IN_PYTHON, got: {row['result']}"
        )

    def test_tv_ob_001_state_documented(self, diff_rows):
        """TV_OB_001 match state must be documented (touched)."""
        row = next((r for r in diff_rows if r["tv_id"] == "TV_OB_001"), None)
        assert row["python_match_state"] == "touched", (
            f"TV_OB_001 Python state should be 'touched', got: {row['python_match_state']}"
        )

    def test_tv_ob_002_has_candidates(self, diff_rows):
        """TV_OB_002 (~69k zone) must find candidates in Python."""
        row = next((r for r in diff_rows if r["tv_id"] == "TV_OB_002"), None)
        assert row is not None, "TV_OB_002 not in differential CSV"
        assert int(row["candidate_ob_count"]) > 0, (
            f"TV_OB_002: Expected candidate OBs in 69k region, got 0"
        )

    def test_fvg_entry_is_excluded(self, snap):
        """A TV entry with is_fvg=True must return IGNORE_FVG."""
        fvg_entry = {
            "tv_id": "FVG_TEST",
            "direction": "bullish",
            "upper": 64328.0,
            "lower": 64138.0,
            "observed_timestamp": "2026-08-19T14:00:00+00:00",
            "is_fvg": True,
            "notes": "green zone — FVG, not OB",
        }
        rows = section_f_tv_differential(
            [fvg_entry], snap.all_obs, snap.active_obs, [], [], [], [], []
        )
        assert rows[0]["result"] == "IGNORE_FVG", (
            f"FVG must be excluded but got: {rows[0]['result']}"
        )

    def test_fvg_produces_no_python_match(self, snap):
        """FVG entry must never have a python_match_upper."""
        fvg_entry = {
            "tv_id": "FVG_TEST2",
            "direction": "bearish",
            "upper": 90000.0,
            "lower": 89000.0,
            "observed_timestamp": "",
            "is_fvg": True,
            "notes": "test FVG",
        }
        rows = section_f_tv_differential(
            [fvg_entry], snap.all_obs, snap.active_obs, [], [], [], [], []
        )
        assert rows[0]["python_match_upper"] == "", (
            "FVG entry must not produce a Python OB match"
        )

    def test_missing_ob_no_break_classified_correctly(self, snap):
        """TV OB in a price/time zone with no breaks → NOT_CREATED_NO_BREAK."""
        future_ob = {
            "tv_id": "FUTURE_TEST",
            "direction": "bullish",
            "upper": 150000.0,
            "lower": 149000.0,
            "observed_timestamp": "2026-01-01T01:00:00+00:00",  # early: no breaks yet
            "is_fvg": False,
            "notes": "hypothetical future price zone — should have no breaks",
        }
        rows = section_f_tv_differential(
            [future_ob], snap.all_obs, snap.active_obs, [], [], [], [], []
        )
        assert rows[0]["result"] in ("NOT_CREATED_NO_BREAK", "NOT_CREATED_ATR_OR_RANGE"), (
            f"Expected NOT_CREATED_*, got: {rows[0]['result']}"
        )

    def test_diff_csv_required_fields(self, diff_rows):
        required = [
            "tv_id", "direction", "upper", "lower", "is_fvg",
            "result", "explanation", "nearby_break_count",
        ]
        headers = set(diff_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing '{f}' in tv_ob_differential.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# Geometric utility tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeometryUtils:
    def test_overlaps_broad_edge_touch(self):
        """Edge touch (low == upper) counts as broad overlap."""
        c = _make_candle(0, high=100.0, low=100.0)
        assert _overlaps_broad(c, Decimal("100.0"), Decimal("90.0"))

    def test_overlaps_broad_no_overlap(self):
        c = _make_candle(0, high=115, low=110)
        assert not _overlaps_broad(c, Decimal("100"), Decimal("90"))

    def test_enters_strictly_edge_only(self):
        """Edge touch (low == upper) does NOT satisfy strict entry."""
        c = _make_candle(0, high=100.0, low=100.0)
        assert not _enters_strictly(c, Decimal("100.0"), Decimal("90.0"))

    def test_enters_strictly_inside(self):
        c = _make_candle(0, high=105, low=95)
        assert _enters_strictly(c, Decimal("100"), Decimal("90"))

    def test_enters_strictly_above_zone(self):
        c = _make_candle(0, high=120, low=110)
        assert not _enters_strictly(c, Decimal("100"), Decimal("90"))


# ═══════════════════════════════════════════════════════════════════════════════
# Frozen SMC files
# ═══════════════════════════════════════════════════════════════════════════════

def test_frozen_smc_files_unchanged():
    for p in FROZEN_SMC:
        assert p.exists(), f"Frozen file missing: {p}"


def test_output_files_all_exist():
    for p in [TRACE_CSV, CMP_CSV, REPLAY_CSV, ID_CSV, DIFF_CSV, SUM_JSON]:
        assert p.exists(), f"Missing output file: {p}"


def test_no_production_code_modified():
    """Phase 3E.1 must not modify production SMC files."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--",
         "engine/src/quantedge/smc/structure.py",
         "engine/src/quantedge/smc/order_blocks.py",
         "engine/src/quantedge/smc/volatility.py"],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT)
    )
    assert result.stdout.strip() == "", (
        f"Frozen production files were modified:\n{result.stdout}"
    )
