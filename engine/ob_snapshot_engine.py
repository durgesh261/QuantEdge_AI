"""
Phase 3D: OB Snapshot Engine

Provides a deterministic, causal snapshot of the Python SMC engine state
at any given timestamp without look-ahead.

Key contract:
    snapshot(candles, snapshot_ts) == snapshot(candles[:N], snapshot_ts)

where N is the index of the candle at snapshot_ts.

This is essential for validating OB visibility against TradingView:
    - LuxAlgo shows OBs *active at the current chart bar*
    - Python must replicate the exact same alive/dead state at that bar

Usage:
    engine = OBSnapshotEngine(candles, atr_period=200, atr_mult=2.0,
                               internal_length=5, swing_length=50)
    snap = engine.snapshot_at("2026-07-31T00:00:00+00:00")
    # snap.active_obs  -> OBs visible at that timestamp
    # snap.all_obs     -> all OBs formed up to that timestamp (incl. invalidated)
"""

import sys
import csv
import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict, Any

ENGINE = Path(__file__).parent
sys.path.insert(0, str(ENGINE / "src"))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import (
    detect_order_blocks_streaming,
    OrderBlockConfig,
)
from quantedge.smc.models import (
    OrderBlock, PivotPoint, StructureBreak,
    OBState, TrendDirection, BreakType,
)


# ── SMC Configuration (matches production) ─────────────────────────────────────
DEFAULT_ATR_PERIOD       = 200
DEFAULT_ATR_MULT         = 2.0
DEFAULT_INTERNAL_LENGTH  = 5
DEFAULT_SWING_LENGTH     = 50


# ── Match result codes ──────────────────────────────────────────────────────────
class MatchResult:
    EXACT_MATCH                = "EXACT_MATCH"
    PRICE_MISMATCH             = "PRICE_MISMATCH"
    TIMESTAMP_MISMATCH         = "TIMESTAMP_MISMATCH"
    DIRECTION_MISMATCH         = "DIRECTION_MISMATCH"
    SOURCE_CANDLE_MISMATCH     = "SOURCE_CANDLE_MISMATCH"
    LIFECYCLE_MISMATCH         = "LIFECYCLE_MISMATCH"
    MISSING_IN_PYTHON          = "MISSING_IN_PYTHON"
    MISSING_IN_TRADINGVIEW     = "MISSING_IN_TRADINGVIEW"
    MITIGATED_NOT_VISIBLE      = "MITIGATED_NOT_VISIBLE"
    REFERENCE_UNAVAILABLE      = "REFERENCE_UNAVAILABLE"


@dataclass
class OBRecord:
    """Rich OB record with all fields required by Phase 3D/3E spec."""
    structure_type:         str        # "internal" | "swing"
    direction:              str        # "bullish"  | "bearish"
    creation_timestamp:     datetime   # formation_candle.timestamp (OB identity)
    creation_candle_index:  int        # formation_index
    break_timestamp:        datetime   # break candle timestamp (OB activation)
    break_candle_index:     int        # break_index
    break_type:             str        # "bos" | "choch"
    source_candle_index:    int        # same as formation_index (OB source candle)
    source_timestamp:       datetime   # same as creation_timestamp
    upper_price:            Decimal    # top_price
    lower_price:            Decimal    # bottom_price
    state:                  str        # OBState value
    first_touch_timestamp:  Optional[datetime]  # first genuine retest after activation
    invalidation_timestamp: Optional[datetime]
    activated_at:           Optional[datetime]  # = break_timestamp (OB becomes live)
    # Pivot info (the pivot that was broken)
    pivot_index:            Optional[int]
    pivot_timestamp:        Optional[datetime]
    pivot_price:            Optional[Decimal]
    # Computed
    is_active:              bool       # True if state != INVALIDATED
    symbol:                 str

    def to_dict(self) -> Dict[str, Any]:
        def _fmt(v):
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        return {
            "structure_type":        self.structure_type,
            "direction":             self.direction,
            "creation_timestamp":    _fmt(self.creation_timestamp),
            "creation_candle_index": self.creation_candle_index,
            "break_timestamp":       _fmt(self.break_timestamp),
            "break_candle_index":    self.break_candle_index,
            "break_type":            self.break_type,
            "source_candle_index":   self.source_candle_index,
            "source_timestamp":      _fmt(self.source_timestamp),
            "upper_price":           _fmt(self.upper_price),
            "lower_price":           _fmt(self.lower_price),
            "state":                 self.state,
            "first_touch_timestamp": _fmt(self.first_touch_timestamp),
            "invalidation_timestamp":_fmt(self.invalidation_timestamp),
            "activated_at":          _fmt(self.activated_at),
            "pivot_index":           self.pivot_index,
            "pivot_timestamp":       _fmt(self.pivot_timestamp),
            "pivot_price":           _fmt(self.pivot_price),
            "is_active":             self.is_active,
            "symbol":                self.symbol,
        }


