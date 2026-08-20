"""
Phase 3E: LuxAlgo OB Differential Validation — Diagnostic Engine

Provides:
1. Diagnostic lifecycle calculator (separate from production _apply_lifecycle)
   - Identifies WHY an OB is marked TOUCHED vs FRESH
   - Distinguishes break-candle overlap from genuine retest
2. Candle-by-candle OB trace for selected OBs
3. Missing-OB investigation tool (given a TV blue OB observation)
4. Differential matcher for manually entered TV blue OB references
5. All analysis uses ONLY the canonical Delta Exchange India BTCUSD dataset

IMPORTANT:
- This module does NOT modify production SMC files.
- All diagnostic lifecycle logic is SEPARATE from ob_snapshot_engine.py.
- Green FVG zones are explicitly excluded from all matching.

Usage (from repo root):
    python engine/generate_phase3e_diagnostics.py
"""

import sys
import csv
import json
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

# ── Path setup ─────────────────────────────────────────────────────────────────
ENGINE    = Path(__file__).parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import detect_order_blocks_streaming, OrderBlockConfig
from quantedge.smc.models import (
    OrderBlock, PivotPoint, StructureBreak,
    OBState, TrendDirection, BreakType,
)
from ob_snapshot_engine import OBSnapshotEngine, OBRecord

# ── Canonical paths ─────────────────────────────────────────────────────────────
DATA_CSV  = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
DATA_META = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026_metadata.json"
OUT_DIR   = REPO_ROOT / "validation" / "phase3e"

DATASET_CUTOFF  = "2026-08-20T00:00:00+00:00"
EXPECTED_SHA256 = "2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b"  # row-based, CRLF-independent
EXPECTED_CANDLES = 5545

SMC_CONFIG = dict(atr_period=200, atr_mult=2.0, internal_length=5, swing_length=50)

# ── Lifecycle event types ──────────────────────────────────────────────────────
RELATION_ABOVE_ZONE   = "ABOVE_ZONE"       # candle entirely above OB zone
RELATION_BELOW_ZONE   = "BELOW_ZONE"       # candle entirely below OB zone
RELATION_ENTERS_ZONE  = "ENTERS_ZONE"      # candle overlaps/enters zone (touch candidate)
RELATION_BREAK_CANDLE = "BREAK_CANDLE"     # the structure-break candle itself
RELATION_FORMATION    = "FORMATION"        # the OB source candle

TRANSITION_CREATED    = "CREATED"
TRANSITION_FRESH      = "FRESH"
TRANSITION_TOUCHED    = "TOUCHED"          # genuine retest (post-break candle)
TRANSITION_TOUCHED_BY_BREAK = "TOUCHED_BY_BREAK_CANDLE"  # immediate overlap from break
TRANSITION_INVALIDATED = "INVALIDATED"

# ── Differential match result codes ───────────────────────────────────────────
class DiffResult:
    EXACT_MATCH            = "EXACT_MATCH"
    PRICE_MATCH_TIME_MISS  = "PRICE_MATCH_TIME_MISMATCH"
    TIME_MATCH_PRICE_MISS  = "TIME_MATCH_PRICE_MISMATCH"
    DIRECTION_MISMATCH     = "DIRECTION_MISMATCH"
    STRUCTURE_MISMATCH     = "STRUCTURE_MISMATCH"
    STATE_MISMATCH         = "STATE_MISMATCH"
    MISSING_IN_PYTHON      = "MISSING_IN_PYTHON"
    EXTRA_IN_PYTHON        = "EXTRA_IN_PYTHON"
    AMBIGUOUS_MATCH        = "AMBIGUOUS_MATCH"

# Price tolerance = Delta Exchange India minimum tick (0.5 USD)
DELTA_TICK = Decimal("0.5")
# Loose price tolerance for approximate matching (0.5% of price)
LOOSE_PRICE_PCT = Decimal("0.005")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DIAGNOSTIC LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CandleRelation:
    """Describes how one candle relates to an OB zone."""
    candle_index:  int
    timestamp:     datetime
    open:          Decimal
    high:          Decimal
    low:           Decimal
    close:         Decimal
    relation:      str   # RELATION_* constant
    state_after:   str   # lifecycle state after this candle
    transition:    str   # what happened (TRANSITION_* or "")
    note:          str = ""

    def to_dict(self) -> dict:
        return {
            "candle_index":  self.candle_index,
            "timestamp":     self.timestamp.isoformat(),
            "open":          float(self.open),
            "high":          float(self.high),
            "low":           float(self.low),
            "close":         float(self.close),
            "relation":      self.relation,
            "state_after":   self.state_after,
            "transition":    self.transition,
            "note":          self.note,
        }


