"""
Phase 3E.1: OB State and Missing-OB Root Cause Analysis

Sections:
    A  - Full candle-by-candle trace for the 2026-08-19 06:00 OB
    B  - Three lifecycle model comparison for all 341 OBs
    C  - Formation candle regression (tested in test file)
    D  - Temporal replay snapshots for key OBs
    E  - Duplicate / source-candle identity analysis
    F  - Missing blue OB diagnostic tool (with known TV observations)

DO NOT MODIFY:
    engine/src/quantedge/smc/structure.py
    engine/src/quantedge/smc/order_blocks.py
    engine/src/quantedge/smc/volatility.py

Output directory: validation/phase3e1/
"""

import sys
import csv
import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent
OUT_DIR   = REPO_ROOT / "validation" / "phase3e1"

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.models import (
    OrderBlock, PivotPoint, StructureBreak, TrendDirection, BreakType, StructureType
)
from ob_snapshot_engine import OBSnapshotEngine, OBRecord

DATA_CSV        = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
DATA_META       = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026_metadata.json"
DATASET_CUTOFF  = "2026-08-20T00:00:00+00:00"
EXPECTED_SHA256 = "2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b"
EXPECTED_CANDLES = 5545

# ── Key OB constants ──────────────────────────────────────────────────────────
AUG19_TS    = "2026-08-19T06:00:00+00:00"
AUG19_UPPER = Decimal("64328.0")
AUG19_LOWER = Decimal("64137.5")