@dataclass
class SnapshotResult:
    """Result of a snapshot_at() call."""
    snapshot_timestamp: datetime
    candles_processed:  int       # how many candles were in the replay window
    all_obs:            List[OBRecord]   # all OBs formed up to snapshot_ts
    active_obs:         List[OBRecord]   # OBs with state != INVALIDATED at snapshot
    invalidated_obs:    List[OBRecord]   # OBs with state == INVALIDATED

    @property
    def active_count(self) -> int:
        return len(self.active_obs)

    @property
    def all_count(self) -> int:
        return len(self.all_obs)


class OBSnapshotEngine:
    """
    Deterministic SMC/OB engine with causal snapshot capability.

    The engine can be queried at any historical timestamp without future look-ahead.
    This mirrors LuxAlgo's bar-by-bar rendering: only candles up to the
    requested timestamp are used.

    Future-data invariance guarantee:
        engine.snapshot_at(T) == engine.snapshot_at(T) for any T
        regardless of how many additional future candles are in self.candles.

    Usage:
        eng = OBSnapshotEngine.from_csv("path/to/data.csv", symbol="BTCUSD.P")
        snap = eng.snapshot_at("2026-07-31T14:00:00+00:00")
        print(len(snap.active_obs), "active OBs")
    """

    def __init__(
        self,
        candles: List[Candle],
        atr_period:       int   = DEFAULT_ATR_PERIOD,
        atr_mult:         float = DEFAULT_ATR_MULT,
        internal_length:  int   = DEFAULT_INTERNAL_LENGTH,
        swing_length:     int   = DEFAULT_SWING_LENGTH,
        symbol:           str   = "BTCUSD.P",
    ):
        self.candles          = candles
        self.atr_period       = atr_period
        self.atr_mult         = atr_mult
        self.internal_length  = internal_length
        self.swing_length     = swing_length
        self.symbol           = symbol

    @classmethod
    def from_csv(
        cls,
        csv_path: str | Path,
        symbol:   str   = "BTCUSD.P",
        **kwargs,
    ) -> "OBSnapshotEngine":
        """Load candles from CSV and return engine instance."""
        candles = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                candles.append(Candle(
                    symbol    = symbol,
                    timeframe = Timeframe.H1,
                    timestamp = ts,
                    open      = Decimal(row["open"]),
                    high      = Decimal(row["high"]),
                    low       = Decimal(row["low"]),
                    close     = Decimal(row["close"]),
                    volume    = Decimal(row.get("volume", "0")),
                    source    = MarketDataSource.HISTORICAL,
                ))
        return cls(candles, symbol=symbol, **kwargs)

    def _parse_snapshot_ts(self, snapshot_ts: str | datetime) -> datetime:
        if isinstance(snapshot_ts, str):
            ts = datetime.fromisoformat(snapshot_ts)
        else:
            ts = snapshot_ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts

    def _slice_candles(self, snapshot_ts: datetime) -> List[Candle]:
        """Return only candles with timestamp <= snapshot_ts."""
        return [c for c in self.candles if c.timestamp <= snapshot_ts]

    def _run_pipeline(self, candles: List[Candle]):
        """Run SMC pipeline on the given candle slice and return all artifacts."""
        if len(candles) < max(self.atr_period, self.swing_length) + 1:
            return [], [], [], [], [], []

        parsed = parse_candles_with_volatility(
            candles, self.atr_period, self.atr_mult
        )

        int_cfg = StructureConfig(self.internal_length, StructureType.INTERNAL)
        sw_cfg  = StructureConfig(self.swing_length,    StructureType.SWING)

        # Pivot collection pass
        int_det  = StructureDetector(int_cfg)
        sw_det   = StructureDetector(sw_cfg)
        int_piv, sw_piv = [], []
        prev_iph = prev_ipl = prev_sph = prev_spl = None

        for i, pc in enumerate(parsed):
            int_det.process_candle(pc, i)
            sw_det.process_candle(pc, i)

            iph = int_det.state.pivot_high
            ipl = int_det.state.pivot_low
            sph = sw_det.state.pivot_high
            spl = sw_det.state.pivot_low

            if iph and iph.index != prev_iph:
                int_piv.append(PivotPoint(iph.index, iph.timestamp, iph.price, True,  iph.candle))
                prev_iph = iph.index
            if ipl and ipl.index != prev_ipl:
                int_piv.append(PivotPoint(ipl.index, ipl.timestamp, ipl.price, False, ipl.candle))
                prev_ipl = ipl.index
            if sph and sph.index != prev_sph:
                sw_piv.append(PivotPoint(sph.index, sph.timestamp, sph.price, True,  sph.candle))
                prev_sph = sph.index
            if spl and spl.index != prev_spl:
                sw_piv.append(PivotPoint(spl.index, spl.timestamp, spl.price, False, spl.candle))
                prev_spl = spl.index

        # Break collection pass (clean detectors)
        int_det2 = StructureDetector(int_cfg)
        sw_det2  = StructureDetector(sw_cfg)
        int_brk, sw_brk = [], []
        for i, pc in enumerate(parsed):
            int_brk.extend(int_det2.process_candle(pc, i))
            sw_brk.extend(sw_det2.process_candle(pc, i))

        ob_cfg = OrderBlockConfig(
            internal_length = self.internal_length,
            swing_length    = self.swing_length,
            atr_period      = self.atr_period,
            atr_multiplier  = self.atr_mult,
        )
        int_obs = detect_order_blocks_streaming(
            parsed_candles  = parsed,
            internal_breaks = int_brk, swing_breaks = [],
            internal_pivots = int_piv, swing_pivots  = sw_piv,
            config          = ob_cfg,
        )
        sw_obs  = detect_order_blocks_streaming(
            parsed_candles  = parsed,
            internal_breaks = [], swing_breaks = sw_brk,
            internal_pivots = int_piv, swing_pivots  = sw_piv,
            config          = ob_cfg,
        )

        return parsed, int_brk, sw_brk, int_piv, sw_piv, int_obs + sw_obs

    def _apply_lifecycle(
        self,
        obs:              List[OrderBlock],
        candles:          List[Candle],
        int_brk:          List[StructureBreak],
        sw_brk:           List[StructureBreak],
        int_piv:          List[PivotPoint],
        sw_piv:           List[PivotPoint],
    ) -> List[OBRecord]:
        """
        Apply candle-by-candle lifecycle to each OB and return rich OBRecord list.
        
        Uses High/Low mitigation rule (matching LuxAlgo):
            Bullish OB invalidated: candle low < ob.bottom_price
            Bearish OB invalidated: candle high > ob.top_price
        """
        # Build pivot lookup: break_index -> pivot that was broken
        break_to_pivot: Dict[int, PivotPoint] = {}
        for brk in int_brk + sw_brk:
            # Find the broken pivot for this break
            piv_list = sw_piv if hasattr(brk, "structure_type") and brk.structure_type == StructureType.SWING else int_piv
            if brk.direction == TrendDirection.BULLISH:
                for p in reversed(piv_list):
                    if p.is_high and p.index < brk.index and brk.price > p.price:
                        break_to_pivot[brk.index] = p
                        break
            else:
                for p in reversed(piv_list):
                    if not p.is_high and p.index < brk.index and brk.price < p.price:
                        break_to_pivot[brk.index] = p
                        break

        records = []
        candle_ts_map = {c.timestamp: c for c in candles}
        candle_by_idx = list(candles)  # indexed by position

        for ob in obs:
            # Determine structure_type from break
            brk_idx   = ob.break_index
            brk_match = None
            # Find the matching break event
            for brk in int_brk + sw_brk:
                if brk.index == brk_idx:
                    brk_match = brk
                    break

            stype = "internal"
            if brk_match and brk_match.structure_type == StructureType.SWING:
                stype = "swing"

            # Break candle timestamp
            break_ts = None
            if brk_idx < len(candle_by_idx):
                break_ts = candle_by_idx[brk_idx].timestamp

            # Pivot info
            piv = break_to_pivot.get(brk_idx)
            piv_idx  = piv.index     if piv else None
            piv_ts   = piv.timestamp if piv else None
            piv_pr   = piv.price     if piv else None

            # Apply candle-by-candle lifecycle starting AFTER the break candle.
            # The OB is activated at the break event; the break candle itself
            # is the trigger, not a retest. Lifecycle begins at break_candle_index + 1.
            state      = OBState.FRESH
            touch_ts   = None
            invalid_ts = None
            activated_ts = break_ts

            # Lifecycle begins at the candle AFTER the break candle
            lifecycle_start_idx = brk_idx + 1

            for i, c in enumerate(candles):
                if i < lifecycle_start_idx:
                    continue  # skip candles up to and including break candle
                if state == OBState.INVALIDATED:
                    break

                # High/Low mitigation check
                if ob.type == "BULLISH":
                    # Bullish OB: touch = price enters zone; invalidated = low < bottom
                    if c.low <= ob.top_price and c.high >= ob.bottom_price:
                        if state == OBState.FRESH:
                            state    = OBState.TOUCHED
                            touch_ts = c.timestamp
                    if c.low < ob.bottom_price:
                        state      = OBState.INVALIDATED
                        invalid_ts = c.timestamp
                else:
                    # Bearish OB: touch = price enters zone; invalidated = high > top
                    if c.low <= ob.top_price and c.high >= ob.bottom_price:
                        if state == OBState.FRESH:
                            state    = OBState.TOUCHED
                            touch_ts = c.timestamp
                    if c.high > ob.top_price:
                        state      = OBState.INVALIDATED
                        invalid_ts = c.timestamp

            records.append(OBRecord(
                structure_type         = stype,
                direction              = "bullish" if ob.type == "BULLISH" else "bearish",
                creation_timestamp     = ob.formation_candle.timestamp,
                creation_candle_index  = ob.formation_index,
                break_timestamp        = break_ts,
                break_candle_index     = brk_idx,
                break_type             = brk_match.break_type.value if brk_match else "bos",
                source_candle_index    = ob.formation_index,
                source_timestamp       = ob.formation_candle.timestamp,
                upper_price            = ob.top_price,
                lower_price            = ob.bottom_price,
                state                  = state.value,
                first_touch_timestamp  = touch_ts,
                invalidation_timestamp = invalid_ts,
                activated_at           = activated_ts,
                pivot_index            = piv_idx,
                pivot_timestamp        = piv_ts,
                pivot_price            = piv_pr,
                is_active              = (state != OBState.INVALIDATED),
                symbol                 = self.symbol,
            ))

        return records

    def snapshot_at(
        self,
        snapshot_ts: str | datetime,
    ) -> SnapshotResult:
        """
        Return the OB snapshot as it would appear at exactly snapshot_ts.

        Only candles with timestamp <= snapshot_ts are used (causal / no look-ahead).
        Future-data invariant: adding future candles does NOT change this result.
        """
        ts       = self._parse_snapshot_ts(snapshot_ts)
        sliced   = self._slice_candles(ts)

        if not sliced:
            return SnapshotResult(
                snapshot_timestamp = ts,
                candles_processed  = 0,
                all_obs            = [],
                active_obs         = [],
                invalidated_obs    = [],
            )

        parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = self._run_pipeline(sliced)
        records = self._apply_lifecycle(raw_obs, sliced, int_brk, sw_brk, int_piv, sw_piv)

        active      = [r for r in records if r.is_active]
        invalidated = [r for r in records if not r.is_active]

        return SnapshotResult(
            snapshot_timestamp = ts,
            candles_processed  = len(sliced),
            all_obs            = records,
            active_obs         = active,
            invalidated_obs    = invalidated,
        )

    def verify_future_data_invariance(
        self,
        snapshot_ts: str | datetime,
        verbose:     bool = False,
    ) -> bool:
        """
        Verify snapshot_at(T) is identical whether or not future candles are present.
        Returns True if invariant holds.
        """
        ts      = self._parse_snapshot_ts(snapshot_ts)
        sliced  = self._slice_candles(ts)

        # Run A: only candles up to T
        eng_a = OBSnapshotEngine(
            sliced,
            self.atr_period, self.atr_mult,
            self.internal_length, self.swing_length,
            self.symbol,
        )
        snap_a = eng_a.snapshot_at(ts)

        # Run B: all candles including future
        snap_b = self.snapshot_at(ts)

        # Compare active OBs at snapshot (by creation_timestamp + direction + structure_type)
        def ob_key(r: OBRecord):
            return (r.structure_type, r.direction, r.creation_timestamp.isoformat())

        keys_a = {ob_key(r) for r in snap_a.active_obs}
        keys_b = {ob_key(r) for r in snap_b.active_obs}

        invariant = (keys_a == keys_b)
        if verbose and not invariant:
            extra_a = keys_a - keys_b
            extra_b = keys_b - keys_a
            if extra_a:
                print(f"  [FAIL] Only in A (no-future): {extra_a}")
            if extra_b:
                print(f"  [FAIL] Only in B (with-future): {extra_b}")

        return invariant