@dataclass
class DiagnosticLifecycleResult:
    """Rich lifecycle result for a single OB under diagnostic rules."""
    ob_id:                     int
    structure_type:            str
    direction:                 str
    upper_price:               Decimal
    lower_price:               Decimal
    creation_timestamp:        datetime
    creation_candle_index:     int
    break_candle_index:        int
    break_timestamp:           Optional[datetime]
    break_type:                str

    # Production (current engine) state
    production_state:          str

    # Diagnostic states
    diag_state:                str   # state under diagnostic rules (break candle excluded from TOUCH)
    diag_touch_ts:             Optional[datetime]  # first genuine retest ts
    diag_invalid_ts:           Optional[datetime]  # first invalidation ts
    break_candle_overlaps_zone: bool  # root cause flag

    # Detailed trace
    candle_trace: List[CandleRelation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ob_id":                      self.ob_id,
            "structure_type":             self.structure_type,
            "direction":                  self.direction,
            "upper_price":                float(self.upper_price),
            "lower_price":                float(self.lower_price),
            "creation_timestamp":         self.creation_timestamp.isoformat(),
            "creation_candle_index":      self.creation_candle_index,
            "break_candle_index":         self.break_candle_index,
            "break_timestamp":            self.break_timestamp.isoformat() if self.break_timestamp else None,
            "break_type":                 self.break_type,
            "production_state":           self.production_state,
            "diag_state":                 self.diag_state,
            "diag_touch_timestamp":       self.diag_touch_ts.isoformat() if self.diag_touch_ts else None,
            "diag_invalid_timestamp":     self.diag_invalid_ts.isoformat() if self.diag_invalid_ts else None,
            "break_candle_overlaps_zone": self.break_candle_overlaps_zone,
            "state_discrepancy":          self.production_state != self.diag_state,
            "candle_trace":               [c.to_dict() for c in self.candle_trace],
        }


def _candle_overlaps_zone(c: Candle, upper: Decimal, lower: Decimal) -> bool:
    """Returns True if candle price range overlaps the OB zone."""
    return c.low <= upper and c.high >= lower


def _candle_relation(
    c: Candle,
    upper: Decimal,
    lower: Decimal,
    is_break_candle: bool,
    is_formation: bool,
    direction: str,
) -> Tuple[str, bool]:
    """
    Returns (relation_type, violates_mitigation_boundary).

    Violation rules (High/Low mitigation):
        Bullish OB: violates when candle LOW < OB lower boundary
        Bearish OB: violates when candle HIGH > OB upper boundary
    """
    if is_formation:
        return RELATION_FORMATION, False

    if is_break_candle:
        overlaps = _candle_overlaps_zone(c, upper, lower)
        return RELATION_BREAK_CANDLE, False  # break candle overlap is NOT a mitigation

    violates = False
    if direction == "bullish":
        violates = c.low < lower
    else:
        violates = c.high > upper

    if _candle_overlaps_zone(c, upper, lower):
        return RELATION_ENTERS_ZONE, violates
    elif c.low > upper:
        return RELATION_ABOVE_ZONE, violates
    else:
        return RELATION_BELOW_ZONE, violates