# ── Known TV manual observations (from prior session screenshots) ──────────────
TV_OBSERVATIONS = [
    {
        "tv_id":              "TV_OB_001",
        "direction":          "bullish",
        "upper":              64328.0,
        "lower":              64138.0,
        "observed_timestamp": "2026-08-19T14:00:00+00:00",
        "is_fvg":             False,
        "notes":              (
            "Blue OB visible near 64k zone. Python shows TOUCHED. "
            "TradingView screenshot appears fresh/unretested."
        ),
        "structure_type":     "unknown",
    },
    {
        "tv_id":              "TV_OB_002",
        "direction":          "bullish",
        "upper":              None,        # unknown from screenshot
        "lower":              None,
        "observed_timestamp": "",
        "is_fvg":             False,
        "notes":              (
            "Blue bullish OB visible in ~69k zone in TradingView screenshots. "
            "Exact upper/lower not readable. Search range: 68000-71000."
        ),
        "structure_type":     "unknown",
        "_search_upper":      71000.0,
        "_search_lower":      68000.0,
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LifecycleResult:
    """Result from running a lifecycle model on one OB."""
    model:        str    # "A", "B", or "C"
    state:        str    # "fresh" | "touched" | "invalidated"
    touch_ts:     Optional[datetime]
    invalid_ts:   Optional[datetime]
    info:         str = ""  # extra notes


def _enters_strictly(c: Candle, upper: Decimal, lower: Decimal) -> bool:
    """
    Strict interior entry: candle's price range STRICTLY enters the zone.
    Excludes edge-touching (c.low == upper or c.high == lower).
    """
    return c.low < upper and c.high > lower


def _overlaps_broad(c: Candle, upper: Decimal, lower: Decimal) -> bool:
    """
    Broad overlap: candle range overlaps zone including edge contact.
    c.low <= upper AND c.high >= lower
    """
    return c.low <= upper and c.high >= lower


def _lifecycle_model_a(
    ob: OBRecord,
    candles: List[Candle],
    break_candle_index: int,
) -> LifecycleResult:
    """
    Model A — BROAD OVERLAP.
    Any later candle whose range overlaps the zone → TOUCHED.
    'Later' means after the formation candle timestamp (production semantics).
    Invalidation: bullish = c.low < lower; bearish = c.high > upper.
    """
    upper     = ob.upper_price
    lower     = ob.lower_price
    direction = ob.direction
    form_ts   = ob.creation_timestamp

    state     = "fresh"
    touch_ts  = None
    invalid_ts = None

    for c in candles:
        if c.timestamp <= form_ts:
            continue
        if state == "invalidated":
            break

        overlaps  = _overlaps_broad(c, upper, lower)
        if direction == "bullish":
            if overlaps and state == "fresh":
                state    = "touched"
                touch_ts = c.timestamp
            if c.low < lower:
                state      = "invalidated"
                invalid_ts = c.timestamp
        else:
            if overlaps and state == "fresh":
                state    = "touched"
                touch_ts = c.timestamp
            if c.high > upper:
                state      = "invalidated"
                invalid_ts = c.timestamp

    return LifecycleResult(model="A", state=state, touch_ts=touch_ts, invalid_ts=invalid_ts,
                           info="Broad overlap — any edge contact counts as touch")


def _lifecycle_model_b(
    ob: OBRecord,
    candles: List[Candle],
    break_candle_index: int,
) -> LifecycleResult:
    """
    Model B — STRICT BODY ENTRY + CLOSE-BASED INVALIDATION.
    Touch: candle's range strictly enters zone interior (LOW < upper AND HIGH > lower).
    Invalidation: bullish = c.close < lower; bearish = c.close > upper.
    'Later' means after formation candle.
    """
    upper     = ob.upper_price
    lower     = ob.lower_price
    direction = ob.direction
    form_ts   = ob.creation_timestamp

    state      = "fresh"
    touch_ts   = None
    invalid_ts = None

    for c in candles:
        if c.timestamp <= form_ts:
            continue
        if state == "invalidated":
            break

        enters   = _enters_strictly(c, upper, lower)
        if direction == "bullish":
            if enters and state == "fresh":
                state    = "touched"
                touch_ts = c.timestamp
            if c.close < lower:
                state      = "invalidated"
                invalid_ts = c.timestamp
        else:
            if enters and state == "fresh":
                state    = "touched"
                touch_ts = c.timestamp
            if c.close > upper:
                state      = "invalidated"
                invalid_ts = c.timestamp

    return LifecycleResult(model="B", state=state, touch_ts=touch_ts, invalid_ts=invalid_ts,
                           info="Strict body entry (LOW<upper AND HIGH>lower) + close-based invalidation")


def _lifecycle_model_c(
    ob: OBRecord,
    candles: List[Candle],
    break_candle_index: int,
) -> LifecycleResult:
    """
    Model C — LUXALGO PRIMARY SEMANTICS.
    Primary states: FRESH | INVALIDATED only.
    'Touch' is recorded informationally but does NOT change primary state.
    Invalidation: bullish = c.low < lower; bearish = c.high > upper.

    This represents LuxAlgo's visual behaviour: the blue OB box STAYS ACTIVE
    until the price fully violates the boundary. No intermediate 'touched' box
    colour is used — the box simply disappears on invalidation.
    """
    upper     = ob.upper_price
    lower     = ob.lower_price
    direction = ob.direction
    form_ts   = ob.creation_timestamp

    state      = "fresh"       # primary: fresh | invalidated
    info_touch = None          # informational only
    invalid_ts = None

    for c in candles:
        if c.timestamp <= form_ts:
            continue
        if state == "invalidated":
            break

        overlaps = _overlaps_broad(c, upper, lower)
        if info_touch is None and overlaps:
            info_touch = c.timestamp   # record informational touch

        if direction == "bullish":
            if c.low < lower:
                state      = "invalidated"
                invalid_ts = c.timestamp
        else:
            if c.high > upper:
                state      = "invalidated"
                invalid_ts = c.timestamp

    return LifecycleResult(
        model="C",
        state=state,
        touch_ts=info_touch,     # informational only — NOT a state transition
        invalid_ts=invalid_ts,
        info="LuxAlgo primary: FRESH/INVALIDATED only. Touch is informational.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION A — Candle-by-candle trace for the 2026-08-19 06:00 OB
# ═══════════════════════════════════════════════════════════════════════════════

#  Extended state names for the trace:
#    FORMATION         — the OB source candle itself
#    FRESH             — no zone entry yet
#    BETWEEN_FORM_BREAK — after formation but before (and including) break candle
#    FIRST_RETEST      — first candle where price genuinely enters the zone
#    TOUCHED           — state after first retest under Model A/B
#    MITIGATED         — fully consumed / invalidated
#    INVALIDATED       — same as MITIGATED (boundary violation)


def section_a_ob_trace(candles: List[Candle]) -> List[dict]:
    """
    Full candle-by-candle trace for the Aug-19 06:00 OB.
    Reports extended state labels for each candle.
    Returns list of row dicts.
    """
    upper     = AUG19_UPPER
    lower     = AUG19_LOWER
    form_ts   = datetime.fromisoformat(AUG19_TS)
    break_idx = 5534          # from pipeline analysis

    # State machines for all three models
    state_a = state_b = state_c = "fresh"
    touch_a = touch_b = None
    info_touch_c = None
    inv_a = inv_b = inv_c = None
    first_retest_seen = False

    rows = []

    for i, c in enumerate(candles):
        if c.timestamp < form_ts:
            continue

        is_formation = (c.timestamp == form_ts)
        is_break     = (i == break_idx)
        is_between   = (c.timestamp > form_ts and i < break_idx and not is_formation)
        is_post_break = (i > break_idx)

        # Geometric analysis
        overlaps_broad  = _overlaps_broad(c, upper, lower)
        enters_strictly = _enters_strictly(c, upper, lower)
        lower_violated  = c.low < lower     # bullish invalidation boundary
        close_below     = c.close < lower   # Model B close-based

        # Classify this candle's geometric relation to the zone
        if is_formation:
            zone_relation = "FORMATION"
        elif overlaps_broad and c.low <= upper and c.high >= lower:
            zone_relation = "OVERLAPS_ZONE"
        elif c.low > upper:
            zone_relation = "ABOVE_ZONE"
        elif c.high < lower:
            zone_relation = "BELOW_ZONE"
        else:
            zone_relation = "OUTSIDE_ZONE"

        # Run Model A (broad overlap, production)
        prev_a = state_a
        if not is_formation and state_a != "invalidated":
            if overlaps_broad and state_a == "fresh":
                state_a = "touched"
                touch_a = c.timestamp
            if lower_violated:
                state_a = "invalidated"
                inv_a   = c.timestamp

        # Run Model B (strict body, close-based)
        prev_b = state_b
        if not is_formation and state_b != "invalidated":
            if enters_strictly and state_b == "fresh":
                state_b = "touched"
                touch_b = c.timestamp
            if close_below:
                state_b = "invalidated"
                inv_b   = c.timestamp

        # Run Model C (primary = fresh/invalidated, touch informational)
        prev_c = state_c
        if not is_formation and state_c != "invalidated":
            if info_touch_c is None and overlaps_broad:
                info_touch_c = c.timestamp
            if lower_violated:
                state_c = "invalidated"
                inv_c   = c.timestamp

        # Extended label for this candle
        if is_formation:
            ext_label = "FORMATION"
        elif is_break:
            ext_label = "BREAK_CANDLE"
        elif is_between:
            # between formation and break — key diagnostic zone
            if overlaps_broad:
                if not first_retest_seen:
                    first_retest_seen = True
                    ext_label = "FIRST_RETEST"
                else:
                    ext_label = "SUBSEQUENT_RETEST"
            else:
                ext_label = "BETWEEN_FORM_BREAK"
        elif is_post_break:
            if state_a == "invalidated" and prev_a != "invalidated":
                ext_label = "MITIGATED"
            elif overlaps_broad and prev_a == "fresh":
                ext_label = "FIRST_RETEST_POST_BREAK"
            else:
                ext_label = "POST_BREAK"
        else:
            ext_label = "?"

        # Transition descriptions
        t_a = "" if state_a == prev_a else f"{prev_a.upper()}→{state_a.upper()}"
        t_b = "" if state_b == prev_b else f"{prev_b.upper()}→{state_b.upper()}"
        t_c = "" if state_c == prev_c else f"{prev_c.upper()}→{state_c.upper()}"

        rows.append({
            "candle_index":        i,
            "timestamp":           c.timestamp.isoformat(),
            "open":                float(c.open),
            "high":                float(c.high),
            "low":                 float(c.low),
            "close":               float(c.close),
            "zone_relation":       zone_relation,
            "overlaps_broad":      overlaps_broad,
            "enters_strictly":     enters_strictly,
            "lower_boundary_viol": lower_violated,
            "close_below_lower":   close_below,
            "is_formation":        is_formation,
            "is_break_candle":     is_break,
            "is_between_form_brk": is_between,
            "is_post_break":       is_post_break,
            "extended_label":      ext_label,
            "model_a_state":       state_a,
            "model_b_state":       state_b,
            "model_c_state":       state_c,
            "transition_a":        t_a,
            "transition_b":        t_b,
            "transition_c":        t_c,
        })

    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION B — Three-model comparison for all 341 OBs
# ═══════════════════════════════════════════════════════════════════════════════

def section_b_model_comparison(
    snap_all_obs: List[OBRecord],
    candles: List[Candle],
    break_map: Dict[int, Any],
) -> List[dict]:
    """
    Run three lifecycle models on every OB.
    Returns list of comparison rows (one per OB).
    """
    rows = []
    for i, ob in enumerate(sorted(snap_all_obs, key=lambda x: x.creation_timestamp)):
        brk_idx = ob.break_candle_index
        r_a = _lifecycle_model_a(ob, candles, brk_idx)
        r_b = _lifecycle_model_b(ob, candles, brk_idx)
        r_c = _lifecycle_model_c(ob, candles, brk_idx)

        # Compute agreement
        states = (r_a.state, r_b.state, r_c.state)
        if len(set(states)) == 1:
            agreement = "AGREE"
        elif r_a.state == r_b.state:
            agreement = "C_DIFFERS"
        elif r_a.state == r_c.state:
            agreement = "B_DIFFERS"
        elif r_b.state == r_c.state:
            agreement = "A_DIFFERS"
        else:
            agreement = "ALL_DIFFER"

        rows.append({
            "ob_id":              i + 1,
            "structure_type":     ob.structure_type,
            "direction":          ob.direction,
            "upper_price":        float(ob.upper_price),
            "lower_price":        float(ob.lower_price),
            "creation_timestamp": ob.creation_timestamp.isoformat(),
            "break_candle_index": brk_idx,
            "break_type":         ob.break_type,
            "production_state":   ob.state,
            "model_a_state":      r_a.state,
            "model_a_touch_ts":   r_a.touch_ts.isoformat() if r_a.touch_ts else "",
            "model_a_invalid_ts": r_a.invalid_ts.isoformat() if r_a.invalid_ts else "",
            "model_b_state":      r_b.state,
            "model_b_touch_ts":   r_b.touch_ts.isoformat() if r_b.touch_ts else "",
            "model_b_invalid_ts": r_b.invalid_ts.isoformat() if r_b.invalid_ts else "",
            "model_c_state":      r_c.state,
            "model_c_info_touch_ts": r_c.touch_ts.isoformat() if r_c.touch_ts else "",
            "model_c_invalid_ts": r_c.invalid_ts.isoformat() if r_c.invalid_ts else "",
            "agreement":          agreement,
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION D — Temporal replay
# ═══════════════════════════════════════════════════════════════════════════════

_TEMPORAL_KEY_OBS = [
    # (label, creation_ts_iso, direction, upper, lower)
    ("Aug19_BULL", "2026-08-19T06:00:00+00:00", "bullish", 64328.0, 64137.5),
    ("Aug18_BULL", "2026-08-18T13:00:00+00:00", "bullish", 64268.5, 64008.0),
    ("Aug16_BULL", "2026-08-16T22:00:00+00:00", "bullish", 62936.5, 62687.0),
    ("Aug14_BULL", "2026-08-14T14:00:00+00:00", "bullish", 62778.0, 62505.0),
    ("Aug03_BULL", "2026-08-03T08:00:00+00:00", "bullish", 62630.5, 62274.0),
]


def section_d_temporal_replay(eng: OBSnapshotEngine) -> List[dict]:
    """
    For each key OB: snapshot at formation, +1h, +5h, +10h, cutoff.
    Report state at each checkpoint.
    """
    rows = []
    for label, creation_ts_iso, direction, upper, lower in _TEMPORAL_KEY_OBS:
        form_ts = datetime.fromisoformat(creation_ts_iso)
        cutoff  = datetime.fromisoformat(DATASET_CUTOFF)

        checkpoints = [
            ("formation",  form_ts),
            ("+1h",        form_ts + timedelta(hours=1)),
            ("+5h",        form_ts + timedelta(hours=5)),
            ("+10h",       form_ts + timedelta(hours=10)),
            ("cutoff",     cutoff),
        ]

        for cp_label, cp_ts in checkpoints:
            if cp_ts > cutoff:
                cp_ts = cutoff  # cap at cutoff

            snap = eng.snapshot_at(cp_ts)

            # Find the OB
            found = None
            for ob in snap.all_obs:
                if (ob.creation_timestamp == form_ts
                        and ob.direction == direction
                        and abs(float(ob.upper_price) - upper) < 1.0):
                    found = ob
                    break

            if found:
                state     = found.state
                is_active = found.is_active
                touch_ts  = found.first_touch_timestamp.isoformat() if found.first_touch_timestamp else ""
                inv_ts    = found.invalidation_timestamp.isoformat() if found.invalidation_timestamp else ""
            else:
                state     = "NOT_YET_CREATED"
                is_active = False
                touch_ts  = inv_ts = ""

            rows.append({
                "ob_label":          label,
                "direction":         direction,
                "upper":             upper,
                "lower":             lower,
                "creation_ts":       creation_ts_iso,
                "checkpoint_label":  cp_label,
                "checkpoint_ts":     cp_ts.isoformat(),
                "state":             state,
                "is_active":         is_active,
                "first_touch_ts":    touch_ts,
                "invalidation_ts":   inv_ts,
            })
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION E — OB Identity / Duplicate Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def section_e_identity_analysis(
    snap_all_obs: List[OBRecord],
    candles: List[Candle],
    int_brk: List[StructureBreak],
    sw_brk: List[StructureBreak],
    int_piv: List[PivotPoint],
    sw_piv: List[PivotPoint],
) -> Tuple[List[dict], dict]:
    """
    Group OBs by (creation_timestamp, direction, upper_price, lower_price).
    For groups with >1 member, investigate the break events to determine
    if they are LEGITIMATE_DISTINCT or LIKELY_DUPLICATE.

    Returns: (rows, summary)
    """
    # Build a map: ts+direction+price -> list of OBRecords
    groups: Dict[tuple, List[OBRecord]] = defaultdict(list)
    for ob in snap_all_obs:
        key = (
            ob.creation_timestamp.isoformat(),
            ob.direction,
            float(ob.upper_price),
            float(ob.lower_price),
        )
        groups[key].append(ob)

    # Build break lookup (index -> StructureBreak)
    all_breaks = int_brk + sw_brk
    brk_by_idx = {b.index: b for b in all_breaks}

    rows = []
    group_id = 0
    single_count = 0
    multi_group_count = 0
    total_multi_obs = 0

    for key, obs_list in sorted(groups.items(), key=lambda x: x[0][0]):
        if len(obs_list) == 1:
            single_count += 1
            continue

        group_id += 1
        multi_group_count += 1
        total_multi_obs += len(obs_list)
        creation_ts, direction, upper, lower = key

        # Analyse each OB in the group
        for idx, ob in enumerate(obs_list):
            brk_idx = ob.break_candle_index
            brk = brk_by_idx.get(brk_idx)

            if brk:
                brk_ts     = candles[brk_idx].timestamp.isoformat() if brk_idx < len(candles) else ""
                brk_dir    = brk.direction.value if hasattr(brk.direction, 'value') else str(brk.direction)
                brk_type   = brk.break_type.value if hasattr(brk.break_type, 'value') else str(brk.break_type)
                brk_stype  = brk.structure_type.value if hasattr(brk.structure_type, 'value') else str(brk.structure_type)
                brk_price  = float(brk.price)
            else:
                brk_ts = brk_dir = brk_type = brk_stype = ""
                brk_price = 0.0

            # Classify the multi-OB group
            # Determine the set of structure_types and break_types in the group
            stype_set = {o.structure_type for o in obs_list}
            btype_set = set()
            bdir_set  = set()
            for o in obs_list:
                b = brk_by_idx.get(o.break_candle_index)
                if b:
                    btype_set.add(b.break_type.value if hasattr(b.break_type, 'value') else str(b.break_type))
                    bdir_set.add(b.direction.value if hasattr(b.direction, 'value') else str(b.direction))

            # Verdict logic
            all_break_indices = [o.break_candle_index for o in obs_list]
            unique_breaks = len(set(all_break_indices))
            unique_stypes = len(stype_set)

            if unique_stypes > 1:
                # Different structure levels (internal vs swing) → legitimate
                verdict = "LEGITIMATE_DISTINCT_STRUCTURE_LEVEL"
                explanation = (
                    f"Same source candle ({creation_ts[:19]}) used as OB extreme for BOTH "
                    f"internal AND swing structural breaks. This is legitimate per LuxAlgo semantics: "
                    f"the same underlying candle represents the extreme in multiple structural contexts."
                )
            elif unique_breaks > 1:
                # Same structure level but different break events
                verdict = "LEGITIMATE_DISTINCT_BREAK_EVENT"
                explanation = (
                    f"Same source candle broken by {unique_breaks} distinct structural break events "
                    f"(break indices: {sorted(set(all_break_indices))}). "
                    f"Each break creates an independent OB instance. "
                    f"This is consistent with LuxAlgo creating a new OB for every BOS/CHOCH event."
                )
            else:
                verdict = "LIKELY_DUPLICATE"
                explanation = (
                    f"Identical source candle, direction, price zone, AND break event. "
                    f"This appears to be a duplicate and should be deduplicated."
                )

            rows.append({
                "group_id":          group_id,
                "ob_count_in_group": len(obs_list),
                "creation_timestamp": creation_ts,
                "direction":          direction,
                "upper_price":        upper,
                "lower_price":        lower,
                "ob_index_in_group":  idx + 1,
                "break_candle_index": brk_idx,
                "break_timestamp":    brk_ts,
                "break_direction":    brk_dir,
                "break_type":         brk_type,
                "structure_type":     ob.structure_type,
                "break_structure":    brk_stype,
                "break_price":        brk_price,
                "state":              ob.state,
                "is_active":          ob.is_active,
                "verdict":            verdict,
                "explanation":        explanation,
            })

    summary = {
        "total_obs":           len(snap_all_obs),
        "unique_source_groups": len(groups),
        "single_ob_groups":    single_count,
        "multi_ob_groups":     multi_group_count,
        "total_multi_obs":     total_multi_obs,
    }
    return rows, summary


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION F — Missing Blue OB Diagnostic
# ═══════════════════════════════════════════════════════════════════════════════

def section_f_tv_differential(
    tv_obs: List[dict],
    snap_all_obs: List[OBRecord],
    snap_active_obs: List[OBRecord],
    candles: List[Candle],
    int_brk: List[StructureBreak],
    sw_brk: List[StructureBreak],
    int_piv: List[PivotPoint],
    sw_piv: List[PivotPoint],
) -> List[dict]:
    """
    For each TV observation, run the full missing-OB investigation.
    """
    rows = []
    all_breaks = int_brk + sw_brk

    for tv in tv_obs:
        tv_id      = tv["tv_id"]
        tv_dir     = tv["direction"].lower()
        tv_upper   = Decimal(str(tv["upper"])) if tv["upper"] is not None else None
        tv_lower   = Decimal(str(tv["lower"])) if tv["lower"] is not None else None
        tv_obs_ts  = tv.get("observed_timestamp", "")
        is_fvg     = tv.get("is_fvg", False)
        tv_notes   = tv.get("notes", "")

        # ── G. FVG protection ─────────────────────────────────────────────────
        if is_fvg:
            rows.append({
                "tv_id":     tv_id,
                "direction": tv_dir,
                "upper":     float(tv_upper) if tv_upper else "",
                "lower":     float(tv_lower) if tv_lower else "",
                "result":    "IGNORE_FVG",
                "explanation": "GREEN zone marked as FVG — excluded from OB comparison.",
                "python_match_upper": "", "python_match_lower": "",
                "python_match_state": "", "python_match_creation": "",
                "nearby_breaks": "", "candidate_obs": "",
                "pivot_search_range": "",
            })
            continue

        # ── Define search window ──────────────────────────────────────────────
        if tv_obs_ts:
            obs_ts = datetime.fromisoformat(tv_obs_ts)
            if obs_ts.tzinfo is None:
                obs_ts = obs_ts.replace(tzinfo=timezone.utc)
            win_start = obs_ts - timedelta(hours=72)
            win_end   = obs_ts + timedelta(hours=24)
        else:
            cutoff = datetime.fromisoformat(DATASET_CUTOFF)
            win_start = cutoff - timedelta(days=30)
            win_end   = cutoff

        # Use _search_upper/lower if prices unknown
        search_upper = float(tv["_search_upper"]) if "_search_upper" in tv else (float(tv_upper) + 500 if tv_upper else 0)
        search_lower = float(tv["_search_lower"]) if "_search_lower" in tv else (float(tv_lower) - 500 if tv_lower else 0)

        # ── Step 1: Find nearby structure breaks in window ────────────────────
        nearby_breaks = []
        for brk in all_breaks:
            if brk.index < len(candles):
                brk_ts = candles[brk.index].timestamp
                if win_start <= brk_ts <= win_end:
                    brk_dir_str = brk.direction.value if hasattr(brk.direction, 'value') else str(brk.direction)
                    nearby_breaks.append({
                        "index":      brk.index,
                        "timestamp":  brk_ts.isoformat(),
                        "direction":  brk_dir_str,
                        "break_type": brk.break_type.value if hasattr(brk.break_type, 'value') else str(brk.break_type),
                        "structure":  brk.structure_type.value if hasattr(brk.structure_type, 'value') else str(brk.structure_type),
                        "price":      float(brk.price),
                    })

        # ── Step 2: Find nearby pivots ────────────────────────────────────────
        all_pivots = int_piv + sw_piv
        nearby_pivots = []
        for piv in all_pivots:
            if piv.index < len(candles):
                piv_ts = candles[piv.index].timestamp
                if win_start <= piv_ts <= win_end:
                    nearby_pivots.append({
                        "index":   piv.index,
                        "ts":      piv_ts.isoformat(),
                        "is_high": piv.is_high,
                        "price":   float(piv.price),
                    })

        # ── Step 3: Look for Python OB in price range ─────────────────────────
        price_tol = Decimal("500")  # broad: zone-size or 500 USD
        python_match = None
        candidates = []

        for ob in snap_all_obs:
            dir_ok = ob.direction == tv_dir
            if not dir_ok:
                continue
            # Price zone proximity check
            if tv_upper is not None and tv_lower is not None:
                upper_delta = abs(ob.upper_price - tv_upper)
                lower_delta = abs(ob.lower_price - tv_lower)
                price_near  = upper_delta <= price_tol or lower_delta <= price_tol
            else:
                # Unknown prices — use search range
                price_near = (float(ob.upper_price) <= search_upper and
                              float(ob.lower_price) >= search_lower)

            if price_near:
                cand = {
                    "direction":    ob.direction,
                    "upper":        float(ob.upper_price),
                    "lower":        float(ob.lower_price),
                    "creation_ts":  ob.creation_timestamp.isoformat(),
                    "state":        ob.state,
                    "is_active":    ob.is_active,
                    "structure_type": ob.structure_type,
                    "break_index":  ob.break_candle_index,
                }
                candidates.append(cand)
                # Exact match?
                if tv_upper is not None:
                    if abs(ob.upper_price - tv_upper) <= Decimal("1") and abs(ob.lower_price - tv_lower) <= Decimal("1"):
                        python_match = cand

        # ── Step 4: Classify the result ───────────────────────────────────────
        dir_breaks = [b for b in nearby_breaks if tv_dir.upper() in b["direction"].upper()]

        if python_match:
            result = "FOUND_IN_PYTHON"
            explanation = (
                f"Python OB found: upper={python_match['upper']:.1f}, "
                f"lower={python_match['lower']:.1f}, state={python_match['state']}. "
                f"State may differ from TradingView visual."
            )
        elif candidates:
            result = "FOUND_NEARBY_PRICE_DIFFERS"
            explanation = (
                f"{len(candidates)} candidate Python OBs found in direction={tv_dir} "
                f"within {float(price_tol):.0f} USD. Prices differ beyond ±1 USD. "
                f"Possible: LuxAlgo selects a different extreme candle in the search range."
            )
        elif not dir_breaks:
            result = "NOT_CREATED_NO_BREAK"
            explanation = (
                f"No {tv_dir.upper()} structure break found in search window "
                f"[{win_start.isoformat()[:19]} → {win_end.isoformat()[:19]}]. "
                f"A structural break is REQUIRED for OB creation. "
                f"Without a break, no OB can be formed at this zone."
            )
        else:
            result = "NOT_CREATED_ATR_OR_RANGE"
            explanation = (
                f"{len(dir_breaks)} {tv_dir.upper()} structural breaks exist in window. "
                f"However no Python OB was created in the price range "
                f"[{search_lower:.0f}–{search_upper:.0f}]. "
                f"Possible causes: (1) ATR filter excluded candidate (high-volatility candle inversion), "
                f"(2) different pivot index used, "
                f"(3) search range [pivot_idx, break_idx) produced a different extreme candle, "
                f"(4) OB was created but already invalidated."
            )

        pivot_search_range = ""
        if dir_breaks:
            # For the first matching break, show what pivot was searched
            best_brk = dir_breaks[0]
            pivot_search_range = (
                f"break_idx={best_brk['index']} at {best_brk['timestamp'][:19]} "
                f"(LuxAlgo search: [pivot_idx, {best_brk['index']}) )"
            )

        rows.append({
            "tv_id":               tv_id,
            "direction":           tv_dir,
            "upper":               float(tv_upper) if tv_upper else "unknown",
            "lower":               float(tv_lower) if tv_lower else "unknown",
            "is_fvg":              is_fvg,
            "notes":               tv_notes,
            "observed_ts":         tv_obs_ts,
            "search_window_start": win_start.isoformat(),
            "search_window_end":   win_end.isoformat(),
            "result":              result,
            "explanation":         explanation,
            "python_match_upper":  python_match["upper"] if python_match else "",
            "python_match_lower":  python_match["lower"] if python_match else "",
            "python_match_state":  python_match["state"] if python_match else "",
            "python_match_creation": python_match["creation_ts"] if python_match else "",
            "nearby_break_count":  len(nearby_breaks),
            "dir_break_count":     len(dir_breaks),
            "candidate_ob_count":  len(candidates),
            "nearby_breaks_json":  json.dumps(nearby_breaks[:5], default=str),
            "candidate_obs_json":  json.dumps(candidates[:5], default=str),
            "pivot_search_range":  pivot_search_range,
        })

    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _write_csv(path: Path, rows: list, fields: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def compute_phase3e1_analysis_in_memory(
    eng: OBSnapshotEngine,
    dataset_meta_path: Path = DATA_META,
) -> Tuple[List[dict], List[dict], List[dict], List[dict], List[dict], dict]:
    """
    Run Phase 3E.1 calculations in memory without writing to disk.
    Returns (trace_rows, cmp_rows, replay_rows, id_rows, diff_rows, summary).
    """
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = json.loads(dataset_meta_path.read_text(encoding="utf-8")) if dataset_meta_path.exists() else {"sha256": EXPECTED_SHA256, "candle_count": EXPECTED_CANDLES}

    candles = eng.candles
    snap = eng.snapshot_at(DATASET_CUTOFF)
    parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = eng._run_pipeline(candles)
    break_map = {b.index: b for b in int_brk + sw_brk}

    trace_rows = section_a_ob_trace(candles)
    first_touch_a = next((r for r in trace_rows if r["transition_a"].endswith("TOUCHED")), None)
    first_touch_b = next((r for r in trace_rows if r["transition_b"].endswith("TOUCHED")), None)

    cmp_rows = section_b_model_comparison(snap.all_obs, candles, break_map)
    agree_counts = {}
    for r in cmp_rows:
        agree_counts[r["agreement"]] = agree_counts.get(r["agreement"], 0) + 1

    replay_rows = section_d_temporal_replay(eng)
    aug19_replay = [r for r in replay_rows if r["ob_label"] == "Aug19_BULL"]

    id_rows, id_summary = section_e_identity_analysis(
        snap.all_obs, candles, int_brk, sw_brk, int_piv, sw_piv
    )
    verdicts = {}
    for r in id_rows:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1

    diff_rows = section_f_tv_differential(
        TV_OBSERVATIONS, snap.all_obs, snap.active_obs,
        candles, int_brk, sw_brk, int_piv, sw_piv
    )

    summary = {
        "generated_at": gen_ts,
        "dataset_sha256": meta["sha256"],
        "dataset_cutoff": DATASET_CUTOFF,
        "total_obs": snap.all_count,
        "active_obs": snap.active_count,
        "model_agreement": agree_counts,
        "identity_summary": id_summary,
        "identity_verdicts": verdicts,
        "tv_differential_results": {r["tv_id"]: r["result"] for r in diff_rows},
        "aug19_ob_trace": {
            "upper": float(AUG19_UPPER),
            "lower": float(AUG19_LOWER),
            "first_touch_model_a": first_touch_a["timestamp"] if first_touch_a else None,
            "first_touch_model_a_label": first_touch_a["extended_label"] if first_touch_a else None,
            "first_touch_model_b": first_touch_b["timestamp"] if first_touch_b else None,
            "first_touch_model_b_label": first_touch_b["extended_label"] if first_touch_b else None,
        },
        "aug19_temporal_replay": aug19_replay,
    }

    return trace_rows, cmp_rows, replay_rows, id_rows, diff_rows, summary


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("=" * 60)
    print("Phase 3E.1: OB State and Missing-OB Root Cause Analysis")
    print("=" * 60)

    # ── Verify dataset ────────────────────────────────────────────────────────
    meta = json.loads(DATA_META.read_text(encoding="utf-8"))
    assert meta["sha256"] == EXPECTED_SHA256, f"SHA-256 mismatch"
    assert meta["candle_count"] == EXPECTED_CANDLES, f"Candle count mismatch"
    print(f"Dataset : {DATA_CSV}")
    print(f"SHA-256 : {meta['sha256']} [OK]")

    # ── Load engine ───────────────────────────────────────────────────────────
    print("\nLoading engine and running pipeline...")
    eng = OBSnapshotEngine.from_csv(str(DATA_CSV))
    candles = eng.candles
    snap    = eng.snapshot_at(DATASET_CUTOFF)
    parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = eng._run_pipeline(candles)
    break_map = {b.index: b for b in int_brk + sw_brk}
    print(f"  All OBs : {snap.all_count} | Active : {snap.active_count}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # A — Candle-by-candle trace for Aug-19 OB
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[A] Generating ob_trace_aug19.csv ...")
    trace_rows = section_a_ob_trace(candles)
    trace_fields = [
        "candle_index", "timestamp", "open", "high", "low", "close",
        "zone_relation", "overlaps_broad", "enters_strictly",
        "lower_boundary_viol", "close_below_lower",
        "is_formation", "is_break_candle", "is_between_form_brk", "is_post_break",
        "extended_label",
        "model_a_state", "model_b_state", "model_c_state",
        "transition_a", "transition_b", "transition_c",
    ]
    _write_csv(OUT_DIR / "ob_trace_aug19.csv", trace_rows, trace_fields)

    # Summarize the first touch event
    first_touch_a = next((r for r in trace_rows if r["transition_a"].endswith("TOUCHED")), None)
    first_touch_b = next((r for r in trace_rows if r["transition_b"].endswith("TOUCHED")), None)
    print(f"  Model A first TOUCHED: {first_touch_a['timestamp'][:19] if first_touch_a else 'never'} "
          f"({first_touch_a['extended_label'] if first_touch_a else ''})")
    print(f"  Model B first TOUCHED: {first_touch_b['timestamp'][:19] if first_touch_b else 'never'} "
          f"({first_touch_b['extended_label'] if first_touch_b else ''})")
    print(f"  Written {len(trace_rows)} rows.")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # B — Three-model comparison for all 341 OBs
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[B] Generating model_comparison.csv ...")
    cmp_rows = section_b_model_comparison(snap.all_obs, candles, break_map)
    cmp_fields = [
        "ob_id", "structure_type", "direction", "upper_price", "lower_price",
        "creation_timestamp", "break_candle_index", "break_type", "production_state",
        "model_a_state", "model_a_touch_ts", "model_a_invalid_ts",
        "model_b_state", "model_b_touch_ts", "model_b_invalid_ts",
        "model_c_state", "model_c_info_touch_ts", "model_c_invalid_ts",
        "agreement",
    ]
    _write_csv(OUT_DIR / "model_comparison.csv", cmp_rows, cmp_fields)
    agree_counts = {}
    for r in cmp_rows:
        agree_counts[r["agreement"]] = agree_counts.get(r["agreement"], 0) + 1
    print(f"  Agreement summary:")
    for k, v in sorted(agree_counts.items()):
        print(f"    {k:35}: {v:4d}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # D — Temporal replay
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[D] Generating temporal_replay.csv ...")
    replay_rows = section_d_temporal_replay(eng)
    replay_fields = [
        "ob_label", "direction", "upper", "lower", "creation_ts",
        "checkpoint_label", "checkpoint_ts",
        "state", "is_active", "first_touch_ts", "invalidation_ts",
    ]
    _write_csv(OUT_DIR / "temporal_replay.csv", replay_rows, replay_fields)
    print(f"  Written {len(replay_rows)} rows for {len(_TEMPORAL_KEY_OBS)} OBs x 5 checkpoints.")

    # Print the Aug-19 OB replay specifically
    aug19_replay = [r for r in replay_rows if r["ob_label"] == "Aug19_BULL"]
    print("  Aug-19 OB temporal replay:")
    for r in aug19_replay:
        print(f"    {r['checkpoint_label']:12} | {r['checkpoint_ts'][:19]} | state={r['state']:12} | touch={r['first_touch_ts'][:19] if r['first_touch_ts'] else 'none':19}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # E — Identity / Duplicate Analysis
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[E] Generating ob_identity_analysis.csv ...")
    id_rows, id_summary = section_e_identity_analysis(
        snap.all_obs, candles, int_brk, sw_brk, int_piv, sw_piv
    )
    id_fields = [
        "group_id", "ob_count_in_group", "creation_timestamp",
        "direction", "upper_price", "lower_price",
        "ob_index_in_group", "break_candle_index", "break_timestamp",
        "break_direction", "break_type", "structure_type", "break_structure",
        "break_price", "state", "is_active", "verdict", "explanation",
    ]
    _write_csv(OUT_DIR / "ob_identity_analysis.csv", id_rows, id_fields)
    print(f"  Summary: {id_summary}")
    verdicts = {}
    for r in id_rows:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    for k, v in sorted(verdicts.items()):
        print(f"    {k:50}: {v}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # F — TV OB Differential
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[F] Generating tv_ob_differential.csv ...")
    diff_rows = section_f_tv_differential(
        TV_OBSERVATIONS, snap.all_obs, snap.active_obs,
        candles, int_brk, sw_brk, int_piv, sw_piv
    )
    diff_fields = [
        "tv_id", "direction", "upper", "lower", "is_fvg", "notes",
        "observed_ts", "search_window_start", "search_window_end",
        "result", "explanation",
        "python_match_upper", "python_match_lower",
        "python_match_state", "python_match_creation",
        "nearby_break_count", "dir_break_count", "candidate_ob_count",
        "nearby_breaks_json", "candidate_obs_json",
        "pivot_search_range",
    ]
    _write_csv(OUT_DIR / "tv_ob_differential.csv", diff_rows, diff_fields)
    print(f"  Written {len(diff_rows)} rows.")
    for r in diff_rows:
        print(f"    {r['tv_id']:12} | {r['result']:35} | {r['explanation'][:70]}...")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Summary JSON
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    summary = {
        "generated_at": gen_ts,
        "dataset_sha256": meta["sha256"],
        "dataset_cutoff": DATASET_CUTOFF,
        "total_obs": snap.all_count,
        "active_obs": snap.active_count,
        "model_agreement": agree_counts,
        "identity_summary": id_summary,
        "identity_verdicts": verdicts,
        "tv_differential_results": {r["tv_id"]: r["result"] for r in diff_rows},
        "aug19_ob_trace": {
            "upper": float(AUG19_UPPER),
            "lower": float(AUG19_LOWER),
            "first_touch_model_a": first_touch_a["timestamp"] if first_touch_a else None,
            "first_touch_model_a_label": first_touch_a["extended_label"] if first_touch_a else None,
            "first_touch_model_b": first_touch_b["timestamp"] if first_touch_b else None,
            "first_touch_model_b_label": first_touch_b["extended_label"] if first_touch_b else None,
        },
        "aug19_temporal_replay": aug19_replay,
    }
    summary_path = OUT_DIR / "phase3e1_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 60)
    print("Output files:")
    for p in sorted(OUT_DIR.iterdir()):
        if p.is_file():
            print(f"  {p.relative_to(REPO_ROOT)}  ({p.stat().st_size:,} bytes)")

    return summary


if __name__ == "__main__":
    result = main()
    import sys; sys.exit(0)