def match_ob_against_reference(
    python_ob: OBRecord,
    tv_ob: Dict[str, Any],
    price_tolerance: Decimal = Decimal("0.5"),   # Delta tick size
) -> Dict[str, Any]:
    """
    Match one Python OBRecord against one TradingView OB reference entry.

    Returns a dict with:
        result: MatchResult code
        details: human-readable description
        fields: per-field comparison
    """
    issues = []
    fields = {}

    # Direction
    py_dir = python_ob.direction
    tv_dir = tv_ob.get("direction", "").lower()
    fields["direction"] = {"python": py_dir, "tv": tv_dir}
    if py_dir != tv_dir:
        issues.append(MatchResult.DIRECTION_MISMATCH)

    # Structure type
    py_st = python_ob.structure_type
    tv_st = tv_ob.get("structure_type", "").lower()
    fields["structure_type"] = {"python": py_st, "tv": tv_st}
    if py_st != tv_st:
        issues.append(MatchResult.SOURCE_CANDLE_MISMATCH)

    # Creation timestamp
    py_cts = python_ob.creation_timestamp.isoformat()
    tv_cts = tv_ob.get("creation_timestamp", "")
    fields["creation_timestamp"] = {"python": py_cts, "tv": tv_cts}
    if py_cts != tv_cts:
        issues.append(MatchResult.TIMESTAMP_MISMATCH)

    # Source timestamp
    py_sts = python_ob.source_timestamp.isoformat()
    tv_sts = tv_ob.get("source_timestamp", "")
    fields["source_timestamp"] = {"python": py_sts, "tv": tv_sts}
    if tv_sts and py_sts != tv_sts:
        issues.append(MatchResult.SOURCE_CANDLE_MISMATCH)

    # Upper price
    py_up  = python_ob.upper_price
    tv_up  = Decimal(str(tv_ob.get("upper", 0))) if tv_ob.get("upper") else None
    fields["upper_price"] = {"python": float(py_up), "tv": float(tv_up) if tv_up else None}
    if tv_up is not None and abs(py_up - tv_up) > price_tolerance:
        issues.append(MatchResult.PRICE_MISMATCH)

    # Lower price
    py_lo  = python_ob.lower_price
    tv_lo  = Decimal(str(tv_ob.get("lower", 0))) if tv_ob.get("lower") else None
    fields["lower_price"] = {"python": float(py_lo), "tv": float(tv_lo) if tv_lo else None}
    if tv_lo is not None and abs(py_lo - tv_lo) > price_tolerance:
        issues.append(MatchResult.PRICE_MISMATCH)

    # Lifecycle
    py_state = python_ob.state
    tv_state = tv_ob.get("state", "")
    fields["state"] = {"python": py_state, "tv": tv_state}
    if tv_state and py_state != tv_state:
        issues.append(MatchResult.LIFECYCLE_MISMATCH)

    # Final result
    if not issues:
        result = MatchResult.EXACT_MATCH
    else:
        # Return the most specific failure
        priority = [
            MatchResult.DIRECTION_MISMATCH,
            MatchResult.TIMESTAMP_MISMATCH,
            MatchResult.PRICE_MISMATCH,
            MatchResult.SOURCE_CANDLE_MISMATCH,
            MatchResult.LIFECYCLE_MISMATCH,
        ]
        result = next((i for i in priority if i in issues), issues[0])

    return {
        "result":  result,
        "issues":  issues,
        "fields":  fields,
        "details": f"{len(issues)} mismatches: {issues}" if issues else "All fields match",
    }