def compute_diagnostic_lifecycle(
    ob: OrderBlock,
    ob_id: int,
    candles: List[Candle],
    break_candle_index: int,
    break_type: str,
    structure_type: str,
    production_state: str,
    max_trace_candles: int = 50,
) -> DiagnosticLifecycleResult:
    """
    Compute diagnostic lifecycle for one OB.

    Key difference from production _apply_lifecycle:
    - The break candle (index == break_candle_index) is NOT counted as a TOUCH.
      It is recorded separately as TOUCHED_BY_BREAK_CANDLE for analysis.
    - Only candles STRICTLY AFTER the break candle count as genuine retests.

    This follows the diagnostic interpretation:
    'A newly created OB must not automatically become TOUCHED simply because
     the structure-break candle (which confirmed the break) overlaps the OB zone.'
    """
    direction  = "bullish" if ob.type == "BULLISH" else "bearish"
    upper      = ob.top_price
    lower      = ob.bottom_price
    form_idx   = ob.formation_index
    form_ts    = ob.formation_candle.timestamp
    break_ts   = candles[break_candle_index].timestamp if break_candle_index < len(candles) else None

    diag_state   = "fresh"
    diag_touch   = None
    diag_invalid = None

    break_overlaps = False
    trace          = []
    candles_scanned = 0

    for i, c in enumerate(candles):
        if c.timestamp < form_ts:
            continue
        if diag_state == "invalidated":
            break
        if candles_scanned >= max_trace_candles and i > break_candle_index + max_trace_candles:
            break

        is_formation  = (c.timestamp == form_ts)
        is_break      = (i == break_candle_index)
        overlaps      = _candle_overlaps_zone(c, upper, lower)

        relation, violates_boundary = _candle_relation(
            c, upper, lower, is_break, is_formation, direction
        )

        state_before = diag_state
        transition   = ""
        note         = ""

        if is_formation:
            transition = TRANSITION_CREATED
        elif is_break:
            if overlaps:
                break_overlaps = True
                note = "Break candle overlaps OB zone — NOT counted as retest in diagnostic mode"
                transition = TRANSITION_TOUCHED_BY_BREAK
            else:
                transition = TRANSITION_FRESH
                note = "Break candle does not overlap zone"
        else:
            # Post-break candle — genuine lifecycle check
            if violates_boundary:
                diag_state   = "invalidated"
                diag_invalid = c.timestamp
                transition   = TRANSITION_INVALIDATED
            elif overlaps and diag_state == "fresh":
                diag_state  = "touched"
                diag_touch  = c.timestamp
                transition  = TRANSITION_TOUCHED
            else:
                transition = diag_state.upper()

        trace.append(CandleRelation(
            candle_index = i,
            timestamp    = c.timestamp,
            open         = c.open,
            high         = c.high,
            low          = c.low,
            close        = c.close,
            relation     = relation,
            state_after  = diag_state,
            transition   = transition,
            note         = note,
        ))
        candles_scanned += 1

    return DiagnosticLifecycleResult(
        ob_id                     = ob_id,
        structure_type            = structure_type,
        direction                 = direction,
        upper_price               = upper,
        lower_price               = lower,
        creation_timestamp        = form_ts,
        creation_candle_index     = form_idx,
        break_candle_index        = break_candle_index,
        break_timestamp           = break_ts,
        break_type                = break_type,
        production_state          = production_state,
        diag_state                = diag_state,
        diag_touch_ts             = diag_touch,
        diag_invalid_ts           = diag_invalid,
        break_candle_overlaps_zone = break_overlaps,
        candle_trace              = trace,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MISSING OB INVESTIGATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MissingOBInvestigation:
    """Investigation result for a TradingView OB not found in Python."""
    tv_direction:       str
    tv_upper:           Decimal
    tv_lower:           Decimal
    tv_observed_ts:     Optional[datetime]
    tv_notes:           str

    # Investigation results
    search_window_start: datetime
    search_window_end:   datetime
    nearby_breaks:       List[dict]
    nearby_pivots:       List[dict]
    candidate_obs:       List[dict]
    python_ob_found:     Optional[dict]
    rejection_reasons:   List[str]
    verdict:             str  # FOUND_IN_PYTHON / NOT_CREATED / ATR_FILTERED / WRONG_LEVEL / UNKNOWN

    def to_dict(self) -> dict:
        return {
            "tv_direction":        self.tv_direction,
            "tv_upper":            float(self.tv_upper),
            "tv_lower":            float(self.tv_lower),
            "tv_observed_ts":      self.tv_observed_ts.isoformat() if self.tv_observed_ts else None,
            "tv_notes":            self.tv_notes,
            "search_window_start": self.search_window_start.isoformat(),
            "search_window_end":   self.search_window_end.isoformat(),
            "nearby_breaks":       self.nearby_breaks,
            "nearby_pivots":       self.nearby_pivots,
            "candidate_obs":       self.candidate_obs,
            "python_ob_found":     self.python_ob_found,
            "rejection_reasons":   self.rejection_reasons,
            "verdict":             self.verdict,
        }


def investigate_missing_ob(
    tv_ob: dict,
    snap_result,
    candles: List[Candle],
    parsed_candles,
    int_breaks: List[StructureBreak],
    sw_breaks: List[StructureBreak],
    int_pivots: List[PivotPoint],
    sw_pivots: List[PivotPoint],
    window_hours: int = 48,
) -> MissingOBInvestigation:
    """
    Investigate why a visually observed TradingView blue OB is not in Python output.

    tv_ob format:
    {
        "direction": "bullish" | "bearish",
        "upper": <float>,
        "lower": <float>,
        "observed_timestamp": "<ISO8601 UTC or empty>",
        "notes": "..."
    }
    """
    tv_dir   = tv_ob.get("direction", "").lower()
    tv_upper = Decimal(str(tv_ob.get("upper", 0)))
    tv_lower = Decimal(str(tv_ob.get("lower", 0)))
    tv_ts_str = tv_ob.get("observed_timestamp", "") or tv_ob.get("timestamp", "")
    tv_notes  = tv_ob.get("notes", "")

    tv_ts = None
    if tv_ts_str:
        try:
            tv_ts = datetime.fromisoformat(tv_ts_str)
            if tv_ts.tzinfo is None:
                tv_ts = tv_ts.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    # Search window: ±window_hours around observed timestamp
    if tv_ts:
        from datetime import timedelta
        window_start = tv_ts - timedelta(hours=window_hours)
        window_end   = tv_ts + timedelta(hours=window_hours)
    else:
        # Default: last 7 days of dataset
        from datetime import timedelta
        cutoff_ts = datetime.fromisoformat(DATASET_CUTOFF)
        window_start = cutoff_ts - timedelta(days=7)
        window_end   = cutoff_ts

    # Find nearby structure breaks
    nearby_breaks = []
    for brk in int_breaks + sw_breaks:
        if brk.index < len(candles):
            brk_ts = candles[brk.index].timestamp
            if window_start <= brk_ts <= window_end:
                nearby_breaks.append({
                    "index":      brk.index,
                    "timestamp":  brk_ts.isoformat(),
                    "direction":  brk.direction.value if hasattr(brk.direction, 'value') else str(brk.direction),
                    "break_type": brk.break_type.value if hasattr(brk.break_type, 'value') else str(brk.break_type),
                    "price":      float(brk.price),
                    "structure":  brk.structure_type.value if hasattr(brk.structure_type, 'value') else str(brk.structure_type),
                })

    # Find nearby pivots
    nearby_pivots = []
    for piv in int_pivots + sw_pivots:
        if piv.index < len(candles):
            piv_ts = candles[piv.index].timestamp if piv.timestamp is None else piv.timestamp
            if window_start <= piv_ts <= window_end:
                nearby_pivots.append({
                    "index":     piv.index,
                    "timestamp": piv_ts.isoformat(),
                    "is_high":   piv.is_high,
                    "price":     float(piv.price),
                })

    # Check if Python has any OB near this price zone
    candidate_obs = []
    python_found  = None
    price_tolerance = max(tv_upper - tv_lower, Decimal("500"))  # zone-size or 500 USD

    for ob in snap_result.all_obs:
        price_overlap = (
            abs(ob.upper_price - tv_upper) <= price_tolerance and
            abs(ob.lower_price - tv_lower) <= price_tolerance
        )
        dir_match = ob.direction == tv_dir

        if price_overlap or (dir_match and abs(ob.upper_price - tv_upper) <= price_tolerance):
            candidate_obs.append({
                "direction":          ob.direction,
                "upper_price":        float(ob.upper_price),
                "lower_price":        float(ob.lower_price),
                "creation_timestamp": ob.creation_timestamp.isoformat(),
                "state":              ob.state,
                "is_active":          ob.is_active,
                "price_delta_upper":  float(abs(ob.upper_price - tv_upper)),
                "price_delta_lower":  float(abs(ob.lower_price - tv_lower)),
            })
            if dir_match and abs(ob.upper_price - tv_upper) <= DELTA_TICK * 10:
                python_found = candidate_obs[-1]

    # Determine rejection reasons
    rejection_reasons = []
    verdict = "UNKNOWN"

    if python_found:
        verdict = "FOUND_IN_PYTHON"
    else:
        if not nearby_breaks:
            rejection_reasons.append(
                "No structure break (BOS/CHOCH) found in the search window. "
                "OB can only form when a structure break occurs."
            )
            verdict = "NOT_CREATED"
        else:
            bullish_breaks = [b for b in nearby_breaks if "BULLISH" in b["direction"].upper()]
            bearish_breaks = [b for b in nearby_breaks if "BEARISH" in b["direction"].upper()]

            if tv_dir == "bullish" and not bullish_breaks:
                rejection_reasons.append(
                    "No bullish structure break in search window. "
                    "Bullish OB requires bullish BOS/CHOCH."
                )
                verdict = "NOT_CREATED"
            elif tv_dir == "bearish" and not bearish_breaks:
                rejection_reasons.append(
                    "No bearish structure break in search window. "
                    "Bearish OB requires bearish BOS/CHOCH."
                )
                verdict = "NOT_CREATED"
            elif candidate_obs:
                rejection_reasons.append(
                    "Python found candidate OBs nearby but prices differ beyond tolerance. "
                    "Possible: LuxAlgo selects different extreme candle (parsed vs raw OHLC difference)."
                )
                verdict = "WRONG_LEVEL"
            else:
                rejection_reasons.append(
                    "Structure breaks exist but no matching OB was created. "
                    "Possible causes: ATR filter excluded the candidate candle, "
                    "different pivot index used, or OB selection logic differs."
                )
                verdict = "ATR_FILTERED"

    return MissingOBInvestigation(
        tv_direction        = tv_dir,
        tv_upper            = tv_upper,
        tv_lower            = tv_lower,
        tv_observed_ts      = tv_ts,
        tv_notes            = tv_notes,
        search_window_start = window_start,
        search_window_end   = window_end,
        nearby_breaks       = nearby_breaks,
        nearby_pivots       = nearby_pivots,
        candidate_obs       = candidate_obs,
        python_ob_found     = python_found,
        rejection_reasons   = rejection_reasons,
        verdict             = verdict,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DIFFERENTIAL MATCHER
# ═══════════════════════════════════════════════════════════════════════════════

def match_tv_ob_to_python(
    tv_ob: dict,
    python_obs: List[OBRecord],
    price_tolerance: Decimal = DELTA_TICK,
) -> dict:
    """
    Match a single manually entered TradingView BLUE OB against Python active OBs.

    tv_ob fields:
        direction: "bullish" | "bearish"
        upper: <float>
        lower: <float>
        creation_timestamp: "<ISO8601 or empty>"
        structure_type: "internal" | "swing" | "unknown" | ""
        state: "fresh" | "touched" | ""
        notes: "..."
        is_fvg: false  # MUST be false; green FVG zones must never be matched

    Returns dict with result code and details.
    """
    # ── FVG guard ─────────────────────────────────────────────────────────────
    if tv_ob.get("is_fvg", False):
        return {
            "result":  "EXCLUDED_FVG",
            "details": "GREEN zone marked as FVG — excluded from OB matching. Only BLUE OBs are compared.",
            "tv_ob":   tv_ob,
            "py_ob":   None,
        }

    tv_dir   = tv_ob.get("direction", "").lower()
    tv_upper = Decimal(str(tv_ob.get("upper", 0)))
    tv_lower = Decimal(str(tv_ob.get("lower", 0)))
    tv_cts   = tv_ob.get("creation_timestamp", "")
    tv_stype = tv_ob.get("structure_type", "").lower()
    tv_state = tv_ob.get("state", "").lower()

    # Step 1: Filter by direction (required)
    dir_candidates = [ob for ob in python_obs if ob.direction == tv_dir]
    if not dir_candidates:
        return {
            "result":  DiffResult.MISSING_IN_PYTHON,
            "details": f"No Python {tv_dir} OBs found at all.",
            "tv_ob": tv_ob, "py_ob": None,
        }

    # Step 2: Try exact timestamp match if provided
    if tv_cts:
        ts_candidates = [
            ob for ob in dir_candidates
            if ob.creation_timestamp.isoformat() == tv_cts
        ]
        if ts_candidates:
            py_ob = ts_candidates[0]
            result = _compare_ob_fields(py_ob, tv_upper, tv_lower, tv_stype, tv_state, price_tolerance)
            return {**result, "tv_ob": tv_ob, "py_ob": py_ob.to_dict(), "match_strategy": "exact_timestamp"}

    # Step 3: Price-based matching
    price_matches = [
        ob for ob in dir_candidates
        if abs(ob.upper_price - tv_upper) <= price_tolerance
        and abs(ob.lower_price - tv_lower) <= price_tolerance
    ]
    if price_matches:
        py_ob = price_matches[0]
        result = _compare_ob_fields(py_ob, tv_upper, tv_lower, tv_stype, tv_state, price_tolerance)
        return {**result, "tv_ob": tv_ob, "py_ob": py_ob.to_dict(), "match_strategy": "price_exact"}

    # Step 4: Loose price matching (0.5% of price)
    loose_matches = [
        ob for ob in dir_candidates
        if abs(ob.upper_price - tv_upper) <= tv_upper * LOOSE_PRICE_PCT
        and abs(ob.lower_price - tv_lower) <= tv_lower * LOOSE_PRICE_PCT
    ]
    if loose_matches:
        if len(loose_matches) > 1:
            return {
                "result":  DiffResult.AMBIGUOUS_MATCH,
                "details": f"{len(loose_matches)} Python OBs match within 0.5% tolerance.",
                "tv_ob": tv_ob, "py_ob": [o.to_dict() for o in loose_matches],
                "match_strategy": "loose_price",
            }
        py_ob = loose_matches[0]
        result = _compare_ob_fields(py_ob, tv_upper, tv_lower, tv_stype, tv_state, price_tolerance)
        result["result"] = DiffResult.PRICE_MATCH_TIME_MISS if tv_cts else result["result"]
        return {**result, "tv_ob": tv_ob, "py_ob": py_ob.to_dict(), "match_strategy": "loose_price"}

    # Step 5: No match
    return {
        "result":  DiffResult.MISSING_IN_PYTHON,
        "details": (
            f"No Python {tv_dir} OB found near upper={float(tv_upper):.1f} "
            f"lower={float(tv_lower):.1f} within {float(price_tolerance):.1f} USD tolerance."
        ),
        "tv_ob": tv_ob, "py_ob": None,
        "match_strategy": "none",
    }


def _compare_ob_fields(
    py_ob: OBRecord,
    tv_upper: Decimal,
    tv_lower: Decimal,
    tv_stype: str,
    tv_state: str,
    price_tolerance: Decimal,
) -> dict:
    issues = []

    upper_delta = abs(py_ob.upper_price - tv_upper)
    lower_delta = abs(py_ob.lower_price - tv_lower)

    if upper_delta > price_tolerance or lower_delta > price_tolerance:
        issues.append(DiffResult.TIME_MATCH_PRICE_MISS)

    if tv_stype and tv_stype not in ("unknown", "") and py_ob.structure_type != tv_stype:
        issues.append(DiffResult.STRUCTURE_MISMATCH)

    if tv_state and py_ob.state != tv_state:
        issues.append(DiffResult.STATE_MISMATCH)

    if not issues:
        result = DiffResult.EXACT_MATCH
    elif DiffResult.STATE_MISMATCH in issues and len(issues) == 1:
        result = DiffResult.STATE_MISMATCH
    elif DiffResult.TIME_MATCH_PRICE_MISS in issues and len(issues) == 1:
        result = DiffResult.TIME_MATCH_PRICE_MISS
    else:
        result = issues[0]

    return {
        "result":  result,
        "issues":  issues,
        "details": f"upper_delta={float(upper_delta):.2f}, lower_delta={float(lower_delta):.2f}",
        "price_tolerance_used": float(price_tolerance),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CANDLE RANGE LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def get_candles_in_range(
    candles: List[Candle],
    ts_start: datetime,
    ts_end: datetime,
) -> List[Candle]:
    return [c for c in candles if ts_start <= c.timestamp <= ts_end]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_pipeline(candles: List[Candle]):
    """Run full SMC pipeline and return all artifacts."""
    from ob_snapshot_engine import OBSnapshotEngine
    eng = OBSnapshotEngine(
        candles,
        atr_period=SMC_CONFIG["atr_period"],
        atr_mult=SMC_CONFIG["atr_mult"],
        internal_length=SMC_CONFIG["internal_length"],
        swing_length=SMC_CONFIG["swing_length"],
        symbol="BTCUSD.P",
    )
    snap = eng.snapshot_at(DATASET_CUTOFF)

    # Also run pipeline to get raw artifacts
    parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = eng._run_pipeline(candles)

    # Map break_index -> break event for lifecycle
    break_map = {brk.index: brk for brk in int_brk + sw_brk}

    return snap, parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs, break_map


# ═══════════════════════════════════════════════════════════════════════════════
# 6. MAIN GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("=" * 60)
    print("Phase 3E: LuxAlgo OB Differential Validation Diagnostics")
    print("=" * 60)

    # ── Verify canonical dataset ─────────────────────────────────────────────
    meta = json.loads(DATA_META.read_text(encoding="utf-8"))
    assert meta["sha256"] == EXPECTED_SHA256, f"SHA-256 mismatch: {meta['sha256']}"
    assert meta["candle_count"] == EXPECTED_CANDLES, f"Candle count mismatch: {meta['candle_count']}"
    print(f"Dataset  : {DATA_CSV}")
    print(f"SHA-256  : {meta['sha256']} [OK]")
    print(f"Candles  : {meta['candle_count']} [OK]")

    # ── Load candles ─────────────────────────────────────────────────────────
    print("\nLoading candles...")
    eng = OBSnapshotEngine.from_csv(str(DATA_CSV))
    candles = eng.candles
    print(f"Loaded   : {len(candles)} candles")

    # ── Run full pipeline ─────────────────────────────────────────────────────
    print("Running SMC pipeline...")
    snap, parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs, break_map = run_full_pipeline(candles)
    print(f"  All OBs formed : {snap.all_count}")
    print(f"  Active OBs     : {snap.active_count}")

    # ── Sort all OBs by creation timestamp ───────────────────────────────────
    all_obs_sorted = sorted(snap.all_obs, key=lambda r: r.creation_timestamp)
    ob_id_map = {r.creation_timestamp.isoformat() + r.direction + r.structure_type: i + 1
                 for i, r in enumerate(all_obs_sorted)}

    # ── Map raw_obs for diagnostic lifecycle ─────────────────────────────────
    # Build map from (formation_ts, direction) -> raw OrderBlock
    raw_ob_map = {}
    for ob in raw_obs:
        key = (ob.formation_candle.timestamp, "bullish" if ob.type == "BULLISH" else "bearish")
        raw_ob_map[key] = ob

    # ── Compute diagnostic lifecycle for ALL OBs ──────────────────────────────
    print("\nComputing diagnostic lifecycle for all OBs...")
    all_diag: List[DiagnosticLifecycleResult] = []

    for i, ob_record in enumerate(all_obs_sorted):
        ob_id = i + 1
        direction = ob_record.direction
        raw_key = (ob_record.creation_timestamp, direction)
        raw_ob = raw_ob_map.get(raw_key)

        if raw_ob is None:
            continue

        # Get break event
        brk = break_map.get(ob_record.break_candle_index)
        structure_type = ob_record.structure_type
        break_type = ob_record.break_type if ob_record.break_type else "bos"

        diag = compute_diagnostic_lifecycle(
            ob=raw_ob,
            ob_id=ob_id,
            candles=candles,
            break_candle_index=ob_record.break_candle_index,
            break_type=break_type,
            structure_type=structure_type,
            production_state=ob_record.state,
            max_trace_candles=100,
        )
        all_diag.append(diag)

    print(f"  Diagnostic records computed: {len(all_diag)}")

    # ── Analyze discrepancies ─────────────────────────────────────────────────
    discrepancies = [d for d in all_diag if d.production_state != d.diag_state]
    break_overlap_count = sum(1 for d in all_diag if d.break_candle_overlaps_zone)
    print(f"  State discrepancies (prod vs diag): {len(discrepancies)}")
    print(f"  OBs where break candle overlaps zone: {break_overlap_count}")

    # ── Select OBs for trace output (latest 10 + discrepancies) ──────────────
    active_sorted = sorted(
        [d for d in all_diag if d.production_state != "invalidated"],
        key=lambda d: d.creation_timestamp,
        reverse=True,
    )
    trace_targets = active_sorted[:10] + [d for d in discrepancies if d not in active_sorted[:10]]

    # ── Write output files ────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) ob_lifecycle_trace.csv — per-candle trace for selected OBs
    trace_rows = []
    for d in trace_targets:
        for c in d.candle_trace:
            row = {
                "ob_id":           d.ob_id,
                "structure_type":  d.structure_type,
                "direction":       d.direction,
                "ob_upper":        float(d.upper_price),
                "ob_lower":        float(d.lower_price),
                "creation_ts":     d.creation_timestamp.isoformat(),
                "production_state": d.production_state,
                "diag_state_final": d.diag_state,
                "break_overlaps":  d.break_candle_overlaps_zone,
            }
            row.update(c.to_dict())
            trace_rows.append(row)

    trace_fields = [
        "ob_id", "structure_type", "direction", "ob_upper", "ob_lower",
        "creation_ts", "production_state", "diag_state_final", "break_overlaps",
        "candle_index", "timestamp", "open", "high", "low", "close",
        "relation", "state_after", "transition", "note",
    ]
    _write_csv(OUT_DIR / "ob_lifecycle_trace.csv", trace_rows, trace_fields)
    print(f"\n  [OK] ob_lifecycle_trace.csv — {len(trace_rows)} rows across {len(trace_targets)} OBs")

    # 2) ob_creation_diagnostics.csv — one row per OB, summary
    diag_rows = []
    for d in all_diag:
        diag_rows.append({
            "ob_id":                     d.ob_id,
            "structure_type":            d.structure_type,
            "direction":                 d.direction,
            "upper_price":               float(d.upper_price),
            "lower_price":               float(d.lower_price),
            "ob_height":                 float(d.upper_price - d.lower_price),
            "creation_timestamp":        d.creation_timestamp.isoformat(),
            "break_candle_index":        d.break_candle_index,
            "break_timestamp":           d.break_timestamp.isoformat() if d.break_timestamp else "",
            "break_type":                d.break_type,
            "break_candle_overlaps_zone": d.break_candle_overlaps_zone,
            "production_state":          d.production_state,
            "diag_state":                d.diag_state,
            "state_discrepancy":         d.production_state != d.diag_state,
            "diag_touch_timestamp":      d.diag_touch_ts.isoformat() if d.diag_touch_ts else "",
            "diag_invalid_timestamp":    d.diag_invalid_ts.isoformat() if d.diag_invalid_ts else "",
        })

    diag_fields = [
        "ob_id", "structure_type", "direction", "upper_price", "lower_price",
        "ob_height", "creation_timestamp", "break_candle_index", "break_timestamp",
        "break_type", "break_candle_overlaps_zone", "production_state", "diag_state",
        "state_discrepancy", "diag_touch_timestamp", "diag_invalid_timestamp",
    ]
    _write_csv(OUT_DIR / "ob_creation_diagnostics.csv", diag_rows, diag_fields)
    print(f"  [OK] ob_creation_diagnostics.csv — {len(diag_rows)} rows")

    # 3) tv_ob_manual_reference_template.json
    known_observations = [
        {
            "_note": "Example: blue OB observed near ~69k in TradingView screenshots",
            "_note2": "Fill in exact values from TradingView LuxAlgo tooltip hover",
            "direction": "bullish",
            "upper": 0.0,
            "lower": 0.0,
            "creation_timestamp": "",
            "observed_timestamp": "2026-08-20T00:00:00+00:00",
            "structure_type": "unknown",
            "state": "",
            "is_fvg": False,
            "notes": "Blue OB visible at ~69k region in TradingView screenshot. Exact prices TBD.",
        }
    ]
    tv_template = {
        "_description": (
            "TradingView LuxAlgo BLUE OB Manual Reference Template. "
            "Enter one entry per BLUE OB box visible on TradingView. "
            "GREEN zones are FVGs — set is_fvg=true to exclude them from matching."
        ),
        "_exchange": "Delta Exchange India",
        "_symbol": "BTCUSD.P",
        "_timeframe": "1H",
        "_luxalgo_settings": {
            "swing_length": 50,
            "internal_length": 5,
            "ob_filter": "ATR",
            "ob_mitigation": "High/Low",
        },
        "_status": "REFERENCE_REQUIRED — fill in from TradingView LuxAlgo",
        "_fvg_note": "GREEN zones = FVGs. Set is_fvg=true for any green zone to exclude it.",
        "observations": known_observations,
    }
    tv_tmpl_path = OUT_DIR / "tv_ob_manual_reference_template.json"
    tv_tmpl_path.write_text(json.dumps(tv_template, indent=2, default=str), encoding="utf-8")
    print(f"  [OK] tv_ob_manual_reference_template.json")

    # 4) differential_results.json — pre-run matching on template (empty observations → all extra)
    diff_results = {
        "generated_at": gen_ts,
        "dataset_cutoff": DATASET_CUTOFF,
        "dataset_sha256": meta["sha256"],
        "status": "PENDING_TV_REFERENCE_DATA",
        "fvg_exclusion_policy": (
            "GREEN LuxAlgo zones are FVGs. Only BLUE OB zones are compared. "
            "Any entry with is_fvg=true is automatically excluded."
        ),
        "known_discrepancies_from_screenshots": {
            "state_mismatch_example": {
                "ob": {
                    "direction": "bullish",
                    "upper": 64328.0,
                    "lower": 64137.5,
                    "creation_timestamp": "2026-08-19T06:00:00+00:00",
                },
                "production_state": "touched",
                "tv_visual_observation": (
                    "TradingView LuxAlgo shows this blue OB as fresh/unretested at dataset cutoff."
                ),
                "root_cause_hypothesis": (
                    "Break candle (the candle that confirmed the structural break) overlaps the OB zone "
                    "price range, causing the production lifecycle to mark it TOUCHED immediately. "
                    "Under diagnostic rules (excluding break candle from touch detection), "
                    "this OB may remain FRESH."
                ),
                "diagnostic_state": None,  # filled by runtime
            },
            "missing_ob_example": {
                "tv_visual_observation": (
                    "A blue BULL OB visible near ~69k zone in TradingView screenshots. "
                    "Exact price not readable from screenshot."
                ),
                "python_status": "NOT_IN_ACTIVE_OBS",
                "investigation_method": "Use investigate_missing_ob() with tv_ob_manual_reference_template.json",
            },
        },
        "discrepancy_statistics": {
            "total_obs": len(all_diag),
            "break_candle_overlaps_zone": break_overlap_count,
            "state_discrepancies_prod_vs_diag": len(discrepancies),
            "pct_touched_by_break_candle": (
                round(break_overlap_count / len(all_diag) * 100, 1) if all_diag else 0
            ),
        },
        "all_ob_diag_summary": [
            {
                "ob_id": d.ob_id,
                "direction": d.direction,
                "structure_type": d.structure_type,
                "upper": float(d.upper_price),
                "lower": float(d.lower_price),
                "creation_ts": d.creation_timestamp.isoformat(),
                "production_state": d.production_state,
                "diag_state": d.diag_state,
                "break_overlaps": d.break_candle_overlaps_zone,
                "state_discrepancy": d.production_state != d.diag_state,
            }
            for d in all_diag
        ],
    }

    # Fill in the diagnostic state for the known state-mismatch example
    aug19_diag = next(
        (d for d in all_diag
         if d.creation_timestamp.isoformat().startswith("2026-08-19T06:00:00")
         and d.direction == "bullish"),
        None
    )
    if aug19_diag:
        diff_results["known_discrepancies_from_screenshots"]["state_mismatch_example"]["diagnostic_state"] = (
            aug19_diag.diag_state
        )
        diff_results["known_discrepancies_from_screenshots"]["state_mismatch_example"]["break_candle_overlaps"] = (
            aug19_diag.break_candle_overlaps_zone
        )

    diff_path = OUT_DIR / "differential_results.json"
    diff_path.write_text(json.dumps(diff_results, indent=2, default=str), encoding="utf-8")
    print(f"  [OK] differential_results.json")

    # 5) README.md
    _write_readme(OUT_DIR / "README.md", gen_ts, meta, len(all_diag), break_overlap_count, len(discrepancies))
    print(f"  [OK] README.md")

    # ── Print key findings ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("KEY DIAGNOSTIC FINDINGS")
    print("=" * 60)
    print(f"Total OBs analyzed:                {len(all_diag)}")
    print(f"Break candle overlaps OB zone:     {break_overlap_count} ({diff_results['discrepancy_statistics']['pct_touched_by_break_candle']}%)")
    print(f"State discrepancies (prod vs diag): {len(discrepancies)}")
    if aug19_diag:
        print(f"\nLatest OB (2026-08-19 06:00 UTC bullish):")
        print(f"  Production state : {aug19_diag.production_state}")
        print(f"  Diagnostic state : {aug19_diag.diag_state}")
        print(f"  Break candle overlaps zone: {aug19_diag.break_candle_overlaps_zone}")

    print("\nOutput files:")
    for p in OUT_DIR.iterdir():
        if p.is_file():
            print(f"  {p.relative_to(REPO_ROOT)}  ({p.stat().st_size:,} bytes)")

    return 0, all_diag, snap, candles, parsed, int_brk, sw_brk, int_piv, sw_piv, diff_results


def _write_csv(path: Path, rows: list, fields: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _write_readme(path: Path, gen_ts: str, meta: dict, total_obs: int,
                  break_overlap: int, discrepancies: int):
    lines = [
        "# Phase 3E — OB Differential Validation",
        "",
        f"> **Generated**: {gen_ts}",
        f"> **Dataset**: Delta Exchange India BTCUSD 1H | {meta['candle_count']:,} candles",
        f"> **Dataset SHA-256**: `{meta['sha256']}`",
        "",
        "## Purpose",
        "",
        "This directory contains diagnostic outputs from Phase 3E.",
        "The goal is NOT to modify production SMC logic, but to understand",
        "the differences between Python OB output and LuxAlgo TradingView visuals.",
        "",
        "## Files",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `ob_lifecycle_trace.csv` | Candle-by-candle lifecycle trace for latest active OBs |",
        "| `ob_creation_diagnostics.csv` | One row per OB: production vs diagnostic state |",
        "| `tv_ob_manual_reference_template.json` | Template to enter TradingView BLUE OB observations |",
        "| `differential_results.json` | Summary of all diagnostic findings + known discrepancies |",
        "| `README.md` | This file |",
        "",
        "## Key Findings",
        "",
        f"- **Total OBs**: {total_obs}",
        f"- **Break-candle overlaps OB zone**: {break_overlap} ({round(break_overlap/max(total_obs,1)*100,1)}%)",
        f"- **State discrepancies (production vs diagnostic)**: {discrepancies}",
        "",
        "## Root Cause Hypothesis",
        "",
        "The production `_apply_lifecycle()` in `ob_snapshot_engine.py` starts checking",
        "lifecycle for all candles **after** the formation candle timestamp.",
        "The **break candle** (which confirmed the structure break) is the first candle",
        "processed. If the break candle's price range overlaps the OB zone, the OB is",
        "immediately marked `TOUCHED` — even though no genuine *retest* of the zone occurred.",
        "",
        "This is the likely cause of the TradingView visual discrepancy:",
        "LuxAlgo may treat the break candle as the *trigger* for OB formation,",
        "not as a retest of the zone.",
        "",
        "## FVG vs OB Distinction",
        "",
        "- **BLUE zones** in LuxAlgo = Order Blocks (OBs)",
        "- **GREEN zones** in LuxAlgo = Fair Value Gaps (FVGs)",
        "",
        "Green FVG zones must NEVER be matched against Python OBs.",
        "Any observation with `is_fvg: true` is automatically excluded.",
        "",
        "## Status",
        "",
        "```",
        "Phase 3E status:  DIAGNOSTIC / PENDING MANUAL TV BLUE OB REFERENCES",
        "Production SMC:   FROZEN (ZERO DIFF)",
        "Phase 4:          NOT STARTED",
        "```",
        "",
        "## Next Steps",
        "",
        "1. Fill in `tv_ob_manual_reference_template.json` with actual LuxAlgo blue OB prices",
        "2. Re-run `generate_phase3e_diagnostics.py` with references populated",
        "3. Review whether diagnostic lifecycle (break-candle excluded from touch) matches TV",
        "4. Only then consider a targeted production SMC update (if warranted)",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    result = main()
    sys.exit(0 if isinstance(result, tuple) else result)