def compare_snapshot_to_reference(
    snap:      SnapshotResult,
    tv_snap:   Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare a Python snapshot to a TradingView reference snapshot.

    tv_snap format:
    {
        "timestamp": "...",
        "order_blocks": [
            {
                "structure_type": "internal",
                "direction": "bullish",
                "creation_timestamp": "...",
                "source_timestamp": "...",
                "upper": 65000.0,
                "lower": 64500.0,
                "state": "fresh"
            }
        ]
    }
    """
    tv_obs  = tv_snap.get("order_blocks", [])
    py_obs  = snap.active_obs
    results = []

    # Match each TV OB to best Python candidate
    matched_py = set()

    for tv_ob in tv_obs:
        tv_dir   = tv_ob.get("direction", "").lower()
        tv_stype = tv_ob.get("structure_type", "").lower()
        tv_cts   = tv_ob.get("creation_timestamp", "")

        # Find Python candidates (primary key: direction + structure_type + creation_timestamp)
        candidates = [
            (i, r) for i, r in enumerate(py_obs)
            if r.direction == tv_dir
            and r.structure_type == tv_stype
            and r.creation_timestamp.isoformat() == tv_cts
        ]

        if not candidates:
            # Check if OB was invalidated (mitigated) — not missing, just not visible
            inv_match = [
                r for r in snap.invalidated_obs
                if r.direction == tv_dir
                and r.structure_type == tv_stype
                and r.creation_timestamp.isoformat() == tv_cts
            ]
            if inv_match:
                results.append({
                    "tv_ob":   tv_ob,
                    "py_ob":   inv_match[0].to_dict(),
                    "result":  MatchResult.MITIGATED_NOT_VISIBLE,
                    "details": "OB found in Python (invalidated) — not visible on TV (mitigated)",
                })
            else:
                results.append({
                    "tv_ob":   tv_ob,
                    "py_ob":   None,
                    "result":  MatchResult.MISSING_IN_PYTHON,
                    "details": "OB visible on TradingView but not found in Python",
                })
        else:
            i, py_ob = candidates[0]
            matched_py.add(i)
            match = match_ob_against_reference(py_ob, tv_ob)
            results.append({
                "tv_ob":   tv_ob,
                "py_ob":   py_ob.to_dict(),
                **match,
            })

    # Python OBs with no TradingView counterpart
    for i, py_ob in enumerate(py_obs):
        if i not in matched_py:
            results.append({
                "tv_ob":   None,
                "py_ob":   py_ob.to_dict(),
                "result":  MatchResult.MISSING_IN_TRADINGVIEW,
                "details": (
                    "OB exists in Python (active) but no matching TradingView reference. "
                    "May be a TV rendering limitation (sub-swing-level OB not shown)."
                ),
            })

    # Summary
    exact    = sum(1 for r in results if r["result"] == MatchResult.EXACT_MATCH)
    missing_py = sum(1 for r in results if r["result"] == MatchResult.MISSING_IN_PYTHON)
    missing_tv = sum(1 for r in results if r["result"] == MatchResult.MISSING_IN_TRADINGVIEW)
    mismatches = len(results) - exact - missing_py - missing_tv

    return {
        "snapshot_timestamp": snap.snapshot_timestamp.isoformat(),
        "tv_ob_count":        len(tv_obs),
        "py_ob_count":        len(py_obs),
        "exact_matches":      exact,
        "mismatches":         mismatches,
        "missing_in_python":  missing_py,
        "missing_in_tv":      missing_tv,
        "comparisons":        results,
    }


if __name__ == "__main__":
    # Quick smoke test
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ob_snapshot_engine.py <csv_path> [snapshot_timestamp]")
        sys.exit(1)

    csv_path = sys.argv[1]
    snap_ts  = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Loading: {csv_path}")
    eng = OBSnapshotEngine.from_csv(csv_path)
    print(f"Loaded {len(eng.candles)} candles")

    if snap_ts:
        snap = eng.snapshot_at(snap_ts)
        print(f"\nSnapshot at {snap.snapshot_timestamp.isoformat()}:")
        print(f"  Candles processed : {snap.candles_processed}")
        print(f"  All OBs formed    : {snap.all_count}")
        print(f"  Active OBs        : {snap.active_count}")
        print(f"  Invalidated OBs   : {len(snap.invalidated_obs)}")

        inv = eng.verify_future_data_invariance(snap_ts, verbose=True)
        print(f"  Future-invariant  : {inv}")
